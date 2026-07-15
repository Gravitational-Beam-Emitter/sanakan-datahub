"""
GF International Investment Management connector.

Extracts fund data from gffunds.com.hk via Playwright DOM scraping.
The site is a static HTML site with NAV history tables rendered on
fund detail pages via JavaScript/ECharts.

Strategy:
  1. Accept the disclaimer modal
  2. Visit the Products listing page to get fund names and detail URLs
  3. Visit each fund detail page
  4. Extract NAV history table (Date, Class X, Class Y, ...)
  5. Match to hk_funds by name with GF International manager constraint

Note: ISIN codes are NOT available on the website (only in downloadable PDFs).
Matching is done by fund name.

CE: AXL121 — GF International Investment Management Limited
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from hk_funds.manager_connectors.base import BaseManagerConnector, register_connector

logger = logging.getLogger("hk_funds.manager_connectors.gf_international")

BASE_URL = "http://www.gffunds.com.hk"
PRODUCTS_URL = f"{BASE_URL}/en/jjcp/?v=2.0"


@register_connector
class GFInternationalConnector(BaseManagerConnector):
    """Extracts fund data from GF International Investment Management HK website."""

    manager_ce_numbers = ["AXL121"]
    base_url = BASE_URL

    request_delay: float = 1.0
    request_timeout: int = 30

    _GFI_MANAGER_SQL = (
        "AND (LOWER(fund_manager_name_en) LIKE '%gf international%'"
        " OR LOWER(fund_manager_name_en) LIKE '%gfii%'"
        " OR LOWER(fund_manager_name_en) LIKE '%gfi %')"
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

    def _accept_disclaimer(self, page):
        """Accept the disclaimer modal."""
        try:
            btn = page.locator('a:has-text("Accept")')
            if btn.is_visible(timeout=5000):
                btn.first.click()
                time.sleep(1)
                return True
        except Exception:
            pass
        return False

    # ── Fund List ───────────────────────────────────────────────

    def _discover_funds(self, page) -> List[Dict[str, Any]]:
        """Discover fund names and detail page URLs from the Products listing."""
        page.goto(PRODUCTS_URL, wait_until="networkidle", timeout=30000)
        time.sleep(2)

        self._accept_disclaimer(page)
        time.sleep(2)

        # Reload products page after disclaimer
        page.goto(PRODUCTS_URL, wait_until="networkidle", timeout=30000)
        time.sleep(3)

        return page.evaluate("""() => {
            const result = [];
            const links = Array.from(document.querySelectorAll('a[href*="/funds/"]'));
            const seen = new Set();
            for (const link of links) {
                const href = link.href;
                if (seen.has(href)) continue;
                seen.add(href);

                const match = href.match(/\\/funds\\/(\\d+)\\/(t\\d+_\\d+)\\.shtml/);
                if (!match) continue;

                // Fund name from link text - take the longest meaningful line
                const rawText = link.textContent.trim();
                const lines = rawText.split(/\\r?\\n/).map(l => l.trim()).filter(l =>
                    l.length > 5 && !l.startsWith('NAV') && !l.startsWith('YTD') &&
                    !l.startsWith('Daily') && !l.startsWith('Date') && !l.includes('Seek to') &&
                    !l.includes('investment objective') && l.length < 200
                );
                const fundName = lines.length > 0 ? lines[0] : '';

                if (!fundName || fundName.length < 5) continue;
                if (fundName === 'Equity' || fundName === 'Bond' || fundName === 'Stock' ||
                    fundName === 'Fixed Income' || fundName === 'Multi-Asset Allocation') continue;

                result.push({
                    fund_name: fundName,
                    detail_url: href,
                });
            }
            return result;
        }""")

    def _parse_fund_detail(self, page, url: str) -> Optional[Dict[str, Any]]:
        """Parse a fund detail page for fund name, NAV history table, and metadata."""
        try:
            page.goto(url, wait_until="networkidle", timeout=30000)
            time.sleep(5)  # Wait for ECharts to render and populate the table
        except Exception:
            logger.warning(f"GFI: failed to load {url}")
            return None

        body_text = page.inner_text("body")
        body_lines = [l.strip() for l in body_text.split('\n') if l.strip()]

        # Extract fund name - it's the heading after navigation (line 2 typically)
        fund_name = ""
        for line in body_lines[:6]:
            if len(line) > 20 and len(line) < 200 and \
               ('Fund' in line or 'Trust' in line or 'Series' in line or 'Company' in line) and \
               not line.startswith('GF Fund Management') and not line.startswith('Home ') and \
               not line.startswith('Welcome') and not line.startswith('Important') and \
               not 'www' in line.lower():
                fund_name = line.strip()
                break

        if not fund_name:
            # Try GFI/GF prefix pattern
            fm = re.search(r'(GFI?\s[^\n]{10,80}(?:Fund|Trust|Series|Company|ETF))', body_text)
            if fm:
                fund_name = fm.group(1).strip()

        # Extract NAV history table
        nav_history = page.evaluate("""() => {
            const tables = document.querySelectorAll('table');
            for (const table of tables) {
                const rows = Array.from(table.querySelectorAll('tr'));
                if (rows.length < 3) continue;
                const headers = Array.from(rows[0].querySelectorAll('td, th'))
                    .map(c => (c.textContent || '').trim());

                // Look for NAV history table with Date + Class columns
                if (headers[0] === 'Date' && headers.length >= 3 &&
                    headers.some(h => h.includes('Class') || h.includes('USD') || h.includes('HKD') || h.includes('RMB') || h.includes('CNY'))) {

                    const result = [];
                    for (let i = 1; i < Math.min(rows.length, 500); i++) {
                        const cells = Array.from(rows[i].querySelectorAll('td, th'))
                            .map(c => (c.textContent || '').trim());
                        if (cells.length < 2) continue;
                        result.push(cells);
                    }
                    return {headers: headers, data: result};
                }
            }
            return null;
        }""")

        # Extract latest NAV from "Net Asset Value" section
        latest_nav = None
        nav_date = None
        nav_section = re.search(
            r'Net Asset Value\s*\n\s*([\d.,]+)',
            body_text
        )
        if nav_section:
            try:
                latest_nav = float(nav_section.group(1).replace(',', ''))
            except ValueError:
                pass

        # Extract base currency
        cur_match = re.search(r'Base Currency[:\s]+\n?\s*(\w+)', body_text)
        base_currency = cur_match.group(1) if cur_match else "USD"

        # Extract fund type from listing label
        ft_match = re.search(r'(Equity|Fixed Income|Bond|Money Market|Multi-Asset)', body_text)
        fund_type = ft_match.group(1) if ft_match else ""

        # Get Fund Info table
        fund_info = page.evaluate("""() => {
            const tables = document.querySelectorAll('table');
            for (const table of tables) {
                const rows = Array.from(table.querySelectorAll('tr'));
                if (rows.length < 2) continue;

                const result = {};
                for (const row of rows) {
                    const cells = Array.from(row.querySelectorAll('td, th'));
                    if (cells.length >= 2) {
                        const key = (cells[0]?.textContent || '').trim().replace(':', '').replace(/\\s+/g, ' ');
                        const val = (cells[1]?.textContent || '').trim();
                        if (key && val && key.length < 60) {
                            result[key] = val;
                        }
                    }
                }

                // Look for fund info keys
                if (result['Manager'] || result['Custodian'] || result['Dealing frequency'] || result['Base currency']) {
                    return result;
                }
            }
            return {};
        }""")

        return {
            "fund_name": fund_name,
            "product_url": url,
            "source_type": "manager_website",
            "nav_history": nav_history,
            "latest_nav": latest_nav,
            "nav_date": nav_date,
            "base_currency": base_currency,
            "fund_type": fund_type,
            "fund_info": fund_info,
        }

    def get_fund_list(self) -> List[Dict[str, Any]]:
        """Discover and parse all GF International fund pages."""
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
            # First accept disclaimer
            page.goto(PRODUCTS_URL, wait_until="networkidle", timeout=30000)
            time.sleep(2)
            self._accept_disclaimer(page)
            time.sleep(2)

            # Now discover fund URLs
            funds_list = self._discover_funds(page)
            logger.info(f"GFI: discovered {len(funds_list)} fund links")

            funds = []
            for i, f in enumerate(funds_list):
                url = f.get("detail_url", "")
                if not url:
                    continue

                detail = self._parse_fund_detail(page, url)
                if detail:
                    # Use detail page name if available, otherwise use listing name
                    if not detail.get("fund_name") or len(detail.get("fund_name", "")) < 5:
                        detail["fund_name"] = f.get("fund_name", "")

                    nav_count = len(detail.get("nav_history", {}).get("data", []) if detail.get("nav_history") else [])
                    logger.info(
                        f"GFI: {detail['fund_name'][:60]} — "
                        f"{nav_count} NAV history rows"
                    )
                    funds.append(detail)

            return funds

        finally:
            context.close()

    # ── Name Matching ──────────────────────────────────────────

    def _match_fund_name(self, conn, extracted_name: str) -> Optional[int]:
        """Match GF International fund name to hk_funds.id.

        Website names follow patterns like:
          - "GFI Global Select Equity Fund"
          - "GFI Unit Trust Series - GFI Global Income Bond Fund"
          - "GF Open-ended Fund Company - GF RMB Money Market Fund"
          - "GFI New Perspective Unit Trust Series - GFI New Perspective Bond Fund"
        """
        if not extracted_name:
            return None

        name = extracted_name.strip()

        # Strip umbrella prefixes
        candidates = [name]
        for prefix in [
            "GFI Unit Trust Series - ",
            "GFI New Perspective Unit Trust Series - ",
            "GF Open-ended Fund Company - ",
            "GFI Investment Trust - ",
            "GFI ",
            "GF ",
        ]:
            if name.lower().startswith(prefix.lower()):
                stripped = name[len(prefix):]
                if stripped not in candidates:
                    candidates.append(stripped)

        for candidate in candidates:
            c = re.sub(r"\s+", " ", candidate.lower().strip())
            c = re.sub(r"\s+(fund|class\s+\w+|etf|ofc|sicav)\s*$", "", c, flags=re.IGNORECASE)

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
                       {self._GFI_MANAGER_SQL}
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
                    "fund", "class", "etf", "gfi", "international",
                    "series", "ofc", "unit", "trust", "perspective",
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
                       {self._GFI_MANAGER_SQL}
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
                nav_history = detail.get("nav_history") or {}
                base_ccy = detail.get("base_currency", "USD")

                if not fund_name:
                    stats["errors"] += 1
                    continue

                # Match to SFC register
                hk_fund_id = self._match_fund_name(conn, fund_name)

                if not hk_fund_id:
                    if (idx + 1) % 3 == 0:
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
                if detail.get("fund_type"):
                    data["fund_type"] = detail["fund_type"]

                if update_fund_from_manager(conn, hk_fund_id, data):
                    stats["details_updated"] += 1

                # Store share classes from NAV table headers
                headers = nav_history.get("headers", [])
                share_classes = [h for h in headers[1:] if h and h != 'Date']
                for sc in share_classes:
                    # Extract currency from share class name
                    ccy = base_ccy
                    for cur in ["USD", "HKD", "RMB", "CNY", "EUR", "GBP", "AUD", "NZD", "SGD"]:
                        if cur in sc:
                            ccy = cur if cur != "RMB" else "CNY"
                            break

                    sc_data = {
                        "share_class_name": sc,
                        "isin": "",
                        "currency": self._parse_currency(ccy),
                        "source": "gfi_website",
                    }
                    upsert_share_classes(conn, hk_fund_id, [sc_data])
                    stats["share_classes_stored"] += 1

                # Store NAV history
                nav_rows = nav_history.get("data", [])
                nav_entries = []
                for row in nav_rows:
                    if len(row) < 2:
                        continue
                    date_val = row[0]
                    if not date_val or not re.match(r'\d{4}-\d{2}-\d{2}', date_val):
                        continue

                    # Store one NAV entry per share class per date
                    for i, nav_str in enumerate(row[1:], 1):
                        if i >= len(headers):
                            break
                        sc_name = headers[i]
                        if not sc_name or sc_name == 'Date':
                            continue
                        try:
                            nav_val = float(nav_str.replace(',', ''))
                            # Determine currency
                            ccy = base_ccy
                            for cur in ["USD", "HKD", "RMB", "CNY", "EUR", "GBP", "AUD", "NZD", "SGD"]:
                                if cur in sc_name:
                                    ccy = cur if cur != "RMB" else "CNY"
                                    break
                            nav_entries.append({
                                "nav": nav_val,
                                "nav_date": date_val,
                                "nav_currency": self._parse_currency(ccy),
                                "source": "gfi_website",
                            })
                        except (ValueError, IndexError):
                            continue

                if nav_entries:
                    n = upsert_nav_history(conn, hk_fund_id, nav_entries)
                    stats["navs_stored"] += n

            logger.info(
                f"GF International scrape complete: "
                f"Matched={stats['matched']}/{stats['funds_found']}, "
                f"ISINs={stats['isins_updated']}, "
                f"NAVs={stats['navs_stored']}, "
                f"ShareClasses={stats['share_classes_stored']}, "
                f"Details={stats['details_updated']}"
            )

        except Exception as e:
            logger.error(f"GF International scrape failed: {e}")
            stats["errors"] += 1
        finally:
            self._close_browser()

        return stats
