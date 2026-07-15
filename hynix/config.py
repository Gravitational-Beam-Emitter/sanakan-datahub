"""
hynix configuration — SK Hynix cross-market instruments and FX pairs.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# DuckDB
DB_PATH = str(Path(__file__).resolve().parent / "hynix.duckdb")

# Base instrument
BASE_TICKER = "000660.KS"
BASE_NAME = "SK hynix"

# ═══════════════════════════════════════════════════════════════
#  Instrument catalog
# ═══════════════════════════════════════════════════════════════
# Each instrument has:
#   ticker:         yfinance / FinanceDataReader ticker
#   name:           display name
#   market:         KR, US, HK
#   currency:       KRW, USD, HKD
#   instrument_type: stock, adr, etp, etf
#   leverage:       multiplier (1.0, 2.0, -1.0, -2.0, etc.)
#   tracking_ratio: shares of 000660.KS represented by 1 unit
#                   (e.g., ADR 10:1 means tracking_ratio = 0.1)
#                   None = auto-estimated from prices
#   skh_weight:     fraction of instrument that is SK Hynix exposure
#                   (1.0 = pure single-stock, 0.23 = 23% weight)
#   yf_ticker:      override yfinance ticker if different from DB ticker
#   note:           human-readable description

INSTRUMENTS = [
    # ── Base: KR common stock ──
    {
        "ticker": "000660.KS",
        "name": "SK hynix (KR)",
        "market": "KR",
        "currency": "KRW",
        "instrument_type": "stock",
        "leverage": 1.0,
        "tracking_ratio": 1.0,
        "skh_weight": 1.0,
        "yf_ticker": "000660.KS",
        "note": "KOSPI common stock, base reference",
    },
    # ── US ADR ──
    {
        "ticker": "SKHY",
        "name": "SK hynix ADR (US)",
        "market": "US",
        "currency": "USD",
        "instrument_type": "adr",
        "leverage": 1.0,
        "tracking_ratio": 0.1,
        "skh_weight": 1.0,
        "yf_ticker": "SKHY",
        "note": "Nasdaq ADR, 10 ADR = 1 KR common share, listed 2026-07-10",
    },
    # ── HK 2x leveraged ETP ──
    {
        "ticker": "7709.HK",
        "name": "CSOP SK Hynix 2x LEP (HK)",
        "market": "HK",
        "currency": "HKD",
        "instrument_type": "etp",
        "leverage": 2.0,
        "tracking_ratio": None,
        "skh_weight": 1.0,
        "yf_ticker": "7709.HK",
        "note": "Swap-based synthetic 2x daily leveraged ETP, listed 2025-10-16",
    },
    # ── KR single-stock leveraged ETFs ──
    {
        "ticker": "0193T0.KS",
        "name": "KODEX SK Hynix Leverage",
        "market": "KR",
        "currency": "KRW",
        "instrument_type": "etf",
        "leverage": 2.0,
        "tracking_ratio": None,
        "skh_weight": 1.0,
        "yf_ticker": "0193T0.KS",
        "note": "Samsung KODEX single-stock 2x leveraged ETF",
    },
    # ── KR semiconductor ETFs with heavy SKH weight (secondary) ──
    # These are NOT pure SK Hynix plays but can be added for broader comparison.
    # Uncomment and adjust skh_weight as holdings change.
    # {
    #     "ticker": "469790.KS",
    #     "name": "KIWOOM K-TechTop10 ETF",
    #     "market": "KR", "currency": "KRW", "instrument_type": "etf",
    #     "leverage": 1.0, "tracking_ratio": None, "skh_weight": 0.23,
    #     "yf_ticker": "469790.KS",
    #     "note": "Top 10 KR tech stocks, ~23% SK Hynix weight",
    # },
]

# FX pairs we need
FX_PAIRS = [
    ("USD", "KRW"),
    ("HKD", "KRW"),
]

# Default lookback for historical fetch
DEFAULT_LOOKBACK_DAYS = 90
