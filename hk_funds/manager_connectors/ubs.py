"""
UBS Asset Management connector.

Extracts fund data from ubs.com fund price page via Playwright.
The page has a static HTML table with ISIN, fund name, date, currency, NAV.

Strategy:
  1. Load the fund price page with Playwright
  2. Parse the HTML table: ISIN, Fund Name, Date, Currency, NAV
  3. Match to hk_funds by ISIN

CE: AGP568 — UBS Asset Management (Hong Kong) Limited
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from hk_funds.manager_connectors.base import BaseManagerConnector, register_connector

logger = logging.getLogger("hk_funds.manager_connectors.ubs")

FUNDS_PAGE_URL = "https://www.ubs.com/hk/en/assetmanagement/funds/asset-class.html"


@register_connector
class UBSConnector(BaseManagerConnector):
    """Extracts fund data from UBS Asset Management HK fund price page."""

    manager_ce_numbers = ["AGP568"]
    base_url = "https://www.ubs.com"

    request_delay: float = 1.0
    request_timeout: int = 30

    _UBS_MANAGER_SQL = (
        "AND (LOWER(fund_manager_name_en) LIKE '%ubs%')"
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
        """Load the fund price page and parse the table."""
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

            # Handle cookie consent
            try:
                btn = page.locator('button:has-text("Accept"), button:has-text("Accept All")')
                if btn.is_visible(timeout=3000):
                    btn.first.click()
                    time.sleep(1)
            except Exception:
                pass

            # Extract table data
            # Columns: ISIN, Fund Name, Date (DD/MM/YYYY), Currency, NAV, ...docs
            funds = page.evaluate("""() => {
                const table = document.querySelector('table');
                if (!table) return [];
                const rows = Array.from(table.querySelectorAll('tbody tr'));
                const result = [];
                for (const row of rows) {
                    const cells = Array.from(row.querySelectorAll('td'));
                    if (cells.length < 5) continue;

                    const isin = (cells[0]?.textContent || '').trim();
                    const fundName = (cells[1]?.textContent || '').trim();
                    const dateStr = (cells[2]?.textContent || '').trim();
                    const currency = (cells[3]?.textContent || '').trim();
                    const navStr = (cells[4]?.textContent || '').trim();

                    if (!isin || !fundName) continue;

                    const nav = parseFloat(navStr);
                    if (isNaN(nav)) continue;

                    result.push({
                        isin: isin,
                        fund_name: fundName,
                        nav_date: dateStr,
                        currency: currency,
                        nav: nav,
                    });
                }
                return result;
            }""")

            # Deduplicate by ISIN
            seen = set()
            deduped = []
            for f in funds:
                isin = f.get("isin", "")
                if isin and isin not in seen:
                    seen.add(isin)
                    deduped.append(f)

            logger.info(
                f"UBS: parsed {len(funds)} entries, "
                f"{len(deduped)} unique ISINs"
            )
            return deduped

        finally:
            context.close()

    # ── Name Matching ──────────────────────────────────────────

    def _match_fund_name(self, conn, extracted_name: str) -> Optional[int]:
        """Match UBS fund name to hk_funds.id.

        Website names follow patterns like:
          - "UBS (HK) Fund Series - Asia Allocation Opportunity (USD) Class A HKD-mdist"
        """
        if not extracted_name:
            return None

        name = extracted_name.strip()

        # Strip share class suffix: "Class A XXX-mdist/acc"
        name = re.sub(r'\s+Class\s+[A-Z]\s+.*$', '', name, flags=re.IGNORECASE)

        candidates = [name]

        # Try without "UBS (HK) Fund Series - " prefix
        for prefix in [
            "UBS (HK) Fund Series - ",
            "UBS (Lux) Fund Solutions - ",
            "UBS ",
        ]:
            if name.lower().startswith(prefix.lower()):
                stripped = name[len(prefix):]
                if stripped not in candidates:
                    candidates.append(stripped)

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
                       {self._UBS_MANAGER_SQL}
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
                    "fund", "class", "etf", "accumulation",
                    "distribution", "series",
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
                       {self._UBS_MANAGER_SQL}
                       LIMIT 1""",
                    params,
                ).fetchone()
                if row:
                    return row[0]

        return None

    # ── Main Interface ─────────────────────────────────────────

    def get_fund_details(self, isin: str) -> Optional[Dict[str, Any]]:
        """Not implemented — individual fund pages are separate."""
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
            all_entries = self.get_fund_list()
            stats["funds_found"] = len(all_entries)

            processed_fund_ids: set = set()
            processed_isins: set = set()

            for idx, entry in enumerate(all_entries):
                isin = entry.get("isin", "")
                fund_name = entry.get("fund_name", "")

                if not isin or not fund_name:
                    continue

                # Match by ISIN first
                hk_fund_id = None
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
                    if (idx + 1) % 30 == 0:
                        logger.info(
                            f"  [{idx + 1}/{len(all_entries)}] "
                            f"Matched={stats['matched']} (no match: {fund_name[:60]})"
                        )
                    continue

                stats["matched"] += 1

                # Set ISIN
                if isin and isin not in processed_isins:
                    update_fund_from_manager(conn, hk_fund_id, {"isin": isin})
                    stats["isins_updated"] += 1
                    processed_isins.add(isin)

                # Store share class
                share_class = entry.get("fund_name", "")  # Full name includes share class
                if isin and share_class:
                    # Extract just the share class part
                    sc_match = re.search(
                        r'Class\s+([A-Z]\s+[^)]+)\)?\s*$',
                        fund_name
                    )
                    sc_name = sc_match.group(0) if sc_match else fund_name.split(" - ")[-1] if " - " in fund_name else ""
                    if sc_name:
                        share_class_data = {
                            "share_class_name": sc_name.strip(),
                            "isin": isin,
                            "currency": entry.get("currency", ""),
                            "source": "ubs_website",
                        }
                        upsert_share_classes(conn, hk_fund_id, [share_class_data])
                        stats["share_classes_stored"] += 1

                # Store fund details (once per fund)
                if hk_fund_id not in processed_fund_ids:
                    data = {
                        "fund_name": fund_name,
                        "isin": isin,
                        "product_url": FUNDS_PAGE_URL,
                        "source_type": "manager_website",
                    }
                    if update_fund_from_manager(conn, hk_fund_id, data):
                        stats["details_updated"] += 1
                    processed_fund_ids.add(hk_fund_id)

                # Store NAV
                nav = entry.get("nav")
                nav_date = entry.get("nav_date", "")
                currency = entry.get("currency", "HKD")
                if nav and nav_date:
                    parsed_date = self._parse_date(nav_date)
                    if parsed_date:
                        n = upsert_nav_history(conn, hk_fund_id, [{
                            "nav": nav,
                            "nav_date": parsed_date,
                            "nav_currency": self._parse_currency(currency),
                            "source": "ubs_website",
                        }])
                        stats["navs_stored"] += n

                if (idx + 1) % 50 == 0:
                    logger.info(
                        f"  [{idx + 1}/{len(all_entries)}] "
                        f"Matched={stats['matched']} "
                        f"ISINs={stats['isins_updated']} "
                        f"NAVs={stats['navs_stored']}"
                    )

            logger.info(
                f"UBS scrape complete: "
                f"Matched={stats['matched']}/{stats['funds_found']}, "
                f"ISINs={stats['isins_updated']}, "
                f"NAVs={stats['navs_stored']}, "
                f"ShareClasses={stats['share_classes_stored']}, "
                f"Details={stats['details_updated']}"
            )

        except Exception as e:
            logger.error(f"UBS scrape failed: {e}")
            stats["errors"] += 1
        finally:
            self._close_browser()

        return stats
