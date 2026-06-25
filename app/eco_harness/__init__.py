"""
Eco Data API Harness — unified macroeconomic data access layer.

Provides a single entry point for all major economic data sources:
  us/       FRED + US Treasury
  cn/       AKShare (China mainland)
  hk/       AKShare (Hong Kong)
  global/   World Bank + DBnomics
  sdmx/     OECD + ECB (via opensdmx)
  jp/       Bank of Japan + AKShare (Japan)
  euro/     AKShare (Eurozone)
  uk/       AKShare (United Kingdom)
  de/       AKShare (Germany)
  au/       AKShare (Australia)
  ca/       AKShare (Canada)
  ch/       AKShare (Switzerland)
  shipping/ AKShare (Baltic freight indices)
  banks/    AKShare (central bank policy rates)
  energy/   EIA

Usage:
    from app.eco_harness import EcoHarness
    eh = EcoHarness(fred_api_key='...')
    df = eh.us.gdp()
    df = eh.cn.cpi()
    df = eh.global.gdp('CHN')
"""

from app.categories import DataCategory, get_category, sources_by_category
from app.eco_harness.us import USHarness
from app.eco_harness.cn import CNHarness
from app.eco_harness.hk import HKHarness
from app.eco_harness.bond import BondHarness, FuturesHarness
from app.eco_harness.global_ import GlobalHarness
from app.eco_harness.jp import JPHarness
from app.eco_harness.energy import EnergyHarness
from app.eco_harness.euro import EuroHarness
from app.eco_harness.uk import UKHarness
from app.eco_harness.germany import GermanyHarness
from app.eco_harness.au import AUHarness
from app.eco_harness.ca import CAHarness
from app.eco_harness.ch import CHHarness
from app.eco_harness.shipping import ShippingHarness
from app.eco_harness.banks import BankRateHarness
from app.eco_harness.alternative import AlternativeHarness
from app.eco_harness.llm_metrics import LLMMetricsHarness
from app.eco_harness.defi_metrics import DeFiMetricsHarness
from app.eco_harness.ai_infra import AIInfraHarness
from app.eco_harness.ai_companies import AICompaniesHarness
from app.eco_harness.aml_ratings import AMLRatingsHarness
from app.eco_harness.sanctions import SanctionsHarness
from app.eco_harness.name_screening import NameScreeningHarness
from app.eco_harness.concept_boards import ConceptBoardHarness
from app.eco_harness.opticals import OpticalsHarness

try:
    from app.eco_harness.sdmx import SDMXHarness
    _HAS_SDMX = True
except ImportError:
    _HAS_SDMX = False


class _CategoryView:
    """Proxy that groups harness sub-sources under a category namespace.

    e.g. eh.macro.us.gdp() or eh.risk.aml.list_ratings()
    """

    __slots__ = ('_harness', '_source_keys')

    def __init__(self, harness: 'EcoHarness', source_keys: list[str]):
        self._harness = harness
        self._source_keys = source_keys

    def __getattr__(self, name: str):
        if name in self._source_keys:
            attr = "global_" if name == "global_" else name
            if attr == "sdmx":
                return self._harness.sdmx
            return getattr(self._harness, attr)
        raise AttributeError(f"{name} not in this category (available: {self._source_keys})")

    def __dir__(self):
        return self._source_keys

    def __repr__(self):
        return f"CategoryView({self._source_keys})"


class EcoHarness:
    __slots__ = ('us', 'cn', 'hk', 'bond', 'futures', 'global_', 'sdmx',
                 'jp', 'euro', 'uk', 'de', 'au', 'ca', 'ch', 'shipping',
                 'banks', 'alt', 'llm', 'defi', 'energy', 'ai', 'ai_co', 'cb', 'optical', 'aml', 'sanctions', 'name_screening',
                 '_macro_view', '_risk_view')

    def __init__(self, fred_api_key: str = '', eia_api_key: str = ''):
        self.us = USHarness(fred_api_key)
        self.cn = CNHarness()
        self.hk = HKHarness()
        self.bond = BondHarness()
        self.futures = FuturesHarness()
        self.global_ = GlobalHarness()
        self.sdmx = SDMXHarness() if _HAS_SDMX else None
        self.jp = JPHarness()
        self.euro = EuroHarness()
        self.uk = UKHarness()
        self.de = GermanyHarness()
        self.au = AUHarness()
        self.ca = CAHarness()
        self.ch = CHHarness()
        self.shipping = ShippingHarness()
        self.banks = BankRateHarness()
        self.alt = AlternativeHarness()
        self.llm = LLMMetricsHarness()
        self.defi = DeFiMetricsHarness()
        self.energy = EnergyHarness(eia_api_key)
        self.ai = AIInfraHarness(fred_api_key)
        self.ai_co = AICompaniesHarness()
        self.cb = ConceptBoardHarness()
        self.optical = OpticalsHarness()
        self.aml = AMLRatingsHarness()
        self.sanctions = SanctionsHarness()
        self.name_screening = NameScreeningHarness()

        # Category views
        macro_keys = sources_by_category(DataCategory.MACRO)
        if _HAS_SDMX:
            macro_keys.append("sdmx")
        risk_keys = sources_by_category(DataCategory.COUNTRY_RISK) + ["name_screening"]
        self._macro_view = _CategoryView(self, macro_keys)
        self._risk_view = _CategoryView(self, risk_keys)

    @property
    def macro(self) -> _CategoryView:
        """Macroeconomic sources (us, cn, hk, jp, euro, ...) grouped under one namespace."""
        return self._macro_view

    @property
    def risk(self) -> _CategoryView:
        """Country risk + name screening sources (aml, sanctions, name_screening)."""
        return self._risk_view

    @property
    def available_sources(self):
        sources = ['us', 'cn', 'hk', 'global_', 'jp', 'euro', 'uk', 'de',
                   'au', 'ca', 'ch', 'shipping', 'banks', 'alt', 'llm', 'defi', 'energy', 'ai', 'ai_co', 'cb', 'optical', 'aml', 'sanctions', 'name_screening']
        if _HAS_SDMX:
            sources.append('sdmx')
        return [{"key": s, "category": get_category(s).value} for s in sources]

    def __repr__(self):
        n_macro = len(sources_by_category(DataCategory.MACRO))
        n_risk = len(sources_by_category(DataCategory.COUNTRY_RISK))
        status = '+sdmx' if _HAS_SDMX else ''
        return f'EcoHarness(macro×{n_macro}{status}, country_risk×{n_risk}, name_screening)'
