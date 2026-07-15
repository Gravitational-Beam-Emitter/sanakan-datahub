"""
Trading Suspensions pipeline — SEC Form 34 (trading halt/suspension notices).

Form 34: Filed when a company's securities are suspended from trading.
Also captures trading halts and revocations.

Usage:
    python -m us_listings.suspension_pipeline --init
    python -m us_listings.suspension_pipeline --date 20260619
"""

from __future__ import annotations

import logging
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import requests

from us_listings.config import SEC_HEADERS, SEC_RATE_LIMIT
from us_listings.storage import init_db, upsert_suspensions, log_fetch_start, log_fetch_end

logger = logging.getLogger("us_listings.suspension_pipeline")

SEC_FORM34_RSS = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=34&count=40&output=atom"


def _lookup_ticker_from_sec(cik: str, session: requests.Session) -> str:
    """Resolve CIK to ticker via SEC submissions API."""
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


def _extract_suspension_info(text: str) -> Dict[str, Any]:
    """Extract suspension reason and effective date from Form 34 text."""
    info = {"reason": "", "effective_date": "", "action_type": "Suspension"}

    # Common Form 34 patterns
    patterns = [
        (r"trading\s+suspension.*?(?:of|in)\s+(?:the\s+)?(?:securities\s+of\s+)?([A-Z]{1,5})", "ticker"),
        (r"(?:order|notice)\s+of\s+suspension\s+of\s+trading", "Suspension"),
        (r"effective\s+(?:on\s+)?(\w+\s+\d{1,2},\s*\d{4})", "effective_date"),
        (r"(\d{1,2}/\d{1,2}/\d{4})", "effective_date"),
        (r"(?:halt|suspended)\s+(?:in|on)\s+(\w+\s+\d{1,2},\s*\d{4})", "effective_date"),
    ]

    for pattern, key in patterns:
        if key == "ticker":
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                info["ticker"] = match.group(1).upper()
        elif key == "effective_date":
            match = re.search(pattern, text, re.IGNORECASE)
            if match and not info["effective_date"]:
                date_str = match.group(1)
                try:
                    dt = datetime.strptime(date_str, "%B %d, %Y")
                    info["effective_date"] = dt.strftime("%Y-%m-%d")
                except ValueError:
                    try:
                        dt = datetime.strptime(date_str, "%m/%d/%Y")
                        info["effective_date"] = dt.strftime("%Y-%m-%d")
                    except ValueError:
                        pass

    # Reason extraction: look for "because" or "concerns regarding"
    reason_match = re.search(
        r'(?:because|concerns?\s+(?:regarding|about|over)|due\s+to)\s+(.{20,200}?)(?:\.|$)',
        text, re.IGNORECASE
    )
    if reason_match:
        info["reason"] = reason_match.group(1).strip()

    return info


def fetch_suspension_filings(date_str: Optional[str] = None) -> List[Dict[str, Any]]:
    """Fetch Form 34 filings from SEC Atom feed."""
    filings = []
    target_date = date_str or datetime.now().strftime("%Y-%m-%d")

    logger.info(f"Fetching Form 34 filings (target: {target_date})...")
    try:
        resp = requests.get(SEC_FORM34_RSS, headers=SEC_HEADERS, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"Failed to fetch Form 34 feed: {e}")
        return filings

    NS = {"atom": "http://www.w3.org/2005/Atom"}
    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError:
        logger.error("Failed to parse Form 34 Atom feed XML")
        return filings

    for entry in root.findall("atom:entry", NS):
        try:
            title_el = entry.find("atom:title", NS)
            title = title_el.text.strip() if title_el is not None and title_el.text else ""

            cik_match = re.search(r"\((\d{7,10})\)", title)
            if not cik_match:
                continue
            cik = str(int(cik_match.group(1)))

            company_name = re.match(r"34\s*[-–]\s*(.+?)\s*\(\d+\)", title)
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

    logger.info(f"Form 34 feed: {len(filings)} filings")
    return filings


