"""
FastAPI REST API — serve KOL thermometer data.

Usage:
    python -m uvicorn kol_thermometer.api:app --host 127.0.0.1 --port 8010

Endpoints:
    GET  /api/v1/health
    GET  /api/v1/kols                   — list KOLs
    GET  /api/v1/kols/{id}              — single KOL detail + recent posts
    GET  /api/v1/thermometer            — current thermometer (top hot stocks)
    GET  /api/v1/thermometer/history    — thermometer history
    GET  /api/v1/thermometer/{code}     — stock heat history
    GET  /api/v1/mentions               — recent stock mentions
    GET  /api/v1/stats                  — database statistics
    POST /api/v1/fetch                  — trigger fetch cycle
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from kol_thermometer.storage import (
    init_db,
    get_kols,
    get_kol_by_id,
    get_recent_posts,
    get_posts_for_kol,
    get_mentions,
    get_thermometer,
    get_thermometer_history,
    get_stats,
)
import math

logger = logging.getLogger("kol_thermometer.api")


def _clean_records(records):
    """Convert NaN/Inf to None so JSON serialization doesn't crash."""
    for r in records:
        for k, v in list(r.items()):
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                r[k] = None
    return records


app = FastAPI(
    title="Global Market Thermometer (全球市场温度计) API",
    description="KOL sentiment tracking across global financial markets — Reddit, YouTube, global forums",
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


# ── KOLs ────────────────────────────────────────────────────

@app.get("/api/v1/kols")
def list_kols(
    platform: Optional[str] = None,
    tier: Optional[str] = None,
    limit: int = Query(default=50, le=500),
):
    """List KOLs, optionally filtered by platform/tier."""
    conn = init_db(read_only=True)
    try:
        df = get_kols(conn, platform=platform, tier=tier, is_active=1, limit=limit)
        records = _clean_records(df.to_dict(orient="records"))
        return {"count": len(records), "kols": records}
    finally:
        conn.close()


@app.get("/api/v1/kols/{kol_id}")
def kol_detail(kol_id: int):
    """Single KOL detail with recent posts."""
    conn = init_db(read_only=True)
    try:
        kol = get_kol_by_id(conn, kol_id)
        if kol is None:
            raise HTTPException(status_code=404, detail=f"KOL {kol_id} not found")
        posts_df = get_posts_for_kol(conn, kol_id, limit=20)
        posts = _clean_records(posts_df.to_dict(orient="records"))
        return {"kol": kol, "recent_posts": posts}
    finally:
        conn.close()


# ── Thermometer ─────────────────────────────────────────────

@app.get("/api/v1/thermometer")
def thermometer_current(
    market: Optional[str] = None,
    min_heat: Optional[float] = None,
    limit: int = Query(default=50, le=200),
):
    """Current market thermometer — hottest stocks by KOL discussion."""
    conn = init_db(read_only=True)
    try:
        df = get_thermometer(conn, market=market, min_heat=min_heat, limit=limit)
        records = _clean_records(df.to_dict(orient="records"))
        return {"count": len(records), "stocks": records}
    finally:
        conn.close()


@app.get("/api/v1/thermometer/history")
def thermometer_history(limit: int = Query(default=30, le=100)):
    """Latest thermometer dates available."""
    conn = init_db(read_only=True)
    try:
        dates = conn.execute("""
            SELECT DISTINCT date FROM thermometer
            ORDER BY date DESC LIMIT ?
        """, [limit]).fetchall()
        return {"dates": [str(d[0]) for d in dates]}
    finally:
        conn.close()


@app.get("/api/v1/thermometer/{stock_code}")
def stock_thermometer(stock_code: str, days: int = Query(default=30, le=90)):
    """Thermometer history for a specific stock."""
    conn = init_db(read_only=True)
    try:
        df = get_thermometer_history(conn, stock_code, days=days)
        records = _clean_records(df.to_dict(orient="records"))
        return {"stock_code": stock_code, "count": len(records), "history": records}
    finally:
        conn.close()


# ── Mentions ────────────────────────────────────────────────

@app.get("/api/v1/mentions")
def list_mentions(
    stock_code: Optional[str] = None,
    kol_id: Optional[int] = None,
    platform: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    limit: int = Query(default=50, le=200),
):
    """Query stock mentions with filters."""
    conn = init_db(read_only=True)
    try:
        df = get_mentions(conn, stock_code=stock_code, kol_id=kol_id,
                          platform=platform, start=start, end=end, limit=limit)
        records = _clean_records(df.to_dict(orient="records"))
        return {"count": len(records), "mentions": records}
    finally:
        conn.close()


# ── Stats ───────────────────────────────────────────────────

@app.get("/api/v1/stats")
def statistics():
    """Database statistics."""
    conn = init_db(read_only=True)
    try:
        return get_stats(conn)
    finally:
        conn.close()


# ── Fetch ───────────────────────────────────────────────────

@app.post("/api/v1/fetch")
def trigger_fetch(source: Optional[str] = None):
    """Manually trigger data fetch cycle. Optional source: reddit, youtube."""
    from kol_thermometer.pipeline import fetch_daily
    result = fetch_daily(source=source)
    return result


# ── Startup ─────────────────────────────────────────────────

@app.on_event("startup")
def startup():
    init_db()
    logger.info("kol_thermometer API started on port 8010")
