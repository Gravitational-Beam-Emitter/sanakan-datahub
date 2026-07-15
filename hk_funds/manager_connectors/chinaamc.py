"""
China Asset Management (Hong Kong) Limited (华夏基金香港) connector.

One of the largest Chinese asset managers with a significant HK presence.
Data sources:
  1. HKEX ListOfSecurities.xlsx — ISINs for all listed ETFs (primary)
  2. chinaamc.com.hk — Fund details (Vue.js SPA, needs JS rendering)
  3. chinaamc.com — Mainland site with fund trading API

CE numbers: ARS988 (China Asset Management (Hong Kong) Limited)
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

from hk_funds.manager_connectors.base import BaseManagerConnector, register_connector

logger = logging.getLogger("hk_funds.manager_connectors.chinaamc")

CHINAAMC_HKEX_ETF_CODES = {
    # Stock Code → ISIN mapping for ChinaAMC managed ETFs listed on HKEX
    "02839": "HK0000804119",  # CAM MSCI A50
    "03042": "HK0001009700",  # CAM BTC
    "03046": "HK0001009734",  # CAM ETH
    "03069": "HK0000711199",  # CAM HSBIOTECH
    "03086": "HK0000280989",  # CAM NASDAQ100
    "03088": "HK0000643327",  # CAM HS TECH
    "03140": "HK0001233946",  # CAM HKUS AI
    "03141": "HK0000221389",  # CAM ASIA IGB
    "03145": "HK0000221405",  # CAM ASIA HIGHDV
    "03146": "HK0001006771",  # CAM 20 UST
    "03160": "HK0000280997",  # CAM JAPAN HDG
    "03161": "HK0000921822",  # A CAM RMB MM
    "03165": "HK0000281003",  # CAM EU QLTY HDG
    "03188": "HK0000123577",  # CAM CSI300
    "03403": "HK0000873676",  # CAM HSI ESG
    "03404": "HK0001040226",  # CAM MSCIINDIA
    "03418": "HK0001279816",  # CAM DIGI GOLD
    "03460": "HK0001198149",  # CAM SOL
    "03461": "HK0001224952",  # A CAM RMBTMMF
    "03471": "HK0001224937",  # A CAM HKDTMMF
    "03472": "HK0001224945",  # A CAM USDTMMF
    # USD counter shares
    "09042": "HK0001009700",  # CAM BTC-U
    "09046": "HK0001009734",  # CAM ETH-U
    "09069": "HK0000711199",  # CAM HSBIOTECH-U
    "09086": "HK0000280989",  # CAM NASDAQ100-U
    "09088": "HK0000643327",  # CAM HS TECH-U
    "09140": "HK0001233946",  # CAM HKUS AI-U
    "09141": "HK0000221389",  # CAM ASIA IGB-U
    "09146": "HK0001006771",  # CAM 20 UST-U
    "09188": "HK0000123577",  # CAM CSI300-U
    "09403": "HK0000873676",  # CAM HSI ESG-U
    "09404": "HK0001040226",  # CAM MSCIINDIA-U
    "09418": "HK0001279816",  # CAM DIGI GOLD-U
    "09446": "HK0001006805",  # CAM 20 UST A-U
    "09460": "HK0001198149",  # CAM SOL-U
    "09472": "HK0001224945",  # A CAM USDTMMF-U
    "09839": "HK0000804119",  # CAM MSCI A50-U
    # RMB counter shares
    "82839": "HK0000804119",  # CAM MSCI A50-R
    "83042": "HK0001009700",  # CAM BTC-R
    "83046": "HK0001009734",  # CAM ETH-R
    "83069": "HK0000711199",  # CAM HSBIOTECH-R
    "83088": "HK0000643327",  # CAM HS TECH-R
    "83140": "HK0001233946",  # CAM HKUS AI-R
    "83146": "HK0001006771",  # CAM 20 UST-R
    "83161": "HK0000921822",  # A CAM RMB MM-R
    "83188": "HK0000123577",  # CAM CSI300-R
    "83403": "HK0000873676",  # CAM HSI ESG-R
    "83404": "HK0001040226",  # CAM MSCIINDIA-R
    "83418": "HK0001279816",  # CAM DIGI GOLD-R
    "83460": "HK0001198149",  # CAM SOL-R
    "83461": "HK0001224952",  # A CAM RMBTMMF-R
}

CHINAAMC_UNIQUE_ISINS = list(set(CHINAAMC_HKEX_ETF_CODES.values()))


@register_connector
class ChinaAMCConnector(BaseManagerConnector):
    """China Asset Management (Hong Kong) Limited connector.

    Primary data source: HKEX ListOfSecurities.xlsx for ETF ISINs.
    The chinaamc.com.hk website is a Vue.js SPA — needs JS rendering
    to extract fund NAVs and details.
    """

    manager_ce_numbers = ["ARS988"]
    base_url = "https://www.chinaamc.com.hk"

    def get_fund_list(self) -> List[Dict[str, Any]]:
        """Return ChinaAMC ETFs from HKEX data with ISINs.

        The chinaamc.com.hk website is a Vue.js SPA that requires
        JavaScript rendering. HKEX provides ISINs for all listed ETFs.
        """
        funds = []
        for stock_code, isin in CHINAAMC_HKEX_ETF_CODES.items():
            fund_type = "etf"
            # Money market funds
            lic = int(stock_code)
            if lic in (3161, 3461, 3471, 3472, 83161, 83461):
                fund_type = "money_market_etf"
            elif lic in (3042, 3046, 3418, 9042, 9046, 9418, 83042, 83046, 83418):
                fund_type = "digital_asset_etf"  # BTC/ETH/Digital Gold

            funds.append({
                "stock_code": stock_code,
                "isin": isin,
                "fund_type": fund_type,
                "currency": "HKD",
                "exchange": "HKEX",
                "source": "hkex_list",
                "fund_manager_name_en": "China Asset Management (Hong Kong) Limited",
            })
        return funds

    def get_fund_nav(self, isin: str) -> Optional[Dict[str, Any]]:
        """Try to get NAV from ChinaAMC's website.

        The HK site is a Vue.js SPA. The mainland site (chinaamc.com)
        has a fund trading API that could be used.
        """
        return None

    def get_fund_details(self, isin: str) -> Optional[Dict[str, Any]]:
        """Try to get fund details. Reserved for JS rendering setup."""
        return None
