"""
CMS Asset Management (HK) connector.

Extracts fund data from cmschina.com.hk via Playwright DOM scraping.
The site is a corporate CMS with fund ISINs on the product listing page.

Strategy:
  1. Visit the Fund Product page (/AM/FundProduct)
  2. Extract fund names and ISINs from the page body text
  3. Match to hk_funds by ISIN first, then by name with CMS constraint

Note: Fund detail pages return 404. This connector extracts what data is
available from the product listing page. No NAV data is available.

CE: WEBB-68518 — CMS Asset Management (HK) Co., Limited
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from hk_funds.manager_connectors.base import BaseManagerConnector, register_connector

logger = logging.getLogger("hk_funds.manager_connectors.cms")

FUND_PRODUCT_URL = "https://www.cmschina.com.hk/AM/FundProduct"


@register_connector
class CMSConnector(BaseManagerConnector):
    """Extracts fund data from CMS Asset Management HK website."""

    manager_ce_numbers = ["WEBB-68518"]
    base_url = "https://www.cmschina.com.hk"

    request_delay: float = 1.0
    request_timeout: int = 30

    _CMS_MANAGER_SQL = (
        "AND (LOWER(fund_manager_name_en) LIKE '%cms%'"
        " OR LOWER(fund_manager_name_en) LIKE '%china merchants%')"
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

    # ── Fund Data Extraction ───────────────────────────────────

    def _parse_fund_products(self, body: str, html: str) -> List[Dict[str, Any]]:
        """Parse fund names and ISINs from the product page body text.

        The relevant text structure is:
          招商紅利回報均衡基金，招商中國靈活策略基金，招商貨幣市場基金ISIN碼
          HK0000890803,HK0000890811,HK0000890829，
          招商美元貨幣市場基金ISIN碼：HK0001039574,HK0001039582,HK0001039590,HK0001039608

        After stripping whitespace and splitting by "ISIN碼":
          Part 0: "...招商紅利回報均衡基金，招商中國靈活策略基金，招商貨幣市場基金"
          Part 1: "HK0000890803,HK0000890811,HK0000890829，招商美元貨幣市場基金"
          Part 2: "HK0001039574,HK0001039582,HK0001039590,HK0001039608..."
        """
        text = re.sub(r'\s+', '', body)

        # Find the section containing ISIN data
        isin_start = text.find('HK0')
        if isin_start < 0:
            return []

        # Take ~200 chars before first ISIN to capture fund names
        section = text[max(0, isin_start - 200):isin_start + 300]
        # Also ensure we capture fund names that include 招商
        section = body  # Use original body for name extraction

        funds = []

        # Split original body (with whitespace preserved) by ISIN markers
        parts = re.split(r'ISIN[碼码][：:]?', body)

        pending_fund_names: List[str] = []
        for part in parts:
            part = part.strip('，,\n\r\t ')
            if not part:
                continue

            # Extract fund names from this part (Chinese text before any ISINs)
            # Remove ISINs first
            text_no_isin = re.sub(r'HK\d{10}[,，\s]*', '', part).strip('，, \n\r\t')

            # Extract fund names matching pattern: 招商...基金/ETF
            fund_names = re.findall(
                r'(招商[A-Za-z\u4e00-\u9fff（）()\d]+?(?:基金|ETF))',
                text_no_isin
            )

            # Extract ISINs
            isins = re.findall(r'HK\d{10}', part)

            # If we have pending names and ISINs, assign them
            if isins and pending_fund_names:
                if len(pending_fund_names) == 1:
                    # Single fund gets all ISINs
                    funds.append({
                        "fund_name": pending_fund_names[0],
                        "isins": list(isins),
                        "share_classes": [
                            {"share_class_name": "", "isin": i, "currency": "HKD"}
                            for i in isins
                        ],
                    })
                elif len(isins) == len(pending_fund_names):
                    for fn, isin in zip(pending_fund_names, isins):
                        funds.append({
                            "fund_name": fn,
                            "isins": [isin],
                            "share_classes": [
                                {"share_class_name": "", "isin": isin, "currency": "HKD"}
                            ],
                        })
                else:
                    # Best effort: assign ISINs proportionally
                    per_fund = max(1, len(isins) // len(pending_fund_names))
                    for j, fn in enumerate(pending_fund_names):
                        start = j * per_fund
                        end = start + per_fund if j < len(pending_fund_names) - 1 else len(isins)
                        fund_isins = isins[start:end]
                        if fund_isins:
                            funds.append({
                                "fund_name": fn,
                                "isins": list(fund_isins),
                                "share_classes": [
                                    {"share_class_name": "", "isin": ii, "currency": "HKD"}
                                    for ii in fund_isins
                                ],
                            })

            # Update pending names for next iteration
            if fund_names:
                pending_fund_names = fund_names

        return funds

    def get_fund_list(self) -> List[Dict[str, Any]]:
        """Parse the Fund Product page for ISIN data."""
        browser = self._get_browser()
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            locale="zh-HK",
        )
        page = context.new_page()

        try:
            page.goto(FUND_PRODUCT_URL, wait_until="load", timeout=30000)
            time.sleep(3)

            body = page.inner_text("body")
            html = page.evaluate("document.documentElement.outerHTML")

            parsed_funds = self._parse_fund_products(body, html)

            # Build standard output format
            results = []
            for pf in parsed_funds:
                fund_name = pf.get("fund_name", "")
                isins = pf.get("isins", [])

                result = {
                    "fund_name": fund_name,
                    "product_url": FUND_PRODUCT_URL,
                    "source_type": "manager_website",
                    "isins": isins,
                    "share_classes": pf.get("share_classes", []),
                    "nav_entries": [],  # No NAV data available
                    "base_currency": "HKD",
                }

                logger.info(
                    f"CMS: {fund_name[:60] if fund_name else '(unnamed)'} — "
                    f"{len(isins)} ISINs: {', '.join(isins)}"
                )

                results.append(result)

            return results

        finally:
            context.close()

    # ── Name Matching ──────────────────────────────────────────

    def _match_fund_name(self, conn, extracted_name: str) -> Optional[int]:
        """Match CMS fund name to hk_funds.id."""

        if not extracted_name:
            return None

        name = extracted_name.strip()

        candidates = [name]

        # Try with/without "CMS " prefix
        if name.lower().startswith("cms "):
            candidates.append(name[4:])
        else:
            candidates.append("CMS " + name)

        # Try with English translation patterns
        chinese_to_eng = {
            "招商紅利回報均衡基金": "CMS Dividend Return Balanced Fund",
            "招商中國靈活策略基金": "CMS China Flexible Strategy Fund",
            "招商貨幣市場基金": "CMS Money Market Fund",
            "招商美元貨幣市場基金": "CMS USD Money Market Fund",
            "招商恒生科技指數ETF": "CMS Hang Seng Tech Index ETF",
        }

        for cn, en in chinese_to_eng.items():
            if cn in name:
                candidates.append(en)
                candidates.append(en.replace("CMS ", ""))

        for candidate in candidates:
            c = re.sub(r"\s+", " ", candidate.lower().strip())
            word_count = len(c.split())

            for query, params in [
                ("LOWER(fund_name_en) = ?", [c]),
                ("LOWER(fund_name_en) LIKE ?", [f"%{c}%"]),
            ]:
                if "LIKE" in query and word_count < 2 and len(c) < 10:
                    continue
                row = conn.execute(
                    f"""SELECT id, fund_name_en FROM hk_funds
                       WHERE {query} AND is_active = true
                       {self._CMS_MANAGER_SQL}
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
                    "fund", "class", "etf", "cms", "china", "merchants",
                    "money", "market", "bond", "equity", "index",
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
                       {self._CMS_MANAGER_SQL}
                       LIMIT 1""",
                    params,
                ).fetchone()
                if row:
                    return row[0]

        return None

    # ── Main Interface ─────────────────────────────────────────

    def get_fund_details(self, isin: str) -> Optional[Dict[str, Any]]:
        """Not implemented — no fund detail pages available."""
        return None

    def scrape_and_store(self, conn, date_str: str = None) -> Dict[str, int]:
        from hk_funds.storage import (
            update_fund_from_manager,
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
                base_ccy = detail.get("base_currency", "HKD")

                if not isins:
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
                if not hk_fund_id and fund_name:
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

                # Store ISINs and share classes
                for sc in share_classes:
                    sc_data = {
                        "share_class_name": sc.get("share_class_name", ""),
                        "isin": sc.get("isin", ""),
                        "currency": sc.get("currency", base_ccy),
                        "source": "cms_website",
                    }
                    upsert_share_classes(conn, hk_fund_id, [sc_data])
                    stats["share_classes_stored"] += 1

                    if sc.get("isin"):
                        update_fund_from_manager(
                            conn, hk_fund_id, {"isin": sc["isin"]}
                        )
                        stats["isins_updated"] += 1

            logger.info(
                f"CMS scrape complete: "
                f"Matched={stats['matched']}/{stats['funds_found']}, "
                f"ISINs={stats['isins_updated']}, "
                f"ShareClasses={stats['share_classes_stored']}, "
                f"Details={stats['details_updated']}"
            )

        except Exception as e:
            logger.error(f"CMS scrape failed: {e}")
            stats["errors"] += 1
        finally:
            self._close_browser()

        return stats
