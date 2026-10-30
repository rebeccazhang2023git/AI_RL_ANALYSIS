"""Yahoo Finance price history and its link to the quarterly analysis."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd
import requests

from . import config

SEARCH_URL = "https://query2.finance.yahoo.com/v1/finance/search"
NEWS_TTL_S = 15 * 60
CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
BROWSER_UA = "Mozilla/5.0 (compatible; DebtRestructuringAnalysis/0.1)"

# Quarter-end prices older than this (calendar days) are treated as missing,
# e.g. when a stock stopped trading mid-quarter.
MAX_STALE_DAYS = 10
MIN_CORR_OBS = 8
CORR_METRICS = [
    "z_double_prime", "current_ratio", "runway_months", "net_debt_to_ebitda",
    "debt_price_pct", "ebitda_margin_ttm", "fcf_ttm", "distress_flag_count",
]


class PriceError(RuntimeError):
    pass


class PriceClient:
    def __init__(self, cache_dir: Path = config.CACHE_DIR,
                 cache_ttl_hours: float = config.CACHE_TTL_HOURS):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_ttl_s = cache_ttl_hours * 3600
        self.session = requests.Session()
        self.session.headers["User-Agent"] = BROWSER_UA

    def history(self, symbol: str, start: str = "2010-01-01") -> pd.Series:
        """Daily split-adjusted closes indexed by trading date."""
        symbol = symbol.strip().upper().replace(".", "-")
        path = self.cache_dir / f"prices_{symbol}.json"
        if path.exists() and time.time() - path.stat().st_mtime < self.cache_ttl_s:
            raw = json.loads(path.read_text())
        else:
            raw = self._fetch(symbol, start)
            path.write_text(json.dumps(raw))
        s = pd.Series(raw["close"], index=pd.to_datetime(raw["dates"]), dtype=float)
        s = s[~s.index.duplicated(keep="last")].dropna().sort_index()
        if s.empty:
            raise PriceError(f"Yahoo returned no prices for {symbol}.")
        return s

    def news(self, symbol: str, limit: int = 25) -> list[dict]:
        """Recent Yahoo Finance headlines tagged with this ticker, newest first."""
        symbol = symbol.strip().upper()
        path = self.cache_dir / f"news_{symbol.replace('.', '-')}.json"
        if path.exists() and time.time() - path.stat().st_mtime < NEWS_TTL_S:
            raw = json.loads(path.read_text())
        else:
            resp = self.session.get(SEARCH_URL, timeout=30, params={
                "q": symbol, "newsCount": 50, "quotesCount": 0})
            if resp.status_code == 404:
                raise PriceError(f"Yahoo Finance has no news for '{symbol}'.")
            resp.raise_for_status()
            raw = resp.json().get("news") or []
            path.write_text(json.dumps(raw))
        items = [{
            "title": n.get("title"), "publisher": n.get("publisher"), "url": n.get("link"),
            "published": pd.Timestamp(n["providerPublishTime"], unit="s", tz="UTC").isoformat(),
            "tickers": n.get("relatedTickers", []),
        } for n in raw if n.get("title") and n.get("link") and n.get("providerPublishTime")]
        # The search is fuzzy: keep stories Yahoo actually tags with this ticker.
        items = [n for n in items if symbol in n["tickers"]]
        items.sort(key=lambda n: n["published"], reverse=True)
        return items[:limit]

    def _fetch(self, symbol: str, start: str) -> dict:
        params = {"period1": int(pd.Timestamp(start).timestamp()),
                  "period2": int(time.time()) + 86400, "interval": "1d"}
        resp = self.session.get(CHART_URL.format(symbol=symbol), params=params, timeout=30)
        if resp.status_code == 404:
            raise PriceError(f"Yahoo Finance has no data for symbol '{symbol}'.")
        resp.raise_for_status()
        result = (resp.json().get("chart", {}).get("result") or [None])[0]
        if not result or not result.get("timestamp"):
            raise PriceError(f"Yahoo Finance has no data for symbol '{symbol}'.")
        tz = result["meta"].get("exchangeTimezoneName", "America/New_York")
        dates = (pd.to_datetime(result["timestamp"], unit="s", utc=True)
                 .tz_convert(tz).tz_localize(None).normalize())
        close = result["indicators"]["quote"][0]["close"]
        return {"dates": [d.strftime("%Y-%m-%d") for d in dates], "close": close}


def check_overlap(close: pd.Series, first_period: pd.Timestamp, last_period: pd.Timestamp) -> None:
    """Tickers get reused after a company delists; reject history that starts
    after the company stopped reporting (or ended before it began)."""
    if close.index[0] > last_period or close.index[-1] < first_period:
        raise PriceError(
            f"Prices for this symbol cover {close.index[0]:%Y-%m-%d} to {close.index[-1]:%Y-%m-%d}, "
            f"which does not overlap the filings ({first_period:%Y-%m-%d} to {last_period:%Y-%m-%d}). "
            "The ticker may have been reassigned to another company.")


# -- analytics ---------------------------------------------------------------

def quarterly_price_stats(close: pd.Series, period_ends: pd.DatetimeIndex) -> pd.DataFrame:
    """Quarter-end close, quarter return and drawdown from the running peak."""
    peak = close.cummax()
    rows = []
    for end in period_ends:
        hist = close.loc[:end]
        if hist.empty or (end - hist.index[-1]).days > MAX_STALE_DAYS:
            rows.append((None, None))
            continue
        rows.append((float(hist.iloc[-1]), float(hist.iloc[-1] / peak.loc[hist.index[-1]] - 1)))
    out = pd.DataFrame(rows, index=period_ends, columns=["price_close", "price_drawdown"], dtype=float)
    out["price_qtr_return"] = out["price_close"].pct_change(fill_method=None)
    return out[["price_close", "price_qtr_return", "price_drawdown"]]


def event_reactions(close: pd.Series, events: list[dict]) -> list[dict]:
    """Price move around each filing: close before the filing date to the close
    of the next trading day, so after-hours 8-Ks are captured."""
    out = []
    for e in events:
        filed = pd.Timestamp(e["filed"])
        before = close.loc[:filed - pd.Timedelta(days=1)]
        on_or_after = close.loc[filed:]
        move, px = None, None
        if len(before) and len(on_or_after) and (on_or_after.index[0] - filed).days <= 5:
            after = on_or_after.iloc[min(1, len(on_or_after) - 1)]
            px = float(on_or_after.iloc[0])
            if len(on_or_after) > 1:
                move = float(after / before.iloc[-1] - 1)
        out.append({"filed": e["filed"], "price": px, "move_1d": move})
    return out


def summarize_reactions(events: list[dict], reactions: list[dict]) -> list[dict]:
    """Average move by event relevance class."""
    by: dict[str, list[float]] = {}
    for e, r in zip(events, reactions):
        if r["move_1d"] is not None:
            by.setdefault(e["relevance"], []).append(r["move_1d"])
    return [{"relevance": k, "n": len(v), "avg_move": sum(v) / len(v),
             "median_move": float(pd.Series(v).median()), "pct_negative": sum(x < 0 for x in v) / len(v)}
            for k, v in sorted(by.items())]


def _rank_corr(a: pd.Series, b: pd.Series):
    df = pd.concat([a, b], axis=1).dropna()
    if len(df) < MIN_CORR_OBS:
        return None, len(df)
    c = df.rank().corr().iloc[0, 1]
    return (None if pd.isna(c) else float(c)), len(df)


def price_correlations(stats: pd.DataFrame, metrics: pd.DataFrame) -> list[dict]:
    """Spearman correlation of each signal with stock returns.

    - level_vs_next: signal level now vs the NEXT quarter's return (does it lead?)
    - change_vs_same: QoQ change in the signal vs THIS quarter's return (does it move together?)
    """
    ret = stats["price_qtr_return"]
    out = []
    for col in CORR_METRICS:
        if col not in metrics:
            continue
        lvl, n1 = _rank_corr(metrics[col], ret.shift(-1))
        chg, n2 = _rank_corr(metrics[col].diff(), ret)
        out.append({"metric": col, "level_vs_next": lvl, "n_level": n1,
                    "change_vs_same": chg, "n_change": n2})
    return out
