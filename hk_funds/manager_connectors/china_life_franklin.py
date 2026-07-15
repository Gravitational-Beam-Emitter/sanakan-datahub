"""
China Life Franklin Asset Management connector.

Extracts fund data from clamc.com.hk via Playwright DOM scraping
and PyPDF2 PDF parsing for ISINs.

Strategy:
  1. Visit the product listing page to discover fund detail pages (pid=1,4,5,6,7)
  2. Visit each fund detail page to extract:
     - Fund name, share class names from <select> dropdown
     - NAV history from HTML table (paginated, up to ~60 days)
  3. Visit the Fact Sheet page to find and download monthly Fact Sheet PDFs
  4. Parse Fact Sheet PDFs with PyPDF2 to extract ISINs, Bloomberg tickers
  5. Match ISINs to share class names by keyword matching
  6. Match to hk_funds by ISIN first, then by name with CLF constraint

CE: ANL846 — China Life Franklin Asset Management Co., Limited
"""

from __future__ import annotations

import logging
import re
import tempfile
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

from hk_funds.manager_connectors.base import BaseManagerConnector, register_connector

logger = logging.getLogger("hk_funds.manager_connectors.china_life_franklin")

BASE_URL = "https://www.clamc.com.hk"
PRODUCT_URL = f"{BASE_URL}/index.php?a=Ehome&f=product"
FACT_SHEET_URL = f"{BASE_URL}/index.php?a=Ehome&f=service&cid=3&id=378"
KFS_URL = f"{BASE_URL}/index.php?a=Ehome&f=service&cid=3&id=223"

# Known fund PIDs on the website
FUND_PIDS = [1, 4, 5, 6, 7]

# Fund name mapping from website names to DB-friendly names
FUND_NAME_MAP = {
    "Diversified Income Public Offering Fund":
        "China Life Franklin Diversified Income Fund",
    "Global Fund – Short Term Bond Fund":
        "China Life Franklin Global Fund - Short Term Bond Fund",
    "Global Fund - Short Term Bond Fund":
        "China Life Franklin Global Fund - Short Term Bond Fund",
    "Global Fund - Global Growth Fund":
        "China Life Franklin Global Fund - Global Growth Fund",
    "Global Fund - Select High Yield Fund":
        "China Life Franklin Global Fund - Select High Yield Bond Fund",
    "China Life Franklin OFC - USD Money Market Fund":
        "China Life Franklin OFC - USD Money Market Fund",
}


