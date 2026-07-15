"""
FastAPI REST API — serve US new listings, crypto products, and expanded equities data.

Usage:
    python -m uvicorn us_listings.api:app --host 127.0.0.1 --port 8003

Endpoints:
    GET  /api/v1/health              — Service health
    GET  /api/v1/listings            — New listings (filters: date range, type, crypto)
    GET  /api/v1/listings/{ticker}   — Single listing details
    GET  /api/v1/listings/upcoming   — Upcoming IPOs
    GET  /api/v1/summary             — New listings summary stats
    GET  /api/v1/dates               — Available listing dates
    GET  /api/v1/crypto              — All crypto products (filters: type, asset)
    GET  /api/v1/crypto/{ticker}     — Single crypto product details
    GET  /api/v1/crypto/stats        — Crypto product statistics
    GET  /api/v1/crypto/recent       — Recently added crypto products
    GET  /api/v1/insider             — Insider trades (Form 4)
    GET  /api/v1/insider/{ticker}    — Insider trades for a ticker
    GET  /api/v1/earnings            — Earnings calendar (10-K/10-Q)
    GET  /api/v1/earnings/upcoming   — Upcoming earnings
    GET  /api/v1/holdings            — Institutional holdings (13F)
    GET  /api/v1/holdings/{ticker}   — Holdings for a ticker
    GET  /api/v1/short-interest      — Short interest data
    GET  /api/v1/ftd                 — Fails-to-deliver (SEC Reg SHO)
    GET  /api/v1/flows               — ETF daily flows
    POST /api/v1/fetch               — Trigger listings fetch
    POST /api/v1/fetch-crypto        — Trigger crypto full refresh
    POST /api/v1/fetch-insider       — Trigger Form 4 fetch
    POST /api/v1/fetch-earnings      — Trigger earnings fetch
    POST /api/v1/fetch-holdings      — Trigger 13F fetch
    POST /api/v1/fetch-risk          — Trigger risk data fetch
    POST /api/v1/fetch-flows         — Trigger ETF flows fetch
    GET  /api/v1/dividends           — Dividend calendar
    GET  /api/v1/splits              — Stock split history
    GET  /api/v1/suspensions         — Trading suspensions (Form 34)
    GET  /api/v1/enforcement         — SEC enforcement actions (AAER/LR/AP)
    GET  /api/v1/threshold           — Reg SHO threshold securities
    GET  /api/v1/ats                 — ATS / dark pool filings (ATS-N)
    GET  /api/v1/short-activity      — Enhanced short sale signals
    GET  /api/v1/lockup              — IPO lockup expiry tracker
    GET  /api/v1/options-flow        — Options flow (unusual activity)
    POST /api/v1/fetch-corporate-events — Trigger dividends + splits
    POST /api/v1/fetch-suspensions   — Trigger suspensions fetch
    POST /api/v1/fetch-enforcement   — Trigger enforcement fetch
    POST /api/v1/fetch-threshold     — Trigger threshold fetch
    POST /api/v1/fetch-ats           — Trigger ATS fetch
    POST /api/v1/fetch-short-activity — Trigger short activity fetch
    POST /api/v1/fetch-lockup        — Trigger lockup fetch
    POST /api/v1/fetch-options       — Trigger options flow fetch
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from us_listings.storage import (
    init_db,
    get_listings,
    get_listing_by_ticker,
    get_upcoming_listings,
    get_listing_summary,
    get_available_listing_dates,
    get_monthly_listing_counts,
    get_all_crypto_products,
    get_crypto_product_by_ticker,
    get_crypto_stats,
    get_crypto_new_additions,
    get_listing_count,
    get_crypto_product_count,
    get_fetch_status,
    mark_listings_as_crypto,
    get_insider_trades,
    get_earnings,
    get_upcoming_earnings,
    get_holdings,
    get_short_interest,
    get_ftd,
    get_etf_flows,
    get_dividends,
    get_splits,
    get_suspensions,
    get_enforcement,
    get_threshold_securities,
    get_ats_filings,
    get_short_activity,
    get_lockup_expiry,
    get_options_flow,
)
from us_listings.pipeline import fetch_listings_for_month
from us_listings.crypto_pipeline import full_refresh, enrich_all_products, scan_sec_tickers_for_crypto
from us_listings.insider_pipeline import fetch_insider_daily
from us_listings.earnings_pipeline import fetch_earnings_daily
from us_listings.holdings_pipeline import fetch_holdings_daily
from us_listings.risk_pipeline import fetch_risk_daily
from us_listings.flow_pipeline import fetch_flows_daily
from us_listings.corporate_events_pipeline import fetch_corporate_events_daily
from us_listings.suspension_pipeline import fetch_suspension_daily
from us_listings.enforcement_pipeline import fetch_enforcement_daily
from us_listings.threshold_pipeline import fetch_threshold_daily
from us_listings.ats_pipeline import fetch_ats_daily
from us_listings.short_sale_pipeline import fetch_short_sale_daily
from us_listings.lockup_pipeline import fetch_lockup_daily
from us_listings.options_flow_pipeline import fetch_options_flow_daily

logger = logging.getLogger("us_listings.api")

app = FastAPI(
    title="US Listings & Crypto Products API",
    description="美股新上市追踪 + Crypto 产品全量清单",
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
    """Convert date/time objects to ISO strings in a list of dicts. Replace NaN/Inf/NaT with None."""
    import math
    import pandas as pd
    for r in records:
        for k, v in r.items():
            if hasattr(v, "isoformat"):
                s = v.isoformat()
                r[k] = None if s == "NaT" else s
            elif isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                r[k] = None
            elif v is pd.NaT:
                r[k] = None
    return records


# ═══════════════════════════════════════════════════════════════
#  Health
# ═══════════════════════════════════════════════════════════════

@app.get("/api/v1/health")
def health():
    conn = init_db()
    try:
        listings_count = get_listing_count(conn)
        crypto_count = get_crypto_product_count(conn)

        status_row = conn.execute(
            "SELECT fetch_date, source, status FROM fetch_log ORDER BY fetch_date DESC LIMIT 1"
        ).fetchone()

        return {
            "status": "ok",
            "total_listings": listings_count,
            "crypto_products": crypto_count,
            "last_fetch": {
                "date": str(status_row[0]) if status_row else None,
                "source": str(status_row[1]) if status_row else None,
                "status": str(status_row[2]) if status_row else None,
            },
        }
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════
#  New Listings
# ═══════════════════════════════════════════════════════════════

@app.get("/api/v1/listings")
def list_listings(
    start: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    end: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
    listing_type: Optional[str] = Query(None, description="IPO, Direct Listing, SPAC, Upcoming"),
    exchange: Optional[str] = Query(None, description="NYSE, NASDAQ"),
    is_crypto: Optional[bool] = Query(None, description="Filter crypto-related only"),
    limit: int = Query(100, ge=1, le=500),
):
    """List new listings with optional filters."""
    conn = init_db(read_only=True)
    try:
        df = get_listings(conn, start, end, listing_type, exchange, is_crypto, limit)
        records = _serialize_records(df.to_dict(orient="records"))
        return {"count": len(records), "listings": records}
    finally:
        conn.close()


@app.get("/api/v1/listings/upcoming")
def upcoming_listings():
    """Get upcoming IPOs (listing_date >= today)."""
    conn = init_db(read_only=True)
    try:
        df = get_upcoming_listings(conn)
        records = _serialize_records(df.to_dict(orient="records"))
        return {"count": len(records), "listings": records}
    finally:
        conn.close()


@app.get("/api/v1/listings/{ticker}")
def listing_detail(ticker: str):
    """Get listing details for a specific ticker."""
    conn = init_db(read_only=True)
    try:
        listing = get_listing_by_ticker(conn, ticker)
        if not listing:
            raise HTTPException(status_code=404, detail=f"No listing for {ticker}")
        return _serialize_records([listing])[0]
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════
#  Summary & Dates
# ═══════════════════════════════════════════════════════════════

@app.get("/api/v1/summary")
def listing_summary(
    start: str = Query(..., description="Start date YYYY-MM-DD"),
    end: str = Query(..., description="End date YYYY-MM-DD"),
):
    """Get listing summary statistics for a date range."""
    conn = init_db(read_only=True)
    try:
        summary = get_listing_summary(conn, start, end)

        # Add monthly breakdown
        monthly_df = get_monthly_listing_counts(conn, months=12)
        monthly_records = _serialize_records(monthly_df.to_dict(orient="records"))

        summary["monthly"] = monthly_records
        return summary
    finally:
        conn.close()


@app.get("/api/v1/dates")
def available_dates(limit: int = Query(60, le=120)):
    """Get list of available listing dates."""
    conn = init_db(read_only=True)
    try:
        dates = get_available_listing_dates(conn, limit=limit)
        return {"count": len(dates), "dates": dates}
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════
#  Crypto Products
# ═══════════════════════════════════════════════════════════════

@app.get("/api/v1/crypto")
def list_crypto_products(
    product_type: Optional[str] = Query(None, description="spot_etf, futures_etf, etp, crypto_stock, blockchain"),
    underlying_asset: Optional[str] = Query(None, description="Bitcoin, Ethereum, Multi-asset, etc."),
    active_only: bool = Query(True, description="Only show active products"),
):
    """List all crypto products with optional filters."""
    conn = init_db(read_only=True)
    try:
        df = get_all_crypto_products(conn, product_type, underlying_asset, active_only)
        records = _serialize_records(df.to_dict(orient="records"))
        return {"count": len(records), "products": records}
    finally:
        conn.close()


@app.get("/api/v1/crypto/stats")
def crypto_statistics():
    """Get crypto product statistics (by type, asset)."""
    conn = init_db(read_only=True)
    try:
        stats = get_crypto_stats(conn)
        return stats
    finally:
        conn.close()


@app.get("/api/v1/crypto/recent")
def crypto_recent_additions(days: int = Query(30, ge=7, le=90)):
    """Get recently added crypto products."""
    conn = init_db(read_only=True)
    try:
        df = get_crypto_new_additions(conn, days=days)
        records = _serialize_records(df.to_dict(orient="records"))
        return {"count": len(records), "products": records}
    finally:
        conn.close()


@app.get("/api/v1/crypto/{ticker}")
def crypto_detail(ticker: str):
    """Get detailed info for a single crypto product."""
    conn = init_db(read_only=True)
    try:
        product = get_crypto_product_by_ticker(conn, ticker)
        if not product:
            raise HTTPException(status_code=404, detail=f"No crypto product {ticker}")
        return _serialize_records([product])[0]
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════
#  Fetch Triggers
# ═══════════════════════════════════════════════════════════════

@app.post("/api/v1/fetch")
def trigger_fetch(month: Optional[str] = None):
    """Manually trigger new listings fetch (defaults to current month)."""
    result = fetch_listings_for_month(month)
    return {"status": "completed", "result": result}


@app.post("/api/v1/fetch-crypto")
def trigger_crypto_fetch(action: str = Query("refresh", description="refresh, enrich, scan")):
    """Manually trigger crypto products update."""
    if action == "enrich":
        result = enrich_all_products()
    elif action == "scan":
        products = scan_sec_tickers_for_crypto()
        result = {"new_products": len(products)}
    else:
        result = full_refresh()
    return {"status": "completed", "action": action, "result": result}


# ═══════════════════════════════════════════════════════════════
#  Fetch Status
# ═══════════════════════════════════════════════════════════════

@app.get("/api/v1/fetch/status")
def fetch_status(days: int = Query(7, le=30)):
    """Get recent fetch log entries."""
    conn = init_db(read_only=True)
    try:
        df = get_fetch_status(conn, days=days)
        records = _serialize_records(df.to_dict(orient="records"))
        return {"count": len(records), "logs": records}
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════
#  Insider Trades
# ═══════════════════════════════════════════════════════════════

@app.get("/api/v1/insider")
def list_insider_trades(
    ticker: Optional[str] = Query(None, description="Filter by ticker"),
    start: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    end: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
    limit: int = Query(100, ge=1, le=500),
):
    """List insider trades (Form 4 filings)."""
    conn = init_db(read_only=True)
    try:
        df = get_insider_trades(conn, ticker=ticker, start_date=start, end_date=end, limit=limit)
        records = _serialize_records(df.to_dict(orient="records"))
        return {"count": len(records), "trades": records}
    finally:
        conn.close()


@app.get("/api/v1/insider/{ticker}")
def insider_ticker_detail(ticker: str):
    """Get insider trading history for a specific ticker."""
    conn = init_db(read_only=True)
    try:
        df = get_insider_trades(conn, ticker=ticker, limit=200)
        records = _serialize_records(df.to_dict(orient="records"))
        if not records:
            raise HTTPException(status_code=404, detail=f"No insider trades for {ticker}")
        return {"count": len(records), "ticker": ticker.upper(), "trades": records}
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════
#  Earnings Calendar
# ═══════════════════════════════════════════════════════════════

@app.get("/api/v1/earnings")
def list_earnings(
    ticker: Optional[str] = Query(None, description="Filter by ticker"),
    start: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    end: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
    report_type: Optional[str] = Query(None, description="10-K or 10-Q"),
    limit: int = Query(100, ge=1, le=500),
):
    """List earnings calendar (10-K/10-Q filings)."""
    conn = init_db(read_only=True)
    try:
        df = get_earnings(conn, ticker=ticker, start_date=start, end_date=end,
                          report_type=report_type, limit=limit)
        records = _serialize_records(df.to_dict(orient="records"))
        return {"count": len(records), "earnings": records}
    finally:
        conn.close()


@app.get("/api/v1/earnings/upcoming")
def upcoming_earnings(limit: int = Query(50, ge=1, le=200)):
    """Get upcoming earnings (filing_date >= today)."""
    conn = init_db(read_only=True)
    try:
        df = get_upcoming_earnings(conn, limit=limit)
        records = _serialize_records(df.to_dict(orient="records"))
        return {"count": len(records), "earnings": records}
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════
#  Institutional Holdings (13F)
# ═══════════════════════════════════════════════════════════════

@app.get("/api/v1/holdings")
def list_holdings(
    ticker: Optional[str] = Query(None, description="Filter by ticker"),
    filer_cik: Optional[str] = Query(None, description="Filter by filer CIK"),
    quarter_end: Optional[str] = Query(None, description="Quarter end date YYYY-MM-DD"),
    limit: int = Query(100, ge=1, le=500),
):
    """List institutional holdings (13F filings)."""
    conn = init_db(read_only=True)
    try:
        df = get_holdings(conn, ticker=ticker, filer_cik=filer_cik,
                         quarter_end=quarter_end, limit=limit)
        records = _serialize_records(df.to_dict(orient="records"))
        return {"count": len(records), "holdings": records}
    finally:
        conn.close()


@app.get("/api/v1/holdings/{ticker}")
def holdings_ticker_detail(ticker: str):
    """Get institutional holdings history for a specific ticker."""
    conn = init_db(read_only=True)
    try:
        df = get_holdings(conn, ticker=ticker, limit=200)
        records = _serialize_records(df.to_dict(orient="records"))
        if not records:
            raise HTTPException(status_code=404, detail=f"No holdings for {ticker}")
        return {"count": len(records), "ticker": ticker.upper(), "holdings": records}
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════
#  Short Interest & Fails-to-Deliver (Risk)
# ═══════════════════════════════════════════════════════════════

@app.get("/api/v1/short-interest")
def list_short_interest(
    ticker: Optional[str] = Query(None, description="Filter by ticker"),
    limit: int = Query(50, ge=1, le=200),
):
    """List short interest data."""
    conn = init_db(read_only=True)
    try:
        df = get_short_interest(conn, ticker=ticker, limit=limit)
        records = _serialize_records(df.to_dict(orient="records"))
        return {"count": len(records), "short_interest": records}
    finally:
        conn.close()


@app.get("/api/v1/ftd")
def list_ftd(
    ticker: Optional[str] = Query(None, description="Filter by ticker"),
    start: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    end: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
    limit: int = Query(100, ge=1, le=500),
):
    """List fails-to-deliver data (SEC Reg SHO)."""
    conn = init_db(read_only=True)
    try:
        df = get_ftd(conn, ticker=ticker, start_date=start, end_date=end, limit=limit)
        records = _serialize_records(df.to_dict(orient="records"))
        return {"count": len(records), "ftd": records}
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════
#  ETF Flows
# ═══════════════════════════════════════════════════════════════

@app.get("/api/v1/flows")
def list_etf_flows(
    ticker: Optional[str] = Query(None, description="Filter by ticker"),
    start: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    end: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
    limit: int = Query(30, ge=1, le=200),
):
    """List crypto ETF daily flows."""
    conn = init_db(read_only=True)
    try:
        df = get_etf_flows(conn, ticker=ticker, start_date=start, end_date=end, limit=limit)
        records = _serialize_records(df.to_dict(orient="records"))
        return {"count": len(records), "flows": records}
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════
#  Fetch Triggers (new data types)
# ═══════════════════════════════════════════════════════════════

@app.post("/api/v1/fetch-insider")
def trigger_insider_fetch(date: Optional[str] = None):
    """Manually trigger insider trading (Form 4) fetch."""
    result = fetch_insider_daily(date)
    return {"status": "completed", "result": result}


@app.post("/api/v1/fetch-earnings")
def trigger_earnings_fetch(date: Optional[str] = None):
    """Manually trigger earnings calendar (10-K/10-Q) fetch."""
    result = fetch_earnings_daily(date)
    return {"status": "completed", "result": result}


@app.post("/api/v1/fetch-holdings")
def trigger_holdings_fetch():
    """Manually trigger institutional holdings (13F) fetch."""
    result = fetch_holdings_daily()
    return {"status": "completed", "result": result}


@app.post("/api/v1/fetch-risk")
def trigger_risk_fetch():
    """Manually trigger risk data (short interest + FTD) fetch."""
    result = fetch_risk_daily()
    return {"status": "completed", "result": result}


@app.post("/api/v1/fetch-flows")
def trigger_flows_fetch():
    """Manually trigger ETF flows fetch."""
    result = fetch_flows_daily()
    return {"status": "completed", "result": result}


# ═══════════════════════════════════════════════════════════════
#  Dividends & Stock Splits
# ═══════════════════════════════════════════════════════════════

@app.get("/api/v1/dividends")
def list_dividends(
    ticker: Optional[str] = Query(None, description="Filter by ticker"),
    start: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    end: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
    limit: int = Query(100, ge=1, le=500),
):
    """List dividend calendar."""
    conn = init_db(read_only=True)
    try:
        df = get_dividends(conn, ticker=ticker, start_date=start, end_date=end, limit=limit)
        records = _serialize_records(df.to_dict(orient="records"))
        return {"count": len(records), "dividends": records}
    finally:
        conn.close()


@app.get("/api/v1/splits")
def list_splits(
    ticker: Optional[str] = Query(None, description="Filter by ticker"),
    limit: int = Query(50, ge=1, le=200),
):
    """List stock split history."""
    conn = init_db(read_only=True)
    try:
        df = get_splits(conn, ticker=ticker, limit=limit)
        records = _serialize_records(df.to_dict(orient="records"))
        return {"count": len(records), "splits": records}
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════
#  Trading Suspensions
# ═══════════════════════════════════════════════════════════════

@app.get("/api/v1/suspensions")
def list_suspensions(
    ticker: Optional[str] = Query(None, description="Filter by ticker"),
    start: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    end: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
    limit: int = Query(100, ge=1, le=500),
):
    """List trading suspensions (SEC Form 34)."""
    conn = init_db(read_only=True)
    try:
        df = get_suspensions(conn, ticker=ticker, start_date=start, end_date=end, limit=limit)
        records = _serialize_records(df.to_dict(orient="records"))
        return {"count": len(records), "suspensions": records}
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════
#  SEC Enforcement Actions
# ═══════════════════════════════════════════════════════════════

@app.get("/api/v1/enforcement")
def list_enforcement(
    enforcement_type: Optional[str] = Query(None, description="AAER, LR, AP"),
    start: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    end: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
    limit: int = Query(100, ge=1, le=500),
):
    """List SEC enforcement actions (AAER, Litigation Releases, Admin Proceedings)."""
    conn = init_db(read_only=True)
    try:
        df = get_enforcement(conn, enforcement_type=enforcement_type, start_date=start, end_date=end, limit=limit)
        records = _serialize_records(df.to_dict(orient="records"))
        return {"count": len(records), "enforcement": records}
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════
#  Threshold Securities
# ═══════════════════════════════════════════════════════════════

@app.get("/api/v1/threshold")
def list_threshold_securities(
    ticker: Optional[str] = Query(None, description="Filter by ticker"),
    date: Optional[str] = Query(None, description="Filter by date YYYY-MM-DD"),
    limit: int = Query(100, ge=1, le=500),
):
    """List Reg SHO threshold securities."""
    conn = init_db(read_only=True)
    try:
        df = get_threshold_securities(conn, ticker=ticker, date=date, limit=limit)
        records = _serialize_records(df.to_dict(orient="records"))
        return {"count": len(records), "threshold": records}
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════
#  ATS / Dark Pool
# ═══════════════════════════════════════════════════════════════

@app.get("/api/v1/ats")
def list_ats_filings(
    filer_cik: Optional[str] = Query(None, description="Filter by filer CIK"),
    start: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    end: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
    limit: int = Query(50, ge=1, le=200),
):
    """List ATS / dark pool filings (SEC Form ATS-N)."""
    conn = init_db(read_only=True)
    try:
        df = get_ats_filings(conn, filer_cik=filer_cik, start_date=start, end_date=end, limit=limit)
        records = _serialize_records(df.to_dict(orient="records"))
        return {"count": len(records), "ats": records}
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════
#  Short Sale Activity (enhanced)
# ═══════════════════════════════════════════════════════════════

@app.get("/api/v1/short-activity")
def list_short_activity(
    ticker: Optional[str] = Query(None, description="Filter by ticker"),
    risk_level: Optional[str] = Query(None, description="normal, elevated, high, extreme"),
    limit: int = Query(50, ge=1, le=200),
):
    """List enhanced short sale activity with risk signals."""
    conn = init_db(read_only=True)
    try:
        df = get_short_activity(conn, ticker=ticker, risk_level=risk_level, limit=limit)
        records = _serialize_records(df.to_dict(orient="records"))
        return {"count": len(records), "short_activity": records}
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════
#  IPO Lockup Expiry
# ═══════════════════════════════════════════════════════════════

@app.get("/api/v1/lockup")
def list_lockup_expiry(
    ticker: Optional[str] = Query(None, description="Filter by ticker"),
    status: Optional[str] = Query("active", description="active or expired"),
    limit: int = Query(50, ge=1, le=200),
):
    """List IPO lockup expiry dates."""
    conn = init_db(read_only=True)
    try:
        df = get_lockup_expiry(conn, ticker=ticker, status=status, limit=limit)
        records = _serialize_records(df.to_dict(orient="records"))
        return {"count": len(records), "lockups": records}
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════
#  Options Flow
# ═══════════════════════════════════════════════════════════════

@app.get("/api/v1/options-flow")
def list_options_flow(
    ticker: Optional[str] = Query(None, description="Filter by ticker"),
    unusual_only: bool = Query(False, description="Only show unusual activity"),
    limit: int = Query(50, ge=1, le=200),
):
    """List options flow data with unusual activity detection."""
    conn = init_db(read_only=True)
    try:
        df = get_options_flow(conn, ticker=ticker, is_unusual=True if unusual_only else None, limit=limit)
        records = _serialize_records(df.to_dict(orient="records"))
        return {"count": len(records), "options_flow": records}
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════
#  Fetch Triggers (round 2)
# ═══════════════════════════════════════════════════════════════

@app.post("/api/v1/fetch-corporate-events")
def trigger_corporate_events_fetch():
    """Manually trigger dividends + stock splits fetch."""
    result = fetch_corporate_events_daily()
    return {"status": "completed", "result": result}


@app.post("/api/v1/fetch-suspensions")
def trigger_suspensions_fetch(date: Optional[str] = None):
    """Manually trigger trading suspensions fetch."""
    result = fetch_suspension_daily(date)
    return {"status": "completed", "result": result}


@app.post("/api/v1/fetch-enforcement")
def trigger_enforcement_fetch(date: Optional[str] = None):
    """Manually trigger SEC enforcement actions fetch."""
    result = fetch_enforcement_daily(date)
    return {"status": "completed", "result": result}


@app.post("/api/v1/fetch-threshold")
def trigger_threshold_fetch(date: Optional[str] = None):
    """Manually trigger threshold securities fetch."""
    result = fetch_threshold_daily(date)
    return {"status": "completed", "result": result}


@app.post("/api/v1/fetch-ats")
def trigger_ats_fetch(date: Optional[str] = None):
    """Manually trigger ATS/dark pool filings fetch."""
    result = fetch_ats_daily(date)
    return {"status": "completed", "result": result}


@app.post("/api/v1/fetch-short-activity")
def trigger_short_activity_fetch():
    """Manually trigger enhanced short sale activity fetch."""
    result = fetch_short_sale_daily()
    return {"status": "completed", "result": result}


@app.post("/api/v1/fetch-lockup")
def trigger_lockup_fetch():
    """Manually trigger IPO lockup expiry computation."""
    result = fetch_lockup_daily()
    return {"status": "completed", "result": result}


@app.post("/api/v1/fetch-options")
def trigger_options_fetch():
    """Manually trigger options flow fetch."""
    result = fetch_options_flow_daily()
    return {"status": "completed", "result": result}


# ═══════════════════════════════════════════════════════════════
#  Startup
# ═══════════════════════════════════════════════════════════════

@app.on_event("startup")
def startup():
    init_db()
    logger.info("US Listings & Crypto Products API started")
