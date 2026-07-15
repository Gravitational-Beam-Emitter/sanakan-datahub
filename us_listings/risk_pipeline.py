"""
Risk pipeline — Short Interest + Fails-to-Deliver data.

Short Interest: from yfinance (sharesShort, shortPercentOfFloat, shortRatio)
Fails-to-Deliver: from SEC Reg SHO daily data (when available)

Usage:
    python -m us_listings.risk_pipeline --init
    python -m us_listings.risk_pipeline --si-only    # short interest only
    python -m us_listings.risk_pipeline --ftd-only   # FTD only
"""

from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, timedelta
from io import BytesIO, StringIO
from typing import Any, Dict, List, Optional, Set
from zipfile import ZipFile

import requests

from us_listings.config import SEC_HEADERS, YFINANCE_RATE_LIMIT
from us_listings.storage import (
    init_db, upsert_short_interest, upsert_ftd,
    log_fetch_start, log_fetch_end, get_all_crypto_products,
)

logger = logging.getLogger("us_listings.risk_pipeline")

# Top US stocks for short interest tracking (high short interest candidates)
TOP_SHORT_TICKERS = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "BRK.B",
    "JPM", "V", "JNJ", "WMT", "PG", "MA", "UNH", "HD", "DIS", "BAC",
    "NFLX", "ADBE", "CRM", "AMD", "INTC", "QCOM", "TXN", "PYPL",
    "GME", "AMC", "CVNA", "AI", "PLTR", "SOFI", "RIVN", "LCID",
    "COIN", "MSTR", "MARA", "RIOT",  # crypto stocks
]

# SEC FTD URL patterns to try
FTD_URL_PATTERNS = [
    "https://www.sec.gov/files/regsho/data/fails-to-deliver/cnsfails{date_compact}.zip",
    "https://www.sec.gov/files/data/fails-to-deliver/cnsfails{date_compact}.zip",
]


def fetch_short_interest_yfinance(tickers: List[str]) -> List[Dict[str, Any]]:
    """Fetch short interest data from yfinance for a list of tickers."""
    records = []
    today = datetime.now().strftime("%Y-%m-%d")

    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance not installed")
        return records

    for ticker in tickers:
        try:
            stock = yf.Ticker(ticker)
            info = stock.info or {}

            short_interest = info.get("sharesShort")
            short_pct = info.get("shortPercentOfFloat")
            short_ratio = info.get("shortRatio")
            avg_vol = info.get("averageVolume")

            if short_interest or short_pct:
                records.append({
                    "ticker": ticker,
                    "settlement_date": today,
                    "short_interest": int(short_interest) if short_interest else None,
                    "avg_daily_volume": int(avg_vol) if avg_vol else None,
                    "days_to_cover": float(short_ratio) if short_ratio else None,
                    "short_pct_float": float(short_pct * 100) if short_pct else None,
                    "source": "yfinance",
                })
        except Exception as e:
            logger.debug(f"Short interest fetch failed for {ticker}: {e}")

        time.sleep(YFINANCE_RATE_LIMIT)

    logger.info(f"Fetched short interest for {len(records)} tickers")
    return records


def fetch_ftd_sec(date_str: str) -> List[Dict[str, Any]]:
    """Try to fetch SEC Fails-to-Deliver data for a given date."""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    date_compact = dt.strftime("%Y%m%d")
    records = []

    for pattern in FTD_URL_PATTERNS:
        url = pattern.format(date_compact=date_compact)
        try:
            resp = requests.get(url, headers=SEC_HEADERS, timeout=15)
            if resp.status_code == 200:
                logger.info(f"Found FTD data at: {url}")
                with ZipFile(BytesIO(resp.content)) as zf:
                    for name in zf.namelist():
                        with zf.open(name) as f:
                            content = f.read().decode("utf-8", errors="ignore")
                            records = _parse_ftd_csv(content, date_str)
                if records:
                    break
            else:
                logger.debug(f"FTD URL returned {resp.status_code}: {url}")
        except Exception as e:
            logger.debug(f"FTD fetch failed for {url}: {e}")

    return records


def _parse_ftd_csv(content: str, date_str: str) -> List[Dict[str, Any]]:
    """Parse SEC FTD CSV file format.

    Format: SETTLEMENT DATE|CUSIP|SYMBOL|QUANTITY (FAILS)|DESCRIPTION|PRICE
    """
    records = []
    lines = content.strip().split("\n")
    for line in lines[1:]:  # Skip header
        if not line.strip():
            continue
        parts = line.split("|")
        if len(parts) < 4:
            continue
        try:
            symbol = parts[2].strip().upper()
            if not symbol:
                continue
            quantity = int(parts[3].strip().replace(",", ""))
            price = float(parts[5].strip()) if len(parts) > 5 and parts[5].strip() else None

            records.append({
                "ticker": symbol,
                "date": date_str,
                "quantity": quantity,
                "price": price,
                "source": "sec",
            })
        except (ValueError, IndexError):
            continue

    return records


def fetch_risk_daily(date_str: Optional[str] = None) -> Dict[str, Any]:
    """Fetch both short interest and FTD data."""
    conn = init_db()
    summary = {"si_stored": 0, "ftd_stored": 0, "errors": []}

    if not date_str:
        date_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    log_id = log_fetch_start(conn, date_str, source="risk_data")

    try:
        # 1. Short Interest — from yfinance
        crypto_df = get_all_crypto_products(conn)
        crypto_tickers = crypto_df["ticker"].tolist() if not crypto_df.empty else []
        all_tickers = list(dict.fromkeys(TOP_SHORT_TICKERS + crypto_tickers))  # deduped, order preserved

        si_records = fetch_short_interest_yfinance(all_tickers)
        if si_records:
            summary["si_stored"] = upsert_short_interest(conn, si_records)
            logger.info(f"Stored {summary['si_stored']} short interest records")

        # 2. Fails-to-Deliver — from SEC (if available)
        ftd_records = fetch_ftd_sec(date_str)
        if ftd_records:
            summary["ftd_stored"] = upsert_ftd(conn, ftd_records)
            logger.info(f"Stored {summary['ftd_stored']} FTD records for {date_str}")
        else:
            logger.info(f"No FTD data available for {date_str} (SEC source not yet published or URL changed)")

        log_fetch_end(conn, log_id, items_checked=len(all_tickers),
                      new_items=summary["si_stored"] + summary["ftd_stored"])

    except Exception as e:
        summary["errors"].append(str(e))
        logger.error(f"Risk fetch failed: {e}")
        log_fetch_end(conn, log_id, status="error", error=str(e))
    finally:
        conn.close()

    return summary


def init(db_path=None):
    return fetch_risk_daily()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    if "--si-only" in sys.argv:
        conn = init_db(); records = fetch_short_interest_yfinance(TOP_SHORT_TICKERS)
        print(f"Short interest records: {len(records)}")
        if records: print(f"Stored: {upsert_short_interest(conn, records)}")
        conn.close()
    elif "--ftd-only" in sys.argv:
        date = sys.argv[sys.argv.index("--ftd-only") + 1] if len(sys.argv) > sys.argv.index("--ftd-only") + 1 else None
        print(fetch_ftd_sec(date or (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"))[:5])
    else:
        print(fetch_risk_daily())
