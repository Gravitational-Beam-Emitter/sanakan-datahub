"""
A-Share Money Flow (A股资金流向+竞价抢筹) Pipeline.

Tracks two daily snapshots:
  1. Pre-market auction rush (盘前竞价抢筹) — captured at ~9:28 via ak.stock_zh_a_spot()
  2. Fund flow rankings (资金流入流出) — captured at ~14:55 via ak.stock_sector_fund_flow_rank()
     and ak.stock_individual_fund_flow_rank()

Data source: 东方财富 (Eastmoney) via AKShare. No API key required.

Tables:
  - auction_stock_daily    — per-stock auction snapshot with rush scores
  - auction_sector_daily   — sector-level auction aggregation
  - fund_flow_stock_daily  — per-stock daily fund flow (main, super_large, large, medium, small)
  - fund_flow_sector_daily — sector fund flow (行业 + 概念)
  - fetch_log              — fetch audit log
"""
