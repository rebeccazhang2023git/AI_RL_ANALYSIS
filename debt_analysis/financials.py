"""Turn SEC XBRL "company facts" into a clean quarterly panel.

Two quirks of EDGAR data are handled here:

* 10-Q cash-flow statements are year-to-date, and Q4 is never filed on its
  own. Quarterly flows are recovered by differencing cumulative periods that
  share a start date (e.g. Q4 = FY - 9M YTD).
* Companies switch XBRL tags over time (e.g. ``Revenues`` ->
  ``RevenueFromContractWithCustomerExcludingAssessedTax`` after ASC 606), so
  every line item has an ordered list of fallback tags, tried per period.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date

import numpy as np
import pandas as pd

PERIODIC_FORMS = {"10-K", "10-K/A", "10-Q", "10-Q/A", "10-KT", "10-KT/A"}
QUARTER_DAYS = (70, 105)  # 52/53-week retail calendars produce 84-98 day quarters

# column -> ordered fallback tags. A tag may be (tag, sign) to flip its sign.
INSTANT_ITEMS: dict[str, list] = {
    "cash": ["CashAndCashEquivalentsAtCarryingValue",
             "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    "short_term_investments": ["ShortTermInvestments",
                               "AvailableForSaleSecuritiesDebtSecuritiesCurrent",
                               "MarketableSecuritiesCurrent"],
    "receivables": ["AccountsReceivableNetCurrent",
                    "CreditAndDebitCardReceivablesAtCarryingValue",
                    "ReceivablesNetCurrent"],
    "inventory": ["InventoryNet", "RetailRelatedInventoryMerchandise"],
    "current_assets": ["AssetsCurrent"],
    "ppe_net": ["PropertyPlantAndEquipmentNet",
                "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization"],
    "total_assets": ["Assets"],
    "accounts_payable": ["AccountsPayableCurrent"],
    "accrued_liabilities": ["AccruedLiabilitiesCurrent"],
    "current_liabilities": ["LiabilitiesCurrent"],
    "total_liabilities": ["Liabilities"],
    "debt_current": ["LongTermDebtCurrent", "DebtCurrent",
                     "LongTermDebtAndCapitalLeaseObligationsCurrent"],
    "short_term_borrowings": ["ShortTermBorrowings", "LineOfCredit"],
    # LongTermDebt (total incl. current) is a last resort for filers that never
    # split it; for those filers debt_current is normally absent.
    "debt_noncurrent": ["LongTermDebtNoncurrent",
                        "LongTermDebtAndCapitalLeaseObligations", "LongTermDebt"],
    "notes_principal": ["DebtInstrumentCarryingAmount"],
    "debt_fair_value": ["LongTermDebtFairValue", "DebtInstrumentFairValue"],
    "op_lease_rou_asset": ["OperatingLeaseRightOfUseAsset"],
    "op_lease_current": ["OperatingLeaseLiabilityCurrent"],
    "op_lease_noncurrent": ["OperatingLeaseLiabilityNoncurrent"],
    "finance_lease_current": ["FinanceLeaseLiabilityCurrent"],
    "finance_lease_noncurrent": ["FinanceLeaseLiabilityNoncurrent"],
    "lease_payments_next_12m": ["LesseeOperatingLeaseLiabilityPaymentsDueNextTwelveMonths",
                                "OperatingLeasesFutureMinimumPaymentsDueCurrent"],
    "retained_earnings": ["RetainedEarningsAccumulatedDeficit"],
    "equity": ["StockholdersEquity",
               "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
}

FLOW_ITEMS: dict[str, list] = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
                "SalesRevenueNet", "SalesRevenueGoodsNet"],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "da": ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization",
           "DepreciationAmortizationAndAccretionNet", "Depreciation"],
    # Net interest is used (negated) when gross interest expense isn't tagged.
    "interest_expense": ["InterestExpense", "InterestExpenseNonoperating",
                         "InterestExpenseDebt",
                         ("InterestIncomeExpenseNonoperatingNet", -1),
                         ("InterestIncomeExpenseNet", -1)],
    "interest_paid": ["InterestPaidNet", "InterestPaid"],
    "op_lease_cost": ["OperatingLeaseCost"],
    "impairments": ["AssetImpairmentCharges", "GoodwillAndIntangibleAssetImpairment"],
    "ocf": ["NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment",
              "PaymentsToAcquireProductiveAssets"],
    "buybacks": ["PaymentsForRepurchaseOfCommonStock"],
    "dividends": ["PaymentsOfDividendsCommonStock", "PaymentsOfDividends"],
    "debt_issued": ["ProceedsFromIssuanceOfLongTermDebt", "ProceedsFromLinesOfCredit"],
    "debt_repaid": ["RepaymentsOfLongTermDebt", "RepaymentsOfLinesOfCredit"],
    "equity_issued": ["ProceedsFromIssuanceOfCommonStock"],
}

LABELS = {
    "cash": "Cash & equivalents", "short_term_investments": "Short-term investments",
    "receivables": "Receivables", "inventory": "Inventory",
    "current_assets": "Current assets", "ppe_net": "PP&E, net",
    "total_assets": "Total assets", "accounts_payable": "Accounts payable",
    "accrued_liabilities": "Accrued liabilities",
    "current_liabilities": "Current liabilities",
    "total_liabilities": "Total liabilities", "debt_current": "Debt, current",
    "short_term_borrowings": "Short-term borrowings",
    "debt_noncurrent": "Debt, non-current", "notes_principal": "Notes principal",
    "debt_fair_value": "Debt fair value",
    "op_lease_rou_asset": "Operating lease ROU asset",
    "op_lease_current": "Operating lease liab., current",
    "op_lease_noncurrent": "Operating lease liab., non-current",
    "finance_lease_current": "Finance lease liab., current",
    "finance_lease_noncurrent": "Finance lease liab., non-current",
    "lease_payments_next_12m": "Lease payments due next 12m",
    "retained_earnings": "Retained earnings (deficit)",
    "equity": "Shareholders' equity", "revenue": "Revenue",
    "gross_profit": "Gross profit", "operating_income": "Operating income (EBIT)",
    "net_income": "Net income", "da": "Depreciation & amortization",
    "interest_expense": "Interest expense", "interest_paid": "Cash interest paid",
    "op_lease_cost": "Operating lease cost", "impairments": "Impairments",
    "ocf": "Operating cash flow", "capex": "Capital expenditures",
    "buybacks": "Share repurchases", "dividends": "Dividends paid",
    "debt_issued": "Debt issued", "debt_repaid": "Debt repaid",
    "equity_issued": "Equity issued",
}


def _d(s: str) -> date:
    return date.fromisoformat(s)


def _usd_facts(us_gaap: dict, tag: str) -> list[dict]:
    node = us_gaap.get(tag)
    if not node:
        return []
    rows = node.get("units", {}).get("USD", [])
    rows = [r for r in rows if r.get("form") in PERIODIC_FORMS]
    return sorted(rows, key=lambda r: r["filed"])  # later filings overwrite


def instant_values(us_gaap: dict, tag: str) -> dict[str, float]:
    return {r["end"]: float(r["val"]) for r in _usd_facts(us_gaap, tag) if "start" not in r}


def quarterly_flow_values(us_gaap: dict, tag: str) -> dict[str, float]:
    """Quarterly values for a duration tag, de-accumulating YTD/annual facts."""
    by_end: dict[str, dict[str, float]] = defaultdict(dict)
    for r in _usd_facts(us_gaap, tag):
        if "start" in r:
            by_end[r["end"]][r["start"]] = float(r["val"])

    lo, hi = QUARTER_DAYS
    out: dict[str, float] = {}
    for end, by_start in by_end.items():
        e = _d(end)
        direct = [v for s, v in by_start.items() if lo <= (e - _d(s)).days <= hi]
        if direct:
            out[end] = direct[0]
            continue
        # cumulative(S..E) - cumulative(S..E_prev), with E_prev one quarter earlier
        for start in sorted(by_start, reverse=True):  # shortest cumulative period first
            for prev_end, prev_by_start in by_end.items():
                if start in prev_by_start and lo <= (e - _d(prev_end)).days <= hi:
                    out[end] = by_start[start] - prev_by_start[start]
                    break
            if end in out:
                break
    return out


def _merge_tags(us_gaap: dict, tags: list, extractor) -> tuple[dict, dict]:
    values: dict[str, float] = {}
    source: dict[str, str] = {}
    for spec in tags:
        tag, sign = spec if isinstance(spec, tuple) else (spec, 1)
        for end, v in extractor(us_gaap, tag).items():
            if end not in values:
                values[end] = sign * v
                source[end] = tag
    return values, source


def _period_labels(us_gaap: dict) -> dict[str, dict]:
    """Fiscal label + original filing for each balance-sheet date."""
    rows = [r for r in _usd_facts(us_gaap, "Assets") if "start" not in r]
    # A filing's own report date is the latest balance-sheet date it contains;
    # earlier dates in it are comparatives and carry the wrong fy/fp.
    report_date: dict[str, str] = {}
    for r in rows:
        report_date[r["accn"]] = max(report_date.get(r["accn"], ""), r["end"])

    labels: dict[str, dict] = {}
    for r in rows:  # sorted by filed date, so the original (not an amendment) wins
        if report_date[r["accn"]] != r["end"] or r["end"] in labels:
            continue
        fp = "Q4" if r["fp"] == "FY" else r["fp"]
        labels[r["end"]] = {
            "fiscal_label": f"FY{r['fy']} {fp}",
            "form": r["form"], "filed": r["filed"], "accession": r["accn"],
        }
    return labels


def build_quarterly_panel(company_facts: dict) -> pd.DataFrame:
    """One row per fiscal quarter-end, columns in raw USD."""
    us_gaap = company_facts.get("facts", {}).get("us-gaap", {})
    if not us_gaap:
        raise ValueError("Company has no us-gaap XBRL facts.")

    columns: dict[str, dict] = {}
    sources: dict[str, dict] = {}
    for col, tags in INSTANT_ITEMS.items():
        columns[col], sources[col] = _merge_tags(us_gaap, tags, instant_values)
    for col, tags in FLOW_ITEMS.items():
        columns[col], sources[col] = _merge_tags(us_gaap, tags, quarterly_flow_values)

    labels = _period_labels(us_gaap)
    ends = sorted(columns["total_assets"])
    if not ends:
        raise ValueError("No balance-sheet periods found.")

    df = pd.DataFrame(index=pd.to_datetime(ends))
    df.index.name = "period_end"
    for col, vals in columns.items():
        df[col] = [vals.get(e, np.nan) for e in ends]
    meta = pd.DataFrame([labels.get(e, {}) for e in ends], index=df.index)
    df = pd.concat([meta, df], axis=1)

    # Fiscal 10-K comparatives can introduce stray dates (e.g. 2 years back
    # with only Assets). Keep periods with a full balance sheet.
    df = df[df["current_assets"].notna() & df["current_liabilities"].notna()]
    df.attrs["sources"] = {
        col: sorted(set(src.values())) for col, src in sources.items() if src
    }
    df.attrs["entity_name"] = company_facts.get("entityName")
    df.attrs["cik"] = company_facts.get("cik")
    return df


def add_ttm(df: pd.DataFrame) -> pd.DataFrame:
    """Trailing-twelve-month sums of every flow column (``<col>_ttm``).

    TTM is only reported where four consecutive quarters exist.
    """
    out = df.copy()
    idx = out.index.to_series()
    span_days = (idx - idx.shift(3)).dt.days
    contiguous = span_days.between(250, 300)
    for col in FLOW_ITEMS:
        ttm = out[col].rolling(4, min_periods=4).sum()
        out[f"{col}_ttm"] = ttm.where(contiguous)
    return out
