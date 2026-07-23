"""
Market Thermometer (市场温度计) — KOL stock sentiment pipeline.

Auto-discovers KOLs across Reddit, YouTube, and 东方财富股吧, tracks their
stock mentions, computes sentiment via LLM, and aggregates a per-stock
"heat" score (0-100) showing how actively a stock is being discussed/pumped.

Data sources:
  - Reddit (r/wallstreetbets, r/stocks, r/investing, etc.) via PRAW
  - YouTube stock analysis channels via YouTube Data API v3
  - 东方财富股吧 via AKShare

Tables:
  - kols            — auto-discovered KOLs with scores, tiers, weights
  - kol_posts       — fetched posts with engagement metrics
  - stock_mentions  — parsed stock mentions with LLM sentiment
  - thermometer     — daily aggregated heat per stock
  - fetch_log       — fetch audit log
"""
