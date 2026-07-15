"""
E Fund Management ETF connector.

E Fund is one of China's top 3 fund managers by AUM. Their HKEX-listed
ETFs cover China equities, fixed income, and money market products.

CE: TBD (will be populated from webb scraper)
Website: https://www.efunds.com.hk
"""

from __future__ import annotations

from typing import Any, Dict, List

from hk_funds.manager_connectors.base import register_connector
from hk_funds.manager_connectors.hkex_etf import HKEXETFConnector


@register_connector
class EFundConnector(HKEXETFConnector):
    """E Fund — 19 HKEX-listed ETFs."""

    manager_ce_numbers = ["ARO593"]  # E Fund: populate after webb scraper completes
    base_url = "https://www.efunds.com.hk"
    issuer_name = "E Fund"

    def get_fund_list(self) -> List[Dict[str, Any]]:
        all_funds = super().get_fund_list()
        funds = [f for f in all_funds if f.get("issuer", "") == self.issuer_name]
        for f in funds:
            f["fund_type"] = "etf"
            f["manager_name"] = "E Fund Management (Hong Kong) Co., Limited"
        return funds
