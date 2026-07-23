"""
Configuration — DB path and scheduler times.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path)

DB_PATH = str(Path(__file__).resolve().parent.parent / "a_share_money_flow.duckdb")

# ── Auction scoring weights ──────────────────────────────────
AUCTION_GAP_WEIGHT = 0.4      # 竞价涨幅权重
AUCTION_TURNOVER_WEIGHT = 0.3 # 竞价换手率分位权重
AUCTION_VOLUME_WEIGHT = 0.3   # 竞价量比分位权重

# ── Filters ──────────────────────────────────────────────────
AUCTION_MIN_GAP_PCT = 1.0     # 最少竞价涨幅(%) 才算抢筹
AUCTION_MIN_RUSH_SCORE = 30   # 最低抢筹评分
FUND_FLOW_MIN_AMOUNT = 1e7    # 最小主力净流入(元)才纳入排行, 默认1000万

# ── AKShare rate limits ──────────────────────────────────────
AKSHARE_RATE_LIMIT = 0.5      # seconds between AKShare calls
