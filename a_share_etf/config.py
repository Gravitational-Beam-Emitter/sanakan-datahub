"""
Configuration — loads .env and sets DB path.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

DB_PATH = str(Path(__file__).resolve().parent.parent / "a_share_etf.duckdb")
