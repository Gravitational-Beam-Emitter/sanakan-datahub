"""
Eco Data SDK — Python clients for all Eco Data API services.

Services:
    EcoDataClient         — port 8000, economic indicators & macro data
    CnStockClient         — port 8001, A-share limit-up review
    UsCorpActionsClient   — port 8002, US corporate actions (SEC 8-K)
    UsListingsClient      — port 8003, US listings & crypto products
    HkFundsClient         — port 8004, HK funds KYP/DD
    AnnouncementsClient   — port 8005, multi-market company announcements
    KrStockClient         — port 8006, Korean stock market (KOSPI/KOSDAQ/KONEX)
    TwStockClient         — port 8007, Taiwan stock market (TWSE/TPEx)

Usage:
    from eco_data_sdk import EcoDataClient, CnStockClient, UsCorpActionsClient
    from eco_data_sdk import UsListingsClient, HkFundsClient, AnnouncementsClient
    from eco_data_sdk import KrStockClient

    eco = EcoDataClient()
    indicators = eco.list_indicators(source="cn")

    cn = CnStockClient()
    review = cn.daily_review("2025-06-20")

    us_corp = UsCorpActionsClient()
    actions = us_corp.list_actions(start="2025-06-01")

    us_list = UsListingsClient()
    ipos = us_list.upcoming_listings()

    hk = HkFundsClient()
    funds = hk.list_funds(domicile="Hong Kong")

    ann = AnnouncementsClient()
    filings = ann.list_announcements(market="us", limit=50)

    kr = KrStockClient()
    kospi = kr.list_listings(market="KOSPI")
"""

from eco_data_sdk.client import EcoDataClient
from eco_data_sdk.cn_stock_client import CnStockClient
from eco_data_sdk.us_corp_actions_client import UsCorpActionsClient
from eco_data_sdk.us_listings_client import UsListingsClient
from eco_data_sdk.hk_funds_client import HkFundsClient
from eco_data_sdk.announcements_client import AnnouncementsClient
from eco_data_sdk.kr_stock_client import KrStockClient
from eco_data_sdk.tw_stock_client import TwStockClient

__all__ = [
    "EcoDataClient",
    "CnStockClient",
    "UsCorpActionsClient",
    "UsListingsClient",
    "HkFundsClient",
    "AnnouncementsClient",
    "KrStockClient",
    "TwStockClient",
]
