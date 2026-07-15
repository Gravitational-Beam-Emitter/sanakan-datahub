"""
KGI Asset Management connector.

Extracts fund data from kgi.com.hk via Playwright DOM scraping.
The site is a Sitecore CMS with server-side rendered HTML.

Strategy:
  1. Visit the Asset Management page to discover fund detail URLs
  2. Parse each fund detail page for ISINs, share classes, and metadata
  3. Extract NAV data if available (funds are new, NAV data may be empty)
  4. Match to hk_funds by ISIN first, then by name with KGI manager constraint

CE: AEN441 — KGI Asset Management Limited
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from hk_funds.manager_connectors.base import BaseManagerConnector, register_connector

logger = logging.getLogger("hk_funds.manager_connectors.kgi")

ASSET_MGMT_URL = "https://www.kgi.com.hk/en/asset-management"
FUND_SLUGS = [
    "kgi-global-credit-income-fund",
    "kgi-global-industry-elite-fund",
    "kgi-diversified-growth-income-fund",
]


@register_connector
class KGIConnector(BaseManagerConnector):
    """Extracts fund data from KGI Asset Management HK website."""

    manager_ce_numbers = ["AEN441"]
    base_url = "https://www.kgi.com.hk"

    request_delay: float = 1.0
    request_timeout: int = 30

    _KGI_MANAGER_SQL = (
        "AND (LOWER(fund_manager_name_en) LIKE '%kgi%')"
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

    def _discover_fund_urls(self, page) -> List[Dict[str, str]]:
        """Discover fund detail page URLs from the Asset Management page."""
        page.goto(ASSET_MGMT_URL, wait_until="networkidle", timeout=30000)
        time.sleep(3)

        # Accept cookie banner
        try:
            btn = page.locator('a:has-text("AGREE"), button:has-text("AGREE")')
            if btn.is_visible(timeout=3000):
                btn.first.click()
                time.sleep(1)
        except Exception:
            pass

        return page.evaluate("""() => {
            const links = Array.from(document.querySelectorAll('a[href*="/asset-management/"]'));
            const seen = new Set();
            const result = [];
            for (const link of links) {
                const href = link.href;
                if (href.includes('/asset-management/') &&
                    !href.endsWith('/asset-management') &&
                    !href.endsWith('/asset-management/') &&
                    !seen.has(href)) {
                    seen.add(href);
                    const text = link.textContent.trim();
                    if (text && text.length > 10 && text.includes('KGI')) {
                        result.push({url: href, name: text.split('\\n')[0].trim()});
                    }
                }
            }
            return result;
        }""")

    def _parse_fund_detail(self, page, url: str) -> Optional[Dict[str, Any]]:
        """Parse a fund detail page for ISINs, share classes, and metadata."""
        try:
            page.goto(url, wait_until="networkidle", timeout=30000)
            time.sleep(3)
        except Exception:
            logger.warning(f"KGI: failed to load {url}")
            return None

        # Accept cookie banner
        try:
            btn = page.locator('a:has-text("AGREE"), button:has-text("AGREE")')
            if btn.is_visible(timeout=3000):
                btn.first.click()
                time.sleep(1)
        except Exception:
            pass

        body_text = page.inner_text("body")

        # Extract fund name from H1 or page title
        fund_name = ""
        h1_match = re.search(r'(KGI\s[^\n]{10,100}(?:Fund))', body_text[:500])
        if h1_match:
            fund_name = h1_match.group(1).strip()

        if not fund_name:
            title = page.title()
            title = re.sub(r'\s*\|\s*KGI\s*$', '', title).strip()
            if len(title) > 10:
                fund_name = title

        # Extract ISINs
        isins = re.findall(r'[A-Z]{2}[0-9]{10}', body_text)
        # Deduplicate
        seen = set()
        unique_isins = [i for i in isins if not (i in seen or seen.add(i))]

        # Extract base currency
        base_ccy = "USD"
        ccy_match = re.search(
            r'Base Currency\s*\n?\s*([A-Z][A-Za-z\s]*?)(?:\n|Dealing|\s{2,})',
            body_text
        )
        if ccy_match:
            ccy_text = ccy_match.group(1).strip()
            for cur in ["USD", "HKD", "RMB", "CNY", "EUR", "GBP", "AUD", "SGD"]:
                if cur in ccy_text:
                    base_ccy = cur if cur != "RMB" else "CNY"
                    break

        # Extract manager
        mgr_match = re.search(r'Manager\s+\n?\s*(KGI\s[^\n]+)', body_text)
        fund_manager = mgr_match.group(1).strip() if mgr_match else "KGI Asset Management Limited"

        # Extract share classes from Fund Overview table.
        # Structure: one row with "Share Class" header + class names in cols,
        # another row with "ISIN Code" header + ISINs in corresponding cols.
        share_classes = page.evaluate("""() => {
            const tables = document.querySelectorAll('table');
            const result = [];
            for (const table of tables) {
                const rows = Array.from(table.querySelectorAll('tr'));
                let shareClassRow = null, isinRow = null;
                const cleanText = (s) => (s || '').replace(/\\n\\s*/g, ' ').replace(/\\s+/g, ' ').trim();

                for (let i = 0; i < rows.length; i++) {
                    const cells = Array.from(rows[i].querySelectorAll('td, th'));
                    const firstCell = (cells[0]?.textContent || '').trim();
                    if (firstCell === 'Share Class') shareClassRow = cells.map(c => cleanText(c.textContent));
                    if (firstCell === 'ISIN Code' || firstCell === 'ISIN') isinRow = cells.map(c => cleanText(c.textContent));
                }

                if (shareClassRow && isinRow) {
                    for (let col = 1; col < Math.min(shareClassRow.length, isinRow.length); col++) {
                        const isin = isinRow[col];
                        const name = shareClassRow[col];
                        if (/^[A-Z]{2}[0-9]{10}$/.test(isin) && name) {
                            let currency = 'USD';
                            ['USD', 'HKD', 'RMB', 'CNY', 'EUR', 'GBP', 'AUD', 'SGD'].forEach(c => {
                                if (name.includes(c)) currency = c === 'RMB' ? 'CNY' : c;
                            });
                            result.push({
                                share_class_name: name,
                                isin: isin,
                                currency: currency,
                            });
                        }
                    }
                }

                if (result.length > 0) break;
            }
            return result;
        }""")

        # Extract NAV data
        nav_entries = []
        nav_table = page.evaluate("""() => {
            const tables = document.querySelectorAll('table');
            for (const table of tables) {
                const rows = Array.from(table.querySelectorAll('tr'));
                for (let i = 0; i < Math.min(rows.length, 5); i++) {
                    const cells = Array.from(rows[i].querySelectorAll('td, th'));
                    const headers = cells.map(c => (c.textContent || '').trim());
                    if (headers.includes('Date') &&
                        headers.some(h => h.includes('Class') || h.includes('USD') || h.includes('Shares'))) {

                        const data = [];
                        for (let j = i + 1; j < Math.min(rows.length, 500); j++) {
                            const dataCells = Array.from(rows[j].querySelectorAll('td, th'))
                                .map(c => (c.textContent || '').trim());
                            if (dataCells.length >= 2 &&
                                /\\d{4}-\\d{2}-\\d{2}/.test(dataCells[0])) {
                                data.push(dataCells);
                            }
                        }
                        if (data.length > 0) {
                            return {headers: headers, data: data};
                        }
                    }
                }
            }
            return null;
        }""")

        if nav_table:
            headers = nav_table.get("headers", [])
            data = nav_table.get("data", [])
            for row in data:
                if len(row) < 2:
                    continue
                date_val = row[0]
                for i, nav_str in enumerate(row[1:], 1):
                    if i >= len(headers):
                        break
                    sc_name = headers[i]
                    try:
                        nav_val = float(nav_str.replace(',', ''))
                        ccy = base_ccy
                        for cur in ["USD", "HKD", "RMB", "CNY", "EUR", "GBP", "AUD", "SGD"]:
                            if cur in sc_name:
                                ccy = cur if cur != "RMB" else "CNY"
                                break
                        nav_entries.append({
                            "nav": nav_val,
                            "nav_date": date_val,
                            "nav_currency": self._parse_currency(ccy),
                            "source": "kgi_website",
                        })
                    except (ValueError, IndexError):
                        continue

        # Extract dealing frequency
        dealing_match = re.search(r'Dealing Frequency\s+\n?\s*(.+)', body_text)
        dealing_freq = dealing_match.group(1).strip() if dealing_match else ""

        # Extract management fee
        fee_match = re.search(
            r'Management Fee[^\n]*\n?\s*([\d.]+%\s*p\.a\.)',
            body_text
        )
        mgmt_fee = fee_match.group(1).strip() if fee_match else ""

        return {
            "fund_name": fund_name,
            "product_url": url,
            "source_type": "manager_website",
            "isins": unique_isins,
            "share_classes": share_classes,
            "base_currency": base_ccy,
            "fund_manager": fund_manager,
            "dealing_frequency": dealing_freq,
            "management_fee": mgmt_fee,
            "nav_entries": nav_entries,
        }

    def get_fund_list(self) -> List[Dict[str, Any]]:
        """Discover and parse all KGI fund pages."""
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
            # Try to discover fund URLs dynamically
            fund_urls = self._discover_fund_urls(page)
            logger.info(f"KGI: discovered {len(fund_urls)} fund URLs")

            # If discovery didn't find enough, use known slugs
            if len(fund_urls) < 2:
                fund_urls = [
                    {"url": f"{self.base_url}/en/asset-management/{slug}", "name": slug}
                    for slug in FUND_SLUGS
                ]
                logger.info(f"KGI: using {len(fund_urls)} known fund slugs")

            funds = []
            for fu in fund_urls:
                detail = self._parse_fund_detail(page, fu["url"])
                if detail:
                    funds.append(detail)
                    logger.info(
                        f"KGI: {detail['fund_name'][:60]} — "
                        f"{len(detail.get('isins', []))} ISINs, "
                        f"{len(detail.get('nav_entries', []))} NAVs"
                    )

            return funds

        finally:
            context.close()

    # ── Name Matching ──────────────────────────────────────────

    def _match_fund_name(self, conn, extracted_name: str) -> Optional[int]:
        """Match KGI fund name to hk_funds.id.

        Website names follow patterns like:
          - "KGI Global Credit Income Fund"
          - "KGI Global Industry Elite Fund"
          - "KGI Diversified Growth Income Fund"
        """
        if not extracted_name:
            return None

        name = extracted_name.strip()

        # Strip "KGI " prefix to create alternative candidates
        candidates = [name]
        for prefix in ["KGI "]:
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
                       {self._KGI_MANAGER_SQL}
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
                    "fund", "class", "etf", "kgi", "global",
                    "income", "growth", "elite", "diversified",
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
                       {self._KGI_MANAGER_SQL}
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
                    if (idx + 1) % 5 == 0:
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

                if update_fund_from_manager(conn, hk_fund_id, data):
                    stats["details_updated"] += 1

                # Store ISINs and share classes
                for sc in share_classes:
                    sc_isin = sc.get("isin", "")
                    sc_name = sc.get("share_class_name", "")
                    ccy = sc.get("currency", base_ccy)

                    sc_data = {
                        "share_class_name": sc_name,
                        "isin": sc_isin,
                        "currency": self._parse_currency(ccy),
                        "source": "kgi_website",
                    }
                    upsert_share_classes(conn, hk_fund_id, [sc_data])
                    stats["share_classes_stored"] += 1

                    if sc_isin:
                        update_fund_from_manager(conn, hk_fund_id, {"isin": sc_isin})
                        stats["isins_updated"] += 1

                # Also store ISINs not in share_classes
                for isin in isins:
                    if not any(sc.get("isin") == isin for sc in share_classes):
                        sc_data = {
                            "share_class_name": "",
                            "isin": isin,
                            "currency": self._parse_currency(base_ccy),
                            "source": "kgi_website",
                        }
                        upsert_share_classes(conn, hk_fund_id, [sc_data])
                        stats["share_classes_stored"] += 1
                        update_fund_from_manager(conn, hk_fund_id, {"isin": isin})
                        stats["isins_updated"] += 1

                # Store NAVs
                if nav_entries:
                    n = upsert_nav_history(conn, hk_fund_id, nav_entries)
                    stats["navs_stored"] += n

            logger.info(
                f"KGI scrape complete: "
                f"Matched={stats['matched']}/{stats['funds_found']}, "
                f"ISINs={stats['isins_updated']}, "
                f"NAVs={stats['navs_stored']}, "
                f"ShareClasses={stats['share_classes_stored']}, "
                f"Details={stats['details_updated']}"
            )

        except Exception as e:
            logger.error(f"KGI scrape failed: {e}")
            stats["errors"] += 1
        finally:
            self._close_browser()

        return stats
