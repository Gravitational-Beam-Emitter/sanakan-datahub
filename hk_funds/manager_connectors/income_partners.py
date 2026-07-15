"""
Income Partners Asset Management (HK) connector.

Extracts fund data from incomepartners.com via Playwright DOM scraping.
The site is a static HTML website with server-side rendered fund data.

Strategy:
  1. Visit the SFC-authorized public funds listing page
  2. Discover fund detail page URLs
  3. Visit each fund detail page
  4. Extract NAV table (Value Date, Share Class, ISIN Code, CCY, NAV)
  5. Extract fund info (Inception Date, Fund AUM, Share Class details)
  6. Match to hk_funds by ISIN first, then by name with Income Partners constraint

CE: ABT605 — Income Partners Asset Management (HK) Limited
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from hk_funds.manager_connectors.base import BaseManagerConnector, register_connector

logger = logging.getLogger("hk_funds.manager_connectors.income_partners")

BASE_URL = "https://www.incomepartners.com"
FUNDS_LISTING_URL = f"{BASE_URL}/products/public-funds-sfc-authorized"


@register_connector
class IncomePartnersConnector(BaseManagerConnector):
    """Extracts fund data from Income Partners Asset Management HK website."""

    manager_ce_numbers = ["ABT605"]
    base_url = BASE_URL

    request_delay: float = 1.0
    request_timeout: int = 30

    _IP_MANAGER_SQL = (
        "AND (LOWER(fund_manager_name_en) LIKE '%income partners%'"
        " OR LOWER(fund_manager_name_en) LIKE '%incomepartners%')"
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

    # ── Fund Discovery ─────────────────────────────────────────

    def _discover_fund_urls(self, page) -> List[Dict[str, str]]:
        """Discover fund detail page URLs from the public funds listing page."""
        page.goto(FUNDS_LISTING_URL, wait_until="networkidle", timeout=30000)
        time.sleep(3)

        return page.evaluate("""(baseUrl) => {
            const links = Array.from(document.querySelectorAll('a'));
            const seen = new Set();
            const result = [];
            for (const link of links) {
                const href = link.href;
                if (href.includes('/products/public-funds-sfc-authorized/') &&
                    !href.endsWith('/public-funds-sfc-authorized') &&
                    !href.endsWith('/public-funds-sfc-authorized/') &&
                    !seen.has(href)) {
                    seen.add(href);
                    result.push({url: href});
                }
            }
            return result;
        }""", BASE_URL)

    # ── Fund Detail Parsing ────────────────────────────────────

    def _parse_fund_detail(self, page, url: str) -> Optional[Dict[str, Any]]:
        """Parse a fund detail page for fund name, NAV table, and fund info."""
        try:
            page.goto(url, wait_until="networkidle", timeout=30000)
            time.sleep(3)
        except Exception:
            logger.warning(f"Income Partners: failed to load {url}")
            return None

        # Extract fund name from H1
        fund_name = page.evaluate("""() => {
            const h1 = document.querySelector('h1');
            return h1 ? h1.textContent.trim() : '';
        }""")

        if not fund_name:
            # Fallback: page title
            title = page.title()
            if '|' in title:
                fund_name = title.split('|')[0].strip()

        # Extract NAV table
        nav_table = page.evaluate("""() => {
            const tables = document.querySelectorAll('table');
            for (const table of tables) {
                const rows = Array.from(table.querySelectorAll('tr'));
                if (rows.length < 2) continue;
                const headers = Array.from(rows[0].querySelectorAll('td, th'))
                    .map(c => (c.textContent || '').trim());
                if (headers.includes('Value Date') &&
                    headers.includes('ISIN Code') &&
                    headers.includes('NAV')) {
                    return Array.from(rows).slice(1).map(r =>
                        Array.from(r.querySelectorAll('td, th'))
                            .map(c => (c.textContent || '').trim())
                    );
                }
            }
            return null;
        }""")

        # Extract fund info table (first table with Inception Date)
        fund_info = page.evaluate("""() => {
            const tables = document.querySelectorAll('table');
            for (const table of tables) {
                const rows = Array.from(table.querySelectorAll('tr'));
                if (rows.length < 2) continue;
                const firstCell = (rows[0].querySelector('td, th')?.textContent || '').trim();
                if (firstCell === 'Inception Date') {
                    const result = {};
                    for (const row of rows) {
                        const cells = Array.from(row.querySelectorAll('td, th'));
                        if (cells.length >= 2) {
                            const key = (cells[0]?.textContent || '').trim().replace(/:$/, '');
                            const val = (cells[1]?.textContent || '').trim();
                            result[key] = val;
                        }
                    }
                    return result;
                }
            }
            return null;
        }""")

        # Parse NAV entries
        nav_entries = []
        share_classes = []
        isins = []
        seen_isins = set()

        if nav_table:
            # Headers: Value Date, Share Class, ISIN Code, CCY, NAV
            for row in nav_table:
                if len(row) < 5:
                    continue
                date_str = row[0]
                sc_name = row[1]
                isin = row[2]
                ccy = row[3]
                nav_str = row[4]

                # Validate ISIN
                if not re.match(r'^HK\d{10}$', isin):
                    continue

                # Parse NAV
                try:
                    nav_val = float(nav_str.replace(',', ''))
                except (ValueError, TypeError):
                    continue

                # Parse date
                parsed_date = self._parse_date(date_str) or date_str

                if isin not in seen_isins:
                    seen_isins.add(isin)
                    isins.append(isin)

                    share_classes.append({
                        "share_class_name": sc_name,
                        "isin": isin,
                        "currency": self._parse_currency(ccy),
                    })

                nav_entries.append({
                    "nav": nav_val,
                    "nav_date": parsed_date,
                    "nav_currency": self._parse_currency(ccy),
                    "source": "income_partners_website",
                })

        # Build result
        result = {
            "fund_name": fund_name,
            "product_url": url,
            "source_type": "manager_website",
            "isins": isins,
            "share_classes": share_classes,
            "nav_entries": nav_entries,
        }

        # Add fund info
        if fund_info:
            if fund_info.get("Inception Date"):
                parsed = self._parse_date(fund_info["Inception Date"])
                if parsed:
                    result["fund_inception_date"] = parsed

            # Base currency from share class info
            share_class_info = fund_info.get("Share Class", "")
            if "USD" in share_class_info:
                result["base_currency"] = "USD"

            # Management fee
            mgmt_fee = fund_info.get("Management Fee", "")
            if mgmt_fee:
                result["management_fee"] = mgmt_fee

        # Determine base currency from NAV entries
        if "base_currency" not in result and share_classes:
            result["base_currency"] = share_classes[0].get("currency", "USD")

        return result

    def get_fund_list(self) -> List[Dict[str, Any]]:
        """Discover and parse all Income Partners fund pages."""
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
            fund_urls = self._discover_fund_urls(page)
            logger.info(f"Income Partners: discovered {len(fund_urls)} fund URLs")

            funds = []
            for fu in fund_urls:
                url = fu["url"]
                detail = self._parse_fund_detail(page, url)
                if detail:
                    # Skip terminated funds with no NAV data
                    nav_count = len(detail.get("nav_entries", []))
                    logger.info(
                        f"Income Partners: {detail['fund_name'][:60]} — "
                        f"{len(detail.get('isins', []))} ISINs, "
                        f"{nav_count} NAVs"
                    )
                    funds.append(detail)

            return funds

        finally:
            context.close()

    # ── Name Matching ──────────────────────────────────────────

    def _match_fund_name(self, conn, extracted_name: str) -> Optional[int]:
        """Match Income Partners fund name to hk_funds.id.

        Website names follow patterns like:
          - "Income Partners Managed Volatility High Yield Bond Fund"
          - "Income Partners RMB Bond Fund"
        """
        if not extracted_name:
            return None

        name = extracted_name.strip()

        # Strip "(Terminated)" suffix
        name = re.sub(r'\s*\(Terminated\)\s*', '', name)

        candidates = [name]

        # Try without "Income Partners " prefix
        for prefix in ["Income Partners "]:
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
                       {self._IP_MANAGER_SQL}
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
                    "fund", "class", "etf", "income", "partners",
                    "managed", "volatility", "high", "yield", "bond",
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
                       {self._IP_MANAGER_SQL}
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
                if detail.get("management_fee"):
                    data["management_fee"] = detail["management_fee"]

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
                        "source": "income_partners_website",
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
                    n = upsert_nav_history(conn, hk_fund_id, nav_entries)
                    stats["navs_stored"] += n

            logger.info(
                f"Income Partners scrape complete: "
                f"Matched={stats['matched']}/{stats['funds_found']}, "
                f"ISINs={stats['isins_updated']}, "
                f"NAVs={stats['navs_stored']}, "
                f"ShareClasses={stats['share_classes_stored']}, "
                f"Details={stats['details_updated']}"
            )

        except Exception as e:
            logger.error(f"Income Partners scrape failed: {e}")
            stats["errors"] += 1
        finally:
            self._close_browser()

        return stats
