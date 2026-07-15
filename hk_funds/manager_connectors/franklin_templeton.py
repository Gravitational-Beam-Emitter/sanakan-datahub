"""
Franklin Templeton connector.

Uses Franklin Templeton's GraphQL API (api/pds/price-and-performance) to extract
fund ISINs, NAVs, benchmarks, AUM, and fund manager data.

Strategy:
  1. Use Playwright to pass the role-selection gateway and get session cookies
  2. Query PPSS for fund list (~81 mutual funds for HK)
  3. Query shareclass/identifiers for ISINs per share class
  4. Query FundFact for each fund's detailed data (ISIN, benchmark, fees, etc.)
  5. Query PricesHistory for NAV data
  6. Query DistributionHistory for dividends
  7. Match funds to hk_funds by name with Franklin Templeton manager constraint

CE: BSQ553 — Franklin Templeton Investments (Asia) Limited
"""

from __future__ import annotations

import logging
import re
import time
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

from hk_funds.manager_connectors.base import BaseManagerConnector, register_connector

logger = logging.getLogger("hk_funds.manager_connectors.franklin_templeton")

API_URL = "https://www.franklintempleton.com.hk/api/pds/price-and-performance"
GATEWAY_URL = "https://www.franklintempleton.com.hk/en-hk/investor/products"


