"""
Data pipeline — fetch US corporate actions from SEC EDGAR 8-K filings.

Usage:
    python -m us_corp_actions.pipeline --init         # first run: download CIK map + backfill
    python -m us_corp_actions.pipeline                 # fetch latest trading day's 8-Ks
    python -m us_corp_actions.pipeline --date 20260612  # fetch specific date
    python -m us_corp_actions.pipeline --from 20260610 --to 20260617  # date range
"""

from __future__ import annotations

import logging
import re
import sys
import time
from datetime import datetime, timedelta, date
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

import xml.etree.ElementTree as ET

import pandas as pd
import requests
from bs4 import BeautifulSoup

from us_corp_actions.config import (
    SEC_HEADERS,
    SEC_RATE_LIMIT,
    SEC_COMPANY_TICKERS_URL,
    SEC_RSS_URL,
    BACKFILL_START,
)
from us_corp_actions.storage import (
    init_db,
    upsert_listed_companies,
    upsert_corporate_actions,
    log_fetch_start,
    log_fetch_end,
    cleanup_old_records,
    get_ticker_for_cik,
    get_company_name_for_cik,
)

logger = logging.getLogger("us_corp_actions.pipeline")

# ── 8-K Item → Corporate Action Type Mapping ──
ITEM_ACTION_MAP: Dict[str, Tuple[str, str]] = {
    # (action_type, action_subtype)
    "1.01": ("merger_acquisition", "material_definitive_agreement"),
    "1.02": ("merger_acquisition", "termination_of_material_agreement"),
    "1.03": ("bankruptcy", "bankruptcy_receivership"),
    "2.01": ("merger_acquisition", "completion_of_acquisition"),
    "2.02": ("earnings", "results_of_operations"),
    "2.03": ("securities_issuance", "direct_financial_obligation"),
    "2.04": ("securities_issuance", "triggering_event"),
    "2.05": ("other", "cost_exit_disposal"),
    "2.06": ("other", "material_impairment"),
    "3.01": ("delisting", "notice_of_delisting"),
    "3.02": ("securities_issuance", "unregistered_sales"),
    "3.03": ("equity_change", "material_modification_of_rights"),
    "4.01": ("other", "accountant_change"),
    "4.02": ("other", "non_reliance_on_financials"),
    "5.01": ("equity_change", "change_in_control"),
    "5.02": ("equity_change", "director_officer_change"),
    "5.03": ("equity_change", "articles_bylaws_amendment"),
    "5.04": ("equity_change", "trading_suspension"),
    "5.05": ("equity_change", "code_of_ethics_amendment"),
    "5.06": ("equity_change", "shelf_trading_plan"),
    "5.07": ("equity_change", "say_on_pay_vote"),
    "5.08": ("equity_change", "shareholder_director_nomination"),
    "6.01": ("other", "abs_informational"),
    "6.02": ("other", "abs_servicer_change"),
    "6.03": ("other", "abs_credit_enhancement"),
    "6.04": ("other", "abs_failure_to_distribute"),
    "6.05": ("other", "abs_derivatives"),
    "6.06": ("other", "abs_filing_extension"),
    "7.01": ("other", "regulation_fd_disclosure"),
    "8.01": ("other", "other_events"),
    "9.01": ("other", "financial_statements_exhibits"),
}

# Items that indicate dividends (subset of 8.01 with dividend keywords)
DIVIDEND_KEYWORDS = [
    r"dividend", r"distribution", r"dividend\s+declaration",
    r"declared\s+a\s+(quarterly|special|cash)\s+dividend",
    r"record\s+date.*dividend", r"payable.*dividend",
]

# Items that indicate stock splits
SPLIT_KEYWORDS = [
    r"stock\s+split", r"reverse\s+split", r"forward\s+split",
    r"share\s+split", r"subdivision", r"consolidation\s+of\s+shares",
]

