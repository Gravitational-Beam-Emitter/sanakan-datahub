"""
FastAPI REST API — serve US corporate actions data.

Usage:
    python -m uvicorn us_corp_actions.api:app --host 127.0.0.1 --port 8002

Endpoints:
    GET  /api/v1/health                  — Service health
    GET  /api/v1/actions/{date}          — Full daily review (actions + summary + breakdown)
    GET  /api/v1/actions                 — Filtered actions (date range, type, ticker)
    GET  /api/v1/actions/ticker/{ticker} — Corporate action history for a ticker
    GET  /api/v1/dates                   — Available filing dates
    GET  /api/v1/summary                 — Daily summary for date range
    GET  /api/v1/breakdown/{date}        — Action type breakdown for a date
    POST /api/v1/fetch                   — Trigger data fetch
"""

from __future__ import annotations

import logging
from datetime import date as date_type
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from us_corp_actions.storage import (
    init_db,
    get_actions_by_date,
    get_actions_by_ticker,
    get_action_summary,
    get_recent_actions,
    get_available_action_dates,
    get_type_breakdown,
    get_daily_summary,
    get_fetch_status,
    get_ticker_count,
    get_action_count,
)
from us_corp_actions.pipeline import fetch_daily, init as pipeline_init

logger = logging.getLogger("us_corp_actions.api")

