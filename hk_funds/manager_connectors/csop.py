"""
CSOP Asset Management (南方东英) connector.

CSOP is the largest China-focused ETF manager in Hong Kong.
Data sources:
  1. HKEX ListOfSecurities.xlsx — ISINs for all listed ETFs (primary)
  2. csopasset.com — Fund details (blocked by Cloudflare, needs workaround)
  3. SFC product list — authorization details

CE numbers: AVL789 (CSOP Asset Management Limited)
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

from hk_funds.manager_connectors.base import BaseManagerConnector, register_connector

logger = logging.getLogger("hk_funds.manager_connectors.csop")

CSOP_HKEX_ETF_CODES = {
    # Stock Code → ISIN mapping for CSOP managed ETFs listed on HKEX
    "02802": "HK0001226486",  # A CSOP HSCEICC
    "02822": "HK0000127412",  # CSOP A50 ETF
    "02830": "HK0000952678",  # CSOP SAUDI
    "03003": "HK0000805785",  # CSOP MSCI A 50
    "03004": "HK0000873700",  # CSOP VIETNAM30
    "03005": "HK0000578549",  # X CSOPCSI500
    "03030": "HK0001286282",  # CSOP GOLD
    "03033": "HK0000642568",  # CSOP HS TECH
    "03034": "HK0000825338",  # CSOP NASDAQ100
    "03037": "HK0000720745",  # CSOP HSI ETF
    "03053": "HK0000426384",  # A CSOP HKD MM
    "03066": "HK0000900347",  # FA CSOP BTC
    "03068": "HK0000900354",  # FA CSOP ETH
    "03096": "HK0000473303",  # A CSOP USD MM
    "03101": "HK0001246823",  # CSOP A500
    "03109": "HK0000702735",  # CSOP STAR 50
    "03121": "HK0001309035",  # CSOP KOSPI
    "03122": "HK0000226149",  # A CSOP RMB MM
    "03133": "HK0001005203",  # CSOP CSI300
    "03134": "HK0000736774",  # CSOP CSI PV
    "03147": "HK0000248234",  # X CSOPCHINEXT
    "03153": "HK0000986254",  # CSOP NIKKEI225
    "03167": "HK0000316767",  # ICBCCSOPCHINA
    "03174": "HK0000756202",  # CSOP HSBIOTECH
    "03193": "HK0000656949",  # CSOP CSI 5G
    "03199": "HK0000182987",  # ICBCCSOP CGPB
    "03431": "HK0001197646",  # CSOP HKKRTECH
    "03432": "HK0001033544",  # CSOP HKCNCON
    "03433": "HK0000981024",  # CSOP UST20
    "03441": "HK0001127072",  # CSOP EWSELECT
    "03442": "HK0001127064",  # CSOP HKUSTECH
    "03443": "HK0001144549",  # CSOP HK EQUITY
    "03447": "HK0001145058",  # CSOP AP REITS
    "03454": "HK0001066643",  # CSOP MAG7
    "03469": "HK0001197653",  # CSOP SCHIGHDIV
    "03473": "HK0001275350",  # CSOP ASIA TECH
    "03535": "HK0001281473",  # CSOP HKJPCF
    # L&I Products
    "07200": "HK0000330149",  # FL2 CSOP HSI
    "07226": "HK0000672946",  # XL2CSOPHSTECH
    "07233": "HK0000629656",  # XL2CSOPCSI300
    "07262": "HK0001014262",  # FL2 CSOP NIKKEI
    "07266": "HK0000604659",  # FL2CSOPNASDAQ
    "07288": "HK0000330164",  # FL2 CSOP HSCEI
    "07299": "HK0000612744",  # FL2CSOPGOLD
    "07300": "HK0000330156",  # FI CSOP HSI
    "07311": "HK0001121265",  # XI2CSOPCOIN
    "07347": "HK0001121364",  # XI2CSOPSMSN
    "07366": "HK0001121224",  # XI2CSOPTSLA
    "07376": "HK0001028825",  # FI CSOP BTC
    "07388": "HK0001121182",  # XI2CSOPNVDA
    "07399": "HK0001121307",  # XI2CSOPMSTR
    "07500": "HK0000503190",  # FI2 CSOP HSI
    "07515": "HK0001014270",  # FI2 CSOP NIKKEI
    "07552": "HK0000672953",  # XI2CSOPHSTECH
    "07568": "HK0000528882",  # FI2CSOPNASDAQ
    "07588": "HK0000330172",  # FI2 CSOP HSCEI
    "07709": "HK0001205258",  # XL2CSOPHYNIX
    "07711": "HK0001121240",  # XL2CSOPCOIN
    "07747": "HK0001121349",  # XL2CSOPSMSN
    "07766": "HK0001121208",  # XL2CSOPTSLA
    "07777": "HK0001121323",  # XL2CSOPBRKB
    "07788": "HK0001121166",  # XL2CSOPNVDA
    "07799": "HK0001121281",  # XL2CSOPMSTR
    # RMB counter shares
    "82822": "HK0000127412",  # CSOP A50 ETF-R
    "82830": "HK0000952678",  # CSOP SAUDI-R
    "83005": "HK0000578549",  # X CSOPCSI500-R
    "83053": "HK0000426384",  # A CSOP HKD MM-R
    "83122": "HK0000226149",  # A CSOP RMB MM-R
    "83147": "HK0000248234",  # X CSOPCHINEXT-R
    "83167": "HK0000316767",  # ICBCCSOPCHINA-R
    "83199": "HK0000182987",  # ICBCCSOP CGPB-R
    # USD counter shares
    "09096": "HK0000473303",  # A CSOP USD MM-U
    "09167": "HK0000316767",  # ICBCCSOPCHINA-U
    "09311": "HK0001121265",  # XI2CSOPCOIN-U
    "09347": "HK0001121364",  # XI2CSOPSMSN-U
    "09366": "HK0001121224",  # XI2CSOPTSLA-U
    "09388": "HK0001121182",  # XI2CSOPNVDA-U
    "09399": "HK0001121307",  # XI2CSOPMSTR-U
    "09711": "HK0001121240",  # XL2CSOPCOIN-U
    "09747": "HK0001121349",  # XL2CSOPSMSN-U
    "09766": "HK0001121208",  # XL2CSOPTSLA-U
    "09777": "HK0001121323",  # XL2CSOPBRKB-U
    "09788": "HK0001121166",  # XL2CSOPNVDA-U
    "09799": "HK0001121281",  # XL2CSOPMSTR-U
}

# Deduplicate ISINs (multiple share classes share the same ISIN)
CSOP_UNIQUE_ISINS = list(set(CSOP_HKEX_ETF_CODES.values()))


@register_connector
class CSOPConnector(BaseManagerConnector):
    """CSOP Asset Management Limited connector.

    Primary data source: HKEX ListOfSecurities.xlsx for ETF ISINs.
    """

    manager_ce_numbers = ["ARN075"]
    base_url = "https://www.csopasset.com"

    def get_fund_list(self) -> List[Dict[str, Any]]:
        """Return CSOP ETFs from HKEX data with ISINs.

        CSOP's website (csopasset.com) is protected by Cloudflare WAF.
        HKEX provides ISINs for all listed ETFs, which serves as the
        primary data source until JS rendering is set up.
        """
        funds = []
        for stock_code, isin in CSOP_HKEX_ETF_CODES.items():
            # Determine fund type from name
            lic = int(stock_code)
            if 7200 <= lic <= 7799 or 7300 <= lic <= 7399:
                fund_type = "leveraged_inverse_product"
            elif lic >= 3000:
                fund_type = "etf"
            else:
                fund_type = "etf"

            funds.append({
                "stock_code": stock_code,
                "isin": isin,
                "fund_type": fund_type,
                "currency": "HKD",  # Primary listing currency
                "exchange": "HKEX",
                "source": "hkex_list",
            })
        return funds

    def get_fund_nav(self, isin: str) -> Optional[Dict[str, Any]]:
        """Try to get NAV from CSOP's website.

        Currently blocked by Cloudflare. Will need browser automation
        or API key to access fund price data.
        """
        return None

    def get_fund_details(self, isin: str) -> Optional[Dict[str, Any]]:
        """Try to get fund details from CSOP's website.

        Currently blocked by Cloudflare. ETF details (TER, benchmark,
        inception date) are available from HKEX and CSOP product pages.
        """
        return None