# Items that indicate buybacks
BUYBACK_KEYWORDS = [
    r"share\s+repurchase", r"stock\s+repurchase", r"buyback",
    r"share\s+buy\s*back", r"repurchase\s+(program|plan|authorization)",
]


def _classify_items(items: List[str], description: str = "") -> Tuple[str, str]:
    """Classify a list of 8-K item numbers into a corporate action type.

    Args:
        items: List of item strings like ["1.01", "2.03"]
        description: Optional filing description for keyword matching

    Returns:
        (action_type, action_subtype) tuple
    """
    text = description.lower()

    # Check for stock splits via keywords
    for kw in SPLIT_KEYWORDS:
        if re.search(kw, text):
            return ("stock_split", "forward_split")

    # Check for buybacks
    for kw in BUYBACK_KEYWORDS:
        if re.search(kw, text):
            return ("buyback", "share_repurchase")

    # Check for dividends
    for kw in DIVIDEND_KEYWORDS:
        if re.search(kw, text):
            return ("dividend", "cash_dividend")

    # Map by item number priority (most informative first)
    priority_order = [
        "1.03",  # bankruptcy
        "3.01",  # delisting
        "2.01",  # merger/acquisition completed
        "1.01",  # material agreement
        "2.02",  # earnings
        "2.03",  # financial obligation
        "3.02",  # unregistered securities
        "3.03",  # rights modification
        "5.01",  # change in control
        "5.02",  # director/officer change
        "5.03",  # bylaws amendment
        "2.04", "2.05", "2.06",  # other financial events
        "4.01", "4.02",  # accounting
        "5.04", "5.05", "5.06", "5.07", "5.08",  # governance
        "7.01", "8.01", "9.01",  # general
    ]

    # Pick the highest-priority item
    items_set = set(items)
    for item_num in priority_order:
        if item_num in items_set:
            action_type, action_subtype = ITEM_ACTION_MAP.get(
                item_num, ("other", "other_events")
            )
            return (action_type, action_subtype)

    return ("other", "other_events")


def _parse_items_from_html(html: str) -> List[str]:
    """Extract 8-K Item numbers from SEC filing page HTML.

    Looks for patterns like 'Item 1.01', 'Items 1.01, 2.03', etc.
    in the filing description table.
    """
    items = []
    soup = BeautifulSoup(html, "lxml")

    # Look for the filing items in the table
    for td in soup.find_all("td"):
        text = td.get_text(strip=True)
        # Match "Item X.XX" or "Items X.XX, Y.YY"
        found = re.findall(r"Item\s*(\d+\.\d+)", text)
        items.extend(found)

    # Also search the whole page text for item references
    if not items:
        page_text = soup.get_text()
        # Look for item patterns in context of 8-K
        found = re.findall(r"Item\s*(\d+\.\d+)", page_text)
        items.extend(found)

    return list(dict.fromkeys(items))  # dedupe preserving order


def _parse_items_from_filing_document(html: str) -> List[str]:
    """Extract 8-K Items from the actual 8-K filing document HTML.

    The 8-K document contains headers like:
    <b>Item 1.01</b> Entry into a Material Definitive Agreement
    """
    items = []
    # Pattern: Item X.XX appearing at the start of a line or after HTML tags
    patterns = [
        r'Item\s*(\d+\.\d+)',
        r'<b>Item\s*(\d+\.\d+)</b>',
        r'<strong>Item\s*(\d+\.\d+)</strong>',
        r'>Item\s*(\d+\.\d+)\s*<',
    ]
    for pattern in patterns:
        found = re.findall(pattern, html, re.IGNORECASE)
        items.extend(found)

    # Filter out items that appear to be just cross-references
    # (usually >20 chars away from "Item" in the source)
    items = list(dict.fromkeys(items))
    return items


# ── SEC Company Tickers ──

