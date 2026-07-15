"""
Samsung Asset Management (Hong Kong) ETF connector.

Samsung AM's Hong Kong ETFs leverage the Samsung brand's technology
expertise, covering Asia tech themes and blockchain/digital assets.

CE: TBD (will be populated from webb scraper)
Website: https://www.samsungetfhk.com
"""

from __future__ import annotations

from typing import Any, Dict, List

from hk_funds.manager_connectors.base import register_connector
from hk_funds.manager_connectors.hkex_etf import HKEXETFConnector


@register_connector
class SamsungAMConnector(HKEXETFConnector):
    """Samsung AM — 9 HKEX-listed ETFs."""

    manager_ce_numbers = ["AQG442"]  # Samsung AM: populate after webb scraper completes
    base_url = "https://www.samsungetfhk.com"
    issuer_name = "Samsung AM"

    def get_fund_list(self) -> List[Dict[str, Any]]:
        all_funds = super().get_fund_list()
        funds = [f for f in all_funds if f.get("issuer", "") == self.issuer_name]
        for f in funds:
            name = f.get("fund_name", "")
            if "BTC" in name.upper() or "BITCOIN" in name.upper():
                f["fund_type"] = "digital_asset_etf"
            f["manager_name"] = "Samsung Asset Management (Hong Kong) Limited"
        return funds
