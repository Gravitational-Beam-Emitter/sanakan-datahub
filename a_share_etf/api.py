"""
FastAPI REST API — serve daily A-share ETF flow and margin data.

Usage:
    python -m uvicorn a_share_etf.api:app --host 127.0.0.1 --port 8009

Endpoints:
    GET  /api/v1/health
    GET  /api/v1/etf/{date}              — All ETFs for a date
    GET  /api/v1/etf/{code}/history      — Single ETF history
    GET  /api/v1/sectors/{date}          — Sector flow breakdown
    GET  /api/v1/sectors/{sector}/history — Sector flow time series
    GET  /api/v1/margin                  — Margin latest/history
    GET  /api/v1/overview/{date}         — Market overview
    GET  /api/v1/overview/history        — Overview time series
    GET  /api/v1/dates                   — Available dates
    POST /api/v1/fetch                   — Trigger fetch
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from a_share_etf.storage import (
    init_db,
    get_etfs_by_date,
    get_etf_history,
    get_sectors_by_date,
    get_sector_history,
    get_sectors_history,
    get_margin_by_date,
    get_margin_history,
    get_overview_by_date,
    get_overview_history,
    get_available_dates,
)
import math

from a_share_etf.etf_classification import list_sectors

logger = logging.getLogger("a_share_etf.api")


def _clean_records(records):
    """Convert NaN/Inf to None so JSON serialization doesn't crash."""
    for r in records:
        for k, v in list(r.items()):
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                r[k] = None
    return records