def download_cik_ticker_map() -> List[Dict[str, str]]:
    """Download CIK-to-Ticker mapping from SEC.

    Returns list of dicts with keys: cik, ticker, company_name, exchange
    """
    logger.info("Downloading CIK-ticker mapping from SEC...")
    resp = requests.get(SEC_COMPANY_TICKERS_URL, headers=SEC_HEADERS, timeout=30)
    resp.raise_for_status()

    data = resp.json()
    companies = []
    for entry in data.values():
        cik_str = str(entry.get("cik_str", ""))
        ticker = str(entry.get("ticker", "")).upper()
        name = str(entry.get("title", ""))
        if cik_str and ticker:
            companies.append({
                "cik": cik_str,
                "ticker": ticker,
                "company_name": name,
                "exchange": _guess_exchange(ticker),
            })

    logger.info(f"Downloaded {len(companies)} company ticker mappings")
    return companies


def _guess_exchange(ticker: str) -> str:
    """Guess exchange from ticker pattern (heuristic)."""
    # NASDAQ tickers are typically 4+ letters
    # NYSE tickers are typically 1-3 letters
    # This is a rough heuristic
    if not ticker:
        return "UNKNOWN"
    letters = re.sub(r"[^A-Z]", "", ticker)
    if len(letters) <= 3:
        return "NYSE"
    else:
        return "NASDAQ"


# ── SEC EDGAR Fetch ──

def _padded_cik(cik: str) -> str:
    """Pad CIK to 10 digits for SEC API URLs."""
    return str(int(cik)).zfill(10)


def fetch_filings_rss(date_str: Optional[str] = None) -> List[Dict[str, Any]]:
    """Fetch latest 8-K filings from SEC EDGAR Atom feed.

    The SEC Atom feed includes Item numbers directly in the summary field,
    so we can classify without additional HTTP requests per filing.

    Args:
        date_str: Target date YYYY-MM-DD. If None, fetches all from feed.

    Returns:
        List of filing dicts with keys: cik, company_name, form_type,
        filing_date, accession_number, link, items, summary
    """
    filings = []
    target_date = date_str or datetime.now().strftime("%Y-%m-%d")

    logger.info(f"Fetching 8-K filings from SEC Atom feed (target: {target_date})...")

    try:
        resp = requests.get(SEC_RSS_URL, headers=SEC_HEADERS, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"Failed to fetch SEC Atom feed: {e}")
        return filings

    # Parse Atom XML directly (feedparser has issues with SEC's feed)
    NS = {"atom": "http://www.w3.org/2005/Atom"}
    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError as e:
        logger.error(f"Failed to parse Atom feed XML: {e}")
        return filings

    for entry in root.findall("atom:entry", NS):
        try:
            # Parse CIK from title: "8-K - Company Name (0001234567) (Filer)"
            title_el = entry.find("atom:title", NS)
            title = title_el.text.strip() if title_el is not None and title_el.text else ""

            cik_match = re.search(r"\((\d{7,10})\)", title)
            if not cik_match:
                continue
            cik = str(int(cik_match.group(1)))

            # Parse company name
            name_match = re.match(r"8-K\s*[-–]\s*(.+?)\s*\(\d+\)", title)
            company_name = name_match.group(1).strip() if name_match else ""

            # Filing date from updated field
            updated_el = entry.find("atom:updated", NS)
            updated = updated_el.text.strip() if updated_el is not None and updated_el.text else ""
            filing_date = updated[:10] if updated else target_date

            # Only keep filings matching target date (when specified)
            if date_str and filing_date != date_str:
                continue

            # Accession number from id field: urn:tag:sec.gov,2008:accession-number=XXX
            id_el = entry.find("atom:id", NS)
            entry_id = id_el.text.strip() if id_el is not None and id_el.text else ""
            accession_match = re.search(r"accession-number=([\d-]+)", entry_id)
            accession_number = accession_match.group(1) if accession_match else ""

            # Link to filing detail page
            link = ""
            for link_el in entry.findall("atom:link", NS):
                if link_el.get("rel") == "alternate":
                    link = link_el.get("href", "")
                    break

            # Summary: contains HTML with Item numbers already listed!
            summary_el = entry.find("atom:summary", NS)
            summary = summary_el.text.strip() if summary_el is not None and summary_el.text else ""

            # Extract items directly from summary (SEC feed already lists them)
            items = _items_from_text(summary)

            filings.append({
                "cik": cik,
                "company_name": company_name,
                "form_type": "8-K",
                "filing_date": filing_date,
                "accession_number": accession_number,
                "link": link,
                "items": items,
                "summary": _clean_html(summary),
            })

        except Exception as e:
            logger.debug(f"Error parsing Atom entry: {e}")
            continue

    logger.info(f"Atom feed returned {len(filings)} 8-K filings")
    return filings