@register_connector
class FranklinTempletonConnector(BaseManagerConnector):
    """Extracts fund data from Franklin Templeton's GraphQL API."""

    manager_ce_numbers = ["BSQ553"]
    base_url = "https://www.franklintempleton.com.hk"

    request_delay: float = 1.0
    request_timeout: int = 30

    _FT_MANAGER_SQL = (
        "AND (LOWER(fund_manager_name_en) LIKE '%franklin%'"
        " OR LOWER(fund_manager_name_en) LIKE '%templeton%'"
        " OR LOWER(fund_manager_name_en) LIKE '%ftgf%')"
    )

    def __init__(self):
        super().__init__()
        self._playwright = None
        self._browser = None
        self._api_session: Optional[requests.Session] = None
        self._api_headers = {
            "Content-Type": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
        }

    # ── Authentication ──────────────────────────────────────────

    def _authenticate(self) -> requests.Session:
        """Use Playwright to pass the role-selection gateway, return a requests Session with cookies."""
        if self._api_session is not None:
            return self._api_session

        logger.info("Authenticating with Franklin Templeton gateway...")

        if self._browser is None:
            from playwright.sync_api import sync_playwright
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=True)

        context = self._browser.new_context(
            user_agent=self._api_headers["User-Agent"],
            locale="en-HK",
        )
        page = context.new_page()

        try:
            page.goto(GATEWAY_URL, wait_until="domcontentloaded", timeout=30000)
            time.sleep(3)
            # Click through the role-selection gateway
            try:
                page.click("text=Individual Investor", timeout=5000)
                time.sleep(3)
            except Exception:
                logger.warning("Could not click 'Individual Investor' — may already be authenticated")

            # Get cookies for requests session
            cookies = context.cookies()
            self._api_session = requests.Session()
            for c in cookies:
                self._api_session.cookies.set(c["name"], c["value"])

            logger.info("Authentication successful")
            return self._api_session
        finally:
            context.close()

    def _graphql(self, query: str) -> dict:
        """Execute a GraphQL query against the FT API."""
        session = self._authenticate()
        resp = session.post(
            API_URL,
            json={"query": query},
            headers=self._api_headers,
            timeout=self.request_timeout,
        )
        resp.raise_for_status()
        return resp.json()

    # ── Cleanup ─────────────────────────────────────────────────

    def _close_browser(self):
        if self._browser:
            self._browser.close()
            self._browser = None
        if self._playwright:
            self._playwright.stop()
            self._playwright = None
        self._api_session = None

    # ── Fund List ───────────────────────────────────────────────

    def get_fund_list(self) -> List[Dict[str, Any]]:
        """Get all mutual funds from Franklin Templeton HK."""
        query = """
        query {
          PPSS(countrycode: "HK", languagecode: "en_GB", productType: "mf") {
            fundid
            fundname
            assetclass
            producttype
          }
        }
        """
        data = self._graphql(query)
        funds = []
        if "data" in data and "PPSS" in data["data"]:
            for f in data["data"]["PPSS"]:
                if isinstance(f, dict):
                    funds.append({
                        "fund_id": f.get("fundid"),
                        "fund_name": f.get("fundname"),
                        "asset_class": f.get("assetclass"),
                        "product_type": f.get("producttype"),
                    })
        logger.info(f"Franklin Templeton: {len(funds)} mutual funds found via API")
        return funds

    # ── ISINs ───────────────────────────────────────────────────

    def get_all_isins(self) -> Dict[str, List[Dict[str, str]]]:
        """Get ISIN codes for all funds. Returns {fundid: [{shclcode, isin}, ...]}."""
        query = """
        query {
          PPSS(countrycode: "HK", languagecode: "en_GB", productType: "mf") {
            shareclass {
              identifiers {
                fundid
                shclcode
                isin
              }
            }
          }
        }
        """
        data = self._graphql(query)
        isins: Dict[str, List[Dict[str, str]]] = defaultdict(list)
        if "data" in data and "PPSS" in data["data"]:
            for fund in data["data"]["PPSS"]:
                if not isinstance(fund, dict):
                    continue
                for sc in fund.get("shareclass", []):
                    if not isinstance(sc, dict):
                        continue
                    ident = sc.get("identifiers", {})
                    if isinstance(ident, dict):
                        fid = ident.get("fundid")
                        isin = ident.get("isin")
                        shcl = ident.get("shclcode", "")
                        if fid and isin:
                            isins[fid].append({"shclcode": shcl, "isin": isin})
        return dict(isins)

    # ── Fund Facts ──────────────────────────────────────────────

    def get_fund_facts(self, fund_id: str, share_class: str) -> Dict[str, Any]:
        """Get detailed fund facts via FundFact query.

        The FundFact API returns a list of sectionstd/elemname/elemvalue triples
        grouped by standardized section codes.
        """
        query = f"""
        query {{
          FundFact(fundid: "{fund_id}", shareclasscode: "{share_class}",
                   countrycode: "HK", languagecode: "en_GB") {{
            sectionstd
            section
            elemname
            elemvalue
            asofdate
          }}
        }}
        """
        data = self._graphql(query)
        result: Dict[str, Any] = {
            "fund_id": fund_id,
            "share_class_code": share_class,
        }

        if "data" not in data or not data["data"] or "FundFact" not in data["data"]:
            return result

        facts = data["data"]["FundFact"]
        if not facts:
            return result

        # Build a lookup: sectionstd → {elemname → value}
        sections: Dict[str, Dict[str, str]] = defaultdict(dict)
        for f in facts:
            if not isinstance(f, dict):
                continue
            sec = f.get("sectionstd", "")
            elem = f.get("elemname", "")
            val = f.get("elemvalue", "")
            if sec and elem:
                sections[sec][elem] = val

        # Extract data from known sectionstd codes
        ff = sections.get("FUNDFACTS", {})
        if ff:
            result["benchmark_name"] = ff.get("Benchmark", "")
            result["asset_class"] = ff.get("Asset Class", "")
            result["fund_inception_date"] = self._parse_date(ff.get("Fund Inception Date", ""))

            # Fund size / AUM
            aum_str = ff.get("Total Net Assets", "")
            if aum_str and aum_str != "-":
                result["fund_size_hkd"] = self._parse_amount(aum_str)

            # Base currency
            base_ccy = ff.get("Base Currency for Fund", "")
            if base_ccy:
                result["nav_currency"] = base_ccy

        # Identifiers
        ident = sections.get("IDENTIFIERS", {})
        if ident:
            result["isin"] = ident.get("ISIN Code") or ident.get("ISIN", "")
            result["bloomberg_ticker"] = ident.get("Bloomberg Code", "")

        # Fees
        fees = sections.get("SALESCHARGESANDFEES", {})
        if fees:
            mgmt_fee = fees.get("Management Charge", "")
            if mgmt_fee and mgmt_fee != "-":
                try:
                    result["management_fee_pct"] = float(mgmt_fee.replace("%", ""))
                except ValueError:
                    pass

            ter = fees.get("Total Expense Ratio", "")
            if ter and ter != "-":
                try:
                    result["expense_ratio_pct"] = float(ter.replace("%", ""))
                except ValueError:
                    pass

            initial = fees.get("Initial Charge", "")
            if initial and initial != "-":
                try:
                    result["front_load_pct"] = float(initial.replace("%", ""))
                except ValueError:
                    pass

        # Additional info
        extra = sections.get("ADDITIONALINFO", {})
        if extra:
            result["domicile"] = extra.get("Domicile", "")
            result["share_class_inception_date"] = self._parse_date(
                extra.get("Share Class Inception Date", "")
            )

        # Dividends
        dists = sections.get("YIELDSANDDISTRIBUTIONS", {})
        if dists:
            div_amt = dists.get("Distribution Amount", "")
            div_freq = dists.get("Distribution Frequency", "")
            if div_freq and div_freq != "-":
                result["distribution_frequency"] = div_freq

        return result

    # ── NAV / Prices ────────────────────────────────────────────

    def get_prices_history(self, fund_id: str, share_class: str) -> List[Dict[str, Any]]:
        """Get recent NAV history for a fund via PricesHistory query.

        Returns list of {nav, nav_date, nav_currency, nav_change_pct}.
        """
        query = f"""
        query {{
          PricesHistory(fundid: "{fund_id}", shareclasscode: "{share_class}",
                        countrycode: "HK", languagecode: "en_GB") {{
            prices {{
              navdate
              nav
              currcode
              navchngpct
            }}
          }}
        }}
        """
        data = self._graphql(query)
        results = []
        if "data" in data and data["data"] and "PricesHistory" in data["data"]:
            hist = data["data"]["PricesHistory"]
            if not isinstance(hist, dict):
                return results
            for entry in hist.get("prices", []) or []:
                if not isinstance(entry, dict):
                    continue
                nav_str = entry.get("nav", "")
                nav_date = self._parse_date(entry.get("navdate", ""))
                if nav_str and nav_date:
                    try:
                        # Strip currency symbols and commas
                        clean_nav = re.sub(r"[^\d.]", "", nav_str.replace(",", ""))
                        results.append({
                            "nav": float(clean_nav),
                            "nav_date": nav_date,
                            "nav_currency": entry.get("currcode", "USD"),
                            "source": "franklin_templeton_api",
                        })
                    except ValueError:
                        pass
        return results

    # ── Dividends ───────────────────────────────────────────────

    def get_distribution_history(self, fund_id: str, share_class: str) -> List[Dict[str, Any]]:
        """Get dividend distribution history via DistributionHistory query."""
        query = f"""
        query {{
          DistributionHistory(fundid: "{fund_id}", shareclasscode: "{share_class}",
                              countrycode: "HK", languagecode: "en_GB") {{
            distribution {{
              incmdistexdivdate
              incmdistamt
              incmdistexpayabledate
              currcode
            }}
          }}
        }}
        """
        data = self._graphql(query)
        results = []
        if "data" in data and data["data"] and "DistributionHistory" in data["data"]:
            hist = data["data"]["DistributionHistory"]
            if not isinstance(hist, dict):
                return results
            for entry in hist.get("distribution", []) or []:
                if not isinstance(entry, dict):
                    continue
                ex_date = self._parse_date(entry.get("incmdistexdivdate", ""))
                amount_str = entry.get("incmdistamt", "")
                if ex_date and amount_str:
                    try:
                        clean_amt = re.sub(r"[^\d.]", "", amount_str.replace(",", ""))
                        results.append({
                            "ex_date": ex_date,
                            "dividend_amount": float(clean_amt),
                            "dividend_currency": entry.get("currcode", "USD"),
                            "dividend_type": "income",
                            "source": "franklin_templeton_api",
                        })
                    except ValueError:
                        pass
        return results

    # ── Name Matching ──────────────────────────────────────────

    def _match_fund_name(self, conn, extracted_name: str) -> Optional[int]:
        """Match Franklin Templeton fund name to hk_funds.id."""
        if not extracted_name:
            return None

        c = re.sub(r"\s+", " ", extracted_name.lower().strip())
        # Remove trailing "fund" suffix
        c = re.sub(r"\s+fund\s*$", "", c, flags=re.IGNORECASE)

        for query, params in [
            ("LOWER(fund_name_en) = ?", [c]),
            ("LOWER(fund_name_en) LIKE ?", [f"%{c}%"]),
        ]:
            row = conn.execute(
                f"""SELECT id, fund_name_en FROM hk_funds
                   WHERE {query} AND is_active = true
                   {self._FT_MANAGER_SQL}
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
                   {self._FT_MANAGER_SQL}
                   LIMIT 1""",
                params,
            ).fetchone()
            if row:
                return row[0]

        return None

    # ── Main Interface ─────────────────────────────────────────

    def get_fund_details(self, fund_id: str, share_class: str = None) -> Optional[Dict[str, Any]]:
        """Get details for a specific fund by its Franklin Templeton fund ID."""
        if not share_class:
            isins = self.get_all_isins()
            if fund_id in isins and isins[fund_id]:
                share_class = isins[fund_id][0]["shclcode"]
            else:
                return None
        return self.get_fund_facts(fund_id, share_class)

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
            # Get fund list
            funds = self.get_fund_list()
            stats["funds_found"] = len(funds)

            # Get all ISINs
            all_isins = self.get_all_isins()
            total_isins = sum(len(v) for v in all_isins.values())
            logger.info(
                f"Franklin Templeton: {len(funds)} funds, {total_isins} ISINs "
                f"across {len(all_isins)} funds with ISINs"
            )

            for idx, fund in enumerate(funds):
                fund_id = fund.get("fund_id")
                fund_name = fund.get("fund_name", "")

                if not fund_id or not fund_name:
                    stats["errors"] += 1
                    continue

                # Match to SFC register
                hk_fund_id = self._match_fund_name(conn, fund_name)

                if not hk_fund_id:
                    if (idx + 1) % 20 == 0:
                        logger.info(
                            f"  [{idx + 1}/{len(funds)}] "
                            f"Matched={stats['matched']} (no match: {fund_name})"
                        )
                    continue

                stats["matched"] += 1

                # Get ISINs for this fund
                fund_isins = all_isins.get(fund_id, [])
                primary_isin = fund_isins[0]["isin"] if fund_isins else None
                primary_sc = fund_isins[0]["shclcode"] if fund_isins else "A"

                # Get detailed fund facts
                time.sleep(0.3)
                facts = self.get_fund_facts(fund_id, primary_sc)

                # Get NAV from PricesHistory
                time.sleep(0.3)
                prices = self.get_prices_history(fund_id, primary_sc)

                # Merge data
                data = {
                    "fund_name": fund_name,
                    "asset_class": fund.get("asset_class"),
                    "product_url": f"{self.base_url}/en-hk/our-funds/price-and-performance/mutual-funds/products/{fund_id}",
                    "source_type": "manager_website",
                }
                if primary_isin:
                    data["isin"] = primary_isin
                data.update({k: v for k, v in facts.items() if v})

                # Store fund details
                if update_fund_from_manager(conn, hk_fund_id, data):
                    stats["details_updated"] += 1

                # Set ISIN on fund record
                if primary_isin:
                    update_fund_from_manager(conn, hk_fund_id, {"isin": primary_isin})
                    stats["isins_updated"] += 1

                # Store share classes
                if fund_isins:
                    from hk_funds.storage import upsert_share_classes
                    sc_records = []
                    for sc in fund_isins:
                        sc_records.append({
                            "isin": sc["isin"],
                            "share_class_name": sc.get("shclcode", ""),
                            "source": "franklin_templeton_api",
                        })
                    upsert_share_classes(conn, hk_fund_id, sc_records)

                # Store NAV
                if prices:
                    n = upsert_nav_history(conn, hk_fund_id, prices)
                    stats["navs_stored"] += n

                # Get and store dividends
                time.sleep(0.3)
                dividends = self.get_distribution_history(fund_id, primary_sc)
                if dividends:
                    n = upsert_dividends(conn, hk_fund_id, dividends)
                    stats["dividends_stored"] += n

                if (idx + 1) % 20 == 0:
                    logger.info(
                        f"  [{idx + 1}/{len(funds)}] "
                        f"Matched={stats['matched']} "
                        f"ISINs={stats['isins_updated']} "
                        f"NAVs={stats['navs_stored']} "
                        f"Divs={stats['dividends_stored']}"
                    )

            logger.info(
                f"Franklin Templeton scrape complete: "
                f"Matched={stats['matched']}/{stats['funds_found']}, "
                f"ISINs={stats['isins_updated']}, "
                f"NAVs={stats['navs_stored']}, "
                f"Dividends={stats['dividends_stored']}, "
                f"Details={stats['details_updated']}, "
                f"Errors={stats['errors']}"
            )

        except Exception as e:
            logger.error(f"Franklin Templeton scrape failed: {e}")
            stats["errors"] += 1
        finally:
            self._close_browser()

        return stats
