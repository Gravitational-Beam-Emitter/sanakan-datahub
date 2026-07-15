"""
Configuration — loads environment and defines paths, tracked companies, rate limits.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path)

# ── Paths ──
DB_PATH = str(Path(__file__).resolve().parent.parent / "announcements.duckdb")
FILES_DIR = str(Path(__file__).resolve().parent / "files")

# ── Pipeline ──
LOOKBACK_DAYS = 30       # How many days back to fetch on each daily run
BACKFILL_START = "2026-01-01"  # Start date for initial backfill
SEC_RATE_LIMIT = 0.15    # Seconds between SEC requests
HKEX_RATE_LIMIT = 0.5    # Seconds between HKEXnews requests
CNINFO_RATE_LIMIT = 0.3  # Seconds between CNINFO requests

# SEC requires a User-Agent header with contact info
SEC_HEADERS = {
    "User-Agent": "Cibo Datahub (contact@cibo.io)",
    "Accept-Encoding": "gzip, deflate",
}

# ── TRACKED COMPANIES ──
# v1: 1 per market. To track additional companies, add entries to this list.
TRACKED_COMPANIES: list[dict] = [
    {
        "ticker": "AAPL",
        "market": "us",
        "name": "Apple Inc.",
        "cik": "0000320193",
    },
    {
        "ticker": "0700",
        "market": "hk",
        "name": "Tencent Holdings Ltd.",
        "hkex_code": "00700",
    },
    {
        "ticker": "600519",
        "market": "cn",
        "name": "Kweichow Moutai Co., Ltd.",
        "cninfo_code": "600519",
        "org_id": "gssh600519",
    },
]
