"""
Fullgoal Asset Management (HK) connector.

Extracts fund data from fullgoal.com.hk via Playwright DOM scraping.
Each fund has a detail page with Fund Information table (ISIN codes,
share classes, Bloomberg codes) and tabbed interface (Summary, Performance,
Holdings, Dividend, Documents).

Strategy:
  1. Visit known fund detail pages
  2. Parse Fund Information table (ISINs, share classes, fund type)
  3. Parse NAV data from Performance tab or Summary tab
  4. Match to hk_funds by name with Fullgoal manager constraint

CE: AZX665 — Fullgoal Asset Management (HK) Limited
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from hk_funds.manager_connectors.base import BaseManagerConnector, register_connector

logger = logging.getLogger("hk_funds.manager_connectors.fullgoal")

BASE_URL = "https://www.fullgoal.com.hk"

FUND_PAGES = [
    ("smallMid", "Fullgoal China Small-Mid Cap Growth Fund"),
    ("chinaAShareFund", "Fullgoal China A Share Fund"),
    ("selectInvestmentGradeBondFund", "Fullgoal Select Investment Grade Bond Fund"),
    ("investmentGradeBondFund", "Fullgoal Investment Grade Bond Fund"),
]


@register_connector
class FullgoalConnector(BaseManagerConnector):
    """Extracts fund data from Fullgoal Asset Management HK website."""

    manager_ce_numbers = ["AZX665"]
    base_url = BASE_URL

    request_delay: float = 1.0
    request_timeout: int = 30

    _FULLGOAL_MANAGER_SQL = (
        "AND (LOWER(fund_manager_name_en) LIKE '%fullgoal%'"
        " OR LOWER(fund_manager_name_en) LIKE '%full goal%')"
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

    def _parse_fund_detail(self, page, page_code: str) -> Optional[Dict[str, Any]]:
        """Parse a single fund's detail page."""
        url = f"{BASE_URL}/enfundDetail/{page_code}/index.html"

        try:
            page.goto(url, wait_until="networkidle", timeout=20000)
            time.sleep(1)
        except Exception:
            logger.warning(f"Fullgoal: failed to load {url}")
            return None

        body_text = page.inner_text("body")

        # Extract fund name from title/breadcrumb
        fund_name_match = re.search(
            r'Fund Details Page\s*\n\s*(.+?)(?:\n|$)', body_text
        )
        fund_name = fund_name_match.group(1).strip() if fund_name_match else ""

        if not fund_name:
            # Fallback: use page title
            fund_name = page.title().strip()

        # Extract ISIN codes from Fund Information section
        # ISIN Code header uses rowspan=N, each ISIN is in a subsequent row
        share_classes = page.evaluate("""() => {
            const result = [];
            const tables = document.querySelectorAll('table');
            for (const table of tables) {
                const rows = Array.from(table.querySelectorAll('tr'));
                for (let i = 0; i < rows.length; i++) {
                    const cells = Array.from(rows[i].querySelectorAll('td, th'));
                    const firstCell = (cells[0]?.textContent || '').trim();
                    if (firstCell === 'ISIN Code' || firstCell === 'ISIN') {
                        const rowspan = parseInt(cells[0].getAttribute('rowspan') || '1');
                        // Parse ISINs from subsequent rows (up to rowspan count)
                        for (let j = 1; j < rowspan && (i + j) < rows.length; j++) {
                            const nextCells = Array.from(rows[i + j].querySelectorAll('td, th'));
                            const cellText = (nextCells[0]?.textContent || '').trim();
                            if (!cellText) continue;
                            // Format: "Class A USD (DIST): HK0001176517" or "Class S USD (DIST): HK0001005641"
                            const isinMatch = cellText.match(/([A-Z]{2}[A-Z0-9]{10})/);
                            if (isinMatch) {
                                const scPart = cellText.replace(isinMatch[0], '').replace(':', '').replace(/^\\s*Class\\s+/, '').trim();
                                result.push({
                                    share_class_name: 'Class ' + scPart,
                                    isin: isinMatch[1],
                                });
                            }
                        }
                        // Also check same row for additional columns (some pages have all ISINs in one row)
                        for (let k = 1; k < cells.length; k++) {
                            const cellText = (cells[k]?.textContent || '').trim();
                            if (!cellText) continue;
                            const isinMatch = cellText.match(/([A-Z]{2}[A-Z0-9]{10})/);
                            if (isinMatch && !result.some(r => r.isin === isinMatch[1])) {
                                const scPart = cellText.replace(isinMatch[0], '').replace(':', '').trim();
                                result.push({
                                    share_class_name: scPart || '',
                                    isin: isinMatch[1],
                                });
                            }
                        }
                        if (result.length > 0) break;
                    }
                }
                if (result.length > 0) break;
            }
            return result;
        }""")

        # Extract fund metadata
        fund_info = {}
        info_text = body_text

        # Fund type
        ft_match = re.search(r'Fund Type\s+(.+)', info_text)
        if ft_match:
            fund_info["fund_type"] = ft_match.group(1).strip().split('\n')[0]

        # Launch date
        ld_match = re.search(r'Launch Date\s+(.+)', info_text)
        if ld_match:
            date_str = ld_match.group(1).strip().split('\n')[0]
            parsed = self._parse_date(date_str)
            if parsed:
                fund_info["fund_inception_date"] = parsed

        # Currency
        cur_match = re.search(r'Accounting Currency\s+(.+)', info_text)
        if cur_match:
            fund_info["base_currency"] = cur_match.group(1).strip().split('\n')[0]

        # Domicile
        dom_match = re.search(r'Domicile\s+(.+)', info_text)
        if dom_match:
            fund_info["domicile"] = dom_match.group(1).strip().split('\n')[0]

        # Legal Structure
        ls_match = re.search(r'Legal Structure\s+(.+)', info_text)
        if ls_match:
            fund_info["legal_structure"] = ls_match.group(1).strip().split('\n')[0]

        # Bloomberg tickers
        bbg_match = re.search(r'Bloomberg Ticker\s+(.+)', info_text)
        if bbg_match:
            fund_info["bloomberg_ticker"] = bbg_match.group(1).strip().split('\n')[0]

        # Extract NAV from Fund Information table (Summary tab)
        # "Net Asset Value*" row has rowspan with one NAV per share class
        nav_data = page.evaluate("""() => {
            const result = [];
            const tables = document.querySelectorAll('table');
            for (const table of tables) {
                const rows = Array.from(table.querySelectorAll('tr'));
                for (let i = 0; i < rows.length; i++) {
                    const cells = Array.from(rows[i].querySelectorAll('td, th'));
                    const firstCell = (cells[0]?.textContent || '').trim();
                    if (firstCell.includes('Net Asset Value')) {
                        const rowspan = parseInt(cells[0].getAttribute('rowspan') || '1');
                        for (let j = 1; j < rowspan && (i + j) < rows.length; j++) {
                            const nextCells = Array.from(rows[i + j].querySelectorAll('td, th'));
                            const cellText = (nextCells[0]?.textContent || '').trim();
                            if (!cellText) continue;
                            // Format: "Class S USD (DIST): 1157.880"
                            const parts = cellText.split(':');
                            if (parts.length >= 2) {
                                const nav = parseFloat(parts[parts.length - 1].trim().replace(/,/g, ''));
                                if (!isNaN(nav) && nav > 0) {
                                    const scName = parts.slice(0, -1).join(':').trim();
                                    const currencyMatch = scName.match(/\b([A-Z]{3})\b/);
                                    result.push({
                                        share_class_name: scName,
                                        nav: nav,
                                        currency: currencyMatch ? currencyMatch[1] : '',
                                    });
                                }
                            }
                        }
                        if (result.length > 0) break;
                    }
                }
                if (result.length > 0) break;
            }
            return result;
        }""")

        return {
            "fund_name": fund_name,
            "page_code": page_code,
            "product_url": url,
            "source_type": "manager_website",
            "share_classes": share_classes,
            "nav_data": nav_data,
            **fund_info,
        }

    def get_fund_list(self) -> List[Dict[str, Any]]:
        """Parse detail pages for all known Fullgoal HK funds."""
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
            funds = []
            for code, name in FUND_PAGES:
                detail = self._parse_fund_detail(page, code)
                if detail:
                    funds.append(detail)
                    logger.info(
                        f"Fullgoal: {detail['fund_name'][:60]} — "
                        f"{len(detail['share_classes'])} share classes"
                    )

            return funds

        finally:
            context.close()

    # ── Name Matching ──────────────────────────────────────────

    def _match_fund_name(self, conn, extracted_name: str) -> Optional[int]:
        """Match Fullgoal fund name to hk_funds.id.

        Website names follow patterns like:
          - "Fullgoal International Funds (SICAV) - Fullgoal China Small-Mid Cap Growth Fund"
          - "Fullgoal International Fund Series OFC - Fullgoal Select Investment Grade Bond Fund"
          - "Fullgoal Global Fund Series - Fullgoal Investment Grade Bond Fund"
        """
        if not extracted_name:
            return None

        name = extracted_name.strip()

        # Strip SICAV / umbrella prefixes
        candidates = [name]
        # Remove umbrella prefix: "Fullgoal Xxx Series - " or "Fullgoal Xxx (SICAV) - "
        cleaned = re.sub(
            r'^Fullgoal\s+(?:International\s+Funds?\s*(?:\(SICAV\)|Series\s+OFC)\s*[-–]\s*)',
            'Fullgoal ', name, flags=re.IGNORECASE,
        )
        if cleaned != name:
            candidates.append(cleaned)
        cleaned = re.sub(
            r'^Fullgoal\s+Global\s+Fund\s+Series\s*[-–]\s*',
            'Fullgoal ', name, flags=re.IGNORECASE,
        )
        if cleaned not in candidates:
            candidates.append(cleaned)

        for candidate in candidates:
            c = re.sub(r"\s+", " ", candidate.lower().strip())
            c = re.sub(r"\s+(fund|class\s+\w+|etf|sicav|ofc)\s*$", "", c, flags=re.IGNORECASE)

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
                       {self._FULLGOAL_MANAGER_SQL}
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
                    "fund", "class", "etf", "fullgoal", "international",
                    "series", "ofc", "sicav", "global",
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
                       {self._FULLGOAL_MANAGER_SQL}
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
                share_classes = detail.get("share_classes", [])
                nav_data = detail.get("nav_data", [])
                base_ccy = detail.get("base_currency", "USD")

                if not fund_name:
                    stats["errors"] += 1
                    continue

                # Match to SFC register
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
                }
                if detail.get("fund_inception_date"):
                    data["fund_inception_date"] = detail["fund_inception_date"]
                if detail.get("fund_type"):
                    data["fund_type"] = detail["fund_type"]
                if detail.get("domicile"):
                    data["domicile"] = detail["domicile"]

                if update_fund_from_manager(conn, hk_fund_id, data):
                    stats["details_updated"] += 1

                # Store share classes with ISINs
                for sc in share_classes:
                    isin = sc.get("isin", "")
                    if isin:
                        sc_data = {
                            "share_class_name": sc.get("share_class_name", ""),
                            "isin": isin,
                            "currency": self._parse_currency(base_ccy),
                            "source": "fullgoal_website",
                        }
                        upsert_share_classes(conn, hk_fund_id, [sc_data])
                        stats["share_classes_stored"] += 1

                        update_fund_from_manager(conn, hk_fund_id, {"isin": isin})
                        stats["isins_updated"] += 1

                # Store NAV
                for nav_entry in nav_data:
                    nav = nav_entry.get("nav")
                    if nav:
                        n = upsert_nav_history(conn, hk_fund_id, [{
                            "nav": nav,
                            "nav_date": today,
                            "nav_currency": self._parse_currency(base_ccy),
                            "source": "fullgoal_website",
                        }])
                        stats["navs_stored"] += n

            logger.info(
                f"Fullgoal scrape complete: "
                f"Matched={stats['matched']}/{stats['funds_found']}, "
                f"ISINs={stats['isins_updated']}, "
                f"NAVs={stats['navs_stored']}, "
                f"ShareClasses={stats['share_classes_stored']}, "
                f"Details={stats['details_updated']}"
            )

        except Exception as e:
            logger.error(f"Fullgoal scrape failed: {e}")
            stats["errors"] += 1
        finally:
            self._close_browser()

        return stats
