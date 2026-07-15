"""
BOCI-Prudential Asset Management connector.

BOCI-Prudential hosts its SFC-authorized unit trusts at
www.boci-pru.com.hk. The fund listing page (/en/ut/ut-prices) calls an
internal REST API at /en/api/UTFund/utListing which returns all fund
share classes with ISINs, NAVs, currencies, and other metadata.

Strategy:
  1. Load the fund listing page with Playwright
  2. Intercept the utListing API response (the page triggers it on load)
  3. Extract ISINs, NAVs, fund names, currencies from the JSON
  4. Deduplicate by base fund name (one ISIN per fund)
  5. Match fund names to hk_funds via name normalization

CE: AFK209 — BOCI-Prudential Asset Management Limited
Also handles: BOCHK Asset Management Limited (AWJ005, BOCHK funds),
              CNCB (Hong Kong) Capital Limited (AEQ982, NCB funds)
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from hk_funds.manager_connectors.base import BaseManagerConnector, register_connector

logger = logging.getLogger("hk_funds.manager_connectors.boci_prudential")

FUND_LIST_URL = "https://www.boci-pru.com.hk/en/ut/ut-prices"
API_ENDPOINT = "/en/api/UTFund/utListing"


@register_connector
class BOCIPrudentialConnector(BaseManagerConnector):
    """Extracts fund data from BOCI-Prudential's internal REST API."""

    manager_ce_numbers = ["AFK209", "AWJ005", "AEQ982"]
    base_url = "https://www.boci-pru.com.hk"

    request_delay: float = 1.0
    request_timeout: int = 30

    # Manager SQL filters — apply different filters based on fund code prefix
    _BOCI_MANAGER_SQL = (
        "AND (LOWER(fund_manager_name_en) LIKE '%boci%'"
        "  OR LOWER(fund_manager_name_en) LIKE '%bochk%'"
        "  OR LOWER(fund_manager_name_en) LIKE '%cncb%'"
        "  OR LOWER(fund_manager_name_en) LIKE '%nanyang%')"
    )

    def __init__(self):
        super().__init__()
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    # ── Playwright helpers ─────────────────────────────────────

    def _get_page(self):
        """Initialize Playwright browser and page."""
        if self._browser is None:
            from playwright.sync_api import sync_playwright
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=True)

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

    def get_fund_list(self) -> List[Dict[str, Any]]:
        """Fetch all funds by intercepting the utListing API response."""
        page = self._get_page()

        # Set up response interception BEFORE navigating
        ut_data: List[Dict] = []

        def on_response(response):
            url = response.url
            if "utListing" in url and response.status == 200:
                ct = response.headers.get("content-type", "")
                if "json" in ct:
                    try:
                        body = response.text()
                        data = json.loads(body)
                        funds = data.get("data", [])
                        if funds:
                            ut_data.extend(funds)
                            logger.info(
                                f"BOCI: captured utListing API with {len(funds)} items"
                            )
                    except Exception as e:
                        logger.debug(f"BOCI: error parsing utListing: {e}")

        page.on("response", on_response)

        # Navigate to fund listing page (triggers the API)
        resp = page.goto(FUND_LIST_URL, wait_until="networkidle", timeout=30000)
        if resp.status != 200:
            logger.error(f"BOCI: fund listing page returned {resp.status}")
            return []

        time.sleep(3)

        if not ut_data:
            logger.error("BOCI: failed to capture utListing API response")
            return []

        funds = self._extract_fund_data(ut_data)
        logger.info(f"BOCI: extracted {len(funds)} unique funds from {len(ut_data)} share classes")
        return funds

    def _extract_fund_data(self, raw_items: List[Dict]) -> List[Dict[str, Any]]:
        """Extract clean fund data from utListing API response.

        Each item is a share class:
          - fundName.nameEn: English fund name (with HTML superscripts)
          - isin: ISIN code
          - utFundCode: internal fund code (e.g. BOCIP-SZGF)
          - currencyCode: currency (HKD, USD, RMB, etc.)
          - nav.value: latest NAV
          - nav.date: NAV date
          - class: share class (A, B, etc.)
          - riskLv: risk level (1-5)
          - inceptionDate: fund inception date
        """
        results = []
        seen_base: set = set()

        for item in raw_items:
            if not isinstance(item, dict):
                continue

            fund_name_obj = item.get("fundName") or {}
            raw_name = fund_name_obj.get("nameEn", "") or fund_name_obj.get("name", "")

            if not raw_name:
                continue

            isin = (item.get("isin") or "").strip()
            if not isin:
                continue

            # Strip HTML tags from name
            base_name = re.sub(r"<[^>]+>", "", raw_name).strip()
            # Strip special unicode symbols (△, ℽ, ♦, ♯, §, etc.)
            base_name = re.sub(r"[\u25B3\u213D\u2666\u266F\u00A7\u2605\u03A9\u2020\u2021\u25CB\u25CF\u271A\u273F]+", "", base_name)
            base_name = re.sub(r"\s+", " ", base_name).strip()

            # Normalize: remove class suffix like "(A Class - HKD Units)"
            base_name = re.sub(r"\s*\([^)]*Class[^)]*\)", "", base_name)
            base_name = re.sub(r"\s*\([^)]*Units?\)", "", base_name)
            base_name = re.sub(r"\s*\([^)]*\)$", "", base_name)  # trailing parenthetical
            base_name = re.sub(r"\s+[-–]\s*[A-C]\s*(Acc|Dis|Inc|Dist).*$", "", base_name, flags=re.IGNORECASE)
            base_name = base_name.strip()

            # Deduplicate by base name
            base_key = base_name.lower()
            if base_key in seen_base:
                continue
            seen_base.add(base_key)

            nav_val = None
            nav_date = None
            nav_obj = item.get("nav")
            if isinstance(nav_obj, dict):
                try:
                    nav_val = float(nav_obj.get("value", ""))
                except (ValueError, TypeError):
                    pass
                nav_date = nav_obj.get("date", "")

            fund_code = item.get("utFundCode", "")
            currency = item.get("currencyCode", "")
            share_class = item.get("class", "")

            # Build product URL
            product_url = ""
            price_link = item.get("priceLink") or fund_name_obj.get("link", "")
            if price_link:
                product_url = f"https://www.boci-pru.com.hk{price_link}"

            results.append({
                "fund_name": base_name,
                "isin": isin,
                "fund_code": fund_code,
                "currency": currency,
                "share_class": share_class,
                "nav": nav_val,
                "nav_date": nav_date,
                "product_url": product_url,
                "source_type": "manager_website",
            })

        return results

    # ── Name Matching ──────────────────────────────────────────

    def _match_fund_name(self, conn, extracted_name: str, fund_code: str = "") -> Optional[int]:
        """Match BOCI fund name to hk_funds.id.

        BOCI API names:
          - "BOCIP Shenzhen Growth Fund"
          - "BOCHK Aggressive Growth Fund"
          - "NCB China Equity Fund"
          - "BOC-Prudential North America Index Fund"

        SFC register names:
          - "BOC-Prudential Shenzhen Growth Fund" (BOCIP → BOC-Prudential)
          - "BOCHK Aggressive Growth Fund" (same)
          - "NCB China Equity Fund" (same)
        """
        if not extracted_name:
            return None

        name = extracted_name.strip()

        # Detect manager from fund code prefix to narrow SQL filter.
        # NOTE: Many BOCHK-branded funds are registered under BOCI-Prudential
        # Asset Management Limited in the SFC register, not BOCHK AM.
        manager_sql = self._BOCI_MANAGER_SQL
        if fund_code.startswith("BOCIP") or fund_code.startswith("BOCPIFS"):
            manager_sql = "AND (LOWER(fund_manager_name_en) LIKE '%boci%')"
        elif fund_code.startswith("BOC-"):
            # BOCHK funds can be under BOCI-Prudential OR BOCHK AM
            manager_sql = (
                "AND (LOWER(fund_manager_name_en) LIKE '%boci%'"
                "  OR LOWER(fund_manager_name_en) LIKE '%bochk%')"
            )
        elif fund_code.startswith("NCB-"):
            manager_sql = (
                "AND (LOWER(fund_manager_name_en) LIKE '%cncb%'"
                "  OR LOWER(fund_manager_name_en) LIKE '%nanyang%'"
                "  OR LOWER(fund_manager_name_en) LIKE '%boci%')"
            )

        # Build candidates
        candidates = [name]

        # BOCIP → BOC-Prudential / BOCI-Prudential conversion
        bocip_prefixes = [
            ("BOCIP ", "BOC-Prudential "),
            ("BOCIP ", "BOCI-Prudential "),
            ("BOC-Prudential ", "BOC-Prudential "),
            ("BOCI-Prudential ", "BOCI-Prudential "),
        ]
        for old_prefix, new_prefix in bocip_prefixes:
            if name.upper().startswith(old_prefix.upper()):
                candidates.append(new_prefix + name[len(old_prefix):])

        # Add "Fund" suffix if not present
        for i in range(len(candidates)):
            if not candidates[i].lower().endswith(" fund"):
                candidates.append(candidates[i] + " Fund")

        for candidate in candidates:
            c = re.sub(r"\s+", " ", candidate.lower().strip())
            # Strip trailing special chars
            c = re.sub(r"[\u25B3\u213D\u2666\u266F\u00A7\u2605\u03A9]+", "", c).strip()

            # Also strip share class suffixes for matching
            c_stripped = re.sub(r"\s*\([^)]*\)\s*$", "", c)
            c_stripped = re.sub(r"\s+class\s+\w+\s*$", "", c_stripped, flags=re.IGNORECASE)
            c_stripped = re.sub(r"\s+[-–]\s*[a-c]\s*(acc|dis|inc|dist|md|qd)?\s*$", "", c_stripped, flags=re.IGNORECASE)

            for ct in [c, c_stripped]:
                ct = ct.strip()
                if not ct:
                    continue

                # Exact match
                row = conn.execute(
                    f"""SELECT id, fund_name_en FROM hk_funds
                       WHERE LOWER(fund_name_en) = ? AND is_active = true
                       {manager_sql}
                       LIMIT 1""",
                    [ct],
                ).fetchone()
                if row:
                    return row[0]

                # LIKE
                word_count = len(ct.split())
                if word_count >= 2 and len(ct) >= 10:
                    row = conn.execute(
                        f"""SELECT id, fund_name_en FROM hk_funds
                           WHERE LOWER(fund_name_en) LIKE ? AND is_active = true
                           {manager_sql}
                           LIMIT 1""",
                        [f"%{ct}%"],
                    ).fetchone()
                    if row:
                        return row[0]

                    row = conn.execute(
                        f"""SELECT id, fund_name_en FROM hk_funds
                           WHERE ? LIKE '%' || LOWER(fund_name_en) || '%' AND is_active = true
                           {manager_sql}
                           LIMIT 1""",
                        [ct],
                    ).fetchone()
                    if row:
                        return row[0]

                # Word-level matching
                keywords = [
                    w for w in ct.split()
                    if len(w) > 2
                    and w not in (
                        "fund", "class", "etf", "acc", "dis", "inc", "dist",
                        "global", "investment", "funds", "the", "and", "for"
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
                           {manager_sql}
                           LIMIT 1""",
                        params,
                    ).fetchone()
                    if row:
                        return row[0]

        return None

    # ── Main Interface ─────────────────────────────────────────

    def get_fund_details(self, isin: str) -> Optional[Dict[str, Any]]:
        """Not implemented for BOCI."""
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
            funds = self.get_fund_list()
            stats["funds_found"] = len(funds)

            for idx, fund in enumerate(funds):
                fund_name = fund.get("fund_name", "")
                isin = fund.get("isin", "")
                fund_code = fund.get("fund_code", "")

                if not fund_name or not isin:
                    continue

                hk_fund_id = self._match_fund_name(conn, fund_name, fund_code)

                if not hk_fund_id:
                    if (idx + 1) % 15 == 0:
                        logger.info(
                            f"  [{idx + 1}/{len(funds)}] "
                            f"Matched={stats['matched']} (no match: {fund_name[:60]} [{fund_code}])"
                        )
                    continue

                stats["matched"] += 1

                data = {
                    "fund_name": fund_name,
                    "isin": isin,
                    "nav_currency": fund.get("currency"),
                    "product_url": fund.get("product_url"),
                    "source_type": "manager_website",
                }

                if update_fund_from_manager(conn, hk_fund_id, data):
                    stats["details_updated"] += 1

                if isin:
                    update_fund_from_manager(conn, hk_fund_id, {"isin": isin})
                    stats["isins_updated"] += 1

                # Store NAV
                nav = fund.get("nav")
                nav_currency = fund.get("currency") or "HKD"
                if nav:
                    n = upsert_nav_history(conn, hk_fund_id, [{
                        "nav": nav,
                        "nav_date": today,
                        "nav_currency": nav_currency,
                        "source": "boci_website",
                    }])
                    stats["navs_stored"] += n

                if (idx + 1) % 15 == 0:
                    logger.info(
                        f"  [{idx + 1}/{len(funds)}] "
                        f"Matched={stats['matched']} "
                        f"ISINs={stats['isins_updated']} "
                        f"NAVs={stats['navs_stored']}"
                    )

            logger.info(
                f"BOCI scrape complete: "
                f"Matched={stats['matched']}/{stats['funds_found']}, "
                f"ISINs={stats['isins_updated']}, "
                f"NAVs={stats['navs_stored']}, "
                f"Details={stats['details_updated']}, "
                f"Errors={stats['errors']}"
            )

        except Exception as e:
            logger.error(f"BOCI scrape failed: {e}")
            stats["errors"] += 1
        finally:
            self._close_browser()

        return stats
