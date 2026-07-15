"""
China Universal Asset Management (Hong Kong) connector.

Extracts fund data from 99fund.com.hk via Playwright DOM scraping.
Each fund has a static HTML detail page with fund info table (ISIN codes,
share classes, Bloomberg codes) and NAV table.

Strategy:
  1. Visit HK Funds listing page to discover product codes
  2. For each fund, visit fundgl.shtml detail page
  3. Parse fund information table (ISINs, share classes, currency)
  4. Parse NAV per Unit table
  5. Match to hk_funds by name with China Universal manager constraint

CE: AUI816 — China Universal Asset Management (Hong Kong) Company Limited
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from hk_funds.manager_connectors.base import BaseManagerConnector, register_connector

logger = logging.getLogger("hk_funds.manager_connectors.china_universal")

BASE_URL = "https://www.99fund.com.hk/main/hkweb_new/en"
FUNDS_LIST_URL = f"{BASE_URL}/products/HKfunds/index.shtml"


@register_connector
class ChinaUniversalConnector(BaseManagerConnector):
    """Extracts fund data from China Universal Asset Management HK website."""

    manager_ce_numbers = ["AUI816"]
    base_url = "https://www.99fund.com.hk"

    request_delay: float = 1.0
    request_timeout: int = 30

    _CUAM_MANAGER_SQL = (
        "AND (LOWER(fund_manager_name_en) LIKE '%china universal%'"
        " OR LOWER(fund_manager_name_en) LIKE '%cuam%')"
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

    # ── Fund List ───────────────────────────────────────────────

    def _discover_product_codes(self, page) -> List[str]:
        """Discover fund product codes from the HK Funds listing page."""
        page.goto(FUNDS_LIST_URL, wait_until="networkidle", timeout=30000)
        time.sleep(2)

        return page.evaluate("""() => {
            const links = Array.from(document.querySelectorAll('a[href*="fundgl.shtml"]'));
            const codes = new Set();
            for (const link of links) {
                const m = link.href.match(/\\/products\\/([^\\/]+)\\/fundgl\\.shtml/);
                if (m) codes.add(m[1]);
            }
            return [...codes];
        }""")

    def _parse_fund_detail(self, page, product_code: str) -> Optional[Dict[str, Any]]:
        """Parse a single fund's detail page for ISINs, NAV, and metadata."""
        url = f"{BASE_URL}/products/{product_code}/fundgl.shtml"

        try:
            page.goto(url, wait_until="networkidle", timeout=20000)
            time.sleep(1)
        except Exception:
            logger.warning(f"CUAM: failed to load {url}")
            return None

        body_text = page.inner_text("body")

        # Extract fund name from breadcrumb
        fund_name_match = re.search(
            r'Product>Hong Kong Funds>(.+?)(?:\n|Fund Overview)', body_text
        )
        fund_name = fund_name_match.group(1).strip() if fund_name_match else ""

        if not fund_name:
            return None

        # Extract ISIN codes per share class from the fund info table
        # Parse rows: Unit Class | I Class | A Class | P Class | N Class
        #              ISIN Code | USD HK... | USD HK... | etc.
        share_class_data = page.evaluate("""() => {
            const result = [];
            const tables = document.querySelectorAll('table');
            for (const table of tables) {
                const rows = Array.from(table.querySelectorAll('tr'));
                let classRow = null;
                let isinRow = null;
                let bbgRow = null;
                let currencyRow = null;

                for (let i = 0; i < rows.length; i++) {
                    const cells = Array.from(rows[i].querySelectorAll('td, th'));
                    const firstCell = (cells[0]?.textContent || '').trim();

                    if (firstCell.includes('Unit Class')) {
                        classRow = rows[i];
                        // ISIN row should be next or nearby
                        for (let j = i + 1; j < Math.min(i + 5, rows.length); j++) {
                            const nextCells = Array.from(rows[j].querySelectorAll('td, th'));
                            const nextFirst = (nextCells[0]?.textContent || '').trim();
                            if (nextFirst.includes('ISIN')) {
                                isinRow = rows[j];
                            }
                            if (nextFirst.includes('Bloomberg')) {
                                bbgRow = rows[j];
                            }
                        }
                        break;
                    }
                }

                if (classRow && isinRow) {
                    const classCells = Array.from(classRow.querySelectorAll('td, th'));
                    const isinCells = Array.from(isinRow.querySelectorAll('td, th'));
                    const bbgCells = bbgRow ? Array.from(bbgRow.querySelectorAll('td, th')) : [];

                    for (let i = 1; i < classCells.length; i++) {
                        const scName = (classCells[i]?.textContent || '').trim();
                        const isinText = (isinCells[i]?.textContent || '').trim();
                        if (!scName || !isinText) continue;

                        // ISIN cell may have format: "USD HK0000911484" (currency + ISIN)
                        const parts = isinText.split(/\\s+/);
                        for (const part of parts) {
                            if (/^[A-Z]{2}[A-Z0-9]{10}$/.test(part)) {
                                const currency = parts[0] !== part ? parts[0] : '';
                                result.push({
                                    share_class_name: scName,
                                    isin: part,
                                    currency: currency,
                                    bloomberg: bbgCells[i]?.textContent?.trim() || '',
                                });
                            }
                        }
                    }
                }
            }
            return result;
        }""")

        # Parse NAV table
        nav_data = page.evaluate("""() => {
            const result = [];
            const tables = document.querySelectorAll('table');
            for (const table of tables) {
                const rows = Array.from(table.querySelectorAll('tr'));
                const headerRow = rows[0];
                if (!headerRow) continue;
                const headers = Array.from(headerRow.querySelectorAll('td, th'))
                    .map(c => (c.textContent || '').trim().toLowerCase());

                if (!headers.some(h => h.includes('nav') || h.includes('date'))) continue;

                for (let i = 1; i < rows.length; i++) {
                    const cells = Array.from(rows[i].querySelectorAll('td'));
                    if (cells.length < 3) continue;
                    const name = (cells[0]?.textContent || '').trim();
                    const dateStr = (cells[1]?.textContent || '').trim();
                    const navStr = (cells[2]?.textContent || '').trim();

                    if (!name || !dateStr || !navStr) continue;

                    const nav = parseFloat(navStr.replace(/,/g, ''));
                    if (isNaN(nav)) continue;

                    result.push({
                        fund_name: name,
                        nav_date: dateStr,
                        nav: nav,
                    });
                }
            }
            return result;
        }""")

        # Extract fund metadata
        fund_info = {}
        info_map = {
            "Base Currency": "base_currency",
            "Fund Manager": "fund_manager",
            "Trustee": "trustee",
            "Custodian": "custodian",
            "Management Fee": "management_fee",
            "Dealing Frequency": "dealing_frequency",
        }
        for label, key in info_map.items():
            match = re.search(
                rf'{label}\s+(.+)',
                body_text, re.IGNORECASE,
            )
            if match:
                fund_info[key] = match.group(1).strip().split('\n')[0]

        return {
            "fund_name": fund_name,
            "product_code": product_code,
            "product_url": url,
            "source_type": "manager_website",
            "share_classes": share_class_data,
            "nav_entries": nav_data,
            **fund_info,
        }

    def get_fund_list(self) -> List[Dict[str, Any]]:
        """Discover product codes and parse detail pages for all HK funds."""
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
            codes = self._discover_product_codes(page)
            logger.info(f"CUAM: discovered {len(codes)} product codes: {codes}")

            funds = []
            for code in codes:
                detail = self._parse_fund_detail(page, code)
                if detail:
                    funds.append(detail)
                    logger.info(
                        f"CUAM: {detail['fund_name']} — "
                        f"{len(detail['share_classes'])} share classes, "
                        f"{len(detail['nav_entries'])} NAV entries"
                    )

            return funds

        finally:
            context.close()

    # ── Name Matching ──────────────────────────────────────────

    def _match_fund_name(self, conn, extracted_name: str) -> Optional[int]:
        """Match China Universal fund name to hk_funds.id.

        Website names follow patterns like:
          - "CUAM China-Hong Kong Strategy Fund"
          - "CUAM USD Money Market Fund"
          - "CUAM Hong Kong Dollar Bond Fund"
        """
        if not extracted_name:
            return None

        name = extracted_name.strip()

        # Strip share class suffix in parentheses
        name = re.sub(r'\s*\([^)]*\)\s*$', '', name)

        candidates = [name]

        # Try with/without "CUAM " prefix
        for prefix in ["CUAM ", "China Universal "]:
            if name.lower().startswith(prefix.lower()):
                stripped = name[len(prefix):]
                if stripped not in candidates:
                    candidates.append(stripped)
            else:
                candidates.append(prefix + name)

        for candidate in candidates:
            c = re.sub(r"\s+", " ", candidate.lower().strip())

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
                       {self._CUAM_MANAGER_SQL}
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
                    "fund", "class", "etf", "cuam", "china", "universal",
                    "hong", "kong",
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
                       {self._CUAM_MANAGER_SQL}
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

            processed_fund_ids: set = set()

            for idx, detail in enumerate(fund_details):
                fund_name = detail.get("fund_name", "")
                share_classes = detail.get("share_classes", [])
                nav_entries = detail.get("nav_entries", [])

                if not fund_name:
                    stats["errors"] += 1
                    continue

                # Match to SFC register by name
                hk_fund_id = self._match_fund_name(conn, fund_name)

                if not hk_fund_id:
                    logger.info(
                        f"  [{idx + 1}/{len(fund_details)}] "
                        f"no match: {fund_name[:60]}"
                    )
                    continue

                stats["matched"] += 1

                # Store fund details (once per fund)
                if hk_fund_id not in processed_fund_ids:
                    data = {
                        "fund_name": fund_name,
                        "product_url": detail.get("product_url", ""),
                        "source_type": "manager_website",
                    }
                    if update_fund_from_manager(conn, hk_fund_id, data):
                        stats["details_updated"] += 1
                    processed_fund_ids.add(hk_fund_id)

                # Store share classes with ISINs
                for sc in share_classes:
                    isin = sc.get("isin", "")
                    currency = sc.get("currency", "")

                    if isin:
                        sc_data = {
                            "share_class_name": sc.get("share_class_name", ""),
                            "isin": isin,
                            "currency": currency if currency else self._parse_currency(detail.get("base_currency", "USD")),
                            "source": "cuam_website",
                        }
                        upsert_share_classes(conn, hk_fund_id, [sc_data])
                        stats["share_classes_stored"] += 1

                        # Mark ISIN on fund
                        update_fund_from_manager(conn, hk_fund_id, {"isin": isin})
                        stats["isins_updated"] += 1

                # Store NAV
                for nav_entry in nav_entries:
                    nav = nav_entry.get("nav")
                    nav_date_str = nav_entry.get("nav_date", "")
                    if nav and nav_date_str:
                        parsed_date = self._parse_date(nav_date_str)
                        if parsed_date:
                            currency = detail.get("base_currency", "USD")
                            n = upsert_nav_history(conn, hk_fund_id, [{
                                "nav": nav,
                                "nav_date": parsed_date,
                                "nav_currency": self._parse_currency(currency),
                                "source": "cuam_website",
                            }])
                            stats["navs_stored"] += n

            logger.info(
                f"China Universal scrape complete: "
                f"Matched={stats['matched']}/{stats['funds_found']}, "
                f"ISINs={stats['isins_updated']}, "
                f"NAVs={stats['navs_stored']}, "
                f"ShareClasses={stats['share_classes_stored']}, "
                f"Details={stats['details_updated']}"
            )

        except Exception as e:
            logger.error(f"China Universal scrape failed: {e}")
            stats["errors"] += 1
        finally:
            self._close_browser()

        return stats
