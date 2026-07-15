"""
ICBC Asset Management ETF connector.

ICBC is the world's largest bank by assets. Their Hong Kong ETFs
cover China government bonds and money market instruments.

CE: TBD (will be populated from webb scraper)
Website: https://www.icbcam.com.hk
"""

from __future__ import annotations

from typing import Any, Dict, List

from hk_funds.manager_connectors.base import register_connector
from hk_funds.manager_connectors.hkex_etf import HKEXETFConnector


@register_connector
class ICBCConnector(HKEXETFConnector):
    """ICBC — 5 HKEX-listed ETFs."""

    manager_ce_numbers = ["AAY077"]  # ICBC: populate after webb scraper completes
    base_url = "https://www.icbcam.com.hk"
    issuer_name = "ICBC"

    def get_fund_list(self) -> List[Dict[str, Any]]:
        all_funds = super().get_fund_list()
        funds = [f for f in all_funds if f.get("issuer", "") == self.issuer_name]
        for f in funds:
            name = f.get("fund_name", "")
            if "Money Market" in name or "MM" in name.split():
                f["fund_type"] = "money_market_etf"
            else:
                f["fund_type"] = "etf"
            f["manager_name"] = "ICBC Asset Management (Global) Company Limited"
        return funds
