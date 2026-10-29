"""Restructuring models: recovery waterfall, distressed exchange, cash runway.

All amounts are in USD. Defaults are seeded from the XBRL balance sheet, but
every input is meant to be overridden by the analyst: XBRL company facts carry
no dimensional detail (e.g. which tranche is secured), so the defaults are
heuristics, not the filing's own capital-structure table.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields

import pandas as pd

# Typical retail liquidation recovery rates (share of book value).
DEFAULT_RECOVERY_RATES = {
    "cash": 1.00,
    "receivables": 0.85,   # card receivables settle quickly
    "inventory": 0.60,     # net orderly liquidation value of retail inventory
    "ppe": 0.15,           # leasehold improvements / fixtures recover little
    "other_assets": 0.10,
}


def _num(v) -> float:
    return 0.0 if v is None or pd.isna(v) else float(v)


@classmethod
def _from_dict(cls, data: dict | None, base=None):
    base = base or cls()
    known = {f.name for f in fields(cls)}
    merged = {**asdict(base), **{k: v for k, v in (data or {}).items() if k in known}}
    return cls(**merged)


# ---------------------------------------------------------------------------
# 1. Recovery waterfall (absolute priority rule)
# ---------------------------------------------------------------------------

@dataclass
class WaterfallInputs:
    # asset book values
    cash: float = 0.0
    receivables: float = 0.0
    inventory: float = 0.0
    ppe: float = 0.0
    other_assets: float = 0.0
    recovery_rates: dict = field(default_factory=lambda: dict(DEFAULT_RECOVERY_RATES))
    # valuation basis
    mode: str = "liquidation"            # "liquidation" | "going_concern"
    ebitda: float = 0.0                  # for going-concern EV
    ev_multiple: float = 5.0
    # deductions & claims
    admin_cost_pct: float = 0.07         # wind-down / professional fees, % of gross value
    priority_claims: float = 0.0         # wages, taxes, 503(b)(9) claims
    secured_debt: float = 0.0            # ABL / FILO / term loans
    unsecured_notes: float = 0.0
    trade_payables: float = 0.0
    lease_rejection_claims: float = 0.0
    other_unsecured: float = 0.0

    from_dict = _from_dict


def default_waterfall_inputs(p_row: pd.Series, m_row: pd.Series) -> WaterfallInputs:
    cash = _num(m_row["liquid_assets"])
    rec, inv, ppe = _num(p_row["receivables"]), _num(p_row["inventory"]), _num(p_row["ppe_net"])
    # ROU lease assets are excluded: rejected leases have no recovery value.
    op_lease = _num(p_row["op_lease_current"]) + _num(p_row["op_lease_noncurrent"])
    rou = _num(p_row["op_lease_rou_asset"]) or op_lease
    other = max(_num(p_row["total_assets"]) - cash - rec - inv - ppe - rou, 0.0)

    funded = _num(m_row["funded_debt"])
    notes = min(_num(m_row["notes_principal_est"]) or funded, funded)
    # Heuristic: anything that isn't the unsecured notes is secured (ABL/FILO).
    secured = max(funded - notes, 0.0) + _num(m_row["finance_leases"])

    # Bankruptcy Code s.502(b)(6) caps landlord damages at roughly one year of rent
    # for most leases; next-12-month lease payments is a reasonable proxy.
    lease_claims = _num(p_row["lease_payments_next_12m"]) or 0.25 * op_lease

    return WaterfallInputs(
        cash=cash, receivables=rec, inventory=inv, ppe=ppe, other_assets=other,
        ebitda=_num(m_row["ebitda_ttm"]),
        secured_debt=secured, unsecured_notes=notes,
        trade_payables=_num(p_row["accounts_payable"]),
        lease_rejection_claims=lease_claims,
        other_unsecured=_num(p_row["accrued_liabilities"]),
    )


def run_waterfall(w: WaterfallInputs) -> dict:
    rates = {**DEFAULT_RECOVERY_RATES, **(w.recovery_rates or {})}
    assets = [
        {"asset": "Cash & investments", "book": w.cash, "rate": rates["cash"]},
        {"asset": "Receivables", "book": w.receivables, "rate": rates["receivables"]},
        {"asset": "Inventory", "book": w.inventory, "rate": rates["inventory"]},
        {"asset": "PP&E", "book": w.ppe, "rate": rates["ppe"]},
        {"asset": "Other assets", "book": w.other_assets, "rate": rates["other_assets"]},
    ]
    for a in assets:
        a["value"] = a["book"] * a["rate"]
    liquidation_value = sum(a["value"] for a in assets)
    going_concern_value = max(w.ebitda, 0.0) * w.ev_multiple

    if w.mode == "going_concern":
        gross = going_concern_value
        basis = "Going concern (EV = EBITDA x multiple)"
    else:
        gross = liquidation_value
        basis = "Liquidation (book value x recovery rate)"

    admin = gross * w.admin_cost_pct
    remaining = gross

    classes = []

    def pay(name, claim, rank, note=""):
        nonlocal remaining
        paid = min(max(remaining, 0.0), claim)
        remaining -= paid
        classes.append({"class": name, "rank": rank, "claim": claim, "recovery": paid,
                        "recovery_pct": (paid / claim * 100) if claim else None,
                        "note": note})
        return paid

    pay("Administrative & wind-down", admin, 1, "Paid off the top")
    pay("Priority claims", w.priority_claims, 2, "Wages, taxes, 503(b)(9)")
    secured_paid = pay("Secured debt (ABL / FILO)", w.secured_debt, 3,
                       "First lien on current assets")
    deficiency = w.secured_debt - secured_paid

    # General unsecured claims share pro rata (pari passu), incl. secured deficiency.
    guc = [
        ("Senior unsecured notes", w.unsecured_notes),
        ("Trade payables", w.trade_payables),
        ("Lease rejection claims", w.lease_rejection_claims),
        ("Other unsecured (accrued)", w.other_unsecured),
        ("Secured deficiency claim", deficiency),
    ]
    guc_total = sum(c for _, c in guc)
    pool = max(remaining, 0.0)
    guc_pct = min(pool / guc_total, 1.0) if guc_total else 0.0
    for name, claim in guc:
        if claim <= 0:
            continue
        classes.append({"class": name, "rank": 4, "claim": claim,
                        "recovery": claim * guc_pct,
                        "recovery_pct": guc_pct * 100, "note": "Pari passu GUC pool"})
    remaining = pool - min(pool, guc_total)

    classes.append({"class": "Equity", "rank": 5, "claim": None,
                    "recovery": max(remaining, 0.0), "recovery_pct": None,
                    "note": "Residual value"})
    return {
        "basis": basis, "assets": assets,
        "liquidation_value": liquidation_value,
        "going_concern_value": going_concern_value,
        "gross_value": gross, "admin_costs": admin,
        "distributable_value": gross - admin,
        "classes": classes, "guc_recovery_pct": guc_pct * 100,
        "fulcrum": _fulcrum(classes),
    }


def _fulcrum(classes: list[dict]) -> str | None:
    """The fulcrum security is the most senior class that is not paid in full:
    it is the class most likely to receive the reorganized equity."""
    for c in classes:
        if c["claim"] and c["recovery"] < c["claim"] - 1e-6 and c["rank"] >= 3:
            return c["class"]
    return None


# ---------------------------------------------------------------------------
# 2. Distressed exchange / debt buyback
# ---------------------------------------------------------------------------

@dataclass
class ExchangeInputs:
    notes_face: float = 0.0
    participation: float = 0.6        # share of notes tendered
    price: float = 0.40               # consideration per $1 face
    consideration: str = "new_secured_debt"  # "cash" | "new_secured_debt" | "equity"
    old_coupon: float = 0.05
    new_coupon: float = 0.12
    other_debt: float = 0.0           # secured / ABL debt left in place
    other_debt_rate: float = 0.08
    liquid_assets: float = 0.0
    ebitda: float = 0.0
    new_equity_raise: float = 0.0     # proceeds used to pay down other_debt
    tax_rate: float = 0.21

    from_dict = _from_dict


def default_exchange_inputs(p_row: pd.Series, m_row: pd.Series) -> ExchangeInputs:
    w = default_waterfall_inputs(p_row, m_row)
    funded = _num(m_row["funded_debt"])
    interest = _num(p_row["interest_expense_ttm"])
    blended = interest / funded if funded else 0.05
    price = _num(m_row["debt_price_pct"]) / 100 or 0.40
    return ExchangeInputs(
        notes_face=w.unsecured_notes,
        price=round(min(price * 1.25, 1.0), 2),  # exchanges clear at a premium to market
        old_coupon=round(min(max(blended, 0.01), 0.15), 4),
        other_debt=w.secured_debt,
        liquid_assets=_num(m_row["liquid_assets"]),
        ebitda=_num(m_row["ebitda_ttm"]),
    )


def run_exchange(x: ExchangeInputs) -> dict:
    tendered = x.notes_face * x.participation
    consideration = tendered * x.price
    discount = tendered - consideration

    paydown = min(x.new_equity_raise, x.other_debt)
    other_debt_pf = x.other_debt - paydown
    new_debt = consideration if x.consideration == "new_secured_debt" else 0.0
    cash_out = consideration if x.consideration == "cash" else 0.0
    new_equity = (consideration if x.consideration == "equity" else 0.0) + x.new_equity_raise

    pre_debt = x.notes_face + x.other_debt
    post_debt = (x.notes_face - tendered) + new_debt + other_debt_pf

    pre_interest = x.notes_face * x.old_coupon + x.other_debt * x.other_debt_rate
    post_interest = ((x.notes_face - tendered) * x.old_coupon + new_debt * x.new_coupon
                     + other_debt_pf * x.other_debt_rate)

    pre_liq = x.liquid_assets
    post_liq = x.liquid_assets - cash_out + (x.new_equity_raise - paydown)

    def lev(debt):
        return debt / x.ebitda if x.ebitda > 0 else None

    def cov(interest):
        return x.ebitda / interest if x.ebitda > 0 and interest > 0 else None

    return {
        "tendered_face": tendered, "consideration_value": consideration,
        "discount_captured": discount,
        # Cancellation-of-debt income: taxable unless the s.108 insolvency /
        # bankruptcy exclusions apply (they usually reduce NOLs instead).
        "cod_income": discount, "cod_tax_if_not_excluded": discount * x.tax_rate,
        "new_equity_issued": new_equity, "other_debt_paydown": paydown,
        "table": [
            {"metric": "Total debt", "pre": pre_debt, "post": post_debt, "fmt": "usd"},
            {"metric": "Annual cash interest", "pre": pre_interest, "post": post_interest, "fmt": "usd"},
            {"metric": "Liquid assets", "pre": pre_liq, "post": post_liq, "fmt": "usd"},
            {"metric": "Debt / EBITDA", "pre": lev(pre_debt), "post": lev(post_debt), "fmt": "x"},
            {"metric": "EBITDA / interest", "pre": cov(pre_interest), "post": cov(post_interest), "fmt": "x"},
        ],
        "notes": _exchange_notes(x, new_debt),
    }


def _exchange_notes(x: ExchangeInputs, new_debt: float) -> list[str]:
    notes = []
    if x.consideration == "new_secured_debt" and new_debt:
        notes.append("New debt primes the remaining unsecured holdouts, which are "
                     "structurally subordinated afterwards. Expect holdout litigation risk.")
        if x.new_coupon > x.old_coupon:
            notes.append("The higher coupon on the exchanged debt offsets part of the "
                         "face-value reduction. Check that cash interest actually falls.")
    if x.consideration == "cash":
        notes.append("A cash buyback captures the discount but consumes liquidity. "
                     "This only makes sense if runway stays adequate.")
    if x.consideration == "equity":
        notes.append("A debt-for-equity swap delevers without using cash, but dilutes "
                     "existing shareholders.")
    if x.ebitda <= 0:
        notes.append("EBITDA is negative, so no exchange restores sustainability on its "
                     "own. The operating burn must be fixed, otherwise a Chapter 11 "
                     "filing is likely.")
    return notes


# ---------------------------------------------------------------------------
# 3. Liquidity runway projection
# ---------------------------------------------------------------------------

@dataclass
class RunwayInputs:
    starting_liquidity: float = 0.0
    revolver_availability: float = 0.0
    monthly_burn: float = 0.0           # positive = cash out
    burn_reduction_pct: float = 0.0     # e.g. 0.3 = 30% cut, phased in over ramp_months
    ramp_months: int = 6
    new_capital: float = 0.0            # equity raise / DIP / asset sale at month 1
    debt_maturities: float = 0.0
    maturity_month: int = 12
    horizon_months: int = 24

    from_dict = _from_dict


def default_runway_inputs(p_row: pd.Series, m_row: pd.Series) -> RunwayInputs:
    fcf = _num(m_row["fcf_ttm"])
    return RunwayInputs(
        starting_liquidity=_num(m_row["liquid_assets"]),
        monthly_burn=max(-fcf / 12, 0.0),
    )


def run_runway(r: RunwayInputs) -> dict:
    liquidity = r.starting_liquidity + r.revolver_availability
    months, balances = [0], [liquidity]
    zero_month = None
    for t in range(1, int(r.horizon_months) + 1):
        phase = min(t / r.ramp_months, 1.0) if r.ramp_months > 0 else 1.0
        burn = r.monthly_burn * (1 - r.burn_reduction_pct * phase)
        liquidity -= burn
        if t == 1:
            liquidity += r.new_capital
        if t == int(r.maturity_month):
            liquidity -= r.debt_maturities
        months.append(t)
        balances.append(liquidity)
        if zero_month is None and liquidity < 0:
            zero_month = t
    return {"months": months, "liquidity": balances, "zero_month": zero_month}
