"""
Configuration — data source URLs, crypto product lists, rate limits.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path)

# ── Paths ──
DB_PATH = str(Path(__file__).resolve().parent.parent / "us_listings.duckdb")

# ── NASDAQ IPO Calendar ──
NASDAQ_IPO_URL = "https://api.nasdaq.com/api/ipo/calendar"
NASDAQ_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json",
}

# ── SEC EDGAR ──
SEC_HEADERS = {
    "User-Agent": "Cibo Datahub (contact@cibo.io)",
    "Accept-Encoding": "gzip, deflate",
}
SEC_COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_API = "https://data.sec.gov/submissions/CIK{}.json"

# ── Pipeline ──
LOOKBACK_DAYS = 90
BACKFILL_START = "2026-05-01"
SEC_RATE_LIMIT = 0.15
YFINANCE_RATE_LIMIT = 0.5

# ── Crypto Keywords (case-insensitive match on company name / description) ──
CRYPTO_KEYWORDS = [
    "bitcoin", "ethereum", "crypto", "blockchain",
    "digital asset", "web3", "defi", "token",
    "mining", "proof of", "validator", "staking",
    "custody", "satoshi", "nakamoto",
]

# ── Known Crypto Products (hardcoded, updated periodically) ──
# Format: { ticker: { product_type, underlying_asset, issuer } }
# product_type: spot_etf | futures_etf | etp | crypto_stock | blockchain
KNOWN_CRYPTO_PRODUCTS = {
    # ── Bitcoin Spot ETFs (approved Jan 2024) ──
    "IBIT": {"product_type": "spot_etf", "underlying_asset": "Bitcoin", "issuer": "BlackRock"},
    "FBTC": {"product_type": "spot_etf", "underlying_asset": "Bitcoin", "issuer": "Fidelity"},
    "GBTC": {"product_type": "etp", "underlying_asset": "Bitcoin", "issuer": "Grayscale"},
    "ARKB": {"product_type": "spot_etf", "underlying_asset": "Bitcoin", "issuer": "ARK/21Shares"},
    "BITB": {"product_type": "spot_etf", "underlying_asset": "Bitcoin", "issuer": "Bitwise"},
    "BTCO": {"product_type": "spot_etf", "underlying_asset": "Bitcoin", "issuer": "Invesco"},
    "EZBC": {"product_type": "spot_etf", "underlying_asset": "Bitcoin", "issuer": "Franklin Templeton"},
    "BRRR": {"product_type": "spot_etf", "underlying_asset": "Bitcoin", "issuer": "Valkyrie"},
    "HODL": {"product_type": "spot_etf", "underlying_asset": "Bitcoin", "issuer": "VanEck"},
    "BTCW": {"product_type": "spot_etf", "underlying_asset": "Bitcoin", "issuer": "WisdomTree"},
    "DEFI": {"product_type": "spot_etf", "underlying_asset": "Bitcoin", "issuer": "Hashdex"},
    "BTC":  {"product_type": "etp", "underlying_asset": "Bitcoin", "issuer": "Grayscale Mini BTC"},

    # ── Ethereum Spot ETFs (approved Jul 2024) ──
    "ETHA": {"product_type": "spot_etf", "underlying_asset": "Ethereum", "issuer": "BlackRock"},
    "FETH": {"product_type": "spot_etf", "underlying_asset": "Ethereum", "issuer": "Fidelity"},
    "ETHW": {"product_type": "spot_etf", "underlying_asset": "Ethereum", "issuer": "Bitwise"},
    "CETH": {"product_type": "spot_etf", "underlying_asset": "Ethereum", "issuer": "21Shares"},
    "ETHV": {"product_type": "spot_etf", "underlying_asset": "Ethereum", "issuer": "VanEck"},
    "QETH": {"product_type": "spot_etf", "underlying_asset": "Ethereum", "issuer": "Invesco"},
    "EZET": {"product_type": "spot_etf", "underlying_asset": "Ethereum", "issuer": "Franklin Templeton"},
    "ETHE": {"product_type": "etp", "underlying_asset": "Ethereum", "issuer": "Grayscale"},
    "ETH":  {"product_type": "etp", "underlying_asset": "Ethereum", "issuer": "Grayscale Mini ETH"},

    # ── Bitcoin Futures ETFs ──
    "BITO": {"product_type": "futures_etf", "underlying_asset": "Bitcoin", "issuer": "ProShares"},
    "BITI": {"product_type": "futures_etf", "underlying_asset": "Bitcoin", "issuer": "ProShares Short"},
    "BTF":  {"product_type": "futures_etf", "underlying_asset": "Bitcoin", "issuer": "Valkyrie"},
    "XBTF": {"product_type": "futures_etf", "underlying_asset": "Bitcoin", "issuer": "VanEck"},

    # ── Crypto Stocks (pure-play crypto companies) ──
    "COIN": {"product_type": "crypto_stock", "underlying_asset": "Multi-asset", "issuer": "Coinbase"},
    "MSTR": {"product_type": "crypto_stock", "underlying_asset": "Bitcoin", "issuer": "MicroStrategy"},
    "MARA": {"product_type": "crypto_stock", "underlying_asset": "Bitcoin", "issuer": "Marathon Digital"},
    "RIOT": {"product_type": "crypto_stock", "underlying_asset": "Bitcoin", "issuer": "Riot Platforms"},
    "CLSK": {"product_type": "crypto_stock", "underlying_asset": "Bitcoin", "issuer": "CleanSpark"},
    "HUT":  {"product_type": "crypto_stock", "underlying_asset": "Bitcoin", "issuer": "Hut 8"},
    "WULF": {"product_type": "crypto_stock", "underlying_asset": "Bitcoin", "issuer": "Terawulf"},
    "IREN": {"product_type": "crypto_stock", "underlying_asset": "Bitcoin", "issuer": "Iris Energy"},
    "CORZ": {"product_type": "crypto_stock", "underlying_asset": "Bitcoin", "issuer": "Core Scientific"},
    "BTDR": {"product_type": "crypto_stock", "underlying_asset": "Bitcoin", "issuer": "Bitdeer"},
    "CAN":  {"product_type": "crypto_stock", "underlying_asset": "Bitcoin", "issuer": "Canaan"},
    "HOOD": {"product_type": "crypto_stock", "underlying_asset": "Multi-asset", "issuer": "Robinhood"},
    "SQ":   {"product_type": "blockchain", "underlying_asset": "Bitcoin", "issuer": "Block (Square)"},

    # ── Blockchain ETFs ──
    "BKCH": {"product_type": "spot_etf", "underlying_asset": "Multi-asset", "issuer": "Global X Blockchain ETF"},
    "BLOK": {"product_type": "spot_etf", "underlying_asset": "Multi-asset", "issuer": "Amplify Transformational Data Sharing ETF"},
    "BITQ": {"product_type": "spot_etf", "underlying_asset": "Multi-asset", "issuer": "Bitwise Crypto Industry Innovators ETF"},
    "DAPP": {"product_type": "spot_etf", "underlying_asset": "Multi-asset", "issuer": "VanEck Digital Transformation ETF"},
    "CRPT": {"product_type": "spot_etf", "underlying_asset": "Multi-asset", "issuer": "First Trust SkyBridge Crypto ETF"},
    "WGMI": {"product_type": "spot_etf", "underlying_asset": "Multi-asset", "issuer": "Valkyrie Bitcoin Miners ETF"},
    "DAM":  {"product_type": "spot_etf", "underlying_asset": "Multi-asset", "issuer": "VanEck Digital Assets Mining ETF"},
    "SATO": {"product_type": "spot_etf", "underlying_asset": "Multi-asset", "issuer": "Invesco Alerian Galaxy Crypto Economy ETF"},
    "GFOF": {"product_type": "spot_etf", "underlying_asset": "Multi-asset", "issuer": "Grayscale Future of Finance ETF"},
}
