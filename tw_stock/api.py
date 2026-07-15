"""
FastAPI REST API — serve Taiwan stock market data.

Usage:
    python -m uvicorn tw_stock.api:app --host 127.0.0.1 --port 8007

Endpoints:
    GET  /api/v1/health              — Service health
    GET  /api/v1/daily/{date}        — Full daily review
    GET  /api/v1/stocks/{date}       — Significant movers for date
    GET  /api/v1/stock/{code}        — Stock price history
    GET  /api/v1/stock/{code}/detail — Stock listing detail
    GET  /api/v1/listings            — Listed stocks with filters
    GET  /api/v1/indices             — Market index data
    GET  /api/v1/narratives/{date}   — Daily narratives
    GET  /api/v1/narratives/range    — Narratives for date range
    GET  /api/v1/industry/{date}     — Industry breakdown
    GET  /api/v1/dates               — Available trading dates
    GET  /api/v1/trend               — Trend data for backtesting
    GET  /api/v1/sectors             — Sector rotation heatmap
    POST /api/v1/prices/batch        — Batch prices
    GET  /api/v1/fetch/status        — Fetch log
    POST /api/v1/fetch               — Trigger daily fetch
    POST /api/v1/init                — Full init
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from tw_stock.storage import (
    init_db,
    get_counts,
    get_listed_stocks,
    get_stock_detail,
    get_daily_prices,
    get_prices_batch,
    get_market_indices,
    get_daily_movers,
    get_stock_mover_history,
    get_narratives,
    get_narratives_range,
    get_industry_summary,
    get_available_dates,
    get_trend_data,
    get_sector_rotation,
    get_sector_detail,
    get_daily_summary,
    get_fetch_status,
)
from tw_stock.pipeline import (
    fetch_daily,
    fetch_latest,
    init_pipeline,
)

logger = logging.getLogger("tw_stock.api")

app = FastAPI(
    title="Taiwan Stock API",
    description="TWSE/TPEx Taiwan stock market data (listings, prices, indices)",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _serialize_records(records):
    """Convert date/time objects to ISO strings."""
    import math
    for r in records:
        for k, v in list(r.items()):
            if hasattr(v, "isoformat"):
                s = v.isoformat()
                r[k] = None if s == "NaT" else s
            elif isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                r[k] = None
    return records


# ── Health ─────────────────────────────────────────────────────

@app.get("/api/v1/health")
def health():
    conn = init_db(read_only=True)
    try:
        counts = get_counts(conn)
        return {"status": "ok", **counts}
    finally:
        conn.close()


# ── Daily Review ──────────────────────────────────────────────

@app.get("/api/v1/daily/{date}")
def daily_review(date: str):
    conn = init_db(read_only=True)
    try:
        movers_df = get_daily_movers(conn, date)
        summary = get_daily_summary(conn, date)
        narratives = get_narratives(conn, date)
        industry_df = get_industry_summary(conn, date)

        movers_records = movers_df.to_dict(orient="records")
        _serialize_records(movers_records)

        return {
            "date": date,
            "summary": summary,
            "movers": movers_records,
            "narratives": narratives,
            "industries": industry_df.to_dict(orient="records"),
        }
    finally:
        conn.close()


# ── Movers ────────────────────────────────────────────────────

@app.get("/api/v1/stocks/{date}")
def stocks_by_date(date: str, industry: Optional[str] = None):
    conn = init_db(read_only=True)
    try:
        df = get_daily_movers(conn, date)
        if df.empty:
            raise HTTPException(status_code=404, detail=f"No data for {date}")
        if industry:
            df = df[df["industry"] == industry]
        records = df.to_dict(orient="records")
        _serialize_records(records)
        return {"date": date, "count": len(records), "movers": records}
    finally:
        conn.close()


# ── Stock Detail & History ───────────────────────────────────

@app.get("/api/v1/stock/{code}")
def stock_history(code: str, limit: int = Query(60, le=200)):
    """Price history for a stock."""
    conn = init_db(read_only=True)
    try:
        df = get_daily_prices(conn, code, limit=limit)
        if df.empty:
            raise HTTPException(status_code=404, detail=f"No price data for {code}")
        records = df.to_dict(orient="records")
        _serialize_records(records)
        return {"code": code, "count": len(records), "prices": records}
    finally:
        conn.close()


@app.get("/api/v1/stock/{code}/detail")
def stock_detail(code: str):
    """Stock listing info + mover history."""
    conn = init_db(read_only=True)
    try:
        detail = get_stock_detail(conn, code)
        if detail is None:
            raise HTTPException(status_code=404, detail=f"Stock {code} not found")
        mover_df = get_stock_mover_history(conn, code, limit=20)
        mover_records = mover_df.to_dict(orient="records")
        _serialize_records(mover_records)
        detail["recent_movers"] = mover_records
        return detail
    finally:
        conn.close()


# ── Batch Prices ──────────────────────────────────────────────

@app.post("/api/v1/prices/batch")
def prices_batch(body: dict):
    """Get recent prices for multiple stocks.
    Body: {"codes": ["2330", ...], "limit": 20}"""
    codes = body.get("codes", [])
    if not codes or not isinstance(codes, list):
        raise HTTPException(status_code=422, detail="codes must be a non-empty list")
    limit = min(body.get("limit", 20), 60)
    conn = init_db(read_only=True)
    try:
        prices = get_prices_batch(conn, codes, limit=limit)
        return {"prices": prices}
    finally:
        conn.close()


# ── Listings ─────────────────────────────────────────────────

@app.get("/api/v1/listings")
def list_listings(
    market: Optional[str] = Query(None, description="TWSE or TPEx"),
    sector: Optional[str] = Query(None, description="Sector filter"),
    search: Optional[str] = Query(None, description="Search by name or code"),
    limit: int = Query(100, ge=1, le=1000),
):
    conn = init_db(read_only=True)
    try:
        df = get_listed_stocks(conn, market=market, sector=sector, search=search, limit=limit)
        records = df.to_dict(orient="records")
        _serialize_records(records)
        return {"count": len(records), "stocks": records}
    finally:
        conn.close()


# ── Indices ──────────────────────────────────────────────────

@app.get("/api/v1/indices")
def list_indices(
    index_code: Optional[str] = Query(None, description="^TWII or ^TWOII"),
    start: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    end: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
    limit: int = Query(200, ge=1, le=500),
):
    conn = init_db(read_only=True)
    try:
        df = get_market_indices(conn, index_code=index_code, start=start, end=end, limit=limit)
        records = df.to_dict(orient="records")
        _serialize_records(records)
        return {"count": len(records), "indices": records}
    finally:
        conn.close()


# ── Narratives ───────────────────────────────────────────────

@app.get("/api/v1/narratives/range")
def narratives_range(
    start: str = Query(..., description="Start date YYYY-MM-DD"),
    end: str = Query(..., description="End date YYYY-MM-DD"),
):
    conn = init_db(read_only=True)
    try:
        narratives = get_narratives_range(conn, start, end)
        return {"start": start, "end": end, "count": len(narratives), "narratives": narratives}
    finally:
        conn.close()


@app.get("/api/v1/narratives/{date}")
def narratives_by_date(date: str):
    conn = init_db(read_only=True)
    try:
        narratives = get_narratives(conn, date)
        return {"date": date, "narratives": narratives}
    finally:
        conn.close()


# ── Industry ─────────────────────────────────────────────────

@app.get("/api/v1/industry/{date}")
def industry_breakdown(date: str):
    conn = init_db(read_only=True)
    try:
        df = get_industry_summary(conn, date)
        if df.empty:
            raise HTTPException(status_code=404, detail=f"No data for {date}")
        return {"date": date, "industries": df.to_dict(orient="records")}
    finally:
        conn.close()


# ── Dates ─────────────────────────────────────────────────────

@app.get("/api/v1/dates")
def available_dates(limit: int = Query(30, le=60)):
    conn = init_db(read_only=True)
    try:
        dates = get_available_dates(conn, limit=limit)
        return {"count": len(dates), "dates": dates}
    finally:
        conn.close()


# ── Trend ─────────────────────────────────────────────────────

@app.get("/api/v1/trend")
def trend_data(
    start: str = Query(..., description="Start date YYYY-MM-DD"),
    end: str = Query(..., description="End date YYYY-MM-DD"),
):
    conn = init_db(read_only=True)
    try:
        df = get_trend_data(conn, start, end)
        records = df.to_dict(orient="records")
        for r in records:
            r["date"] = str(r["date"])
        return {"start": start, "end": end, "count": len(records), "data": records}
    finally:
        conn.close()


# ── Sectors ──────────────────────────────────────────────────

@app.get("/api/v1/sectors")
def sector_rotation(
    start: str = Query(..., description="Start date YYYY-MM-DD"),
    end: str = Query(..., description="End date YYYY-MM-DD"),
    top_n: int = Query(15, ge=5, le=30),
):
    conn = init_db(read_only=True)
    try:
        raw = get_sector_rotation(conn, start, end, top_n)
        for r in raw:
            r["date"] = str(r["date"])
        sectors = list(dict.fromkeys(r["industry"] for r in raw))
        days = sorted(set(r["date"] for r in raw))
        matrix: list[list[int]] = []
        for sector in sectors:
            row_vals = []
            for day in days:
                found = next((r["cnt"] for r in raw if r["date"] == day and r["industry"] == sector), 0)
                row_vals.append(found)
            matrix.append(row_vals)
        return {"start": start, "end": end, "days": days, "sectors": sectors, "matrix": matrix}
    finally:
        conn.close()


@app.get("/api/v1/sectors/macro")
def sector_macro_detail(
    start: str = Query(..., description="Start date YYYY-MM-DD"),
    end: str = Query(..., description="End date YYYY-MM-DD"),
    sector: str = Query(..., description="Industry name"),
):
    conn = init_db(read_only=True)
    try:
        df = get_sector_detail(conn, start, end, sector)
        records = df.to_dict(orient="records")
        for r in records:
            r["date"] = str(r["date"])
        return {"sector": sector, "count": len(records), "data": records}
    finally:
        conn.close()


# ── Fetch Status ──────────────────────────────────────────────

@app.get("/api/v1/fetch/status")
def fetch_status(days: int = Query(7, le=30)):
    conn = init_db(read_only=True)
    try:
        df = get_fetch_status(conn, days=days)
        records = df.to_dict(orient="records")
        _serialize_records(records)
        return {"count": len(records), "logs": records}
    finally:
        conn.close()


# ── Fetch Triggers ────────────────────────────────────────────

@app.post("/api/v1/fetch")
def trigger_fetch(date: Optional[str] = None, llm: bool = True):
    """Trigger daily fetch (defaults to latest trading day)."""
    if date:
        result = fetch_daily(date, use_llm=llm)
    else:
        result = fetch_latest(use_llm=llm)
    return {"status": "completed", "result": result}


@app.post("/api/v1/init")
def trigger_init():
    """Full init: listings + index history + recent prices."""
    result = init_pipeline()
    return {"status": "completed", "result": result}


# ── Startup ───────────────────────────────────────────────────

@app.on_event("startup")
def startup():
    init_db()
    logger.info("Taiwan Stock API started")
