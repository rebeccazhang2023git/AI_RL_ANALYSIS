"""Offline tests on synthetic XBRL facts (no network)."""

import math

import pytest

from debt_analysis import restructuring as rs
from debt_analysis.financials import add_ttm, build_quarterly_panel, quarterly_flow_values
from debt_analysis.metrics import compute_metrics, distress_flags
from debt_analysis.service import clean

# Fiscal year starting 2022-02-27, quarter ends (52-week retail calendar)
Q_ENDS = ["2022-05-28", "2022-08-27", "2022-11-26", "2023-02-25"]
FY_START = "2022-02-27"


def fact(val, end, start=None, form="10-Q", filed="2023-01-01", accn=None, fy=2022, fp="Q1"):
    r = {"val": val, "end": end, "form": form, "filed": filed, "fy": fy, "fp": fp,
         "accn": accn or f"acc-{end}"}
    if start:
        r["start"] = start
    return r


def ytd_flow(quarterly):
    """10-Q style: Q1 direct + YTD; 10-K: full year only."""
    rows, cum = [], 0
    for i, (end, v) in enumerate(zip(Q_ENDS, quarterly)):
        cum += v
        form = "10-K" if i == 3 else "10-Q"
        rows.append(fact(cum, end, FY_START, form=form, accn=f"acc-{end}"))
    return rows


def company_facts():
    inst = lambda vals: [fact(v, e, accn=f"acc-{e}", fp=f"Q{i + 1}") for i, (e, v) in enumerate(zip(Q_ENDS, vals))]  # noqa: E731
    g = {
        "Assets": inst([5000, 4800, 4500, 4000]),
        "AssetsCurrent": inst([2500, 2300, 2000, 1500]),
        "LiabilitiesCurrent": inst([1800, 2000, 2500, 3000]),
        "Liabilities": inst([4500, 4700, 5000, 6000]),
        "CashAndCashEquivalentsAtCarryingValue": inst([400, 300, 150, 60]),
        "LongTermDebtNoncurrent": inst([1200, 1200, 1000, 1000]),
        "LongTermDebtCurrent": inst([0, 0, 900, 650]),
        "DebtInstrumentCarryingAmount": inst([1200, 1200, 1030, 1030]),
        "LongTermDebtFairValue": inst([600, 250, 200, 170]),
        "StockholdersEquity": inst([500, 100, -500, -2000]),
        "RetainedEarningsAccumulatedDeficit": inst([9000, 8600, 8200, 6500]),
        "OperatingIncomeLoss": ytd_flow([-300, -350, -450, -1600]),
        "DepreciationDepletionAndAmortization": ytd_flow([70, 70, 90, 200]),
        "InterestIncomeExpenseNonoperatingNet": ytd_flow([-16, -19, -34, -42]),
        "NetCashProvidedByUsedInOperatingActivities": ytd_flow([-380, -200, -310, -100]),
        "PaymentsToAcquirePropertyPlantAndEquipment": ytd_flow([100, 120, 100, 10]),
    }
    return {"cik": 1, "entityName": "TestCo",
            "facts": {"us-gaap": {k: {"units": {"USD": v}} for k, v in g.items()}}}


def test_deaccumulates_ytd_and_q4():
    g = company_facts()["facts"]["us-gaap"]
    q = quarterly_flow_values(g, "OperatingIncomeLoss")
    assert [q[e] for e in Q_ENDS] == [-300, -350, -450, -1600]


def test_panel_and_ttm():
    p = add_ttm(build_quarterly_panel(company_facts()))
    assert list(p.index.strftime("%Y-%m-%d")) == Q_ENDS
    assert p["interest_expense"].iloc[-1] == 42  # net interest sign-flipped
    assert p["operating_income_ttm"].iloc[-1] == -2700
    assert math.isnan(p["operating_income_ttm"].iloc[2])  # < 4 quarters
    assert p["fiscal_label"].iloc[0] == "FY2022 Q1"


def test_metrics_and_flags():
    p = add_ttm(build_quarterly_panel(company_facts()))
    m = compute_metrics(p)
    last = m.iloc[-1]
    assert last["total_debt"] == 1650
    assert last["ebitda_ttm"] == -2270
    assert math.isnan(last["debt_to_ebitda"])  # undefined for negative EBITDA
    assert last["debt_price_pct"] == pytest.approx(170 / 1030 * 100)
    titles = {f["title"] for f in distress_flags(p, m)}
    assert {"Negative EBITDA", "Short cash runway", "Debt reclassified as current",
            "Distressed debt pricing", "Negative book equity"} <= titles


def test_waterfall_absolute_priority():
    w = rs.WaterfallInputs(cash=100, inventory=1000, admin_cost_pct=0.1,
                           recovery_rates={"inventory": 0.5}, secured_debt=400,
                           unsecured_notes=600, trade_payables=400)
    r = rs.run_waterfall(w)
    # gross 600, admin 60, secured 400 paid in full, 140 left for 1,000 GUC
    by = {c["class"]: c for c in r["classes"]}
    assert r["gross_value"] == pytest.approx(600)
    assert by["Secured debt (ABL / FILO)"]["recovery_pct"] == 100
    assert by["Senior unsecured notes"]["recovery_pct"] == pytest.approx(14.0)
    assert by["Equity"]["recovery"] == 0
    assert r["fulcrum"] == "Senior unsecured notes"


def test_waterfall_secured_deficiency_joins_guc():
    w = rs.WaterfallInputs(cash=300, admin_cost_pct=0, secured_debt=500, unsecured_notes=500)
    by = {c["class"]: c for c in rs.run_waterfall(w)["classes"]}
    assert by["Secured debt (ABL / FILO)"]["recovery"] == 300
    assert by["Secured deficiency claim"]["claim"] == 200
    assert by["Senior unsecured notes"]["recovery"] == 0
    assert by["Equity"]["recovery"] == 0


def test_exchange_and_runway():
    x = rs.run_exchange(rs.ExchangeInputs(notes_face=1000, participation=0.5, price=0.4,
                                          consideration="new_secured_debt", old_coupon=0.05,
                                          new_coupon=0.10, other_debt=0, ebitda=100))
    assert x["discount_captured"] == pytest.approx(300)
    debt = next(r for r in x["table"] if r["metric"] == "Total debt")
    assert debt["post"] == pytest.approx(700)
    r = rs.run_runway(rs.RunwayInputs(starting_liquidity=100, monthly_burn=30, horizon_months=6))
    assert r["zero_month"] == 4


def test_from_dict_ignores_unknown_and_clean_handles_nan():
    w = rs.WaterfallInputs.from_dict({"cash": 5, "bogus": 1}, rs.WaterfallInputs(inventory=7))
    assert (w.cash, w.inventory) == (5, 7)
    assert clean({"a": float("nan"), "b": [1.0, float("inf")]}) == {"a": None, "b": [1.0, None]}