def fetch_filings_by_date(date_str: str) -> List[Dict[str, Any]]:
    """Fetch 8-K filings for a specific date using SEC daily index files.

    SEC daily index files are available at:
    https://www.sec.gov/Archives/edgar/daily-index/{year}/QTR{q}/form.{YYYYMMDD}.idx

    Each line: FORM_TYPE  COMPANY_NAME  CIK  DATE  FILE_PATH

    Args:
        date_str: Date in YYYY-MM-DD format

    Returns:
        List of filing dicts with CIK, company_name, date, file_path
    """
    filings = []

    dt = datetime.strptime(date_str, "%Y-%m-%d")
    year = dt.year
    quarter = f"QTR{(dt.month - 1) // 3 + 1}"
    date_compact = dt.strftime("%Y%m%d")

    url = (
        f"https://www.sec.gov/Archives/edgar/daily-index/"
        f"{year}/{quarter}/form.{date_compact}.idx"
    )

    logger.info(f"Fetching daily index: {url}")

    try:
        resp = requests.get(url, headers=SEC_HEADERS, timeout=30)
        if resp.status_code != 200:
            logger.warning(f"Daily index not found for {date_str} (status {resp.status_code})")
            return filings
    except Exception as e:
        logger.error(f"Failed to fetch daily index for {date_str}: {e}")
        return filings

    # Parse the fixed-width index file
    # Format: FORM_TYPE  COMPANY_NAME  CIK  DATE_FILED  FILE_PATH
    # Columns: 0-11=form, 12-73=company, 74-83=cik, 84-93=date, 94+=path
    lines = resp.text.strip().split("\n")

    for line in lines:
        if len(line) < 90:
            continue

        form_type = line[0:12].strip()
        if form_type != "8-K":
            continue

        company_name = line[12:74].strip()
        cik = line[74:84].strip()
        filing_date_raw = line[84:94].strip()

        # Normalize CIK
        try:
            cik = str(int(cik))
        except ValueError:
            continue

        # Build accession number and URL from the file path
        file_path = line[94:].strip() if len(line) > 94 else ""
        # File path format: edgar/data/CIK/ACCESSION.txt
        accession_number = ""
        if file_path:
            parts = file_path.split("/")
            if len(parts) >= 4:
                accession_number = parts[-1].replace(".txt", "")

        filings.append({
            "cik": cik,
            "company_name": company_name,
            "form_type": "8-K",
            "filing_date": date_str,
            "accession_number": accession_number,
            "link": _build_filing_url(cik, accession_number) if accession_number else "",
            "file_path": file_path,  # For fast item extraction
            "items": [],  # Will be populated during classify_and_prepare
            "summary": "",
        })

    logger.info(f"Daily index for {date_str}: {len(filings)} 8-K filings found")
    return filings


