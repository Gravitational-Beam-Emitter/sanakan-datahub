"""
CITIC Securities / CLSA Asset Management connector.

Extracts fund data from clsa.com via Playwright DOM scraping.
Fund detail pages have NAV Per Share tables with share class, currency,
NAV, and date. Fund Information section contains metadata.

Strategy:
  1. Visit the Asset Management page to discover fund detail URLs
  2. Accept the Important Notes modal
  3. Parse each fund detail page for NAVs, share classes, and metadata
  4. Match to hk_funds by name with CITIC/CLSA manager constraint

Note: ISIN codes are NOT available on the website (only in downloadable PDFs).
Matching is done by fund name.

CE: ARE947 — CITIC Securities Asset Management (HK) Limited
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from hk_funds.manager_connectors.base import BaseManagerConnector, register_connector

logger = logging.getLogger("hk_funds.manager_connectors.clsa")

ASSET_MGMT_URL = "https://www.clsa.com/services/assets-management/"


@register_connector
class CLSAConnector(BaseManagerConnector):
    """Extracts fund data from CLSA / CITIC Securities AM website."""

    manager_ce_numbers = ["ARE947"]
    base_url = "https://www.clsa.com"

    request_delay: float = 1.0
    request_timeout: int = 30

    _CLSA_MANAGER_SQL = (
        "AND (LOWER(fund_manager_name_en) LIKE '%citic%'"
        " OR LOWER(fund_manager_name_en) LIKE '%clsa%')"
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

    def _accept_terms(self, page):
        """Accept the Important Notes modal."""
        try:
            btn = page.locator('button:has-text("Accept"), a:has-text("Accept")')
            if btn.is_visible(timeout=5000):
                btn.first.click()
                time.sleep(1)
                return True
        except Exception:
            pass
        return False

    # ── Fund List ───────────────────────────────────────────────

    def _discover_fund_urls(self, page) -> List[Dict[str, str]]:
        """Discover fund detail page URLs from the Asset Management page."""
        page.goto(ASSET_MGMT_URL, wait_until="networkidle", timeout=30000)
        time.sleep(2)

        self._accept_terms(page)
        time.sleep(2)

        return page.evaluate("""() => {
            const links = Array.from(document.querySelectorAll('a'));
            const fundLinks = links.filter(a => {
                const text = a.textContent.trim().toUpperCase();
                return text === 'READ MORE' && a.href.includes('/services/assets-management/');
            });
            const result = [];
            for (const link of fundLinks) {
                // Extract fund name from URL slug
                const slug = link.href.split('/').filter(s => s).pop();
                if (!slug) continue;
                const name = slug.replace(/-/g, ' ').replace(/clsa /i, 'CLSA ');
                result.push({
                    url: link.href,
                    name_slug: name,
                });
            }
            return result;
        }""")

    def _parse_fund_detail(self, page, url: str) -> Optional[Dict[str, Any]]:
        """Parse a fund detail page for NAVs, share classes, and metadata."""
        try:
            page.goto(url, wait_until="networkidle", timeout=30000)
            time.sleep(2)
        except Exception:
            logger.warning(f"CLSA: failed to load {url}")
            return None

        self._accept_terms(page)
        time.sleep(1)

        body_text = page.inner_text("body")

        # Extract fund name from page title or H1
        title = page.title()
        fund_name = re.sub(r'\s*[–-]\s*CITIC CLSA', '', title).strip()

        # If page title isn't right, try the page content
        if not fund_name or len(fund_name) < 10:
            name_match = re.search(r'^([A-Z][A-Za-z\s]{10,80})\n', body_text, re.MULTILINE)
            if name_match:
                fund_name = name_match.group(1).strip()

        # Extract NAV table data (each row is a separate table with headers format)
        nav_entries = page.evaluate("""() => {
            const tables = document.querySelectorAll('table');
            const result = [];
            let foundHeader = false;

            for (const table of tables) {
                const rows = Array.from(table.querySelectorAll('tr'));
                if (rows.length === 0) continue;
                const cells = Array.from(rows[0].querySelectorAll('td, th'));

                if (cells.length === 4) {
                    const firstCell = (cells[0]?.textContent || '').trim();
                    // Check if this is the NAV header
                    if (firstCell === 'Share Class' && cells[1]?.textContent?.trim() === 'Currency') {
                        foundHeader = true;
                        continue;
                    }
                    // Data rows after header
                    if (foundHeader && firstCell && firstCell.startsWith('Class ')) {
                        const navVal = parseFloat((cells[2]?.textContent || '').trim());
                        if (!isNaN(navVal) && navVal > 0) {
                            result.push({
                                share_class_name: firstCell,
                                currency: (cells[1]?.textContent || '').trim(),
                                nav: navVal,
                                nav_date: (cells[3]?.textContent || '').trim(),
                            });
                        }
                    }
                }

                // Stop when we hit non-NAV tables
                if (foundHeader && (cells.length !== 4 || !cells[0]?.textContent?.trim()?.startsWith('Class '))) {
                    // Check if it's the header row for a different section
                    if (cells.length === 4 && cells[0]?.textContent?.trim() === 'Share Class') {
                        continue; // another NAV table header
                    }
                    if (result.length > 0) break;
                }
            }
            return result;
        }""")

        # Extract fund metadata
        fund_info = {}

        # Base currency
        cur_match = re.search(r'Base Currency\n\s*(\w+)', body_text)
        if cur_match:
            fund_info["base_currency"] = cur_match.group(1)

        # Inception date
        inc_match = re.search(r'Inception Date\n\s*(.+)', body_text)
        if inc_match:
            parsed = self._parse_date(inc_match.group(1).strip().split('\n')[0])
            if parsed:
                fund_info["fund_inception_date"] = parsed

        # Manager
        mgr_match = re.search(r'Manager:\n\s*(.+)', body_text)
        if mgr_match:
            fund_info["fund_manager"] = mgr_match.group(1).strip().split('\n')[0]

        return {
            "fund_name": fund_name,
            "product_url": url,
            "source_type": "manager_website",
            "nav_entries": nav_entries,
            **fund_info,
        }

    def get_fund_list(self) -> List[Dict[str, Any]]:
        """Discover and parse all CLSA fund pages."""
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
            logger.info(f"CLSA: discovered {len(fund_urls)} fund URLs")

            funds = []
            for fu in fund_urls:
                detail = self._parse_fund_detail(page, fu["url"])
                if detail:
                    funds.append(detail)
                    logger.info(
                        f"CLSA: {detail['fund_name'][:60]} — "
                        f"{len(detail.get('nav_entries', []))} NAV entries"
                    )

            return funds

        finally:
            context.close()

    # ── Name Matching ──────────────────────────────────────────

    def _match_fund_name(self, conn, extracted_name: str) -> Optional[int]:
        """Match CLSA fund name to hk_funds.id.

        Website names follow patterns like:
          - "CLSA Short Duration China Bond Fund"
          - "CLSA Ultra Short Duration Global IG Bond Fund"
          - "CLSA US Dollar Money Market Fund"
        """
        if not extracted_name:
            return None

        name = extracted_name.strip()

        candidates = [name]

        # Try without "CLSA " prefix
        for prefix in ["CLSA "]:
            if name.lower().startswith(prefix.lower()):
                stripped = name[len(prefix):]
                if stripped not in candidates:
                    candidates.append(stripped)

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
                       {self._CLSA_MANAGER_SQL}
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
                    "fund", "class", "etf", "clsa", "citic", "securities",
                    "asset", "management",
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
                       {self._CLSA_MANAGER_SQL}
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
                nav_entries = detail.get("nav_entries", [])
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

                if update_fund_from_manager(conn, hk_fund_id, data):
                    stats["details_updated"] += 1

                # Store NAVs and share classes
                for nav_entry in nav_entries:
                    sc_name = nav_entry.get("share_class_name", "")
                    currency = nav_entry.get("currency", base_ccy)
                    nav = nav_entry.get("nav")
                    nav_date_str = nav_entry.get("nav_date", "")

                    # Store share class
                    if sc_name:
                        sc_data = {
                            "share_class_name": sc_name,
                            "isin": "",
                            "currency": self._parse_currency(currency),
                            "source": "clsa_website",
                        }
                        upsert_share_classes(conn, hk_fund_id, [sc_data])
                        stats["share_classes_stored"] += 1

                    # Store NAV
                    if nav:
                        parsed_date = self._parse_date(nav_date_str) if nav_date_str else today
                        if parsed_date:
                            n = upsert_nav_history(conn, hk_fund_id, [{
                                "nav": nav,
                                "nav_date": parsed_date,
                                "nav_currency": self._parse_currency(currency),
                                "source": "clsa_website",
                            }])
                            stats["navs_stored"] += n

            logger.info(
                f"CLSA scrape complete: "
                f"Matched={stats['matched']}/{stats['funds_found']}, "
                f"ISINs={stats['isins_updated']}, "
                f"NAVs={stats['navs_stored']}, "
                f"ShareClasses={stats['share_classes_stored']}, "
                f"Details={stats['details_updated']}"
            )

        except Exception as e:
            logger.error(f"CLSA scrape failed: {e}")
            stats["errors"] += 1
        finally:
            self._close_browser()

        return stats
