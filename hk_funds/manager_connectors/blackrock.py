"""
BlackRock Playwright connector — scrapes fund data from blackrock.com/hk.

BlackRock is the world's largest asset manager with ~89 SFC-authorized funds.
Their product sitemap contains 2,992 share class pages covering ~251 distinct
funds. Each page embeds ISINs, NAVs, fees, benchmarks, Morningstar ratings,
portfolio managers, and top holdings in the HTML.

Strategy:
  1. Fetch sitemap XML (requests) → 2,992 product URLs
  2. Deduplicate to ~251 distinct fund families
  3. Visit one page per fund with Playwright
  4. Extract all share classes (ISIN table), key facts, holdings, managers
  5. Match to hk_funds by fund name and store

CE: AFF275 — BlackRock Asset Management North Asia Limited
"""

from __future__ import annotations

import logging
import re
import time
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree as ET

import requests

from hk_funds.manager_connectors.base import BaseManagerConnector, register_connector

logger = logging.getLogger("hk_funds.manager_connectors.blackrock")

BLACKROCK_SITEMAP_URL = "https://www.blackrock.com/hk/en/product-sitemap.xml"
FUND_NAME_CLEANUP = re.compile(
    r"-(a\d+|d\d+|i\d+|x\d+|e\d+|c\d+|class-\w+)(-.*)?$",
    re.IGNORECASE,
)


def _slug_to_display_name(slug: str) -> str:
    """Convert URL slug to display name: 'blackrock-global-allocation-fund' → 'BlackRock Global Allocation Fund'."""
    # Known brand capitalizations
    overrides = {
        "blackrock": "BlackRock",
        "ishares": "iShares",
        "etf": "ETF",
        "esg": "ESG",
        "usd": "USD",
        "eur": "EUR",
        "gbp": "GBP",
        "hkd": "HKD",
        "cny": "CNY",
        "cnh": "CNH",
        "jpy": "JPY",
        "aud": "AUD",
        "sgd": "SGD",
        "chf": "CHF",
        "zar": "ZAR",
        "uk": "UK",
        "us": "US",
        "eu": "EU",
        "em": "EM",
        "ai": "AI",
        "cio": "CIO",
        "reit": "REIT",
        "asia": "Asia",
        "china": "China",
        "japan": "Japan",
        "european": "European",
        "global": "Global",
        "america": "America",
        "emerging": "Emerging",
        "markets": "Markets",
        "technology": "Technology",
        "income": "Income",
        "growth": "Growth",
        "allocation": "Allocation",
        "equity": "Equity",
        "bond": "Bond",
        "fund": "Fund",
        "high": "High",
        "yield": "Yield",
        "credit": "Credit",
        "fixed": "Fixed",
        "opportunities": "Opportunities",
    }

    words = slug.split("-")
    result = []
    for w in words:
        if w.lower() in overrides:
            result.append(overrides[w.lower()])
        else:
            result.append(w.capitalize())
    return " ".join(result)


