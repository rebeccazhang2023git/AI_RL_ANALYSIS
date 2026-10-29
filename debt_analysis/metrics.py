"""Liquidity, leverage, coverage and distress metrics on the quarterly panel."""

from __future__ import annotations

import numpy as np
import pandas as pd


def _safe_div(a: pd.Series, b: pd.Series) -> pd.Series:
    b = b.where(b != 0)
    return a / b


def _positive_div(a: pd.Series, b: pd.Series) -> pd.Series:
    """Ratio that is only meaningful when the denominator is positive
    (e.g. Debt / EBITDA is undefined when EBITDA is negative)."""
    return a / b.where(b > 0)


def compute_metrics(p: pd.DataFrame) -> pd.DataFrame:
    """``p`` is the output of ``financials.add_ttm(build_quarterly_panel(...))``."""
    m = pd.DataFrame(index=p.index)
    z = lambda col: p[col].fillna(0)  # noqa: E731 - untagged debt lines mean "none"

    # -- capital structure --------------------------------------------------
    m["funded_debt"] = z("debt_current") + z("short_term_borrowings") + z("debt_noncurrent")
    m["finance_leases"] = z("finance_lease_current") + z("finance_lease_noncurrent")
    m["total_debt"] = m["funded_debt"] + m["finance_leases"]
    m["operating_leases"] = (p["op_lease_current"] + p["op_lease_noncurrent"])
    m["lease_adjusted_debt"] = m["total_debt"] + m["operating_leases"].fillna(0)
    m["liquid_assets"] = p["cash"] + z("short_term_investments")
    m["net_debt"] = m["total_debt"] - m["liquid_assets"]
    m["debt_current_share"] = _safe_div(z("debt_current") + z("short_term_borrowings"),
                                        m["funded_debt"])

    # -- liquidity ----------------------------------------------------------
    m["working_capital"] = p["current_assets"] - p["current_liabilities"]
    m["current_ratio"] = _safe_div(p["current_assets"], p["current_liabilities"])
    m["quick_ratio"] = _safe_div(m["liquid_assets"] + z("receivables"),
                                 p["current_liabilities"])
    m["cash_ratio"] = _safe_div(m["liquid_assets"], p["current_liabilities"])

    # -- cash generation ----------------------------------------------------
    m["fcf"] = p["ocf"] - p["capex"]
    m["fcf_ttm"] = p["ocf_ttm"] - p["capex_ttm"]
    m["shareholder_returns_ttm"] = p["buybacks_ttm"].fillna(0) + p["dividends_ttm"].fillna(0)
    burn = (-m["fcf_ttm"]).where(m["fcf_ttm"] < 0)
    m["runway_months"] = m["liquid_assets"] / (burn / 12)

    # -- earnings, leverage & coverage --------------------------------------
    m["ebitda_ttm"] = p["operating_income_ttm"] + p["da_ttm"]
    # "Adjusted" here = impairments added back; management's figure strips more.
    m["adj_ebitda_ttm"] = m["ebitda_ttm"] + p["impairments_ttm"].fillna(0)
    m["ebitdar_ttm"] = m["ebitda_ttm"] + p["op_lease_cost_ttm"]
    m["ebitda_margin_ttm"] = _safe_div(m["ebitda_ttm"], p["revenue_ttm"])
    m["debt_to_ebitda"] = _positive_div(m["total_debt"], m["ebitda_ttm"])
    m["net_debt_to_ebitda"] = _positive_div(m["net_debt"], m["ebitda_ttm"])
    m["lease_adj_leverage"] = _positive_div(m["lease_adjusted_debt"], m["ebitdar_ttm"])
    # Coverage is negative when earnings are negative; kept signed for charts.
    m["ebit_interest_coverage"] = _positive_div(p["operating_income_ttm"], p["interest_expense_ttm"])
    m["ebitda_interest_coverage"] = _positive_div(m["ebitda_ttm"], p["interest_expense_ttm"])
    # Signed version is still informative when EBITDA < 0 (how far underwater).
    m["ebitda_minus_interest_ttm"] = m["ebitda_ttm"] - p["interest_expense_ttm"]
    m["debt_to_assets"] = _safe_div(m["total_debt"], p["total_assets"])

    # -- market signal from the debt itself ---------------------------------
    # Fair value / carrying principal = price in cents on the dollar.
    # Older filings often omit the principal tag; non-current debt is the
    # closest proxy (for most issuers the fair-value footnote covers it).
    m["notes_principal_est"] = p["notes_principal"].fillna(p["debt_noncurrent"])
    m["debt_price_pct"] = _safe_div(p["debt_fair_value"], m["notes_principal_est"]) * 100

    # -- Altman Z'' (non-manufacturing / emerging-market version) -----------
    # Caveat: heavy buyback companies hold retired stock in treasury, so
    # retained earnings (X2) can stay large while book equity is negative,
    # which flatters the score (BBBY in 2022 is a textbook case).
    ta, tl = p["total_assets"], p["total_liabilities"]
    m["z_double_prime"] = (
        6.56 * _safe_div(m["working_capital"], ta)
        + 3.26 * _safe_div(p["retained_earnings"], ta)
        + 6.72 * _safe_div(p["operating_income_ttm"], ta)
        + 1.05 * _safe_div(p["equity"], tl)
    )
    return m