def fetch_filing_items(cik: str, accession_number: str,
                       file_path: str = "",
                       session: Optional[requests.Session] = None) -> Tuple[List[str], str]:
    """Extract 8-K item numbers and description text from a specific filing.

    Returns:
        (list of item strings, plain-text description from the 8-K document)
    """
    items: List[str] = []
    doc_text = ""

    if not accession_number:
        return items, doc_text

    acc_no = accession_number.replace("-", "")

    # Determine the correct CIK directory from file_path if available
    if file_path:
        # file_path format: edgar/data/{cik_dir}/{accession}.txt
        parts = file_path.split("/")
        if len(parts) >= 3:
            cik_dir = parts[2]
        else:
            cik_dir = str(int(cik))
    else:
        cik_dir = str(int(cik))

    base_url = (
        f"https://www.sec.gov/Archives/edgar/data/"
        f"{cik_dir}/{acc_no}"
    )

    fetcher = session if session else requests
    try:
        # Step 1: Fetch the filing index page to find primary document
        index_url = f"{base_url}/{accession_number}-index.htm"
        resp = fetcher.get(index_url, headers=SEC_HEADERS, timeout=8)
        if resp.status_code != 200:
            logger.debug(f"Index page not accessible: {resp.status_code}")
            return items, doc_text

        soup = BeautifulSoup(resp.text, "lxml")

        # Step 2: Find the primary 8-K document link
        # Common patterns: form8-k.htm, *_8k-ixbrl.htm, *_8k.htm, etc.
        doc_url = ""
        xml_url = ""

        for a in soup.find_all("a"):
            href = a.get("href", "")
            text = a.get_text(strip=True).lower()

            # Skip exhibits, graphics, and non-document files
            if any(skip in text for skip in ["ex99", "ex-99", ".jpg", ".png", ".gif", ".xsd", ".xml"]):
                if "_htm.xml" not in text:  # But keep XBRL XML versions
                    continue

            is_8k_doc = (
                "8k" in text or "8-k" in text or
                "form8" in text
            )

            if is_8k_doc and text.endswith(".htm"):
                # Found primary 8-K document
                if href.startswith("/ix?doc="):
                    doc_url = "https://www.sec.gov" + href.split("?doc=")[-1]
                elif href.startswith("/"):
                    doc_url = f"https://www.sec.gov{href}"
                elif href.startswith("http"):
                    doc_url = href
                else:
                    doc_url = f"{base_url}/{href}"
                break

            # Also collect XBRL XML version as fallback
            if "_htm.xml" in text and not xml_url:
                if href.startswith("/"):
                    xml_url = f"https://www.sec.gov{href}"
                elif not href.startswith("http"):
                    xml_url = f"{base_url}/{href}"
                else:
                    xml_url = href

        # If no 8-K document found, try the .txt filing
        if not doc_url:
            doc_url = f"{base_url}/{accession_number}.txt"

        # Step 3: Fetch the 8-K document and extract items + description
        resp_doc = fetcher.get(doc_url, headers=SEC_HEADERS, timeout=8)
        if resp_doc.status_code == 200:
            items = _parse_items_from_filing_document(resp_doc.text)
            # Extract description text from the 8-K document body
            try:
                doc_text = BeautifulSoup(resp_doc.text, "lxml").get_text(" ", strip=True)[:2000]
            except Exception:
                pass
            if items:
                return items, doc_text

        # Step 4: Try XBRL XML version (more structured) as fallback
        if not items and xml_url:
            resp_xml = fetcher.get(xml_url, headers=SEC_HEADERS, timeout=8)
            if resp_xml.status_code == 200:
                items = _parse_items_from_filing_document(resp_xml.text)
                if not doc_text:
                    try:
                        doc_text = BeautifulSoup(resp_xml.text, "lxml").get_text(" ", strip=True)[:2000]
                    except Exception:
                        pass

    except Exception as e:
        logger.debug(f"Failed to extract items for CIK {cik}: {e}")

    return items, doc_text


