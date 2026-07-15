"""
tw_stock configuration — Taiwan stock market (TWSE/TPEx).
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# LLM API keys (reused from .env, optional — LLM tagging skipped without them)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
QWEN_API_KEY = os.getenv("QWEN_API_KEY", "")
MINIMAX_API_KEY = os.getenv("MINIMAX_API_KEY", "")

# DuckDB
DB_PATH = str(Path(__file__).resolve().parent / "tw_stock.duckdb")
