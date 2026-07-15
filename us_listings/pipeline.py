"""
Data pipeline — fetch US stock new listings from NASDAQ IPO calendar and SEC EDGAR.

Usage:
    python -m us_listings.pipeline --init          # first run: backfill
    python -m us_listings.pipeline                  # fetch latest month
    python -m us_listings.pipeline --date 20260619   # fetch specific date
"""

from __future__ import annotations

import logging
import re
import sys
import time
from datetime import datetime, timedelta, date
from typing import Any, Dict, List, Optional

import pandas as pd
import requests

from us_listings.config import (
    NASDAQ_IPO_URL,
    NASDAQ_HEADERS,
    SEC_HEADERS,
    SEC_COMPANY_TICKERS_URL,
    SEC_RATE_LIMIT,
    BACKFILL_START,
)
from us_listings.storage import (
    init_db,
    upsert_listings,
    log_fetch_start,
    log_fetch_end,
    cleanup_old_records,
    mark_listings_as_crypto,
    get_crypto_tickers_set,
)

logger = logging.getLogger("us_listings.pipeline")

# ── NASDAQ IPO Calendar ──


def _parse_nasdaq_share_count(raw: str) -> Optional[int]:
    """Parse NASDAQ share count string like '2,500,000' to int."""
    if not raw:
        return None
    try:
        return int(raw.replace(",", "").replace("$", ""))
    except (ValueError, TypeError):
        return None


def _parse_nasdaq_price(raw: str) -> Optional[float]:
    """Parse NASDAQ price string like '16.00' or '$40,000,000' to float."""
    if not raw:
        return None
    try:
        return float(raw.replace(",", "").replace("$", ""))
    except (ValueError, TypeError):
        return None


def _classify_listing_type(company_name: str, deal_status: str) -> str:
    """Classify listing type from company name and deal status."""
    if deal_status == "Upcoming":
        return "Upcoming"
    if deal_status == "Filed":
        return "Upcoming"

    name_upper = company_name.upper()
    spac_keywords = ["ACQUISITION CORP", "ACQUISITION CO", "SPAC",
                     "ACQUISITION I", "ACQUISITION II", "ACQUISITION III",
                     "VENTURES ACQUISITION", "CAPITAL CORP"]
    for kw in spac_keywords:
        if kw in name_upper:
            return "SPAC"

    return "IPO"


