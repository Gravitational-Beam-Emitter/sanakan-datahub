"""
Insider Trading pipeline — SEC Form 4 filings (insider transactions).

SEC Form 4: filed within 2 business days of insider transactions.
Contains: insider name, title, transaction type (P/S), shares, price.

Usage:
    python -m us_listings.insider_pipeline --init
    python -m us_listings.insider_pipeline --date 20260618
"""

from __future__ import annotations

import logging
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pandas as pd
import requests

from us_listings.config import SEC_HEADERS, SEC_RATE_LIMIT
from us_listings.storage import init_db, upsert_insider_trades, log_fetch_start, log_fetch_end

logger = logging.getLogger("us_listings.insider_pipeline")

SEC_FORM4_RSS = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4&count=100&output=atom"


def _lookup_ticker_from_sec(cik: str, session: requests.Session) -> str:
    try:
        padded = str(int(cik)).zfill(10)
        resp = session.get(
            f"https://data.sec.gov/submissions/CIK{padded}.json",
            headers=SEC_HEADERS, timeout=5,
        )
        if resp.status_code == 200:
            data = resp.json()
            tickers = data.get("tickers", [])
            if tickers:
                return tickers[0].upper().strip()
    except Exception:
        pass
    return ""


def fetch_form4_filings(date_str: Optional[str] = None) -> List[Dict[str, Any]]:
    """Fetch Form 4 filings from SEC Atom feed. Returns list of filing dicts."""
    filings = []
    target_date = date_str or datetime.now().strftime("%Y-%m-%d")

    logger.info(f"Fetching Form 4 filings (target: {target_date})...")
    try:
        resp = requests.get(SEC_FORM4_RSS, headers=SEC_HEADERS, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"Failed to fetch Form 4 feed: {e}")
        return filings

    NS = {"atom": "http://www.w3.org/2005/Atom"}
    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError:
        logger.error("Failed to parse Form 4 Atom feed XML")
        return filings

    for entry in root.findall("atom:entry", NS):
        try:
            title_el = entry.find("atom:title", NS)
            title = title_el.text.strip() if title_el is not None and title_el.text else ""

            cik_match = re.search(r"\((\d{7,10})\)", title)
            if not cik_match:
                continue
            cik = str(int(cik_match.group(1)))

            company_name = re.match(r"4\s*[-–]\s*(.+?)\s*\(\d+\)", title)
            company_name = company_name.group(1).strip() if company_name else ""

            updated_el = entry.find("atom:updated", NS)
            updated = updated_el.text.strip() if updated_el is not None and updated_el.text else ""
            filing_date = updated[:10] if updated else target_date

            if date_str and filing_date != date_str:
                continue

            id_el = entry.find("atom:id", NS)
            entry_id = id_el.text.strip() if id_el is not None and id_el.text else ""
            acc_match = re.search(r"accession-number=([\d-]+)", entry_id)
            accession_number = acc_match.group(1) if acc_match else ""

            link = ""
            for link_el in entry.findall("atom:link", NS):
                if link_el.get("rel") == "alternate":
                    link = link_el.get("href", "")
                    break

            filings.append({
                "cik": cik, "company_name": company_name,
                "filing_date": filing_date, "accession_number": accession_number,
                "link": link,
            })
        except Exception:
            continue

    logger.info(f"Form 4 feed: {len(filings)} filings")
    return filings


def parse_form4_xml(cik: str, accession_number: str, session: requests.Session) -> List[Dict[str, Any]]:
    """Parse a Form 4 XML filing to extract transaction details.

    Form 4 primary document is typically: {accession}.xml or form4.xml
    Contains <nonDerivativeTransaction> and <derivativeTransaction> elements.
    """
    trades = []
    if not accession_number:
        return trades

    acc_no = accession_number.replace("-", "")
    cik_dir = str(int(cik))
    base_url = f"https://www.sec.gov/Archives/edgar/data/{cik_dir}/{acc_no}"

    # Try XML document first (most Form 4s have structured XML)
    xml_url = f"{base_url}/{accession_number}.xml"
    try:
        resp = session.get(xml_url, headers=SEC_HEADERS, timeout=10)
        if resp.status_code != 200:
            # Try alternate URL pattern
            resp = session.get(f"{base_url}/form4.xml", headers=SEC_HEADERS, timeout=10)
        if resp.status_code != 200:
            return trades

        root = ET.fromstring(resp.text)
        ns = {
            "ns": "http://www.sec.gov/edgar/document/thirteenf/informationtable",
            "own": "http://www.sec.gov/edgar/common",
        }

        # Also try without namespace
        ns_fallback = {}

        # Extract issuer (ticker/company)
        issuer_elem = root.find(".//issuerTradingSymbol")
        ticker = issuer_elem.text.strip().upper() if issuer_elem is not None and issuer_elem.text else ""
        issuer_elem2 = root.find(".//{http://www.sec.gov/edgar/common}issuerTradingSymbol")
        if not ticker and issuer_elem2 is not None and issuer_elem2.text:
            ticker = issuer_elem2.text.strip().upper()

        # Extract reporting person
        rpt_owner = root.find(".//rptOwnerName")
        owner_name = rpt_owner.text.strip() if rpt_owner is not None and rpt_owner.text else ""
        if not owner_name:
            rpt_owner2 = root.find(".//{http://www.sec.gov/edgar/common}rptOwnerName")
            if rpt_owner2 is not None and rpt_owner2.text:
                owner_name = rpt_owner2.text.strip()

        # Check for 10b5-1 plan indicator
        is_10b5_1 = "10b5-1" in resp.text.lower() or "10b5" in resp.text.lower()

        # Filing date from document period
        period_elem = root.find(".//periodOfReport")
        filing_date = period_elem.text.strip()[:10] if period_elem is not None and period_elem.text else ""

        # Parse non-derivative transactions
        for txn in root.iter("nonDerivativeTransaction"):
            trades.append(_parse_transaction(txn, ticker, owner_name, filing_date, is_10b5_1))

        # Parse derivative transactions
        for txn in root.iter("derivativeTransaction"):
            trades.append(_parse_transaction(txn, ticker, owner_name, filing_date, is_10b5_1))

    except ET.ParseError:
        logger.debug(f"XML parse error for {cik}/{accession_number}")
    except Exception as e:
        logger.debug(f"Error parsing Form 4 for {cik}: {e}")

    return [t for t in trades if t.get("ticker") and t.get("shares")]


