"""Minimal SEC EDGAR client with on-disk caching and polite rate limiting."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import requests

from . import config

DATA_BASE = "https://data.sec.gov"
TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"


class EdgarError(RuntimeError):
    pass


class EdgarClient:
    def __init__(
        self,
        user_agent: str = config.SEC_USER_AGENT,
        cache_dir: Path = config.CACHE_DIR,
        cache_ttl_hours: float = config.CACHE_TTL_HOURS,
    ):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_ttl_s = cache_ttl_hours * 3600
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}
        )
        self._lock = threading.Lock()
        self._last_request = 0.0

    # -- low level ---------------------------------------------------------

    def _get_json(self, url: str, cache_name: str) -> dict:
        path = self.cache_dir / cache_name
        if path.exists() and time.time() - path.stat().st_mtime < self.cache_ttl_s:
            return json.loads(path.read_text())

        with self._lock:
            wait = config.MIN_REQUEST_INTERVAL_S - (time.time() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            try:
                resp = self.session.get(url, timeout=30)
            finally:
                self._last_request = time.time()

        if resp.status_code == 404:
            raise EdgarError(f"Not found on EDGAR: {url}")
        if resp.status_code == 403:
            raise EdgarError(
                "EDGAR rejected the request (403). Set SEC_USER_AGENT to "
                "'YourApp your.email@domain.com' and retry."
            )
        resp.raise_for_status()
        data = resp.json()
        path.write_text(json.dumps(data))
        return data

    # -- endpoints ---------------------------------------------------------

    def company_facts(self, cik: int) -> dict:
        """All XBRL facts a company has reported (10-K, 10-Q, ...)."""
        return self._get_json(
            f"{DATA_BASE}/api/xbrl/companyfacts/CIK{cik:010d}.json",
            f"companyfacts_{cik}.json",
        )

    def submissions(self, cik: int) -> dict:
        """Company metadata plus its recent filing index."""
        return self._get_json(
            f"{DATA_BASE}/submissions/CIK{cik:010d}.json", f"submissions_{cik}.json"
        )

    def ticker_map(self) -> dict[str, int]:
        raw = self._get_json(TICKER_MAP_URL, "company_tickers.json")
        return {row["ticker"].upper(): int(row["cik_str"]) for row in raw.values()}

    def search_companies(self, query: str, limit: int = 10) -> list[dict]:
        """Find companies by ticker, name or CIK.

        Ranking: exact ticker, ticker prefix, name prefix, name substring.
        Returns [{"ticker", "name", "cik"}].
        """
        q = query.strip().upper()
        if not q:
            return []
        rows = [
            {"ticker": r["ticker"].upper(), "name": r["title"], "cik": int(r["cik_str"])}
            for r in self._get_json(TICKER_MAP_URL, "company_tickers.json").values()
        ]
        seen = {r["ticker"] for r in rows}
        rows += [
            {"ticker": t, "name": config.KNOWN_NAMES.get(t, t), "cik": c}
            for t, c in config.KNOWN_TICKERS.items() if t not in seen
        ]

        def rank(r: dict) -> int | None:
            name = r["name"].upper()
            if q.isdigit():
                c = str(r["cik"])
                return 0 if c == q.lstrip("0") else 1 if c.startswith(q.lstrip("0") or q) else None
            if r["ticker"] == q:
                return 0
            if r["ticker"].startswith(q):
                return 1
            if name.startswith(q):
                return 2
            if q in name:
                return 3
            return None

        ranked = [(k, r["ticker"], r) for r in rows if (k := rank(r)) is not None]
        ranked.sort(key=lambda t: (t[0], len(t[1]), t[1]))
        return [r for _, _, r in ranked[:limit]]

    def resolve_cik(self, query: str) -> int:
        """Accept a CIK (digits) or ticker symbol."""
        q = query.strip().upper()
        if not q:
            raise EdgarError("Enter a ticker or CIK.")
        if q.isdigit():
            return int(q)
        if q in config.KNOWN_TICKERS:
            return config.KNOWN_TICKERS[q]
        cik = self.ticker_map().get(q)
        if cik is None:
            # Fall back to name search so "apple" or "Tesla Inc" also work.
            hits = self.search_companies(query, limit=1)
            if hits:
                return hits[0]["cik"]
            raise EdgarError(
                f"No company matching '{query.strip()}' in SEC's current list. "
                "Delisted companies must be looked up by CIK."
            )
        return cik


def filing_url(cik: int, accession: str, document: str = "") -> str:
    return (
        f"https://www.sec.gov/Archives/edgar/data/{cik}/"
        f"{accession.replace('-', '')}/{document}"
    )
