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

# ── Stock subreddits to monitor ──────────────────────────────
STOCK_SUBREDDITS = [
    "wallstreetbets",
    "stocks",
    "investing",
    "ChinaStocks",
    "StockMarket",
    "SPACs",
    "pennystocks",
    "dividends",
    "options",
    "daytrading",
]

# ── YouTube search queries for KOL discovery ─────────────────
YOUTUBE_SEARCH_QUERIES = [
    "stock analysis",
    "A股分析",
    "股票推荐",
    "market outlook",
    "投资策略",
    "stock picks",
    "earnings analysis",
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
    "guba": 1.0,
    "xueqiu": 0.9,
}

# ── Tier base weights ────────────────────────────────────────
TIER_WEIGHTS = {
    "S": 1.0,
    "A": 0.7,
    "B": 0.5,
    "C": 0.3,
    "D": 0.15,
}
