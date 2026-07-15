"""
Hang Seng Investment Management ETF connector.

Hang Seng IM manages the Tracker Fund of Hong Kong (TraHK, 02800) and
the Hang Seng Index ETF series. They are the largest ETF manager in
Hong Kong by AUM.

CE: AAB421 — Hang Seng Investment Management Limited
Website: https://www.hangseng.com (bank-integrated, complex scraping)
"""

from __future__ import annotations

from typing import Any, Dict, List

from hk_funds.manager_connectors.base import register_connector
from hk_funds.manager_connectors.hkex_etf import HKEXETFConnector


@register_connector
class HangSengIMConnector(HKEXETFConnector):
    """Hang Seng IM — 20 HKEX-listed ETFs including TraHK (02800)."""

    manager_ce_numbers = ["ABT330"]
    base_url = "https://www.hangseng.com"
    issuer_name = "Hang Seng IM"

    def get_fund_list(self) -> List[Dict[str, Any]]:
        all_funds = super().get_fund_list()
        funds = [f for f in all_funds if f.get("issuer", "") == self.issuer_name]
        for f in funds:
            name = f.get("fund_name", "")
            if "Leveraged" in name or "Inverse" in name:
                f["fund_type"] = "leveraged_inverse_product"
            f["manager_name"] = "Hang Seng Investment Management Limited"
            f["ce_number"] = self.manager_ce_numbers[0]
        return funds
