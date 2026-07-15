"""
Ninety One connector.

Extracts fund ISINs, share classes, and NAVs from ninetyone.com.
The site is a React SPA behind Cloudflare with a region selection gate.

Strategy:
  1. Load the fund prices page with Playwright (bypasses Cloudflare)
  2. Accept OneTrust cookies (force click)
  3. Accept region selector (force click)
  4. Parse all 29 GSF Lux SICAV funds with ~130 share classes from DOM text
  5. Extract ISINs, share class names, NAVs, NAV dates, and categories
  6. Match to hk_funds by ISIN first, then by name with Ninety One constraint

CE: WEBB-2094652 — Ninety One Hong Kong Limited
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from hk_funds.manager_connectors.base import BaseManagerConnector, register_connector

logger = logging.getLogger("hk_funds.manager_connectors.ninety_one")

FUND_LISTING_URL = (
    "https://ninetyone.com/en/hong-kong/funds-literature/funds"
)

# Map abbreviated category letters to fund types
CATEGORY_MAP = {
    "B": "Bond",
    "E": "Equity",
    "M": "Multi-Asset",
    "MM": "Money Market",
}


@register_connector
class NinetyOneConnector(BaseManagerConnector):
    """Extracts fund data from Ninety One Hong Kong website."""

    manager_ce_numbers = ["WEBB-2094652"]
    base_url = "https://ninetyone.com"

    request_delay: float = 1.0
    request_timeout: int = 30

    _NINETYONE_MANAGER_SQL = (
        "AND (LOWER(fund_manager_name_en) LIKE '%ninety one%'"
        " OR LOWER(fund_manager_name_en) LIKE '%ninetyone%')"
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
            self._browser = self._playwright.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
        return self._browser

    def _close_browser(self):
        if self._browser:
            self._browser.close()
            self._browser = None
        if self._playwright:
            self._playwright.stop()
            self._playwright = None

    # ── Fund Data Extraction ───────────────────────────────────

    def _load_fund_page(self, page) -> str:
        """Load the fund listing page, accept cookies and region, return body text."""
        try:
            page.goto(FUND_LISTING_URL, wait_until="load", timeout=60000)
        except Exception:
            logger.warning("Ninety One: page load timed out, continuing...")

        time.sleep(3)

        # Accept OneTrust cookies (force click bypasses dark filter overlay)
        try:
            page.click("#onetrust-accept-btn-handler", force=True, timeout=10000)
            time.sleep(1)
        except Exception:
            pass

        # Accept region selector
        try:
            page.click("text=Agree & enter site", force=True, timeout=10000)
            time.sleep(5)
        except Exception:
            pass

        # Wait for React to render fund data
        time.sleep(3)

        # Scroll down in increments to load all lazy-rendered fund rows
        for _ in range(25):
            page.evaluate("window.scrollBy(0, 2000)")
            time.sleep(0.2)

        return page.inner_text("body")

    def _parse_funds(self, body: str) -> List[Dict[str, Any]]:
        """Parse fund data from the page body text.

        Structure:
            GSF Lux SICAV
            <Fund Name>
            <category letter>
            <category name>
            [optional: SFDR labels like "8", "Info", "EU SFDR"]
            Share class\tISIN\tChange %\tNAV (<date>)\t12 month low\t12 month high
            <ShareClassName>\t<ISIN>
            Up/Down
            <change%>\t<NAV>\t<12m_low>\t<12m_high>
            View fund
            ...
        """
        # Split by GSF Lux SICAV sections (each section is a fund)
        # The body has repeated "GSF Lux SICAV" headers
        sections = re.split(r'\nGSF Lux SICAV\s*\n', body)

        if len(sections) < 2:
            logger.warning("Ninety One: no GSF Lux SICAV sections found")
            return []

        funds = []

        for section in sections[1:]:  # Skip preamble before first GSF Lux SICAV
            fund_data = self._parse_fund_section(section)
            if fund_data:
                funds.append(fund_data)

        return funds

    def _parse_fund_section(self, section: str) -> Optional[Dict[str, Any]]:
        """Parse a single fund section from the body text."""
        lines = [l.strip() for l in section.split("\n") if l.strip()]
        if len(lines) < 6:
            return None

        # First line is fund name
        fund_name = lines[0]
        idx = 1

        # Next lines: category letter, category name, optional SFDR labels
        fund_type = ""
        while idx < len(lines):
            line = lines[idx]
            if "Share class" in line and "ISIN" in line:
                break
            if line in ("B", "E", "M", "MM", "Bond", "Equity", "Multi-Asset",
                        "Money Market"):
                if line in CATEGORY_MAP:
                    fund_type = CATEGORY_MAP[line]
                elif line in CATEGORY_MAP.values():
                    fund_type = line
            idx += 1

        # Find the header row
        header_idx = None
        for i, line in enumerate(lines):
            if line.startswith("Share class") and "ISIN" in line:
                header_idx = i
                break

        if header_idx is None:
            return None

        # Parse NAV date from header: "NAV (18/06/2026)" or "NAV (DD/MM/YYYY)"
        nav_date_match = re.search(
            r'NAV\s*\((\d{1,2}/\d{1,2}/\d{4})\)', lines[header_idx]
        )
        nav_date_str = nav_date_match.group(1) if nav_date_match else ""

        # Parse share class rows
        share_classes = []
        nav_entries = []
        seen_isins = set()
        isins = []

        # Each share class block:
        # ShareClassName\tISIN
        # Up/Down/Dash
        # change%\tNAV\t12m_low\t12m_high
        # View fund (×2)
        # Download literature (×2)
        i = header_idx + 1
        while i < len(lines):
            line = lines[i]

            # Match share class row: "<Name>\t<ISIN>"
            sc_match = re.match(
                r'^([A-Z][^\t]+)\t(LU\d{10})', line
            )
            if sc_match:
                sc_name = sc_match.group(1).strip()
                isin = sc_match.group(2)

                if isin not in seen_isins:
                    seen_isins.add(isin)
                    isins.append(isin)

                # Determine currency
                ccy = self._extract_currency(sc_name)

                # Next lines should have: direction, change%/NAV/low/high
                nav_val = None
                change_pct = None
                day_low = None
                day_high = None

                if i + 2 < len(lines):
                    data_line = lines[i + 2]
                    # Format: "0.07\t97.1300\t95.6900\t100.2800"
                    data_parts = data_line.split("\t")
                    if len(data_parts) >= 2:
                        try:
                            change_pct = float(data_parts[0].replace(",", ""))
                        except ValueError:
                            change_pct = None
                        try:
                            nav_val = float(data_parts[1].replace(",", ""))
                        except ValueError:
                            nav_val = None
                        if len(data_parts) >= 4:
                            try:
                                day_low = float(data_parts[2].replace(",", ""))
                            except ValueError:
                                day_low = None
                            try:
                                day_high = float(data_parts[3].replace(",", ""))
                            except ValueError:
                                day_high = None

                # Parse NAV date
                parsed_nav_date = datetime.now().strftime("%Y-%m-%d")
                if nav_date_str:
                    parsed = self._parse_date(nav_date_str)
                    if parsed:
                        parsed_nav_date = parsed

                share_classes.append({
                    "share_class_name": sc_name,
                    "isin": isin,
                    "currency": ccy,
                    "change_pct": change_pct,
                })

                if nav_val is not None:
                    # Avoid duplicate NAV entries for the same ISIN
                    existing = [n for n in nav_entries if n.get("isin") == isin]
                    if not existing:
                        nav_entries.append({
                            "isin": isin,
                            "nav": nav_val,
                            "nav_date": parsed_nav_date,
                            "nav_currency": ccy,
                            "change_pct": change_pct,
                            "source": "ninetyone_website",
                        })

            i += 1

        if not fund_name:
            return None

        result = {
            "fund_name": fund_name,
            "product_url": FUND_LISTING_URL,
            "source_type": "manager_website",
            "isins": isins,
            "share_classes": share_classes,
            "nav_entries": nav_entries,
            "base_currency": "USD",
            "fund_type": fund_type,
            "fund_manager_name_en": "Ninety One Luxembourg S.A.",
            "domicile": "Luxembourg",
        }

        logger.info(
            f"Ninety One: {fund_name[:50]} — "
            f"{len(isins)} ISINs, {len(nav_entries)} NAVs"
        )

        return result

    def _extract_currency(self, share_class_name: str) -> str:
        """Extract currency from share class name."""
        name_upper = share_class_name.upper()
        # Look for currency code in the name
        for ccy in ["USD", "HKD", "EUR", "GBP", "CHF", "JPY", "SGD", "AUD",
                     "CNH", "CNY", "RMB", "CAD", "NZD", "SEK", "NOK", "DKK",
                     "ZAR", "PLN", "CZK"]:
            # Match as whole word or before closing paren
            if re.search(r'\b' + ccy + r'\b', name_upper):
                if ccy in ("CNY", "RMB"):
                    return "CNH"
                return ccy
        return "USD"

    def get_fund_list(self) -> List[Dict[str, Any]]:
        """Load and parse all Ninety One fund data from the website."""
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
        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.chrome = {runtime: {}};
        """)

        try:
            body = self._load_fund_page(page)
            funds = self._parse_funds(body)
            logger.info(f"Ninety One: parsed {len(funds)} funds total")
            return funds
        finally:
            context.close()

    # ── Name Matching ──────────────────────────────────────────

    def _match_fund_name(self, conn, extracted_name: str) -> Optional[int]:
        """Match Ninety One fund name to hk_funds.id."""
        if not extracted_name:
            return None

        name = extracted_name.strip()
        candidates = [name]

        # Try without "Ninety One " prefix
        for prefix in ["Ninety One "]:
            if name.lower().startswith(prefix.lower()):
                candidates.append(name[len(prefix):])

        for candidate in candidates:
            c = re.sub(r"\s+", " ", candidate.lower().strip())

            for query, params in [
                ("LOWER(fund_name_en) = ?", [c]),
                ("LOWER(fund_name_en) LIKE ?", [f"%{c}%"]),
            ]:
                row = conn.execute(
                    f"""SELECT id, fund_name_en FROM hk_funds
                       WHERE {query} AND is_active = true
                       {self._NINETYONE_MANAGER_SQL}
                       LIMIT 1""",
                    params,
                ).fetchone()
                if row:
                    return row[0]

            # Keyword matching
            keywords = [
                w for w in c.split()
                if len(w) > 2
                and w not in (
                    "fund", "class", "etf", "ninety", "one",
                    "equity", "bond", "global",
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
                       {self._NINETYONE_MANAGER_SQL}
                       LIMIT 1""",
                    params,
                ).fetchone()
                if row:
                    return row[0]

        return None

    # ── Main Interface ─────────────────────────────────────────

    def get_fund_details(self, isin: str) -> Optional[Dict[str, Any]]:
        """Not implemented — fund detail pages require separate navigation."""
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

                if not hk_fund_id:
                    for isin in isins:
                        row = conn.execute(
                            "SELECT fund_id FROM hk_fund_share_classes "
                            "WHERE isin = ?",
                            [isin],
                        ).fetchone()
                        if row:
                            hk_fund_id = row[0]
                            break

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
                    "fund_manager_name_en": detail.get(
                        "fund_manager_name_en", ""
                    ),
                }
                for key in ("fund_type", "domicile", "base_currency", "currency"):
                    if detail.get(key):
                        data[key] = detail[key]

                if update_fund_from_manager(conn, hk_fund_id, data):
                    stats["details_updated"] += 1

                # Store ISINs and share classes
                for sc in share_classes:
                    sc_isin = sc.get("isin", "")
                    sc_data = {
                        "share_class_name": sc.get("share_class_name", ""),
                        "isin": sc_isin,
                        "currency": sc.get("currency", base_ccy),
                        "source": "ninetyone_website",
                    }
                    upsert_share_classes(conn, hk_fund_id, [sc_data])
                    stats["share_classes_stored"] += 1

                    if sc_isin:
                        update_fund_from_manager(
                            conn, hk_fund_id, {"isin": sc_isin}
                        )
                        stats["isins_updated"] += 1

                # Store NAVs
                if nav_entries:
                    n = upsert_nav_history(conn, hk_fund_id, nav_entries)
                    stats["navs_stored"] += n

            logger.info(
                f"Ninety One scrape complete: "
                f"Matched={stats['matched']}/{stats['funds_found']}, "
                f"ISINs={stats['isins_updated']}, "
                f"NAVs={stats['navs_stored']}, "
                f"ShareClasses={stats['share_classes_stored']}, "
                f"Details={stats['details_updated']}"
            )

        except Exception as e:
            logger.error(f"Ninety One scrape failed: {e}")
            stats["errors"] += 1
        finally:
            self._close_browser()

        return stats
