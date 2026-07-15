"""
Earnings Calendar pipeline — SEC 10-K and 10-Q filings.

Usage:
    python -m us_listings.earnings_pipeline --init
    python -m us_listings.earnings_pipeline --date 20260618
"""

from __future__ import annotations

import logging
import re
import sys
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import requests
from bs4 import BeautifulSoup

from us_listings.config import SEC_HEADERS, SEC_RATE_LIMIT
from us_listings.storage import init_db, upsert_earnings, log_fetch_start, log_fetch_end

logger = logging.getLogger("us_listings.earnings_pipeline")


def _guess_exchange(ticker: str) -> str:
    letters = re.sub(r"[^A-Z]", "", ticker)
    return "NYSE" if len(letters) <= 3 else "NASDAQ"


def fetch_earnings_by_date(date_str: str) -> List[Dict[str, Any]]:
    """Fetch 10-K and 10-Q filings from SEC daily index for a given date."""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    year = dt.year
    quarter = f"QTR{(dt.month - 1) // 3 + 1}"
    date_compact = dt.strftime("%Y%m%d")

    url = (
        f"https://www.sec.gov/Archives/edgar/daily-index/"
        f"{year}/{quarter}/form.{date_compact}.idx"
    )

    logger.info(f"Fetching SEC daily index for earnings: {url}")
    results = []

    try:
        resp = requests.get(url, headers=SEC_HEADERS, timeout=30)
        if resp.status_code != 200:
            logger.debug(f"No daily index for {date_str}")
            return results
    except Exception as e:
        logger.error(f"Failed: {e}")
        return results

    lines = resp.text.strip().split("\n")
    for line in lines:
        if len(line) < 90:
            continue
        form_type = line[0:12].strip()
        if form_type not in ("10-K", "10-Q", "10-K/A", "10-Q/A"):
            continue

        company_name = line[12:74].strip()
        cik = ""
        file_path = line[94:].strip() if len(line) > 94 else ""
        if file_path:
            parts = file_path.split("/")
            if len(parts) >= 3:
                try:
                    cik = str(int(parts[2]))
                except ValueError:
                    pass
        if not cik:
            try:
                cik = str(int(line[74:84].strip()))
            except ValueError:
                continue

        # Build source URL
        accession = ""
        if file_path:
            parts = file_path.split("/")
            if len(parts) >= 4:
                accession = parts[-1].replace(".txt", "")

        source_url = ""
        if cik and accession:
            acc_no = accession.replace("-", "")
            source_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_no}/{accession}-index.htm"

        results.append({
            "ticker": "",
            "company_name": company_name,
            "report_type": form_type.replace("/A", ""),
            "fiscal_period_end": None,
            "filing_date": date_str,
            "source_url": source_url,
        })

    logger.info(f"Earnings for {date_str}: {len(results)} filings")
    return results


def resolve_tickers_and_period(conn, earnings: List[Dict], session: requests.Session) -> List[Dict]:
    """Try to resolve ticker for earnings entries from SEC submissions API."""
    # Load existing ticker map from DB
    try:
        rows = conn.execute("SELECT cik, ticker FROM crypto_products WHERE is_active = true").fetchall()
        ticker_map = {}
    except Exception:
        rows = []
        ticker_map = {}

    for e in earnings:
        company_name = e["company_name"]
        cik = ""
        # Look up from SEC submissions
        try:
            # Use company name in filings
            name_upper = company_name.upper()
            for kw, ticker in ticker_map.items():
                if kw in name_upper:
                    e["ticker"] = ticker
                    break
        except Exception:
            pass

    return earnings


def fetch_earnings_daily(date_str: Optional[str] = None) -> Dict[str, Any]:
    """Fetch and store earnings calendar for a date."""
    conn = init_db()
    summary = {"date": date_str or "latest", "found": 0, "stored": 0, "errors": []}

    if not date_str:
        date_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    log_id = log_fetch_start(conn, date_str, source="sec_earnings")
    session = requests.Session()
    session.headers.update(SEC_HEADERS)

    try:
        earnings = fetch_earnings_by_date(date_str)
        summary["found"] = len(earnings)

        if earnings:
            # Resolve tickers
            earnings = resolve_tickers_and_period(conn, earnings, session)
            count = upsert_earnings(conn, earnings)
            summary["stored"] = count
            logger.info(f"Stored {count} earnings for {date_str}")

        log_fetch_end(conn, log_id, items_checked=len(earnings), new_items=summary["stored"])

    except Exception as e:
        err = f"Earnings fetch failed: {e}"
        summary["errors"].append(err)
        logger.error(err)
        log_fetch_end(conn, log_id, status="error", error=str(e))
    finally:
        session.close()
        conn.close()

    return summary


def init(db_path=None):
    """Backfill recent earnings data."""
    conn = init_db(db_path)
    summary = {"stored": 0}
    try:
        today = datetime.now()
        for i in range(30):
            d = (today - timedelta(days=i))
            if d.weekday() >= 5:
                continue
            r = fetch_earnings_daily(d.strftime("%Y-%m-%d"))
            summary["stored"] += r["stored"]
            time.sleep(SEC_RATE_LIMIT * 2)
    finally:
        conn.close()
    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    if "--init" in sys.argv:
        print(init())
    elif "--date" in sys.argv:
        idx = sys.argv.index("--date")
        print(fetch_earnings_daily(sys.argv[idx + 1]))
    else:
        print(fetch_earnings_daily())