def fetch_nasdaq_ipo_calendar(month: Optional[str] = None) -> List[Dict[str, Any]]:
    """Fetch IPO calendar from NASDAQ API for a given month (YYYY-MM).

    Returns list of listing dicts with ticker, company_name, listing_date, etc.
    """
    if month is None:
        month = datetime.now().strftime("%Y-%m")

    url = f"{NASDAQ_IPO_URL}?date={month}"
    logger.info(f"Fetching NASDAQ IPO calendar: {url}")

    try:
        resp = requests.get(url, headers=NASDAQ_HEADERS, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.error(f"Failed to fetch NASDAQ IPO calendar: {e}")
        return []

    listings = []
    data_section = data.get("data", {})

    # Process all deal categories: priced, upcoming, filed
    for category in ["priced", "upcoming", "filed"]:
        section = data_section.get(category, {})
        rows = section.get("rows", [])
        for row in rows:
            ticker = (row.get("proposedTickerSymbol") or "").strip().upper()
            if not ticker:
                continue

            company_name = (row.get("companyName") or "").strip()
            deal_status = row.get("dealStatus", category.title())

            # Parse listing date
            listing_date = None
            date_fields = ["pricedDate", "expectedPricingDate", "expectedDate", "filedDate"]
            for df in date_fields:
                raw_date = row.get(df, "")
                if raw_date:
                    try:
                        listing_date = datetime.strptime(raw_date, "%m/%d/%Y").strftime("%Y-%m-%d")
                        break
                    except ValueError:
                        continue

            if not listing_date:
                continue

            # Parse exchange
            exchange_raw = (row.get("proposedExchange") or "").strip()
            exchange = "NASDAQ" if "nasdaq" in exchange_raw.lower() else (
                "NYSE" if "nyse" in exchange_raw.lower() else exchange_raw
            )

            listings.append({
                "ticker": ticker,
                "company_name": company_name,
                "listing_date": listing_date,
                "listing_type": _classify_listing_type(company_name, deal_status),
                "exchange": exchange,
                "offer_price": _parse_nasdaq_price(row.get("proposedSharePrice")),
                "shares_offered": _parse_nasdaq_share_count(row.get("sharesOffered")),
                "description": "",
                "source": "nasdaq",
                "source_url": f"https://www.nasdaq.com/market-activity/ipos/{ticker.lower()}",
                "is_crypto": False,
                "crypto_product_id": None,
            })

    logger.info(f"NASDAQ IPO calendar: {len(listings)} listings found for {month}")
    return listings


# ── SEC New Registrations (S-1 / F-1 filings) ──


def fetch_sec_new_registrations(date_str: Optional[str] = None) -> List[Dict[str, Any]]:
    """Fetch S-1 and F-1 registration statements from SEC daily index.

    S-1: US company IPO registration
    F-1: Foreign company US listing registration

    These are EARLY signals - companies file S-1 weeks/months before the actual IPO.
    """
    if date_str is None:
        date_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    dt = datetime.strptime(date_str, "%Y-%m-%d")
    year = dt.year
    quarter = f"QTR{(dt.month - 1) // 3 + 1}"
    date_compact = dt.strftime("%Y%m%d")

    url = (
        f"https://www.sec.gov/Archives/edgar/daily-index/"
        f"{year}/{quarter}/form.{date_compact}.idx"
    )

    logger.info(f"Fetching SEC daily index for S-1/F-1: {url}")
    listings = []

    try:
        resp = requests.get(url, headers=SEC_HEADERS, timeout=30)
        if resp.status_code != 200:
            logger.debug(f"SEC daily index not found for {date_str}")
            return listings
    except Exception as e:
        logger.error(f"Failed to fetch SEC daily index: {e}")
        return listings

    # Parse fixed-width index
    lines = resp.text.strip().split("\n")
    for line in lines:
        if len(line) < 90:
            continue

        form_type = line[0:12].strip()
        if form_type not in ("S-1", "F-1", "S-1/A", "F-1/A"):
            continue

        company_name = line[12:74].strip()
        file_path = line[94:].strip() if len(line) > 94 else ""

        # Extract CIK from file_path
        cik = ""
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

        accession_number = ""
        if file_path:
            parts = file_path.split("/")
            if len(parts) >= 4:
                accession_number = parts[-1].replace(".txt", "")

        # S-1 filings are early signals, not actual IPOs yet
        # Mark as Upcoming with the filing date
        listings.append({
            "ticker": "",  # No ticker yet for S-1
            "company_name": company_name,
            "listing_date": "",
            "listing_type": "Upcoming",
            "exchange": "",
            "offer_price": None,
            "shares_offered": None,
            "description": f"SEC {form_type} registration filed",
            "source": "sec_edgar",
            "source_url": (
                f"https://www.sec.gov/Archives/edgar/data/"
                f"{cik}/{accession_number.replace('-', '')}/{accession_number}-index.htm"
            ) if accession_number else "",
            "is_crypto": False,
            "crypto_product_id": None,
        })

    logger.info(f"SEC index for {date_str}: {len(listings)} S-1/F-1 registrations")
    return listings


# ── SEC company_tickers.json diff ──


def _download_company_tickers() -> Dict[str, str]:
    """Download current SEC company_tickers.json. Returns {ticker: company_name}."""
    try:
        resp = requests.get(SEC_COMPANY_TICKERS_URL, headers=SEC_HEADERS, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        result = {}
        for entry in data.values():
            ticker = str(entry.get("ticker", "")).upper()
            name = str(entry.get("title", ""))
            if ticker:
                result[ticker] = name
        return result
    except Exception as e:
        logger.error(f"Failed to download SEC company tickers: {e}")
        return {}


# ── Main Pipeline ──


def _auto_tag_crypto(conn, listings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Tag listings as crypto based on crypto_products table."""
    try:
        crypto_tickers = get_crypto_tickers_set(conn)
    except Exception:
        return listings

    for item in listings:
        if item["ticker"] and item["ticker"].upper() in crypto_tickers:
            item["is_crypto"] = True

    return listings


def fetch_listings_for_month(month: Optional[str] = None) -> Dict[str, Any]:
    """Fetch and store new listings for a given month.

    Args:
        month: YYYY-MM format. Defaults to current month.

    Returns summary dict.
    """
    conn = init_db()
    summary = {
        "month": month or datetime.now().strftime("%Y-%m"),
        "listings_found": 0,
        "listings_stored": 0,
        "errors": [],
    }

    log_id = -1
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        log_id = log_fetch_start(conn, today, source="nasdaq_ipo")

        # 1. Fetch NASDAQ IPO calendar (primary source)
        nasdaq_listings = fetch_nasdaq_ipo_calendar(month)
        summary["listings_found"] = len(nasdaq_listings)

        if nasdaq_listings:
            # 2. Auto-tag crypto from existing crypto_products table
            nasdaq_listings = _auto_tag_crypto(conn, nasdaq_listings)

            # 3. Store
            count = upsert_listings(conn, nasdaq_listings)
            summary["listings_stored"] = count
            logger.info(f"Stored {count} new listings for {month}")

            # 4. Cross-reference: mark newly added listings as crypto
            mark_listings_as_crypto(conn)

        # 5. Cleanup old records
        cleaned = cleanup_old_records(conn)
        if cleaned:
            logger.info(f"Cleaned up {cleaned} expired records")

        log_fetch_end(conn, log_id, items_checked=len(nasdaq_listings),
                      new_items=summary["listings_stored"])

    except Exception as e:
        err = f"Fetch failed: {e}"
        summary["errors"].append(err)
        logger.error(err)
        if log_id >= 0:
            log_fetch_end(conn, log_id, status="error", error=str(e))
    finally:
        conn.close()

    return summary


def fetch_past_months(start_month: str, months: int = 6) -> List[Dict[str, Any]]:
    """Backfill listings for the past N months."""
    results = []
    dt = datetime.strptime(start_month + "-01", "%Y-%m-%d")
    for i in range(months):
        m = (dt - timedelta(days=30 * i)).strftime("%Y-%m")
        result = fetch_listings_for_month(m)
        results.append(result)
        time.sleep(1)
    return results


def init(db_path: Optional[str] = None) -> Dict[str, Any]:
    """Initialize pipeline: backfill listings from BACKFILL_START to now."""
    conn = init_db(db_path)
    summary = {"listings_stored": 0, "errors": []}

    try:
        start = datetime.strptime(BACKFILL_START, "%Y-%m-%d")
        now = datetime.now()

        # Generate all months from BACKFILL_START to now
        months = set()
        d = start
        while d <= now:
            months.add(d.strftime("%Y-%m"))
            # Next month
            if d.month == 12:
                d = d.replace(year=d.year + 1, month=1)
            else:
                d = d.replace(month=d.month + 1)

        logger.info(f"Backfilling {len(months)} months from {BACKFILL_START}...")
        total = 0

        for m in sorted(months):
            logger.info(f"  Fetching {m}...")
            result = fetch_listings_for_month(m)
            total += result["listings_stored"]
            time.sleep(1)

        summary["listings_stored"] = total
        logger.info(f"Backfill complete: {total} listings stored")

    except Exception as e:
        summary["errors"].append(str(e))
        logger.error(f"Init failed: {e}")
    finally:
        conn.close()

    return summary


# ── Crypto detection via keyword scan of company names ──


def scan_for_crypto_keywords(listings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Scan un-tagged listings for crypto keywords in company names.

    This is a supplementary method - the primary crypto classification
    is done via the crypto_products table.
    """
    from us_listings.config import CRYPTO_KEYWORDS

    for item in listings:
        if item.get("is_crypto"):
            continue
        name = (item.get("company_name") or "").lower()
        desc = (item.get("description") or "").lower()
        combined = name + " " + desc
        for kw in CRYPTO_KEYWORDS:
            if kw in combined:
                item["is_crypto"] = True
                item["_crypto_keyword"] = kw
                break

    return listings


# ── CLI ──

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    if "--init" in sys.argv:
        result = init()
        print(f"\nInit result: {result}")

    elif "--date" in sys.argv:
        idx = sys.argv.index("--date")
        month = sys.argv[idx + 1][:7]  # Extract YYYY-MM from date
        result = fetch_listings_for_month(month)
        print(f"\nResult: {result}")

    elif "--backfill" in sys.argv:
        idx = sys.argv.index("--backfill")
        months = int(sys.argv[idx + 1]) if len(sys.argv) > idx + 1 else 6
        results = fetch_past_months(datetime.now().strftime("%Y-%m"), months)
        for r in results:
            print(f"  {r}")

    else:
        # Default: fetch current month
        result = fetch_listings_for_month()
        print(f"\nResult: {result}")
