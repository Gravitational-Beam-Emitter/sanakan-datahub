"""
Cinda International Asset Management connector.

Extracts fund data from cinda.com.hk via Playwright DOM scraping.
The site is a static PHP website with fund data rendered server-side
in hidden HTML tabs (Overview, Daily NAV, Key Performance, etc.).

Strategy:
  1. Visit the Shinya public fund page
  2. Extract fund data from raw HTML (all tab content is in page source)
  3. Parse NAV table for share classes, ISINs, NAVs, Bloomberg tickers
  4. Parse Key Performance tab for manager, custodian, fees metadata
  5. Match to hk_funds by ISIN first, then by name with Cinda constraint

CE: WEBB-15352 — Cinda International Asset Management Limited
    (Webb-site ID; actual SFC CE to be verified)
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from hk_funds.manager_connectors.base import BaseManagerConnector, register_connector

logger = logging.getLogger("hk_funds.manager_connectors.cinda")

FUND_PAGE_URL = "https://www.cinda.com.hk/en/shinya_public_fund.php"


@register_connector
class CindaConnector(BaseManagerConnector):
    """Extracts fund data from Cinda International HK website."""

    manager_ce_numbers = ["WEBB-15352"]  # Webb-site ID; actual SFC CE TBD
    base_url = "https://www.cinda.com.hk"

    request_delay: float = 1.0
    request_timeout: int = 30

    _CINDA_MANAGER_SQL = (
        "AND (LOWER(fund_manager_name_en) LIKE '%cinda%')"
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

    def _extract_fund_data(self, page) -> List[Dict[str, Any]]:
        """Extract all fund data from the Shinya public fund page.

        The page has a single fund with tabbed content (Overview, Daily NAV,
        Key Performance, etc.). All tab content is in the page source HTML
        but only the active tab is visible.  We use page.evaluate() to parse
        all tables regardless of visibility.
        """
        page.goto(FUND_PAGE_URL, wait_until="networkidle", timeout=30000)
        time.sleep(3)

        html = page.evaluate("document.documentElement.outerHTML")
        body_text = page.inner_text("body")

        results = []

        # ── Fund name ──────────────────────────────────────────
        fund_name = ""
        # Try fund-nav-box text (in tab navigation)
        fn_match = re.search(
            r'<div class="fund-nav-box">\s*(.*?)\s*</div>', html, re.DOTALL
        )
        if fn_match:
            fund_name = re.sub(r'<!--.*?-->', '', fn_match.group(1)).strip()
            fund_name = re.sub(r'<[^>]+>', '', fund_name).strip()
        if not fund_name:
            # Try fund-max-title
            fn_match = re.search(r'class="fund-max-title">(.*?)</div>', html)
            if fn_match:
                fund_name = re.sub(r'<[^>]+>', '', fn_match.group(1)).strip()
        if not fund_name:
            # Fallback: find "Shinya ... Fund" in body
            for pattern in [
                r'(Shinya\s+USD\s+Money\s+Market\s+Fund)',
                r'(Shinya\s+Value\s+Fixed\s+Income\s+Fund)',
                r'(Shinya\s+[A-Za-z\s]{5,50}?(?:Fund|Company))',
            ]:
                fn_match = re.search(pattern, body_text)
                if fn_match:
                    fund_name = fn_match.group(1).strip()
                    break

        # ── Parse all tables via JavaScript ────────────────────
        all_tables = page.evaluate("""() => {
            const tables = document.querySelectorAll('table');
            return Array.from(tables).map(t => {
                const rows = Array.from(t.querySelectorAll('tr'));
                return rows.map(r =>
                    Array.from(r.querySelectorAll('td, th'))
                        .map(c => (c.textContent || '').replace(/\\s+/g, ' ').trim())
                );
            });
        }""")

        nav_entries = []
        share_classes = []
        currency = "USD"
        fund_info = {}
        fund_type = ""

        for table_rows in all_tables:
            if len(table_rows) < 2:
                continue

            first_cell = table_rows[0][0] if table_rows[0] else ""

            # ── NAV table ──────────────────────────────────────
            if first_cell == 'Share Class' and len(table_rows[0]) >= 3:
                headers = [h.lower() for h in table_rows[0]]
                sc_idx = next((i for i, h in enumerate(headers) if 'share' in h or 'class' in h), 0)
                ccy_idx = next((i for i, h in enumerate(headers) if 'currency' in h), -1)
                nav_idx = next((i for i, h in enumerate(headers) if 'nav' in h), -1)
                date_idx = next((i for i, h in enumerate(headers) if 'date' in h), -1)
                bloom_idx = next((i for i, h in enumerate(headers)
                                  if 'bloomberg' in h or 'ticker' in h), -1)
                isin_idx = next((i for i, h in enumerate(headers) if 'isin' in h), -1)

                for row in table_rows[1:]:
                    if len(row) < max(sc_idx, isin_idx, nav_idx) + 1:
                        continue
                    sc_name = row[sc_idx] if sc_idx >= 0 else ""
                    sc_ccy = row[ccy_idx] if ccy_idx >= 0 else "USD"
                    isin = row[isin_idx] if isin_idx >= 0 else ""
                    nav_str = row[nav_idx] if nav_idx >= 0 else ""
                    nav_date = row[date_idx] if date_idx >= 0 else ""
                    bloomberg = row[bloom_idx] if bloom_idx >= 0 else ""

                    if sc_ccy.upper() in ("USD", "HKD", "CNY", "EUR", "GBP", "AUD", "SGD"):
                        currency = sc_ccy.upper()
                        if currency == "RMB":
                            currency = "CNY"

                    if isin and re.match(r'^HK\d{10}$', isin):
                        share_classes.append({
                            "share_class_name": sc_name,
                            "isin": isin,
                            "currency": self._parse_currency(sc_ccy),
                            "bloomberg_ticker": bloomberg,
                        })

                    try:
                        nav_val = float(nav_str.replace(',', ''))
                        parsed_date = self._parse_date(nav_date) or nav_date
                        if parsed_date and nav_val > 0:
                            nav_entries.append({
                                "share_class_name": sc_name,
                                "nav": nav_val,
                                "nav_date": parsed_date if re.match(
                                    r'\d{4}-\d{2}-\d{2}', parsed_date
                                ) else nav_date,
                                "nav_currency": self._parse_currency(sc_ccy),
                                "source": "cinda_website",
                            })
                    except (ValueError, TypeError):
                        pass

            # ── Key Info table ─────────────────────────────────
            elif first_cell in ('Manager:', 'Manager: '):
                for row in table_rows:
                    if len(row) < 2:
                        continue
                    key = row[0].rstrip(':').strip().lower()
                    val = row[1].strip() if len(row) > 1 else ""

                    if 'manager' in key and 'fund_manager' not in fund_info and 'management' not in key:
                        fund_info["fund_manager"] = re.sub(r'\s+', ' ', val).strip()
                    elif key == 'custodian' or key == 'custodian ':
                        fund_info["custodian"] = re.sub(r'\s+', ' ', val).strip()
                    elif 'base currency' in key:
                        for cur in ["USD", "HKD", "RMB", "CNY", "EUR", "GBP", "AUD", "SGD"]:
                            if cur in val:
                                currency = cur if cur != "RMB" else "CNY"
                                fund_info["base_currency"] = currency
                                break
                    elif 'launch date' in key or 'inception' in key:
                        parsed = self._parse_date(val)
                        if parsed:
                            fund_info["fund_inception_date"] = parsed
                    elif 'dealing frequency' in key:
                        fund_info["dealing_frequency"] = val
                    elif 'ongoing charges' in key:
                        pct_match = re.search(r'([\d.]+)\s*%', val)
                        if pct_match:
                            fund_info["ongoing_charges_pct"] = float(pct_match.group(1))

        # ── Fund type (from fund name and page content) ────────
        combined = (fund_name + " " + body_text).lower()
        if 'money market' in combined:
            fund_type = "Money Market"
        elif 'fixed income' in combined:
            fund_type = "Fixed Income"
        elif 'equity' in combined:
            fund_type = "Equity"

        # Deduplicate ISINs
        seen_isins = set()
        for sc in share_classes:
            isin = sc.get("isin", "")
            if isin and isin not in seen_isins:
                seen_isins.add(isin)

        result = {
            "fund_name": fund_name,
            "product_url": FUND_PAGE_URL,
            "source_type": "manager_website",
            "isins": list(seen_isins),
            "share_classes": share_classes,
            "nav_entries": nav_entries,
            "base_currency": currency,
            "fund_type": fund_type,
            **fund_info,
        }

        results.append(result)
        return results

    def get_fund_list(self) -> List[Dict[str, Any]]:
        """Parse the Shinya public fund page for all fund data."""
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
            funds = self._extract_fund_data(page)
            logger.info(f"Cinda: extracted {len(funds)} funds")

            for f in funds:
                logger.info(
                    f"Cinda: {f['fund_name'][:60]} — "
                    f"{len(f.get('isins', []))} ISINs, "
                    f"{len(f.get('nav_entries', []))} NAVs"
                )

            return funds

        finally:
            context.close()

    # ── Name Matching ──────────────────────────────────────────

    def _match_fund_name(self, conn, extracted_name: str) -> Optional[int]:
        """Match Cinda fund name to hk_funds.id.

        Website names follow patterns like:
          - "Shinya USD Money Market Fund"
        """
        if not extracted_name:
            return None

        name = extracted_name.strip()

        candidates = [name]

        # Remove "Shinya " prefix
        for prefix in ["Shinya "]:
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
                       {self._CINDA_MANAGER_SQL}
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
                    "fund", "class", "etf", "shinya", "money",
                    "market", "cinda", "international",
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
                       {self._CINDA_MANAGER_SQL}
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
                if detail.get("base_currency"):
                    data["currency"] = detail["base_currency"]
                if detail.get("fund_inception_date"):
                    data["fund_inception_date"] = detail["fund_inception_date"]
                if detail.get("ongoing_charges_pct"):
                    data["ongoing_charges_pct"] = detail["ongoing_charges_pct"]

                if update_fund_from_manager(conn, hk_fund_id, data):
                    stats["details_updated"] += 1

                # Store ISINs and share classes
                for sc in share_classes:
                    sc_data = {
                        "share_class_name": sc.get("share_class_name", ""),
                        "isin": sc.get("isin", ""),
                        "currency": self._parse_currency(sc.get("currency", base_ccy)),
                        "source": "cinda_website",
                    }
                    upsert_share_classes(conn, hk_fund_id, [sc_data])
                    stats["share_classes_stored"] += 1

                    if sc.get("isin"):
                        update_fund_from_manager(
                            conn, hk_fund_id, {"isin": sc["isin"]}
                        )
                        stats["isins_updated"] += 1

                # Store NAVs (grouped by share class)
                if nav_entries:
                    navs_to_store = []
                    for nav in nav_entries:
                        nav_date = nav.get("nav_date", today)
                        if not re.match(r'\d{4}-\d{2}-\d{2}', nav_date):
                            nav_date = today
                        navs_to_store.append({
                            "nav": nav["nav"],
                            "nav_date": nav_date,
                            "nav_currency": self._parse_currency(
                                nav.get("nav_currency", base_ccy)
                            ),
                            "source": "cinda_website",
                        })
                    if navs_to_store:
                        n = upsert_nav_history(conn, hk_fund_id, navs_to_store)
                        stats["navs_stored"] += n

            logger.info(
                f"Cinda scrape complete: "
                f"Matched={stats['matched']}/{stats['funds_found']}, "
                f"ISINs={stats['isins_updated']}, "
                f"NAVs={stats['navs_stored']}, "
                f"ShareClasses={stats['share_classes_stored']}, "
                f"Details={stats['details_updated']}"
            )

        except Exception as e:
            logger.error(f"Cinda scrape failed: {e}")
            stats["errors"] += 1
        finally:
            self._close_browser()

        return stats
