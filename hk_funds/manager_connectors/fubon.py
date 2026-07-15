"""
Fubon Fund Management (Hong Kong) connector.

Extracts fund data from fubonfund.com.hk via Playwright DOM scraping.
The site is a static PHP website with server-side rendered HTML.

Strategy:
  1. Accept the cookie disclaimer
  2. Visit retail fund listing pages (pkey=2 retail, pkey=3 ETF)
  3. Extract fund names, NAVs, currencies from listing tables
  4. Visit each fund's detail page (overview) to extract ISIN codes
  5. Match to hk_funds by ISIN first, then by name with Fubon constraint

CE: AAA662 — Fubon Fund Management (Hong Kong) Limited
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from hk_funds.manager_connectors.base import BaseManagerConnector, register_connector

logger = logging.getLogger("hk_funds.manager_connectors.fubon")

BASE_URL = "https://www.fubonfund.com.hk/eng"
LISTING_PAGES = [
    (2, "retail"),   # Retail Funds (Unlisted Classes)
    (3, "etf"),      # Fubon ETF
]


@register_connector
class FubonConnector(BaseManagerConnector):
    """Extracts fund data from Fubon Fund Management HK website."""

    manager_ce_numbers = ["AAA662"]
    base_url = BASE_URL

    request_delay: float = 1.0
    request_timeout: int = 30

    _FUBON_MANAGER_SQL = (
        "AND (LOWER(fund_manager_name_en) LIKE '%fubon%')"
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

    def _accept_disclaimer(self, page):
        """Accept the cookie disclaimer."""
        try:
            btn = page.locator('button:has-text("Accept")')
            if btn.is_visible(timeout=5000):
                btn.first.click()
                time.sleep(2)
                return True
        except Exception:
            pass
        return False

    # ── Fund List ───────────────────────────────────────────────

    def _parse_listing_page(self, page, pkey: int) -> List[Dict[str, Any]]:
        """Parse a fund listing page for fund names, NAVs, and currency."""
        url = f"{BASE_URL}/retail-funds-list?pkey={pkey}"
        try:
            page.goto(url, wait_until="networkidle", timeout=30000)
            time.sleep(2)
            self._accept_disclaimer(page)
            time.sleep(2)
            # Reload if disclaimer was clicked
            page.goto(url, wait_until="networkidle", timeout=30000)
            time.sleep(3)
        except Exception:
            logger.warning(f"Fubon: failed to load listing pkey={pkey}")
            return []

        return page.evaluate("""(pkey) => {
            const tables = document.querySelectorAll('table');
            const result = [];
            const seen = new Set();

            for (const table of tables) {
                const rows = Array.from(table.querySelectorAll('tr'));
                if (rows.length < 2) continue;

                const headers = Array.from(rows[0].querySelectorAll('td, th'))
                    .map(c => (c.textContent || '').trim().toLowerCase());

                // Identify column indices
                let nameIdx = -1, navIdx = -1, ccyIdx = -1, dateIdx = -1, codeIdx = -1;

                headers.forEach((h, i) => {
                    if (h.includes('fund name')) nameIdx = i;
                    if ((h.includes('nav') || h.includes('unit')) && !h.includes('estimated')) navIdx = i;
                    if (h.includes('currency')) ccyIdx = i;
                    if ((h.includes('date') || h.includes('as of')) && !h.includes('payment')) dateIdx = i;
                    if (h.includes('stock code') || h.includes('code')) codeIdx = i;
                });

                // Fallback for ETF page: NAV header might be "NAV(DD/MM/YYYY)"
                if (navIdx < 0) {
                    headers.forEach((h, i) => {
                        if (h.startsWith('nav')) navIdx = i;
                    });
                }

                // Fallback for retail page: exact header names
                if (nameIdx < 0) {
                    headers.forEach((h, i) => {
                        if (h === 'fund name') nameIdx = i;
                    });
                }

                for (let i = 1; i < rows.length; i++) {
                    const cells = Array.from(rows[i].querySelectorAll('td, th'))
                        .map(c => (c.textContent || '').trim());

                    if (cells.length < 2) continue;

                    const name = nameIdx >= 0 ? cells[nameIdx] || '' : '';
                    if (!name || name.length < 5 || name.includes('Stock Code')) continue;
                    if (seen.has(name)) continue;
                    seen.add(name);

                    const navStr = navIdx >= 0 ? cells[navIdx] || '' : '';
                    const rawDate = dateIdx >= 0 ? cells[dateIdx] || '' : '';

                    // Parse NAV - could be "10.6160" or "2.6405 (USD)(Offical) / 20.69 HKD(Reference)"
                    let nav = null;
                    let ccy = '';

                    const simpleNav = navStr.match(/^([\\d.,]+)\\s*(?:\\(?([A-Z]{3}))?/);
                    if (simpleNav) {
                        nav = parseFloat(simpleNav[1].replace(/,/g, ''));
                        ccy = simpleNav[2] || '';
                    }

                    // Get currency from currency column if available
                    if (ccyIdx >= 0 && cells[ccyIdx]) {
                        const ccyText = cells[ccyIdx].replace(/[()]/g, '');
                        if (['USD', 'HKD', 'CNY', 'RMB', 'EUR', 'GBP', 'AUD', 'SGD'].includes(ccyText)) {
                            ccy = ccyText;
                        }
                    }

                    // Get stock code
                    const stockCode = codeIdx >= 0 ? cells[codeIdx] || '' : '';

                    // Find detail page link
                    let detailUrl = '';
                    const links = Array.from(document.querySelectorAll('a'));
                    for (const link of links) {
                        if (link.textContent.trim().includes(name.substring(0, 20)) &&
                            link.href.includes('retail-funds-details')) {
                            detailUrl = link.href;
                            break;
                        }
                    }

                    result.push({
                        fund_name: name,
                        nav: nav,
                        nav_date: rawDate,
                        currency: ccy === 'RMB' ? 'CNY' : ccy,
                        stock_code: stockCode,
                        product_url: detailUrl,
                        listing_type: pkey === 2 ? 'retail' : 'etf',
                    });
                }

                if (result.length > 0) break;
            }
            return result;
        }""", pkey)

    def _parse_fund_detail(self, page, fund_entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Visit a fund detail page to extract ISIN code."""
        url = fund_entry.get("product_url", "")
        if not url:
            return None

        try:
            page.goto(url, wait_until="networkidle", timeout=30000)
            time.sleep(2)
        except Exception:
            logger.warning(f"Fubon: failed to load {url}")
            return None

        html = page.evaluate("document.documentElement.outerHTML")

        # Extract all HK ISINs from the page
        isins = re.findall(r'HK\d{10}', html)
        unique_isins = list(dict.fromkeys(isins))  # deduplicate preserving order

        # Get fund name from page title
        title = page.title()
        fund_name = ""
        # Title format: "Fund Name - Fubon Fund Management"
        if ' - Fubon' in title:
            fund_name = title.split(' - Fubon')[0].strip()

        if not fund_name or len(fund_name) < 5:
            fund_name = fund_entry.get("fund_name", "")

        # Extract base currency from ISIN table area
        body = page.inner_text("body")
        base_ccy = ""
        ccy_match = re.search(r'Base Currency[:\s]+([A-Z]{3})', body)
        if ccy_match:
            base_ccy = ccy_match.group(1)

        # Fund size
        size_match = re.search(r'Fund Size[^:]*:\s*([^\n]+)', body)
        fund_size = size_match.group(1).strip() if size_match else ""

        # Launch date
        launch_match = re.search(r'(?:Fund Launch Date|Inception Date)[:\s]+(\d{2}/\d{2}/\d{4})', body)
        launch_date = ""
        if launch_match:
            launch_date = self._parse_date(launch_match.group(1)) or ""

        return {
            "fund_name": fund_name,
            "product_url": url,
            "source_type": "manager_website",
            "isins": unique_isins,
            "base_currency": base_ccy or fund_entry.get("currency", "USD"),
            "fund_size": fund_size,
            "fund_inception_date": launch_date,
            "nav": fund_entry.get("nav"),
            "nav_date": fund_entry.get("nav_date", ""),
            "currency": fund_entry.get("currency", base_ccy or "USD"),
            "stock_code": fund_entry.get("stock_code", ""),
            "listing_type": fund_entry.get("listing_type", ""),
        }

    def get_fund_list(self) -> List[Dict[str, Any]]:
        """Discover and parse all Fubon fund pages."""
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
            # Step 1: Accept disclaimer on any page
            page.goto(f"{BASE_URL}/retail-funds-list?pkey=2", wait_until="networkidle", timeout=30000)
            time.sleep(2)
            self._accept_disclaimer(page)
            time.sleep(2)

            # Step 2: Parse all listing pages
            all_entries = []
            for pkey, label in LISTING_PAGES:
                entries = self._parse_listing_page(page, pkey)
                logger.info(f"Fubon: found {len(entries)} {label} funds")
                all_entries.extend(entries)

            # Step 3: Visit each fund's detail page for ISINs
            funds = []
            for i, entry in enumerate(all_entries):
                detail = self._parse_fund_detail(page, entry)
                if detail:
                    funds.append(detail)
                    logger.info(
                        f"Fubon: {detail['fund_name'][:60]} — "
                        f"{len(detail.get('isins', []))} ISINs"
                    )
                else:
                    # Still include the entry even if detail parsing failed
                    entry["isins"] = []
                    entry["source_type"] = "manager_website"
                    funds.append(entry)

            return funds

        finally:
            context.close()

    # ── Name Matching ──────────────────────────────────────────

    def _match_fund_name(self, conn, extracted_name: str) -> Optional[int]:
        """Match Fubon fund name to hk_funds.id.

        Website names follow patterns like:
          - "Fubon NYSE FactSet Taiwan Core Semiconductor Index ETF"
          - "Fubon Shanghai-Shenzhen-Hong Kong High Dividend Yield ETF"
          - "Fubon Solactive Core Diversified Multi Asset Index ETF"
        """
        if not extracted_name:
            return None

        name = extracted_name.strip()

        # Strip share class suffixes
        name = re.sub(r'\s*[-–]\s*Class\s+[A-Z]\s*$', '', name)
        name = re.sub(r'\s*\([^)]*\)\s*$', '', name)

        candidates = [name]

        # Try without "Fubon " prefix
        for prefix in ["Fubon "]:
            if name.lower().startswith(prefix.lower()):
                stripped = name[len(prefix):]
                if stripped not in candidates:
                    candidates.append(stripped)

        for candidate in candidates:
            c = re.sub(r"\s+", " ", candidate.lower().strip())
            c = re.sub(
                r"\s+(fund|class\s+\w+|etf|sicav|ofc)\s*$",
                "", c, flags=re.IGNORECASE
            )

            word_count = len(c.split())

            for query, params in [
                ("LOWER(fund_name_en) = ?", [c]),
                ("LOWER(fund_name_en) LIKE ?", [f"%{c}%"]),
                ("? LIKE '%' || LOWER(fund_name_en) || '%'", [c]),
            ]:
                if "LIKE" in query and word_count < 2 and len(c) < 10:
                    continue
                if "? LIKE" in query and word_count < 2:
                    continue
                row = conn.execute(
                    f"""SELECT id, fund_name_en FROM hk_funds
                       WHERE {query} AND is_active = true
                       {self._FUBON_MANAGER_SQL}
                       LIMIT 1""",
                    params,
                ).fetchone()
                if row:
                    return row[0]

            # Word-level matching
            keywords = [
                w for w in c.split()
                if len(w) > 2
                and w not in (
                    "fund", "class", "etf", "fubon", "index",
                    "etf", "listed", "unlisted",
                )
            ]
            if len(keywords) >= 2:
                conditions = " AND ".join(
                    ["LOWER(fund_name_en) LIKE ?" for _ in keywords]
                )
                params = [f"%{kw}%" for kw in keywords]
                row = conn.execute(
                    f"""SELECT id, fund_name_en FROM hk_funds
                       WHERE {conditions} AND is_active = true
                       {self._FUBON_MANAGER_SQL}
                       LIMIT 1""",
                    params,
                ).fetchone()
                if row:
                    return row[0]

        return None

    # ── Main Interface ─────────────────────────────────────────

    def get_fund_details(self, isin: str) -> Optional[Dict[str, Any]]:
        """Not implemented."""
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
                nav = detail.get("nav")
                nav_date = detail.get("nav_date", "")
                currency = detail.get("currency", "")

                if not fund_name and not isins:
                    stats["errors"] += 1
                    continue

                # Match by ISIN first
                hk_fund_id = None
                for isin in isins:
                    row = conn.execute(
                        "SELECT id FROM hk_funds WHERE isin = ?",
                        [isin],
                    ).fetchone()
                    if row:
                        hk_fund_id = row[0]
                        break

                # Fallback to name matching
                if not hk_fund_id:
                    hk_fund_id = self._match_fund_name(conn, fund_name)

                if not hk_fund_id:
                    if (idx + 1) % 5 == 0:
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
                }
                if detail.get("base_currency"):
                    data["currency"] = detail["base_currency"]
                if detail.get("fund_inception_date"):
                    data["fund_inception_date"] = detail["fund_inception_date"]

                if update_fund_from_manager(conn, hk_fund_id, data):
                    stats["details_updated"] += 1

                # Store ISINs as share classes
                for isin in isins:
                    sc_data = {
                        "share_class_name": fund_name,
                        "isin": isin,
                        "currency": self._parse_currency(currency or "USD"),
                        "source": "fubon_website",
                    }
                    upsert_share_classes(conn, hk_fund_id, [sc_data])
                    stats["share_classes_stored"] += 1

                    update_fund_from_manager(conn, hk_fund_id, {"isin": isin})
                    stats["isins_updated"] += 1

                # Store NAV
                if nav and nav > 0:
                    parsed_date = self._parse_date(nav_date) if nav_date else today
                    n = upsert_nav_history(conn, hk_fund_id, [{
                        "nav": nav,
                        "nav_date": parsed_date or today,
                        "nav_currency": self._parse_currency(currency or "USD"),
                        "source": "fubon_website",
                    }])
                    stats["navs_stored"] += n

            logger.info(
                f"Fubon scrape complete: "
                f"Matched={stats['matched']}/{stats['funds_found']}, "
                f"ISINs={stats['isins_updated']}, "
                f"NAVs={stats['navs_stored']}, "
                f"ShareClasses={stats['share_classes_stored']}, "
                f"Details={stats['details_updated']}"
            )

        except Exception as e:
            logger.error(f"Fubon scrape failed: {e}")
            stats["errors"] += 1
        finally:
            self._close_browser()

        return stats
