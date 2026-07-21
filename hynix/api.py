"""
FastAPI REST API — SK Hynix cross-market arbitrage data.

Usage:
    python -m uvicorn hynix.api:app --host 127.0.0.1 --port 8008

Endpoints:
    GET  /api/v1/health              — Service health
    GET  /api/v1/instruments          — List tracked instruments
    GET  /api/v1/arbitrage/latest     — Latest arbitrage snapshot
    GET  /api/v1/arbitrage/{date}     — Arbitrage for a specific date
    GET  /api/v1/arbitrage/{ticker}/history — Premium history for an instrument
    GET  /api/v1/prices/{ticker}      — Price history for an instrument
    GET  /api/v1/fx/latest            — Latest FX rates
    GET  /api/v1/fx/history           — FX rate history
    GET  /api/v1/dates                — Available trading dates
    POST /api/v1/fetch                — Trigger daily fetch
    POST /api/v1/init                 — Full init with backfill
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from hynix.storage import (
    init_db,
    get_counts,
    get_instruments,
    get_daily_prices,
    get_prices_for_date,
    get_fx_rates,
    get_fx_history,
    get_arbitrage,
    get_arbitrage_history,
    get_available_dates,
    get_latest_summary,
    get_fetch_status,
)
from hynix.pipeline import (
    fetch_daily,
    fetch_latest,
    init_pipeline,
)
from hynix.kimpremium import fetch_and_store as kimpremium_fetch
from hynix.storage import (
    get_kr_leverage_latest,
    get_kr_leverage_series,
    get_kr_leverage_etf,
    get_kr_leverage_full_snapshot,
)

logger = logging.getLogger("hynix.api")

app = FastAPI(
    title="SK Hynix Cross-Market API",
    description="SK Hynix arbitrage comparison across KR stock, US ADR, HK ETP, and KR ETFs",
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
    """Convert date/time objects to ISO strings and NaN to None."""
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


# ── Instruments ────────────────────────────────────────────────

@app.get("/api/v1/instruments")
def list_instruments(
    market: Optional[str] = Query(None, description="Filter by market: KR, US, HK"),
):
    conn = init_db(read_only=True)
    try:
        df = get_instruments(conn, market=market)
        records = df.to_dict(orient="records")
        _serialize_records(records)
        return {"count": len(records), "instruments": records}
    finally:
        conn.close()


# ── Arbitrage ──────────────────────────────────────────────────

@app.get("/api/v1/arbitrage/latest")
def latest_arbitrage():
    """Get the latest cross-market arbitrage snapshot."""
    conn = init_db(read_only=True)
    try:
        summary = get_latest_summary(conn)
        if summary is None:
            raise HTTPException(status_code=404, detail="No arbitrage data available")
        _serialize_records(summary["instruments"])
        return summary
    finally:
        conn.close()


@app.get("/api/v1/arbitrage/{date}")
def arbitrage_by_date(date: str):
    """Get arbitrage comparison for a specific date."""
    conn = init_db(read_only=True)
    try:
        df = get_arbitrage(conn, date)
        if df.empty:
            raise HTTPException(status_code=404, detail=f"No arbitrage data for {date}")
        fx = get_fx_rates(conn, date)
        records = df.to_dict(orient="records")
        _serialize_records(records)
        base_price = df.iloc[0]["base_price_krw"] if not df.empty else None
        return {
            "date": date,
            "base_ticker": "000660.KS",
            "base_price_krw": base_price,
            "fx_rates": fx,
            "instruments": records,
        }
    finally:
        conn.close()


@app.get("/api/v1/arbitrage/{ticker}/history")
def arbitrage_history(
    ticker: str,
    start: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    end: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
    limit: int = Query(60, ge=1, le=200),
):
    """Get premium/discount time series for a specific instrument."""
    conn = init_db(read_only=True)
    try:
        df = get_arbitrage_history(conn, ticker, start=start, end=end, limit=limit)
        if df.empty:
            raise HTTPException(status_code=404, detail=f"No arbitrage history for {ticker}")
        records = df.to_dict(orient="records")
        _serialize_records(records)
        return {"ticker": ticker, "count": len(records), "history": records}
    finally:
        conn.close()


# ── Prices ─────────────────────────────────────────────────────

@app.get("/api/v1/prices/{ticker}")
def price_history(
    ticker: str,
    start: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    end: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
    limit: int = Query(60, ge=1, le=200),
):
    """Get price history for an instrument."""
    conn = init_db(read_only=True)
    try:
        df = get_daily_prices(conn, ticker, start=start, end=end, limit=limit)
        if df.empty:
            raise HTTPException(status_code=404, detail=f"No price data for {ticker}")
        records = df.to_dict(orient="records")
        _serialize_records(records)
        return {"ticker": ticker, "count": len(records), "prices": records}
    finally:
        conn.close()


@app.get("/api/v1/prices")
def prices_by_date(date: str = Query(..., description="Date YYYY-MM-DD")):
    """Get all instrument prices for a given date."""
    conn = init_db(read_only=True)
    try:
        df = get_prices_for_date(conn, date)
        if df.empty:
            raise HTTPException(status_code=404, detail=f"No price data for {date}")
        records = df.to_dict(orient="records")
        _serialize_records(records)
        return {"date": date, "count": len(records), "prices": records}
    finally:
        conn.close()


# ── FX Rates ──────────────────────────────────────────────────

@app.get("/api/v1/fx/latest")
def latest_fx():
    """Get the latest FX rates."""
    conn = init_db(read_only=True)
    try:
        latest_row = conn.execute(
            "SELECT MAX(date) FROM hynix_fx_rates"
        ).fetchone()
        if latest_row is None or latest_row[0] is None:
            raise HTTPException(status_code=404, detail="No FX data available")
        latest_date = str(latest_row[0])
        fx = get_fx_rates(conn, latest_date)
        return {"date": latest_date, "rates": fx}
    finally:
        conn.close()


@app.get("/api/v1/fx/history")
def fx_history(
    from_ccy: str = Query("USD", description="Source currency (USD, HKD)"),
    to_ccy: str = Query("KRW", description="Target currency (KRW)"),
    limit: int = Query(60, ge=1, le=200),
):
    """Get FX rate history."""
    conn = init_db(read_only=True)
    try:
        df = get_fx_history(conn, from_ccy=from_ccy, to_ccy=to_ccy, limit=limit)
        if df.empty:
            raise HTTPException(status_code=404, detail=f"No FX history for {from_ccy}/{to_ccy}")
        records = df.to_dict(orient="records")
        _serialize_records(records)
        return {"from": from_ccy, "to": to_ccy, "count": len(records), "history": records}
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


# ── Korean Retail Leverage (kimpremium.com) ───────────────────

@app.get("/api/v1/kr-leverage/summary")
def kr_leverage_summary():
    """Get the latest Korean retail leverage KPI snapshot."""
    conn = init_db(read_only=True)
    try:
        result = get_kr_leverage_latest(conn)
        if result is None:
            raise HTTPException(status_code=404, detail="No leverage data available")
        return result
    finally:
        conn.close()


@app.get("/api/v1/kr-leverage/snapshot")
def kr_leverage_snapshot(date: Optional[str] = Query(None, description="Date YYYY-MM-DD, omit for latest")):
    """Get full snapshot for a date: daily indicators + ETF data + meta."""
    conn = init_db(read_only=True)
    try:
        result = get_kr_leverage_full_snapshot(conn, date)
        if result is None:
            raise HTTPException(status_code=404, detail=f"No data for {date or 'latest'}")
        return result
    finally:
        conn.close()


@app.get("/api/v1/kr-leverage/series")
def kr_leverage_series(
    indicator: str = Query("r2", description="Indicator: r2, p10, kospi, spx, fin, dep, liq, mg, util, ..."),
    start: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    end: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
    limit: int = Query(500, ge=1, le=2000),
):
    """Get a single-indicator time series from the daily leverage table."""
    conn = init_db(read_only=True)
    try:
        df = get_kr_leverage_series(conn, indicator=indicator, start=start, end=end, limit=limit)
        if df.empty:
            raise HTTPException(status_code=404, detail=f"No data for indicator '{indicator}'")
        records = df.to_dict(orient="records")
        _serialize_records(records)
        return {"indicator": indicator, "count": len(records), "data": records}
    finally:
        conn.close()


@app.get("/api/v1/kr-leverage/etf")
def kr_leverage_etf_series(
    indicator: str = Query("thermo", description="Indicator: thermo, thermoW, flow, flowW, cumFlow, cumFlowW"),
    start: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    end: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
    limit: int = Query(500, ge=1, le=2000),
):
    """Get a single-indicator time series from the ETF daily table."""
    conn = init_db(read_only=True)
    try:
        df = get_kr_leverage_etf(conn, indicator=indicator, start=start, end=end, limit=limit)
        if df.empty:
            raise HTTPException(status_code=404, detail=f"No ETF data for indicator '{indicator}'")
        records = df.to_dict(orient="records")
        _serialize_records(records)
        return {"indicator": indicator, "count": len(records), "data": records}
    finally:
        conn.close()


@app.post("/api/v1/kr-leverage/fetch")
def trigger_kimpremium_fetch():
    """Fetch latest Korean retail leverage data from kimpremium.com."""
    result = kimpremium_fetch()
    return {"status": "completed", "result": result}


@app.get("/api/v1/kr-leverage/dump")
def kr_leverage_dump(
    start: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    end: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
):
    """Get all indicators at once for charting. Returns daily + ETF data as parallel arrays."""
    conn = init_db(read_only=True)
    try:
        daily_rows = conn.execute("""
            SELECT * FROM kr_leverage_daily
            WHERE (? IS NULL OR date >= ?) AND (? IS NULL OR date <= ?)
            ORDER BY date
        """, [start, start, end, end]).fetchall()

        etf_rows = conn.execute("""
            SELECT * FROM kr_leverage_etf_daily
            WHERE (? IS NULL OR date >= ?) AND (? IS NULL OR date <= ?)
            ORDER BY date
        """, [start, start, end, end]).fetchall()

        import math

        def build(rows, col_names):
            result = {}
            arrays = {}
            for row in rows:
                d = str(row[0]) if hasattr(row[0], "isoformat") else str(row[0])
                if "dates" not in result:
                    result["dates"] = []
                result["dates"].append(d)
                for j, col in enumerate(col_names):
                    if col == "date":
                        continue
                    if col not in arrays:
                        arrays[col] = []
                    val = row[j]
                    if val is None or (isinstance(val, float) and (math.isnan(val) or math.isinf(val))):
                        arrays[col].append(None)
                    else:
                        arrays[col].append(val)
            result["series"] = arrays
            return result

        daily_cols = [desc[0] for desc in conn.description]
        etf_cols = [desc[0] for desc in conn.description] if etf_rows else ["date"]

        result = build(daily_rows, daily_cols)
        etf_result = build(etf_rows, etf_cols)
        result["etf_series"] = etf_result.get("series", {})

        return result
    finally:
        conn.close()


# ── Fetch Triggers ────────────────────────────────────────────

@app.post("/api/v1/fetch")
def trigger_fetch(date: Optional[str] = None):
    """Trigger daily fetch (defaults to latest trading day)."""
    if date:
        result = fetch_daily(date)
    else:
        result = fetch_latest()
    return {"status": "completed", "result": result}


@app.post("/api/v1/init")
def trigger_init(lookback: int = Query(90, ge=7, le=365)):
    """Full init: seed instruments + backfill."""
    result = init_pipeline(lookback_days=lookback)
    return {"status": "completed", "result": result}


# ── Startup ───────────────────────────────────────────────────

@app.on_event("startup")
def startup():
    init_db()
    logger.info("SK Hynix Cross-Market API started")
