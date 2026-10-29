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
            raise EdgarError(
                f"Ticker '{q}' not found in SEC's current ticker list. "
                "Delisted companies must be looked up by CIK."
            )
        return cik


def filing_url(cik: int, accession: str, document: str = "") -> str:
    return (
        f"https://www.sec.gov/Archives/edgar/data/{cik}/"
        f"{accession.replace('-', '')}/{document}"
    )
