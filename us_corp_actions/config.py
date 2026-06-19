"""
Configuration — loads environment and defines paths.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path)

# ── Paths ──
DB_PATH = str(Path(__file__).resolve().parent.parent / "us_corp_actions.duckdb")

# ── SEC EDGAR ──
SEC_RSS_URL = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&count=100&output=atom"
SEC_SUBMISSIONS_API = "https://data.sec.gov/submissions/CIK{}.json"
SEC_COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

# SEC requires a User-Agent header
SEC_HEADERS = {
    "User-Agent": "Cibo Datahub (contact@cibo.io)",
    "Accept-Encoding": "gzip, deflate",
}

# ── Pipeline ──
LOOKBACK_DAYS = 90  # Data retention: auto-delete records older than this
BACKFILL_START = "2026-06-10"  # Start date for historical backfill

# Rate limiting: SEC allows ~10 requests/second, we stay well under
SEC_RATE_LIMIT = 0.15  # seconds between requests
