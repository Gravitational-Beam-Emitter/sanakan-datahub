"""
HuaAn Asset Management (Hong Kong) connector.

Extracts fund data from huaan.com.hk via Playwright DOM scraping.
The site is a static HTML website with server-side rendered fund data.

Strategy:
  1. Visit the public funds listing page (/en/fund/public.html)
  2. Discover fund detail page URLs
  3. Dismiss the disclaimer modal via JS
  4. Visit each fund detail page
  5. Extract all share class ISINs, NAVs, and fund info from the HTML
  6. Map ISINs to share class names by position (USD/HKD/RMB x I/A/O x Acc/Dis)
  7. Match to hk_funds by ISIN first, then by name with HuaAn constraint

CE: WEBB-131720 — HuaAn Asset Management (Hong Kong) Limited
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from hk_funds.manager_connectors.base import BaseManagerConnector, register_connector

logger = logging.getLogger("hk_funds.manager_connectors.huaan")

BASE_URL = "https://www.huaan.com.hk"
FUNDS_LISTING_URL = f"{BASE_URL}/en/fund/public.html"

# ISIN-to-share-class mapping: ISINs appear in order by (currency, class_letter, acc_dis)
# Order: USD Class I Acc, HKD Class I Acc, RMB Class I Acc,
#        USD Class I Dis, HKD Class I Dis, RMB Class I Dis,
#        USD Class A Acc, ...
# Actually, from the HTML structure, ISINs are grouped by currency:
#   USD: I Acc, I Dis, A Acc, A Dis, O Acc, O Dis
#   HKD: I Acc, I Dis, A Acc, A Dis, O Acc, O Dis
#   RMB: I Acc, I Dis, A Acc, A Dis, O Acc, O Dis
SHARE_CLASS_TEMPLATES = [
    ("Class I {ccy} (Acc)", "Class I {ccy} (Dis)"),
    ("Class A {ccy} (Acc)", "Class A {ccy} (Dis)"),
    ("Class O {ccy} (Acc)", "Class O {ccy} (Dis)"),
]


@register_connector
class HuaAnConnector(BaseManagerConnector):
    """Extracts fund data from HuaAn Asset Management HK website."""

    manager_ce_numbers = ["WEBB-131720"]
    base_url = BASE_URL

    request_delay: float = 1.0
    request_timeout: int = 30

    _HUAAN_MANAGER_SQL = (
        "AND (LOWER(fund_manager_name_en) LIKE '%huaan%'"
        " OR LOWER(fund_manager_name_en) LIKE '%hua an%')"
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

    def _dismiss_disclaimer(self, page) -> bool:
        """Remove the disclaimer modal via JS."""
        try:
            page.evaluate("""() => {
                document.querySelectorAll(
                    '.mask, .modal, .popup, .disclaimer, .overlay, ' +
                    '[class*="pop"], [class*="modal"], [id*="pop"], [id*="modal"]'
                ).forEach(el => el.remove());
                document.body.style.overflow = '';
            }""")
            time.sleep(1)
            return True
        except Exception:
            return False

    # ── Fund Discovery ─────────────────────────────────────────

    def _discover_fund_urls(self, page) -> List[Dict[str, str]]:
        """Discover fund detail page URLs from the public funds listing page."""
        page.goto(FUNDS_LISTING_URL, wait_until="load", timeout=30000)
        time.sleep(3)
        self._dismiss_disclaimer(page)

        return page.evaluate("""() => {
            const links = Array.from(document.querySelectorAll('a'));
            const seen = new Set();
            const result = [];
            for (const link of links) {
                const href = link.href;
                if (href.includes('/fund/public/') && /\\d+\\.html/.test(href) && !seen.has(href)) {
                    seen.add(href);
                    result.push({url: href});
                }
            }
            return result;
        }""")

    # ── Fund Detail Parsing ────────────────────────────────────

    def _parse_fund_detail_page(self, page, url: str) -> Optional[Dict[str, Any]]:
        """Parse a fund detail page for all fund and share class data."""
        try:
            page.goto(url, wait_until="load", timeout=30000)
            time.sleep(3)
            self._dismiss_disclaimer(page)
            page.goto(url, wait_until="load", timeout=30000)
            time.sleep(3)
        except Exception:
            logger.warning(f"HuaAn: failed to load {url}")
            return None

        body = page.inner_text("body")
        html = page.evaluate("document.documentElement.outerHTML")

        # Extract fund name
        fund_name = page.evaluate("""() => {
            const h1 = document.querySelector('h1');
            if (h1) return h1.textContent.trim();
            const title = document.title;
            if (title && title.includes(' - ')) return title.split(' - ')[0].trim();
            return '';
        }""")
        if not fund_name:
            fn_match = re.search(
                r'(Hua[aA]n\s+[A-Za-z\s]+?(?:Fund|Bond))', body
            )
            if fn_match:
                fund_name = fn_match.group(1)

        # Get share class names from the tab buttons
        sc_names = page.evaluate("""() => {
            const names = [];
            const seen = new Set();
            document.querySelectorAll('.tab-term').forEach(el => {
                const text = (el.textContent || '').trim();
                if (/Class\\s+[IOA]\\s+(?:USD|HKD|RMB)\\s*\\((?:Acc|Dis)\\)/.test(text) && !seen.has(text)) {
                    seen.add(text);
                    names.push(text);
                }
            });
            return names;
        }""")
        logger.info(f"HuaAn: {len(sc_names)} share class names for {fund_name}")

        # Extract all HK ISINs from HTML
        all_isins = list(dict.fromkeys(re.findall(r'HK\d{10}', html)))
        logger.info(f"HuaAn: {len(all_isins)} HK ISINs found in HTML")

        # The first ISIN is usually the umbrella - identify it
        # Umbrella ISIN appears in a different context than share class ISINs
        umbrella_isin = ""
        sc_isins = list(all_isins)
        if len(all_isins) > 18:
            # More ISINs than expected - first one is umbrella
            umbrella_isin = all_isins[0]
            sc_isins = all_isins[1:]

        # Map ISINs to share class names
        if len(sc_isins) == len(sc_names):
            share_classes = []
            for isin, name in zip(sc_isins, sc_names):
                ccy = "USD"
                if "HKD" in name:
                    ccy = "HKD"
                elif "RMB" in name:
                    ccy = "CNH"
                share_classes.append({
                    "share_class_name": name,
                    "isin": isin,
                    "currency": ccy,
                })
        else:
            # Best effort: use all ISINs with generic names
            share_classes = []
            for isin in sc_isins:
                share_classes.append({
                    "share_class_name": "",
                    "isin": isin,
                    "currency": "USD",
                })

        # Parse NAV from the overview section (value-before-label format)
        #   Line format: "1168.3111\nNet Asset Value"
        nav_entries = []
        overview_nav_match = re.search(
            r'([\d,]+\.\d+)\s*\n\s*Net Asset Value\b', body
        )
        nav_date = datetime.now().strftime("%Y-%m-%d")
        nav_date_match = re.search(
            r'(\d{1,2}\s+[A-Z][a-z]{2},?\s*\d{4})\s*\n\s*Net Asset Value Date',
            body
        )
        if nav_date_match:
            parsed = self._parse_date(nav_date_match.group(1))
            if parsed:
                nav_date = parsed

        if overview_nav_match:
            try:
                nav_val = float(overview_nav_match.group(1).replace(',', ''))
                # Determine currency from first share class
                ccy = share_classes[0]["currency"] if share_classes else "USD"
                nav_entries.append({
                    "nav": nav_val,
                    "nav_date": nav_date,
                    "nav_currency": ccy,
                    "source": "huaan_website",
                })
            except (ValueError, TypeError):
                pass

        # Also parse NAVs from detail sections (label-before-value format)
        # "Net asset value per unit\nup to DD Mon, YYYY\n1168.3111"
        detail_nav_blocks = re.findall(
            r'Net asset value per unit\s*\n(?:[^\n]*\n)?\s*([\d,]+\.\d+)',
            body
        )
        for nav_str in detail_nav_blocks:
            try:
                nav_val = float(nav_str.replace(',', ''))
                if nav_val == nav_entries[0]["nav"] if nav_entries else False:
                    continue  # Skip duplicate
                nav_entries.append({
                    "nav": nav_val,
                    "nav_date": nav_date,
                    "nav_currency": "USD",
                    "source": "huaan_website",
                })
            except (ValueError, TypeError):
                pass

        # Build result
        result = {
            "fund_name": fund_name,
            "product_url": url,
            "source_type": "manager_website",
            "isins": [sc["isin"] for sc in share_classes],
            "share_classes": share_classes,
            "nav_entries": nav_entries,
            "base_currency": "USD",
        }

        # Fund manager - label before value format
        mgr_match = re.search(
            r'Manager\s*\n\s*(Hua[aA]n\s+Asset\s+Management[^\n]*)',
            body
        )
        if mgr_match:
            result["fund_manager_name_en"] = mgr_match.group(1).strip()

        # Inception date - handles both formats:
        #   Overview: "28 Nov, 2022\nInception Date"
        #   Detail:   "Inception Date\n28 Nov, 2022"
        inception_match = re.search(
            r'(\d{1,2}\s+[A-Z][a-z]{2},?\s*\d{4})\s*\n\s*Inception Date',
            body
        )
        if not inception_match:
            inception_match = re.search(
                r'Inception Date\s*\n\s*(\d{1,2}\s+[A-Z][a-z]{2},?\s*\d{4})',
                body
            )
        if inception_match:
            # Strip comma for _parse_date compatibility
            date_str = inception_match.group(1).replace(',', '')
            parsed = self._parse_date(date_str)
            if parsed:
                result["fund_inception_date"] = parsed

        # Domicile - value before label: "Hong Kong\nDomicile"
        dom_match = re.search(
            r'(Hong Kong)\s*\n\s*Domicile', body
        )
        if dom_match:
            result["domicile"] = "Hong Kong"

        # Management fee - value before label: "0.40%\nManagement Fee"
        fee_match = re.search(
            r'([\d.]+%)\s*\n\s*Management Fee\b', body
        )
        if fee_match:
            result["management_fee_pct"] = self._parse_pct(fee_match.group(1))

        # Trustee - label before value
        trustee_match = re.search(
            r'Trustee\s*\n\s*(Bank\s+of\s+Communications[^\n]*)',
            body
        )
        if trustee_match:
            result["trustee_custodian"] = trustee_match.group(1).strip()

        return result

    def get_fund_list(self) -> List[Dict[str, Any]]:
        """Discover and parse all HuaAn fund pages."""
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
            fund_urls = self._discover_fund_urls(page)
            logger.info(f"HuaAn: discovered {len(fund_urls)} fund URLs")

            funds = []
            for fu in fund_urls:
                url = fu["url"]
                detail = self._parse_fund_detail_page(page, url)
                if detail:
                    logger.info(
                        f"HuaAn: {detail['fund_name'][:60]} — "
                        f"{len(detail.get('isins', []))} ISINs, "
                        f"{len(detail.get('nav_entries', []))} NAVs"
                    )
                    funds.append(detail)

            return funds

        finally:
            context.close()

    # ── Name Matching ──────────────────────────────────────────

    def _match_fund_name(self, conn, extracted_name: str) -> Optional[int]:
        """Match HuaAn fund name to hk_funds.id."""
        if not extracted_name:
            return None

        name = extracted_name.strip()
        candidates = [name]

        for prefix in ["HuaAn ", "Huaan ", "Hua An "]:
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
                       {self._HUAAN_MANAGER_SQL}
                       LIMIT 1""",
                    params,
                ).fetchone()
                if row:
                    return row[0]

            keywords = [
                w for w in c.split()
                if len(w) > 2
                and w not in (
                    "fund", "class", "etf", "huaan", "hua", "bond",
                    "investment", "grade",
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
                       {self._HUAAN_MANAGER_SQL}
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

                # Match by ISIN
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
                            "SELECT fund_id FROM hk_fund_share_classes WHERE isin = ?",
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
                }
                for key in ("currency", "base_currency", "fund_inception_date",
                           "management_fee_pct", "fund_manager_name_en",
                           "domicile", "trustee_custodian"):
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
                        "currency": self._parse_currency(
                            sc.get("currency", base_ccy)
                        ),
                        "source": "huaan_website",
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
                f"HuaAn scrape complete: "
                f"Matched={stats['matched']}/{stats['funds_found']}, "
                f"ISINs={stats['isins_updated']}, "
                f"NAVs={stats['navs_stored']}, "
                f"ShareClasses={stats['share_classes_stored']}, "
                f"Details={stats['details_updated']}"
            )

        except Exception as e:
            logger.error(f"HuaAn scrape failed: {e}")
            stats["errors"] += 1
        finally:
            self._close_browser()

        return stats
