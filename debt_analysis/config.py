"""Runtime configuration (override with environment variables)."""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# The SEC requires a descriptive User-Agent that includes a contact email:
# https://www.sec.gov/os/accessing-edgar-data
SEC_USER_AGENT = os.environ.get(
    "SEC_USER_AGENT", "DebtRestructuringAnalysis/0.1 research@example.com"
)

CACHE_DIR = Path(os.environ.get("EDGAR_CACHE_DIR", PROJECT_ROOT / "data" / "cache"))
CACHE_TTL_HOURS = float(os.environ.get("EDGAR_CACHE_TTL_HOURS", "24"))

# SEC fair-access limit is 10 requests/second; stay well under it.
MIN_REQUEST_INTERVAL_S = 0.15

DEFAULT_COMPANY = "BBBY"

# Delisted / renamed companies drop out of SEC's live ticker map, so keep
# well-known restructuring case studies here.
KNOWN_TICKERS = {
    "BBBY": 886158,  # Bed Bath & Beyond Inc. (now "20230930-DK-Butterfly-1, Inc.")
}
KNOWN_NAMES = {
    "BBBY": "Bed Bath & Beyond Inc.",
}
