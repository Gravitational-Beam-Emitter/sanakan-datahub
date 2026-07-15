"""
Principal Asset Management connector.

Extracts fund data from principal.com.hk mutual funds page via
Playwright DOM scraping. The page uses DataTables with a Bootstrap
disclaimer modal (Accept button). No ISINs — name-based matching only.

Strategy:
  1. Load the mutual funds page with Playwright
  2. Click Accept on the disclaimer modal (.js-modal-page-ok-button)
  3. Set DataTables to show all rows
  4. Parse the table: col[0]=fund name, col[2]=currency, col[3]=NAV
  5. Extract "as of DD-MM-YYYY" date from page text
  6. Match to hk_funds by name (no ISIN available)

CE: AFA235 — Principal Asset Management Company (Asia) Limited
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from hk_funds.manager_connectors.base import BaseManagerConnector, register_connector

logger = logging.getLogger("hk_funds.manager_connectors.principal")

FUNDS_PAGE_URL = "https://www.principal.com.hk/fund-finder/mutual-funds"


@register_connector
class PrincipalConnector(BaseManagerConnector):
    """Extracts fund data from Principal's mutual funds page using Playwright.

    Table columns (fixed structure):
      [0] Fund Name (series name + specific fund name concatenated)
      [1] Fund ID (internal code, not ISIN)
      [2] Currency (USD, HKD, CNY, etc.)
      [3] NAV ($ prefixed)
      [4] Series Name
      [5] Asset Class
      [6] Region
      [7] Fact Sheet link
    """

    manager_ce_numbers = ["AFA235"]
    base_url = "https://www.principal.com.hk"

    request_delay: float = 1.0
    request_timeout: int = 30

    _PRINCIPAL_MANAGER_SQL = (
        "AND (LOWER(fund_manager_name_en) LIKE '%principal%')"
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

    def _accept_disclaimer(self, page) -> bool:
        """Click the Accept button on the disclaimer modal.

        The modal uses Bootstrap's modal with class js-modal-page-ok-button.
        """
        try:
            btn = page.locator('.js-modal-page-ok-button')
            if btn.is_visible(timeout=5000):
                btn.click()
                time.sleep(3)
                return True
        except Exception:
            pass

        # Fallback: remove modal from DOM
        try:
            page.evaluate("""() => {
                const modals = document.querySelectorAll(
                    '.modal, .modal-backdrop, [class*=modal]'
                );
                modals.forEach(el => el.remove());
                document.body.classList.remove('modal-open');
                document.body.style.overflow = 'auto';
            }""")
            time.sleep(1)
        except Exception:
            pass
        return False

    def _extract_as_of_date(self, page) -> Optional[str]:
        """Extract 'as of DD-MM-YYYY' date from page text."""
        return page.evaluate("""() => {
            const body = document.body.textContent;
            const match = body.match(/as of (\\d{1,2}-\\d{1,2}-\\d{2,4})/i);
            return match ? match[1] : null;
        }""")

    # ── Fund List ───────────────────────────────────────────────

    def get_fund_list(self) -> List[Dict[str, Any]]:
        """Load mutual funds page, accept disclaimer, parse DataTables."""
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

            # Remove any lingering backdrop
            page.evaluate("""() => {
                document.querySelectorAll('.modal-backdrop').forEach(el => el.remove());
                document.body.classList.remove('modal-open');
            }""")
            time.sleep(1)

            # Wait for table to appear
            try:
                page.wait_for_selector('table tbody tr', timeout=10000)
            except Exception:
                logger.warning("Fund table did not load on Principal")
                return []

            # Extract "as of" date from page
            as_of_date = self._extract_as_of_date(page)
            if as_of_date:
                logger.info(f"Principal: NAV date = {as_of_date}")

            # Iterate through all paginated pages
            all_funds = []
            seen_dedup = set()
            page_num = 1

            while True:
                # Extract funds from current page
                funds = page.evaluate("""(asOfDate) => {
                    const tables = document.querySelectorAll('table');
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
                        if (cells.length < 4) continue;

                        // Cell 0 HTML format:
                        // <span class="subtext">Series Name</span><br>
                        // Fund Name<br>
                        // <span class="subtext">Asset Class</span>
                        const cell0 = cells[0];
                        const html = cell0.innerHTML || '';
                        const brParts = html.split(/<br\\s*\\/?>/i);

                        let fundName = '';
                        if (brParts.length >= 2) {
                            fundName = brParts[1].replace(/<[^>]+>/g, '').trim();
                        } else {
                            fundName = cell0.textContent.trim();
                        }

                        // Strip long footnote like "* (*The name of the fund is not indicative of..."
                        fundName = fundName.replace(/\*?\s*\(\*[^)]*\)\s*$/, '').trim();

                        if (!fundName) continue;

                        const currency = (cells[2]?.textContent || '').trim();
                        const navStr = (cells[3]?.textContent || '').trim();
                        const nav = parseFloat(navStr.replace(/[$,\\s]/g, ''));
                        const assetClass = (cells[5]?.textContent || '').trim();
                        const region = (cells[6]?.textContent || '').trim();

                        const fund = {
                            fund_name: fundName,
                            currency: currency,
                            nav_date: asOfDate || '',
                            asset_class: assetClass,
                            region: region,
                            source_type: 'manager_website',
                            product_url: '""" + FUNDS_PAGE_URL + """',
                        };

                        if (!isNaN(nav) && nav > 0) fund.nav = nav;

                        result.push(fund);
                    }

                    return result;
                }""", as_of_date)

                # Deduplicate entries
                new_count = 0
                for f in funds:
                    key = f.get("fund_name", "") + "|" + f.get("currency", "")
                    if key not in seen_dedup:
                        seen_dedup.add(key)
                        all_funds.append(f)
                        new_count += 1

                logger.info(
                    f"Principal page {page_num}: {len(funds)} entries, "
                    f"{new_count} new (total unique: {len(all_funds)})"
                )

                if new_count == 0:
                    break

                # Try to go to next page (multiple paginations on page for
                # different tabs — use .first to target the main fund table)
                try:
                    next_page_el = page.locator(
                        f'.pagination a.page:has-text("{page_num + 1}")'
                    ).first
                    if next_page_el.is_visible(timeout=1000):
                        next_page_el.click()
                        time.sleep(2)
                        page_num += 1
                        continue
                except Exception:
                    pass

                # No more pages
                break

            logger.info(f"Principal: parsed {len(all_funds)} funds from {page_num} pages")
            return all_funds

        finally:
            context.close()

    # ── Name Matching ──────────────────────────────────────────

    def _match_fund_name(self, conn, extracted_name: str) -> Optional[int]:
        """Match Principal fund name to hk_funds.id.

        Website names follow patterns like:
          - "Principal Sustainable Asia Equity Income Fund - Income (monthly) Class Units - Retail (USD)"
          - "CCB Principal China New Energy Innovation Fund – A Class Acc Units (USD)"
          - "European Equity Fund - A Class Acc Units (USD)"
        """
        if not extracted_name:
            return None

        name = extracted_name.strip()

        # Remove class suffix: "- Income (monthly) Class Units - Retail (USD)"
        name = re.sub(
            r'\s*[-–]\s*(Income|Accumulation|Distribution)\s*(\([^)]+\))?\s*Class\s+Units.*$',
            '', name, flags=re.IGNORECASE,
        )
        name = re.sub(
            r'\s*[-–]\s*[A-I]\s+Class\s+(Acc|Inc|Dis)\s+Units?\s*.*$',
            '', name, flags=re.IGNORECASE,
        )
        name = re.sub(r'\s*[-–]\s*Class\s+[A-I]\s*.*$', '', name, flags=re.IGNORECASE)

        candidates = [name]

        # Strip common prefixes
        for prefix in [
            "Principal Global Investors Funds ",
            "CCB Principal ",
            "Principal Prosperity Series ",
            "Principal ",
            "CCB ",
        ]:
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
                       {self._PRINCIPAL_MANAGER_SQL}
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
                    "fund", "class", "etf", "principal", "global",
                    "investors", "accumulation", "distribution",
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
                       {self._PRINCIPAL_MANAGER_SQL}
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

            processed_fund_ids: set = set()

            for idx, entry in enumerate(entries):
                fund_name = entry.get("fund_name", "")

                if not fund_name:
                    continue

                hk_fund_id = self._match_fund_name(conn, fund_name)

                if not hk_fund_id:
                    if (idx + 1) % 10 == 0:
                        logger.info(
                            f"  [{idx + 1}/{len(entries)}] "
                            f"Matched={stats['matched']} (no match: {fund_name[:60]})"
                        )
                    continue

                stats["matched"] += 1

                data = {
                    "fund_name": fund_name,
                    "nav_currency": entry.get("currency"),
                    "asset_class": entry.get("asset_class"),
                    "product_url": FUNDS_PAGE_URL,
                    "source_type": "manager_website",
                }

                if hk_fund_id not in processed_fund_ids:
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
                            "nav_currency": currency,
                            "source": "principal_website",
                        }])
                        stats["navs_stored"] += n

                if (idx + 1) % 20 == 0:
                    logger.info(
                        f"  [{idx + 1}/{len(entries)}] "
                        f"Matched={stats['matched']} "
                        f"NAVs={stats['navs_stored']}"
                    )

            logger.info(
                f"Principal scrape complete: "
                f"Matched={stats['matched']}/{stats['funds_found']}, "
                f"NAVs={stats['navs_stored']}, "
                f"Details={stats['details_updated']}"
            )

        except Exception as e:
            logger.error(f"Principal scrape failed: {e}")
            stats["errors"] += 1
        finally:
            self._close_browser()

        return stats
