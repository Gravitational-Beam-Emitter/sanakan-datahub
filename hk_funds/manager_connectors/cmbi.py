"""
CMB International (CMBI) Asset Management connector.

Extracts fund data from cmbi.com via Playwright DOM scraping.
The site is a Chinese-language SSR website with fund data in HTML.

Strategy:
  1. Visit mutual fund listing page (/zh-CN/mutualfundlist)
  2. Parse listing table for fund names, share classes, NAVs, dates
  3. Visit each fund detail page (/zh-CN/mutualfund?id=X)
  4. Extract ISIN codes, Bloomberg tickers, fund info from detail tables
  5. Match to hk_funds by ISIN first, then by name with CMB constraint

Funds (all in Chinese):
  - ID 1: 招银国际美元货币市场基金 (CMB USD Money Market Fund)
  - ID 4: 招银国际港元货币市场基金 (CMB HKD Money Market Fund)
  - ID 5: 招银国际投资级债券基金 (CMB Investment Grade Bond Fund)
  - ID 6: 招银国际环球投资级精选债券基金 (CMB Global Inv Grade Bond Fund)
  - ID 7: 招银国际AI股票基金 (CMB AI Equity Fund)

CE: AVA101 — CMB International Asset Management Limited
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from hk_funds.manager_connectors.base import BaseManagerConnector, register_connector

logger = logging.getLogger("hk_funds.manager_connectors.cmbi")

BASE_URL = "https://www.cmbi.com"
LISTING_URL = f"{BASE_URL}/zh-CN/mutualfundlist"
FUND_DETAIL_URL = f"{BASE_URL}/zh-CN/mutualfund"


@register_connector
class CMBIConnector(BaseManagerConnector):
    """Extracts fund data from CMB International Asset Management website."""

    manager_ce_numbers = ["AVA101"]
    base_url = BASE_URL

    request_delay: float = 1.0
    request_timeout: int = 30

    _CMBI_MANAGER_SQL = (
        "AND (LOWER(fund_manager_name_en) LIKE '%cmb%'"
        " OR LOWER(fund_manager_name_en) LIKE '%cmbi%'"
        " OR LOWER(fund_manager_name_en) LIKE '%china merchants bank%')"
    )

    # Chinese to English fund type mapping
    _ASSET_CLASS_MAP = {
        "货币类": "Money Market",
        "固定收益类": "Fixed Income",
        "权益股票类": "Equity",
    }

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

    def _parse_listing_page(self, page) -> List[Dict[str, Any]]:
        """Parse the fund listing page for fund names, NAVs, and fund detail URLs."""
        page.goto(LISTING_URL, wait_until="load", timeout=30000)
        time.sleep(5)

        # Discover fund detail URLs
        fund_urls = page.evaluate("""() => {
            const links = Array.from(document.querySelectorAll('a'));
            const seen = new Set();
            const result = [];
            for (const link of links) {
                const href = link.href;
                const match = href.match(/mutualfund\\?id=(\\d+)/);
                if (match && !seen.has(match[1])) {
                    seen.add(match[1]);
                    result.push({
                        id: match[1],
                        name_cn: link.textContent.replace('(点击进入详情页)', '').trim(),
                        url: href
                    });
                }
            }
            return result;
        }""")

        # Get listing table with NAV data
        listing_data = page.evaluate("""() => {
            const tables = document.querySelectorAll('table');
            for (const table of tables) {
                const rows = Array.from(table.querySelectorAll('tr'));
                if (rows.length < 3) continue;
                const headers = Array.from(rows[0].querySelectorAll('td, th'))
                    .map(c => (c.textContent || '').trim());
                if (headers.includes('基金名称') && headers.includes('单位净值')) {
                    return Array.from(rows).slice(1).map(r =>
                        Array.from(r.querySelectorAll('td, th'))
                            .map(c => (c.textContent || '').trim())
                    );
                }
            }
            return null;
        }""")

        # Index listing data by fund name
        listing_nav = {}
        if listing_data:
            for row in listing_data:
                if len(row) < 5:
                    continue
                fund_name_cn = row[0].replace('(点击进入详情页)', '').strip()
                sc_name = row[1] if len(row) > 1 else ""
                ccy = row[2] if len(row) > 2 else ""
                dist_type = row[3] if len(row) > 3 else ""
                launch_date = row[4] if len(row) > 4 else ""
                nav_str = row[5] if len(row) > 5 else ""
                nav_date = row[6] if len(row) > 6 else ""

                if not fund_name_cn:
                    # This row belongs to the previous fund
                    continue

                if fund_name_cn not in listing_nav:
                    listing_nav[fund_name_cn] = {
                        "nav_entries": [],
                        "share_classes": [],
                    }

                # Parse NAV
                nav_val = None
                try:
                    nav_val = float(nav_str.replace(',', ''))
                except (ValueError, TypeError):
                    pass

                # Parse NAV date
                parsed_date = None
                if nav_date and re.match(r'\d{4}-\d{2}-\d{2}', nav_date):
                    parsed_date = nav_date

                listing_nav[fund_name_cn]["share_classes"].append({
                    "share_class_name": sc_name,
                    "currency": self._parse_currency(ccy),
                    "distribution_type": dist_type,
                    "launch_date": launch_date,
                })

                if nav_val and nav_val > 0:
                    listing_nav[fund_name_cn]["nav_entries"].append({
                        "share_class": sc_name,
                        "nav": nav_val,
                        "nav_date": parsed_date,
                        "nav_currency": self._parse_currency(ccy),
                    })

        # Combine with fund URLs
        for fu in fund_urls:
            name_cn = fu["name_cn"]
            if name_cn in listing_nav:
                fu.update(listing_nav[name_cn])
            else:
                fu["nav_entries"] = []
                fu["share_classes"] = []

        return fund_urls

    def _parse_fund_detail(self, page, fund_entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Visit a fund detail page to extract ISINs, fund info, and additional NAVs."""
        url = fund_entry.get("url", "")
        if not url:
            return None

        try:
            page.goto(url, wait_until="load", timeout=30000)
            time.sleep(3)
        except Exception:
            logger.warning(f"CMBI: failed to load {url}")
            return None

        html = page.evaluate("document.documentElement.outerHTML")
        body_text = page.inner_text("body")

        # Extract fund name from title or H1
        fund_name_cn = fund_entry.get("name_cn", "")
        if not fund_name_cn:
            h1_match = re.search(r'(招银国际[^\n]{5,50})', body_text)
            if h1_match:
                fund_name_cn = h1_match.group(1).strip()

        # ── Parse all tables ────────────────────────────────────
        all_tables = page.evaluate("""() => {
            return Array.from(document.querySelectorAll('table')).map(t => {
                const rows = Array.from(t.querySelectorAll('tr'));
                return rows.map(r =>
                    Array.from(r.querySelectorAll('td, th'))
                        .map(c => (c.textContent || '').trim())
                );
            });
        }""")

        isins = []
        share_classes_detail = []
        fund_info = {}
        detail_nav_entries = []
        seen_isins = set()

        for table in all_tables:
            if len(table) < 2:
                continue

            header = table[0]
            first_header = header[0] if header else ""

            # ── ISIN table (分级, 货币, 派息方式, 发行日期, 彭博标签, ISIN) ──
            if "ISIN" in header and "彭博标签" in header:
                isin_idx = next((i for i, h in enumerate(header) if h == "ISIN"), -1)
                bloom_idx = next((i for i, h in enumerate(header) if "彭博" in h), -1)
                sc_idx = next((i for i, h in enumerate(header) if "分级" in h), 0)
                ccy_idx = next((i for i, h in enumerate(header) if "货币" in h), -1)
                date_idx = next((i for i, h in enumerate(header) if "发行日期" in h), -1)

                for row in table[1:]:
                    if len(row) <= max(isin_idx, 0):
                        continue

                    isin = row[isin_idx] if isin_idx >= 0 and len(row) > isin_idx else ""
                    sc_name = row[sc_idx] if sc_idx >= 0 and len(row) > sc_idx else ""
                    ccy = row[ccy_idx] if ccy_idx >= 0 and len(row) > ccy_idx else ""
                    bloomberg = row[bloom_idx] if bloom_idx >= 0 and len(row) > bloom_idx else ""
                    launch_date = row[date_idx] if date_idx >= 0 and len(row) > date_idx else ""

                    # NAV may be in extra column beyond ISIN
                    nav_val = None
                    if len(row) > isin_idx + 1 and isin_idx >= 0:
                        extra = row[isin_idx + 1]
                        try:
                            nav_val = float(extra.replace(',', ''))
                        except (ValueError, TypeError):
                            pass

                    # Validate ISIN
                    if not re.match(r'^HK\d{10}$', isin):
                        continue

                    if isin not in seen_isins:
                        seen_isins.add(isin)
                        isins.append(isin)

                        share_classes_detail.append({
                            "share_class_name": sc_name,
                            "isin": isin,
                            "currency": self._parse_currency(ccy),
                            "bloomberg_ticker": bloomberg,
                            "launch_date": launch_date,
                        })

                        if nav_val and nav_val > 0:
                            detail_nav_entries.append({
                                "share_class": sc_name,
                                "nav": nav_val,
                                "nav_currency": self._parse_currency(ccy),
                                "source": "cmbi_website",
                            })

            # ── Fund Info table (资产类别, 基本货币, etc.) ──
            elif first_header in ("资产类别",):
                for row in table:
                    if len(row) < 2:
                        continue
                    key = row[0].strip()
                    val = row[1].strip()
                    if key == "资产类别":
                        fund_info["fund_type_cn"] = val
                        fund_info["fund_type"] = self._ASSET_CLASS_MAP.get(val, "")
                    elif key == "基本货币":
                        for cur in ["USD", "HKD", "RMB", "CNY", "EUR", "GBP", "AUD", "SGD"]:
                            if cur in val:
                                fund_info["base_currency"] = cur
                                break
                    elif key == "交易频率":
                        fund_info["dealing_frequency"] = val

        # Base currency fallback
        base_ccy = fund_info.get("base_currency", "USD")
        if not base_ccy and share_classes_detail:
            base_ccy = share_classes_detail[0].get("currency", "USD")

        return {
            "fund_name_cn": fund_name_cn,
            "product_url": url,
            "source_type": "manager_website",
            "isins": isins,
            "share_classes_detail": share_classes_detail,
            "detail_nav_entries": detail_nav_entries,
            "base_currency": base_ccy,
            "fund_type": fund_info.get("fund_type", ""),
            "fund_type_cn": fund_info.get("fund_type_cn", ""),
            "dealing_frequency": fund_info.get("dealing_frequency", ""),
            "listing_nav_entries": fund_entry.get("nav_entries", []),
            "listing_share_classes": fund_entry.get("share_classes", []),
        }

    def get_fund_list(self) -> List[Dict[str, Any]]:
        """Discover and parse all CMBI fund pages."""
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
            fund_entries = self._parse_listing_page(page)
            logger.info(f"CMBI: discovered {len(fund_entries)} fund URLs")

            funds = []
            for entry in fund_entries:
                detail = self._parse_fund_detail(page, entry)
                if detail:
                    isin_count = len(detail.get("isins", []))
                    nav_count = len(detail.get("detail_nav_entries", []))
                    listing_nav_count = len(detail.get("listing_nav_entries", []))
                    logger.info(
                        f"CMBI: {detail['fund_name_cn'][:50]} — "
                        f"{isin_count} ISINs, "
                        f"{nav_count} detail NAVs, "
                        f"{listing_nav_count} listing NAVs"
                    )
                    funds.append(detail)

            return funds

        finally:
            context.close()

    # ── Name Matching ──────────────────────────────────────────

    def _match_fund_name(self, conn, fund_name_cn: str) -> Optional[int]:
        """Match CMBI fund name to hk_funds.id.

        Fund names on the website are in Chinese, e.g.:
          - "招银国际美元货币市场基金" → CMB USD Money Market Fund
          - "招银国际港元货币市场基金" → CMB HKD Money Market Fund
          - "招银国际投资级债券基金" → CMB Investment Grade Bond Fund

        Matching strategy: use word-level keywords from Chinese name,
        combined with manager constraint.
        """
        if not fund_name_cn:
            return None

        name = fund_name_cn.strip()
        candidates = [name]

        # Add stripped version without "招银国际" prefix
        for prefix in ["招银国际"]:
            if name.startswith(prefix):
                stripped = name[len(prefix):]
                if stripped not in candidates:
                    candidates.append(stripped)

        for candidate in candidates:
            c = re.sub(r"\s+", " ", candidate.strip())

            for query, params in [
                ("LOWER(fund_name_cn) = ?", [c]),
                ("LOWER(fund_name_cn) LIKE ?", [f"%{c}%"]),
                ("LOWER(fund_name_en) LIKE ?", [f"%{c}%"]),
            ]:
                # Skip overly broad LIKE queries
                if "LIKE" in query and len(c) < 4:
                    continue
                try:
                    row = conn.execute(
                        f"""SELECT id, fund_name_en, fund_name_cn FROM hk_funds
                           WHERE {query} AND is_active = true
                           {self._CMBI_MANAGER_SQL}
                           LIMIT 1""",
                        params,
                    ).fetchone()
                    if row:
                        return row[0]
                except Exception:
                    continue

            # Word-level matching on Chinese characters
            words = [
                w for w in c
                if w not in ("招", "银", "国", "际", "基", "金", "（", "）", "(", ")")
            ]
            if len(words) >= 3:
                pattern = "%" + "%".join(words) + "%"
                try:
                    row = conn.execute(
                        f"""SELECT id FROM hk_funds
                           WHERE LOWER(fund_name_cn) LIKE ?
                           AND is_active = true
                           {self._CMBI_MANAGER_SQL}
                           LIMIT 1""",
                        [pattern],
                    ).fetchone()
                    if row:
                        return row[0]
                except Exception:
                    continue

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
                fund_name_cn = detail.get("fund_name_cn", "")
                isins = detail.get("isins", [])
                share_classes_detail = detail.get("share_classes_detail", [])
                detail_navs = detail.get("detail_nav_entries", [])
                listing_navs = detail.get("listing_nav_entries", [])
                base_ccy = detail.get("base_currency", "USD")

                if not isins and not fund_name_cn:
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
                    hk_fund_id = self._match_fund_name(conn, fund_name_cn)

                if not hk_fund_id:
                    logger.info(
                        f"  [{idx + 1}/{len(fund_details)}] "
                        f"no match: {fund_name_cn[:50]}"
                    )
                    continue

                stats["matched"] += 1

                # Store fund details
                data = {
                    "fund_name_cn": fund_name_cn,
                    "product_url": detail.get("product_url", ""),
                    "source_type": "manager_website",
                }
                if detail.get("fund_type"):
                    data["fund_type"] = detail["fund_type"]
                if detail.get("base_currency"):
                    data["currency"] = detail["base_currency"]
                if detail.get("dealing_frequency"):
                    data["dealing_frequency"] = detail["dealing_frequency"]

                if update_fund_from_manager(conn, hk_fund_id, data):
                    stats["details_updated"] += 1

                # Store ISINs and share classes from detail pages
                for sc in share_classes_detail:
                    sc_isin = sc.get("isin", "")
                    sc_data = {
                        "share_class_name": sc.get("share_class_name", ""),
                        "isin": sc_isin,
                        "currency": self._parse_currency(
                            sc.get("currency", base_ccy)
                        ),
                        "source": "cmbi_website",
                    }
                    upsert_share_classes(conn, hk_fund_id, [sc_data])
                    stats["share_classes_stored"] += 1

                    if sc_isin:
                        update_fund_from_manager(
                            conn, hk_fund_id, {"isin": sc_isin}
                        )
                        stats["isins_updated"] += 1

                # Store NAVs from detail pages
                if detail_navs:
                    nav_list = []
                    for nav_entry in detail_navs:
                        nav_list.append({
                            "nav": nav_entry["nav"],
                            "nav_date": today,
                            "nav_currency": nav_entry["nav_currency"],
                            "source": "cmbi_website",
                        })
                    n = upsert_nav_history(conn, hk_fund_id, nav_list)
                    stats["navs_stored"] += n

                # Also store NAVs from listing page
                if listing_navs:
                    listing_nav_list = []
                    for nav_entry in listing_navs:
                        nav_date = nav_entry.get("nav_date") or today
                        listing_nav_list.append({
                            "nav": nav_entry["nav"],
                            "nav_date": nav_date,
                            "nav_currency": nav_entry["nav_currency"],
                            "source": "cmbi_website",
                        })
                    n = upsert_nav_history(conn, hk_fund_id, listing_nav_list)
                    stats["navs_stored"] += n

            logger.info(
                f"CMBI scrape complete: "
                f"Matched={stats['matched']}/{stats['funds_found']}, "
                f"ISINs={stats['isins_updated']}, "
                f"NAVs={stats['navs_stored']}, "
                f"ShareClasses={stats['share_classes_stored']}, "
                f"Details={stats['details_updated']}"
            )

        except Exception as e:
            logger.error(f"CMBI scrape failed: {e}")
            stats["errors"] += 1
        finally:
            self._close_browser()

        return stats
