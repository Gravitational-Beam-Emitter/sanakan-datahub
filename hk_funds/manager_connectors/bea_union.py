"""
BEA Union Investment connector.

Extracts fund data from buim.com via Playwright DOM scraping.
The fund price page is a server-rendered HTML table with ~193 share class
entries across ~20 distinct funds. No ISINs are available on the page.

Strategy:
  1. Load the fund price page with Playwright
  2. Scroll to accept disclaimer, then click Accept
  3. Parse the HTML table for fund names, share classes, NAVs, dates
  4. Match to hk_funds by name (no ISIN available)

CE: AAJ159 — BEA Union Investment Management Limited
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from hk_funds.manager_connectors.base import BaseManagerConnector, register_connector

logger = logging.getLogger("hk_funds.manager_connectors.bea_union")

FUND_PRICE_URL = "https://buim.com/EN/fund-price"


@register_connector
class BEAUnionConnector(BaseManagerConnector):
    """Extracts fund data from BEA Union's fund price page using Playwright.

    The page has a "scroll to accept" disclaimer modal. After accepting,
    a simple HTML table lists all funds with NAVs, currencies, and dates.
    """

    manager_ce_numbers = ["AAJ159"]
    base_url = "https://buim.com"

    request_delay: float = 1.0
    request_timeout: int = 30

    _BEA_UNION_MANAGER_SQL = (
        "AND (LOWER(fund_manager_name_en) LIKE '%bea union%'"
        " OR LOWER(fund_manager_name_en) LIKE '%bea union investment%')"
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

    def _accept_disclaimer(self, page) -> bool:
        """Scroll to bottom to enable Accept button, then click it."""
        try:
            page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(1)
            btn = page.locator('button:has-text("Accept")').first
            if btn.is_visible(timeout=3000):
                btn.click(force=True)
                time.sleep(2)
                return True
        except Exception:
            pass

        # Fallback: remove modal from DOM
        try:
            page.evaluate("""() => {
                const modal = document.querySelector('.modal-backdrop, .modal, [class*=disclaimer]');
                if (modal) modal.remove();
                document.body.style.overflow = 'auto';
            }""")
            time.sleep(1)
        except Exception:
            pass
        return False

    # ── Fund List ───────────────────────────────────────────────

    def get_fund_list(self) -> List[Dict[str, Any]]:
        """Load fund price page, accept disclaimer, parse the table."""
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
            page.goto(FUND_PRICE_URL, wait_until="domcontentloaded", timeout=30000)
            time.sleep(2)
            self._accept_disclaimer(page)

            # Wait for table
            page.wait_for_selector('table.fund-performance-new', timeout=10000)
            time.sleep(2)

            funds = page.evaluate("""() => {
                const table = document.querySelector('table.fund-performance-new');
                if (!table) return [];

                const rows = Array.from(table.querySelectorAll('tbody tr'));
                const result = [];
                let currentUmbrella = '';

                for (const row of rows) {
                    const cells = Array.from(row.querySelectorAll('td'));
                    if (cells.length < 3) continue;

                    // Umbrella header row (e.g., "BU Investment Series OFC")
                    if (row.classList.contains('table--bg-dark-grey') && cells.length === 1) {
                        currentUmbrella = cells[0].textContent.trim();
                        continue;
                    }

                    // Skip non-fund rows
                    if (!row.id || !row.id.startsWith('f')) continue;

                    const fundNameEl = row.querySelector('.fund-name-label a');
                    const fundName = fundNameEl
                        ? fundNameEl.textContent.trim()
                        : (cells[0]?.textContent?.trim() || '');

                    const shareClass = cells[2]?.textContent?.trim() || '';
                    const currency = cells[3]?.textContent?.trim() || '';
                    let navStr = cells[4]?.textContent?.trim() || '';
                    const dateStr = cells[5]?.textContent?.trim() || '';

                    // Strip currency symbol from NAV
                    navStr = navStr.replace(/^[$HK￥¥€£]+/, '').replace(/,/g, '').trim();
                    const nav = parseFloat(navStr);

                    if (!fundName) continue;

                    const fund = {
                        fund_name: fundName,
                        umbrella: currentUmbrella,
                        share_class_name: shareClass,
                        currency: currency,
                        nav_date: dateStr,
                        source_type: 'manager_website',
                        product_url: '""" + FUND_PRICE_URL + """',
                    };

                    if (!isNaN(nav)) fund.nav = nav;

                    result.push(fund);
                }

                return result;
            }""")

            logger.info(f"BEA Union: parsed {len(funds)} entries from table")
            return funds

        finally:
            context.close()

    # ── Name Matching ──────────────────────────────────────────

    def _match_fund_name(self, conn, extracted_name: str) -> Optional[int]:
        """Match BEA Union fund name to hk_funds.id.

        Website names include: "BU Global Flexi Allocation Fund",
        "BEA Wise All Weather Fund", "BEA Union Investment Global Quality Bond Fund"
        """
        if not extracted_name:
            return None

        name = extracted_name.strip()

        candidates = [name]

        # Try with/without common prefixes
        prefixes = [
            "BEA Union Investment ",
            "BEA ",
            "BU ",
        ]
        for prefix in prefixes:
            if name.upper().startswith(prefix.upper()):
                candidates.append(name[len(prefix):])
            else:
                candidates.append(prefix + name)

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
                       {self._BEA_UNION_MANAGER_SQL}
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
                    "fund", "class", "etf", "bea", "union", "investment",
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
                       {self._BEA_UNION_MANAGER_SQL}
                       LIMIT 1""",
                    params,
                ).fetchone()
                if row:
                    return row[0]

        return None

    # ── Main Interface ─────────────────────────────────────────

    def get_fund_details(self, isin: str) -> Optional[Dict[str, Any]]:
        """Not implemented — no ISINs on this website."""
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
            entries = self.get_fund_list()
            stats["funds_found"] = len(entries)

            # Track matched fund IDs to avoid duplicate detail updates
            seen_fund_ids = set()

            for idx, entry in enumerate(entries):
                fund_name = entry.get("fund_name", "")

                if not fund_name:
                    continue

                hk_fund_id = self._match_fund_name(conn, fund_name)

                if not hk_fund_id:
                    if (idx + 1) % 20 == 0:
                        logger.info(
                            f"  [{idx + 1}/{len(entries)}] "
                            f"Matched={stats['matched']} (no match: {fund_name[:50]})"
                        )
                    continue

                stats["matched"] += 1

                data = {
                    "fund_name": fund_name,
                    "nav_currency": entry.get("currency"),
                    "product_url": FUND_PRICE_URL,
                    "source_type": "manager_website",
                }
                if entry.get("share_class_name"):
                    data["share_class_name"] = entry["share_class_name"]

                if hk_fund_id not in seen_fund_ids:
                    if update_fund_from_manager(conn, hk_fund_id, data):
                        stats["details_updated"] += 1
                    seen_fund_ids.add(hk_fund_id)

                # Store NAV
                nav = entry.get("nav")
                nav_date = entry.get("nav_date", "")
                if nav and nav_date:
                    parsed_date = self._parse_date(nav_date)
                    if parsed_date:
                        n = upsert_nav_history(conn, hk_fund_id, [{
                            "nav": nav,
                            "nav_date": parsed_date,
                            "nav_currency": entry.get("currency", "HKD"),
                            "source": "bea_union_website",
                        }])
                        stats["navs_stored"] += n

                if (idx + 1) % 50 == 0:
                    logger.info(
                        f"  [{idx + 1}/{len(entries)}] "
                        f"Matched={stats['matched']} "
                        f"NAVs={stats['navs_stored']}"
                    )

            logger.info(
                f"BEA Union scrape complete: "
                f"Matched={stats['matched']}/{stats['funds_found']}, "
                f"NAVs={stats['navs_stored']}, "
                f"Details={stats['details_updated']}"
            )

        except Exception as e:
            logger.error(f"BEA Union scrape failed: {e}")
            stats["errors"] += 1
        finally:
            self._close_browser()

        return stats