def classify_and_prepare(
    conn,
    filings: List[Dict[str, Any]],
    ticker_map: Optional[Dict[str, str]] = None,
    fetch_items: bool = False,
) -> List[Dict[str, Any]]:
    """Classify a list of filings into corporate actions.

    For each filing:
    1. Look up ticker from CIK (DB or local map)
    2. Use pre-extracted 8-K items (or classify from description)
    3. Classify into action_type
    4. Return structured records

    Args:
        conn: DuckDB connection
        filings: List of raw filing dicts from SEC
        ticker_map: Optional pre-loaded CIK→ticker dict
        fetch_items: If True, fetch items from SEC filing pages (slow).
                     If False (default for backfill), use keyword classification only.

    Returns:
        List of structured corporate action dicts ready for storage
    """
    actions = []
    seen = set()
    session = requests.Session()
    session.headers.update(SEC_HEADERS)

    for i, f in enumerate(filings):
        cik = f["cik"]
        filing_date = f["filing_date"]
        form_type = f["form_type"]

        # Deduplicate
        key = (filing_date, cik, form_type)
        if key in seen:
            continue
        seen.add(key)

        # Look up ticker
        ticker = ""
        if ticker_map:
            ticker = ticker_map.get(cik, "")
        if not ticker:
            ticker = get_ticker_for_cik(conn, cik) or ""
        if not ticker:
            ticker = _lookup_ticker_from_sec(cik, session)

        # Get company name
        company_name = f.get("company_name", "")
        if not company_name:
            company_name = get_company_name_for_cik(conn, cik) or ""

        # Get items
        items: List[str] = f.get("items", [])
        description = f.get("summary", "")[:2000]

        # If items not pre-extracted and fetch_items is enabled, get from SEC
        if not items and fetch_items:
            accession = f.get("accession_number", "")
            if accession:
                time.sleep(SEC_RATE_LIMIT)
                file_path = f.get("file_path", "")
                try:
                    items, fetched_text = fetch_filing_items(cik, accession, file_path, session)
                    if fetched_text and not description:
                        description = fetched_text
                except Exception:
                    pass

        # If still no items, extract from text description (works for RSS summaries)
        if not items:
            items = _items_from_text(description)

        # Classify
        action_type, action_subtype = _classify_items(items, description)
        item_numbers = ",".join(items) if items else ""

        # Extract dates from description
        dates = _extract_dates(description)

        # Build source URL
        source_url = f.get("link", "")

        actions.append({
            "filing_date": filing_date,
            "cik": cik,
            "ticker": ticker,
            "company_name": company_name or f"CIK {cik}",
            "form_type": form_type,
            "action_type": action_type,
            "action_subtype": action_subtype,
            "item_numbers": item_numbers,
            "effective_date": dates.get("effective_date"),
            "record_date": dates.get("record_date"),
            "pay_date": dates.get("pay_date"),
            "description": description[:1000],
            "source_url": source_url,
        })

        if (i + 1) % 50 == 0:
            logger.info(f"  Processed {i + 1}/{len(filings)} filings...")

    session.close()
    return actions


def _items_from_text(text: str) -> List[str]:
    """Try to extract 8-K item references from plain text or HTML."""
    if not text:
        return []
    items = re.findall(r"Item\s*(\d+\.\d+)", text, re.IGNORECASE)
    return list(dict.fromkeys(items))


