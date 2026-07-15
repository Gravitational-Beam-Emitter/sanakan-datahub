"""
PineBridge Investments connector.

Extracts fund data from pinebridge.com UCITS fund literature page via
Playwright DOM scraping. The page has a static HTML table with ISINs,
NAVs, dates, share classes, and fund names.

Strategy:
  1. Load the UCITS fund literature page with Playwright
  2. Dismiss cookie consent + country/role filter overlays
  3. Parse the HTML table for fund names, ISINs, NAVs, dates
  4. Deduplicate by ISIN
  5. Match to hk_funds by ISIN first, then by name

CE: AFD869 — PineBridge Investments Asia Limited
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from hk_funds.manager_connectors.base import BaseManagerConnector, register_connector

logger = logging.getLogger("hk_funds.manager_connectors.pinebridge")

FUNDS_PAGE_URL = "https://www.pinebridge.com/en/funds/ucits-fund-literature"


@register_connector
class PineBridgeConnector(BaseManagerConnector):
    """Extracts fund data from PineBridge's UCITS fund literature page."""

    manager_ce_numbers = ["AFD869"]
    base_url = "https://www.pinebridge.com"

    request_delay: float = 1.0
    request_timeout: int = 30

    _PINEBRIDGE_MANAGER_SQL = (
        "AND (LOWER(fund_manager_name_en) LIKE '%pinebridge%'"
        " OR LOWER(fund_manager_name_en) LIKE '%pine bridge%')"
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

    # ── Page setup ──────────────────────────────────────────────

    def _dismiss_overlays(self, page):
        """Dismiss cookie banner, region selector, and other overlays."""
        # Accept cookies
        try:
            btn = page.locator('button:has-text("Accept"), button:has-text("Accept All")')
            if btn.is_visible(timeout=3000):
                btn.first.click()
                time.sleep(1)
        except Exception:
            pass

        # Dismiss MetLife / "Continue to PineBridge" banner
        try:
            btn = page.locator('button:has-text("Continue"), button:has-text("Proceed")')
            if btn.is_visible(timeout=3000):
                btn.first.click()
                time.sleep(1)
        except Exception:
            pass

        # Handle region selector dropdown — select "Hong Kong" or "Asia" if present
        try:
            select = page.locator('select, .region-selector')
            if select.is_visible(timeout=2000):
                # Try to find and select Hong Kong option
                page.evaluate("""() => {
                    const select = document.querySelector('select');
                    if (!select) return;
                    const options = Array.from(select.options);
                    const hk = options.find(o =>
                        o.textContent.includes('Hong Kong') ||
                        o.textContent.includes('Asia')
                    );
                    if (hk) {
                        select.value = hk.value;
                        select.dispatchEvent(new Event('change', {bubbles: true}));
                    }
                }""")
                time.sleep(2)
        except Exception:
            pass

        # Remove any remaining overlays from DOM
        try:
            page.evaluate("""() => {
                const overlays = document.querySelectorAll(
                    '.modal-backdrop, .overlay, [class*=cookie], [class*=banner], [class*=popup]'
                );
                overlays.forEach(el => el.remove());
                document.body.style.overflow = 'auto';
            }""")
            time.sleep(1)
        except Exception:
            pass

    # ── Fund List ───────────────────────────────────────────────

    def get_fund_list(self) -> List[Dict[str, Any]]:
        """Load UCITS fund literature page, parse the table for ISINs and NAVs."""
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
            self._dismiss_overlays(page)

            # Wait for table
            try:
                page.wait_for_selector('table', timeout=10000)
            except Exception:
                logger.warning("Fund table did not load on PineBridge")
                return []
            time.sleep(2)

            funds = page.evaluate("""() => {
                const tables = document.querySelectorAll('table');
                if (!tables.length) return [];

                // Find the table with most rows
                let bestTable = tables[0];
                let bestRows = 0;
                for (const t of tables) {
                    const rows = t.querySelectorAll('tbody tr');
                    if (rows.length > bestRows) {
                        bestRows = rows.length;
                        bestTable = t;
                    }
                }

                const rows = Array.from(bestTable.querySelectorAll('tbody tr'));
                const result = [];

                for (const row of rows) {
                    const cells = Array.from(row.querySelectorAll('td'));
                    if (cells.length < 7) continue;

                    // Fund name: first cell or link within it
                    const nameEl = row.querySelector('.text-pinebridgeblue-50, span.font-normal, a[href*="/funds/"]');
                    const fundName = nameEl
                        ? nameEl.textContent.trim()
                        : (cells[0]?.textContent?.trim() || '');

                    const shareClass = cells[1]?.textContent?.trim() || '';
                    const isin = cells[2]?.textContent?.trim() || '';
                    const navStr = cells[6]?.textContent?.trim() || '';
                    const dateStr = cells[7]?.textContent?.trim() || '';

                    // Validate ISIN format
                    if (!isin || !/^[A-Z]{2}[A-Z0-9]{10}$/.test(isin)) continue;
                    if (!fundName) continue;

                    const fund = {
                        fund_name: fundName,
                        share_class_name: shareClass,
                        isin: isin,
                        nav_date: dateStr,
                        source_type: 'manager_website',
                        product_url: '""" + FUNDS_PAGE_URL + """',
                    };

                    // Parse NAV
                    if (navStr && navStr !== '-' && navStr !== 'N/A') {
                        const navVal = parseFloat(navStr.replace(/,/g, ''));
                        if (!isNaN(navVal)) fund.nav = navVal;
                    }

                    result.push(fund);
                }

                return result;
            }""")

            # Deduplicate by ISIN — keep first occurrence
            seen_isins = set()
            deduped = []
            for f in funds:
                isin = f.get("isin", "")
                if isin and isin not in seen_isins:
                    seen_isins.add(isin)
                    deduped.append(f)

            logger.info(
                f"PineBridge: parsed {len(funds)} entries, "
                f"{len(deduped)} unique ISINs"
            )
            return deduped

        finally:
            context.close()

    # ── Name Matching ──────────────────────────────────────────

    def _match_fund_name(self, conn, extracted_name: str) -> Optional[int]:
        """Match PineBridge fund name to hk_funds.id.

        Website names follow patterns like:
          - "PineBridge Asia ex Japan Small Cap Equity Fund"
          - "PineBridge Global Funds - PineBridge Asia Pacific Investment Grade Bond Fund"
        """
        if not extracted_name:
            return None

        name = extracted_name.strip()

        # Remove trailing "Y" or class letters
        name = re.sub(r'\s+[A-IY]\s*$', '', name)
        name = re.sub(r'\s+Class\s+[A-Z]\s*$', '', name, flags=re.IGNORECASE)

        # Handle "PineBridge Global Funds - XXX" pattern
        candidates = [name]
        pgf_prefix = "PineBridge Global Funds - "
        if name.upper().startswith(pgf_prefix.upper()):
            candidates.append(name[len(pgf_prefix):])

        # Strip "PineBridge " prefix
        for prefix in ["PineBridge "]:
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
                       {self._PINEBRIDGE_MANAGER_SQL}
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
                    "fund", "class", "etf", "pinebridge", "pine", "bridge",
                    "global", "investment", "accumulation", "distribution",
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
                       {self._PINEBRIDGE_MANAGER_SQL}
                       LIMIT 1""",
                    params,
                ).fetchone()
                if row:
                    return row[0]

        return None

    # ── Main Interface ─────────────────────────────────────────

    def get_fund_details(self, isin: str) -> Optional[Dict[str, Any]]:
        """Not implemented — individual fund pages are separate routes."""
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
                fund_name = entry.get("fund_name", "")
                isin = entry.get("isin", "")

                if not fund_name or not isin:
                    continue

                # Match to SFC register — ISIN first
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
                sc_name = entry.get("share_class_name", "")
                if isin and sc_name:
                    share_class_data = {
                        "share_class_name": sc_name,
                        "isin": isin,
                        "source": "pinebridge_website",
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
                if nav and nav_date:
                    parsed_date = self._parse_date(nav_date)
                    if parsed_date:
                        n = upsert_nav_history(conn, hk_fund_id, [{
                            "nav": nav,
                            "nav_date": parsed_date,
                            "nav_currency": "USD",
                            "source": "pinebridge_website",
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
                f"PineBridge scrape complete: "
                f"Matched={stats['matched']}/{stats['funds_found']}, "
                f"ISINs={stats['isins_updated']}, "
                f"NAVs={stats['navs_stored']}, "
                f"ShareClasses={stats['share_classes_stored']}, "
                f"Details={stats['details_updated']}"
            )

        except Exception as e:
            logger.error(f"PineBridge scrape failed: {e}")
            stats["errors"] += 1
        finally:
            self._close_browser()

        return stats
