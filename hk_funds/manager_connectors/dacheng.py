"""
Da Cheng International Asset Management connector.

Extracts fund data from dcfund.com.hk via Playwright DOM scraping.
The site is a static ASP.NET website with fund listing pages that display
NAV data directly, and fund detail pages that contain ISIN codes.

Strategy:
  1. Visit the Authorized Funds listing page to get fund names, codes, NAVs
  2. Visit each fund's detail page (/en/{code}/summary/) to get ISINs
  3. Also check Mutual Recognition Fund page
  4. Match to hk_funds by name with Da Cheng manager constraint

CE: ATE045 — DA CHENG INTERNATIONAL ASSET MANAGEMENT COMPANY LIMITED
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from hk_funds.manager_connectors.base import BaseManagerConnector, register_connector

logger = logging.getLogger("hk_funds.manager_connectors.dacheng")

BASE_URL = "https://www.dcfund.com.hk"
FUNDS_LIST_URL = f"{BASE_URL}/en/AuthorizedFund/"
MRF_LIST_URL = f"{BASE_URL}/en/MutualRecognitionFund/"


@register_connector
class DaChengConnector(BaseManagerConnector):
    """Extracts fund data from Da Cheng International HK website."""

    manager_ce_numbers = ["ATE045"]
    base_url = BASE_URL

    request_delay: float = 1.0
    request_timeout: int = 30

    _DACHENG_MANAGER_SQL = (
        "AND (LOWER(fund_manager_name_en) LIKE '%da cheng%'"
        " OR LOWER(fund_manager_name_en) LIKE '%dacheng%')"
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

    def _parse_listing_page(self, page, url: str) -> List[Dict[str, Any]]:
        """Parse a fund listing page for fund names, codes, NAVs, and NAV dates."""
        try:
            page.goto(url, wait_until="networkidle", timeout=30000)
            time.sleep(2)
        except Exception:
            logger.warning(f"Da Cheng: failed to load {url}")
            return []

        return page.evaluate("""() => {
            const result = [];
            const links = Array.from(document.querySelectorAll('a[href*="/summary/"]'));
            for (const link of links) {
                const href = link.href;
                const match = href.match(/\\/en\\/([^\\/]+)\\/summary\\//);
                if (!match) continue;
                const code = match[1];

                const text = link.textContent;
                const lines = text.split(/\\r?\\n/).map(l => l.trim()).filter(l => l);

                // Structure: [category, fund_name, description, "NAV: X.XX", ...]
                const fundName = lines[1]?.trim();
                if (!fundName || fundName.length < 5) continue;

                const entry = {
                    fund_name: fundName,
                    page_code: code,
                    product_url: href,
                    asset_type: lines[0]?.trim() || '',
                };

                // Parse NAV
                for (const line of lines) {
                    const navMatch = line.match(/^NAV:\\s*([\\d.]+)/);
                    if (navMatch) {
                        entry.nav = parseFloat(navMatch[1]);
                    }
                    const navTypeMatch = line.match(/^NAV Type:\\s*(.+)\\s+Date:\\s*([\\d.]+)/);
                    if (navTypeMatch) {
                        const typeStr = navTypeMatch[1];
                        const ccyMatch = typeStr.match(/\\b(USD|HKD|CNY|RMB|EUR|GBP|AUD|NZD|SGD)\\b/);
                        entry.currency = ccyMatch ? (ccyMatch[1] === 'RMB' ? 'CNY' : ccyMatch[1]) : 'USD';
                        entry.nav_date = navTypeMatch[2].replace(/\\./g, '-');
                    }
                }

                result.push(entry);
            }
            return result;
        }""")

    def _parse_fund_detail(self, page, code: str, fund_name: str) -> Optional[Dict[str, Any]]:
        """Parse a fund detail page for ISIN codes."""
        url = f"{BASE_URL}/en/{code}/summary/"

        try:
            page.goto(url, wait_until="load", timeout=30000)
            time.sleep(1)
        except Exception:
            logger.warning(f"Da Cheng: failed to load {url}")
            return None

        raw_html = page.evaluate("document.documentElement.outerHTML")
        isins = re.findall(r'[A-Z]{2}[0-9]{10}', raw_html)

        # Deduplicate
        seen = set()
        unique_isins = []
        for isin in isins:
            if isin not in seen:
                seen.add(isin)
                unique_isins.append(isin)

        body_text = page.inner_text("body")

        # Extract fund type
        ft_match = re.search(
            r'(?:Asset Type|Fund Type)[:\s]*\n?\s*(Equity|Fixed Income|Multi-Asset|Bond|Stock)',
            body_text
        )

        return {
            "fund_name": fund_name,
            "page_code": code,
            "product_url": url,
            "source_type": "manager_website",
            "isins": unique_isins,
            "fund_type": ft_match.group(1) if ft_match else "",
        }

    def get_fund_list(self) -> List[Dict[str, Any]]:
        """Parse listing and detail pages for all Da Cheng HK funds."""
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
            # Step 1: Get fund list with codes and NAVs from authorized funds page
            funds_list = self._parse_listing_page(page, FUNDS_LIST_URL)
            logger.info(f"Da Cheng: found {len(funds_list)} authorized funds")

            # Step 2: Also check MRF page
            mrf_funds = self._parse_listing_page(page, MRF_LIST_URL)
            if mrf_funds:
                logger.info(f"Da Cheng: found {len(mrf_funds)} MRF funds")
                funds_list.extend(mrf_funds)

            # Step 3: Get ISINs from fund detail pages
            for fund in funds_list:
                code = fund.get("page_code", "")
                fund_name = fund.get("fund_name", "")

                if not code:
                    continue

                detail = self._parse_fund_detail(page, code, fund_name)
                if detail:
                    fund["isins"] = detail.get("isins", [])
                    fund["fund_type"] = detail.get("fund_type", "")

                logger.info(
                    f"Da Cheng: {fund_name[:60]} — "
                    f"{len(fund.get('isins', []))} ISINs"
                )

            return funds_list

        finally:
            context.close()

    # ── Name Matching ──────────────────────────────────────────

    def _match_fund_name(self, conn, extracted_name: str) -> Optional[int]:
        """Match Da Cheng fund name to hk_funds.id.

        Website names follow patterns like:
          - "Da Cheng Overseas China Concept Fund"
          - "Da Cheng China Balanced Fund"
          - "Da Cheng Money Market Fund"
        """
        if not extracted_name:
            return None

        name = extracted_name.strip()

        candidates = [name]

        # Try without "Da Cheng " prefix
        for prefix in ["Da Cheng ", "Dacheng "]:
            if name.lower().startswith(prefix.lower()):
                stripped = name[len(prefix):]
                if stripped not in candidates:
                    candidates.append(stripped)

        # Some funds start with "Da Cheng International" on the website
        if "International" in name:
            simplified = re.sub(r'\s+International\s+', ' ', name, flags=re.IGNORECASE)
            if simplified not in candidates:
                candidates.append(simplified)

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
                       {self._DACHENG_MANAGER_SQL}
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
                    "fund", "class", "etf", "dacheng", "cheng",
                    "international", "hong", "kong",
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
                       {self._DACHENG_MANAGER_SQL}
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
                nav = detail.get("nav")
                nav_date = detail.get("nav_date", today)
                currency = detail.get("currency", "USD")

                if not fund_name:
                    stats["errors"] += 1
                    continue

                # Match to SFC register
                hk_fund_id = self._match_fund_name(conn, fund_name)

                if not hk_fund_id:
                    # Try ISIN matching
                    for isin in isins:
                        row = conn.execute(
                            "SELECT id FROM hk_funds WHERE isin = ?",
                            [isin],
                        ).fetchone()
                        if row:
                            hk_fund_id = row[0]
                            break

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
                if detail.get("fund_type"):
                    data["fund_type"] = detail["fund_type"]

                if update_fund_from_manager(conn, hk_fund_id, data):
                    stats["details_updated"] += 1

                # Store ISINs
                for isin in isins:
                    sc_data = {
                        "share_class_name": "",
                        "isin": isin,
                        "currency": self._parse_currency(currency),
                        "source": "dacheng_website",
                    }
                    upsert_share_classes(conn, hk_fund_id, [sc_data])
                    stats["share_classes_stored"] += 1

                    update_fund_from_manager(conn, hk_fund_id, {"isin": isin})
                    stats["isins_updated"] += 1

                # Store NAV
                if nav:
                    parsed_date = self._parse_date(nav_date) or today
                    n = upsert_nav_history(conn, hk_fund_id, [{
                        "nav": nav,
                        "nav_date": parsed_date,
                        "nav_currency": self._parse_currency(currency),
                        "source": "dacheng_website",
                    }])
                    stats["navs_stored"] += n

            logger.info(
                f"Da Cheng scrape complete: "
                f"Matched={stats['matched']}/{stats['funds_found']}, "
                f"ISINs={stats['isins_updated']}, "
                f"NAVs={stats['navs_stored']}, "
                f"ShareClasses={stats['share_classes_stored']}, "
                f"Details={stats['details_updated']}"
            )

        except Exception as e:
            logger.error(f"Da Cheng scrape failed: {e}")
            stats["errors"] += 1
        finally:
            self._close_browser()

        return stats
