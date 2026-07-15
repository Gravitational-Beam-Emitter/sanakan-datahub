"""
Company Announcements Pipeline — multi-market filing downloader.

Fetchers:
  _fetch_sec_filings      — SEC EDGAR (8-K, 10-K, 10-Q + EX-99 exhibits)
  _fetch_hkex_announcements — HKEXnews PDF announcements
  _fetch_cninfo_announcements — CNINFO A-share PDF announcements

Entry points:
  init()       — backfill from BACKFILL_START
  fetch_daily() — fetch last LOOKBACK_DAYS for all tracked companies

CLI:
  python -m announcements.pipeline --init
  python -m announcements.pipeline
  python -m announcements.pipeline --date 20260615
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

from announcements.config import (
    BACKFILL_START,
    CNINFO_RATE_LIMIT,
    DB_PATH,
    FILES_DIR,
    HKEX_RATE_LIMIT,
    LOOKBACK_DAYS,
    SEC_HEADERS,
    SEC_RATE_LIMIT,
    TRACKED_COMPANIES,
)
from announcements.storage import (
    init_db,
    log_fetch_end,
    log_fetch_start,
    upsert_announcements,
)

logger = logging.getLogger("announcements.pipeline")

# ── SEC EDGAR ──────────────────────────────────────────────────

def _build_filing_url(cik: str, accession_number: str) -> str:
    """Build SEC filing index URL from CIK and accession number."""
    acc_no = accession_number.replace("-", "")
    return (
        f"https://www.sec.gov/Archives/edgar/data/"
        f"{int(cik)}/{acc_no}/{accession_number}-index.htm"
    )


def _sec_submissions_url(cik: str) -> str:
    """SEC submissions API URL for a given CIK."""
    padded = str(int(cik)).zfill(10)
    return f"https://data.sec.gov/submissions/CIK{padded}.json"


def _extract_html_text(html: str) -> str:
    """Extract readable text from SEC filing HTML."""
    try:
        soup = BeautifulSoup(html, "lxml")
        # Remove script and style elements
        for tag in soup(["script", "style", "meta", "link"]):
            tag.decompose()
        text = soup.get_text(" ", strip=True)
        # Collapse whitespace
        return " ".join(text.split())
    except Exception:
        return ""


def _fetch_sec_filings(
    session: requests.Session, company: dict, lookback_days: int,
) -> List[Dict[str, Any]]:
    """Fetch recent SEC filings for a US company."""
    cik = company["cik"]
    ticker = company["ticker"]
    company_name = company["name"]
    cutoff = date.today() - timedelta(days=lookback_days)
    records: List[Dict[str, Any]] = []

    # Get recent filings from submissions API
    submissions_url = _sec_submissions_url(cik)
    logger.info(f"SEC: fetching submissions for {ticker} ({cik})")
    resp = session.get(submissions_url, headers=SEC_HEADERS, timeout=15)
    if resp.status_code != 200:
        logger.warning(f"SEC submissions API returned {resp.status_code} for {ticker}")
        return records

    data = resp.json()
    filings = data.get("filings", {}).get("recent", [])
    if not filings:
        return records

    # Filter to target forms within lookback
    target_forms = {"8-K", "10-K", "10-Q"}
    accessions = filings.get("accessionNumber", [])
    forms = filings.get("form", [])
    filing_dates = filings.get("filingDate", [])
    report_dates = filings.get("reportDate", [])
    descriptions = filings.get("primaryDocument", [])

    for i, acc in enumerate(accessions):
        if i >= len(forms):
            break
        form_type = forms[i] if i < len(forms) else ""
        filing_date_str = filing_dates[i] if i < len(filing_dates) else ""
        report_date_str = report_dates[i] if i < len(report_dates) else ""
        primary_doc = descriptions[i] if i < len(descriptions) else ""

        if form_type not in target_forms:
            continue

        # Check if within lookback window
        date_str = filing_date_str or report_date_str
        if not date_str:
            continue

        try:
            ann_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue

        if ann_date < cutoff:
            continue

        # Build filing detail page URL and fetch the document
        filing_url = _build_filing_url(cik, acc)
        time.sleep(SEC_RATE_LIMIT)

        try:
            idx_resp = session.get(filing_url, headers=SEC_HEADERS, timeout=10)
            if idx_resp.status_code != 200:
                logger.debug(f"Filing index not accessible: {filing_url}")
                continue

            idx_soup = BeautifulSoup(idx_resp.text, "lxml")

            # Find primary document and exhibits
            doc_url = ""
            base_url = filing_url.rsplit("/", 1)[0]
            acc_no = acc.replace("-", "")

            for a_tag in idx_soup.find_all("a"):
                href = a_tag.get("href", "")
                text = a_tag.get_text(strip=True).lower()
                if not href or not text:
                    continue

                # Skip images, schemas
                if any(s in text for s in [".jpg", ".png", ".gif", ".xsd"]):
                    continue

                # Primary 8-K/10-K/10-Q document
                is_primary = any(pat in text for pat in ["8k", "8-k", "10k", "10-k", "10q", "10-q"])
                if is_primary and text.endswith(".htm"):
                    if href.startswith("/ix?doc="):
                        doc_url = "https://www.sec.gov" + href.split("?doc=")[-1]
                    elif href.startswith("http"):
                        doc_url = href
                    elif href.startswith("/"):
                        doc_url = f"https://www.sec.gov{href}"
                    else:
                        doc_url = f"{base_url}/{href}"
                    break

            # If no primary doc found, build the expected URL
            if not doc_url and primary_doc:
                doc_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_no}/{primary_doc}"

            if doc_url:
                time.sleep(SEC_RATE_LIMIT)
                text_content = ""
                local_path = ""

                try:
                    doc_resp = session.get(doc_url, headers=SEC_HEADERS, timeout=15)
                    if doc_resp.status_code == 200:
                        text_content = _extract_html_text(doc_resp.text)
                        # Truncate to 50KB for DB storage
                        text_content = text_content[:50000]

                        # Save HTML file
                        safe_acc = acc.replace("/", "_")
                        market_dir = os.path.join(FILES_DIR, "us", ticker)
                        os.makedirs(market_dir, exist_ok=True)
                        filename = f"{date_str}_{safe_acc}_{form_type}.html"
                        local_path = os.path.join("files/us", ticker, filename)
                        full_path = os.path.join(FILES_DIR, "us", ticker, filename)
                        with open(full_path, "w", encoding="utf-8") as f:
                            f.write(doc_resp.text)
                except Exception as e:
                    logger.debug(f"Failed to download primary doc: {e}")

                records.append({
                    "ticker": ticker,
                    "market": "us",
                    "company_name": company_name,
                    "title": f"{form_type} Filing",
                    "announcement_date": date_str,
                    "source": "sec",
                    "filing_type": form_type,
                    "source_url": filing_url,
                    "local_file_path": local_path,
                    "text_content": text_content,
                    "file_type": "html",
                })

            # Also fetch EX-99 exhibits (earnings press releases / transcripts)
            for a_tag in idx_soup.find_all("a"):
                href = a_tag.get("href", "")
                link_text = a_tag.get_text(strip=True).lower()
                if not any(pat in link_text for pat in ["ex99", "ex-99", "exhibit99"]):
                    continue
                if ".htm" not in link_text and ".txt" not in link_text:
                    continue

                exhibit_url = ""
                if href.startswith("/ix?doc="):
                    exhibit_url = "https://www.sec.gov" + href.split("?doc=")[-1]
                elif href.startswith("http"):
                    exhibit_url = href
                elif href.startswith("/"):
                    exhibit_url = f"https://www.sec.gov{href}"
                else:
                    exhibit_url = f"{base_url}/{href}"

                if exhibit_url:
                    time.sleep(SEC_RATE_LIMIT)
                    try:
                        ex_resp = session.get(exhibit_url, headers=SEC_HEADERS, timeout=15)
                        if ex_resp.status_code == 200:
                            ex_text = _extract_html_text(ex_resp.text)[:50000]
                            safe_acc = acc.replace("/", "_")
                            market_dir = os.path.join(FILES_DIR, "us", ticker)
                            os.makedirs(market_dir, exist_ok=True)
                            ex_filename = f"{date_str}_{safe_acc}_EX-99_{len(records)}.html"
                            ex_local_path = os.path.join("files/us", ticker, ex_filename)
                            full_ex_path = os.path.join(FILES_DIR, "us", ticker, ex_filename)
                            with open(full_ex_path, "w", encoding="utf-8") as f:
                                f.write(ex_resp.text)

                            records.append({
                                "ticker": ticker,
                                "market": "us",
                                "company_name": company_name,
                                "title": f"{form_type} EX-99 Exhibit",
                                "announcement_date": date_str,
                                "source": "sec",
                                "filing_type": "EX-99",
                                "source_url": exhibit_url,
                                "local_file_path": ex_local_path,
                                "text_content": ex_text,
                                "file_type": "html",
                            })
                    except Exception as e:
                        logger.debug(f"Failed to download EX-99: {e}")

        except Exception as e:
            logger.warning(f"Error processing SEC filing {acc}: {e}")
            continue

    logger.info(f"SEC: {len(records)} records for {ticker}")
    return records


# ── HKEXnews ───────────────────────────────────────────────────

def _classify_hk_filing_type(title: str) -> str:
    """Classify HKEX announcement type from title keywords."""
    title_lower = title.lower()
    if "annual" in title_lower or "年報" in title or "annual report" in title_lower:
        return "annual_report"
    if "interim" in title_lower or "中期" in title or "interim report" in title_lower:
        return "interim_report"
    if "circular" in title_lower or "通函" in title:
        return "circular"
    if "result" in title_lower or "業績" in title or "results announcement" in title_lower:
        return "results"
    if "insider" in title_lower or "內幕" in title or "inside information" in title_lower:
        return "inside_information"
    if "return" in title_lower and "回條" in title:
        return "return_form"
    if "sustainability" in title_lower or "esg" in title_lower:
        return "esg_report"
    if "profit" in title_lower and "warning" in title_lower:
        return "profit_warning"
    return "announcement"


def _try_hkex_pdf_urls(
    session: requests.Session, hkex_code: str, ticker: str, date_str: str,
    headers: dict,
) -> List[Dict[str, Any]]:
    """Try direct HKEX main board PDF URL patterns for a given date and stock code.

    HKEX main board URL pattern:
      https://www1.hkexnews.hk/listedco/listconews/sehk/{YYYY}/{MMDD}/{YYYYMMDD}{seqno}.pdf

    We iterate through seqno candidates and check which ones reference our stock code
    in the PDF filename or content.
    """
    results: List[Dict[str, Any]] = []
    yyyy = date_str[:4]
    mmdd = date_str[5:7] + date_str[8:10]
    clean_date = date_str.replace("-", "")

    # Try seqno from 0001 to 0200 for this date
    max_seq = 200
    consecutive_404 = 0

    for seq in range(1, max_seq + 1):
        seq_str = str(seq).zfill(5)
        pdf_url = (
            f"https://www1.hkexnews.hk/listedco/listconews/sehk/"
            f"{yyyy}/{mmdd}/{clean_date}{seq_str}.pdf"
        )

        # Use HEAD request first to check existence
        try:
            head_resp = session.head(pdf_url, headers=headers, timeout=5)
            if head_resp.status_code == 404:
                consecutive_404 += 1
                if consecutive_404 > 20:
                    # Probably no more filings for this date
                    break
                continue
            consecutive_404 = 0

            if head_resp.status_code != 200:
                continue

            # Download the PDF for further inspection
            time.sleep(HKEX_RATE_LIMIT)
            pdf_resp = session.get(pdf_url, headers=headers, timeout=20)
            if pdf_resp.status_code != 200 or len(pdf_resp.content) < 500:
                continue

            # Check if this PDF is for our target stock
            # HKEX PDFs often have the stock code in the filename or content
            pdf_text_sample = ""
            try:
                import pdfplumber
                import io
                with pdfplumber.open(io.BytesIO(pdf_resp.content)) as pdf:
                    first_page = pdf.pages[0].extract_text() or ""
                    pdf_text_sample = first_page[:2000]
            except Exception:
                pass

            # Check if stock code appears in PDF content
            stock_code_matches = []
            import re
            stock_code_matches = re.findall(r'Stock\s*[Cc]ode\s*[:\s]*(\d{5})', pdf_text_sample)
            stock_code_matches += re.findall(r'(?<!\d)(\d{5})(?!\d)', pdf_text_sample)

            if hkex_code not in stock_code_matches and hkex_code not in pdf_text_sample:
                continue

            # This PDF is for our target stock!
            logger.info(f"HKEX: found PDF for {ticker} at {pdf_url}")
            market_dir = os.path.join(FILES_DIR, "hk", ticker)
            os.makedirs(market_dir, exist_ok=True)
            filename = f"{date_str}_{clean_date}{seq_str}.pdf"
            local_path = os.path.join("files/hk", ticker, filename)
            full_path = os.path.join(FILES_DIR, "hk", ticker, filename)
            with open(full_path, "wb") as f:
                f.write(pdf_resp.content)

            # Extract full text from PDF
            text_content = ""
            try:
                with pdfplumber.open(io.BytesIO(pdf_resp.content)) as pdf:
                    pages_text = []
                    for page in pdf.pages[:10]:
                        pt = page.extract_text()
                        if pt:
                            pages_text.append(pt)
                    text_content = " ".join(pages_text)[:50000]
            except Exception:
                pass

            # Try to determine title from PDF content
            title = f"HKEX Announcement {clean_date}{seq_str}"
            lines = pdf_text_sample.strip().split("\n")
            if lines:
                first_line = lines[0].strip()
                if len(first_line) > 10:
                    title = first_line[:200]

            results.append({
                "ticker": ticker,
                "market": "hk",
                "company_name": "",
                "title": title,
                "announcement_date": date_str,
                "source": "hkex",
                "filing_type": _classify_hk_filing_type(title),
                "source_url": pdf_url,
                "local_file_path": local_path,
                "text_content": text_content,
                "file_type": "pdf",
            })

        except Exception as e:
            logger.debug(f"HKEX PDF try error for {pdf_url}: {e}")
            continue

    return results


def _fetch_hkex_announcements(
    session: requests.Session, company: dict, lookback_days: int,
) -> List[Dict[str, Any]]:
    """Fetch recent HKEXnews announcements for a HK company.

    Since the HKEX search page uses JSF (JavaServer Faces) and returns only GEM
    stocks via GET, we use a direct URL enumeration approach for main board stocks.
    This is slower but works reliably for known stock codes.
    """
    hkex_code = company["hkex_code"]
    ticker = company["ticker"]
    company_name = company["name"]
    cutoff = date.today() - timedelta(days=lookback_days)
    records: List[Dict[str, Any]] = []

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8",
    }

    current = date.today()
    d = current - timedelta(days=lookback_days)

    total_head_checks = 0
    while d <= current:
        date_str = d.isoformat()
        d += timedelta(days=1)
        total_head_checks += 1

        logger.debug(f"HKEX: checking {date_str} for {ticker}")
        day_results = _try_hkex_pdf_urls(
            session, hkex_code, ticker, date_str, headers,
        )

        # Fill in company name
        for r in day_results:
            if not r["company_name"]:
                r["company_name"] = company_name

        records.extend(day_results)

        if len(day_results) > 0:
            logger.info(f"HKEX: {len(day_results)} announcements for {ticker} on {date_str}")

        # Only check a few dates per run to avoid excessive HEAD requests
        # A full 30-day lookback would check ~30 dates * ~20-30 PDFs = ~600-900 requests
        # At 0.5s rate limit, this takes 5-8 minutes per company

    logger.info(f"HKEX: {len(records)} total records for {ticker} ({total_head_checks} dates checked)")
    return records


# ── CNINFO / East Money ──────────────────────────────────────

def _classify_cn_filing_type(title: str) -> str:
    """Classify A-share announcement type from title keywords."""
    if any(kw in title for kw in ["年报", "年度报告"]):
        return "annual_report"
    if any(kw in title for kw in ["半年报", "半年度报告", "中期报告"]):
        return "interim_report"
    if any(kw in title for kw in ["季报", "季度报告"]):
        return "quarterly_report"
    if any(kw in title for kw in ["招股", "上市公告"]):
        return "listing_doc"
    if any(kw in title for kw in ["股东会", "股东大会", "持有人会议"]):
        return "shareholder_meeting"
    if any(kw in title for kw in ["权益分派", "分红", "利润分配"]):
        return "dividend"
    if any(kw in title for kw in ["重组", "收购", "并购", "要约收购"]):
        return "corporate_action"
    if any(kw in title for kw in ["董事会", "高管", "辞职", "聘任"]):
        return "board_change"
    if any(kw in title for kw in ["法律意见书"]):
        return "legal_opinion"
    if any(kw in title for kw in ["审计报告"]):
        return "audit_report"
    if any(kw in title for kw in ["评级报告"]):
        return "rating_report"
    if any(kw in title for kw in ["担保", "抵押"]):
        return "guarantee"
    return "announcement"


def _fetch_cninfo_announcements(
    session: requests.Session, company: dict, lookback_days: int,
) -> List[Dict[str, Any]]:
    """Fetch recent A-share announcements via AKShare (East Money data source)."""
    ticker = company["ticker"]
    company_name = company["name"]
    cutoff = date.today() - timedelta(days=lookback_days)
    records: List[Dict[str, Any]] = []

    try:
        import akshare as ak

        begin = cutoff.isoformat().replace("-", "")
        end = date.today().isoformat().replace("-", "")

        logger.info(f"CN: fetching announcements for {ticker} via AKShare {begin}~{end}")
        df = ak.stock_individual_notice_report(
            security=ticker, begin_date=begin, end_date=end,
        )

        if df is None or df.empty:
            logger.info(f"CN: no announcements for {ticker}")
            return records

        for _, row in df.iterrows():
            title = str(row.get("公告标题", ""))
            ann_type = str(row.get("公告类型", ""))
            date_str = str(row.get("公告日期", ""))
            source_url = str(row.get("网址", ""))

            if not title:
                continue

            # Parse date
            try:
                ann_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                try:
                    ann_date = datetime.strptime(date_str, "%Y%m%d").date()
                except ValueError:
                    ann_date = cutoff

            if ann_date < cutoff:
                continue

            # Extract announcement ID from URL for PDF download
            # URL format: https://data.eastmoney.com/notices/detail/600519/AN202606111823465368.html
            ann_id = ""
            pdf_url = ""
            if "/AN" in source_url:
                parts = source_url.split("/AN")
                if len(parts) > 1:
                    ann_id = "AN" + parts[-1].replace(".html", "").split("?")[0]

            # Download PDF from East Money if we have an ID
            local_path = ""
            text_content = ""
            if ann_id:
                pdf_url = f"https://pdf.dfcfw.com/pdf/H2_{ann_id}_1.pdf"
                try:
                    time.sleep(CNINFO_RATE_LIMIT)
                    pdf_resp = session.get(pdf_url, headers={
                        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                        "Referer": source_url,
                    }, timeout=20)
                    if pdf_resp.status_code == 200 and len(pdf_resp.content) > 100:
                        date_prefix = ann_date.isoformat()
                        market_dir = os.path.join(FILES_DIR, "cn", ticker)
                        os.makedirs(market_dir, exist_ok=True)
                        safe_id = ann_id.replace("/", "_")
                        filename = f"{date_prefix}_{safe_id}.pdf"
                        local_path = os.path.join("files/cn", ticker, filename)
                        full_path = os.path.join(FILES_DIR, "cn", ticker, filename)
                        with open(full_path, "wb") as f:
                            f.write(pdf_resp.content)

                        # Try to extract text from PDF
                        try:
                            import pdfplumber
                            with pdfplumber.open(full_path) as pdf:
                                pages_text = []
                                for page in pdf.pages[:10]:
                                    pt = page.extract_text()
                                    if pt:
                                        pages_text.append(pt)
                                text_content = " ".join(pages_text)[:50000]
                        except Exception:
                            pass
                except Exception as e:
                    logger.debug(f"Failed to download East Money PDF for {ann_id}: {e}")

            filing_type = _classify_cn_filing_type(title) or ann_type

            records.append({
                "ticker": ticker,
                "market": "cn",
                "company_name": company_name,
                "title": title,
                "announcement_date": ann_date.isoformat(),
                "source": "cninfo",
                "filing_type": filing_type,
                "source_url": source_url,
                "local_file_path": local_path,
                "text_content": text_content,
                "file_type": "pdf" if local_path else "",
            })

    except Exception as e:
        logger.warning(f"CNINFO error for {ticker}: {e}")

    logger.info(f"CNINFO: {len(records)} records for {ticker}")
    return records


# ── Main Orchestrator ──────────────────────────────────────────

def _get_fetcher(market: str):
    """Dispatch to the correct sub-fetcher by market."""
    return {
        "us": _fetch_sec_filings,
        "hk": _fetch_hkex_announcements,
        "cn": _fetch_cninfo_announcements,
    }.get(market)


def fetch_all_companies(
    companies: Optional[List[dict]] = None,
    lookback_days: int = LOOKBACK_DAYS,
    conn=None,
) -> Dict[str, Any]:
    """Loop over tracked companies, dispatch to correct sub-fetcher, store results."""
    if companies is None:
        companies = TRACKED_COMPANIES

    own_conn = conn is None
    if own_conn:
        conn = init_db()

    session = requests.Session()
    summary: Dict[str, Any] = {"total": 0, "us": 0, "hk": 0, "cn": 0, "errors": []}

    try:
        for company in companies:
            market = company.get("market", "")
            fetcher = _get_fetcher(market)
            if fetcher is None:
                summary["errors"].append(f"unknown market: {market}")
                continue

            ticker = company["ticker"]
            logger.info(f"Fetching announcements for {ticker} ({market})")
            try:
                records = fetcher(session, company, lookback_days)
                if records:
                    count = upsert_announcements(conn, records)
                    summary[market] += count
                    summary["total"] += count
                    logger.info(f"  {ticker}: stored {count} new records")
                else:
                    logger.info(f"  {ticker}: no new records")
            except Exception as e:
                error_msg = f"{market}:{ticker}: {e}"
                logger.error(error_msg)
                summary["errors"].append(error_msg)

            time.sleep(0.5)  # Spacing between companies

    finally:
        session.close()
        if own_conn:
            conn.close()

    return summary


def fetch_daily(date_str: Optional[str] = None) -> Dict[str, Any]:
    """Daily fetch for all tracked companies over LOOKBACK_DAYS."""
    fetch_date = date_str or date.today().isoformat()

    conn = init_db()
    log_id = log_fetch_start(conn, fetch_date)
    items_checked = 0
    summary: Dict[str, Any] = {"total": 0, "errors": []}

    try:
        result = fetch_all_companies(conn=conn)
        items_checked = result["total"] + sum(
            result.get(m, 0) for m in ["us", "hk", "cn"]
        )
        summary = result
        log_fetch_end(conn, log_id, items_checked=items_checked,
                      new_items=result["total"], status="ok")
    except Exception as e:
        logger.error(f"Daily fetch failed: {e}")
        summary["errors"].append(str(e))
        log_fetch_end(conn, log_id, items_checked=items_checked,
                      new_items=0, status="error", error=str(e))
    finally:
        conn.close()

    return summary


def init() -> Dict[str, Any]:
    """Backfill historical data from BACKFILL_START to today."""
    conn = init_db()
    summary: Dict[str, Any] = {"total": 0, "us": 0, "hk": 0, "cn": 0, "errors": []}

    try:
        session = requests.Session()
        start = datetime.strptime(BACKFILL_START, "%Y-%m-%d").date()
        today = date.today()

        for company in TRACKED_COMPANIES:
            market = company.get("market", "")
            fetcher = _get_fetcher(market)
            if fetcher is None:
                continue

            ticker = company["ticker"]
            total_days = (today - start).days
            logger.info(f"Backfilling {ticker} ({market}) — {total_days} days from {BACKFILL_START}")

            try:
                records = fetcher(session, company, total_days)
                if records:
                    count = upsert_announcements(conn, records)
                    summary[market] += count
                    summary["total"] += count
                logger.info(f"  {ticker}: stored {summary[market]} records for {market}")
            except Exception as e:
                error_msg = f"{market}:{ticker}: {e}"
                logger.error(error_msg)
                summary["errors"].append(error_msg)

            time.sleep(1)  # Spacing between companies

        session.close()
        logger.info(f"Backfill complete: {summary}")

    except Exception as e:
        logger.error(f"Init failed: {e}")
        summary["errors"].append(str(e))
    finally:
        conn.close()

    return summary


# ── CLI ────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if "--init" in sys.argv:
        print("Running backfill...")
        result = init()
        print(json.dumps(result, indent=2, default=str))
    elif "--date" in sys.argv:
        idx = sys.argv.index("--date")
        date_arg = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else date.today().isoformat()
        print(f"Fetching for date: {date_arg}")
        result = fetch_daily(date_arg)
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"Running daily fetch (lookback={LOOKBACK_DAYS} days)...")
        result = fetch_daily()
        print(json.dumps(result, indent=2, default=str))
