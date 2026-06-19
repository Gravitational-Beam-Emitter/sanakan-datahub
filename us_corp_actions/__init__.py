"""
US Corporate Actions — daily SEC EDGAR 8-K monitoring pipeline.

Fetches 8-K filings from SEC EDGAR, classifies corporate actions
(dividends, mergers, delistings, bankruptcies, stock splits, buybacks, etc.),
and stores structured data in DuckDB.

Usage:
    python -m us_corp_actions.pipeline --init       # first run: download CIK map + backfill
    python -m us_corp_actions.pipeline               # fetch latest trading day
    python -m us_corp_actions.pipeline --date 20260612  # fetch specific date
    python -m us_corp_actions.scheduler              # start daily scheduler
"""
