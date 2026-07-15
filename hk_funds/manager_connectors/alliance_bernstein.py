"""
AllianceBernstein (AB) Hong Kong connector.

Extracts fund data from abfunds.com.hk via Playwright DOM scraping.
The site is an Adobe Experience Manager (AEM) SPA that loads fund data
via AJAX from webapi.alliancebernstein.com.

Strategy (Playwright-only, no direct API calls):
  1. Navigate to the fund listing page
  2. Wait for the fund table to render via JavaScript
  3. Parse table rows for fund name, share class, NAV, NAV date
  4. Extract ISINs from fund detail page URLs embedded in the page
  5. Match to hk_funds by name with AllianceBernstein manager constraint

CE: ADX555 — AllianceBernstein Hong Kong Limited
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from hk_funds.manager_connectors.base import BaseManagerConnector, register_connector

logger = logging.getLogger("hk_funds.manager_connectors.alliance_bernstein")

FUNDS_LIST_URL = "https://www.abfunds.com.hk/hk/en/investor/funds.html"


@register_connector
class AllianceBernsteinConnector(BaseManagerConnector):
    """Extracts fund data from AllianceBernstein HK website (abfunds.com.hk)."""

    manager_ce_numbers = ["ADX555"]
    base_url = "https://www.abfunds.com.hk"

    request_delay: float = 1.0
    request_timeout: int = 30

    _AB_MANAGER_SQL = (
        "AND (LOWER(fund_manager_name_en) LIKE '%alliancebernstein%'"
        " OR LOWER(fund_manager_name_en) LIKE '%alliance bernstein%'"
        " OR LOWER(fund_manager_name_en) LIKE '%ab global%')"
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

    def get_fund_list(self) -> List[Dict[str, Any]]:
        """Load the fund listing page, wait for AJAX table to render,
        then extract fund data from the DOM."""
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
            page.goto(FUNDS_LIST_URL, wait_until="networkidle", timeout=30000)
            # Wait extra time for AJAX content
            time.sleep(5)

            # Extract fund data from rendered table
            funds = page.evaluate("""() => {
                const result = [];
                const seen = new Set();

                // Find fund links - each fund has a detail URL with ISIN
                const links = document.querySelectorAll('a[href*=".html"]');
                for (const link of links) {
                    const href = link.href;
                    // Fund detail URLs contain ISIN: .{ISIN}.html
                    const isinMatch = href.match(/\\.([A-Z]{2}[A-Z0-9]{10})\\.html/);
                    if (!isinMatch) continue;

                    const isin = isinMatch[1];
                    if (seen.has(isin)) continue;
                    seen.add(isin);

                    // Get fund name from the link text or surrounding context
                    let fundName = link.textContent.trim();
                    if (!fundName || fundName.length < 3) {
                        // Try to find fund name from nearby elements
                        const parent = link.closest('div, li, tr');
                        if (parent) {
                            fundName = parent.textContent.trim().split('\\n')[0].substring(0, 120);
                        }
                    }

                    result.push({
                        fund_name: fundName,
                        isin: isin,
                        product_url: href,
                    });
                }

                // If no ISINs found in links, try parsing the page differently
                if (result.length === 0) {
                    // Look for ISINs in the page source
                    const text = document.body.textContent;
                    const isinPattern = /[A-Z]{2}[A-Z0-9]{10}/g;
                    let m;
                    const seen2 = new Set();
                    while ((m = isinPattern.exec(text)) !== null) {
                        const isin = m[0];
                        if (/^[A-Z]{2}[0-9]{10}$/.test(isin) && !seen2.has(isin)) {
                            seen2.add(isin);
                            result.push({fund_name: '', isin: isin, product_url: ''});
                        }
                    }
                }

                return result;
            }""")

            # If we got ISIN links, extract NAV data from the table
            if funds:
                # Try to parse the daily NAV table
                table_data = page.evaluate("""() => {
                    const result = [];
                    const tables = document.querySelectorAll('table');
                    for (const table of tables) {
                        const rows = Array.from(table.querySelectorAll('tr'));
                        let headerRow = null;
                        let navColIdx = -1;
                        let dateColIdx = -1;
                        let nameColIdx = -1;
                        let isinColIdx = -1;
                        let ccyColIdx = -1;

                        // Find header row
                        for (let i = 0; i < Math.min(rows.length, 5); i++) {
                            const headers = Array.from(rows[i].querySelectorAll('td, th'))
                                .map(c => (c.textContent || '').trim().toLowerCase());
                            if (headers.some(h =>
                                h.includes('nav') || h.includes('fund name') ||
                                h.includes('isin') || h.includes('price')
                            )) {
                                headerRow = i;
                                headers.forEach((h, idx) => {
                                    if (h.includes('nav') && !h.includes('change')) navColIdx = idx;
                                    if (h.includes('date') || h.includes('as of')) dateColIdx = idx;
                                    if (h.includes('fund') || h.includes('name')) nameColIdx = idx;
                                    if (h.includes('isin')) isinColIdx = idx;
                                    if (h.includes('currency')) ccyColIdx = idx;
                                });
                                break;
                            }
                        }

                        if (headerRow !== null) {
                            for (let i = headerRow + 1; i < rows.length; i++) {
                                const cells = Array.from(rows[i].querySelectorAll('td'));
                                if (cells.length < 2) continue;

                                const entry = {};
                                if (nameColIdx >= 0 && cells[nameColIdx]) {
                                    entry.fund_name = cells[nameColIdx].textContent.trim();
                                }
                                if (navColIdx >= 0 && cells[navColIdx]) {
                                    const nav = parseFloat(cells[navColIdx].textContent.replace(/,/g, ''));
                                    if (!isNaN(nav)) entry.nav = nav;
                                }
                                if (dateColIdx >= 0 && cells[dateColIdx]) {
                                    entry.nav_date = cells[dateColIdx].textContent.trim();
                                }
                                if (isinColIdx >= 0 && cells[isinColIdx]) {
                                    entry.isin = cells[isinColIdx].textContent.trim();
                                }
                                if (ccyColIdx >= 0 && cells[ccyColIdx]) {
                                    entry.currency = cells[ccyColIdx].textContent.trim();
                                }

                                if (entry.fund_name || entry.isin) {
                                    result.push(entry);
                                }
                            }
                        }
                    }
                    return result;
                }""")

                # Merge table data with ISIN data
                if table_data:
                    # Create ISIN lookup
                    for td in table_data:
                        isin = td.get("isin", "")
                        if isin and not td.get("fund_name"):
                            # Try to find fund name from the links
                            for f in funds:
                                if f["isin"] == isin:
                                    td["fund_name"] = f["fund_name"]
                                    break

                    funds = table_data if table_data else funds

            logger.info(f"AB: extracted {len(funds)} fund entries")
            return funds

        finally:
            context.close()

    # ── Name Matching ──────────────────────────────────────────

    def _match_fund_name(self, conn, extracted_name: str) -> Optional[int]:
        """Match AB fund name to hk_funds.id.

        Website names follow patterns like:
          - "AB American Growth Portfolio"
          - "AB FCP I - American Income Portfolio"
          - "AB SICAV I - Low Volatility Equity Portfolio"
        """
        if not extracted_name:
            return None

        name = extracted_name.strip()

        # Strip share class info from parentheticals
        name = re.sub(r'\s*\([^)]*\)\s*$', '', name)
        name = re.sub(r'\s+[A-I]\s+(?:Acc|Dist|Inc).*$', '', name, flags=re.IGNORECASE)

        # Strip share class suffix like "A USD", "A2 EUR", etc.
        name = re.sub(r'\s+[A-I]\d?\s+[A-Z]{3}\s*$', '', name)

        candidates = [name]

        # Expand "AB" prefix
        if re.match(r'^AB\s', name, re.IGNORECASE):
            ab_stripped = re.sub(r'^AB\s+', '', name, flags=re.IGNORECASE)
            candidates.append(ab_stripped)

        # Handle "AB FCP I -" and "AB SICAV I -" prefixes
        for prefix in ["AB FCP I - ", "AB SICAV I - ", "AB "]:
            if name.lower().startswith(prefix.lower()):
                stripped = name[len(prefix):]
                if stripped not in candidates:
                    candidates.append(stripped)

        for candidate in candidates:
            c = re.sub(r"\s+", " ", candidate.lower().strip())
            c = re.sub(r"\s+(fund|class\s+\w+|portfolio|etf)\s*$", "", c, flags=re.IGNORECASE)

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
                       {self._AB_MANAGER_SQL}
                       LIMIT 1""",
                    params,
                ).fetchone()
                if row:
                    return row[0]

            # Word-level matching
            keywords = [
                w for w in c.split()
                if len(w) > 3
                and w not in (
                    "fund", "class", "portfolio", "american", "global",
                    "international", "income", "growth",
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
                       {self._AB_MANAGER_SQL}
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
            funds = self.get_fund_list()
            stats["funds_found"] = len(funds)

            for idx, fund in enumerate(funds):
                fund_name = fund.get("fund_name", "")
                isin = fund.get("isin", "")
                nav = fund.get("nav")
                nav_date = fund.get("nav_date", "")
                currency = fund.get("currency", "USD")

                if not isin and not fund_name:
                    continue

                # Match by ISIN first
                hk_fund_id = None
                if isin:
                    row = conn.execute(
                        "SELECT id FROM hk_funds WHERE isin = ?",
                        [isin],
                    ).fetchone()
                    if row:
                        hk_fund_id = row[0]

                # Fallback to name matching
                if not hk_fund_id and fund_name:
                    hk_fund_id = self._match_fund_name(conn, fund_name)

                if not hk_fund_id:
                    if (idx + 1) % 20 == 0:
                        logger.info(
                            f"  [{idx + 1}/{len(funds)}] "
                            f"Matched={stats['matched']} "
                            f"(no match: {fund_name[:60] if fund_name else isin})"
                        )
                    continue

                stats["matched"] += 1

                # Set ISIN
                if isin:
                    update_fund_from_manager(conn, hk_fund_id, {"isin": isin})
                    stats["isins_updated"] += 1

                    # Store share class
                    sc_data = {
                        "share_class_name": fund_name,
                        "isin": isin,
                        "currency": self._parse_currency(currency),
                        "source": "ab_website",
                    }
                    upsert_share_classes(conn, hk_fund_id, [sc_data])
                    stats["share_classes_stored"] += 1

                # Store NAV
                if nav:
                    parsed_date = self._parse_date(nav_date) if nav_date else today
                    n = upsert_nav_history(conn, hk_fund_id, [{
                        "nav": nav,
                        "nav_date": parsed_date,
                        "nav_currency": self._parse_currency(currency or "USD"),
                        "source": "ab_website",
                    }])
                    stats["navs_stored"] += n

                # Store fund details
                data = {
                    "fund_name": fund_name,
                    "isin": isin,
                    "product_url": fund.get("product_url", ""),
                    "source_type": "manager_website",
                }
                if update_fund_from_manager(conn, hk_fund_id, data):
                    stats["details_updated"] += 1

                if (idx + 1) % 20 == 0:
                    logger.info(
                        f"  [{idx + 1}/{len(funds)}] "
                        f"Matched={stats['matched']} "
                        f"ISINs={stats['isins_updated']} "
                        f"NAVs={stats['navs_stored']}"
                    )

            logger.info(
                f"AB scrape complete: "
                f"Matched={stats['matched']}/{stats['funds_found']}, "
                f"ISINs={stats['isins_updated']}, "
                f"NAVs={stats['navs_stored']}, "
                f"ShareClasses={stats['share_classes_stored']}, "
                f"Details={stats['details_updated']}"
            )

        except Exception as e:
            logger.error(f"AB scrape failed: {e}")
            stats["errors"] += 1
        finally:
            self._close_browser()

        return stats
