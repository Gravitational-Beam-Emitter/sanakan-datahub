"""
FastAPI REST API — serve company announcements data.

Usage:
    python -m uvicorn announcements.api:app --host 127.0.0.1 --port 8005

Endpoints:
    GET  /api/v1/health          — Service health, counts by market
    GET  /api/v1/announcements   — List with filters (ticker, market, source, date range)
    GET  /api/v1/announcements/{id} — Single announcement detail with full text
    GET  /api/v1/companies       — Tracked companies with counts
    POST /api/v1/fetch           — Trigger data fetch
"""

from __future__ import annotations

import logging
from datetime import date as date_type
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from announcements.storage import (
    init_db,
    upsert_announcements,
    query_announcements,
    get_announcement_by_id,
    get_tracked_companies,
    get_announcement_dates,
    get_announcement_count,
    get_counts_by_market,
    get_fetch_status,
)
from announcements.pipeline import fetch_daily

logger = logging.getLogger("announcements.api")

app = FastAPI(
    title="Company Announcements API",
    description="Multi-market company announcements & filings (US SEC, HK HKEXnews, CN CNINFO)",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _serialize_record(record: dict) -> dict:
    """Convert date/datetime types to ISO date strings."""
    for k, v in record.items():
        if hasattr(v, "date"):
            # datetime — return date portion only
            record[k] = v.date().isoformat()
        elif hasattr(v, "isoformat"):
            record[k] = v.isoformat()
    return record


# ── Health ─────────────────────────────────────────────────────

@app.get("/api/v1/health")
def health():
    conn = init_db(read_only=True)
    try:
        total = get_announcement_count(conn)
        by_market = get_counts_by_market(conn)

        status_row = conn.execute(
            "SELECT fetch_date, status, new_items FROM fetch_log ORDER BY fetch_date DESC LIMIT 1"
        ).fetchone()

        return {
            "status": "ok",
            "total_announcements": total,
            "by_market": by_market,
            "last_fetch": {
                "date": str(status_row[0]) if status_row else None,
                "status": str(status_row[1]) if status_row else None,
                "new_items": int(status_row[2]) if status_row else 0,
            },
        }
    finally:
        conn.close()


# ── Announcements List ─────────────────────────────────────────

@app.get("/api/v1/announcements")
def list_announcements(
    ticker: Optional[str] = Query(None, description="Filter by ticker"),
    market: Optional[str] = Query(None, description="Filter by market (us, hk, cn)"),
    source: Optional[str] = Query(None, description="Filter by source (sec, hkex, cninfo)"),
    start: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    end: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
    limit: int = Query(100, ge=1, le=500, description="Max results"),
):
    """List announcements with optional filters. Excludes text_content for performance."""
    conn = init_db(read_only=True)
    try:
        df = query_announcements(
            conn, ticker=ticker, market=market, source=source,
            start=start, end=end, limit=limit,
        )
        records = df.to_dict(orient="records")
        for r in records:
            _serialize_record(r)

        return {"count": len(records), "announcements": records}
    finally:
        conn.close()


# ── Announcement Detail ────────────────────────────────────────

@app.get("/api/v1/announcements/{ann_id}")
def announcement_detail(ann_id: int):
    """Get single announcement with full text_content."""
    conn = init_db(read_only=True)
    try:
        record = get_announcement_by_id(conn, ann_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Announcement {ann_id} not found")
        _serialize_record(record)
        return record
    finally:
        conn.close()


# ── Companies ──────────────────────────────────────────────────

@app.get("/api/v1/companies")
def tracked_companies():
    """List tracked companies with announcement counts."""
    conn = init_db(read_only=True)
    try:
        companies = get_tracked_companies(conn)
        return {"companies": companies}
    finally:
        conn.close()


# ── Dates ──────────────────────────────────────────────────────

@app.get("/api/v1/dates")
def available_dates(limit: int = Query(30, le=60)):
    """Get list of available announcement dates."""
    conn = init_db(read_only=True)
    try:
        dates = get_announcement_dates(conn, limit=limit)
        return {"count": len(dates), "dates": dates}
    finally:
        conn.close()


# ── Fetch ──────────────────────────────────────────────────────

@app.post("/api/v1/fetch")
def trigger_fetch():
    """Manually trigger a daily fetch for all tracked companies."""
    result = fetch_daily()
    return {"status": "completed", "result": result}


# ── Fetch Status ───────────────────────────────────────────────

@app.get("/api/v1/fetch/status")
def fetch_status(days: int = Query(7, le=30)):
    """Get recent fetch log entries."""
    conn = init_db(read_only=True)
    try:
        df = get_fetch_status(conn, days=days)
        records = df.to_dict(orient="records")
        for r in records:
            _serialize_record(r)
        return {"count": len(records), "logs": records}
    finally:
        conn.close()


# ── Startup ────────────────────────────────────────────────────

@app.on_event("startup")
def startup():
    init_db()
    logger.info("Company Announcements API started")