app = FastAPI(
    title="US Corporate Actions API",
    description="美国上市公司公司行动数据 (SEC EDGAR 8-K filings)",
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
    conn = init_db()
    try:
        companies = get_ticker_count(conn)
        actions = get_action_count(conn)
        row = conn.execute(
            "SELECT COUNT(DISTINCT filing_date) FROM corporate_actions"
        ).fetchone()
        days = row[0] if row else 0

        # Last fetch status
        status_row = conn.execute(
            "SELECT fetch_date, status, new_actions FROM fetch_log ORDER BY fetch_date DESC LIMIT 1"
        ).fetchone()

        return {
            "status": "ok",
            "listed_companies": companies,
            "total_actions": actions,
            "trading_days": days,
            "last_fetch": {
                "date": str(status_row[0]) if status_row else None,
                "status": str(status_row[1]) if status_row else None,
                "new_actions": int(status_row[2]) if status_row else 0,
            },
        }
    finally:
        conn.close()


# ── Daily Review ────────────────────────────────────────────

@app.get("/api/v1/actions/{date}")
def daily_actions(date: str):
    """Get all corporate actions for a filing date, with summary and breakdown."""
    conn = init_db(read_only=True)
    try:
        df = get_actions_by_date(conn, date)
        if df.empty:
            raise HTTPException(status_code=404, detail=f"No data for {date}")

        summary = get_daily_summary(conn, date)
        breakdown_df = get_type_breakdown(conn, date)

        # Convert DataFrames to records, handling date types
        records = df.to_dict(orient="records")
        for r in records:
            for k, v in r.items():
                if hasattr(v, "isoformat"):
                    r[k] = v.isoformat()

        return {
            "date": date,
            "summary": summary,
            "actions": records,
            "breakdown": breakdown_df.to_dict(orient="records"),
        }
    finally:
        conn.close()


# ── Filtered Actions ────────────────────────────────────────

@app.get("/api/v1/actions")
def list_actions(
    start: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    end: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
    action_type: Optional[str] = Query(None, description="Filter by action type"),
    ticker: Optional[str] = Query(None, description="Filter by ticker"),
    limit: int = Query(100, ge=1, le=500),
):
    """List actions with optional date range, type, and ticker filters."""
    conn = init_db(read_only=True)
    try:
        where = ["1=1"]
        params: list = []

        if start:
            where.append("filing_date >= ?")
            params.append(start)
        if end:
            where.append("filing_date <= ?")
            params.append(end)
        if action_type:
            where.append("action_type = ?")
            params.append(action_type)
        if ticker:
            where.append("ticker = ?")
            params.append(ticker.upper())

        sql = f"""
            SELECT filing_date, ticker, company_name, action_type,
                   action_subtype, item_numbers, description, source_url
            FROM corporate_actions
            WHERE {' AND '.join(where)}
            ORDER BY filing_date DESC, action_type
            LIMIT ?
        """
        params.append(limit)

        df = conn.execute(sql, params).df()
        records = df.to_dict(orient="records")
        for r in records:
            for k, v in r.items():
                if hasattr(v, "isoformat"):
                    r[k] = v.isoformat()

        return {"count": len(records), "actions": records}
    finally:
        conn.close()


# ── Ticker History ──────────────────────────────────────────

@app.get("/api/v1/actions/ticker/{ticker}")
def ticker_history(ticker: str, limit: int = Query(50, ge=1, le=200)):
    """Get corporate action history for a specific ticker."""
    conn = init_db(read_only=True)
    try:
        df = get_actions_by_ticker(conn, ticker, limit=limit)
        if df.empty:
            raise HTTPException(status_code=404, detail=f"No actions for {ticker}")
        records = df.to_dict(orient="records")
        for r in records:
            for k, v in r.items():
                if hasattr(v, "isoformat"):
                    r[k] = v.isoformat()
        return {"ticker": ticker.upper(), "count": len(records), "actions": records}
    finally:
        conn.close()


# ── Dates ───────────────────────────────────────────────────

@app.get("/api/v1/dates")
def available_dates(limit: int = Query(30, le=60)):
    """Get list of available filing dates."""
    conn = init_db(read_only=True)
    try:
        dates = get_available_action_dates(conn, limit=limit)
        return {"count": len(dates), "dates": dates}
    finally:
        conn.close()


# ── Summary ─────────────────────────────────────────────────

@app.get("/api/v1/summary")
def date_summary(
    start: str = Query(..., description="Start date YYYY-MM-DD"),
    end: str = Query(..., description="End date YYYY-MM-DD"),
):
    """Get daily action type breakdown for a date range."""
    conn = init_db(read_only=True)
    try:
        df = get_action_summary(conn, start, end)
        records = df.to_dict(orient="records")
        for r in records:
            r["date"] = str(r["filing_date"])
            del r["filing_date"]

        # Also get daily totals
        daily_df = conn.execute("""
            SELECT filing_date, COUNT(*) AS total, COUNT(DISTINCT ticker) AS companies
            FROM corporate_actions
            WHERE filing_date >= ? AND filing_date <= ?
            GROUP BY filing_date
            ORDER BY filing_date DESC
        """, [start, end]).df()
        daily_records = daily_df.to_dict(orient="records")
        for r in daily_records:
            r["date"] = str(r["filing_date"])
            del r["filing_date"]

        return {
            "start": start, "end": end,
            "daily_totals": daily_records,
            "type_breakdown": records,
        }
    finally:
        conn.close()


# ── Breakdown ───────────────────────────────────────────────

@app.get("/api/v1/breakdown/{date}")
def action_breakdown(date: str):
    """Get action type breakdown for a specific date."""
    conn = init_db(read_only=True)
    try:
        df = get_type_breakdown(conn, date)
        if df.empty:
            raise HTTPException(status_code=404, detail=f"No data for {date}")
        return {"date": date, "breakdown": df.to_dict(orient="records")}
    finally:
        conn.close()


# ── Fetch Status ────────────────────────────────────────────

@app.get("/api/v1/fetch/status")
def fetch_status(days: int = Query(7, le=30)):
    """Get recent fetch log entries."""
    conn = init_db(read_only=True)
    try:
        df = get_fetch_status(conn, days=days)
        records = df.to_dict(orient="records")
        for r in records:
            for k, v in r.items():
                if hasattr(v, "isoformat"):
                    r[k] = v.isoformat()
        return {"count": len(records), "logs": records}
    finally:
        conn.close()


# ── Fetch ───────────────────────────────────────────────────

@app.post("/api/v1/fetch")
def trigger_fetch(date: Optional[str] = None):
    """Manually trigger data fetch for a date (defaults to latest)."""
    if date:
        result = fetch_daily(date)
    else:
        result = fetch_daily()
    return {"status": "completed", "result": result}


# ── Init ────────────────────────────────────────────────────

@app.post("/api/v1/init")
def trigger_init():
    """Re-initialize: download CIK map and backfill historical data."""
    result = pipeline_init()
    return {"status": "completed", "result": result}


# ── Startup ─────────────────────────────────────────────────

@app.on_event("startup")
def startup():
    init_db()
    logger.info("US Corporate Actions API started")
