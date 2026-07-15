"""
Bosera Asset Management ETF connector.

Bosera is one of China's largest fund managers. Their HKEX-listed ETFs
include China bond ETFs and the Bosera FTSE China A50 Index ETF.

CE: TBD (will be populated from webb scraper)
Website: https://www.bosera.com.hk
"""

from __future__ import annotations

from typing import Any, Dict, List

from hk_funds.manager_connectors.base import register_connector
from hk_funds.manager_connectors.hkex_etf import HKEXETFConnector


@register_connector
class BoseraConnector(HKEXETFConnector):
    """Bosera — 19 HKEX-listed ETFs."""

    manager_ce_numbers = ["AVR135"]  # Bosera: populate after webb scraper completes
    base_url = "https://www.bosera.com.hk"
    issuer_name = "Bosera"

    def get_fund_list(self) -> List[Dict[str, Any]]:
        all_funds = super().get_fund_list()
        funds = [f for f in all_funds if f.get("issuer", "") == self.issuer_name]
        for f in funds:
            f["fund_type"] = "etf"
            f["manager_name"] = "Bosera Asset Management (International) Co., Limited"
        return funds