def _extract_dates(text: str) -> Dict[str, Optional[str]]:
    """Extract effective, record, and payment dates from filing text.

    Returns dict with keys: effective_date, record_date, pay_date.
    """
    result: Dict[str, Optional[str]] = {
        "effective_date": None,
        "record_date": None,
        "pay_date": None,
    }
    if not text:
        return result

    text_lower = text.lower()

    # Date patterns
    iso_pat = r"(\d{4}-\d{2}-\d{2})"
    us_pat = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+(\d{1,2}),?\s*(\d{4})"

    def _first_date(pattern, context_words):
        """Find first date match near a context word."""
        for word in context_words:
            idx = text_lower.find(word)
            if idx >= 0:
                # Search within 300 chars after the context word
                snippet = text[idx:idx + 300]
                m = re.search(pattern, snippet, re.IGNORECASE)
                if m:
                    return _normalize_date(m)
        return None

    def _normalize_date(m):
        if m.lastindex and m.lastindex >= 3:
            # US format: month name + day + year
            month_str, day, year = m.group(1), m.group(2), m.group(3)
            months = {"jan": "01", "feb": "02", "mar": "03", "apr": "04",
                      "may": "05", "jun": "06", "jul": "07", "aug": "08",
                      "sep": "09", "oct": "10", "nov": "11", "dec": "12"}
            for prefix, num in months.items():
                if month_str.lower().startswith(prefix):
                    return f"{year}-{num}-{int(day):02d}"
        else:
            # ISO format
            return m.group(1)
        return None

    # Effective date
    result["effective_date"] = _first_date(
        iso_pat + "|" + us_pat,
        ["effective", "as of", "commenced on", "entry into", "entered into"],
    )

    # Record date
    result["record_date"] = _first_date(
        iso_pat + "|" + us_pat,
        ["record date", "record_date", "holders of record", "shareholders of record",
         "stockholders of record"],
    )

    # Payment date
    result["pay_date"] = _first_date(
        iso_pat + "|" + us_pat,
        ["payable", "payment date", "pay_date", "paid on", "distribution date",
         "dividend payable"],
    )

    return result