app = FastAPI(
    title="A股ETF资金流 API",
    description="中国A股全市场ETF每日资金流、板块轮动、融资融券数据",
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
        etf_count = conn.execute("SELECT COUNT(*) FROM etf_daily").fetchone()
        day_count = conn.execute("SELECT COUNT(DISTINCT date) FROM etf_daily").fetchone()
        margin_count = conn.execute("SELECT COUNT(*) FROM margin_daily").fetchone()
        overview_count = conn.execute("SELECT COUNT(*) FROM market_overview_daily").fetchone()
        return {
            "status": "ok",
            "total_etf_records": etf_count[0] if etf_count else 0,
            "trading_days": day_count[0] if day_count else 0,
            "margin_days": margin_count[0] if margin_count else 0,
            "overview_days": overview_count[0] if overview_count else 0,
        }
    finally:
        conn.close()


# ── ETF ─────────────────────────────────────────────────────

@app.get("/api/v1/etf/{date}")
def etfs_by_date(date: str, sector: Optional[str] = None):
    """All ETFs for a date, optionally filtered by sector."""
    conn = init_db(read_only=True)
    try:
        df = get_etfs_by_date(conn, date, sector=sector)
        if df.empty:
            raise HTTPException(status_code=404, detail=f"No ETF data for {date}")
        records = _clean_records(_clean_records(df.to_dict(orient="records")))
        return {"date": date, "count": len(df), "etfs": records}
    finally:
        conn.close()


@app.get("/api/v1/etf/{code}/history")
def etf_history(code: str, limit: int = Query(default=60, le=200)):
    """Daily history for a specific ETF."""
    conn = init_db(read_only=True)
    try:
        df = get_etf_history(conn, code, limit=limit)
        if df.empty:
            raise HTTPException(status_code=404, detail=f"No history for {code}")
        return {"code": code, "count": len(df), "history": _clean_records(df.to_dict(orient="records"))}
    finally:
        conn.close()


# ── Sectors ─────────────────────────────────────────────────

@app.get("/api/v1/sectors/list")
def sector_list():
    """List all sector labels."""
    return {"sectors": list_sectors()}


@app.get("/api/v1/sectors/history")
def sectors_history(limit: int = Query(default=60, le=200)):
    """Sector flow history for all sectors across last N dates (for stacked chart)."""
    conn = init_db(read_only=True)
    try:
        df = get_sectors_history(conn, limit=limit)
        if df.empty:
            return {"dates": [], "sectors": [], "rows": []}
        # Convert date column to string for JSON
        df["date"] = df["date"].astype(str).str[:10]
        return {"count": len(df), "rows": _clean_records(df.to_dict(orient="records"))}
    finally:
        conn.close()


@app.get("/api/v1/sectors/{date}")
def sectors_by_date(date: str):
    """Sector flow breakdown for a date."""
    conn = init_db(read_only=True)
    try:
        df = get_sectors_by_date(conn, date)
        if df.empty:
            raise HTTPException(status_code=404, detail=f"No sector data for {date}")
        return {"date": date, "sectors": _clean_records(df.to_dict(orient="records"))}
    finally:
        conn.close()


@app.get("/api/v1/sectors/{sector}/history")
def sector_history(sector: str, limit: int = Query(default=60, le=200)):
    """Daily flow history for a sector."""
    conn = init_db(read_only=True)
    try:
        df = get_sector_history(conn, sector, limit=limit)
        if df.empty:
            raise HTTPException(status_code=404, detail=f"No history for sector: {sector}")
        return {"sector": sector, "count": len(df), "history": _clean_records(df.to_dict(orient="records"))}
    finally:
        conn.close()


# ── Margin ──────────────────────────────────────────────────

@app.get("/api/v1/margin")
def margin_latest():
    """Get latest margin balance data."""
    conn = init_db(read_only=True)
    try:
        df = get_margin_history(conn, limit=1)
        if df.empty:
            raise HTTPException(status_code=404, detail="No margin data")
        return df.iloc[0].to_dict()
    finally:
        conn.close()


@app.get("/api/v1/margin/history")
def margin_history(
    start: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    end: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
    limit: int = Query(default=60, le=500),
):
    """Margin balance history."""
    conn = init_db(read_only=True)
    try:
        df = get_margin_history(conn, start=start, end=end, limit=limit)
        return {"count": len(df), "data": _clean_records(df.to_dict(orient="records"))}
    finally:
        conn.close()


# ── Market Overview ─────────────────────────────────────────

@app.get("/api/v1/overview/history")
def overview_history(
    start: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    end: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
    limit: int = Query(default=60, le=500),
):
    """Market overview time series."""
    conn = init_db(read_only=True)
    try:
        df = get_overview_history(conn, start=start, end=end, limit=limit)
        return {"count": len(df), "data": _clean_records(df.to_dict(orient="records"))}
    finally:
        conn.close()


@app.get("/api/v1/overview/{date}")
def overview_by_date(date: str):
    """Market overview (merged proxy) for a date."""
    conn = init_db(read_only=True)
    try:
        row = get_overview_by_date(conn, date)
        if row is None:
            raise HTTPException(status_code=404, detail=f"No overview for {date}")
        return row
    finally:
        conn.close()


# ── Dates ───────────────────────────────────────────────────

@app.get("/api/v1/dates")
def available_dates(limit: int = Query(default=30, le=60)):
    """Get list of available trading dates."""
    conn = init_db(read_only=True)
    try:
        dates = get_available_dates(conn, limit=limit)
        return {"count": len(dates), "dates": dates}
    finally:
        conn.close()


# ── Fetch ───────────────────────────────────────────────────

@app.post("/api/v1/fetch")
def trigger_fetch(date: Optional[str] = None):
    """Manually trigger data fetch for a date (defaults to latest)."""
    from a_share_etf.pipeline import fetch_latest as _fetch_latest
    if date:
        result = fetch_daily(date)
    else:
        result = _fetch_latest()
    return {"status": "completed", "result": result}


# ── SDK compatibility alias ─────────────────────────────────
# The SDK client uses api.fetch(date) — POST to /api/v1/fetch?date=...

from a_share_etf.pipeline import fetch_daily


# ── Startup ─────────────────────────────────────────────────

@app.on_event("startup")
def startup():
    init_db()
    logger.info("a_share_etf API started on port 8009")
