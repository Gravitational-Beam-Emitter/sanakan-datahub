"""
Base class for manager website connectors.

Each connector scrapes a specific fund manager's website to extract:
  - ISIN codes (critical — currently 0 ISINs in hk_funds)
  - NAV / pricing data
  - Fund details (fees, benchmarks, AUM, inception date)
  - Performance metrics

Connector Selection:
    A connector is matched to a manager by SFC CE number. Each connector
    declares `manager_ce_numbers` — the list of CE numbers it can handle.
"""

from __future__ import annotations

import logging
import re
import time
from abc import ABC, abstractmethod
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger("hk_funds.manager_connectors")

# Registry and decorator (imported by connector modules)
_registry: Dict[str, type] = {}


def register_connector(cls):
    """Decorator to register a connector class for its declared CE numbers."""
    for ce in cls.manager_ce_numbers:
        _registry[ce] = cls
    logger.debug(f"Registered {cls.__name__} for CE numbers: {cls.manager_ce_numbers}")
    return cls


def get_connector_registry() -> Dict[str, type]:
    """Return the connector registry dict."""
    return _registry

# Known SFC fund manager websites (verified as of 2026-06 via webb-database.com)
# Maps CE number → website URL. CE numbers cross-referenced with hk_fund_managers.
MANAGER_WEBSITES: Dict[str, str] = {
    # BlackRock group
    "AFF275": "https://www.blackrock.com/hk",
    # Fidelity / FIL group
    "ARI747": "https://www.fidelity.com.hk",
    # Franklin Templeton
    "BSQ553": "https://www.franklintempleton.com.hk",
    # Allianz Global Investors
    "BFE699": "https://hk.allianzgi.com",
    # JPMorgan
    "AAA121": "https://am.jpmorgan.com/hk",
    # HSBC
    "AAF684": "https://www.assetmanagement.hsbc.com.hk",
    # BOCHK
    "AWJ005": "https://www.bochkam.com",
    # Value Partners
    "AFJ002": "https://www.valuepartners.com.hk",
    # Invesco
    "AAJ770": "https://www.invesco.com/hk",
    # Amundi
    "AAB444": "https://www.amundi.com.hk",
    # CSOP
    "ARN075": "https://www.csopasset.com",
    # Mirae Asset
    "BJB333": "https://www.am.miraeasset.com.hk",
    # Hang Seng IM
    "ABT330": "https://www.hangseng.com",
    # China Asset Management (Hong Kong)
    "ARS988": "https://www.chinaamc.com.hk",
    # Harvest Global
    "ASE565": "https://www.harvestglobal.com.hk",
    # Bosera
    "AVR135": "https://www.bosera.com.hk",
    # E Fund
    "ARO593": "https://www.efunds.com.hk",
    # Samsung AM
    "AQG442": "https://www.samsungetfhk.com",
    # Premia Partners
    "BIN676": "https://www.premia-partners.com",
    # ICBC Asset Management
    "AAY077": "https://www.icbcam.com.hk",
    # State Street (Hong Kong via Asia entity)
    "AEI343": "https://www.ssga.com/hk",
    # Asset Management Group
    "AMT657": "https://asset-mg.com",
    # BEA Union Investment
    "AAJ159": "https://buim.com",
    # PineBridge Investments
    "AFD869": "https://www.pinebridge.com",
    # Principal
    "AFA235": "https://www.principal.com.hk",
    # Taikang Asset Management
    "ARG103": "https://hk.taikangasset.cn",
    # GF International
    "AXL121": "https://www.gffunds.com.hk",
    # Da Cheng International
    "ATE045": "https://www.dcfund.com.hk",
    # UBS Asset Management
    "AGP568": "https://www.ubs.com/hk/en/assetmanagement",
    # Ping An Asset Management
    "AOD938": "https://asset.pingan.com.hk",
    # China Universal AM
    "AUI816": "https://www.99fund.com.hk",
    # AllianceBernstein
    "ADX555": "https://www.abfunds.com.hk",
    # CITIC Securities / CLSA
    "ARE947": "https://www.clsa.com",
    # Da Cheng International
    "ATE045": "https://www.dcfund.com.hk",
    # KGI Asset Management
    "AEN441": "https://www.kgi.com.hk/en/asset-management",
    # Cinda International Asset Management (Webb CE; actual SFC CE TBD)
    "WEBB-15352": "https://www.cinda.com.hk/en/shinya_public_fund.php",
    # Fubon Fund Management (Hong Kong)
    "AAA662": "https://www.fubonfund.com.hk/eng",
    # Income Partners Asset Management
    "ABT605": "https://www.incomepartners.com",
    # CMB International Asset Management
    "AVA101": "https://www.cmbi.com",
    # Pickers Capital Management
    "BDW926": "https://www.pickerscapital.com",
    # CMS Asset Management (HK)
    "WEBB-68518": "https://www.cmschina.com.hk",
    # HuaAn Asset Management (Hong Kong)
    "WEBB-131720": "https://www.huaan.com.hk",
    # China Life Franklin Asset Management
    "ANL846": "https://www.clamc.com.hk",
    # Capital Group (Capital International Management Company)
    "AOK434": "https://www.capitalgroup.com/individual-investors/hk/en/investments/fund-centre.html",
    # Ninety One (Ninety One Hong Kong Limited)
    "WEBB-2094652": "https://ninetyone.com/en/hong-kong/funds-literature/funds",
}