Z_ZONES = [(-np.inf, 1.1, "distress"), (1.1, 2.6, "grey"), (2.6, np.inf, "safe")]


def z_zone(z: float | None) -> str | None:
    if z is None or pd.isna(z):
        return None
    return next(name for lo, hi, name in Z_ZONES if lo <= z < hi)


def distress_flags(p: pd.DataFrame, m: pd.DataFrame, at=None) -> list[dict]:
    """Rule-based warning signals as of one period (default: latest)."""
    at = p.index[-1] if at is None else at
    r, x = p.loc[at], m.loc[at]
    prev = m.loc[:at].iloc[-5] if len(m.loc[:at]) >= 5 else None  # ~1 year earlier
    flags: list[dict] = []

    def add(severity, title, detail):
        flags.append({"severity": severity, "title": title, "detail": detail})

    usd = lambda v: f"{'-' if v < 0 else ''}${abs(v) / 1e6:,.0f}M"  # noqa: E731

    if pd.notna(x["ebitda_ttm"]) and x["ebitda_ttm"] < 0:
        add("critical", "Negative EBITDA",
            f"TTM EBITDA is {usd(x['ebitda_ttm'])}. The business is not covering "
            "its cash operating costs, so leverage multiples are undefined.")
    elif pd.notna(x["debt_to_ebitda"]) and x["debt_to_ebitda"] > 6:
        add("serious", "High leverage", f"Debt / EBITDA is {x['debt_to_ebitda']:.1f}x.")

    if (pd.notna(x["ebitda_interest_coverage"]) and x["ebitda_ttm"] > 0
            and x["ebitda_interest_coverage"] < 1.5):
        add("serious", "Thin interest coverage",
            f"EBITDA / interest is {x['ebitda_interest_coverage']:.2f}x.")

    if pd.notna(x["runway_months"]) and x["runway_months"] < 12:
        add("critical" if x["runway_months"] < 6 else "serious", "Short cash runway",
            f"Liquid assets of {usd(x['liquid_assets'])} cover about "
            f"{x['runway_months']:.1f} months of TTM free-cash-flow burn "
            f"({usd(-x['fcf_ttm'])} per year). Undrawn revolver capacity is not included.")

    if pd.notna(x["current_ratio"]) and x["current_ratio"] < 1:
        add("serious", "Current ratio below 1.0",
            f"Current assets cover {x['current_ratio']:.2f}x current liabilities.")

    if pd.notna(x["debt_current_share"]) and x["debt_current_share"] > 0.3:
        add("critical", "Debt reclassified as current",
            f"{x['debt_current_share']:.0%} of funded debt is due within 12 months. "
            "This often follows a drawn revolver/ABL, a covenant breach or "
            "acceleration. Check the 8-K item 2.04 filings in the Events tab.")

    if pd.notna(x["debt_price_pct"]) and x["debt_price_pct"] < 70:
        add("critical" if x["debt_price_pct"] < 40 else "serious", "Distressed debt pricing",
            f"The notes' fair value is about {x['debt_price_pct']:.0f} cents on the dollar. "
            "The market is pricing in a restructuring.")

    if pd.notna(r["equity"]) and r["equity"] < 0:
        add("critical", "Negative book equity",
            f"Shareholders' deficit of {usd(r['equity'])}.")

    zone = z_zone(x["z_double_prime"])
    if (zone != "distress" and pd.notna(r["equity"]) and r["equity"] < 0
            and pd.notna(r["retained_earnings"])
            and r["retained_earnings"] > 0):
        add("warning", "Z'' may be overstated",
            f"Retained earnings are {usd(r['retained_earnings'])} while equity is negative. "
            "Treasury-stock buybacks drained equity without reducing retained earnings, "
            "so the Z'' retained-earnings term flatters the score.")

    if zone == "distress":
        add("critical", "Altman Z'' in distress zone", f"Z'' = {x['z_double_prime']:.2f} (< 1.1).")
    elif zone == "grey":
        add("warning", "Altman Z'' in grey zone", f"Z'' = {x['z_double_prime']:.2f}.")

    if (pd.notna(x["shareholder_returns_ttm"]) and x["shareholder_returns_ttm"] > 0
            and pd.notna(x["fcf_ttm"]) and x["shareholder_returns_ttm"] > max(x["fcf_ttm"], 0)):
        add("warning", "Buybacks/dividends exceed free cash flow",
            f"{usd(x['shareholder_returns_ttm'])} returned to shareholders vs. "
            f"{usd(x['fcf_ttm'])} TTM FCF. Liquidity is being used to fund distributions.")

    if prev is not None and pd.notna(prev["liquid_assets"]) and prev["liquid_assets"] > 0:
        drop = 1 - x["liquid_assets"] / prev["liquid_assets"]
        if drop > 0.5:
            add("serious", "Liquidity collapse",
                f"Liquid assets fell {drop:.0%} year over year "
                f"({usd(prev['liquid_assets'])} to {usd(x['liquid_assets'])}).")
    return flags
