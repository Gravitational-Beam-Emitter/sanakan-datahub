"""
Asset Management Group Limited connector.

Extracts fund data from asset-mg.com via Playwright DOM scraping.
The funds page has a disclaimer modal that must be accepted first,
then a static HTML table with 3 OFC sub-funds: ISIN, NAV, valuation date.

CE: AMT657 — ASSET MANAGEMENT GROUP LIMITED
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from hk_funds.manager_connectors.base import BaseManagerConnector, register_connector

logger = logging.getLogger("hk_funds.manager_connectors.asset_mg")

FUNDS_PAGE_URL = "https://asset-mg.com/en/funds/"


@register_connector
class AssetMGConnector(BaseManagerConnector):
    """Extracts fund data from Asset Management Group's website using Playwright.

    Strategy:
      1. Load the funds page with Playwright
      2. Click ACCEPT to dismiss the disclaimer modal
      3. Parse the HTML table for fund names, ISINs, NAVs, dates
      4. Match to hk_funds by ISIN
    """

    manager_ce_numbers = ["AMT657"]
    base_url = "https://asset-mg.com"

    request_delay: float = 1.0
    request_timeout: int = 30

    _ASSET_MG_MANAGER_SQL = (
        "AND (LOWER(fund_manager_name_en) LIKE '%asset management group%'"
        " OR LOWER(fund_manager_name_en) LIKE '%opes%')"
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

    def _accept_disclaimer(self, page) -> bool:
        """Click the ACCEPT button on the disclaimer modal."""
        try:
            btn = page.locator('button:has-text("ACCEPT")')
            if btn.is_visible(timeout=5000):
                btn.click()
                time.sleep(2)
                return True
        except Exception as e:
            logger.warning(f"Could not click ACCEPT button: {e}")
        return False

    def get_fund_list(self) -> List[Dict[str, Any]]:
        """Load the funds page with Playwright, accept disclaimer, parse the table."""
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
            page.goto(FUNDS_PAGE_URL, wait_until="networkidle", timeout=30000)
            time.sleep(3)

            self._accept_disclaimer(page)

            # Extract table data
            funds = page.evaluate("""() => {
                const tables = document.querySelectorAll('table');
                if (!tables.length) return [];

                const rows = Array.from(tables[0].querySelectorAll('tr'));
                if (rows.length < 2) return [];

                // Parse header row to find column indices
                const headerCells = Array.from(rows[0].querySelectorAll('th'));
                const headerMap = {};
                headerCells.forEach((th, i) => {
                    const text = (th.textContent || '').toLowerCase();
                    headerMap[i] = text;
                });

                // Map columns by header keywords
                let nameCol = -1, scCol = -1, isinCol = -1, ccyCol = -1, navCol = -1, dateCol = -1;
                for (const [i, h] of Object.entries(headerMap)) {
                    if (h.includes('fund name') || h.includes('基金名稱')) nameCol = parseInt(i);
                    else if (h.includes('share class') || h.includes('股份類別')) scCol = parseInt(i);
                    else if (h.includes('isin') || h.includes('基本編號')) isinCol = parseInt(i);
                    else if (h.includes('curr') || h.includes('貨幣')) ccyCol = parseInt(i);
                    else if (h.includes('nav') || h.includes('資產淨值')) navCol = parseInt(i);
                    else if (h.includes('valuation') || h.includes('估值日')) dateCol = parseInt(i);
                }

                // Parse data rows
                const funds = [];
                for (let r = 1; r < rows.length; r++) {
                    const cells = Array.from(rows[r].querySelectorAll('td'));
                    if (cells.length === 0) continue;

                    const getVal = (col) => col >= 0 && col < cells.length
                        ? (cells[col].textContent || '').trim() : '';

                    // Get EN name only — cell contains "EN Name<br>CN Name"
                    const getEnName = (col) => {
                        if (col < 0 || col >= cells.length) return '';
                        const html = cells[col].innerHTML || '';
                        const parts = html.split('<br>');
                        return (parts[0] || '').replace(/<[^>]+>/g, '').trim();
                    };

                    const fundNameEn = getEnName(nameCol);

                    const isin = getVal(isinCol);
                    const currency = getVal(ccyCol);
                    const navStr = getVal(navCol);
                    const dateStr = getVal(dateCol);
                    const shareClass = getVal(scCol);

                    if (!fundNameEn) continue;

                    const fund = {
                        fund_name: fundNameEn,
                        share_class_name: shareClass,
                        isin: isin,
                        currency: currency,
                        source_type: 'manager_website',
                        product_url: '""" + FUNDS_PAGE_URL + """',
                    };

                    if (isin) fund.isin = isin;
                    if (shareClass) fund.share_class_name = shareClass;

                    // Parse NAV
                    if (navStr && navStr !== 'Not Available' && navStr !== 'N/A') {
                        const navVal = parseFloat(navStr.replace(/,/g, ''));
                        if (!isNaN(navVal)) fund.nav = navVal;
                    }

                    // Parse date
                    if (dateStr) {
                        fund.nav_date = dateStr;
                    }

                    funds.push(fund);
                }

                return funds;
            }""")

            logger.info(f"Asset MG: parsed {len(funds)} funds from table")
            return funds

        finally:
            context.close()

    # ── Name Matching ──────────────────────────────────────────

    def _match_fund_name(self, conn, extracted_name: str) -> Optional[int]:
        """Match Asset MG fund name to hk_funds.id.

        Website names follow: "ASSET MANAGEMENT GROUP OPES <Type> Fund"
        """
        if not extracted_name:
            return None

        name = extracted_name.strip()

        candidates = [name]

        # Also try without "ASSET MANAGEMENT GROUP " prefix
        prefixes = [
            "ASSET MANAGEMENT GROUP ",
            "ASSET MANAGEMENT GROUP OPES ",
        ]
        for prefix in prefixes:
            if name.upper().startswith(prefix.upper()):
                candidates.append(name[len(prefix):])

        for candidate in candidates:
            c = re.sub(r"\s+", " ", candidate.lower().strip())
            c = re.sub(r"\s+(fund|class\s+\w+|etf)\s*$", "", c, flags=re.IGNORECASE)

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
                       {self._ASSET_MG_MANAGER_SQL}
                       LIMIT 1""",
                    params,
                ).fetchone()
                if row:
                    return row[0]

            # Word-level matching
            keywords = [
                w for w in c.split()
                if len(w) > 3
                and w not in ("fund", "class", "etf", "asset", "management", "group", "opes")
            ]
            if len(keywords) >= 2:
                conditions = " AND ".join(
                    ["LOWER(fund_name_en) LIKE ?" for _ in keywords]
                )
                params = [f"%{kw}%" for kw in keywords]
                row = conn.execute(
                    f"""SELECT id, fund_name_en FROM hk_funds
                       WHERE {conditions} AND is_active = true
                       {self._ASSET_MG_MANAGER_SQL}
                       LIMIT 1""",
                    params,
                ).fetchone()
                if row:
                    return row[0]

        return None

    # ── Main Interface ─────────────────────────────────────────

    def get_fund_details(self, isin: str) -> Optional[Dict[str, Any]]:
        """Not implemented — no individual fund detail pages on this site."""
        return None

    def scrape_and_store(self, conn, date_str: str = None) -> Dict[str, int]:
        from hk_funds.storage import (
            update_fund_from_manager,
            upsert_nav_history,
        )

        today = date_str or datetime.now().strftime("%Y-%m-%d")
        stats = {
            "funds_found": 0, "matched": 0, "isins_updated": 0,
            "navs_stored": 0, "details_updated": 0, "errors": 0,
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

                if not fund_name:
                    stats["errors"] += 1
                    continue

                # Try ISIN match first (exact)
                hk_fund_id = None
                if isin:
                    row = conn.execute(
                        "SELECT id FROM hk_funds WHERE isin = ?",
                        [isin],
                    ).fetchone()
                    if row:
                        hk_fund_id = row[0]

                # Fallback to name matching
                if not hk_fund_id:
                    hk_fund_id = self._match_fund_name(conn, fund_name)

                if not hk_fund_id:
                    logger.info(f"  No match: {fund_name[:60]}")
                    continue

                stats["matched"] += 1

                data = {
                    "fund_name": fund_name,
                    "isin": isin,
                    "nav_currency": currency,
                    "product_url": FUNDS_PAGE_URL,
                    "source_type": "manager_website",
                }

                if fund.get("share_class_name"):
                    data["share_class_name"] = fund["share_class_name"]

                if update_fund_from_manager(conn, hk_fund_id, data):
                    stats["details_updated"] += 1

                if isin:
                    update_fund_from_manager(conn, hk_fund_id, {"isin": isin})
                    stats["isins_updated"] += 1

                if nav and nav_date:
                    parsed_date = self._parse_date(nav_date)
                    if parsed_date:
                        n = upsert_nav_history(conn, hk_fund_id, [{
                            "nav": nav,
                            "nav_date": parsed_date,
                            "nav_currency": currency,
                            "source": "asset_mg_website",
                        }])
                        stats["navs_stored"] += n

                logger.info(
                    f"  [{idx + 1}/{len(funds)}] {fund_name[:50]} "
                    f"ISIN={isin} NAV={nav} Date={nav_date}"
                )

            logger.info(
                f"Asset MG scrape complete: "
                f"Matched={stats['matched']}/{stats['funds_found']}, "
                f"ISINs={stats['isins_updated']}, "
                f"NAVs={stats['navs_stored']}, "
                f"Details={stats['details_updated']}, "
                f"Errors={stats['errors']}"
            )

        except Exception as e:
            logger.error(f"Asset MG scrape failed: {e}")
            stats["errors"] += 1
        finally:
            self._close_browser()

        return stats