@register_connector
class ChinaLifeFranklinConnector(BaseManagerConnector):
    """Extracts fund data from China Life Franklin Asset Management website."""

    manager_ce_numbers = ["ANL846"]
    base_url = BASE_URL

    request_delay: float = 1.0
    request_timeout: int = 30

    _CLF_MANAGER_SQL = (
        "AND (LOWER(fund_manager_name_en) LIKE '%china life franklin%'"
        " OR LOWER(fund_manager_name_en) LIKE '%china life%')"
    )

    def __init__(self):
        super().__init__()
        self._playwright = None
        self._browser = None

    # ── Playwright helpers ─────────────────────────────────────

    def _get_browser(self):
        if self._browser is None:
            from playwright.sync_api import sync_playwright
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=True)
        return self._browser

    def _close_browser(self):
        if self._browser:
            self._browser.close()
            self._browser = None
        if self._playwright:
            self._playwright.stop()
            self._playwright = None

    # ── PDF Parsing ────────────────────────────────────────────

    def _download_pdf(self, url: str) -> Optional[str]:
        """Download a PDF and return its text content extracted via PyPDF2."""
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
        except Exception as e:
            logger.warning(f"CLF: failed to download PDF {url}: {e}")
            return None

        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as f:
            f.write(resp.content)
            tmp_path = f.name

        try:
            from PyPDF2 import PdfReader
            reader = PdfReader(tmp_path)
            full_text = ""
            for page in reader.pages:
                t = page.extract_text()
                if t:
                    full_text += t + "\n"
            return full_text
        except Exception as e:
            logger.warning(f"CLF: failed to parse PDF {url}: {e}")
            return None
        finally:
            import os
            os.unlink(tmp_path)

    def _parse_factsheet_isins(self, pdf_text: str) -> List[Dict[str, str]]:
        """Parse ISINs, Bloomberg tickers, and share class info from a Fact Sheet PDF.

        The FFS has a "Fund Class Information" table like:
            Class  NAV per Unit  Minimum Initial Subscription  ISIN Code  Bloomberg Code
            Class A - USD  12.1266  100  HK0000664422  CLSTFAU HK
            Class A - HKD  12.1585  1,000  HK0000664430  CLSTFAH HK
        """
        share_classes = []
        seen_isins = set()

        # Find the Fund Class Information section
        # Pattern: share class name, then optional fields, then ISIN, then optional bloomberg
        # Lines look like: "Class A - USD  12.1266  100 HK0000664422  CLSTFAU HK"
        # or: "Class I - HKD  - 5,000,000  HK0000664497  -"
        for line in pdf_text.split('\n'):
            line = line.strip()
            if not line or not re.match(r'Class\s+', line):
                continue

            # Extract ISIN
            isin_match = re.search(r'(HK\d{10})', line)
            if not isin_match:
                continue
            isin = isin_match.group(1)
            if isin in seen_isins:
                continue
            seen_isins.add(isin)

            # Extract share class name
            # "Class A - USD", "Class A2 - HKD", "Class I - HKD", etc.
            sc_match = re.match(
                r'(Class\s+[A-Z]+\d?\s*[-–]\s*(?:USD|HKD|RMB|CNY))', line
            )
            if not sc_match:
                # Try simpler: "Class X - XXX"
                sc_match = re.match(r'(Class\s+\S+\s*[-–]\s*\S+)', line)
            sc_name = sc_match.group(1).strip() if sc_match else ""

            # Normalize
            sc_name = re.sub(r'\s+', ' ', sc_name)
            sc_name = sc_name.replace('–', '-')
            sc_name = sc_name.replace('RMB', 'CNH')

            # Extract currency
            currency = "USD"
            if "HKD" in sc_name:
                currency = "HKD"
            elif "RMB" in sc_name or "CNY" in sc_name:
                currency = "CNH"

            # Extract Bloomberg ticker
            bbg_match = re.search(
                r'(?:HK\d{10})\s+([A-Z0-9]+\s+(?:HK|US|LN|SW)\b)', line
            )
            bloomberg = bbg_match.group(1).strip() if bbg_match else ""

            # Extract NAV from the line
            # NAV is typically the first number after the share class name
            nav_match = re.search(
                r'Class\s+\S+\s*[-–]\s*\S+\s+([\d,]+\.\d+)', line
            )
            nav = None
            if nav_match:
                try:
                    nav = float(nav_match.group(1).replace(',', ''))
                except ValueError:
                    pass

            share_classes.append({
                "share_class_name": sc_name,
                "isin": isin,
                "currency": currency,
                "bloomberg_ticker": bloomberg,
                "nav": nav,
            })

        return share_classes

    # ── Fund Detail Page Parsing ────────────────────────────────

    def _parse_fund_detail_page(self, page, pid: int) -> Optional[Dict[str, Any]]:
        """Parse a fund detail page for fund name, share classes, and NAV history."""
        url = f"{BASE_URL}/index.php?a=Ehome&f=product&pid={pid}"
        try:
            page.goto(url, wait_until="load", timeout=30000)
            time.sleep(2)
        except Exception:
            logger.warning(f"CLF: failed to load pid={pid}")
            return None

        body = page.inner_text("body")

        # Extract fund name from breadcrumb
        fund_name = ""
        name_match = re.search(r'> Products & Services > ([^\n]+)', body)
        if name_match:
            fund_name = name_match.group(1).strip()

        if not fund_name:
            return None

        # Map to DB-friendly name
        db_name = FUND_NAME_MAP.get(fund_name, fund_name)
        logger.info(f"CLF: pid={pid} -> {fund_name[:60]}")

        # Extract share class names from <select> dropdown
        share_class_names = page.evaluate("""() => {
            const select = document.querySelector('select');
            if (!select) return [];
            return Array.from(select.querySelectorAll('option'))
                .map(o => (o.textContent || '').trim())
                .filter(t => t && !t.match(/^(USD|HKD|RMB|CNY)$/));
        }""")

        # Collect NAVs from all paginated pages
        nav_entries = []
        seen_navs = set()
        max_pages = 10  # Safety limit

        for page_num in range(1, max_pages + 1):
            if page_num > 1:
                page_url = f"{url}&p={page_num}"
                try:
                    page.goto(page_url, wait_until="load", timeout=30000)
                    time.sleep(1)
                except Exception:
                    break

            body_text = page.inner_text("body")
            page_navs = re.findall(
                r'(\d{4}-\d{2}-\d{2})\s+(.+?)\s+\$\s*([\d,]+\.\d+)',
                body_text
            )

            if not page_navs:
                break

            for nav_date, sc_name, nav_str in page_navs:
                nav_key = f"{nav_date}|{sc_name}"
                if nav_key in seen_navs:
                    continue
                seen_navs.add(nav_key)

                try:
                    nav_val = float(nav_str.replace(',', ''))
                except ValueError:
                    continue

                # Determine currency from share class name
                currency = "USD"
                sc_upper = sc_name.upper()
                if "HKD" in sc_upper:
                    currency = "HKD"
                elif "RMB" in sc_upper or "CNY" in sc_upper:
                    currency = "CNH"

                # Normalize share class name
                sc_normalized = sc_name.strip()
                sc_normalized = re.sub(r'\s+', ' ', sc_normalized)

                nav_entries.append({
                    "nav": nav_val,
                    "nav_date": nav_date,
                    "nav_currency": currency,
                    "share_class_name": sc_normalized,
                    "source": "clamc_website",
                })

        # Build share class list from HTML dropdown
        share_classes = []
        for sc_name in share_class_names:
            sc_normalized = re.sub(r'\s+', ' ', sc_name.strip())
            sc_normalized = sc_normalized.replace('–', '-')

            currency = "USD"
            sc_upper = sc_normalized.upper()
            if "HKD" in sc_upper:
                currency = "HKD"
            elif "RMB" in sc_upper or "CNY" in sc_upper:
                currency = "CNH"

            share_classes.append({
                "share_class_name": sc_normalized,
                "isin": "",
                "currency": currency,
            })

        # Build result
        result = {
            "fund_name": db_name,
            "product_url": url,
            "source_type": "manager_website",
            "isins": [],
            "share_classes": share_classes,
            "nav_entries": nav_entries,
            "base_currency": "USD",
            "fund_manager_name_en": "China Life Franklin Asset Management Co., Limited",
        }

        return result

    # ── Fund Discovery ─────────────────────────────────────────

    def _discover_factsheet_pdfs(self, page) -> Dict[str, str]:
        """Discover Fact Sheet PDF URLs from the Fact Sheet page.

        Returns a dict mapping fund name keywords to PDF URLs.
        The Fact Sheet page has links like:
          CLF Short Term Bond Fund FFS Apr 2026.pdf
          CLF USD MMF Factsheet April 2026.pdf
        """
        page.goto(FACT_SHEET_URL, wait_until="load", timeout=30000)
        time.sleep(2)

        pdf_links = page.evaluate("""() => {
            return Array.from(document.querySelectorAll('a[href*=".pdf"]')).map(a => ({
                href: a.href,
                text: (a.textContent || '').trim()
            }));
        }""")

        result = {}
        for pl in pdf_links:
            result[pl["text"]] = pl["href"]
            logger.info(f"CLF Fact Sheet: {pl['text'][:60]} -> {pl['href'][:80]}")

        return result

    def _match_factsheet_to_fund(
        self, fund_name: str, pdf_map: Dict[str, str]
    ) -> Optional[str]:
        """Match a fund name to its Fact Sheet PDF URL by keyword."""
        name_lower = fund_name.lower()

        keyword_map = {
            "short term bond": ["short term bond", "stbf"],
            "global growth": ["global growth", "ggf"],
            "select high yield": ["select high yield", "high yield bond", "shybf"],
            "usd money market": ["usd money market", "usd mmf", "money market fund"],
            "diversified income": ["diversified income", "dif"],
        }

        keywords = []
        for key, kws in keyword_map.items():
            if key in name_lower:
                keywords = kws
                break

        for pdf_name, pdf_url in pdf_map.items():
            pdf_lower = pdf_name.lower()
            for kw in keywords:
                if kw in pdf_lower:
                    return pdf_url

        return None

    # ── Name Matching ──────────────────────────────────────────

    def _match_fund_name(self, conn, extracted_name: str) -> Optional[int]:
        """Match China Life Franklin fund name to hk_funds.id.

        The DB may store short names like "Global Growth Fund" while the
        connector generates full names like "China Life Franklin Global
        Fund - Global Growth Fund". We try both forms.
        """
        if not extracted_name:
            return None

        name = extracted_name.strip()
        candidates = [name]

        # Try without various prefixes (for DB short names)
        for prefix in [
            "China Life Franklin Global Fund - ",
            "China Life Franklin OFC - ",
            "China Life Franklin ",
        ]:
            if name.lower().startswith(prefix.lower()):
                stripped = name[len(prefix):]
                if stripped not in candidates:
                    candidates.append(stripped)

        # Also build "China Life Franklin <short>" variants
        # e.g., "Global Growth Fund" -> "China Life Franklin Global Growth Fund"
        short_name = candidates[-1]  # The shortest name after stripping
        if short_name != name:
            candidates.append(f"China Life Franklin {short_name}")

        for candidate in candidates:
            c = re.sub(r"\s+", " ", candidate.lower().strip())
            word_count = len(c.split())

            for query, params in [
                ("LOWER(fund_name_en) = ?", [c]),
                ("LOWER(fund_name_en) LIKE ?", [f"%{c}%"]),
            ]:
                if "LIKE" in query and word_count < 2 and len(c) < 10:
                    continue
                row = conn.execute(
                    f"""SELECT id, fund_name_en FROM hk_funds
                       WHERE {query} AND is_active = true
                       {self._CLF_MANAGER_SQL}
                       LIMIT 1""",
                    params,
                ).fetchone()
                if row:
                    return row[0]

            # Keyword matching
            keywords = [
                w for w in c.split()
                if len(w) > 2
                and w not in (
                    "fund", "class", "etf", "china", "life", "franklin",
                    "global", "bond", "growth", "yield", "money", "market",
                    "select", "term", "short", "income",
                )
            ]
            if len(keywords) >= 1:
                conditions = " AND ".join(
                    ["LOWER(fund_name_en) LIKE ?" for _ in keywords]
                )
                params_kw = [f"%{kw}%" for kw in keywords]
                row = conn.execute(
                    f"""SELECT id, fund_name_en FROM hk_funds
                       WHERE {conditions} AND is_active = true
                       {self._CLF_MANAGER_SQL}
                       LIMIT 1""",
                    params_kw,
                ).fetchone()
                if row:
                    return row[0]

        return None

    # ── Main Interface ─────────────────────────────────────────

    def get_fund_list(self) -> List[Dict[str, Any]]:
        """Discover and parse all China Life Franklin fund pages."""
        browser = self._get_browser()
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            locale="en-HK",
        )
        page = context.new_page()

        try:
            # First, discover Fact Sheet PDFs
            pdf_map = self._discover_factsheet_pdfs(page)
            logger.info(f"CLF: discovered {len(pdf_map)} Fact Sheet PDFs")

            funds = []
            for pid in FUND_PIDS:
                detail = self._parse_fund_detail_page(page, pid)
                if not detail:
                    continue

                fund_name = detail.get("fund_name", "")

                # Find and parse the matching Fact Sheet PDF
                pdf_url = self._match_factsheet_to_fund(fund_name, pdf_map)
                if pdf_url:
                    logger.info(f"CLF: matched Fact Sheet PDF for {fund_name[:50]}")
                    pdf_text = self._download_pdf(pdf_url)
                    if pdf_text:
                        fs_share_classes = self._parse_factsheet_isins(pdf_text)
                        logger.info(
                            f"CLF: extracted {len(fs_share_classes)} ISINs from PDF"
                        )

                        # Merge ISIN data into share classes from HTML
                        html_scs = detail.get("share_classes", [])
                        for fs_sc in fs_share_classes:
                            fs_name = fs_sc.get("share_class_name", "").lower()
                            fs_isin = fs_sc.get("isin", "")

                            # Try to match to HTML share class
                            matched = False
                            for html_sc in html_scs:
                                html_name = html_sc.get(
                                    "share_class_name", ""
                                ).lower()
                                # Match by class letter + currency
                                if self._sc_names_match(fs_name, html_name):
                                    html_sc["isin"] = fs_isin
                                    html_sc["bloomberg_ticker"] = fs_sc.get(
                                        "bloomberg_ticker", ""
                                    )
                                    if fs_sc.get("nav"):
                                        html_sc["nav"] = fs_sc["nav"]
                                    matched = True
                                    break

                            if not matched:
                                # Add as new share class
                                html_scs.append(fs_sc)

                        detail["isins"] = [
                            sc["isin"] for sc in html_scs if sc.get("isin")
                        ]

                isins = detail.get("isins", [])
                scs = detail.get("share_classes", [])
                navs = detail.get("nav_entries", [])

                logger.info(
                    f"CLF: {fund_name[:50]} — "
                    f"{len(isins)} ISINs, {len(scs)} share classes, {len(navs)} NAVs"
                )
                funds.append(detail)

            return funds

        finally:
            context.close()

    def _sc_names_match(self, fs_name: str, html_name: str) -> bool:
        """Check if a Fact Sheet share class name matches an HTML dropdown name."""
        # Extract class letter and currency from each
        def extract_key(name):
            name = name.lower().replace('–', '-')
            # Class A, Class A2, Class B, Class C, Class I, Class P
            class_match = re.search(
                r'class\s+([a-z]\d?)\s*[-–]\s*(usd|hkd|rmb|cny)', name
            )
            if class_match:
                return f"{class_match.group(1)}-{class_match.group(2)}"
            # HKD Class, USD Class 1, USD Class 2
            class_match2 = re.search(
                r'(usd|hkd|rmb|cny)\s+class\s*(\d?)', name
            )
            if class_match2:
                return f"{class_match2.group(1)}-{class_match2.group(2)}"
            return name.strip()

        fs_key = extract_key(fs_name)
        html_key = extract_key(html_name)

        if fs_key == html_key:
            return True

        # Fuzzy: check if significant parts overlap
        fs_parts = set(fs_key.replace('-', ' ').split())
        html_parts = set(html_key.replace('-', ' ').split())
        common = fs_parts & html_parts
        return len(common) >= 2

    def get_fund_details(self, isin: str) -> Optional[Dict[str, Any]]:
        """Not implemented — data extracted in get_fund_list."""
        return None

    def scrape_and_store(self, conn, date_str: str = None) -> Dict[str, int]:
        from hk_funds.storage import (
            update_fund_from_manager,
            upsert_nav_history,
            upsert_share_classes,
        )

        today = date_str or datetime.now().strftime("%Y-%m-%d")
        stats = {
            "funds_found": 0, "matched": 0, "isins_updated": 0,
            "navs_stored": 0, "share_classes_stored": 0,
            "details_updated": 0, "errors": 0,
        }

        try:
            fund_details = self.get_fund_list()
            stats["funds_found"] = len(fund_details)

            for idx, detail in enumerate(fund_details):
                fund_name = detail.get("fund_name", "")
                isins = detail.get("isins", [])
                share_classes = detail.get("share_classes", [])
                nav_entries = detail.get("nav_entries", [])
                base_ccy = detail.get("base_currency", "USD")

                if not fund_name and not isins:
                    stats["errors"] += 1
                    continue

                # Match by ISIN first
                hk_fund_id = None
                for isin in isins:
                    row = conn.execute(
                        "SELECT id FROM hk_funds WHERE isin = ?", [isin],
                    ).fetchone()
                    if row:
                        hk_fund_id = row[0]
                        break

                if not hk_fund_id:
                    for isin in isins:
                        row = conn.execute(
                            "SELECT fund_id FROM hk_fund_share_classes WHERE isin = ?",
                            [isin],
                        ).fetchone()
                        if row:
                            hk_fund_id = row[0]
                            break

                if not hk_fund_id:
                    hk_fund_id = self._match_fund_name(conn, fund_name)

                if not hk_fund_id:
                    logger.info(
                        f"  [{idx + 1}/{len(fund_details)}] "
                        f"no match: {fund_name[:60]}"
                    )
                    continue

                stats["matched"] += 1

                # Store fund details
                data = {
                    "fund_name": fund_name,
                    "product_url": detail.get("product_url", ""),
                    "source_type": "manager_website",
                    "fund_manager_name_en": detail.get(
                        "fund_manager_name_en", ""
                    ),
                }
                for key in ("currency", "base_currency", "fund_inception_date",
                           "management_fee_pct", "domicile", "trustee_custodian"):
                    if detail.get(key):
                        data[key] = detail[key]

                if update_fund_from_manager(conn, hk_fund_id, data):
                    stats["details_updated"] += 1

                # Store ISINs and share classes
                for sc in share_classes:
                    sc_isin = sc.get("isin", "")
                    sc_data = {
                        "share_class_name": sc.get("share_class_name", ""),
                        "isin": sc_isin,
                        "currency": self._parse_currency(
                            sc.get("currency", base_ccy)
                        ),
                        "bloomberg_ticker": sc.get("bloomberg_ticker", ""),
                        "source": "clamc_website",
                    }
                    upsert_share_classes(conn, hk_fund_id, [sc_data])
                    stats["share_classes_stored"] += 1

                    if sc_isin:
                        update_fund_from_manager(
                            conn, hk_fund_id, {"isin": sc_isin}
                        )
                        stats["isins_updated"] += 1

                # Store NAVs
                if nav_entries:
                    # Deduplicate and format NAVs
                    navs_to_store = []
                    seen = set()
                    for nav in nav_entries:
                        nav_key = f"{nav['nav_date']}|{nav.get('nav_currency', base_ccy)}"
                        if nav_key in seen:
                            continue
                        seen.add(nav_key)
                        navs_to_store.append({
                            "nav": nav["nav"],
                            "nav_date": nav.get("nav_date", today),
                            "nav_currency": nav.get("nav_currency", base_ccy),
                            "source": "clamc_website",
                        })
                    if navs_to_store:
                        n = upsert_nav_history(conn, hk_fund_id, navs_to_store)
                        stats["navs_stored"] += n

            logger.info(
                f"CLF scrape complete: "
                f"Matched={stats['matched']}/{stats['funds_found']}, "
                f"ISINs={stats['isins_updated']}, "
                f"NAVs={stats['navs_stored']}, "
                f"ShareClasses={stats['share_classes_stored']}, "
                f"Details={stats['details_updated']}"
            )

        except Exception as e:
            logger.error(f"CLF scrape failed: {e}")
            stats["errors"] += 1
        finally:
            self._close_browser()

        return stats
