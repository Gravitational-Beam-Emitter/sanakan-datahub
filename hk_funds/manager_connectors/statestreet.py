"""
State Street / SPDR ETF connector.

SPDR (Standard & Poor's Depositary Receipts) is State Street's ETF brand,
the original ETF pioneer with the first US-listed ETF (SPY, 1993).

CE: TBD (will be populated from webb scraper)
Website: https://www.ssga.com/hk
"""

from __future__ import annotations

from typing import Any, Dict, List

from hk_funds.manager_connectors.base import register_connector
from hk_funds.manager_connectors.hkex_etf import HKEXETFConnector


@register_connector
class StateStreetConnector(HKEXETFConnector):
    """State Street / SPDR — 3 HKEX-listed ETFs (Gold, S&P 500, MSCI EM)."""

    manager_ce_numbers = ["AEI343"]  # State Street: populate after webb scraper completes
    base_url = "https://www.ssga.com/hk"
    issuer_name = "State Street / SPDR"

    def get_fund_list(self) -> List[Dict[str, Any]]:
        all_funds = super().get_fund_list()
        funds = [f for f in all_funds if f.get("issuer", "") == self.issuer_name]
        for f in funds:
            name = f.get("fund_name", "")
            if "GOLD" in name.upper():
                f["fund_type"] = "digital_asset_etf"
            else:
                f["fund_type"] = "etf"
            f["manager_name"] = "State Street Global Advisors (Hong Kong) Limited"
        return funds
