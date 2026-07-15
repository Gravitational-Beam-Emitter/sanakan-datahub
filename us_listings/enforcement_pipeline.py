"""
SEC Enforcement Actions pipeline — AAER + Litigation Releases + Admin Proceedings.

Tracks SEC enforcement against companies/individuals:
  - AAER: Accounting and Auditing Enforcement Releases
  - LR: Litigation Releases (civil suits)
  - AP: Administrative Proceedings

Usage:
    python -m us_listings.enforcement_pipeline --init
    python -m us_listings.enforcement_pipeline --type AAER
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
from us_listings.storage import init_db, upsert_enforcement, log_fetch_start, log_fetch_end

logger = logging.getLogger("us_listings.enforcement_pipeline")

SEC_ENFORCEMENT_FEEDS = {
    "AAER": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=AAER&count=40&output=atom",
    "LR": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=LR&count=40&output=atom",
    "AP": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=AP&count=40&output=atom",
}

# Keywords to detect penalties/fines
PENALTY_KEYWORDS = [
    r'\$\s*([\d,]+(?:\.\d+)?)\s*(?:million|billion)',
    r'penalt(?:y|ies)\s+of\s+\$\s*([\d,]+(?:\.\d+)?)',
    r'\$\s*([\d,]+(?:\.\d+)?)\s*(?:civil|monetary)\s+(?:penalty|fine|payment)',
    r'sanctions?\s+(?:of|totaling)\s+\$\s*([\d,]+(?:\.\d+)?)',
    r'disgorgement\s+(?:of|totaling)\s+\$\s*([\d,]+(?:\.\d+)?)',
]


def _parse_penalty_amount(text: str) -> Optional[float]:
    """Try to extract penalty/fine amount from text."""
    for pattern in PENALTY_KEYWORDS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            amt_str = match.group(1).replace(",", "")
            try:
                amt = float(amt_str)
                # Check if "million" or "billion" modifier
                before = text[max(0, match.start() - 50):match.start()].lower() + text[match.end():match.end() + 20].lower()
                if "billion" in before:
                    amt *= 1e9
                elif "million" in before:
                    amt *= 1e6
                return amt
            except ValueError:
                pass
    return None


def _extract_companies(text: str) -> List[str]:
    """Extract company names mentioned in enforcement text."""
    companies = []
    # Look for patterns like "In the Matter of XYZ Corp." or "against ABC Inc."
    patterns = [
        r'(?:In the Matter of|against|respondent)\s+([A-Z][A-Za-z\s&.,]+?)(?:,\s*(?:Inc\.?|Corp\.?|LLC|Ltd\.?|PLC|S\.A\.))',
        r'([A-Z][A-Za-z\s&.,]+?)(?:,\s*(?:Inc\.?|Corp\.?|LLC|Ltd\.?|PLC|S\.A\.))\s*(?:\(|was|has|is|and)',
    ]
    for pattern in patterns:
        matches = re.findall(pattern, text)
        companies.extend(m.strip() for m in matches)
    return list(dict.fromkeys(companies))  # deduplicate


def fetch_enforcement_filings(feed_type: str, date_str: Optional[str] = None) -> List[Dict[str, Any]]:
    """Fetch enforcement filings from SEC Atom feed (AAER/LR/AP)."""
    filings = []
    target_date = date_str or datetime.now().strftime("%Y-%m-%d")
    url = SEC_ENFORCEMENT_FEEDS.get(feed_type, "")
    if not url:
        return filings

    logger.info(f"Fetching SEC {feed_type} filings...")
    try:
        resp = requests.get(url, headers=SEC_HEADERS, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"Failed to fetch {feed_type} feed: {e}")
        return filings

    NS = {"atom": "http://www.w3.org/2005/Atom"}
    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError:
        logger.error(f"Failed to parse {feed_type} feed XML")
        return filings

    for entry in root.findall("atom:entry", NS):
        try:
            title_el = entry.find("atom:title", NS)
            title = title_el.text.strip() if title_el is not None and title_el.text else ""
            if "no recent filings" in title.lower():
                continue

            cik_match = re.search(r"\((\d{7,10})\)", title)
            cik = str(int(cik_match.group(1))) if cik_match else ""

            entity_name = re.match(rf"{feed_type}\s*[-–]\s*(.+?)\s*\(\d+\)", title)
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
                "enforcement_type": feed_type,
                "cik": cik,
                "entity_name": entity_name,
                "filing_date": filing_date,
                "accession_number": accession_number,
                "source_url": link,
            })
        except Exception:
            continue

    logger.info(f"{feed_type} feed: {len(filings)} filings")
    return filings


def parse_enforcement_filing(filing: Dict, session: requests.Session) -> List[Dict[str, Any]]:
    """Parse an enforcement filing to extract details."""
    results = []
    cik = filing.get("cik", "")
    acc = filing.get("accession_number", "")
    if not acc:
        return results

    acc_no = acc.replace("-", "")
    cik_dir = str(int(cik)) if cik else ""
    if not cik_dir:
        return results

    base_url = f"https://www.sec.gov/Archives/edgar/data/{cik_dir}/{acc_no}"
    txt_url = f"{base_url}/{acc}.txt"

    try:
        resp = session.get(txt_url, headers=SEC_HEADERS, timeout=15)
        if resp.status_code != 200:
            return results

        text = resp.text[:10000]  # First 10K chars usually enough for summary

        penalty = _parse_penalty_amount(text)
        companies = _extract_companies(text)

        # Extract a brief description
        desc = text[:2000]
        # Try to get first meaningful paragraph
        para_match = re.search(r'(?:ORDER|COMPLAINT|NOTICE|SUMMARY)\s*\n(.*?)(?:\n\n|\n[A-Z])', text, re.DOTALL | re.IGNORECASE)
        description = para_match.group(1).strip()[:500] if para_match else desc[:300]

        results.append({
            "enforcement_type": filing.get("enforcement_type", ""),
            "entity_name": filing.get("entity_name", ""),
            "ticker": "|".join(companies) if companies else "",
            "penalty_amount": penalty,
            "description": description,
            "filing_date": filing.get("filing_date", ""),
            "source_url": filing.get("source_url", ""),
        })

    except Exception as e:
        logger.debug(f"Error parsing {filing.get('enforcement_type')} filing for {cik}: {e}")

    return results


def fetch_enforcement_daily(date_str: Optional[str] = None, feed_type: Optional[str] = None) -> Dict[str, Any]:
    """Fetch and store SEC enforcement actions."""
    conn = init_db()
    summary = {"date": date_str or "latest", "filings_found": 0, "stored": 0, "errors": []}

    if not date_str:
        date_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    types_to_fetch = [feed_type] if feed_type else list(SEC_ENFORCEMENT_FEEDS.keys())
    log_id = log_fetch_start(conn, date_str, source="sec_enforcement")
    session = requests.Session()
    session.headers.update(SEC_HEADERS)

    try:
        all_actions = []
        for etype in types_to_fetch:
            filings = fetch_enforcement_filings(etype, date_str)
            summary["filings_found"] += len(filings)

            for i, f in enumerate(filings):
                time.sleep(SEC_RATE_LIMIT)
                actions = parse_enforcement_filing(f, session)
                all_actions.extend(actions)

                if (i + 1) % 10 == 0:
                    logger.info(f"  Processed {i + 1}/{len(filings)} {etype}")

        if all_actions:
            count = upsert_enforcement(conn, all_actions)
            summary["stored"] = count
            logger.info(f"Stored {count} enforcement actions")

        log_fetch_end(conn, log_id, items_checked=summary["filings_found"],
                      new_items=summary["stored"])

    except Exception as e:
        summary["errors"].append(str(e))
        logger.error(f"Enforcement fetch failed: {e}")
        log_fetch_end(conn, log_id, status="error", error=str(e))
    finally:
        session.close()
        conn.close()

    return summary


def init(db_path=None):
    """Backfill recent enforcement actions."""
    conn = init_db(db_path)
    summary = {"stored": 0}
    try:
        today = datetime.now()
        for i in range(14):
            d = (today - timedelta(days=i))
            if d.weekday() >= 5:
                continue
            r = fetch_enforcement_daily(d.strftime("%Y-%m-%d"))
            summary["stored"] += r["stored"]
            time.sleep(SEC_RATE_LIMIT * 2)
    finally:
        conn.close()
    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    if "--init" in sys.argv:
        print(init())
    elif "--type" in sys.argv:
        idx = sys.argv.index("--type")
        print(fetch_enforcement_daily(feed_type=sys.argv[idx + 1]))
    elif "--date" in sys.argv:
        idx = sys.argv.index("--date")
        print(fetch_enforcement_daily(sys.argv[idx + 1]))
    else:
        print(fetch_enforcement_daily())
