"""
ATS / Dark Pool pipeline — SEC Form ATS-N filings.

Form ATS-N: Filed by Alternative Trading Systems (dark pools) to disclose
operational details. Types include:
  - ATS-N/UA: Update Amendment (operational changes)
  - ATS-N/MA: Material Amendment (significant changes)
  - ATS-N/CA: Cessation Amendment (ATS closing)

Usage:
    python -m us_listings.ats_pipeline --init
    python -m us_listings.ats_pipeline
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
from us_listings.storage import init_db, upsert_ats_filings, log_fetch_start, log_fetch_end

logger = logging.getLogger("us_listings.ats_pipeline")

SEC_ATS_RSS = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=ATS-N&count=40&output=atom"

# ATS-N filing types
ATS_TYPES = {
    "ATS-N": "Initial",
    "ATS-N/UA": "Update",
    "ATS-N/MA": "Material",
    "ATS-N/CA": "Cessation",
}


def _extract_ats_details(text: str) -> Dict[str, Any]:
    """Extract ATS operational details from filing text."""
    details = {
        "venue_name": "",
        "volume_estimate": None,
        "securities_traded": "",
        "hours_of_operation": "",
    }

    # Venue/ATS name
    name_match = re.search(r'(?:Name\s+of\s+(?:the\s+)?(?:ATS|Alternative\s+Trading\s+System)|Venue\s+Name)[:\s]+([^\n]{5,100})', text, re.IGNORECASE)
    if name_match:
        details["venue_name"] = name_match.group(1).strip()

    # Volume estimates
    vol_match = re.search(r'(?:average\s+daily\s+volume|ADV|daily\s+share\s+volume)[^\d]*([\d,]+(?:\.\d+)?)\s*(million|billion|thousand)?', text, re.IGNORECASE)
    if vol_match:
        try:
            vol = float(vol_match.group(1).replace(",", ""))
            modifier = (vol_match.group(2) or "").lower()
            if "billion" in modifier:
                vol *= 1e9
            elif "million" in modifier:
                vol *= 1e6
            elif "thousand" in modifier:
                vol *= 1e3
            details["volume_estimate"] = vol
        except ValueError:
            pass

    # Securities types
    sec_match = re.search(r'(?:types?\s+of\s+)?securities?\s+(?:traded|available)[:\s]+([^\n]{10,200})', text, re.IGNORECASE)
    if sec_match:
        details["securities_traded"] = sec_match.group(1).strip()

    # Hours
    hours_match = re.search(r'(?:hours?\s+of\s+operation|operating\s+hours?)[:\s]+([^\n]{5,80})', text, re.IGNORECASE)
    if hours_match:
        details["hours_of_operation"] = hours_match.group(1).strip()

    return details


def fetch_ats_filings(date_str: Optional[str] = None) -> List[Dict[str, Any]]:
    """Fetch ATS-N filings from SEC Atom feed."""
    filings = []
    target_date = date_str or datetime.now().strftime("%Y-%m-%d")

    logger.info(f"Fetching ATS-N filings (target: {target_date})...")
    try:
        resp = requests.get(SEC_ATS_RSS, headers=SEC_HEADERS, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"Failed to fetch ATS-N feed: {e}")
        return filings

    NS = {"atom": "http://www.w3.org/2005/Atom"}
    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError:
        logger.error("Failed to parse ATS-N feed XML")
        return filings

    for entry in root.findall("atom:entry", NS):
        try:
            title_el = entry.find("atom:title", NS)
            title = title_el.text.strip() if title_el is not None and title_el.text else ""
            if "no recent filings" in title.lower():
                continue

            cik_match = re.search(r"\((\d{7,10})\)", title)
            if not cik_match:
                continue
            cik = str(int(cik_match.group(1)))

            # Extract filing type from title
            filing_type = "ATS-N"
            for ftype in ["ATS-N/UA", "ATS-N/MA", "ATS-N/CA", "ATS-N"]:
                if ftype in title:
                    filing_type = ftype
                    break

            entity_name = re.match(rf"(?:ATS-N(?:/[A-Z]{{2}})?)\s*[-–]\s*(.+?)\s*\(\d+\)", title)
            entity_name = entity_name.group(1).strip() if entity_name else title

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
                "cik": cik,
                "entity_name": entity_name,
                "filing_type": filing_type,
                "filing_date": filing_date,
                "accession_number": accession_number,
                "source_url": link,
            })
        except Exception:
            continue

    logger.info(f"ATS-N feed: {len(filings)} filings")
    return filings


def parse_ats_filing(cik: str, accession_number: str, session: requests.Session) -> List[Dict[str, Any]]:
    """Parse ATS-N filing to extract operational details."""
    results = []
    if not accession_number:
        return results

    acc_no = accession_number.replace("-", "")
    cik_dir = str(int(cik))
    base_url = f"https://www.sec.gov/Archives/edgar/data/{cik_dir}/{acc_no}"

    txt_url = f"{base_url}/{accession_number}.txt"
    try:
        resp = session.get(txt_url, headers=SEC_HEADERS, timeout=15)
        if resp.status_code != 200:
            return results

        text = resp.text
        details = _extract_ats_details(text)

        results.append({
            "ats_name": details["venue_name"] or "",
            "filer_cik": cik,
            "filer_name": "",
            "filing_type": "ATS-N",
            "volume_estimate": details["volume_estimate"],
            "securities_traded": details["securities_traded"],
            "description": details["hours_of_operation"] or "",
            "filing_date": "",
            "source_url": "",
        })

    except Exception as e:
        logger.debug(f"Error parsing ATS-N for {cik}: {e}")

    return results


def fetch_ats_daily(date_str: Optional[str] = None) -> Dict[str, Any]:
    """Fetch and store ATS/dark pool filings."""
    conn = init_db()
    summary = {"date": date_str or "latest", "filings_found": 0, "stored": 0, "errors": []}

    if not date_str:
        date_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    log_id = log_fetch_start(conn, date_str, source="sec_ats")
    session = requests.Session()
    session.headers.update(SEC_HEADERS)

    try:
        filings = fetch_ats_filings(date_str)
        summary["filings_found"] = len(filings)

        all_ats_records = []
        for i, f in enumerate(filings):
            time.sleep(SEC_RATE_LIMIT)
            records = parse_ats_filing(f["cik"], f["accession_number"], session)

            for r in records:
                r["filer_name"] = f.get("entity_name", "")
                r["filing_type"] = f.get("filing_type", "ATS-N")
                r["filing_date"] = f.get("filing_date", "")
                r["source_url"] = f.get("source_url", "")

            all_ats_records.extend(records)

            if (i + 1) % 10 == 0:
                logger.info(f"  Processed {i + 1}/{len(filings)} ATS-N")

        if all_ats_records:
            count = upsert_ats_filings(conn, all_ats_records)
            summary["stored"] = count
            logger.info(f"Stored {count} ATS/dark pool records")

        log_fetch_end(conn, log_id, items_checked=len(filings),
                      new_items=summary["stored"])

    except Exception as e:
        summary["errors"].append(str(e))
        logger.error(f"ATS fetch failed: {e}")
        log_fetch_end(conn, log_id, status="error", error=str(e))
    finally:
        session.close()
        conn.close()

    return summary


def init(db_path=None):
    """Backfill recent ATS filings."""
    conn = init_db(db_path)
    summary = {"stored": 0}
    try:
        today = datetime.now()
        for i in range(14):
            d = (today - timedelta(days=i))
            if d.weekday() >= 5:
                continue
            r = fetch_ats_daily(d.strftime("%Y-%m-%d"))
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
        print(fetch_ats_daily(sys.argv[idx + 1]))
    else:
        print(fetch_ats_daily())