def _parse_transaction(txn_elem, ticker: str, owner_name: str,
                       filing_date: str, is_10b5_1: bool) -> Dict[str, Any]:
    """Extract fields from a single transaction element."""
    def _text(tag):
        e = txn_elem.find(".//" + tag)
        if e is not None:
            val = e.find("value")
            if val is not None and val.text:
                return val.text.strip()
            if e.text:
                return e.text.strip()
        return ""

    shares_str = _text("transactionShares")
    price_str = _text("transactionPricePerShare")
    code = _text("transactionAcquiredDisposedCode")  # A = Acquired (buy), D = Disposed (sell)
    date_str = _text("transactionDate")
    shares_after_str = _text("sharesOwnedFollowingTransaction")
    title_str = _text("securityTitle")

    try:
        shares = float(shares_str) if shares_str else 0.0
        price = float(price_str) if price_str else 0.0
        shares_after = float(shares_after_str) if shares_after_str else None
    except ValueError:
        return {}

    return {
        "ticker": ticker,
        "company_name": "",
        "insider_name": owner_name,
        "insider_title": _text("officerTitle") or "",
        "transaction_type": "P-Purchase" if code == "A" else "S-Sale" if code == "D" else code,
        "shares": shares,
        "price_per_share": price,
        "total_value": shares * price,
        "shares_owned_after": shares_after,
        "filing_date": filing_date,
        "transaction_date": date_str or filing_date,
        "is_10b5_1": is_10b5_1,
        "source_url": "",
    }


def fetch_insider_daily(date_str: Optional[str] = None) -> Dict[str, Any]:
    """Fetch and store Form 4 filings for a given date."""
    conn = init_db()
    summary = {"date": date_str or "latest", "filings_found": 0, "trades_stored": 0, "errors": []}

    if not date_str:
        date_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    log_id = log_fetch_start(conn, date_str, source="sec_form4")
    session = requests.Session()
    session.headers.update(SEC_HEADERS)

    try:
        filings = fetch_form4_filings(date_str)
        summary["filings_found"] = len(filings)
        all_trades = []

        for i, f in enumerate(filings):
            time.sleep(SEC_RATE_LIMIT)
            trades = parse_form4_xml(f["cik"], f["accession_number"], session)

            # Resolve ticker if not in XML
            ticker_from_xml = trades[0]["ticker"] if trades else ""
            if not ticker_from_xml:
                ticker_from_sec = _lookup_ticker_from_sec(f["cik"], session)
                if ticker_from_sec:
                    for t in trades:
                        t["ticker"] = ticker_from_sec

            # Set source_url + company_name
            for t in trades:
                t["source_url"] = f["link"]
                t["company_name"] = f.get("company_name", "")

            all_trades.extend(trades)

            if (i + 1) % 20 == 0:
                logger.info(f"  Processed {i + 1}/{len(filings)} Form 4s, {len(all_trades)} trades so far")

        if all_trades:
            count = upsert_insider_trades(conn, all_trades)
            summary["trades_stored"] = count
            logger.info(f"Stored {count} insider trades for {date_str}")

        log_fetch_end(conn, log_id, items_checked=len(filings), new_items=summary["trades_stored"])

    except Exception as e:
        err = f"Insider fetch failed: {e}"
        summary["errors"].append(err)
        logger.error(err)
        log_fetch_end(conn, log_id, status="error", error=str(e))
    finally:
        session.close()
        conn.close()

    return summary


def init(db_path=None):
    """Backfill recent Form 4 filings."""
    conn = init_db(db_path)
    summary = {"trades_stored": 0}
    try:
        today = datetime.now()
        for i in range(5):
            d = (today - timedelta(days=i))
            if d.weekday() >= 5:
                continue
            result = fetch_insider_daily(d.strftime("%Y-%m-%d"))
            summary["trades_stored"] += result["trades_stored"]
            time.sleep(1)
    finally:
        conn.close()
    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    if "--init" in sys.argv:
        print(init())
    elif "--date" in sys.argv:
        idx = sys.argv.index("--date")
        print(fetch_insider_daily(sys.argv[idx + 1]))
    else:
        print(fetch_insider_daily())
