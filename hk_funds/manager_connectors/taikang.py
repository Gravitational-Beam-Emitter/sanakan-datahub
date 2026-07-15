"""
Taikang Asset Management connector.

Extracts fund data from hk.taikangasset.cn via Playwright DOM scraping.
The product page is an SPA with fund tabs. On initial load, it shows
data for the default fund with a NAV table (table.table1). ISINs for
all funds are embedded in the page.

Strategy:
  1. Load the product service page with Playwright
  2. Extract all 25+ HK ISINs from page text
  3. Extract current fund's NAV table (share class, currency, date, NAV)
  4. Match to hk_funds by ISIN first, then by name

CE: ARG103 — Taikang Asset Management (Hong Kong) Company Limited
"""

from __future__ import annotations

import logging
import re
import time
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

from hk_funds.manager_connectors.base import BaseManagerConnector, register_connector

logger = logging.getLogger("hk_funds.manager_connectors.taikang")

FUNDS_PAGE_URL = "https://hk.taikangasset.cn/en/product_service/productService.html"


@register_connector
class TaikangConnector(BaseManagerConnector):
    """Extracts fund data from Taikang Asset Management HK website."""

    manager_ce_numbers = ["ARG103"]
    base_url = "https://hk.taikangasset.cn"

    request_delay: float = 1.5
    request_timeout: int = 30

    _TAIKANG_MANAGER_SQL = (
        "AND (LOWER(fund_manager_name_en) LIKE '%taikang%'"
        " OR LOWER(fund_manager_name_en) LIKE '%tai kang%')"
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
        """Load page, extract ISINs and NAV table."""
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

            # Get fund names from the LI > P navigation (real funds only)
            fund_names = page.evaluate("""() => {
                const allLis = document.querySelectorAll('li');
                const funds = [];
                const seen = new Set();
                for (const li of allLis) {
                    const p = li.querySelector('p');
                    if (!p) continue;
                    const name = p.textContent.trim();
                    // Real fund names: contain "Taikang" and are reasonably short
                    if (!name.includes('Taikang')) continue;
                    if (name.length > 100) continue;
                    // Exclude section headers
                    if (name.includes('ETF') && name.includes('Exchange')) continue;
                    if (!seen.has(name)) {
                        seen.add(name);
                        funds.push(name);
                    }
                }
                return funds;
            }""")
            logger.info(f"Taikang: {len(fund_names)} fund tabs found")

            # Extract all HK ISINs from the page
            isins = page.evaluate("""() => {
                const text = document.body.textContent;
                const matches = text.match(/HK\\d{10}/g) || [];
                return [...new Set(matches)];
            }""")
            logger.info(f"Taikang: {len(isins)} unique ISINs")

            # Extract NAV table from the currently visible fund
            nav_entries = page.evaluate("""() => {
                const table = document.querySelector('table.table1');
                if (!table) return [];
                const rows = Array.from(table.querySelectorAll('tbody tr'));
                const result = [];
                for (const row of rows) {
                    const cells = Array.from(row.querySelectorAll('td'));
                    if (cells.length < 4) continue;
                    const shareClass = cells[0]?.textContent?.trim() || '';
                    const currency = cells[1]?.textContent?.trim() || '';
                    const dateStr = cells[2]?.textContent?.trim() || '';
                    const navStr = cells[3]?.textContent?.trim() || '';

                    if (!shareClass || shareClass === 'Class') continue;

                    const nav = parseFloat(navStr);
                    if (isNaN(nav)) continue;

                    result.push({
                        share_class_name: shareClass,
                        currency: currency,
                        nav_date: dateStr,
                        nav: nav,
                    });
                }
                return result;
            }""")

            # Get active fund name from the page header
            active_fund = page.evaluate("""() => {
                // Try to find the current fund name from active tab or page title
                const activeLi = document.querySelector('li.active p');
                if (activeLi) return activeLi.textContent.trim();

                // Look for fund name in the NAV section heading
                const fundTitle = document.querySelector('.id-title, .title-box');
                if (fundTitle) {
                    const text = fundTitle.textContent.trim();
                    // Extract just the fund name part (before "Risk Disclosure")
                    const match = text.match(/^(.+?)(?:Risk Disclosure|Fund Price)/i);
                    return match ? match[1].trim() : text.slice(0, 80);
                }
                return null;
            }""")

            if not active_fund:
                # Fallback: use first fund name from tab list
                active_fund = fund_names[0] if fund_names else "Taikang Fund"

            logger.info(
                f"Taikang: active fund = {active_fund[:60]}, "
                f"{len(nav_entries)} NAV entries"
            )

            # Build fund entries: each share class entry is associated with
            # the active fund name
            funds = []
            for entry in nav_entries:
                funds.append({
                    "fund_name": active_fund,
                    "share_class_name": entry.get("share_class_name", ""),
                    "currency": entry.get("currency", ""),
                    "nav": entry.get("nav"),
                    "nav_date": entry.get("nav_date", ""),
                    "isin": "",  # Individual ISINs can't be mapped per share class
                    "source_type": "manager_website",
                    "product_url": FUNDS_PAGE_URL,
                })

            # Also create entries for unmatched ISINs (other funds without NAV)
            # These ISINs can be matched to hk_funds records
            for isin in isins:
                funds.append({
                    "fund_name": "",  # Unknown which fund this ISIN belongs to
                    "isin": isin,
                    "source_type": "manager_website",
                    "product_url": FUNDS_PAGE_URL,
                })

            return funds

        finally:
            context.close()

    # ── Name Matching ──────────────────────────────────────────

    def _match_fund_name(self, conn, extracted_name: str) -> Optional[int]:
        """Match Taikang fund name to hk_funds.id."""
        if not extracted_name:
            return None

        name = extracted_name.strip()

        candidates = [name]

        for prefix in ["Taikang Kaitai ", "Taikang "]:
            if name.lower().startswith(prefix.lower()):
                stripped = name[len(prefix):]
                if stripped not in candidates:
                    candidates.append(stripped)
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
                       {self._TAIKANG_MANAGER_SQL}
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
                    "fund", "class", "etf", "taikang", "kaitai",
                    "accumulation", "distribution",
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
                       {self._TAIKANG_MANAGER_SQL}
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
        )

        today = date_str or datetime.now().strftime("%Y-%m-%d")
        stats = {
            "funds_found": 0, "matched": 0, "isins_updated": 0,
            "navs_stored": 0, "details_updated": 0, "errors": 0,
        }

        try:
            all_entries = self.get_fund_list()
            stats["funds_found"] = len(all_entries)

            # Process ISIN-based matches first
            seen_fund_ids: set = set()
            processed_isins: set = set()

            for entry in all_entries:
                isin = entry.get("isin", "")
                fund_name = entry.get("fund_name", "")

                hk_fund_id = None

                # ISIN match first
                if isin:
                    row = conn.execute(
                        "SELECT id FROM hk_funds WHERE isin = ?",
                        [isin],
                    ).fetchone()
                    if row:
                        hk_fund_id = row[0]
                        if isin not in processed_isins:
                            stats["isins_updated"] += 1
                            processed_isins.add(isin)

                # Fallback to name matching
                if not hk_fund_id and fund_name:
                    hk_fund_id = self._match_fund_name(conn, fund_name)

                if not hk_fund_id:
                    continue

                stats["matched"] += 1

                # Store fund details once per fund
                if hk_fund_id not in seen_fund_ids:
                    data = {
                        "product_url": FUNDS_PAGE_URL,
                        "source_type": "manager_website",
                    }
                    if fund_name:
                        data["fund_name"] = fund_name
                    if isin:
                        data["isin"] = isin
                    if update_fund_from_manager(conn, hk_fund_id, data):
                        stats["details_updated"] += 1
                    seen_fund_ids.add(hk_fund_id)

                # Store NAV (only for entries with NAV data)
                nav = entry.get("nav")
                nav_date = entry.get("nav_date", "")
                currency = entry.get("currency", "USD")
                if nav and nav_date:
                    parsed_date = self._parse_date(nav_date)
                    if parsed_date:
                        n = upsert_nav_history(conn, hk_fund_id, [{
                            "nav": nav,
                            "nav_date": parsed_date,
                            "nav_currency": self._parse_currency(currency),
                            "source": "taikang_website",
                        }])
                        stats["navs_stored"] += n

            logger.info(
                f"Taikang scrape complete: "
                f"Matched={stats['matched']}/{stats['funds_found']}, "
                f"ISINs={stats['isins_updated']}, "
                f"NAVs={stats['navs_stored']}, "
                f"Details={stats['details_updated']}"
            )

        except Exception as e:
            logger.error(f"Taikang scrape failed: {e}")
            stats["errors"] += 1
        finally:
            self._close_browser()

        return stats
