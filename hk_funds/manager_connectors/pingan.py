"""
Ping An of China Asset Management (HK) connector.

Extracts fund data from asset.pingan.com.hk via Playwright DOM scraping.
The site has a Terms & Conditions modal that must be accepted first,
then fund pages contain ISINs and NAVs in static HTML tables.

Strategy:
  1. Navigate to the Products and Services page
  2. Accept the Terms & Conditions modal
  3. Discover ETF and Unit Trust fund pages
  4. Parse each fund page for ISIN codes and NAV data
  5. Match to hk_funds by name with Ping An manager constraint

CE: AOD938 — PING AN OF CHINA ASSET MANAGEMENT (HONG KONG) COMPANY LIMITED
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from hk_funds.manager_connectors.base import BaseManagerConnector, register_connector

logger = logging.getLogger("hk_funds.manager_connectors.pingan")

BASE_URL = "https://asset.pingan.com.hk"

# Known fund pages
FUND_PAGES = [
    # ETFs
    ("PACT-PACCHKD", "Ping An of China CSI HK Dividend ETF"),
    ("PACT-PATECHS", "Ping An Technology Select ETF"),
    ("PACT-PAEWS", "Ping An East-West Select ETF"),
    # Unit Trusts
    ("PACSIF-RMBBF", "Ping An of China SIF - RMB Bond Fund"),
    ("PACSIF-PAMMF", "Ping An Money Market Fund"),
    ("PACSIF-PASF", "Ping An Stable Fund"),
    ("PACSIF-PAHKMMF", "Ping An Hong Kong Dollar Money Market Fund"),
]


@register_connector
class PingAnConnector(BaseManagerConnector):
    """Extracts fund data from Ping An Asset Management HK website."""

    manager_ce_numbers = ["AOD938"]
    base_url = BASE_URL

    request_delay: float = 1.5
    request_timeout: int = 30

    _PINGAN_MANAGER_SQL = (
        "AND (LOWER(fund_manager_name_en) LIKE '%ping an%'"
        " OR LOWER(fund_manager_name_en) LIKE '%pingan%')"
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
        """Accept the Terms & Conditions modal."""
        try:
            btn = page.locator('button:has-text("Acknowledge")')
            if btn.is_visible(timeout=5000):
                btn.first.click()
                time.sleep(1)
                return True
        except Exception:
            pass
        return False

    # ── Fund List ───────────────────────────────────────────────

    def _parse_fund_page(self, page, page_code: str) -> Optional[Dict[str, Any]]:
        """Parse a single fund page for ISINs, NAV, and metadata."""
        url = f"{BASE_URL}/en/{page_code}"

        try:
            page.goto(url, wait_until="networkidle", timeout=20000)
            time.sleep(1)
        except Exception:
            logger.warning(f"Ping An: failed to load {url}")
            return None

        # Accept terms if modal appears
        self._accept_terms(page)
        time.sleep(1)

        # Extract fund name from title or page structure
        # Unit Trust: "Ping An of China Select Investment Fund Series -\nPing An XXX Fund"
        # ETF: "PING AN OF CHINA TRUST\nPing An of China XXX ETF (3070/9070)"
        body_text = page.inner_text("body")

        fund_name = ""
        # Try to extract from the fund header area
        ut_match = re.search(
            r'(?:Ping An of China Select Investment Fund Series)[\s-]+(\w)',
            body_text, re.IGNORECASE,
        )
        if ut_match:
            # Look for the actual fund name after the series header
            fn_match = re.search(
                r'Ping An of China Select Investment Fund Series\s*[-–]\s*\n?\s*(Ping An[^\n]{5,60})',
                body_text, re.IGNORECASE,
            )
            if fn_match:
                fund_name = fn_match.group(1).strip()
        if not fund_name:
            # ETF pattern
            fn_match = re.search(
                r'(Ping An of China (?:CSI\s+)?[^\n]{5,80}?(?:ETF|Fund))',
                body_text, re.IGNORECASE,
            )
            if fn_match:
                fund_name = fn_match.group(1).strip()

        if not fund_name:
            fund_name = page.title().strip()

        # Clean up fund name
        fund_name = re.sub(r'\s*\([^)]*\)\s*', '', fund_name).strip()

        # Extract ISIN codes from FULL page HTML (they may be in hidden tabs)
        isins = page.evaluate("""() => {
            const result = [];
            const html = document.documentElement.outerHTML;
            const pattern = /[A-Z]{2}[0-9]{10}/g;
            let match;
            const seen = new Set();
            while ((match = pattern.exec(html)) !== null) {
                const isin = match[0];
                if (!seen.has(isin)) {
                    seen.add(isin);
                    result.push(isin);
                }
            }
            return result;
        }""")

        # Extract NAV data from "Unit price" or NAV tables
        nav_data = page.evaluate("""() => {
            const result = [];
            const tables = document.querySelectorAll('table');
            for (const table of tables) {
                const rows = Array.from(table.querySelectorAll('tr'));
                const headerRow = rows[0];
                if (!headerRow) continue;
                const headers = Array.from(headerRow.querySelectorAll('td, th'))
                    .map(c => (c.textContent || '').trim().toLowerCase());

                // Look for NAV/price tables
                const hasNAV = headers.some(h =>
                    h.includes('nav') || h.includes('price') || h.includes('unit') || h.includes('net asset')
                );
                if (!hasNAV) continue;

                for (let i = 1; i < rows.length; i++) {
                    const cells = Array.from(rows[i].querySelectorAll('td, th'));
                    if (cells.length < 2) continue;
                    const texts = cells.map(c => (c.textContent || '').trim());

                    // Find NAV value - look for a share class name and numeric value
                    for (let j = 0; j < texts.length - 1; j++) {
                        const val = parseFloat(texts[j + 1].replace(/,/g, ''));
                        if (!isNaN(val) && val > 0 && texts[j].length > 0) {
                            const currencyMatch = texts[j].match(/\b([A-Z]{3})\b/);
                            result.push({
                                label: texts[j],
                                nav: val,
                                currency: currencyMatch ? currencyMatch[1] : '',
                            });
                        }
                    }
                }
            }
            return result;
        }""")

        # Extract fund info
        fund_info = {}

        # Base currency
        cur_match = re.search(r'(?:Base Currency|Currency)\s+([A-Z]{3})', body_text)
        if cur_match:
            fund_info["base_currency"] = cur_match.group(1)

        # Fund type
        ft_match = re.search(r'Fund Type\s+(.+)', body_text, re.IGNORECASE)
        if ft_match:
            fund_info["fund_type"] = ft_match.group(1).strip().split('\n')[0]

        return {
            "fund_name": fund_name,
            "page_code": page_code,
            "product_url": url,
            "source_type": "manager_website",
            "isins": isins,
            "nav_data": nav_data,
            **fund_info,
        }

    def get_fund_list(self) -> List[Dict[str, Any]]:
        """Parse all Ping An fund pages."""
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
            funds = []
            for code, name in FUND_PAGES:
                detail = self._parse_fund_page(page, code)
                if detail:
                    funds.append(detail)
                    logger.info(
                        f"Ping An: {detail['fund_name'][:60]} — "
                        f"{len(detail['isins'])} ISINs, "
                        f"{len(detail['nav_data'])} NAV entries"
                    )
            return funds

        finally:
            context.close()

    # ── Name Matching ──────────────────────────────────────────

    def _match_fund_name(self, conn, extracted_name: str) -> Optional[int]:
        """Match Ping An fund name to hk_funds.id.

        Website names follow patterns like:
          - "Ping An of China CSI HK Dividend ETF"
          - "Ping An of China SIF - RMB Bond Fund"
          - "Ping An Stable Fund"
        """
        if not extracted_name:
            return None

        name = extracted_name.strip()

        # Strip parentheticals
        name = re.sub(r'\s*\([^)]*\)\s*', ' ', name)

        candidates = [name]

        # Simplify "Ping An of China SIF - " prefix
        name_simplified = re.sub(
            r'Ping An of China SIF\s*[-–]\s*',
            'Ping An ', name, flags=re.IGNORECASE,
        )
        if name_simplified != name:
            candidates.append(name_simplified)

        # Try without "Ping An of China " prefix
        for prefix in [
            "Ping An of China SIF - ",
            "Ping An of China Trust - ",
            "Ping An of China CSI ",
            "Ping An of China ",
            "Ping An ",
        ]:
            if name.lower().startswith(prefix.lower()):
                stripped = name[len(prefix):]
                if stripped not in candidates:
                    candidates.append(stripped)

        for candidate in candidates:
            c = re.sub(r"\s+", " ", candidate.lower().strip())
            c = re.sub(r"\s+(fund|class\s+\w+|etf|sicav|sif|ofc)\s*$", "", c, flags=re.IGNORECASE)

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
                       {self._PINGAN_MANAGER_SQL}
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
                    "fund", "class", "etf", "sif", "ping", "china",
                    "hong", "kong",
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
                       {self._PINGAN_MANAGER_SQL}
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
                nav_data = detail.get("nav_data", [])
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
                if update_fund_from_manager(conn, hk_fund_id, data):
                    stats["details_updated"] += 1

                # Store ISINs
                for isin in isins:
                    # Create share class entry per ISIN
                    sc_data = {
                        "share_class_name": "",
                        "isin": isin,
                        "currency": self._parse_currency(base_ccy),
                        "source": "pingan_website",
                    }
                    upsert_share_classes(conn, hk_fund_id, [sc_data])
                    stats["share_classes_stored"] += 1

                    update_fund_from_manager(conn, hk_fund_id, {"isin": isin})
                    stats["isins_updated"] += 1

                # Store NAV
                for nav_entry in nav_data:
                    nav = nav_entry.get("nav")
                    currency = nav_entry.get("currency", base_ccy)
                    if nav:
                        n = upsert_nav_history(conn, hk_fund_id, [{
                            "nav": nav,
                            "nav_date": today,
                            "nav_currency": self._parse_currency(currency),
                            "source": "pingan_website",
                        }])
                        stats["navs_stored"] += n

            logger.info(
                f"Ping An scrape complete: "
                f"Matched={stats['matched']}/{stats['funds_found']}, "
                f"ISINs={stats['isins_updated']}, "
                f"NAVs={stats['navs_stored']}, "
                f"ShareClasses={stats['share_classes_stored']}, "
                f"Details={stats['details_updated']}"
            )

        except Exception as e:
            logger.error(f"Ping An scrape failed: {e}")
            stats["errors"] += 1
        finally:
            self._close_browser()

        return stats
