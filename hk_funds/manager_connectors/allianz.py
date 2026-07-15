"""
Allianz Global Investors connector.

Extracts fund data from hk.allianzgi.com via Playwright DOM scraping.
The retail funds page is a React SPA with a paginated table (411 share classes,
~50 distinct funds). Each fund row embeds a data-fund-info JSON attribute
containing ISIN, NAV, currency, asset class, etc.

Strategy:
  1. Load the retail funds page with Playwright
  2. Accept cookie consent + role selection (Individual Investor)
  3. Extract all data from data-fund-info JSON attributes in anchor tags
  4. Click through pagination until all funds are collected
  5. Match to hk_funds by ISIN

CE: BFE699 — Allianz Global Investors Asia Pacific Limited
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from hk_funds.manager_connectors.base import BaseManagerConnector, register_connector

logger = logging.getLogger("hk_funds.manager_connectors.allianz")

FUNDS_PAGE_URL = "https://hk.allianzgi.com/en/retail/products-solutions/retail-funds"


@register_connector
class AllianzConnector(BaseManagerConnector):
    """Extracts fund data from Allianz Global Investors HK website using Playwright.

    All fund data is embedded in data-fund-info JSON attributes on anchor tags
    within the React-rendered fund table. Pagination requires clicking through
    ~17 pages (25 entries each, 411 total).
    """

    manager_ce_numbers = ["BFE699"]
    base_url = "https://hk.allianzgi.com"

    request_delay: float = 2.0
    request_timeout: int = 60

    _ALLIANZ_MANAGER_SQL = (
        "AND (LOWER(fund_manager_name_en) LIKE '%allianz%'"
        " OR LOWER(fund_manager_name_en) LIKE '%allianz global investors%')"
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

    def _setup_page(self, page):
        """Handle cookie consent and role selection overlays."""
        # Accept cookies
        try:
            btn = page.locator('button:has-text("Accept All Cookies")')
            if btn.is_visible(timeout=3000):
                btn.click()
                time.sleep(1)
        except Exception:
            pass

        # Select Individual Investor role
        try:
            btn = page.locator('button:has-text("Individual Investor")')
            if btn.is_visible(timeout=3000):
                btn.click()
                time.sleep(2)
        except Exception:
            pass

        # Dismiss T&C overlay by removing it from DOM
        page.evaluate("""() => {
            const overlay = document.getElementById('roleTnCOverlay');
            if (overlay) overlay.remove();
            const backdrops = document.querySelectorAll('.c-overlay.is-open, .modal-backdrop');
            backdrops.forEach(b => b.remove());
        }""")
        time.sleep(1)

    def _extract_funds_from_page(self, page) -> List[Dict[str, Any]]:
        """Extract fund data from data-fund-info JSON attributes on the current page."""
        entries = page.evaluate("""() => {
            const anchors = Array.from(
                document.querySelectorAll('.c-fund-table-container a[data-fund-info]')
            );
            return anchors.map(a => {
                try {
                    const raw = a.getAttribute('data-fund-info');
                    // The JSON uses HTML entities for quotes and angle brackets
                    const cleaned = raw
                        .replace(/&quot;/g, '"')
                        .replace(/&lt;/g, '<')
                        .replace(/&gt;/g, '>')
                        .replace(/&amp;/g, '&');
                    return JSON.parse(cleaned);
                } catch(e) {
                    return null;
                }
            }).filter(x => x !== null);
        }""")

        funds = []
        for e in entries:
            isin = (e.get("Isin") or "").strip()
            fund_name = (e.get("FundNameColumn") or e.get("ProductNameTranslated") or "").strip()

            if not fund_name:
                continue

            nav_str = (e.get("Nav") or "").strip().replace(",", "")
            nav_date_str = (e.get("UnitPricesPerDate") or "").strip()  # DD/MM/YYYY

            fund = {
                "fund_name": fund_name,
                "isin": isin,
                "asset_class": e.get("AssetClass", ""),
                "share_class_name": e.get("DisplayShareclass", ""),
                "currency": e.get("DisplayCurrency", ""),
                "nav_date": nav_date_str,
                "source_type": "manager_website",
                "product_url": (
                    f"https://hk.allianzgi.com/en-hk/retail/products-solutions/"
                    f"retail-funds/{e.get('viewdetail', '')}"
                ) if e.get("viewdetail") else FUNDS_PAGE_URL,
            }

            if nav_str and nav_str != "Not Available":
                try:
                    fund["nav"] = float(nav_str)
                except ValueError:
                    pass

            # 12-month range
            high_str = (e.get("High12MonthsPrice") or "").strip().replace(",", "")
            low_str = (e.get("Low12MonthsPrice") or "").strip().replace(",", "")
            if high_str:
                try:
                    fund["high_52w"] = float(high_str)
                except ValueError:
                    pass
            if low_str:
                try:
                    fund["low_52w"] = float(low_str)
                except ValueError:
                    pass

            # Daily change
            change_str = (e.get("NavChangePct") or "").strip()
            if change_str:
                change_match = re.search(r"([\d.-]+)\s*%", change_str)
                if change_match:
                    try:
                        fund["nav_change_pct"] = float(change_match.group(1))
                    except ValueError:
                        pass

            funds.append(fund)

        return funds

    # ── Fund List ───────────────────────────────────────────────

    def get_fund_list(self) -> List[Dict[str, Any]]:
        """Load the funds page, click through pagination, extract all fund data."""
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
            page.goto(FUNDS_PAGE_URL, wait_until="domcontentloaded", timeout=30000)
            time.sleep(3)
            self._setup_page(page)

            # Wait for table
            try:
                page.wait_for_selector(
                    '.c-fund-table-container a[data-fund-info]',
                    timeout=15000,
                )
            except Exception:
                logger.warning("Fund table did not load")
                return []

            time.sleep(3)

            all_funds = []
            seen_isins = set()
            page_num = 1
            no_new_pages = 0

            while True:
                # Extract funds from current page
                funds = self._extract_funds_from_page(page)

                # Deduplicate by ISIN
                new_funds = []
                for f in funds:
                    isin = f.get("isin", "")
                    if isin and isin not in seen_isins:
                        seen_isins.add(isin)
                        new_funds.append(f)

                all_funds.extend(new_funds)
                logger.info(
                    f"Allianz page {page_num}: {len(funds)} entries, "
                    f"{len(new_funds)} new ISINs (total unique ISINs: {len(seen_isins)})"
                )

                # Stop if no new ISINs found this page
                if len(new_funds) == 0:
                    no_new_pages += 1
                    if no_new_pages >= 2:
                        logger.info("No new ISINs for 2 pages, stopping")
                        break
                else:
                    no_new_pages = 0

                # Stop if we have enough (411 share classes per page listing)
                if len(seen_isins) >= 411:
                    logger.info("Reached expected total (411 ISINs)")
                    break

                # Try to go to next page
                try:
                    next_btn = page.locator(
                        'a[aria-label="Go to next page"]'
                    ).first

                    if not next_btn.is_visible(timeout=1000):
                        logger.info("No next page button")
                        break

                    # Check if disabled
                    parent = next_btn.locator("..")
                    parent_class = (parent.get_attribute("class") or "")
                    if "disabled" in parent_class or "is-disabled" in parent_class:
                        logger.info("Next button disabled, reached last page")
                        break

                    # Force click to bypass any remaining overlays
                    next_btn.click(force=True)
                    time.sleep(3)
                    page_num += 1

                    if page_num > 30:
                        logger.warning("Page limit reached (30)")
                        break

                except Exception as e:
                    logger.info(f"No more pages: {e}")
                    break

            logger.info(f"Allianz: total {len(all_funds)} funds from {page_num} pages")

            # Deduplicate by ISIN (group share classes under base funds)
            return all_funds

        finally:
            context.close()

    # ── Name Matching ──────────────────────────────────────────

    def _match_fund_name(self, conn, extracted_name: str) -> Optional[int]:
        """Match Allianz fund name to hk_funds.id.

        Allianz website names follow patterns like:
          - "Allianz All China Equity - Class AT (USD) Acc."
          - "Allianz American Income - Class AM (HKD) Dis."
          - "Allianz AI Income (...renamed...) - Class AM (H2-RMB) Dis."

        SFC register names are base fund names without share class suffixes.
        """
        if not extracted_name:
            return None

        name = extracted_name.strip()

        # Remove rename notes like "(renamed and repositioned from ...)"
        name = re.sub(r'\s*\(renamed[^)]+\)', '', name, flags=re.IGNORECASE)

        # Strip share class suffix: " - Class XX (CCY) Acc/Dis"
        name = re.sub(
            r'\s*[-–]\s*Class\s+[A-Za-z0-9\s-]+$',
            '', name, flags=re.IGNORECASE,
        )

        # Also try without "Allianz " prefix
        candidates = [name]
        for prefix in ["Allianz ", "Allianz Global Investors "]:
            if name.lower().startswith(prefix.lower()):
                candidates.append(name[len(prefix):])

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
                       {self._ALLIANZ_MANAGER_SQL}
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
                    "fund", "class", "etf", "allianz", "global",
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
                       {self._ALLIANZ_MANAGER_SQL}
                       LIMIT 1""",
                    params,
                ).fetchone()
                if row:
                    return row[0]

        return None

    # ── Main Interface ─────────────────────────────────────────

    def get_fund_details(self, isin: str) -> Optional[Dict[str, Any]]:
        """Not implemented — fund detail pages are separate SPA routes."""
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
            all_entries = self.get_fund_list()
            stats["funds_found"] = len(all_entries)

            # Group by base fund (dedup by base fund name)
            # Track processed ISINs + base fund IDs
            processed_fund_ids: set = set()
            processed_isins: set = set()

            for idx, entry in enumerate(all_entries):
                fund_name = entry.get("fund_name", "")
                isin = entry.get("isin", "")

                if not fund_name:
                    continue

                # Match to SFC register
                hk_fund_id = None

                # Try ISIN match first
                if isin:
                    row = conn.execute(
                        "SELECT id FROM hk_funds WHERE isin = ?",
                        [isin],
                    ).fetchone()
                    if row:
                        hk_fund_id = row[0]

                # Fallback to name matching
                if not hk_fund_id:
                    hk_fund_id = self._match_fund_name(conn, fund_name)

                if not hk_fund_id:
                    continue

                stats["matched"] += 1

                # Store ISIN
                if isin and isin not in processed_isins:
                    update_fund_from_manager(conn, hk_fund_id, {"isin": isin})
                    stats["isins_updated"] += 1
                    processed_isins.add(isin)

                # Store share class
                sc_name = entry.get("share_class_name", "")
                if isin and sc_name:
                    share_class_data = {
                        "share_class_name": sc_name,
                        "isin": isin,
                        "currency": entry.get("currency", ""),
                        "source": "allianz_website",
                    }
                    upsert_share_classes(conn, hk_fund_id, [share_class_data])
                    stats["share_classes_stored"] += 1

                # Store fund details (only once per fund)
                if hk_fund_id not in processed_fund_ids:
                    data = {
                        "isin": isin,
                        "nav_currency": entry.get("currency"),
                        "asset_class": entry.get("asset_class"),
                        "product_url": entry.get("product_url", FUNDS_PAGE_URL),
                        "source_type": "manager_website",
                    }
                    if update_fund_from_manager(conn, hk_fund_id, data):
                        stats["details_updated"] += 1
                    processed_fund_ids.add(hk_fund_id)

                # Store NAV
                nav = entry.get("nav")
                nav_date = entry.get("nav_date", "")
                if nav and nav_date:
                    parsed_date = self._parse_date(nav_date)
                    if parsed_date:
                        n = upsert_nav_history(conn, hk_fund_id, [{
                            "nav": nav,
                            "nav_date": parsed_date,
                            "nav_currency": entry.get("currency", "HKD"),
                            "source": "allianz_website",
                        }])
                        stats["navs_stored"] += n

                if (idx + 1) % 50 == 0:
                    logger.info(
                        f"  [{idx + 1}/{len(all_entries)}] "
                        f"Matched={stats['matched']} "
                        f"ISINs={stats['isins_updated']} "
                        f"NAVs={stats['navs_stored']}"
                    )

            logger.info(
                f"Allianz scrape complete: "
                f"Matched={stats['matched']}/{stats['funds_found']}, "
                f"ISINs={stats['isins_updated']}, "
                f"NAVs={stats['navs_stored']}, "
                f"ShareClasses={stats['share_classes_stored']}, "
                f"Details={stats['details_updated']}"
            )

        except Exception as e:
            logger.error(f"Allianz scrape failed: {e}")
            stats["errors"] += 1
        finally:
            self._close_browser()

        return stats
