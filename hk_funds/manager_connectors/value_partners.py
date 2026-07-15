"""
Value Partners Group ETF connector.

Value Partners is one of Hong Kong's largest independent asset managers,
known for Greater China equity strategies and the Value Gold ETF (03081).

CE: ABZ681 — Value Partners Hong Kong Limited
Website: https://www.valuepartners.com.hk
"""

from __future__ import annotations

from typing import Any, Dict, List

from hk_funds.manager_connectors.base import register_connector
from hk_funds.manager_connectors.hkex_etf import HKEXETFConnector


@register_connector
class ValuePartnersConnector(HKEXETFConnector):
    """Value Partners — Gold ETF and select strategies."""

    manager_ce_numbers = ["AFJ002"]
    base_url = "https://www.valuepartners.com.hk"
    issuer_name = "Value Partners"

    def get_fund_list(self) -> List[Dict[str, Any]]:
        all_funds = super().get_fund_list()
        funds = [f for f in all_funds if f.get("issuer", "") == self.issuer_name]
        for f in funds:
            name = f.get("fund_name", "")
            if "GOLD" in name.upper():
                f["fund_type"] = "digital_asset_etf"
            else:
                f["fund_type"] = "etf"
            f["manager_name"] = "Value Partners Hong Kong Limited"
            f["ce_number"] = self.manager_ce_numbers[0]
        return funds