class BaseManagerConnector(ABC):
    """Base class for scraping fund data from a manager's website.

    Subclasses must implement:
      - get_fund_list() → List of fund dicts with ISINs
      - At least one of: get_fund_nav(), get_fund_details()
    """

    # SFC CE numbers this connector handles
    manager_ce_numbers: List[str] = []

    # Base URL of the manager's fund center / fund prices page
    base_url: str = ""

    # Rate limiting
    request_delay: float = 1.0
    request_timeout: int = 30

    def __init__(self):
        self._session: Optional[requests.Session] = None

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update({
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9,zh-HK;q=0.8,zh;q=0.7",
            })
        return self._session

    # ── Abstract methods ──────────────────────────────────────

    @abstractmethod
    def get_fund_list(self) -> List[Dict[str, Any]]:
        """Return all funds from this manager's website.

        Each dict should have at minimum:
          - isin: ISIN code (e.g. 'LU0827887357')
          - fund_name: fund name as displayed on the website
          - currency: trading currency (e.g. 'USD', 'HKD')
          - fund_type: asset class / category

        Optional but valuable:
          - bloomberg_ticker
          - nav: latest NAV
          - nav_date: date of latest NAV
          - fund_class: share class (Acc, Dis, Hedged, etc.)
          - product_url: URL to the fund's detail page on the manager site
        """
        ...

    def get_fund_nav(self, isin: str) -> Optional[Dict[str, Any]]:
        """Get latest NAV for a fund by ISIN.

        Return: {nav, nav_date, nav_currency} or None.
        """
        return None

    def get_fund_details(self, isin: str) -> Optional[Dict[str, Any]]:
        """Get detailed fund info by ISIN.

        Return dict with any of:
          {expense_ratio_pct, front_load_pct, back_load_pct,
           management_fee_pct, benchmark_name, fund_inception_date,
           aum, aum_date, distribution_frequency, dividend_yield_12m_pct,
           fund_manager_name_en, kfs_document_url, product_key_features,
           min_subscription_hkd, min_subscription_usd, subscription_mode,
           redemption_frequency}
        """
        return None

    def get_fund_nav_history(
        self, isin: str, start_date: str = None, end_date: str = None
    ) -> List[Dict[str, Any]]:
        """Get NAV history for a fund.

        Return: [{nav_date, nav, nav_currency}, ...]
        """
        return []

    def get_fund_performance(self, isin: str) -> Optional[Dict[str, Any]]:
        """Get performance metrics for a fund.

        Return: {ytd_return_pct, return_1m_pct, return_3m_pct, return_6m_pct,
                 return_1y_pct, return_3y_annualized_pct, return_5y_annualized_pct,
                 std_dev_3y, sharpe_ratio_3y, max_drawdown_pct, ...}
        """
        return None

    # ── Company Profile Scraping ───────────────────────────────

    def _scrape_page_text(self, url: str, timeout: int = 20) -> Optional[str]:
        """Fetch a page and return cleaned visible text.

        Strips HTML tags, scripts, styles, and collapses whitespace.
        Returns None if the page can't be fetched.
        """
        try:
            resp = self._get(url, timeout=timeout)
            text = resp.text
            # Strip scripts and styles
            text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)
            text = self._clean_html(text)
            # Collapse whitespace
            text = re.sub(r'\n\s*\n', '\n', text)
            text = re.sub(r'[ \t]+', ' ', text)
            return text.strip()
        except Exception as e:
            logger.debug(f"[{self.__class__.__name__}] Failed to scrape {url}: {e}")
            return None

    def _try_url_patterns(self, paths: List[str], timeout: int = 20) -> Optional[str]:
        """Try multiple URL path patterns, return text from first successful fetch."""
        base = self.base_url.rstrip("/")
        for path in paths:
            url = f"{base}{path}" if path.startswith("/") else f"{base}/{path}"
            text = self._scrape_page_text(url, timeout=timeout)
            if text and len(text) > 200:
                return text
        return None

    def get_about_page(self) -> Optional[str]:
        """Scrape the About Us / company overview page.

        Tries common URL patterns across manager websites. Override in
        connector subclass for site-specific paths.
        """
        paths = [
            "/en/about", "/en/about-us", "/en/who-we-are",
            "/en/our-firm", "/en/company", "/en/corporate-profile",
            "/about", "/about-us", "/who-we-are",
            "/en/about/overview", "/en/about-us/our-firm",
            "/en/introduction", "/en/profile",
            # Chinese paths
            "/zh/about", "/zh/about-us", "/zh/who-we-are",
            "/sc/about", "/sc/about-us",
            # Site-specific known paths
            "/en/about-blackrock", "/en/who-we-are/overview",
            "/en/about-us/company-overview",
        ]
        return self._try_url_patterns(paths)

    def get_team_page(self) -> Optional[str]:
        """Scrape the team / people / leadership page.

        Override in connector subclass for site-specific paths.
        """
        paths = [
            "/en/team", "/en/our-team", "/en/people",
            "/en/leadership", "/en/management-team", "/en/management",
            "/en/about/leadership", "/en/about/management",
            "/en/who-we-are/leadership", "/en/who-we-are/our-people",
            "/team", "/our-team", "/people", "/leadership",
            "/zh/team", "/zh/management",
        ]
        return self._try_url_patterns(paths)

    def get_contact_page(self) -> Optional[str]:
        """Scrape the contact / office locations page.

        Override in connector subclass for site-specific paths.
        """
        paths = [
            "/en/contact", "/en/contact-us", "/en/offices",
            "/en/locations", "/en/global-presence",
            "/contact", "/contact-us", "/offices",
            "/zh/contact", "/zh/contact-us",
        ]
        return self._try_url_patterns(paths)

    def get_news_page(self) -> Optional[str]:
        """Scrape the news / press / awards page.

        Override in connector subclass for site-specific paths.
        """
        paths = [
            "/en/news", "/en/press", "/en/media",
            "/en/about/awards", "/en/awards", "/en/recognition",
            "/en/insights", "/en/newsroom",
            "/news", "/press", "/media", "/awards",
        ]
        return self._try_url_patterns(paths)

    def scrape_company_profile(self) -> Dict[str, Any]:
        """Orchestrate full company profile scrape.

        Visits About, Team, Contact, and News pages, concatenates the text,
        and returns raw scrape results ready for LLM extraction.

        Returns: {
            "about_text": str or None,
            "team_text": str or None,
            "contact_text": str or None,
            "news_text": str or None,
            "combined_text": str (all non-None pages joined),
            "source_urls": [list of URLs that returned content],
        }
        """
        sources = []
        combined_parts = []

        about = self.get_about_page()
        if about:
            sources.append("about")
            combined_parts.append(about)

        team = self.get_team_page()
        if team:
            sources.append("team")
            combined_parts.append(team)

        contact = self.get_contact_page()
        if contact:
            sources.append("contact")
            combined_parts.append(contact)

        news = self.get_news_page()
        if news:
            sources.append("news")
            combined_parts.append(news)

        return {
            "about_text": about,
            "team_text": team,
            "contact_text": contact,
            "news_text": news,
            "combined_text": "\n\n---\n\n".join(combined_parts) if combined_parts else None,
            "source_urls": sources,
        }

    # ── Utility methods ───────────────────────────────────────

    def _get(self, url: str, **kwargs) -> requests.Response:
        """GET with rate limiting and error handling."""
        time.sleep(self.request_delay)
        timeout = kwargs.pop("timeout", self.request_timeout)
        resp = self.session.get(url, timeout=timeout, **kwargs)
        resp.raise_for_status()
        return resp

    def _get_json(self, url: str, **kwargs) -> Any:
        """GET JSON from an API endpoint."""
        resp = self._get(url, **kwargs)
        return resp.json()

    def _find_isin(self, text: str) -> Optional[str]:
        """Extract ISIN from text. ISIN format: 2 letters + 10 alphanumeric."""
        match = re.search(r'\b([A-Z]{2}[A-Z0-9]{10})\b', text)
        return match.group(1) if match else None

    def _clean_html(self, text: str) -> str:
        """Strip HTML tags and entities."""
        import html as _html
        text = re.sub(r'<[^>]+>', ' ', text)
        text = _html.unescape(text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text

    def _parse_currency(self, text: str) -> str:
        """Normalize currency codes."""
        text = text.strip().upper()
        mapping = {
            "RMB": "CNH",
            "RENMINBI": "CNH",
            "HK$": "HKD",
            "HKD": "HKD",
            "US$": "USD",
            "USD": "USD",
            "EUR": "EUR",
            "GBP": "GBP",
            "JPY": "JPY",
            "AUD": "AUD",
            "SGD": "SGD",
        }
        return mapping.get(text, text)

    def _parse_pct(self, text: str) -> Optional[float]:
        """Parse a percentage string like '1.50%' or '0.015' → 1.5."""
        text = text.strip().replace('%', '').replace(',', '')
        try:
            val = float(text)
            return val if val <= 1 else val  # Already in percentage
        except ValueError:
            return None

    def _parse_date(self, text: str) -> Optional[str]:
        """Parse date string to YYYY-MM-DD. Handles common formats."""
        text = text.strip()
        formats = [
            "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y",
            "%d.%m.%Y",  # European: DD.MM.YYYY (Schroders)
            "%d-%b-%Y", "%d %b %Y", "%d %B %Y",
            "%b %d, %Y", "%B %d, %Y",
            "%Y/%m/%d",
        ]
        for fmt in formats:
            try:
                return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
        return None

    def _parse_amount(self, text: str) -> Optional[float]:
        """Parse a monetary amount like 'USD 1,234.56' or 'HK$100M'."""
        text = text.strip()
        # Remove currency prefixes
        text = re.sub(r'^(USD|HKD|EUR|GBP|JPY|CNH|CNY|AUD|SGD)\s*', '', text, flags=re.IGNORECASE)
        text = text.replace('HK$', '').replace('US$', '').replace('$', '').replace('€', '').replace('£', '')
        text = text.replace(',', '').strip()

        # Handle M/B suffixes
        multiplier = 1
        if text.upper().endswith('M'):
            multiplier = 1_000_000
            text = text[:-1]
        elif text.upper().endswith('B'):
            multiplier = 1_000_000_000
            text = text[:-1]
        elif text.upper().endswith('K'):
            multiplier = 1_000
            text = text[:-1]

        try:
            return float(text) * multiplier
        except ValueError:
            return None

    # ── AUM Collection ───────────────────────────────────────

    def get_manager_aum(self) -> Optional[Dict[str, Any]]:
        """Get manager AUM from the manager's website.

        Default implementation: visits the manager's base URL or About page,
        regex-extracts AUM from page text.

        Override in connector subclass for site-specific extraction.

        Returns: {aum, aum_currency, aum_date, aum_source, aum_raw_text}
                 or None if not found.
        """
        if not self.base_url:
            return None

        # Common AUM patterns ordered by reliability
        # Group 1: amount (e.g. "10.5"), Group 2: scale (trillion/billion/million)
        # Group 3 (optional): currency prefix
        aum_patterns = [
            # "US$10.5 trillion" / "$500 billion" / "€850 million"
            r'(?:US\$|HK\$|USD|HKD|EUR|GBP|CNY|RMB)\s*([\d,.]+)\s*(trillion|billion|million)',
            # "$1.2 trillion in assets under management"
            r'\$\s*([\d,.]+)\s*(trillion|billion|million)',
            # "10.5 trillion AUM" / "AUM of $500 billion"
            r'AUM\s+(?:of\s+)?\$?\s*([\d,.]+)\s*(trillion|billion|million)',
            # "assets under management (of)? US$10.5 trillion"
            r'assets?\s+under\s+management\s+(?:of\s+)?(?:US\$|USD|HK\$|HKD|\$|€|£)?\s*([\d,.]+)\s*(trillion|billion|million)',
            # "managing (over)? $500 billion"
            r'manag(?:es?|ing)\s+(?:over\s+)?\$?\s*([\d,.]+)\s*(trillion|billion|million)',
            # Generic: number followed by trillion/billion/million near AUM context
            r'([\d,.]+)\s*(trillion|billion|million)\s+(?:in\s+)?(?:AUM|assets?\s+under\s+management|total\s+assets)',
        ]

        urls_to_try = [
            self.base_url,
            self.base_url.rstrip('/') + '/about',
            self.base_url.rstrip('/') + '/en/about',
            self.base_url.rstrip('/') + '/en/about-us',
            self.base_url.rstrip('/') + '/about-us',
            self.base_url.rstrip('/') + '/en/who-we-are',
            self.base_url.rstrip('/') + '/who-we-are',
        ]

        for url in urls_to_try[:3]:  # Limit to 3 attempts
            try:
                resp = self._get(url, timeout=15)
                text = resp.text

                # Strip HTML tags for cleaner matching
                text_clean = self._clean_html(text)
                text_lower = text_clean.lower()

                # Find the paragraph/section most likely containing AUM
                # Look for key phrases and extract the surrounding context
                context_keywords = [
                    'assets under management', 'aum', 'total assets',
                    'manages', 'managing', 'assets managed',
                    '资产管理规模', '管理资产',
                ]
                best_match = None

                for pattern in aum_patterns:
                    match = re.search(pattern, text_clean, re.IGNORECASE)
                    if match:
                        amount_str = match.group(1).replace(',', '')
                        scale_str = match.group(2).lower()

                        try:
                            amount = float(amount_str)
                        except ValueError:
                            continue

                        # Normalize to actual dollar amount
                        if scale_str == 'trillion':
                            amount *= 1_000_000_000_000
                        elif scale_str == 'billion':
                            amount *= 1_000_000_000
                        elif scale_str == 'million':
                            amount *= 1_000_000

                        # Determine currency from context
                        currency = 'USD'
                        full_match = match.group(0).upper()
                        if 'HK$' in full_match or 'HKD' in full_match:
                            currency = 'HKD'
                        elif '€' in full_match or 'EUR' in full_match:
                            currency = 'EUR'
                        elif '£' in full_match or 'GBP' in full_match:
                            currency = 'GBP'
                        elif 'RMB' in full_match or 'CNY' in full_match:
                            currency = 'CNH'

                        # Convert HKD to USD (approximate)
                        if currency == 'HKD':
                            amount = amount / 7.8

                        best_match = {
                            "aum": amount,
                            "aum_currency": currency,
                            "aum_date": datetime.now().strftime("%Y-%m-%d"),
                            "aum_source": f"manager_website:{url}",
                            "aum_raw_text": match.group(0).strip()[:200],
                        }
                        break

                if best_match:
                    return best_match

            except Exception:
                continue

        return None

    def _collect_manager_aum(self, conn, ce_number: str, manager_id: int) -> bool:
        """Collect and store AUM for a manager. Returns True if AUM was stored."""
        from hk_funds.storage import upsert_manager_aum

        try:
            aum_data = self.get_manager_aum()
            if aum_data:
                aum_data["aum_source"] = (
                    aum_data.get("aum_source", "")
                    + f"|ce:{ce_number}|connector:{self.__class__.__name__}"
                )
                return upsert_manager_aum(conn, manager_id, aum_data)
        except Exception as e:
            logger.warning(
                f"[{self.__class__.__name__}] AUM collection failed for "
                f"CE {ce_number}: {e}"
            )

        return False

    def _discover_kfs_pdf(self, fund_name: str = "",
                          product_url: str = "") -> Optional[str]:
        """Try to discover the KFS PDF URL for a fund.

        Default: constructs common URL patterns from base_url.
        Override in connector subclass for site-specific KFS discovery.

        Returns: URL string or None.
        """
        if not self.base_url:
            return None

        # Common KFS URL patterns across manager websites
        base = self.base_url.rstrip("/")
        candidates = []

        # Pattern 1: fund-specific path based on product URL
        if product_url:
            candidates.append(product_url)
            # Some sites have /documents or /kfs appended
            candidates.append(f"{product_url}/documents")
            candidates.append(f"{product_url}/kfs")

        # Pattern 2: shared documents directory
        for path in [
            "/documents/kfs",
            "/literature/kfs",
            "/fund-documents",
            "/en/documents/kfs",
            "/en/fund-documents",
            "/en/literature/kfs",
        ]:
            candidates.append(f"{base}{path}")

        return None  # Default: no automatic discovery

    # ── Orchestration ─────────────────────────────────────────

    def scrape_and_store(self, conn, date_str: str = None) -> Dict[str, int]:
        """Run full scrape cycle for this manager: fund list → details → NAV.

        Returns: {funds_found, isins_updated, navs_stored, details_updated}
        """
        from hk_funds.storage import (
            upsert_nav_history,
            update_fund_from_manager,
        )

        today = date_str or datetime.now().strftime("%Y-%m-%d")
        stats = {"funds_found": 0, "isins_updated": 0, "navs_stored": 0, "details_updated": 0}

        try:
            funds = self.get_fund_list()
            stats["funds_found"] = len(funds)
            logger.info(f"[{self.__class__.__name__}] Found {len(funds)} funds")

            # Match each fund to hk_funds by ISIN
            for fund_data in funds:
                isin = fund_data.get("isin")
                if not isin:
                    continue

                # Find matching fund in database
                row = conn.execute(
                    "SELECT id FROM hk_funds WHERE isin = ?",
                    [isin]
                ).fetchone()

                fund_id = row[0] if row else None

                if fund_id:
                    # Update with ISIN-linked data
                    if update_fund_from_manager(conn, fund_id, fund_data):
                        stats["isins_updated"] += 1

                    # Try to get latest NAV
                    nav = self.get_fund_nav(isin)
                    if nav:
                        n = upsert_nav_history(conn, fund_id, [nav])
                        stats["navs_stored"] += n

                    # Try to get NAV history (if connector supports it)
                    nav_history = self.get_fund_nav_history(isin)
                    if nav_history:
                        n = upsert_nav_history(conn, fund_id, nav_history)
                        stats["navs_stored"] += n

                    # Try to get details
                    details = self.get_fund_details(isin)
                    if details:
                        if update_fund_from_manager(conn, fund_id, details):
                            stats["details_updated"] += 1
                else:
                    # Try fuzzy match by fund name
                    fund_name = fund_data.get("fund_name", "").strip()
                    if fund_name:
                        row = conn.execute(
                            """SELECT id FROM hk_funds
                               WHERE fund_name_en LIKE ?
                               LIMIT 1""",
                            [f"%{fund_name}%"]
                        ).fetchone()
                        if row:
                            fund_id = row[0]
                            if update_fund_from_manager(conn, fund_id, fund_data):
                                stats["isins_updated"] += 1

            logger.info(
                f"[{self.__class__.__name__}] Done: "
                f"ISINs updated={stats['isins_updated']}, "
                f"NAVs={stats['navs_stored']}, "
                f"Details={stats['details_updated']}"
            )

        except Exception as e:
            logger.error(f"[{self.__class__.__name__}] Scrape failed: {e}")

        return stats