def parse_form34_filing(cik: str, accession_number: str, session: requests.Session) -> List[Dict[str, Any]]:
    """Parse Form 34 filing to extract suspension details."""
    results = []
    if not accession_number:
        return results

    acc_no = accession_number.replace("-", "")
    cik_dir = str(int(cik))
    base_url = f"https://www.sec.gov/Archives/edgar/data/{cik_dir}/{acc_no}"

    # Try to fetch the filing text
    txt_url = f"{base_url}/{accession_number}.txt"
    try:
        resp = session.get(txt_url, headers=SEC_HEADERS, timeout=15)
        if resp.status_code != 200:
            return results

        text = resp.text

        # Extract info
        info = _extract_suspension_info(text)

        # Try to get ticker from filing text (Form 34 often mentions ticker)
        ticker_match = re.search(r'\b([A-Z]{1,5})\b.*?(?:common\s+stock|securities|ordinary\s+shares)', text, re.IGNORECASE)
        ticker = info.get("ticker", "") or (ticker_match.group(1) if ticker_match else "")

        # Filing date from document header
        date_match = re.search(r'FILED\s+(?:AS\s+OF\s+)?(?:DATE\s*:?\s*)?(\d{8})', text)
        if date_match:
            fd = date_match.group(1)
            filing_date = f"{fd[:4]}-{fd[4:6]}-{fd[6:8]}"
        else:
            filing_date = info.get("effective_date", "")

        results.append({
            "ticker": ticker,
            "company_name": "",
            "suspension_type": info.get("action_type", "Suspension"),
            "reason": info.get("reason", ""),
            "effective_date": info.get("effective_date", ""),
            "filing_date": filing_date,
            "source_url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=34",
        })

    except Exception as e:
        logger.debug(f"Error parsing Form 34 for {cik}: {e}")

    return results


def fetch_suspension_daily(date_str: Optional[str] = None) -> Dict[str, Any]:
    """Fetch and store trading suspension data."""
    conn = init_db()
    summary = {"date": date_str or "latest", "filings_found": 0, "stored": 0, "errors": []}

    if not date_str:
        date_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    log_id = log_fetch_start(conn, date_str, source="sec_suspensions")
    session = requests.Session()
    session.headers.update(SEC_HEADERS)

    try:
        filings = fetch_suspension_filings(date_str)
        summary["filings_found"] = len(filings)

        all_suspensions = []
        for i, f in enumerate(filings):
            time.sleep(SEC_RATE_LIMIT)
            suspensions = parse_form34_filing(f["cik"], f["accession_number"], session)

            # Resolve ticker if not in filing
            for s in suspensions:
                if not s["ticker"]:
                    s["ticker"] = _lookup_ticker_from_sec(f["cik"], session)
                s["company_name"] = f.get("company_name", "")

            all_suspensions.extend(suspensions)

            if (i + 1) % 10 == 0:
                logger.info(f"  Processed {i + 1}/{len(filings)} Form 34s")

        if all_suspensions:
            count = upsert_suspensions(conn, all_suspensions)
            summary["stored"] = count
            logger.info(f"Stored {count} trading suspensions")

        log_fetch_end(conn, log_id, items_checked=len(filings), new_items=summary["stored"])

    except Exception as e:
        summary["errors"].append(str(e))
        logger.error(f"Suspension fetch failed: {e}")
        log_fetch_end(conn, log_id, status="error", error=str(e))
    finally:
        session.close()
        conn.close()

    return summary


def init(db_path=None):
    """Backfill recent trading suspension data."""
    conn = init_db(db_path)
    summary = {"stored": 0}
    try:
        today = datetime.now()
        for i in range(30):
            d = (today - timedelta(days=i))
            if d.weekday() >= 5:
                continue
            r = fetch_suspension_daily(d.strftime("%Y-%m-%d"))
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
        print(fetch_suspension_daily(sys.argv[idx + 1]))
    else:
        print(fetch_suspension_daily())