def _lookup_ticker_from_sec(cik: str, session: Optional[requests.Session] = None) -> str:
    """Get ticker from SEC submissions API (covers all SEC filers, not just listed)."""
    try:
        fetcher = session if session else requests
        padded = str(int(cik)).zfill(10)
        url = f"https://data.sec.gov/submissions/CIK{padded}.json"
        resp = fetcher.get(url, headers=SEC_HEADERS, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            tickers = data.get("tickers", [])
            if tickers:
                return tickers[0].upper().strip()
    except Exception:
        pass
    return ""


def _clean_html(html_text: str) -> str:
    """Strip HTML tags from text."""
    soup = BeautifulSoup(html_text, "lxml")
    return soup.get_text(" ", strip=True)


def _build_filing_url(cik: str, accession_number: str) -> str:
    """Build SEC filing URL from CIK and accession number."""
    acc_no = accession_number.replace("-", "")
    return (
        f"https://www.sec.gov/Archives/edgar/data/"
        f"{int(cik)}/{acc_no}/{accession_number}-index.htm"
    )


# ── Main Pipeline Functions ──

def init(db_path: Optional[str] = None) -> Dict[str, Any]:
    """Initialize the pipeline: download CIK map and backfill historical data.

    Returns summary dict.
    """
    conn = init_db(db_path)
    summary = {"companies_loaded": 0, "actions_backfilled": 0, "errors": []}

    try:
        # 1. Download and store CIK-ticker mapping
        companies = download_cik_ticker_map()
        if companies:
            count = upsert_listed_companies(conn, companies)
            summary["companies_loaded"] = count
            logger.info(f"Loaded {count} companies into listed_companies table")

        # 2. Build in-memory ticker map for faster lookups
        ticker_map = {c["cik"]: c["ticker"] for c in companies}

        # 3. Backfill from BACKFILL_START to today
        start = datetime.strptime(BACKFILL_START, "%Y-%m-%d")
        end = datetime.now()
        dates = []
        d = start
        while d <= end:
            # Only fetch weekdays (fewer filings on weekends)
            if d.weekday() < 5:
                dates.append(d.strftime("%Y-%m-%d"))
            d += timedelta(days=1)

        logger.info(f"Backfilling {len(dates)} days from {BACKFILL_START}...")
        total_actions = 0

        for i, day in enumerate(dates):
            logger.info(f"  [{i + 1}/{len(dates)}] Fetching {day}...")
            filings = fetch_filings_by_date(day)
            if filings:
                actions = classify_and_prepare(conn, filings, ticker_map, fetch_items=True)
                if actions:
                    count = upsert_corporate_actions(conn, actions)
                    total_actions += count
                    logger.info(f"    {count} corporate actions stored for {day}")
            time.sleep(SEC_RATE_LIMIT * 2)  # Extra spacing for bulk fetch

        summary["actions_backfilled"] = total_actions

        # 4. Run cleanup
        cleaned = cleanup_old_records(conn)
        logger.info(f"Cleaned up {cleaned} old records")

    except Exception as e:
        summary["errors"].append(str(e))
        logger.error(f"Init failed: {e}")
    finally:
        conn.close()

    return summary


def fetch_daily(date_str: Optional[str] = None, db_path: Optional[str] = None) -> Dict[str, Any]:
    """Fetch and store corporate actions for a single day.

    Args:
        date_str: Date in YYYY-MM-DD format. None = latest available.
        db_path: Optional DuckDB path override.

    Returns:
        Summary dict with counts.
    """
    conn = init_db(db_path)
    summary = {
        "date": date_str or "latest",
        "filings_found": 0,
        "actions_stored": 0,
        "errors": [],
    }

    log_id = -1
    try:
        if not date_str:
            date_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        summary["date"] = date_str
        log_id = log_fetch_start(conn, date_str)
        logger.info(f"Fetching 8-K filings for {date_str}...")

        # Fetch filings from RSS feed (has items pre-extracted in summary)
        filings = fetch_filings_rss(date_str)
        summary["filings_found"] = len(filings)

        # If RSS returns nothing (e.g. for older dates), fall back to daily index
        if not filings:
            logger.info(f"RSS feed returned no filings for {date_str}, trying daily index...")
            filings = fetch_filings_by_date(date_str)
            summary["filings_found"] = len(filings)

        if filings:
            ticker_map = _load_ticker_map(conn)
            actions = classify_and_prepare(conn, filings, ticker_map, fetch_items=True)
            if actions:
                count = upsert_corporate_actions(conn, actions)
                summary["actions_stored"] = count
                logger.info(f"Stored {count} corporate actions for {date_str}")
            else:
                logger.info(f"No corporate actions extracted for {date_str}")
        else:
            logger.info(f"No 8-K filings found for {date_str}")

        # Cleanup old records
        cleaned = cleanup_old_records(conn)
        if cleaned:
            logger.info(f"Cleaned up {cleaned} expired records")

        log_fetch_end(conn, log_id, filings_checked=len(filings),
                      new_actions=summary["actions_stored"])

    except Exception as e:
        err = f"Fetch failed: {e}"
        summary["errors"].append(err)
        logger.error(err)
        if log_id >= 0:
            log_fetch_end(conn, log_id, status="error", error=str(e))
    finally:
        conn.close()

    return summary


def fetch_range(start_date: str, end_date: str, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Fetch corporate actions for a date range."""
    results = []
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    d = start
    while d <= end:
        if d.weekday() < 5:  # Skip weekends
            result = fetch_daily(d.strftime("%Y-%m-%d"), db_path)
            results.append(result)
        d += timedelta(days=1)
        time.sleep(SEC_RATE_LIMIT * 5)  # Be extra nice to SEC
    return results


def _load_ticker_map(conn) -> Dict[str, str]:
    """Load CIK→ticker mapping from DuckDB."""
    try:
        rows = conn.execute(
            "SELECT cik, ticker FROM listed_companies WHERE is_active = true"
        ).fetchall()
        return {str(r[0]): str(r[1]) for r in rows}
    except Exception:
        return {}


# ── CLI ──

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    if "--init" in sys.argv:
        result = init()
        print(f"\nInit result: {result}")

    elif "--from" in sys.argv and "--to" in sys.argv:
        from_idx = sys.argv.index("--from")
        to_idx = sys.argv.index("--to")
        start = sys.argv[from_idx + 1]
        end = sys.argv[to_idx + 1]
        results = fetch_range(start, end)
        for r in results:
            print(f"  {r}")

    elif "--date" in sys.argv:
        idx = sys.argv.index("--date")
        date_str = sys.argv[idx + 1]
        result = fetch_daily(date_str)
        print(f"\nResult: {result}")

    else:
        # Default: fetch latest
        result = fetch_daily()
        print(f"\nResult: {result}")
