"""
Fidelity / FIL Investment Management connector.

Scrapes fidelity.com.hk fund factsheet pages using Playwright.
Each page contains: ISIN, NAV, fund size, benchmark, fund managers,
Bloomberg ticker, dividends, and performance data.

Strategy:
  1. Fetch sitemap → ~599 fund factsheet URLs
  2. Visit each page with Playwright
  3. Extract fund name from page title, deduplicate by fund family
  4. Match to hk_funds by fund name with FIL/Fidelity manager constraint
  5. Store ISINs, NAVs, dividends, fund details

CE: ARI747 — FIL Investment Management (Luxembourg) S.à r.l.
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

logger = logging.getLogger("hk_funds.manager_connectors.fidelity")

FIDELITY_SITEMAP_URL = "https://www.fidelity.com.hk/static/hong-kong/sitemap.xml"


@register_connector
class FidelityConnector(BaseManagerConnector):
    """Scrapes Fidelity fund factsheet pages for ISINs, NAVs, dividends."""

    manager_ce_numbers = ["ARI747", "AAG408"]
    base_url = "https://www.fidelity.com.hk"

    request_delay: float = 2.0
    request_timeout: int = 60

    # FIL / Fidelity manager names in SFC register
    _FIL_MANAGER_SQL = (
        "AND (LOWER(fund_manager_name_en) LIKE '%fil%'"
        " OR LOWER(fund_manager_name_en) LIKE '%fidelity%')"
    )

    def __init__(self):
        super().__init__()
        self._playwright = None
        self._browser = None

    # ── Sitemap ────────────────────────────────────────────────

    def _fetch_sitemap_urls(self) -> List[str]:
        """Fetch the Fidelity sub-sitemap and return English factsheet URLs."""
        # First get the sitemap index
        resp = requests.get(
            "https://www.fidelity.com.hk/sitemap.xml",
            timeout=30,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        sitemaps = root.findall(".//sm:sitemap/sm:loc", ns)

        # Fetch the main sub-sitemap
        if sitemaps:
            sub_url = sitemaps[0].text
        else:
            sub_url = FIDELITY_SITEMAP_URL

        resp = requests.get(
            sub_url,
            timeout=30,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        urls = [el.text for el in root.findall(".//sm:url/sm:loc", ns)]

        # Filter to English factsheet URLs only
        en_urls = [u for u in urls if "/en/funds/factsheet/" in u]
        logger.info(f"Fidelity sitemap: {len(en_urls)} English factsheet URLs")
        return en_urls

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

    def _new_page(self):
        browser = self._get_browser()
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            locale="en-HK",
        )
        return context, context.new_page()

    # ── Page scraping ──────────────────────────────────────────

    def _scrape_product_page(self, page, url: str) -> Optional[Dict[str, Any]]:
        """Scrape a single Fidelity factsheet page."""
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(5)  # Fidelity SPA needs time to render fund data
        except Exception as e:
            logger.warning(f"Failed to load {url}: {e}")
            return None

        try:
            text = page.evaluate("() => document.body.innerText")
        except Exception:
            return None

        if not text or len(text) < 300:
            return None

        result: Dict[str, Any] = {
            "product_url": url,
            "source_type": "manager_website",
        }

        lines = text.split("\n")

        # ── Fund Name + ISIN from title line ──
        # "Fidelity Funds - Asian Bond Fund A-HMDIST(G)-RMB (hedged) **"
        # "ISIN: LU2262856953  |  Bond Fund"
        for i, line in enumerate(lines):
            line = line.strip()
            if line.startswith("Fidelity Funds - ") or line.startswith("Fidelity Funds 2 - ") or line.startswith("Fidelity Selected Funds - "):
                title_line = line.rstrip(" *")
                # Parse: "Fidelity Funds - FundName ShareClass"
                m = re.match(
                    r"(Fidelity (?:Funds|Funds 2|Selected Funds)) - (.+?)(?:\s+(A\d?|D\d?|I\d?|Y\d?|Class\s+\w+)[-\s].*)?$",
                    title_line,
                )
                if m:
                    result["fund_family"] = m.group(1)
                    result["fund_name"] = m.group(2).strip()
                    if m.group(3):
                        result["share_class_type"] = m.group(3)
                else:
                    # Simpler: take everything after " - " as the full name
                    result["fund_name"] = title_line.split(" - ", 1)[1].strip()

                # Look for ISIN on same line or next line
                isin_match = re.search(r"ISIN:\s*([A-Z]{2}[A-Z0-9]{10})", lines[i].strip())
                if not isin_match and i + 1 < len(lines):
                    isin_match = re.search(r"ISIN:\s*([A-Z]{2}[A-Z0-9]{10})", lines[i + 1].strip())
                if isin_match:
                    result["isin"] = isin_match.group(1)
                break

        # Fallback ISIN search: scan full page text
        if not result.get("isin"):
            isin_match = re.search(r"ISIN:\s*([A-Z]{2}[A-Z0-9]{10})", text)
            if isin_match:
                result["isin"] = isin_match.group(1)

        # ── NAV ──
        for i, line in enumerate(lines):
            line = line.strip()
            if re.match(r"NAV\s*\([A-Z]{3}\)", line) or line == "NAV":
                # NAV section: "NAV (CNY)" \n "" \n "CN¥74.4900" \n "-0.09%..." \n "19/06/2026"
                # Skip empty lines to find value
                for offset in range(1, 5):
                    if i + offset < len(lines) and lines[i + offset].strip():
                        nav_line = lines[i + offset].strip()
                        # Skip lines that look like dates or percentages
                        if re.search(r"\d{1,2}/\d{1,2}/\d{4}", nav_line):
                            continue
                        if nav_line.endswith("%") and not any(
                            c.isalpha() for c in nav_line.replace("%", "").replace(".", "").replace(",", "").replace("-", "").strip()
                        ):
                            continue
                        # Strip any leading non-digit characters (currency symbols, codes)
                        clean = re.sub(r"^[^\d]*", "", nav_line)
                        if "/" in clean:
                            continue
                        nav_match = re.match(r"([\d,]+\.?\d*)", clean)
                        if nav_match:
                            try:
                                result["nav"] = float(nav_match.group(1).replace(",", ""))
                            except ValueError:
                                pass
                            # Date is 2 lines after NAV value (skipping change line)
                            for doff in range(1, 4):
                                if i + offset + doff < len(lines):
                                    dline = lines[i + offset + doff].strip()
                                    parsed = self._parse_date(dline)
                                    if parsed:
                                        result["nav_date"] = parsed
                                        break
                            break
                # Currency from NAV header
                curr_match = re.search(r"NAV\s*\(([A-Z]+)\)", line)
                if curr_match:
                    result["nav_currency"] = curr_match.group(1)
                break

        # ── Fund facts section ──
        for i, line in enumerate(lines):
            line = line.strip()
            if line == "Fund facts":
                j = i + 1
                managers = []
                while j < len(lines):
                    raw = lines[j]
                    stripped = raw.strip()
                    if not stripped:
                        j += 1
                        continue

                    # Stop markers — these indicate the facts section has ended
                    if stripped.startswith("Data Source:") or stripped.startswith("Note:") or stripped.startswith("The fund") or stripped.startswith("Please refer"):
                        break
                    if stripped in ("Summary of investment objective", "Related documents",
                                    "Overview", "Performance", "Holdings", "Dividends"):
                        break

                    # Tab-separated key-value pair
                    if "\t" in raw:
                        parts = raw.split("\t")
                        key = parts[0].strip()
                        val = parts[1].strip() if len(parts) >= 2 else ""

                        if key == "Fund category":
                            if val and val != "-":
                                result["asset_class"] = val
                        elif key.startswith("Index"):
                            if val and val != "-":
                                result["benchmark_name"] = val
                        elif key == "Fund manager":
                            if val and val != "-":
                                managers.append(val)
                        elif key == "Class launch date":
                            result["share_class_inception_date"] = self._parse_date(val)
                        elif key.startswith("Fund size"):
                            # "CN¥4,040.55 (million)"
                            size_match = re.search(r"(?:[A-Z]{3})?\s*[¥$€]?\s*([\d,.]+)", val)
                            if size_match:
                                try:
                                    size = float(size_match.group(1).replace(",", ""))
                                    if "million" in val.lower():
                                        size *= 1_000_000
                                    elif "billion" in val.lower():
                                        size *= 1_000_000_000
                                    result["fund_size_hkd"] = size
                                except ValueError:
                                    pass
                            # Date from key: "Fund size (31/05/2026)"
                            date_in_key = re.search(r"\(([^)]+)\)", key)
                            if date_in_key:
                                result["fund_size_date"] = self._parse_date(date_in_key.group(1))
                        elif key == "Bloomberg ticker":
                            if val and val != "-":
                                result["bloomberg_ticker"] = val
                    else:
                        # Non-tab lines after "Fund manager" key: continuation names
                        # A person name is 2-3 words, capitalized, no tabs
                        if managers and re.match(r"^[A-Z][a-zA-Z'-]+ [A-Z][a-zA-Z'-]+(?:\s[A-Z][a-zA-Z'-]+)?$", stripped):
                            if stripped not in managers:
                                managers.append(stripped)
                        # Else: not a name, could be boilerplate — skip

                    j += 1

                if managers:
                    result["portfolio_manager_name"] = ", ".join(managers)
                break

        # ── Dividends table ──
        dividends = page.evaluate(
            """() => {
                const tables = document.querySelectorAll('table');
                for (const t of tables) {
                    const headers = Array.from(t.querySelectorAll('th')).map(h => h.textContent?.trim());
                    if (headers.includes('Dates') && headers.includes('Dividends')) {
                        const rows = Array.from(t.rows).slice(1);
                        return rows.map(row => {
                            const cells = Array.from(row.cells);
                            return {
                                ex_date: cells[0]?.textContent?.trim() || '',
                                dividend_amount: cells[1]?.textContent?.trim() || '',
                            };
                        });
                    }
                }
                return [];
            }"""
        )

        cleaned_divs = []
        for d in dividends:
            ex_date = d.get("ex_date", "")
            amount_str = d.get("dividend_amount", "")
            if not ex_date or not amount_str:
                continue
            parsed_date = self._parse_date(ex_date)
            amount_match = re.search(r"[\d,.]+", amount_str)
            if parsed_date and amount_match:
                try:
                    cleaned_divs.append({
                        "ex_date": parsed_date,
                        "dividend_amount": float(amount_match.group().replace(",", "")),
                        "dividend_currency": result.get("nav_currency", "HKD"),
                        "dividend_type": "income",
                        "source": "fidelity_website",
                    })
                except ValueError:
                    pass
        result["dividends"] = cleaned_divs

        return result

    # ── Name Matching ──────────────────────────────────────────

    def _match_fund_name(self, conn, extracted_name: str) -> Optional[int]:
        """Match Fidelity fund name to hk_funds.id."""
        if not extracted_name:
            return None

        candidates = [extracted_name]

        # Also try stripping "Fidelity Funds - " prefix if present
        for prefix in ("Fidelity Funds - ", "Fidelity Funds 2 - ", "Fidelity Selected Funds - "):
            if extracted_name.startswith(prefix):
                candidates.append(extracted_name[len(prefix):])
                break

        for candidate in candidates:
            c = re.sub(r"\s+", " ", candidate.lower().strip())
            c = re.sub(r"\s+(fund|class\s+\w+|etf)\s*$", "", c, flags=re.IGNORECASE)

            for query, params in [
                ("LOWER(fund_name_en) = ?", [c]),
                ("LOWER(fund_name_en) LIKE ?", [f"%{c}%"]),
            ]:
                row = conn.execute(
                    f"""SELECT id, fund_name_en FROM hk_funds
                       WHERE {query} AND is_active = true
                       {self._FIL_MANAGER_SQL}
                       LIMIT 1""",
                    params,
                ).fetchone()
                if row:
                    return row[0]

            # Word-level matching
            keywords = [w for w in c.split() if len(w) > 3 and w not in ("fund", "class", "etf")]
            if len(keywords) >= 2:
                conditions = " AND ".join(["LOWER(fund_name_en) LIKE ?" for _ in keywords])
                params = [f"%{kw}%" for kw in keywords]
                row = conn.execute(
                    f"""SELECT id, fund_name_en FROM hk_funds
                       WHERE {conditions} AND is_active = true
                       {self._FIL_MANAGER_SQL}
                       LIMIT 1""",
                    params,
                ).fetchone()
                if row:
                    return row[0]

        return None

    # ── Main Interface ─────────────────────────────────────────

    def get_fund_list(self) -> List[Dict[str, Any]]:
        urls = self._fetch_sitemap_urls()
        return [{"product_url": url} for url in urls]

    def get_fund_details(self, product_url: str) -> Optional[Dict[str, Any]]:
        context, page = self._new_page()
        try:
            return self._scrape_product_page(page, product_url)
        finally:
            context.close()

    def scrape_and_store(self, conn, date_str: str = None) -> Dict[str, int]:
        from hk_funds.storage import (
            update_fund_from_manager,
            upsert_dividends,
            upsert_nav_history,
        )

        today = date_str or datetime.now().strftime("%Y-%m-%d")
        stats = {
            "funds_found": 0, "matched": 0, "isins_updated": 0,
            "navs_stored": 0, "dividends_stored": 0, "details_updated": 0, "errors": 0,
        }

        try:
            urls = self._fetch_sitemap_urls()
            stats["funds_found"] = len(urls)
            seen_names: set = set()

            # Process in batches of 15 with browser restarts
            BATCH_SIZE = 20
            BATCH_DELAY = 5  # seconds between pages
            BATCH_REST_DELAY = 10  # seconds rest between batches

            logger.info(
                f"Starting Playwright scrape of {len(urls)} Fidelity fund pages "
                f"(batches of {BATCH_SIZE}, {BATCH_DELAY}s delay)"
            )

            for idx, url in enumerate(urls):
                # Delay between pages
                if idx > 0:
                    time.sleep(BATCH_DELAY)

                # Restart browser at batch boundaries
                if idx > 0 and idx % BATCH_SIZE == 0:
                    logger.info(
                        f"  Batch boundary at [{idx}/{len(urls)}] — restarting browser "
                        f"(Matched={stats['matched']}, ISINs={stats['isins_updated']})"
                    )
                    self._close_browser()
                    time.sleep(BATCH_REST_DELAY)

                try:
                    context, page = self._new_page()
                    try:
                        data = self._scrape_product_page(page, url)
                    finally:
                        context.close()
                except Exception as e:
                    logger.warning(f"Error on {url}: {e}")
                    stats["errors"] += 1
                    continue

                if not data:
                    stats["errors"] += 1
                    continue

                fund_name = data.get("fund_name", "")
                if not fund_name:
                    stats["errors"] += 1
                    continue

                fund_id = self._match_fund_name(conn, fund_name)

                if not fund_id:
                    if (idx + 1) % 10 == 0:
                        logger.info(
                            f"  [{idx + 1}/{len(urls)}] "
                            f"Matched={stats['matched']} (no match: {fund_name})"
                        )
                    continue

                stats["matched"] += 1

                # Store fund details
                if update_fund_from_manager(conn, fund_id, data):
                    stats["details_updated"] += 1

                # Set ISIN
                isin = data.get("isin")
                if isin:
                    update_fund_from_manager(conn, fund_id, {"isin": isin})
                    stats["isins_updated"] += 1

                # Store NAV
                nav = data.get("nav")
                nav_date = data.get("nav_date")
                if nav and nav_date:
                    n = upsert_nav_history(conn, fund_id, [{
                        "nav": nav,
                        "nav_date": nav_date,
                        "nav_currency": data.get("nav_currency", "HKD"),
                        "source": "fidelity_website",
                    }])
                    stats["navs_stored"] += n

                # Store dividends
                dividends = data.get("dividends", [])
                if dividends:
                    n = upsert_dividends(conn, fund_id, dividends)
                    stats["dividends_stored"] += n

                if (idx + 1) % BATCH_SIZE == 0:
                    logger.info(
                        f"  [{idx + 1}/{len(urls)}] "
                        f"Matched={stats['matched']} "
                        f"ISINs={stats['isins_updated']} "
                        f"NAVs={stats['navs_stored']} "
                        f"Divs={stats['dividends_stored']}"
                    )

            logger.info(
                f"Fidelity scrape complete: "
                f"Matched={stats['matched']}/{stats['funds_found']}, "
                f"ISINs={stats['isins_updated']}, "
                f"NAVs={stats['navs_stored']}, "
                f"Dividends={stats['dividends_stored']}, "
                f"Errors={stats['errors']}"
            )

        except Exception as e:
            logger.error(f"Fidelity scrape failed: {e}")
            stats["errors"] += 1
        finally:
            self._close_browser()

        return stats
