"""
FastAPI REST API — serve A-share auction rush & fund flow rankings.

Usage:
    python3 -m uvicorn a_share_money_flow.api:app --host 127.0.0.1 --port 8011

Endpoints:
    GET  /api/v1/health
    GET  /api/v1/auction/stocks         — auction stock rankings
    GET  /api/v1/auction/sectors        — auction sector rankings
    GET  /api/v1/auction/{code}         — stock auction history
    GET  /api/v1/fund-flow/stocks       — stock fund flow rankings
    GET  /api/v1/fund-flow/sectors      — sector fund flow rankings
    GET  /api/v1/fund-flow/stocks/{code}   — stock fund flow history
    GET  /api/v1/fund-flow/sectors/{name}  — sector fund flow history
    GET  /api/v1/stats                  — database statistics
    POST /api/v1/fetch                  — trigger fetch
"""

from __future__ import annotations

import logging
import math
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from a_share_money_flow.storage import (
    init_db,
    get_auction_stocks,
    get_auction_history,
    get_auction_sectors,
    get_fund_flow_stocks,
    get_fund_flow_stock_history,
    get_fund_flow_sectors,
    get_fund_flow_sector_history,
    get_stats,
)

logger = logging.getLogger("a_share_money_flow.api")


def _clean_records(records):
    """Convert NaN/Inf to None."""
    for r in records:
        for k, v in list(r.items()):
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                r[k] = None
    return records


app = FastAPI(
    title="A股资金流向+竞价抢筹 API",
    description="盘前竞价抢筹排行 + 主力资金流入流出板块/个股排行",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Health ──────────────────────────────────────────────────

@app.get("/api/v1/health")
def health():
    conn = init_db(read_only=True)
    try:
        s = get_stats(conn)
        return {"status": "ok", **s}
    finally:
        conn.close()


# ── Auction ─────────────────────────────────────────────────

@app.get("/api/v1/auction/stocks")
def auction_stocks(
    min_gap: Optional[float] = Query(None, description="Minimum auction gap %"),
    min_score: Optional[float] = Query(None, description="Minimum rush score"),
    sector: Optional[str] = Query(None, description="Sector filter"),
    limit: int = Query(default=50, le=200),
):
    """Auction stock rankings by rush score."""
    conn = init_db(read_only=True)
    try:
        df = get_auction_stocks(conn, min_gap=min_gap, min_score=min_score,
                                 sector=sector, limit=limit)
        records = _clean_records(df.to_dict(orient="records"))
        return {"count": len(records), "stocks": records}
    finally:
        conn.close()


@app.get("/api/v1/auction/sectors")
def auction_sectors(limit: int = Query(default=50, le=100)):
    """Auction sector rankings."""
    conn = init_db(read_only=True)
    try:
        df = get_auction_sectors(conn, limit=limit)
        records = _clean_records(df.to_dict(orient="records"))
        return {"count": len(records), "sectors": records}
    finally:
        conn.close()


@app.get("/api/v1/auction/{code}")
def auction_stock_detail(code: str, days: int = Query(default=30, le=90)):
    """Single stock auction history."""
    conn = init_db(read_only=True)
    try:
        df = get_auction_history(conn, code, days=days)
        if df.empty:
            raise HTTPException(status_code=404, detail=f"No auction data for {code}")
        records = _clean_records(df.to_dict(orient="records"))
        return {"code": code, "count": len(records), "history": records}
    finally:
        conn.close()


# ── Fund Flow ───────────────────────────────────────────────

@app.get("/api/v1/fund-flow/stocks")
def fund_flow_stocks(
    direction: str = Query(default="all", description="inflow | outflow | all"),
    sector: Optional[str] = Query(None),
    min_amount: Optional[float] = Query(None, description="Min abs(main_inflow)"),
    limit: int = Query(default=50, le=200),
):
    """Stock fund flow rankings."""
    conn = init_db(read_only=True)
    try:
        df = get_fund_flow_stocks(conn, direction=direction, sector=sector,
                                   min_amount=min_amount, limit=limit)
        records = _clean_records(df.to_dict(orient="records"))
        return {"count": len(records), "stocks": records}
    finally:
        conn.close()


@app.get("/api/v1/fund-flow/sectors")
def fund_flow_sectors(
    sector_type: str = Query(default="行业资金流", description="行业资金流 | 概念资金流"),
    direction: str = Query(default="all", description="inflow | outflow | all"),
    limit: int = Query(default=50, le=100),
):
    """Sector fund flow rankings."""
    conn = init_db(read_only=True)
    try:
        df = get_fund_flow_sectors(conn, sector_type=sector_type,
                                    direction=direction, limit=limit)
        records = _clean_records(df.to_dict(orient="records"))
        return {"count": len(records), "sectors": records}
    finally:
        conn.close()


@app.get("/api/v1/fund-flow/stocks/{code}")
def fund_flow_stock_detail(code: str, days: int = Query(default=30, le=90)):
    """Single stock fund flow history."""
    conn = init_db(read_only=True)
    try:
        df = get_fund_flow_stock_history(conn, code, days=days)
        if df.empty:
            raise HTTPException(status_code=404, detail=f"No fund flow data for {code}")
        records = _clean_records(df.to_dict(orient="records"))
        return {"code": code, "count": len(records), "history": records}
    finally:
        conn.close()


@app.get("/api/v1/fund-flow/sectors/{sector_name}")
def fund_flow_sector_detail(
    sector_name: str,
    sector_type: str = Query(default="行业资金流"),
    days: int = Query(default=30, le=90),
):
    """Single sector fund flow history."""
    conn = init_db(read_only=True)
    try:
        df = get_fund_flow_sector_history(conn, sector_name,
                                           sector_type=sector_type, days=days)
        if df.empty:
            raise HTTPException(status_code=404, detail=f"No fund flow data for sector: {sector_name}")
        records = _clean_records(df.to_dict(orient="records"))
        return {"sector_name": sector_name, "sector_type": sector_type,
                "count": len(records), "history": records}
    finally:
        conn.close()


# ── Stats ───────────────────────────────────────────────────

@app.get("/api/v1/stats")
def statistics():
    conn = init_db(read_only=True)
    try:
        return get_stats(conn)
    finally:
        conn.close()


# ── Fetch ───────────────────────────────────────────────────

@app.post("/api/v1/fetch")
def trigger_fetch(source: Optional[str] = None):
    """Manual trigger fetch. Optional source: auction | fund_flow."""
    from a_share_money_flow.pipeline import fetch_daily
    return fetch_daily(source=source)


# ── Startup ─────────────────────────────────────────────────

@app.on_event("startup")
def startup():
    init_db()
    logger.info("a_share_money_flow API started on port 8011")
