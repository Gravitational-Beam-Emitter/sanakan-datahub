"""
Invesco Hong Kong connector.

Invesco's HK website (www.invesco.com/hk) hosts SFC-authorized mutual funds
under /hk/en/mutual-funds.html. The page uses Adobe Experience Manager (AEM)
as its CMS and queries a Solr-based DNG API at dng-api.invesco.com for fund
search results.

Strategy:
  1. Bypass the country splash page (click "Confirm" after selecting HK)
  2. Navigate to the mutual funds listing page
  3. Intercept the DNG API response (product/search) that the page triggers
  4. Extract ISINs, base fund names (accountName), share class metadata
  5. Deduplicate by accountName (one ISIN per base fund)
  6. Match base fund names to hk_funds via enhanced name matching

CE: AAJ770 — Invesco Hong Kong Limited / Invesco Asset Management Asia Ltd

Note: The DNG API is on a different origin (dng-api.invesco.com), so we
cannot call it via page.evaluate() due to CORS. We intercept the response
that the page itself triggers instead.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

from hk_funds.manager_connectors.base import BaseManagerConnector, register_connector

logger = logging.getLogger("hk_funds.manager_connectors.invesco")

FUND_LIST_URL = "https://www.invesco.com/hk/en/mutual-funds.html"
COUNTRY_SPLASH_URL = "https://www.invesco.com/hk/en/country-splash.html"


@register_connector
class InvescoConnector(BaseManagerConnector):
    """Extracts fund data from Invesco HK via DNG API response interception."""

    manager_ce_numbers = ["AAJ770"]
    base_url = "https://www.invesco.com/hk"

    request_delay: float = 1.0
    request_timeout: int = 30

    _INVESCO_MANAGER_SQL = (
        "AND (LOWER(fund_manager_name_en) LIKE '%invesco%'"
        "  OR LOWER(fund_manager_name_en) LIKE '%nvesco%')"
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

    # ── Country Splash Bypass ──────────────────────────────────

    def _bypass_country_splash(self, page) -> bool:
        """Navigate through the country splash to get to the actual site."""
        page.goto(COUNTRY_SPLASH_URL, wait_until="networkidle", timeout=30000)
        time.sleep(2)

        # Accept cookies if prompted
        try:
            page.click('text=Accept all cookies', timeout=3000)
            time.sleep(1)
            logger.debug("Invesco: accepted cookies")
        except Exception:
            pass

        # Click "Confirm" to proceed with HK as selected country
        try:
            page.click('button:has-text("Confirm")', timeout=5000)
            time.sleep(3)
            logger.debug(f"Invesco: bypassed splash, now at {page.url}")
            return "splash" not in page.url.lower()
        except Exception as e:
            logger.error(f"Invesco: failed to click Confirm on country splash: {e}")
            return False

    # ── Fund List ───────────────────────────────────────────────

    def get_fund_list(self) -> List[Dict[str, Any]]:
        """Fetch all funds from Invesco by intercepting the DNG API response."""
        page = self._get_page()

        # Bypass country splash
        if not self._bypass_country_splash(page):
            return []

        # Set up response interception BEFORE navigating
        dng_data: List[Dict] = []

        def on_response(response):
            url = response.url
            if "dng-api.invesco.com/product/search" in url and response.status == 200:
                ct = response.headers.get("content-type", "")
                if "json" in ct:
                    try:
                        body = response.text()
                        if len(body) > 500:
                            data = json.loads(body)
                            docs = data.get("response", {}).get("docs", [])
                            if docs:
                                dng_data.extend(docs)
                                logger.info(
                                    f"Invesco: captured DNG API response "
                                    f"with {len(docs)} docs "
                                    f"(total: {data['response'].get('numFound')})"
                                )
                    except Exception as e:
                        logger.debug(f"Invesco: error parsing DNG response: {e}")

        page.on("response", on_response)

        # Navigate to mutual funds page (triggers the DNG API call)
        resp = page.goto(FUND_LIST_URL, wait_until="networkidle", timeout=30000)
        if resp.status != 200:
            logger.error(f"Invesco: mutual-funds page returned {resp.status}")
            return []

        # Wait for the DNG API to be called (AEM SPA loads asynchronously)
        time.sleep(5)

        # If we didn't get the response yet, wait longer
        if not dng_data:
            logger.info("Invesco: DNG API not yet captured, waiting...")
            for _ in range(10):
                time.sleep(1)
                if dng_data:
                    break

        if not dng_data:
            logger.error("Invesco: failed to capture DNG API response after waiting")
            return []

        # Extract clean fund data
        funds = self._extract_fund_data(dng_data)
        logger.info(f"Invesco: extracted {len(funds)} unique funds from {len(dng_data)} share classes")
        return funds

    def _extract_fund_data(self, raw_docs: List[Dict]) -> List[Dict[str, Any]]:
        """Extract clean fund data from DNG API Solr response.

        Each doc represents a share class with fields:
          - accountName: base fund name (e.g. "Invesco ASEAN Equity")
          - title: full share class name
          - isin: ISIN code
          - fundId: internal fund identifier
          - baseCurrency: share class currency
          - assetClass: e.g. "Equity", "Fixed Income", "Allocation"
          - inceptionDate: fund inception date
          - shareClassSuffix: e.g. "A(HKD) Acc"
          - url: detail page URL
        """
        results = []
        seen_accounts: set = set()

        for doc in raw_docs:
            if not isinstance(doc, dict):
                continue

            account_name = (doc.get("accountName") or "").strip()
            isin = (doc.get("isin") or "").strip()
            title = (doc.get("title") or "").strip()

            if not account_name or not isin:
                continue

            # Deduplicate: one ISIN per base fund (accountName)
            if account_name.lower() in seen_accounts:
                continue
            seen_accounts.add(account_name.lower())

            fund_id = doc.get("fundId", "")
            currency = doc.get("baseCurrency", "USD")
            asset_class = doc.get("assetClass", "")
            inception_date = doc.get("inceptionDate", "")
            url = doc.get("url", "")

            if url and not url.startswith("http"):
                url = "https://www.invesco.com" + url

            results.append({
                "fund_name": account_name,
                "isin": isin,
                "fund_id": fund_id,
                "currency": currency,
                "asset_class": asset_class,
                "inception_date": inception_date,
                "share_class_name": title,
                "product_url": url,
                "source_type": "manager_website",
            })

        return results

    # ── Name Matching ──────────────────────────────────────────

    # DNG accountNames use heavy abbreviations. Expand them.
    _ABBREV_MAP = {
        "real estt": "real estate",
        "secs fd": "securities fund",
        "invm grd": "investment grade",
        "corp bd": "corporate bond",
        "fdr fd": "feeder fund",
        "sust pan": "sustainable pan",
        "eurp systmtc eq fd": "european systematic equity fund",
        "systmtc eq": "systematic equity",
        "pan eurp": "pan european",
    }

    def _expand_abbreviations(self, name: str) -> str:
        """Expand common abbreviations in Invesco DNG fund names."""
        result = name.lower()
        for abbr, full in self._ABBREV_MAP.items():
            result = result.replace(abbr, full)
        return result

    def _match_fund_name(self, conn, extracted_name: str) -> Optional[int]:
        """Match Invesco fund name to hk_funds.id.

        Invesco DNG API accountNames follow patterns like:
          - "Invesco ASEAN Equity"
          - "Invesco Global Investment Grade Corporate Bond"
          - "Invesco China Focus Equity"
          - "Invesco Asian Flexible Bond"

        Some funds have SFC register names like:
          - "Invesco Global Investment Grade Corporate Bond Fund"
          - "Invesco Asia Consumer Demand Fund"

        The DNG accountName may NOT include "Fund" suffix, but SFC names do.
        Some DNG names use heavy abbreviations (e.g. "Invm Grd Corp Bd Fdr Fd").
        """
        if not extracted_name:
            return None

        name = extracted_name.strip()

        # Build candidates
        candidates = [name]

        # Add expanded abbreviation version
        expanded = self._expand_abbreviations(name)
        if expanded.lower() != name.lower():
            candidates.append(expanded)

        # Add with "Fund" suffix if not present
        for i in range(len(candidates)):
            c = candidates[i]
            if not c.lower().rstrip(" fund").endswith(" fund"):
                candidates.append(c + " Fund")

        # Try stripping "Invesco " prefix
        for prefix in ["Invesco ", "INVESCO "]:
            if name.lower().startswith(prefix.lower()):
                stripped = name[len(prefix):]
                if stripped not in candidates:
                    candidates.append(stripped)
                    if not stripped.lower().endswith(" fund"):
                        candidates.append(stripped + " Fund")

        for candidate in candidates:
            c = re.sub(r"\s+", " ", candidate.lower().strip())

            # Exact match (case-insensitive)
            row = conn.execute(
                f"""SELECT id, fund_name_en FROM hk_funds
                   WHERE LOWER(fund_name_en) = ? AND is_active = true
                   {self._INVESCO_MANAGER_SQL}
                   LIMIT 1""",
                [c],
            ).fetchone()
            if row:
                return row[0]

            # LIKE both ways
            word_count = len(c.split())
            if word_count >= 2:
                row = conn.execute(
                    f"""SELECT id, fund_name_en FROM hk_funds
                       WHERE LOWER(fund_name_en) LIKE ? AND is_active = true
                       {self._INVESCO_MANAGER_SQL}
                       LIMIT 1""",
                    [f"%{c}%"],
                ).fetchone()
                if row:
                    return row[0]

                row = conn.execute(
                    f"""SELECT id, fund_name_en FROM hk_funds
                       WHERE ? LIKE '%' || LOWER(fund_name_en) || '%' AND is_active = true
                       {self._INVESCO_MANAGER_SQL}
                       LIMIT 1""",
                    [c],
                ).fetchone()
                if row:
                    return row[0]

            # Word-level matching
            keywords = [
                w for w in c.split()
                if len(w) > 3
                and w not in (
                    "fund", "class", "etf", "accumulation", "distribution",
                    "global", "investment", "funds"
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
                       {self._INVESCO_MANAGER_SQL}
                       LIMIT 1""",
                    params,
                ).fetchone()
                if row:
                    return row[0]

        return None

    # ── Main Interface ─────────────────────────────────────────

    def get_fund_details(self, isin: str) -> Optional[Dict[str, Any]]:
        """Not implemented for Invesco yet."""
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

                if not fund_name or not isin:
                    continue

                hk_fund_id = self._match_fund_name(conn, fund_name)

                if not hk_fund_id:
                    if (idx + 1) % 10 == 0:
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
                    "nav_currency": fund.get("currency"),
                    "product_url": fund.get("product_url"),
                    "source_type": "manager_website",
                }

                # Store fund details
                if update_fund_from_manager(conn, hk_fund_id, data):
                    stats["details_updated"] += 1

                # Set ISIN
                if isin:
                    update_fund_from_manager(conn, hk_fund_id, {"isin": isin})
                    stats["isins_updated"] += 1

                if (idx + 1) % 10 == 0:
                    logger.info(
                        f"  [{idx + 1}/{len(funds)}] "
                        f"Matched={stats['matched']} "
                        f"ISINs={stats['isins_updated']}"
                    )

            logger.info(
                f"Invesco scrape complete: "
                f"Matched={stats['matched']}/{stats['funds_found']}, "
                f"ISINs={stats['isins_updated']}, "
                f"Details={stats['details_updated']}, "
                f"Errors={stats['errors']}"
            )

        except Exception as e:
            logger.error(f"Invesco scrape failed: {e}")
            stats["errors"] += 1
        finally:
            self._close_browser()

        return stats
