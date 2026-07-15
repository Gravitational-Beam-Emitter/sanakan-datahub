"""
HSBC Asset Management connector.

Uses HSBC's internal REST API (api/v1/nav/funds) to extract ISINs, NAVs,
and fund metadata for Hong Kong funds. The API requires a JWT token obtained
via a browser-authenticated session — Playwright handles this automatically.

Strategy:
  1. Load the fund centre page with Playwright (triggers token issuance)
  2. Call the nav/funds API from within the browser context via page.evaluate()
  3. Extract ISINs (from share class UniqueIdentifier), NAVs, currencies
  4. Match funds to hk_funds by name with HSBC manager constraint

CE: AAM940 — HSBC Investment Funds (Luxembourg) S.A.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

from hk_funds.manager_connectors.base import BaseManagerConnector, register_connector

logger = logging.getLogger("hk_funds.manager_connectors.hsbc")

API_URL = "https://www.assetmanagement.hsbc.com.hk/api/v1/nav/funds"
PAGE_URL = "https://www.assetmanagement.hsbc.com.hk/en/intermediary/fund-centre"


@register_connector
class HSBCConnector(BaseManagerConnector):
    """Extracts fund data from HSBC Asset Management's internal REST API."""

    manager_ce_numbers = ["AAM940", "AAL518", "AAF684"]
    base_url = "https://www.assetmanagement.hsbc.com.hk"

    request_delay: float = 1.0
    request_timeout: int = 30

    _HSBC_MANAGER_SQL = (
        "AND (LOWER(fund_manager_name_en) LIKE '%hsbc%')"
    )

    def __init__(self):
        super().__init__()
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    # ── Playwright helpers ─────────────────────────────────────

    def _get_browser(self):
        """Initialize Playwright and return the browser."""
        if self._browser is None:
            from playwright.sync_api import sync_playwright
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=True)
        return self._browser

    def _get_page(self):
        """Initialize Playwright and return a page with authenticated session."""
        self._get_browser()

        if self._context is None:
            self._context = self._browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
                locale="en-HK",
            )

        if self._page is None or self._page.is_closed():
            self._page = self._context.new_page()

        return self._page

    def _close_browser(self):
        if self._context:
            self._context.close()
            self._context = None
        if self._browser:
            self._browser.close()
            self._browser = None
        if self._playwright:
            self._playwright.stop()
            self._playwright = None
        self._page = None

    # ── Fund List ───────────────────────────────────────────────

    def _get_token(self, page) -> Optional[str]:
        """Extract the JWT auth token from the browser's cookies."""
        return page.evaluate('''() => {
            const componentId = "e0FFNDg5MTJELUFEMzEtNEQ5RC04MzA4LTdBQzZERTgyQTc4Rn0=";
            const cookies = document.cookie.match(/([^=]+)(=([^;]+)(;|$))?/gi) || [];
            const parsed = cookies.map(c => {
                const trimmed = c.trim().replace(/;$/,"");
                const idx = trimmed.indexOf("=");
                return [trimmed.slice(0, idx), trimmed.slice(idx + 1)];
            });
            const found = parsed.find(p => decodeURIComponent(p[0]) === componentId);
            return found ? decodeURIComponent(found[1]) : null;
        }''')

    def _load_page_and_get_token(self):
        """Load fund centre page with a fresh context to get new auth cookies."""
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
        page.goto(PAGE_URL, wait_until="networkidle", timeout=30000)
        time.sleep(3)
        return context, page

    def _get_token_from_cookies(self, page) -> Optional[str]:
        """Extract the JWT auth token from browser cookies.

        The token cookie name is a base64 string that changes between sessions,
        but the value always starts with 'eyJ' (JWT header).
        """
        return page.evaluate('''() => {
            const cookies = document.cookie.split(';').map(c => c.trim());
            for (const c of cookies) {
                const idx = c.indexOf('=');
                if (idx < 0) continue;
                const value = decodeURIComponent(c.slice(idx + 1));
                if (value.startsWith('eyJ') && value.length > 100) {
                    return value;
                }
            }
            return null;
        }''')

    def _call_funds_api(self, page, current_page: int) -> Dict[str, Any]:
        """Call the nav/funds API from within the browser context."""
        return page.evaluate(
            """async ([body]) => {
                const resp = await fetch('/api/v1/nav/funds', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'Authorization': 'Bearer ' + body.authToken,
                        'ifc-cache-header': 'en,hk,priv,prices,1,nundefined',
                    },
                    body: JSON.stringify(body.body),
                });
                if (!resp.ok) {
                    return { error: resp.status };
                }
                const data = await resp.json();
                return {
                    funds: data.funds,
                    fundsTotal: data.fundsTotal,
                };
            }""",
            [{
                "authToken": self._get_token_from_cookies(page),
                "body": {
                    "appliedFilters": [],
                    "paging": {"currentPage": current_page},
                    "view": "prices",
                    "searchTerm": "",
                    "selectedValues": [],
                },
            }],
        )

    def get_fund_list(self) -> List[Dict[str, Any]]:
        """Fetch all funds from HSBC via the internal API using Playwright.

        Creates a fresh browser context for each run to ensure valid auth
        tokens. Calls the nav/funds API for all pages via page.evaluate()
        within the same page session.
        """
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
            page.goto(PAGE_URL, wait_until="networkidle", timeout=30000)
            time.sleep(3)

            # Fetch page 1 first to get total count
            result = self._call_funds_api(page, 1)

            if "error" in result:
                logger.error(f"HSBC API error: {result.get('error')}")
                return []

            all_funds = list(result.get("funds", []))
            total = result.get("fundsTotal", 0)
            per_page = len(all_funds)
            total_pages = (total + per_page - 1) // per_page if per_page else 1

            logger.info(f"HSBC: {total} funds total, {per_page} per page, {total_pages} pages")

            # Fetch remaining pages
            for page_num in range(2, total_pages + 1):
                time.sleep(0.5)
                result = self._call_funds_api(page, page_num)

                if "error" in result:
                    logger.warning(f"HSBC API error on page {page_num}: {result.get('error')}")
                    break

                page_funds = result.get("funds", [])
                all_funds.extend(page_funds)
                logger.info(f"HSBC: fetched {len(page_funds)} funds from page {page_num}")

            logger.info(f"HSBC: fetched {len(all_funds)}/{total} funds total")
            return all_funds

        finally:
            context.close()

    def _extract_fund_data(self, raw_funds: List[Dict]) -> List[Dict[str, Any]]:
        """Extract clean fund data from the raw API response.

        Each fund has:
          - name: "ABF Hong Kong Bond Index"
          - id: "FSUSA07KTA"
          - shareClasses: [{id, name, data: [{columnId, value, ...}]}]

        We extract ISIN from data where columnId == "UniqueIdentifier".
        """
        results = []
        for fund in raw_funds:
            if not isinstance(fund, dict):
                continue

            fund_name = fund.get("name", "")
            fund_id = fund.get("id", "")

            for sc in fund.get("shareClasses", []) or []:
                if not isinstance(sc, dict):
                    continue

                sc_name = sc.get("name", "")
                sc_id = sc.get("id", "")

                isin = None
                nav = None
                nav_currency = None
                nav_change_pct = None

                for item in sc.get("data", []) or []:
                    if not isinstance(item, dict):
                        continue
                    col = item.get("columnId", "")
                    val = (item.get("value") or "").strip()

                    if col == "UniqueIdentifier" and val:
                        isin = val
                    elif col == "Nav" and val:
                        try:
                            nav = float(val.replace(",", ""))
                        except ValueError:
                            pass
                    elif col == "DailyNavChangePerc" and val:
                        try:
                            nav_change_pct = float(val.replace("%", "").strip())
                        except ValueError:
                            pass
                    elif col == "DropDown_PriceCurrency":
                        groups = item.get("groups") or []
                        if groups:
                            options = groups[0].get("options") or []
                            if options:
                                nav_currency = options[0].get("value", "")

                if fund_name and isin:
                    results.append({
                        "fund_name": fund_name,
                        "fund_id": fund_id,
                        "share_class_name": sc_name,
                        "share_class_id": sc_id,
                        "isin": isin,
                        "nav": nav,
                        "nav_currency": nav_currency,
                        "nav_change_pct": nav_change_pct,
                    })

        return results

    # ── Name Matching ──────────────────────────────────────────

    def _match_fund_name(self, conn, extracted_name: str) -> Optional[int]:
        """Match HSBC fund name to hk_funds.id.

        HSBC API names follow patterns like:
          - "ABF Hong Kong Bond Index"
          - "HSBC All China Bond Fund"
          - "HSBC Global Investment Funds - Asia ex Japan Equity Bond"

        SFC register names may have slight differences.
        """
        if not extracted_name:
            return None

        name = extracted_name.strip()

        # Strip share class info from parentheses
        # e.g., "HSBC GIF - Asia Bond (Class A)" -> "HSBC GIF - Asia Bond"
        name = re.sub(r'\s*\([^)]*Class\s+\w+[^)]*\)', '', name)
        name = re.sub(r'\s+[A-I]\s+(Accumulation|Distribution|Acc|Dist|Inc)\s*$', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s+[A-Z]{3}\s*$', '', name)  # trailing currency

        candidates = []
        # Handle "HSBC Collective Investment Trust - XXX" → "HSBC XXX"
        cit_prefix = "HSBC Collective Investment Trust - "
        if name.upper().startswith(cit_prefix.upper()):
            candidates.append("HSBC " + name[len(cit_prefix):])
        cit_prefix2 = "HSBC Collective Investment Trust- "
        if name.upper().startswith(cit_prefix2.upper()):
            candidates.append("HSBC " + name[len(cit_prefix2):])

        # Handle "HSBC GIF" → "HSBC Global Investment Funds"
        gif_prefix = "HSBC GIF - "
        if name.upper().startswith(gif_prefix.upper()):
            candidates.append("HSBC Global Investment Funds - " + name[len(gif_prefix):])
        gif_prefix2 = "HSBC GIF "
        if name.upper().startswith(gif_prefix2.upper()):
            candidates.append("HSBC Global Investment Funds - " + name[len(gif_prefix2):])

        # Add the original name last (fallback)
        candidates.append(name)

        # Also try stripped "HSBC " prefix
        for prefix in ["HSBC Global Investment Funds - ", "HSBC "]:
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
                       {self._HSBC_MANAGER_SQL}
                       LIMIT 1""",
                    params,
                ).fetchone()
                if row:
                    return row[0]

            # Word-level matching
            keywords = [w for w in c.split() if len(w) > 3
                       and w not in ("fund", "class", "etf", "accumulation", "distribution",
                                     "global", "investment")]
            if len(keywords) >= 2:
                conditions = " AND ".join(
                    ["LOWER(fund_name_en) LIKE ?" for _ in keywords]
                )
                params = [f"%{kw}%" for kw in keywords]
                row = conn.execute(
                    f"""SELECT id, fund_name_en FROM hk_funds
                       WHERE {conditions} AND is_active = true
                       {self._HSBC_MANAGER_SQL}
                       LIMIT 1""",
                    params,
                ).fetchone()
                if row:
                    return row[0]

        return None

    # ── Company Profile (Playwright-based) ─────────────────────

    def get_about_page(self) -> Optional[str]:
        """Scrape HSBC AM's About Us page using Playwright."""
        url = self.base_url.rstrip("/") + "/en/about-us"
        page = self._get_page()
        try:
            page.goto(url, wait_until="networkidle", timeout=15000)
            time.sleep(2)
            text = page.evaluate("() => document.body.innerText")
            if text and len(text) > 200:
                return text
        except Exception as e:
            logger.debug(f"HSBC about page: {e}")
        return None

    def get_team_page(self) -> Optional[str]:
        """Scrape HSBC AM's leadership/people page."""
        for path in ["/en/about-us/our-people", "/en/about-us/leadership"]:
            url = self.base_url.rstrip("/") + path
            page = self._get_page()
            try:
                page.goto(url, wait_until="networkidle", timeout=15000)
                time.sleep(2)
                text = page.evaluate("() => document.body.innerText")
                if text and len(text) > 200:
                    return text
            except Exception as e:
                logger.debug(f"HSBC team page {path}: {e}")
        return None

    # ── Main Interface ─────────────────────────────────────────

    def get_fund_details(self, isin: str) -> Optional[Dict[str, Any]]:
        """Not implemented — HSBC API doesn't have a public fund detail endpoint."""
        return None

    def scrape_and_store(self, conn, date_str: str = None) -> Dict[str, int]:
        from hk_funds.storage import (
            update_fund_from_manager,
            upsert_nav_history,
        )

        today = date_str or datetime.now().strftime("%Y-%m-%d")
        stats = {
            "funds_found": 0, "matched": 0, "isins_updated": 0,
            "navs_stored": 0, "dividends_stored": 0, "details_updated": 0, "errors": 0,
        }

        try:
            raw_funds = self.get_fund_list()
            funds = self._extract_fund_data(raw_funds)
            stats["funds_found"] = len(funds)

            # Deduplicate: keep first ISIN per base fund name
            seen_fund_ids: set = set()

            for idx, fund in enumerate(funds):
                fund_name = fund.get("fund_name", "")
                isin = fund.get("isin", "")
                fund_api_id = fund.get("fund_id", "")

                if not fund_name or not isin:
                    continue

                # Deduplicate by HSBC fund ID (first ISIN per fund)
                if fund_api_id in seen_fund_ids:
                    continue
                seen_fund_ids.add(fund_api_id)

                # Match to SFC register
                hk_fund_id = self._match_fund_name(conn, fund_name)

                if not hk_fund_id:
                    if (idx + 1) % 20 == 0:
                        logger.info(
                            f"  [{idx + 1}/{len(funds)}] "
                            f"Matched={stats['matched']} (no match: {fund_name[:60]})"
                        )
                    continue

                stats["matched"] += 1

                # Build data dict
                data = {
                    "fund_name": fund_name,
                    "isin": isin,
                    "nav_currency": fund.get("nav_currency"),
                    "product_url": (
                        f"https://www.assetmanagement.hsbc.com.hk/en/intermediary/"
                        f"fund-centre?fundId={fund_api_id}"
                    ),
                    "source_type": "manager_website",
                }

                # Store fund details
                if update_fund_from_manager(conn, hk_fund_id, data):
                    stats["details_updated"] += 1

                # Set ISIN
                if isin:
                    update_fund_from_manager(conn, hk_fund_id, {"isin": isin})
                    stats["isins_updated"] += 1

                # Store NAV
                nav = fund.get("nav")
                nav_currency = fund.get("nav_currency") or "USD"
                if nav:
                    n = upsert_nav_history(conn, hk_fund_id, [{
                        "nav": nav,
                        "nav_date": today,
                        "nav_currency": nav_currency,
                        "source": "hsbc_website",
                    }])
                    stats["navs_stored"] += n

                if (idx + 1) % 20 == 0:
                    logger.info(
                        f"  [{idx + 1}/{len(funds)}] "
                        f"Matched={stats['matched']} "
                        f"ISINs={stats['isins_updated']} "
                        f"NAVs={stats['navs_stored']}"
                    )

            logger.info(
                f"HSBC scrape complete: "
                f"Matched={stats['matched']}/{stats['funds_found']}, "
                f"ISINs={stats['isins_updated']}, "
                f"NAVs={stats['navs_stored']}, "
                f"Details={stats['details_updated']}, "
                f"Errors={stats['errors']}"
            )

        except Exception as e:
            logger.error(f"HSBC scrape failed: {e}")
            stats["errors"] += 1
        finally:
            self._close_browser()

        return stats
