"""Glue between EDGAR, the analytics, and the web layer (JSON-ready output)."""

from __future__ import annotations

import math
import threading
from dataclasses import asdict

import pandas as pd

from . import restructuring as rs
from .edgar import EdgarClient, filing_url
from .events import filing_timeline
from .financials import LABELS, add_ttm, build_quarterly_panel
from .metrics import compute_metrics, distress_flags, z_zone


class CompanyAnalysis:
    def __init__(self, cik: int, facts: dict, submissions: dict):
        self.cik = cik
        self.panel = add_ttm(build_quarterly_panel(facts))
        self.metrics = compute_metrics(self.panel)
        self.submissions = submissions

    def period(self, period_end: str | None):
        if not period_end:
            return self.panel.index[-1]
        ts = pd.Timestamp(period_end)
        if ts not in self.panel.index:
            raise KeyError(f"No data for period {period_end}")
        return ts

    def rows(self, at):
        return self.panel.loc[at], self.metrics.loc[at]


class AnalysisService:
    def __init__(self, client: EdgarClient | None = None):
        self.client = client or EdgarClient()
        self._cache: dict[int, CompanyAnalysis] = {}
        self._lock = threading.Lock()

    def get(self, query: str) -> CompanyAnalysis:
        cik = self.client.resolve_cik(query)
        with self._lock:
            if cik not in self._cache:
                self._cache[cik] = CompanyAnalysis(
                    cik, self.client.company_facts(cik), self.client.submissions(cik))
            return self._cache[cik]

    # -- payloads -----------------------------------------------------------

    def overview(self, query: str) -> dict:
        a = self.get(query)
        p, m = a.panel, a.metrics
        sub = a.submissions
        series = pd.concat([p.drop(columns=["form", "filed", "accession"]), m], axis=1)
        series.index = series.index.strftime("%Y-%m-%d")
        latest = p.index[-1]

        periods = [{
            "end": ts.strftime("%Y-%m-%d"),
            "label": p.at[ts, "fiscal_label"] if isinstance(p.at[ts, "fiscal_label"], str) else ts.strftime("%Y-%m-%d"),
            "form": p.at[ts, "form"] if isinstance(p.at[ts, "form"], str) else None,
            "url": filing_url(a.cik, p.at[ts, "accession"]) if isinstance(p.at[ts, "accession"], str) else None,
        } for ts in p.index]

        return clean({
            "company": {
                "cik": a.cik, "name": sub.get("name"),
                "former_names": [f["name"] for f in sub.get("formerNames", [])],
                "tickers": sub.get("tickers", []), "sic": sub.get("sicDescription"),
                "fiscal_year_end": sub.get("fiscalYearEnd"),
            },
            "periods": periods,
            "latest": latest.strftime("%Y-%m-%d"),
            "series": {col: series[col].tolist() for col in series.columns if col != "fiscal_label"},
            "labels": LABELS,
            "sources": p.attrs.get("sources", {}),
            "events": filing_timeline(sub),
        })

    def snapshot(self, query: str, period_end: str | None = None) -> dict:
        a = self.get(query)
        at = a.period(period_end)
        p_row, m_row = a.rows(at)
        return clean({
            "period_end": at.strftime("%Y-%m-%d"),
            "fiscal_label": p_row["fiscal_label"],
            "values": {**p_row.drop(["form", "filed", "accession", "fiscal_label"]).to_dict(),
                       **m_row.to_dict()},
            "z_zone": z_zone(m_row["z_double_prime"]),
            "flags": distress_flags(a.panel, a.metrics, at),
            "defaults": {
                "waterfall": asdict(rs.default_waterfall_inputs(p_row, m_row)),
                "exchange": asdict(rs.default_exchange_inputs(p_row, m_row)),
                "runway": asdict(rs.default_runway_inputs(p_row, m_row)),
            },
        })

    def waterfall(self, query: str, period_end: str | None, overrides: dict) -> dict:
        a = self.get(query)
        base = rs.default_waterfall_inputs(*a.rows(a.period(period_end)))
        return clean(rs.run_waterfall(rs.WaterfallInputs.from_dict(overrides, base)))

    def exchange(self, query: str, period_end: str | None, overrides: dict) -> dict:
        a = self.get(query)
        base = rs.default_exchange_inputs(*a.rows(a.period(period_end)))
        return clean(rs.run_exchange(rs.ExchangeInputs.from_dict(overrides, base)))

    def runway(self, query: str, period_end: str | None, overrides: dict) -> dict:
        a = self.get(query)
        base = rs.default_runway_inputs(*a.rows(a.period(period_end)))
        return clean(rs.run_runway(rs.RunwayInputs.from_dict(overrides, base)))

    def csv(self, query: str) -> str:
        a = self.get(query)
        return pd.concat([a.panel, a.metrics], axis=1).to_csv()


def clean(obj):
    """Make pandas/numpy output JSON-safe (NaN/inf -> None, numpy -> python)."""
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    if hasattr(obj, "item") and not isinstance(obj, (str, bytes)):
        obj = obj.item()
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    return obj
