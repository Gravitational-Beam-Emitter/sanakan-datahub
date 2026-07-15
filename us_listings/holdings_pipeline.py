"""
Institutional Holdings pipeline — SEC 13F filings (quarterly).

13F-HR: filed within 45 days of quarter end by institutional investors
with >$100M in US equities. Each filing contains hundreds/thousands of positions.

Usage:
    python -m us_listings.holdings_pipeline --init
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
from us_listings.storage import init_db, upsert_holdings, log_fetch_start, log_fetch_end

logger = logging.getLogger("us_listings.holdings_pipeline")

SEC_13F_RSS = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=13F-HR&count=40&output=atom"


def fetch_13f_filings() -> List[Dict[str, Any]]:
    """Fetch recent 13F-HR filings from SEC Atom feed."""
    filings = []
    logger.info("Fetching 13F-HR filings...")
    try:
        resp = requests.get(SEC_13F_RSS, headers=SEC_HEADERS, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"Failed: {e}")
        return filings

    NS = {"atom": "http://www.w3.org/2005/Atom"}
    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError:
        logger.error("XML parse error in 13F feed")
        return filings

    for entry in root.findall("atom:entry", NS):
        try:
            title_el = entry.find("atom:title", NS)
            title = title_el.text.strip() if title_el is not None and title_el.text else ""

            cik_match = re.search(r"\((\d{7,10})\)", title)
            if not cik_match:
                continue
            cik = str(int(cik_match.group(1)))

            filer_name = re.match(r"13F-HR\s*[-–]\s*(.+?)\s*\(\d+\)", title)
            filer_name = filer_name.group(1).strip() if filer_name else ""

            id_el = entry.find("atom:id", NS)
            entry_id = id_el.text.strip() if id_el is not None and id_el.text else ""
            acc_match = re.search(r"accession-number=([\d-]+)", entry_id)
            accession_number = acc_match.group(1) if acc_match else ""

            updated_el = entry.find("atom:updated", NS)
            updated = updated_el.text.strip() if updated_el is not None and updated_el.text else ""
            filing_date = updated[:10]

            link = ""
            for link_el in entry.findall("atom:link", NS):
                if link_el.get("rel") == "alternate":
                    link = link_el.get("href", "")
                    break

            filings.append({
                "cik": cik, "filer_name": filer_name,
                "accession_number": accession_number,
                "filing_date": filing_date, "link": link,
            })
        except Exception:
            continue

    logger.info(f"13F feed: {len(filings)} filings")
    return filings


def parse_13f_xml(cik: str, accession_number: str, session: requests.Session) -> List[Dict[str, Any]]:
    """Parse 13F-HR informationTable XML to extract holdings."""
    holdings = []
    if not accession_number:
        return holdings

    acc_no = accession_number.replace("-", "")
    cik_dir = str(int(cik))

    # 13F filings use a specific XML naming convention
    # The information table is typically at: {base_url}/primary_doc.xml
    # or embedded in the filing document
    base_url = f"https://www.sec.gov/Archives/edgar/data/{cik_dir}/{acc_no}"

    # Try full submission text file first — primary_doc.xml is the cover
    # page XML and does NOT contain the informationTable
    xml_urls = [
        f"{base_url}/{accession_number}.txt",
        f"{base_url}/primary_doc.xml",
    ]

    xml_text = None
    for url in xml_urls:
        try:
            resp = session.get(url, headers=SEC_HEADERS, timeout=15)
            if resp.status_code == 200 and "informationTable" in resp.text:
                xml_text = resp.text
                break
        except Exception:
            continue

    if not xml_text:
        return holdings

    # Extract quarter end from the filing header
    quarter_end = ""
    q_match = re.search(r"CONFORMED PERIOD OF REPORT:\s*(\d{8})", xml_text)
    if q_match:
        qe = q_match.group(1)
        quarter_end = f"{qe[:4]}-{qe[4:6]}-{qe[6:8]}"

    # The information table is in XML format within the submission
    # Look for <informationTable> ... </informationTable>
    info_match = re.search(
        r"<informationTable>(.*?)</informationTable>", xml_text, re.DOTALL
    )
    if not info_match:
        # Try without namespace
        info_match = re.search(
            r"<informationTable.*?>(.*?)</informationTable>", xml_text, re.DOTALL
        )

    if info_match:
        info_xml = "<root>" + info_match.group(1) + "</root>"
    else:
        # Some filings have the XML embedded with namespace prefixes
        info_match = re.search(
            r"<ns1:informationTable.*?>(.*?)</ns1:informationTable>", xml_text, re.DOTALL
        )
        if info_match:
            info_xml = "<root>" + info_match.group(1) + "</root>"
        else:
            logger.debug(f"No informationTable found for {cik}")
            return holdings

    try:
        root = ET.fromstring(info_xml)
    except ET.ParseError:
        logger.debug(f"XML parse error in 13F info table for {cik}")
        return holdings

    for entry in root.findall("infoTable"):
        try:
            name_of_issuer = ""
            title_of_class = ""
            cusip = ""
            ticker = ""
            value_x1000 = 0
            shares = 0

            for child in entry:
                tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                text = child.text.strip() if child.text else ""
                if tag == "nameOfIssuer":
                    name_of_issuer = text
                elif tag == "titleOfClass":
                    title_of_class = text
                elif tag == "cusip":
                    cusip = text[:8] if text else ""
                elif tag == "ticker":
                    ticker = text.upper()
                elif tag == "value":
                    try:
                        value_x1000 = int(text)
                    except ValueError:
                        pass
                elif tag in ("shrsOrPrnAmt", "sshPrnamt"):
                    for sub in child:
                        sub_tag = sub.tag.split("}")[-1] if "}" in sub.tag else sub.tag
                        if sub_tag == "sshPrnamt":
                            try:
                                shares = float(sub.text) if sub.text else 0
                            except ValueError:
                                pass

            if ticker or cusip:
                holdings.append({
                    "filer_cik": cik,
                    "filer_name": "",
                    "ticker": ticker or cusip,
                    "cusip": cusip,
                    "security_name": f"{name_of_issuer} - {title_of_class}",
                    "shares": shares,
                    "market_value": value_x1000 * 1000 if value_x1000 else 0,
                    "quarter_end": quarter_end,
                    "filing_date": "",
                    "source_url": "",
                })
        except Exception:
            continue

    return holdings


def fetch_holdings_daily() -> Dict[str, Any]:
    """Fetch and store 13F filings from the RSS feed."""
    conn = init_db()
    summary = {"filings_found": 0, "holdings_stored": 0, "errors": []}
    session = requests.Session()
    session.headers.update(SEC_HEADERS)

    today = datetime.now().strftime("%Y-%m-%d")
    log_id = log_fetch_start(conn, today, source="sec_13f")

    try:
        filings = fetch_13f_filings()
        summary["filings_found"] = len(filings)
        all_holdings = []

        for i, f in enumerate(filings):
            time.sleep(SEC_RATE_LIMIT)
            holdings = parse_13f_xml(f["cik"], f["accession_number"], session)
            for h in holdings:
                h["filer_name"] = f["filer_name"]
                h["filing_date"] = f["filing_date"]
                h["source_url"] = f["link"]
            all_holdings.extend(holdings)

            if holdings:
                logger.info(f"  {f['filer_name']}: {len(holdings)} positions")
            if (i + 1) % 5 == 0:
                logger.info(f"  Processed {i + 1}/{len(filings)}, {len(all_holdings)} total holdings")

        if all_holdings:
            count = upsert_holdings(conn, all_holdings)
            summary["holdings_stored"] = count
            logger.info(f"Stored {count} institutional holdings")

        log_fetch_end(conn, log_id, items_checked=len(filings), new_items=summary["holdings_stored"])

    except Exception as e:
        summary["errors"].append(str(e))
        logger.error(f"Holdings fetch failed: {e}")
        log_fetch_end(conn, log_id, status="error", error=str(e))
    finally:
        session.close()
        conn.close()

    return summary


def init(db_path=None):
    return fetch_holdings_daily()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    print(fetch_holdings_daily())