@register_connector
class BlackRockConnector(BaseManagerConnector):
    """Scrapes BlackRock product pages for fund ISINs, NAVs, fees, holdings.

    Uses Playwright because the pages are JS-rendered SPAs.
    """

    manager_ce_numbers = ["AFF275"]
    base_url = "https://www.blackrock.com/hk"

    # Rate limiting — be gentle to avoid WAF blocks
    request_delay: float = 2.0
    request_timeout: int = 60

    def __init__(self):
        super().__init__()
        self._playwright = None
        self._browser = None

    # ── Sitemap ────────────────────────────────────────────────

    def _fetch_sitemap_urls(self) -> List[str]:
        """Fetch and parse the product sitemap. Returns list of product page URLs."""
        resp = requests.get(
            BLACKROCK_SITEMAP_URL,
            timeout=30,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
            },
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        urls = [el.text for el in root.findall(".//sm:url/sm:loc", ns)]
        logger.info(f"Sitemap: {len(urls)} product URLs")
        return urls

    def _deduplicate_urls(self, urls: List[str]) -> Dict[str, str]:
        """Deduplicate product URLs to one per distinct fund family.

        Returns {base_fund_name: url_to_scrape}.
        """
        families: Dict[str, str] = {}
        for url in urls:
            m = re.search(r"/products/\d+/(.+)$", url)
            if not m:
                continue
            slug = m.group(1)
            # Strip share class suffix to get base fund name slug
            base_slug = FUND_NAME_CLEANUP.sub("", slug)
            current = families.get(base_slug)
            if current is None:
                families[base_slug] = url
                continue
            # Prefer /en/ over /zh/ URLs
            if "/en/" in url and "/zh/" in current:
                families[base_slug] = url
            # Prefer non-hedged, non-distributing share class
            elif "-hedged" not in slug and "-dis" not in slug and "-hedged" in current.split("/")[-1]:
                families[base_slug] = url

        logger.info(f"Deduplicated: {len(families)} distinct fund families from {len(urls)} URLs")
        return families

    # ── Playwright helpers ─────────────────────────────────────

    def _get_browser(self):
        """Lazy-init Playwright browser."""
        if self._browser is None:
            from playwright.sync_api import sync_playwright

            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=True)
        return self._browser

    def _close_browser(self):
        """Clean up Playwright resources."""
        if self._browser:
            self._browser.close()
            self._browser = None
        if self._playwright:
            self._playwright.stop()
            self._playwright = None

    def _new_page(self):
        """Create a new page with realistic headers."""
        browser = self._get_browser()
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            locale="en-HK",
        )
        return context.new_page()

    # ── Page scraping ──────────────────────────────────────────

    def _scrape_product_page(self, page, url: str) -> Optional[Dict[str, Any]]:
        """Scrape a single product page for all fund data.

        Returns dict with keys: fund_name, share_classes, holdings,
        key_facts, portfolio_managers, or None on failure.
        """
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=self.request_timeout * 1000)
            time.sleep(3)  # Wait for JS rendering
        except Exception as e:
            logger.warning(f"Failed to load {url}: {e}")
            return None

        try:
            text = page.evaluate("() => document.body.innerText")
        except Exception:
            return None

        if not text or len(text) < 500:
            return None

        result: Dict[str, Any] = {
            "product_url": url,
            "source_type": "manager_website",
        }

        # ── Fund Name ──
        # The fund name is in <h1 class="product-title"> → <span class="product-title-main">
        fund_name = page.evaluate(
            """() => {
                const el = document.querySelector('.product-title-main');
                if (el) return el.textContent.trim();
                const h1 = document.querySelector('h1');
                if (h1) return h1.textContent.trim();
                return null;
            }"""
        )
        if fund_name:
            result["fund_name"] = fund_name

        # ── Key Facts section ──
        # There are two "Key Facts" on the page: one in the nav tabs, one
        # containing the actual data. The data section starts near the end
        # of the page and has keys like "Net Assets of Fund".
        fact_map = {}
        lines = text.split("\n")
        # Find the LAST "Key Facts" occurrence (the data section)
        kf_indices = [i for i, l in enumerate(lines) if l.strip() == "Key Facts"]
        for start_idx in reversed(kf_indices):
            j = start_idx + 1
            section_keys = []
            while j < len(lines):
                key = lines[j].strip()
                if not key:
                    j += 1
                    continue
                if key in ("Portfolio Characteristics", "Documents", "Managers",
                           "Holdings", "Overview", "Performance", "Ratings",
                           "Exposure Breakdowns", "Pricing & Exchange",
                           "Portfolio Managers", "ESG Integration"):
                    break
                # Value may be on the next line(s)
                # Pattern: Key \n [as of DATE \n] Value
                val_idx = j + 1
                if val_idx < len(lines) and lines[val_idx].strip().startswith("as of "):
                    val_idx += 1  # skip date subline
                val = lines[val_idx].strip() if val_idx < len(lines) else ""
                fact_map[key] = val
                section_keys.append(key)
                j = val_idx + 1
            # If we found actual data keys, this is the right section
            if any(k in fact_map for k in ("Net Assets of Fund", "Fund Inception",
                                            "ISIN", "Management Fee", "Domicile")):
                break

        result["key_facts"] = fact_map

        # Map key facts to fund fields
        if "ISIN" in fact_map:
            result["isin"] = fact_map["ISIN"]
        if "Management Fee" in fact_map:
            try:
                result["management_fee_pct"] = float(fact_map["Management Fee"].replace("%", ""))
            except ValueError:
                pass
        if "Initial Charge" in fact_map:
            try:
                result["front_load_pct"] = float(fact_map["Initial Charge"].replace("%", ""))
            except ValueError:
                pass
        if "Performance Fee" in fact_map:
            try:
                result["performance_fee_pct"] = float(fact_map["Performance Fee"].replace("%", ""))
            except ValueError:
                pass
        if "Fund Inception" in fact_map:
            result["fund_inception_date"] = self._parse_date(fact_map["Fund Inception"])
        if "Fund Base Currency" in fact_map:
            result["currency"] = fact_map["Fund Base Currency"]
        if "Share Class Currency" in fact_map:
            result["share_class_currency"] = fact_map["Share Class Currency"]
        if "Domicile" in fact_map:
            result["domicile"] = fact_map["Domicile"]
        if "Bloomberg Ticker" in fact_map:
            result["bloomberg_ticker"] = fact_map["Bloomberg Ticker"]
        if "Morningstar Category" in fact_map:
            result["morningstar_category"] = fact_map["Morningstar Category"]
        if "Management Company" in fact_map:
            result["management_company"] = fact_map["Management Company"]
        if "Use of Income" in fact_map:
            income = fact_map["Use of Income"].lower()
            result["is_distributing"] = "distributing" in income or "income" in income
            result["distribution_type"] = fact_map["Use of Income"]
        if "Benchmark 1" in fact_map:
            result["benchmark_name"] = fact_map["Benchmark 1"]
        if "Net Assets of Fund" in fact_map:
            # Net assets value is typically on its own line after the label
            pass  # handled below

        # Extract net assets (may span multiple lines)
        net_match = re.search(
            r"Net Assets of Fund\s*(?:as of[^\n]*)?\s*(\w+)\s*([\d,.]+)",
            text,
        )
        if net_match:
            currency = net_match.group(1)
            try:
                result["fund_size_hkd"] = float(net_match.group(2).replace(",", ""))
                result["fund_size_currency"] = currency
            except ValueError:
                pass

        # Extract Morningstar rating
        mr_match = re.search(r"Morningstar Rating\s+([★☆]+)", text)
        if mr_match:
            stars = mr_match.group(1)
            result["morningstar_rating"] = len(stars)

        # ── NAV ──
        nav_match = re.search(
            r"NAV as of (\d{1,2}-[A-Za-z]{3}-\d{4})\s+(\w+)\s+([\d,.]+)",
            text,
        )
        if nav_match:
            result["nav_date"] = self._parse_date(nav_match.group(1))
            result["nav_currency"] = nav_match.group(2)
            try:
                result["nav"] = float(nav_match.group(3).replace(",", ""))
            except ValueError:
                pass

        # 52-week range
        wk_match = re.search(r"52 WK:\s*([\d,.]+)\s*-\s*([\d,.]+)", text)
        if wk_match:
            try:
                result["low_52w"] = float(wk_match.group(1).replace(",", ""))
                result["high_52w"] = float(wk_match.group(2).replace(",", ""))
            except ValueError:
                pass

        # ── Share Classes Table ──
        share_classes = page.evaluate(
            """() => {
            const tables = document.querySelectorAll('table');
            for (const t of tables) {
                const headers = Array.from(t.querySelectorAll('th')).map(h => h.textContent?.trim());
                if (headers.includes('Share Class') && headers.includes('ISIN')) {
                    const rows = Array.from(t.rows).slice(1);
                    return rows.map(row => {
                        const cells = Array.from(row.cells);
                        return {
                            share_class: cells[0]?.textContent?.trim() || '',
                            currency: cells[1]?.textContent?.trim() || '',
                            nav: cells[2]?.textContent?.trim() || '',
                            nav_date: cells[5]?.textContent?.trim() || '',
                            isin: cells[8]?.textContent?.trim() || '',
                            high_52w: cells[6]?.textContent?.trim() || '',
                            low_52w: cells[7]?.textContent?.trim() || '',
                        };
                    });
                }
            }
            return [];
        }"""
        )

        # Clean share class data
        cleaned_sc = []
        for sc in share_classes:
            isin = sc.get("isin", "")
            if not isin or isin == "NA":
                continue
            sc_name = sc.get("share_class", "")
            currency = sc.get("currency", "HKD")
            is_hedged = "hedged" in sc_name.lower()

            entry = {
                "share_class_name": sc_name,
                "isin": isin,
                "currency": currency,
                "is_hedged": is_hedged,
                "source": "blackrock_website",
            }

            # Parse NAV
            nav_str = sc.get("nav", "").replace(",", "")
            if nav_str and nav_str != "NA":
                try:
                    entry["nav"] = float(nav_str)
                except ValueError:
                    pass

            # Parse NAV date
            nav_date = sc.get("nav_date", "")
            if nav_date and nav_date != "NA":
                entry["nav_date"] = self._parse_date(nav_date)

            cleaned_sc.append(entry)

        result["share_classes"] = cleaned_sc
        result["isin_list"] = [sc["isin"] for sc in cleaned_sc]

        # ── Top Holdings ──
        holdings = page.evaluate(
            """() => {
            const tables = document.querySelectorAll('table');
            const result = [];
            let rank = 0;
            for (const t of tables) {
                const headers = Array.from(t.querySelectorAll('th')).map(h => h.textContent?.trim());
                if (headers.length === 2 && headers[0] === 'Name' && headers[1] === 'Weight (%)') {
                    const rows = Array.from(t.rows).slice(1);
                    for (const row of rows) {
                        const cells = row.cells;
                        if (cells.length >= 2) {
                            rank++;
                            result.push({
                                rank: rank,
                                holding_name: cells[0]?.textContent?.trim() || '',
                                weight_pct: cells[1]?.textContent?.trim() || '',
                            });
                        }
                    }
                }
            }
            return result;
        }"""
        )

        cleaned_holdings = []
        for h in holdings:
            name = h.get("holding_name", "")
            if not name:
                continue
            try:
                weight = float(h.get("weight_pct", "0"))
            except ValueError:
                weight = None

            cleaned_holdings.append({
                "rank": h["rank"],
                "holding_name": name,
                "weight_pct": weight,
                "source": "blackrock_website",
            })

        result["holdings"] = cleaned_holdings

        # ── Portfolio Managers ──
        pm_section = re.search(
            r"Portfolio Managers\s*\n+(.+?)(?:\n\s*\n|\n(Documents|Key Facts|Prospectus))",
            text,
            re.DOTALL,
        )
        if pm_section:
            pm_text = pm_section.group(1)
            # Extract name lines (skip titles like "Managing Director")
            managers = []
            for line in pm_text.split("\n"):
                line = line.strip()
                if not line or len(line) < 3:
                    continue
                # Skip lines that are clearly titles/descriptions
                if re.match(
                    r"^(Managing Director|Director|Vice President|Senior|CIO|Head of|Read More|ESG|BlackRock)",
                    line,
                ):
                    continue
                # A name is typically 2 words, capital letters
                if re.match(r"^[A-Z][a-z]+ [A-Z][a-z]+", line):
                    managers.append(line)
            if managers:
                result["portfolio_managers"] = managers
                result["portfolio_manager_name"] = ", ".join(managers[:3])

        return result

    # ── Name Matching ──────────────────────────────────────────

    # BlackRock manager names in the SFC register
    _BLK_MANAGER_SQL = (
        "AND (LOWER(fund_manager_name_en) LIKE '%blackrock%'"
        " OR LOWER(fund_manager_name_en) LIKE '%ishares%')"
    )

    def _match_fund_name(self, conn, extracted_name: str) -> Optional[int]:
        """Match extracted BlackRock fund name to hk_funds.id.

        BlackRock website uses "BlackRock Global Allocation Fund" but the
        SFC register often uses "Global Allocation Fund" (without the
        BlackRock prefix). We try multiple normalizations and require
        the fund's manager to be a BlackRock entity.
        """
        if not extracted_name:
            return None

        name = extracted_name.strip()

        # Variations to try, in order of specificity
        candidates = [name]

        # Strip brand prefix: "BlackRock Global Allocation Fund" → "Global Allocation Fund"
        for prefix in ("BlackRock ", "iShares "):
            if name.startswith(prefix):
                candidates.append(name[len(prefix):])
                break

        for candidate in list(candidates):
            # Normalize: lowercase, collapse whitespace
            c = re.sub(r"\s+", " ", candidate.lower().strip())
            c = re.sub(r"\s+(fund|class\s+\w+|etf)\s*$", "", c, flags=re.IGNORECASE)

            # Try exact match (manager must be BlackRock)
            row = conn.execute(
                f"""SELECT id, fund_name_en FROM hk_funds
                   WHERE LOWER(fund_name_en) = ? AND is_active = true
                   {self._BLK_MANAGER_SQL}
                   LIMIT 1""",
                [c],
            ).fetchone()
            if row:
                return row[0]

            # Try LIKE match
            row = conn.execute(
                f"""SELECT id, fund_name_en FROM hk_funds
                   WHERE LOWER(fund_name_en) LIKE ?
                   AND is_active = true
                   {self._BLK_MANAGER_SQL}
                   LIMIT 1""",
                [f"%{c}%"],
            ).fetchone()
            if row:
                return row[0]

            # Try word-level matching: all significant words must appear
            keywords = [
                w for w in c.split()
                if len(w) > 3 and w not in ("fund", "class", "etf")
            ]
            if len(keywords) >= 2:
                conditions = " AND ".join(
                    ["LOWER(fund_name_en) LIKE ?" for _ in keywords]
                )
                params = [f"%{kw}%" for kw in keywords]
                row = conn.execute(
                    f"""SELECT id, fund_name_en FROM hk_funds
                       WHERE {conditions} AND is_active = true
                       {self._BLK_MANAGER_SQL}
                       LIMIT 1""",
                    params,
                ).fetchone()
                if row:
                    return row[0]

        return None

    # ── Main Interface ─────────────────────────────────────────

    # ── Company Profile (Playwright-based) ─────────────────────

    def get_about_page(self) -> Optional[str]:
        """Scrape BlackRock's About and Who We Are pages using Playwright."""
        parts = []
        for path in ["/hk/en/about-blackrock", "/hk/en/who-we-are"]:
            url = self.base_url.rstrip("/") + path
            page = self._new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=self.request_timeout * 1000)
                time.sleep(2)
                text = page.evaluate("() => document.body.innerText")
                if text and len(text) > 200:
                    parts.append(text)
            except Exception as e:
                logger.debug(f"BlackRock profile page {path}: {e}")
            finally:
                page.close()
        return "\n\n---\n\n".join(parts) if parts else None

    def get_team_page(self) -> Optional[str]:
        """Scrape BlackRock's leadership page using Playwright."""
        url = self.base_url.rstrip("/") + "/hk/en/who-we-are/leadership"
        page = self._new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=self.request_timeout * 1000)
            time.sleep(2)
            text = page.evaluate("() => document.body.innerText")
            if text and len(text) > 200:
                return text
        except Exception as e:
            logger.debug(f"BlackRock leadership page: {e}")
        finally:
            page.close()
        return None

    def get_news_page(self) -> Optional[str]:
        """Scrape BlackRock's newsroom using Playwright."""
        url = self.base_url.rstrip("/") + "/hk/en/newsroom"
        page = self._new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=self.request_timeout * 1000)
            time.sleep(2)
            text = page.evaluate("() => document.body.innerText")
            if text and len(text) > 200:
                return text
        except Exception as e:
            logger.debug(f"BlackRock newsroom: {e}")
        finally:
            page.close()
        return None

    def get_fund_list(self) -> List[Dict[str, Any]]:
        """Get all BlackRock funds from the product sitemap.

        Each dict contains: fund_name, isin, share_classes, nav, etc.
        """
        urls = self._fetch_sitemap_urls()
        families = self._deduplicate_urls(urls)
        return [
            {
                "base_fund_name": _slug_to_display_name(slug),
                "product_url": url,
            }
            for slug, url in families.items()
        ]

    def get_fund_details(self, product_url: str) -> Optional[Dict[str, Any]]:
        """Scrape a single product page and return full fund details."""
        page = self._new_page()
        try:
            return self._scrape_product_page(page, product_url)
        finally:
            page.close()

    def scrape_and_store(self, conn, date_str: str = None) -> Dict[str, int]:
        """Run full BlackRock scrape: sitemap → dedup → Playwright scrape → store.

        Returns stats dict.
        """
        from hk_funds.storage import (
            update_fund_from_manager,
            upsert_holdings,
            upsert_nav_history,
            upsert_share_classes,
        )

        today = date_str or datetime.now().strftime("%Y-%m-%d")
        stats = {
            "funds_found": 0,
            "matched": 0,
            "isins_updated": 0,
            "navs_stored": 0,
            "holdings_stored": 0,
            "share_classes_stored": 0,
            "details_updated": 0,
            "errors": 0,
        }

        try:
            # Step 1: Get deduplicated fund URLs
            urls = self._fetch_sitemap_urls()
            families = self._deduplicate_urls(urls)
            stats["funds_found"] = len(families)

            logger.info(
                f"Starting Playwright scrape of {len(families)} BlackRock fund pages..."
            )

            # Step 2: Scrape each fund page
            for idx, (base_slug, url) in enumerate(families.items()):
                fund_name = _slug_to_display_name(base_slug)

                try:
                    page = self._new_page()
                    try:
                        data = self._scrape_product_page(page, url)
                    finally:
                        page.close()

                    if not data:
                        stats["errors"] += 1
                        continue

                    # Ensure fund name is set
                    if "fund_name" not in data:
                        data["fund_name"] = fund_name

                    # Step 3: Match to hk_funds
                    fund_id = self._match_fund_name(conn, data.get("fund_name", ""))

                    if not fund_id:
                        if (idx + 1) % 25 == 0:
                            logger.info(
                                f"  [{idx + 1}/{len(families)}] "
                                f"No match: {data.get('fund_name', fund_name)}"
                            )
                        continue

                    stats["matched"] += 1

                    # Step 4: Store data
                    # Update fund with key facts
                    if update_fund_from_manager(conn, fund_id, data):
                        stats["details_updated"] += 1

                    # Store share classes
                    sc_list = data.get("share_classes", [])
                    if sc_list:
                        n = upsert_share_classes(conn, fund_id, sc_list)
                        stats["share_classes_stored"] += n
                        # Also update ISIN on main fund record from primary share class
                        primary_isin = data.get("isin") or (
                            sc_list[0]["isin"] if sc_list else None
                        )
                        if primary_isin:
                            # Use the first non-hedged share class ISIN as primary
                            for sc in sc_list:
                                if not sc.get("is_hedged"):
                                    primary_isin = sc["isin"]
                                    break
                            update_fund_from_manager(conn, fund_id, {"isin": primary_isin})
                            stats["isins_updated"] += 1

                    # Store NAV history
                    nav = data.get("nav")
                    nav_date = data.get("nav_date")
                    if nav and nav_date:
                        n = upsert_nav_history(conn, fund_id, [{
                            "nav": nav,
                            "nav_date": nav_date,
                            "nav_currency": data.get("nav_currency", "HKD"),
                            "source": "blackrock_website",
                        }])
                        stats["navs_stored"] += n

                    # Store holdings
                    holdings = data.get("holdings", [])
                    if holdings:
                        n = upsert_holdings(conn, fund_id, holdings)
                        stats["holdings_stored"] += n

                    if (idx + 1) % 25 == 0:
                        logger.info(
                            f"  [{idx + 1}/{len(families)}] "
                            f"Matched={stats['matched']} "
                            f"ISINs={stats['isins_updated']} "
                            f"NAVs={stats['navs_stored']} "
                            f"Holdings={stats['holdings_stored']}"
                        )

                except Exception as e:
                    logger.warning(f"Error scraping {fund_name}: {e}")
                    stats["errors"] += 1
                    continue

            logger.info(
                f"BlackRock scrape complete: "
                f"Matched={stats['matched']}/{stats['funds_found']}, "
                f"ISINs={stats['isins_updated']}, "
                f"NAVs={stats['navs_stored']}, "
                f"ShareClasses={stats['share_classes_stored']}, "
                f"Holdings={stats['holdings_stored']}, "
                f"Errors={stats['errors']}"
            )

        except Exception as e:
            logger.error(f"BlackRock scrape failed: {e}")
            stats["errors"] += 1
        finally:
            self._close_browser()

        return stats
