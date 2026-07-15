"""
Harvest Global Investments ETF connector.

Harvest Global is one of the first Chinese fund managers to establish
a Hong Kong presence. ETFs cover China A-shares, bonds, and ESG themes.

CE: AZC907 — Harvest Global Investments Limited
Website: https://www.harvestglobal.com.hk
"""

from __future__ import annotations

from typing import Any, Dict, List

from hk_funds.manager_connectors.base import register_connector
from hk_funds.manager_connectors.hkex_etf import HKEXETFConnector


@register_connector
class HarvestGlobalConnector(HKEXETFConnector):
    """Harvest Global — 9 HKEX-listed ETFs."""

    manager_ce_numbers = ["ASE565"]
    base_url = "https://www.harvestglobal.com.hk"
    issuer_name = "Harvest Global"

    def get_fund_list(self) -> List[Dict[str, Any]]:
        all_funds = super().get_fund_list()
        funds = [f for f in all_funds if f.get("issuer", "") == self.issuer_name]
        for f in funds:
            f["fund_type"] = "etf"
            f["manager_name"] = "Harvest Global Investments Limited"
            f["ce_number"] = self.manager_ce_numbers[0]
        return funds
