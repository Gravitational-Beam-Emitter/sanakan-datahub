"""
Pickers Capital Management Limited connector.

Extracts fund data from pickerscapital.com via Playwright DOM scraping.
The site is a WordPress site with fund data in embedded JS on a single page.

Strategy:
  1. Visit the C Fund page (/cfund)
  2. Extract share classes + ISINs from embedded JavaScript
  3. Select each share class via dropdown, wait for AJAX NAV update
  4. Extract NAV, NAV date, and ISIN from the updated DOM
  5. Match to hk_funds by ISIN first, then by name with Pickers constraint

CE: BDW926 — Pickers Capital Management Limited
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from hk_funds.manager_connectors.base import BaseManagerConnector, register_connector

logger = logging.getLogger("hk_funds.manager_connectors.pickers")

FUND_PAGE_URL = "https://www.pickerscapital.com/cfund"


@register_connector
class PickersConnector(BaseManagerConnector):
    """Extracts fund data from Pickers Capital Management website."""

    manager_ce_numbers = ["BDW926"]
    base_url = "https://www.pickerscapital.com"

    request_delay: float = 1.0
    request_timeout: int = 30

    _PICKERS_MANAGER_SQL = (
        "AND (LOWER(fund_manager_name_en) LIKE '%pickers%')"
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

    def _extract_share_classes_from_js(self, html: str) -> List[Dict[str, str]]:
        """Extract share classes and ISINs from embedded JavaScript.

        The JS contains mappings like:
          if (value == "Class A HKD (Dist)"){
              type = "hkd_dist"; isin = "HK0000563889"; bloomberg = "CFCHEHI HK Equity"
          }else if(value == "Class A RMB (Accu)"){
              type = "rmb_accu"; isin = "HK0000563897"; bloomberg = "CFCCECA HK Equity"
          }else{
              type = "hkd_accu"; isin = "HK0000483724"; bloomberg = "CFCHIEQ HK Equity"
          }

        Each block has both Chinese and English class names separated by ||.
        We extract the English name as it's more useful for the database.
        """
        share_classes = []
        seen_isins = set()

        # Find each complete if/else-if block for a share class.
        # The JS has both Chinese and English names via ||, e.g.:
        #   if(value == "A類港元（派息）" || value == "Class A HKD (Dist)"){
        # We capture ALL value matches and prefer the English (last one).
        block_pattern = re.compile(
            r'(?:if|else\s*if)\s*\(((?:[^)]*?value\s*==\s*"[^"]*"\s*(?:\|\|\s*)?)+)\)\s*\{'
            r'[^}]*?'
            r'type\s*=\s*"([^"]*)"[^;]*;'
            r'[^}]*?'
            r'isin\s*=\s*"(HK\d{10})"[^;]*;'
            r'[^}]*?'
            r'bloomberg\s*=\s*"([^"]*)"',
            re.DOTALL
        )

        for match in block_pattern.finditer(html):
            condition_block = match.group(1)
            fund_type = match.group(2).strip()
            isin = match.group(3).strip()
            bloomberg = match.group(4).strip()

            # Extract all value matches and prefer the English name
            value_matches = re.findall(r'value\s*==\s*"([^"]*)"', condition_block)
            if not value_matches:
                continue

            # Last match is usually the English one; prefer it if it looks English
            eng_name = value_matches[-1].strip()
            for vm in value_matches:
                # Prefer ASCII/English names
                if all(ord(c) < 128 for c in vm):
                    eng_name = vm.strip()
                    break

            if isin in seen_isins:
                continue
            seen_isins.add(isin)

            # Map type to currency
            currency = "HKD"
            if "rmb" in fund_type.lower():
                currency = "CNH"

            share_classes.append({
                "share_class_name": eng_name,
                "isin": isin,
                "bloomberg_ticker": bloomberg,
                "currency": currency,
                "type": fund_type,
            })

        # Find the else (default) block
        else_pattern = re.compile(
            r'\}else\s*\{'
            r'[^}]*?'
            r'type\s*=\s*"([^"]*)"[^;]*;'
            r'[^}]*?'
            r'isin\s*=\s*"(HK\d{10})"[^;]*;'
            r'[^}]*?'
            r'bloomberg\s*=\s*"([^"]*)"',
            re.DOTALL
        )

        for match in else_pattern.finditer(html):
            fund_type = match.group(1).strip()
            isin = match.group(2).strip()
            bloomberg = match.group(3).strip()

            if isin in seen_isins:
                continue
            seen_isins.add(isin)

            currency = "HKD"
            if "rmb" in fund_type.lower():
                currency = "CNH"

            share_classes.append({
                "share_class_name": "Class A HKD (Accu)",
                "isin": isin,
                "bloomberg_ticker": bloomberg,
                "currency": currency,
                "type": fund_type,
            })

        return share_classes

    def _parse_nav_from_text(self, text: str) -> Optional[Dict[str, Any]]:
        """Extract NAV and NAV date from the page text.

        Pattern on page:
          淨值截至 18-06-2026
          A類港元（累算）基金單位:106.0282
        """
        # Find NAV date
        date_match = re.search(
            r'(?:淨值截至|NAV|As of)\s*(\d{2}-\d{2}-\d{4})', text
        )
        nav_date = None
        if date_match:
            nav_date = self._parse_date(date_match.group(1))

        # Find NAV value
        nav_match = re.search(
            r'(?:基金單位|NAV|Net Asset Value)[:\s]*([\d,]+\.\d+)', text
        )
        if not nav_match:
            nav_match = re.search(
                r'(?:基金單位|NAV)[:\s]*(\d+\.\d+)', text
            )
        if nav_match:
            try:
                nav_val = float(nav_match.group(1).replace(',', ''))
                return {
                    "nav": nav_val,
                    "nav_date": nav_date or datetime.now().strftime("%Y-%m-%d"),
                    "source": "pickers_website",
                }
            except (ValueError, TypeError):
                pass

        return None

    def _get_select_options(self, page) -> List[Dict[str, str]]:
        """Get all select options with values and text."""
        return page.evaluate("""() => {
            const select = document.querySelector('select');
            if (!select) return [];
            return Array.from(select.querySelectorAll('option')).map(o => ({
                value: o.value,
                text: (o.textContent || '').trim()
            }));
        }""")

    def get_fund_list(self) -> List[Dict[str, Any]]:
        """Extract C Fund - China Equity data from Pickers Capital website."""
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
            page.goto(FUND_PAGE_URL, wait_until="load", timeout=30000)
            time.sleep(3)

            html = page.evaluate("document.documentElement.outerHTML")
            body = page.inner_text("body")

            # Extract fund name
            fund_name = ""
            # Try page title first
            title = page.title()
            if "C基金" in title or "C Fund" in title:
                # "C基金-中國股票︱最低入場費$100港元︱Pickers 鵬格斯"
                fund_name = title.split("︱")[0].strip()
                # Use English name for DB matching
                fund_name = "C Fund - China Equity"

            if not fund_name:
                fn_match = re.search(
                    r'(C\s*(?:Fund|基金)[^|\n]{0,50})', body
                )
                if fn_match:
                    fund_name = fn_match.group(1).strip()

            if not fund_name:
                fund_name = "C Fund - China Equity"

            # Extract share classes from embedded JS
            share_classes = self._extract_share_classes_from_js(html)

            if not share_classes:
                # Fallback: scrape from select element
                options = self._get_select_options(page)
                # Also extract ISINs from the JS block manually
                all_isins = list(set(re.findall(r'HK\d{10}', html)))
                for i, opt in enumerate(options):
                    isin = all_isins[i] if i < len(all_isins) else ""
                    share_classes.append({
                        "share_class_name": opt.get("text", ""),
                        "isin": isin,
                        "bloomberg_ticker": "",
                        "type": opt.get("value", ""),
                    })

            # Get NAV for default share class
            nav_entries = []
            base_nav = self._parse_nav_from_text(body)
            if base_nav:
                base_nav["nav_currency"] = "HKD"
                nav_entries.append(base_nav)

            # Try to get NAV for other share classes via select
            options = self._get_select_options(page)
            for opt in options:
                opt_val = opt.get("value", "")
                if opt_val and opt_val != options[0].get("value", ""):
                    try:
                        select = page.locator("select").first
                        select.select_option(value=opt_val)
                        time.sleep(2)
                        updated_text = page.inner_text("body")
                        nav_data = self._parse_nav_from_text(updated_text)
                        if nav_data:
                            # Map currency based on share class type
                            if "rmb" in opt_val.lower():
                                nav_data["nav_currency"] = "CNH"
                            else:
                                nav_data["nav_currency"] = "HKD"
                            nav_entries.append(nav_data)
                    except Exception:
                        pass

            result = {
                "fund_name": fund_name,
                "product_url": FUND_PAGE_URL,
                "source_type": "manager_website",
                "isins": [sc["isin"] for sc in share_classes if sc.get("isin")],
                "share_classes": share_classes,
                "nav_entries": nav_entries,
                "base_currency": "HKD",
                "fund_type": "Equity",
            }

            logger.info(
                f"Pickers: {fund_name[:60]} — "
                f"{len(share_classes)} share classes, "
                f"{len(nav_entries)} NAVs"
            )

            return [result]

        finally:
            context.close()

    # ── Name Matching ──────────────────────────────────────────

    def _match_fund_name(self, conn, extracted_name: str) -> Optional[int]:
        """Match Pickers fund name to hk_funds.id.

        Website fund name: "C Fund - China Equity"
        DB: "CFund - China Equity" (without space after C)
        """
        if not extracted_name:
            return None

        name = extracted_name.strip()

        # Generate candidates: "C Fund", "CFund"
        candidates = [name]
        if name.lower().startswith("c fund"):
            candidates.append("CFund" + name[6:])
        elif name.lower().startswith("cfund"):
            candidates.append("C Fund" + name[5:])

        for candidate in candidates:
            c = re.sub(r"\s+", " ", candidate.lower().strip())

            for query, params in [
                ("LOWER(fund_name_en) = ?", [c]),
                ("LOWER(fund_name_en) LIKE ?", [f"%{c}%"]),
            ]:
                row = conn.execute(
                    f"""SELECT id, fund_name_en FROM hk_funds
                       WHERE {query} AND is_active = true
                       {self._PICKERS_MANAGER_SQL}
                       LIMIT 1""",
                    params,
                ).fetchone()
                if row:
                    return row[0]

        return None

    # ── Main Interface ─────────────────────────────────────────

    def get_fund_details(self, isin: str) -> Optional[Dict[str, Any]]:
        """Not implemented — data extracted in get_fund_list."""
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
                base_ccy = detail.get("base_currency", "HKD")

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
                    "currency": base_ccy,
                }
                if detail.get("fund_type"):
                    data["fund_type"] = detail["fund_type"]

                if update_fund_from_manager(conn, hk_fund_id, data):
                    stats["details_updated"] += 1

                # Store ISINs and share classes
                for sc in share_classes:
                    sc_data = {
                        "share_class_name": sc.get("share_class_name", ""),
                        "isin": sc.get("isin", ""),
                        "currency": sc.get("currency", base_ccy),
                        "bloomberg_ticker": sc.get("bloomberg_ticker", ""),
                        "source": "pickers_website",
                    }
                    upsert_share_classes(conn, hk_fund_id, [sc_data])
                    stats["share_classes_stored"] += 1

                    if sc.get("isin"):
                        update_fund_from_manager(
                            conn, hk_fund_id, {"isin": sc["isin"]}
                        )
                        stats["isins_updated"] += 1

                # Store NAVs
                if nav_entries:
                    navs_to_store = []
                    for nav in nav_entries:
                        navs_to_store.append({
                            "nav": nav["nav"],
                            "nav_date": nav.get("nav_date", today),
                            "nav_currency": nav.get("nav_currency", base_ccy),
                            "source": "pickers_website",
                        })
                    if navs_to_store:
                        n = upsert_nav_history(conn, hk_fund_id, navs_to_store)
                        stats["navs_stored"] += n

            logger.info(
                f"Pickers scrape complete: "
                f"Matched={stats['matched']}/{stats['funds_found']}, "
                f"ISINs={stats['isins_updated']}, "
                f"NAVs={stats['navs_stored']}, "
                f"ShareClasses={stats['share_classes_stored']}, "
                f"Details={stats['details_updated']}"
            )

        except Exception as e:
            logger.error(f"Pickers scrape failed: {e}")
            stats["errors"] += 1
        finally:
            self._close_browser()

        return stats
