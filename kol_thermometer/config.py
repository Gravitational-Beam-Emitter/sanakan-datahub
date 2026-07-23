"""
Configuration — loads .env and sets DB path, API keys, rate limits, thresholds.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path)

DB_PATH = str(Path(__file__).resolve().parent.parent / "kol_thermometer.duckdb")

# ── LLM API keys ──────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
QWEN_API_KEY = os.environ.get("QWEN_API_KEY", "")

# ── Reddit API (PRAW) ────────────────────────────────────────
REDDIT_CLIENT_ID = os.environ.get("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.environ.get("REDDIT_CLIENT_SECRET", "")
REDDIT_USER_AGENT = os.environ.get("REDDIT_USER_AGENT", "kol-thermometer/1.0")

# ── YouTube Data API v3 ──────────────────────────────────────
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "")

# ── StockTwits API ──────────────────────────────────────────
STOCKTWITS_ACCESS_TOKEN = os.environ.get("STOCKTWITS_ACCESS_TOKEN", "")
STOCKTWITS_SYMBOLS_LIMIT = 100      # top trending symbols to fetch
STOCKTWITS_MESSAGES_PER_SYMBOL = 30  # messages per symbol
STOCKTWITS_RATE_LIMIT = 0.5          # seconds between API calls

# ── Finnhub API ─────────────────────────────────────────────
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "")
FINNHUB_NEWS_LIMIT = 50             # articles per fetch
FINNHUB_SENTIMENT_LIMIT = 30        # symbols for social sentiment
FINNHUB_RATE_LIMIT = 0.5            # seconds between API calls

# ── Playwright scraping ─────────────────────────────────────
PLAYWRIGHT_HEADLESS = os.environ.get("PLAYWRIGHT_HEADLESS", "1") == "1"
PLAYWRIGHT_TIMEOUT = 30000           # ms per page load

# ── Twitter/X scraping ──────────────────────────────────────
TWITTER_SEARCH_QUERIES = [
    "$TSLA", "$AAPL", "$NVDA", "$MSFT", "$AMZN", "$GOOGL",
    "$META", "$AMD", "$PLTR", "$CRWD", "$SMCI", "$COIN",
    "$SPY", "$QQQ", "$IWM", "$DIA",
    "$BTC", "$ETH", "$SOL",
    "$FXI", "$EWJ", "$EWY",
]
TWITTER_TWEETS_PER_QUERY = 30

# ── Weibo scraping ──────────────────────────────────────────
WEIBO_SEARCH_QUERIES = [
    "茅台", "宁德时代", "比亚迪", "中芯国际",
    "AI概念股", "新能源", "半导体", "芯片",
    "港股", "美股", "A股行情",
]
WEIBO_POSTS_PER_QUERY = 20

# ── Seeking Alpha scraping ──────────────────────────────────
SEEKINGALPHA_NEWS_LIMIT = 50

# ── Moomoo (富途) scraping ──────────────────────────────────
MOOMOO_SYMBOLS = [
    "AAPL", "TSLA", "NVDA", "MSFT", "AMZN", "GOOGL",
    "META", "AMD", "PLTR", "COIN",
]
MOOMOO_POSTS_PER_SYMBOL = 20

# ── Stock subreddits to monitor ──────────────────────────────
STOCK_SUBREDDITS = [
    # US / Global
    "wallstreetbets",
    "stocks",
    "investing",
    "StockMarket",
    "SPACs",
    "pennystocks",
    "dividends",
    "options",
    "daytrading",
    "quant",
    "algotrading",
    "economics",
    # ETFs
    "ETFs",
    # Crypto
    "CryptoCurrency",
    "CryptoMarkets",
    # Commodities
    "Commodities",
    "Gold",
    # Europe
    "EUStock",
    "UKInvesting",
    "Finanzen",
    # Asia
    "ChinaStocks",
    "JapanFinance",
    "KoreaStockMarket",
    "IndianStockMarket",
    "DalalStreetTalks",
    # Oceania
    "ASX_Bets",
    "AusFinance",
]

# ── YouTube search queries for KOL discovery ─────────────────
YOUTUBE_SEARCH_QUERIES = [
    # English — global
    "stock analysis",
    "stock market outlook",
    "stock picks this week",
    "earnings analysis",
    "global markets outlook",
    "emerging markets investing",
    # Europe
    "European stocks analysis",
    "FTSE DAX CAC analysis",
    # Asia
    "日本株 分析",
    "한국 주식 투자",
    "Indian stock market analysis",
    "A股分析",
    "港股分析",
    # Oceania
    "ASX stocks analysis",
    # Crypto / Commodities / Forex
    "crypto market analysis",
    "commodities trading outlook",
    "forex market analysis",
    # ETFs
    "ETF investing strategy",
]

# ── Rate limits ──────────────────────────────────────────────
REDDIT_POSTS_PER_SUB = 50       # posts to fetch per subreddit per run
REDDIT_RATE_LIMIT = 0.5         # seconds between Reddit API calls
YOUTUBE_RATE_LIMIT = 1.0        # seconds between YouTube API calls
YOUTUBE_MAX_RESULTS = 20        # results per search query

# ── KOL discovery thresholds ─────────────────────────────────
KOL_MIN_REDDIT_KARMA = 1000     # minimum combined karma to be considered KOL
KOL_MIN_REDDIT_POSTS = 5        # minimum stock-related posts
KOL_MIN_YOUTUBE_SUBS = 1000     # minimum subscribers
KOL_MIN_GUBA_POSTS = 20         # minimum guba posts to be considered KOL

# ── Auto-decay thresholds ────────────────────────────────────
KOL_INACTIVE_DAYS_DECAY = 30    # days of no posting to drop one tier
KOL_INACTIVE_DAYS_REMOVE = 90   # days of no posting to remove

# ── Thermometer parameters ───────────────────────────────────
THERMOMETER_LOOKBACK_DAYS = 7   # lookback window for heat calculation
THERMOMETER_RECENCY_HALF_LIFE = 48  # hours for recency decay half-life

# ── Platform multipliers ─────────────────────────────────────
PLATFORM_MULTIPLIERS = {
    "reddit": 0.8,
    "youtube": 0.7,
    "stocktwits": 0.85,
    "finnhub": 0.5,
    "twitter": 0.8,
    "weibo": 0.9,
    "seekingalpha": 0.7,
    "moomoo": 0.75,
}

# ── Tier base weights ────────────────────────────────────────
TIER_WEIGHTS = {
    "S": 1.0,
    "A": 0.7,
    "B": 0.5,
    "C": 0.3,
    "D": 0.15,
}
