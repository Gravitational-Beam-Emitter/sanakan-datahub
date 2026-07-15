"""
Mirae Asset / Global X ETF connector.

Mirae Asset acquired Global X ETFs in 2018. Their HKEX-listed products
include the popular Global X China series (GX prefix stock codes).

CE: AZG766 — Mirae Asset Global Investments (Hong Kong) Limited
Website: https://www.am.miraeasset.com.hk (SPA, likely WAF-protected)
"""

from __future__ import annotations

from typing import Any, Dict, List

from hk_funds.manager_connectors.base import BaseManagerConnector, register_connector
from hk_funds.manager_connectors.hkex_etf import HKEXETFConnector


@register_connector
class MiraeAssetConnector(HKEXETFConnector):
    """Mirae Asset / Global X — 63 HKEX-listed ETFs and L&I products."""

    manager_ce_numbers = ["BJB333", "ALK083"]
    base_url = "https://www.am.miraeasset.com.hk"
    issuer_name = "Mirae Asset / Global X"

    def get_fund_list(self) -> List[Dict[str, Any]]:
        """Return Mirae Asset ETFs from HKEX data."""
        all_funds = super().get_fund_list()
        funds = [f for f in all_funds if f.get("issuer", "") == self.issuer_name]

        # Enrich with manager-specific metadata
        for f in funds:
            name = f.get("fund_name", "")
            # Classify more precisely
            if "Leveraged" in name or "Inverse" in name:
                f["fund_type"] = "leveraged_inverse_product"
            elif any(tag in name.upper() for tag in ["BTC", "ETH", "BITCOIN", "ETHEREUM"]):
                f["fund_type"] = "digital_asset_etf"
            f["manager_name"] = "Mirae Asset Global Investments (Hong Kong) Limited"
            f["ce_number"] = self.manager_ce_numbers[0]

        return funds
