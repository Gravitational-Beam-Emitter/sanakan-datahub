"""
Amundi Asset Management ETF connector.

Amundi is Europe's largest asset manager. Their Hong Kong ETFs cover
Asia investment grade bonds and ESG-themed equity strategies.

CE: AQD994 — Amundi Hong Kong Limited
Website: https://www.amundi.com.hk
"""

from __future__ import annotations

from typing import Any, Dict, List

from hk_funds.manager_connectors.base import register_connector
from hk_funds.manager_connectors.hkex_etf import HKEXETFConnector


@register_connector
class AmundiConnector(HKEXETFConnector):
    """Amundi — 4 HKEX-listed ETFs."""

    manager_ce_numbers = ["AAB444"]
    base_url = "https://www.amundi.com.hk"
    issuer_name = "Amundi"

    def get_fund_list(self) -> List[Dict[str, Any]]:
        all_funds = super().get_fund_list()
        funds = [f for f in all_funds if f.get("issuer", "") == self.issuer_name]
        for f in funds:
            f["fund_type"] = "etf"
            f["manager_name"] = "Amundi Hong Kong Limited"
            f["ce_number"] = self.manager_ce_numbers[0]
        return funds
