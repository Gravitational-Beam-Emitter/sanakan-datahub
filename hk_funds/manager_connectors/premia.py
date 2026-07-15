"""
Premia Partners ETF connector.

Premia is a Hong Kong-based ETF issuer specializing in smart beta
and thematic ETFs covering Asia markets, US Treasuries, and REITs.

CE: TBD (will be populated from webb scraper)
Website: https://www.premia-partners.com
"""

from __future__ import annotations

from typing import Any, Dict, List

from hk_funds.manager_connectors.base import register_connector
from hk_funds.manager_connectors.hkex_etf import HKEXETFConnector


@register_connector
class PremiaPartnersConnector(HKEXETFConnector):
    """Premia Partners — 14 HKEX-listed ETFs."""

    manager_ce_numbers = ["BIN676"]  # Premia: populate after webb scraper completes
    base_url = "https://www.premia-partners.com"
    issuer_name = "Premia Partners"

    def get_fund_list(self) -> List[Dict[str, Any]]:
        all_funds = super().get_fund_list()
        funds = [f for f in all_funds if f.get("issuer", "") == self.issuer_name]
        for f in funds:
            f["fund_type"] = "etf"
            f["manager_name"] = "Premia Partners Company Limited"
        return funds
