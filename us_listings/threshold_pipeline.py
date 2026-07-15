"""
Threshold Securities pipeline — NASDAQ Reg SHO Threshold List.

Threshold securities are equities with persistent fails-to-deliver
for 5 consecutive settlement days at a registered clearing agency.

Source: NASDAQ FTP (ftp://ftp.nasdaqtrader.com/SymbolDirectory/regsho/)
Format: Symbol|Security Name|Market Category|Reg SHO Threshold Flag|Rule 3210|Filler

Usage:
    python -m us_listings.threshold_pipeline --init
    python -m us_listings.threshold_pipeline --date 20260618
"""

from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from io import StringIO

import pandas as pd
import requests

from us_listings.storage import init_db, upsert_threshold_securities, log_fetch_start, log_fetch_end

logger = logging.getLogger("us_listings.threshold_pipeline")

# NASDAQ publishes threshold list daily via FTP
# HTTP mirror also works (though some dates return 301 to FTP)
THRESHOLD_URL_PATTERNS = [
    "ftp://ftp.nasdaqtrader.com/SymbolDirectory/regsho/nasdaqth{date_compact}.txt",
]

# Alternative: NYSE also publishes threshold lists (though NYSE page was 404)
# The NASDAQ list covers NASDAQ-listed securities only
# For NYSE, we'd need a different source


def fetch_threshold_securities(date_str: str) -> List[Dict[str, Any]]:
    """Download and parse NASDAQ threshold securities list for a given date.

    Returns list of dicts with: ticker, security_name, market_category,
    threshold_flag, date
    """
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    date_compact = dt.strftime("%Y%m%d")
    records = []

    for pattern in THRESHOLD_URL_PATTERNS:
        url = pattern.format(date_compact=date_compact)
        try:
            resp = requests.get(url, timeout=30)
            if resp.status_code == 200 and len(resp.text) > 100:
                logger.info(f"Found threshold data at: {url} ({len(resp.text)} bytes)")
                df = pd.read_csv(StringIO(resp.text), sep="|")
                for _, row in df.iterrows():
                    symbol = str(row.iloc[0]).strip().upper() if pd.notna(row.iloc[0]) else ""
                    sec_name = str(row.iloc[1]).strip() if len(row) > 1 and pd.notna(row.iloc[1]) else ""
                    market_cat = str(row.iloc[2]).strip() if len(row) > 2 and pd.notna(row.iloc[2]) else ""
                    flag = str(row.iloc[3]).strip() if len(row) > 3 and pd.notna(row.iloc[3]) else ""

                    if symbol and flag.upper() == "Y":
                        records.append({
                            "ticker": symbol,
                            "security_name": sec_name,
                            "market_category": market_cat,
                            "is_threshold": True,
                            "date": date_str,
                            "source": "nasdaq_regsho",
                        })
                break
            else:
                logger.debug(f"Threshold URL returned {resp.status_code}: {url}")
        except Exception as e:
            logger.debug(f"Threshold fetch failed for {url}: {e}")

    return records


def fetch_threshold_daily(date_str: Optional[str] = None) -> Dict[str, Any]:
    """Fetch and store threshold securities for a date."""
    conn = init_db()
    summary = {"date": date_str or "latest", "found": 0, "stored": 0, "errors": []}

    if not date_str:
        date_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    log_id = log_fetch_start(conn, date_str, source="nasdaq_threshold")

    try:
        records = fetch_threshold_securities(date_str)
        summary["found"] = len(records)

        if records:
            count = upsert_threshold_securities(conn, records)
            summary["stored"] = count
            logger.info(f"Stored {count} threshold securities for {date_str}")

        log_fetch_end(conn, log_id, items_checked=1, new_items=summary["stored"])

    except Exception as e:
        summary["errors"].append(str(e))
        logger.error(f"Threshold fetch failed: {e}")
        log_fetch_end(conn, log_id, status="error", error=str(e))
    finally:
        conn.close()

    return summary


def init(db_path=None):
    """Backfill recent threshold securities data."""
    conn = init_db(db_path)
    summary = {"stored": 0}
    try:
        today = datetime.now()
        for i in range(30):
            d = (today - timedelta(days=i))
            if d.weekday() >= 5:
                continue
            r = fetch_threshold_daily(d.strftime("%Y-%m-%d"))
            summary["stored"] += r["stored"]
            time.sleep(0.5)
    finally:
        conn.close()
    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    if "--init" in sys.argv:
        print(init())
    elif "--date" in sys.argv:
        idx = sys.argv.index("--date")
        print(fetch_threshold_daily(sys.argv[idx + 1]))
    else:
        print(fetch_threshold_daily())
