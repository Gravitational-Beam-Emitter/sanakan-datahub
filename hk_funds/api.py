"""
FastAPI REST API — serve HK Fund KYP data.

Usage:
    python -m uvicorn hk_funds.api:app --host 127.0.0.1 --port 8004

Endpoints:
    GET  /api/v1/health                  — Service health + table stats (includes ISIN/NAV counts)
    GET  /api/v1/funds                   — List funds (filters: derivative/complex/type/domicile/search)
    GET  /api/v1/funds/stats             — Aggregate fund statistics (dual-dimension)
    GET  /api/v1/funds/complex           — §5.5 complex products
    GET  /api/v1/funds/derivatives       — §5.1A derivative products
    GET  /api/v1/funds/search            — Full-text search
    GET  /api/v1/funds/isins             — List funds with ISIN codes (v4)
    GET  /api/v1/funds/by-isin/{isin}    — Look up fund by ISIN (v4)
    GET  /api/v1/funds/{id}              — Single fund detail (joins classification, manager, docs, NAV, perf)
    GET  /api/v1/funds/{id}/documents    — Fund offering documents
    GET  /api/v1/funds/{id}/nav-history  — NAV time series (v4)
    GET  /api/v1/funds/{id}/nav-latest   — Latest NAV (v4)
    GET  /api/v1/funds/{id}/performance  — Performance metrics (v4)
    GET  /api/v1/managers                — List managers (filters: license type, status, enforcement)
    GET  /api/v1/managers/stats          — Manager statistics
    GET  /api/v1/managers/{id}           — Single manager detail (joins funds, regulatory history)
    GET  /api/v1/managers/{id}/funds     — Funds managed by this manager
    GET  /api/v1/managers/{id}/regulatory — Regulatory/enforcement history
    GET  /api/v1/hkex-funds              — HKEX-listed fund products
    POST /api/v1/fetch-funds             — Trigger fund list fetch
    POST /api/v1/fetch-managers          — Trigger manager fetch + link + enforcement
    POST /api/v1/classify                — Re-run classification engine
    POST /api/v1/link-managers           — Re-run fund-manager linker
    POST /api/v1/import-isins            — Import ISINs from HKEX ListOfSecurities (v4)
    POST /api/v1/managers/scrape         — Trigger manager website scraping (v4)
    GET  /api/v1/managers/scrape/status  — Get connector registry status (v4)
    POST /api/v1/funds/{id}/classify     — Manually set fund classification
    POST /api/v1/import/csv              — Import CSV data
"""

from __future__ import annotations

import logging
import math
from datetime import datetime
from io import StringIO
from typing import Optional

import pandas as pd
from fastapi import FastAPI, HTTPException, Query, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware

from hk_funds.storage import (
    init_db,
    get_funds,
    get_fund_by_id,
    get_complex_funds,
    get_derivative_funds,
    get_fund_stats,
    get_fund_classification,
    get_fund_documents,
    get_managers,
    get_manager_by_id,
    get_manager_stats,
    get_manager_funds,
    get_manager_regulatory,
    get_hkex_funds,
    get_hkex_fund_by_code,
    get_fetch_status,
    search_funds_fulltext,
    update_fund_classification,
    upsert_funds,
    # v3 — KYP / Risk Rating / Manager DD / Non-Authorized
    get_kyp_dimensions,
    upsert_kyp_dimension,
    init_kyp_dimensions,
    get_kyp_assessment_history,
    get_funds_with_kyp_gaps,
    get_kyp_stats,
    get_fund_risk_rating,
    get_all_risk_ratings,
    override_risk_rating,
    get_manager_dd,
    upsert_manager_dd,
    init_manager_dd,
    get_managers_with_dd_gaps,
    upsert_non_authorized_funds,
    get_non_authorized_funds,
    get_non_authorized_fund,
    # v4 — NAV history & performance
    get_nav_history,
    get_latest_nav,
    get_fund_performance,
    # v4 — holdings, dividends, share classes
    get_holdings,
    get_dividends,
    get_share_classes,
    get_portfolio_manager,
    search_portfolio_manager,
    # v5 — configurable rating templates
    get_system_templates,
    get_user_templates,
    get_template,
    get_template_factors,
    delete_template,
    upsert_template,
    upsert_template_factors,
    clone_template,
    get_user_ratings,
    get_user_rating_summary,
    # v6 — compute job tracking
    create_compute_job,
    update_compute_job_progress,
    finish_compute_job,
    get_compute_job_status,
)
import io
from hk_funds.pipeline_funds import fetch_funds_daily, classify_all_funds
from hk_funds.pipeline_managers import (
    fetch_managers_daily,
    link_funds_to_managers,
    cross_check_enforcement,
    import_managers_csv,
)
from hk_funds.pipeline_ofc import (
    fetch_ofc_daily,
    init_ofc_pipeline,
    get_ofc_stats,
    rate_ofc_funds,
    classify_ofc_funds,
    init_kyp_for_ofc_funds,
)

logger = logging.getLogger("hk_funds.api")

app = FastAPI(
    title="HK Fund KYP API",
    description="香港基金尽调 — SFC认可基金+管理人+复杂产品分类",
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
    """Convert date/time objects to ISO strings. Replace NaN/Inf/NaT with None.
    Recursively handles nested dicts and lists."""
    def _walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if hasattr(v, "isoformat"):
                    s = v.isoformat()
                    obj[k] = None if s == "NaT" else s
                elif isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                    obj[k] = None
                elif v is pd.NaT:
                    obj[k] = None
                elif isinstance(v, (dict, list)):
                    _walk(v)
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                if hasattr(v, "isoformat"):
                    s = v.isoformat()
                    obj[i] = None if s == "NaT" else s
                elif isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                    obj[i] = None
                elif v is pd.NaT:
                    obj[i] = None
                elif isinstance(v, (dict, list)):
                    _walk(v)

    _walk(records)
    return records


# ═══════════════════════════════════════════════════════════════
#  Health
# ═══════════════════════════════════════════════════════════════

@app.get("/api/v1/health")
def health():
    conn = init_db(read_only=True)
    try:
        fund_count = conn.execute("SELECT COUNT(*) FROM hk_funds WHERE is_active = true").fetchone()[0]
        complex_count = conn.execute(
            "SELECT COUNT(*) FROM hk_funds WHERE is_active = true AND is_complex_product = true"
        ).fetchone()[0]
        derivative_count = conn.execute(
            "SELECT COUNT(*) FROM hk_funds WHERE is_active = true AND is_derivative_product = true"
        ).fetchone()[0]
        manager_count = conn.execute(
            "SELECT COUNT(*) FROM hk_fund_managers WHERE license_status = 'active'"
        ).fetchone()[0]
        hkex_count = conn.execute("SELECT COUNT(*) FROM hkex_listed_funds").fetchone()[0]
        isin_count = conn.execute(
            "SELECT COUNT(*) FROM hk_funds WHERE is_active = true AND isin IS NOT NULL AND isin != ''"
        ).fetchone()[0]
        nav_count = conn.execute("SELECT COUNT(*) FROM hk_fund_nav_history").fetchone()[0]
        perf_count = conn.execute("SELECT COUNT(*) FROM hk_fund_performance").fetchone()[0]

        last_fetch = conn.execute(
            "SELECT fetch_date, source, status FROM hk_fetch_log ORDER BY fetch_date DESC, started_at DESC LIMIT 1"
        ).fetchone()

        return {
            "status": "ok",
            "funds": {
                "total": fund_count,
                "complex_product": complex_count,
                "derivative_product": derivative_count,
                "with_isin": isin_count,
            },
            "nav_records": nav_count,
            "performance_records": perf_count,
            "managers": manager_count,
            "hkex_listed": hkex_count,
            "last_fetch": {
                "date": str(last_fetch[0]) if last_fetch else None,
                "source": str(last_fetch[1]) if last_fetch else None,
                "status": str(last_fetch[2]) if last_fetch else None,
            },
        }
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════
#  Funds
# ═══════════════════════════════════════════════════════════════

@app.get("/api/v1/funds")
def list_funds(
    is_derivative_product: Optional[bool] = Query(None, description="§5.1A derivative product"),
    is_complex_product: Optional[bool] = Query(None, description="§5.5 complex product"),
    complex_product_type: Optional[str] = Query(None, description="derivative_fund, structured, L&I, etc."),
    fund_type: Optional[str] = Query(None, description="unit_trust, etf, mutual_fund, etc."),
    domicile: Optional[str] = Query(None, description="Hong Kong, Luxembourg, Ireland, etc."),
    is_active: bool = Query(True, description="Only active funds"),
    search: Optional[str] = Query(None, description="Search name/ISIN/auth_no"),
    limit: int = Query(100, ge=1, le=1000),
    # Deprecated params (backward compat):
    classification: Optional[str] = Query(None, description="[deprecated] ordinary/complex/derivatives/structured"),
    is_complex: Optional[bool] = Query(None, description="[deprecated] use is_complex_product"),
):
    conn = init_db(read_only=True)
    try:
        df = get_funds(conn,
                       is_derivative_product=is_derivative_product,
                       is_complex_product=is_complex_product,
                       complex_product_type=complex_product_type,
                       fund_type=fund_type,
                       domicile=domicile,
                       is_active=is_active,
                       search=search,
                       limit=limit,
                       classification=classification,
                       is_complex=is_complex)
        records = _serialize_records(df.to_dict(orient="records"))
        return {"count": len(records), "funds": records}
    finally:
        conn.close()


@app.get("/api/v1/funds/stats")
def fund_statistics():
    conn = init_db(read_only=True)
    try:
        stats = get_fund_stats(conn)
        return _serialize_records([stats])[0]
    finally:
        conn.close()


@app.get("/api/v1/funds/complex")
def complex_funds(
    complex_product_type: Optional[str] = Query(None, description="Filter by complex product type"),
    limit: int = Query(100, ge=1, le=500),
):
    """§5.5: Complex products — six-factor test."""
    conn = init_db(read_only=True)
    try:
        if complex_product_type:
            df = get_funds(conn, is_complex_product=True,
                          complex_product_type=complex_product_type, limit=limit)
        else:
            df = get_complex_funds(conn, limit=limit)
        records = _serialize_records(df.to_dict(orient="records"))
        return {"count": len(records), "funds": records}
    finally:
        conn.close()


@app.get("/api/v1/funds/derivatives")
def derivative_funds(limit: int = Query(100, ge=1, le=500)):
    """§5.1A: Derivative products — NDE > 50%, synthetic, L&I, hedge funds."""
    conn = init_db(read_only=True)
    try:
        df = get_derivative_funds(conn, limit=limit)
        records = _serialize_records(df.to_dict(orient="records"))
        return {"count": len(records), "funds": records}
    finally:
        conn.close()


@app.get("/api/v1/funds/search")
def search_funds(q: str = Query(..., description="Search query"), limit: int = Query(50, le=200)):
    conn = init_db(read_only=True)
    try:
        df = search_funds_fulltext(conn, q, limit=limit)
        records = _serialize_records(df.to_dict(orient="records"))
        return {"count": len(records), "query": q, "funds": records}
    finally:
        conn.close()


@app.get("/api/v1/funds/isins")
def list_funds_with_isins(
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """List funds that have ISIN codes."""
    conn = init_db(read_only=True)
    try:
        rows = conn.execute(
            "SELECT id, fund_name_en, isin, fund_manager_name_en, fund_type, domicile "
            "FROM hk_funds WHERE is_active = true AND isin IS NOT NULL AND isin != '' "
            "ORDER BY fund_name_en LIMIT ? OFFSET ?",
            [limit, offset]
        ).fetchall()
        cols = ["id", "fund_name_en", "isin", "fund_manager_name_en", "fund_type", "domicile"]
        records = _serialize_records([dict(zip(cols, r)) for r in rows])
        total = conn.execute(
            "SELECT COUNT(*) FROM hk_funds WHERE is_active = true AND isin IS NOT NULL AND isin != ''"
        ).fetchone()[0]
        return {"total": total, "count": len(records), "funds": records}
    finally:
        conn.close()


@app.get("/api/v1/funds/by-isin/{isin}")
def fund_by_isin(isin: str):
    """Look up a fund by its ISIN code."""
    conn = init_db(read_only=True)
    try:
        fund = conn.execute(
            "SELECT * FROM hk_funds WHERE isin = ?",
            [isin.strip().upper()]
        ).fetchone()
        if not fund:
            raise HTTPException(status_code=404, detail=f"Fund with ISIN {isin} not found")
        cols = [desc[0] for desc in conn.description]
        result = _serialize_records([dict(zip(cols, fund))])[0]
        # Enrich with manager and latest NAV
        if result.get("fund_manager_id"):
            mgr = get_manager_by_id(conn, result["fund_manager_id"])
            if mgr:
                result["manager"] = _serialize_records([mgr])[0]
        latest_nav = get_latest_nav(conn, result["id"])
        if latest_nav:
            result["latest_nav"] = _serialize_records(latest_nav)
        return result
    finally:
        conn.close()


@app.get("/api/v1/funds/{fund_id}")
def fund_detail(fund_id: int):
    conn = init_db(read_only=True)
    try:
        fund = get_fund_by_id(conn, fund_id)
        if not fund:
            raise HTTPException(status_code=404, detail=f"Fund {fund_id} not found")

        result = _serialize_records([fund])[0]

        # Enrich with classification detail
        cls_detail = get_fund_classification(conn, fund_id)
        if cls_detail:
            result["classification_detail"] = _serialize_records([cls_detail])[0]

        # Enrich with documents
        docs_df = get_fund_documents(conn, fund_id)
        if not docs_df.empty:
            result["documents"] = _serialize_records(docs_df.to_dict(orient="records"))

        # Enrich with manager info
        if fund.get("fund_manager_id"):
            mgr = get_manager_by_id(conn, fund["fund_manager_id"])
            if mgr:
                result["manager"] = _serialize_records([mgr])[0]

        # Enrich with latest NAV and performance (v4)
        latest_nav = get_latest_nav(conn, fund_id)
        if latest_nav:
            result["latest_nav"] = _serialize_records(latest_nav)

        perf = get_fund_performance(conn, fund_id)
        if perf:
            result["performance"] = _serialize_records(perf)

        # Enrich with holdings, dividends, share classes
        holdings = get_holdings(conn, fund_id, limit=20)
        if holdings:
            result["holdings"] = _serialize_records(holdings)

        dividends = get_dividends(conn, fund_id, limit=20)
        if dividends:
            result["dividends"] = _serialize_records(dividends)

        share_classes = get_share_classes(conn, fund_id)
        if share_classes:
            result["share_classes"] = _serialize_records(share_classes)

        return result
    finally:
        conn.close()


@app.get("/api/v1/funds/{fund_id}/documents")
def fund_documents(fund_id: int):
    conn = init_db(read_only=True)
    try:
        df = get_fund_documents(conn, fund_id)
        records = _serialize_records(df.to_dict(orient="records"))
        return {"count": len(records), "documents": records}
    finally:
        conn.close()


@app.post("/api/v1/funds/{fund_id}/classify")
def manually_classify_fund(
    fund_id: int,
    is_derivative_product: bool = Query(..., description="§5.1A derivative product"),
    is_complex_product: bool = Query(..., description="§5.5 complex product"),
    complex_product_type: str = Query(..., description="derivative_fund, structured, L&I, etc."),
    reason: str = Query("", description="Classification rationale"),
):
    conn = init_db()
    try:
        fund = get_fund_by_id(conn, fund_id)
        if not fund:
            raise HTTPException(status_code=404, detail=f"Fund {fund_id} not found")

        update_fund_classification(conn, fund_id,
                                   is_derivative_product=is_derivative_product,
                                   is_complex_product=is_complex_product,
                                   complex_product_type=complex_product_type,
                                   reason=reason,
                                   source="manual")
        return {
            "status": "ok", "fund_id": fund_id,
            "is_derivative_product": is_derivative_product,
            "is_complex_product": is_complex_product,
            "complex_product_type": complex_product_type,
        }
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════
#  NAV History & Performance (v4)
# ═══════════════════════════════════════════════════════════════

@app.get("/api/v1/funds/{fund_id}/nav-history")
def fund_nav_history(
    fund_id: int,
    start: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    limit: int = Query(500, ge=1, le=5000),
):
    """Get NAV time series for a fund."""
    conn = init_db(read_only=True)
    try:
        fund = get_fund_by_id(conn, fund_id)
        if not fund:
            raise HTTPException(status_code=404, detail=f"Fund {fund_id} not found")
        df = get_nav_history(conn, fund_id, start=start, end=end, limit=limit)
        records = _serialize_records(df.to_dict(orient="records"))
        return {"fund_id": fund_id, "count": len(records), "nav_history": records}
    finally:
        conn.close()


@app.get("/api/v1/funds/{fund_id}/nav-latest")
def fund_latest_nav(fund_id: int):
    """Get latest NAV for a fund."""
    conn = init_db(read_only=True)
    try:
        fund = get_fund_by_id(conn, fund_id)
        if not fund:
            raise HTTPException(status_code=404, detail=f"Fund {fund_id} not found")
        nav = get_latest_nav(conn, fund_id)
        if nav is None:
            return {"fund_id": fund_id, "nav": None, "message": "No NAV data available"}
        return {"fund_id": fund_id, "nav": _serialize_records(nav)}
    finally:
        conn.close()


@app.get("/api/v1/funds/{fund_id}/performance")
def fund_performance(fund_id: int):
    """Get performance metrics for a fund."""
    conn = init_db(read_only=True)
    try:
        fund = get_fund_by_id(conn, fund_id)
        if not fund:
            raise HTTPException(status_code=404, detail=f"Fund {fund_id} not found")
        perf = get_fund_performance(conn, fund_id)
        if perf is None:
            return {"fund_id": fund_id, "performance": None,
                    "message": "No performance data available"}
        return {"fund_id": fund_id, "performance": _serialize_records(perf)}
    finally:
        conn.close()


@app.get("/api/v1/funds/{fund_id}/holdings")
def fund_holdings(fund_id: int, limit: int = Query(50, ge=1, le=200)):
    """Get top holdings for a fund."""
    conn = init_db(read_only=True)
    try:
        fund = get_fund_by_id(conn, fund_id)
        if not fund:
            raise HTTPException(status_code=404, detail=f"Fund {fund_id} not found")
        holdings = get_holdings(conn, fund_id, limit=limit)
        return {"fund_id": fund_id, "count": len(holdings), "holdings": _serialize_records(holdings)}
    finally:
        conn.close()


@app.get("/api/v1/funds/{fund_id}/dividends")
def fund_dividends(fund_id: int, limit: int = Query(50, ge=1, le=200)):
    """Get dividend history for a fund."""
    conn = init_db(read_only=True)
    try:
        fund = get_fund_by_id(conn, fund_id)
        if not fund:
            raise HTTPException(status_code=404, detail=f"Fund {fund_id} not found")
        dividends = get_dividends(conn, fund_id, limit=limit)
        return {"fund_id": fund_id, "count": len(dividends), "dividends": _serialize_records(dividends)}
    finally:
        conn.close()


@app.get("/api/v1/funds/{fund_id}/share-classes")
def fund_share_classes(fund_id: int):
    """Get share classes for a fund (ISINs, currencies, hedging)."""
    conn = init_db(read_only=True)
    try:
        fund = get_fund_by_id(conn, fund_id)
        if not fund:
            raise HTTPException(status_code=404, detail=f"Fund {fund_id} not found")
        share_classes = get_share_classes(conn, fund_id)
        return {"fund_id": fund_id, "count": len(share_classes), "share_classes": _serialize_records(share_classes)}
    finally:
        conn.close()


@app.get("/api/v1/funds/{fund_id}/portfolio-manager")
def fund_portfolio_manager(
    fund_id: int,
    search: Optional[str] = Query(None, description="Search by portfolio manager / fund manager name"),
    limit: int = Query(50, ge=1, le=200),
):
    """Get portfolio manager info for a fund, or search across all funds."""
    conn = init_db(read_only=True)
    try:
        if search:
            results = search_portfolio_manager(conn, search, limit)
            return {"search": search, "count": len(results), "funds": results}
        pm = get_portfolio_manager(conn, fund_id)
        if not pm:
            raise HTTPException(status_code=404, detail=f"Fund {fund_id} not found or no portfolio manager")
        return pm
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════
#  Managers
# ═══════════════════════════════════════════════════════════════

@app.get("/api/v1/managers")
def list_managers(
    license_type: Optional[str] = Query(None, description="Type 1,4,9 etc."),
    license_status: str = Query("active", description="active, suspended, revoked"),
    has_enforcement: Optional[bool] = Query(None, description="Filter by enforcement history"),
    search: Optional[str] = Query(None, description="Search name/CE number"),
    limit: int = Query(100, ge=1, le=500),
):
    conn = init_db(read_only=True)
    try:
        df = get_managers(conn, license_type=license_type, license_status=license_status,
                          has_enforcement=has_enforcement, search=search, limit=limit)
        records = _serialize_records(df.to_dict(orient="records"))
        return {"count": len(records), "managers": records}
    finally:
        conn.close()


@app.get("/api/v1/managers/stats")
def manager_statistics():
    conn = init_db(read_only=True)
    try:
        stats = get_manager_stats(conn)
        return stats
    finally:
        conn.close()


@app.get("/api/v1/managers/{manager_id}")
def manager_detail(manager_id: int):
    conn = init_db(read_only=True)
    try:
        mgr = get_manager_by_id(conn, manager_id)
        if not mgr:
            raise HTTPException(status_code=404, detail=f"Manager {manager_id} not found")

        result = _serialize_records([mgr])[0]

        # Enrich with fund count
        funds_df = get_manager_funds(conn, manager_id, limit=500)
        result["fund_count"] = len(funds_df) if not funds_df.empty else 0
        result["funds"] = _serialize_records(funds_df.to_dict(orient="records")) if not funds_df.empty else []

        # Enrich with regulatory history
        reg_df = get_manager_regulatory(conn, manager_id)
        result["regulatory_count"] = len(reg_df) if not reg_df.empty else 0
        result["regulatory_history"] = _serialize_records(reg_df.to_dict(orient="records")) if not reg_df.empty else []

        return result
    finally:
        conn.close()


@app.get("/api/v1/managers/{manager_id}/funds")
def manager_funds_list(manager_id: int, limit: int = Query(200, le=500)):
    conn = init_db(read_only=True)
    try:
        df = get_manager_funds(conn, manager_id, limit=limit)
        records = _serialize_records(df.to_dict(orient="records"))
        return {"count": len(records), "funds": records}
    finally:
        conn.close()


@app.get("/api/v1/managers/{manager_id}/regulatory")
def manager_regulatory_history(manager_id: int, limit: int = Query(100, le=200)):
    conn = init_db(read_only=True)
    try:
        df = get_manager_regulatory(conn, manager_id, limit=limit)
        records = _serialize_records(df.to_dict(orient="records"))
        return {"count": len(records), "regulatory": records}
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════
#  HKEX Listed Funds
# ═══════════════════════════════════════════════════════════════

@app.get("/api/v1/hkex-funds")
def list_hkex_funds(
    etf_type: Optional[str] = Query(None, description="physical, synthetic, futures, leveraged_inverse"),
    limit: int = Query(100, ge=1, le=200),
):
    conn = init_db(read_only=True)
    try:
        df = get_hkex_funds(conn, etf_type=etf_type, limit=limit)
        records = _serialize_records(df.to_dict(orient="records"))
        return {"count": len(records), "hkex_funds": records}
    finally:
        conn.close()


@app.get("/api/v1/hkex-funds/{stock_code}")
def hkex_fund_detail(stock_code: str):
    conn = init_db(read_only=True)
    try:
        fund = get_hkex_fund_by_code(conn, stock_code)
        if not fund:
            raise HTTPException(status_code=404, detail=f"HKEX fund {stock_code} not found")
        return _serialize_records([fund])[0]
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════
#  Fetch Status
# ═══════════════════════════════════════════════════════════════

@app.get("/api/v1/fetch/status")
def fetch_status(days: int = Query(7, le=30)):
    conn = init_db(read_only=True)
    try:
        df = get_fetch_status(conn, days=days)
        records = _serialize_records(df.to_dict(orient="records"))
        return {"count": len(records), "logs": records}
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════
#  Fetch Triggers
# ═══════════════════════════════════════════════════════════════

@app.post("/api/v1/fetch-funds")
def trigger_fund_fetch(date: Optional[str] = None):
    result = fetch_funds_daily(date)
    return {"status": "completed", "result": result}


@app.post("/api/v1/fetch-managers")
def trigger_manager_fetch(date: Optional[str] = None):
    result = fetch_managers_daily(date)
    return {"status": "completed", "result": result}


@app.post("/api/v1/classify")
def trigger_classification():
    conn = init_db()
    try:
        result = classify_all_funds(conn)
        return {"status": "completed", "result": result}
    finally:
        conn.close()


@app.post("/api/v1/link-managers")
def trigger_manager_linking():
    conn = init_db()
    try:
        result = link_funds_to_managers(conn)
        return {"status": "completed", "result": result}
    finally:
        conn.close()


@app.post("/api/v1/import-isins")
def trigger_isin_import():
    """Import ISINs from HKEX ListOfSecurities into hk_funds."""
    conn = init_db()
    try:
        from hk_funds.manager_connectors.hkex_etf import HKEXETFConnector
        connector = HKEXETFConnector()
        stats = connector.import_isins_to_db(conn)
        return {"status": "completed", "result": stats}
    finally:
        conn.close()


@app.post("/api/v1/managers/scrape")
def trigger_manager_scrape(ce_number: str = Query(None, description="Specific CE number to scrape. Omit to scrape all.")):
    """Trigger manager website scraping. Scrapes all registered connectors by default,
    or a specific manager if ce_number is provided."""
    from hk_funds.pipeline_manager_scrape import scrape_manager, scrape_all_managers

    conn = init_db()
    try:
        if ce_number:
            from hk_funds.pipeline_manager_scrape import update_manager_websites
            update_manager_websites(conn)
            stats = scrape_manager(conn, ce_number)
            if stats is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"No connector registered for CE {ce_number}"
                )
            return {"status": "completed", "result": stats}
        else:
            conn.close()
            result = scrape_all_managers()
            return {"status": "completed", "result": result}
    finally:
        if ce_number:
            conn.close()


@app.get("/api/v1/managers/scrape/status")
def manager_scrape_status():
    """Get status of registered manager connectors and which managers have connectors."""
    from hk_funds.manager_connectors import (
        get_all_registered_ce_numbers,
        get_website_for_manager,
        list_managers_needing_connectors,
    )

    conn = init_db(read_only=True)
    try:
        registered = get_all_registered_ce_numbers()
        needs_connectors = list_managers_needing_connectors(conn, min_funds=3)

        connector_info = []
        for ce in sorted(registered):
            mgr_row = conn.execute(
                "SELECT company_name_en FROM hk_fund_managers WHERE ce_number = ?",
                [ce]
            ).fetchone()
            connector_info.append({
                "ce_number": ce,
                "company_name_en": mgr_row[0] if mgr_row else "Unknown",
                "website": get_website_for_manager(ce),
            })

        return {
            "registered_connectors": len(registered),
            "connectors": connector_info,
            "managers_needing_connectors": len(needs_connectors),
            "top_managers_without_connectors": [
                {"ce_number": m["ce_number"],
                 "company_name_en": m["company_name_en"],
                 "fund_count": m["fund_count"]}
                for m in needs_connectors[:15] if not m["has_connector"]
            ],
        }
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════
#  CSV Import
# ═══════════════════════════════════════════════════════════════

@app.post("/api/v1/import/csv")
async def import_csv(file: UploadFile = File(..., description="CSV file with fund data")):
    """Import fund records from CSV file.

    Expected columns: sfc_authorization_no, fund_name_en, fund_name_cn,
    fund_type, domicile, currency, isin, launch_date, authorization_date,
    fund_manager_name_en, fund_manager_name_cn, management_fee_pct, nav, etc.
    """
    conn = init_db()
    try:
        content = await file.read()
        df = pd.read_csv(StringIO(content.decode("utf-8")))
        records = df.to_dict(orient="records")
        stored = upsert_funds(conn, records)
        return {"status": "ok", "file": file.filename, "imported": len(records), "stored": stored}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"CSV import failed: {e}")
    finally:
        conn.close()


@app.post("/api/v1/import/managers-csv")
async def import_managers_csv_endpoint(file: UploadFile = File(..., description="CSV/Excel file with manager data")):
    """Import manager records from CSV or Excel file.

    Expected columns: ce_number, company_name_en, company_name_cn, license_type,
    regulated_activity_1, regulated_activity_4, regulated_activity_9,
    license_status, license_effective_date, business_address, website,
    key_ro_name_en, key_ro_name_cn, ro_count, total_licensed_staff
    """
    conn = init_db()
    try:
        import tempfile, os
        suffix = ".csv"
        if file.filename and file.filename.lower().endswith((".xlsx", ".xls")):
            suffix = ".xlsx"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            content = await file.read()
            tmp.write(content)
            tmp.flush()
            result = import_managers_csv(tmp.name)
        os.unlink(tmp.name)
        return {"status": "ok", "file": file.filename, **result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Manager CSV import failed: {e}")
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════
#  KYP Dimensions
# ═══════════════════════════════════════════════════════════════

@app.get("/api/v1/funds/{fund_id}/kyp")
def get_fund_kyp(fund_id: int):
    """Get all 10 KYP dimensions for a fund."""
    conn = init_db(read_only=True)
    try:
        dims = get_kyp_dimensions(conn, fund_id)
        return _serialize_records(dims.to_dict(orient="records"))
    finally:
        conn.close()


@app.put("/api/v1/funds/{fund_id}/kyp/{dimension}")
def update_fund_kyp_dimension(fund_id: int, dimension: str, updates: dict):
    """Update a single KYP dimension assessment."""
    conn = init_db()
    try:
        ok = upsert_kyp_dimension(conn, fund_id, dimension, updates)
        return {"success": ok, "fund_id": fund_id, "dimension": dimension}
    finally:
        conn.close()


@app.get("/api/v1/funds/{fund_id}/kyp/history")
def get_fund_kyp_history(fund_id: int, limit: int = 50):
    """Get KYP assessment audit trail for a fund."""
    conn = init_db(read_only=True)
    try:
        return _serialize_records(get_kyp_assessment_history(conn, fund_id, limit).to_dict(orient="records"))
    finally:
        conn.close()


@app.get("/api/v1/kyp/gaps")
def get_kyp_gaps(limit: int = 50, offset: int = 0, gaps_only: bool = False):
    """Get funds with KYP assessment status. Supports pagination."""
    conn = init_db(read_only=True)
    try:
        from hk_funds.storage import get_kyp_funds_count
        rows = get_funds_with_kyp_gaps(conn, limit, offset, gaps_only).to_dict(orient="records")
        total = get_kyp_funds_count(conn, gaps_only)
        return {"data": _serialize_records(rows), "total": total, "limit": limit, "offset": offset}
    finally:
        conn.close()


@app.get("/api/v1/kyp/stats")
def get_kyp_summary_stats():
    """Get aggregate KYP assessment statistics."""
    conn = init_db(read_only=True)
    try:
        return get_kyp_stats(conn)
    finally:
        conn.close()


@app.post("/api/v1/funds/{fund_id}/kyp/init")
def init_fund_kyp(fund_id: int):
    """Initialize all 10 KYP dimensions for a fund."""
    conn = init_db()
    try:
        count = init_kyp_dimensions(conn, fund_id)
        return {"success": True, "dimensions_created": count}
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════
#  Risk Ratings
# ═══════════════════════════════════════════════════════════════

@app.get("/api/v1/funds/{fund_id}/risk-rating")
def get_fund_risk_rating_endpoint(fund_id: int):
    """Get risk rating for a fund."""
    conn = init_db(read_only=True)
    try:
        rating = get_fund_risk_rating(conn, fund_id)
        if rating is None:
            return {"error": "No rating found", "fund_id": fund_id}
        return _serialize_records([rating])[0]
    finally:
        conn.close()


@app.get("/api/v1/risk-ratings")
def get_all_risk_ratings_endpoint(risk_category: str = None, limit: int = Query(None, ge=1, le=2000), offset: int = Query(0, ge=0)):
    """Get all fund risk ratings, optionally filtered by category."""
    conn = init_db(read_only=True)
    try:
        df = get_all_risk_ratings(conn, risk_category, limit=limit, offset=offset)
        return _serialize_records(df.to_dict(orient="records"))
    finally:
        conn.close()


@app.put("/api/v1/funds/{fund_id}/risk-rating/override")
def override_fund_risk_rating(fund_id: int,
                               new_score: float,
                               new_category: str,
                               reason: str,
                               overridden_by: str = "api"):
    """Manually override a fund's risk rating."""
    conn = init_db()
    try:
        ok = override_risk_rating(conn, fund_id, new_score, new_category, reason, overridden_by)
        return {"success": ok, "fund_id": fund_id, "new_score": new_score}
    finally:
        conn.close()


@app.post("/api/v1/risk-ratings/calculate")
def trigger_risk_rating_calculation(fund_id: int = None):
    """Calculate risk ratings. If fund_id provided, rate single fund; else rate all."""
    from hk_funds.risk_rating import rate_all_funds, rate_single_fund
    if fund_id:
        result = rate_single_fund(fund_id)
        return {"status": "completed", "fund_id": fund_id, **result}
    result = rate_all_funds()
    return {"status": "completed", **result}


# ═══════════════════════════════════════════════════════════════
#  Manager DD
# ═══════════════════════════════════════════════════════════════

@app.get("/api/v1/managers/{manager_id}/dd")
def get_manager_dd_endpoint(manager_id: int):
    conn = init_db(read_only=True)
    try:
        return _serialize_records(get_manager_dd(conn, manager_id).to_dict(orient="records"))
    finally:
        conn.close()


@app.put("/api/v1/managers/{manager_id}/dd/{dimension}")
def update_manager_dd_endpoint(manager_id: int, dimension: str, updates: dict):
    conn = init_db()
    try:
        ok = upsert_manager_dd(conn, manager_id, dimension, updates)
        return {"success": ok, "manager_id": manager_id, "dimension": dimension}
    finally:
        conn.close()


@app.get("/api/v1/managers/dd/gaps")
def get_manager_dd_gaps(limit: int = 50):
    conn = init_db(read_only=True)
    try:
        return _serialize_records(get_managers_with_dd_gaps(conn, limit).to_dict(orient="records"))
    finally:
        conn.close()


@app.post("/api/v1/managers/{manager_id}/dd/init")
def init_manager_dd_endpoint(manager_id: int):
    conn = init_db()
    try:
        count = init_manager_dd(conn, manager_id)
        return {"success": True, "dimensions_created": count}
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════
#  Non-Authorized Funds
# ═══════════════════════════════════════════════════════════════

@app.get("/api/v1/non-authorized-funds")
def get_non_authorized_funds_endpoint(distribution_restriction: str = None,
                                       is_active: bool = True, limit: int = 100):
    conn = init_db(read_only=True)
    try:
        return _serialize_records(get_non_authorized_funds(conn, distribution_restriction, is_active, limit).to_dict(orient="records"))
    finally:
        conn.close()


@app.get("/api/v1/non-authorized-funds/{fund_id}")
def get_non_authorized_fund_endpoint(fund_id: int):
    conn = init_db(read_only=True)
    try:
        fund = get_non_authorized_fund(conn, fund_id)
        if fund is None:
            raise HTTPException(status_code=404, detail="Fund not found")
        return _serialize_records([fund])[0]
    finally:
        conn.close()


@app.get("/api/v1/non-authorized-funds/{fund_id}/kyp")
def get_non_authorized_fund_kyp(fund_id: int):
    """Get KYP dimensions for a non-authorized fund.
    Uses negated ID -(fund_id) - 100000 internally to avoid collision
    with SFC fund IDs in the shared hk_kyp_dimensions table.
    """
    conn = init_db(read_only=True)
    try:
        kyp_id = -int(fund_id) - 100000
        dims = get_kyp_dimensions(conn, kyp_id)
        return _serialize_records(dims.to_dict(orient="records"))
    finally:
        conn.close()


@app.post("/api/v1/non-authorized-funds")
def create_non_authorized_fund(records: list):
    conn = init_db()
    try:
        count = upsert_non_authorized_funds(conn, records)
        return {"success": True, "stored": count}
    finally:
        conn.close()


@app.post("/api/v1/import/non-authorized-csv")
async def import_non_authorized_csv(file: UploadFile = File(...)):
    conn = init_db()
    try:
        import pandas as pd
        content = await file.read()
        df = pd.read_csv(io.BytesIO(content))
        records = df.to_dict(orient="records")
        count = upsert_non_authorized_funds(conn, records)
        return {"success": True, "imported": len(records), "stored": count}
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════
#  OFC (Open-ended Fund Company) Endpoints
# ═══════════════════════════════════════════════════════════════

@app.get("/api/v1/ofc/stats")
def ofc_statistics():
    """Get OFC-specific statistics from the database."""
    return get_ofc_stats()


@app.post("/api/v1/ofc/fetch")
def trigger_ofc_fetch():
    """Fetch the SFC OFC register and store results."""
    result = fetch_ofc_daily()
    return {"status": "completed", "result": result}


@app.post("/api/v1/ofc/init")
def trigger_ofc_init():
    """Full OFC pipeline: fetch → store → classify → rate → KYP."""
    result = init_ofc_pipeline()
    return result


@app.post("/api/v1/ofc/rate")
def trigger_ofc_rating():
    """Run risk rating on all OFC funds (public + private)."""
    result = rate_ofc_funds()
    return {"status": "completed", "result": result}


@app.post("/api/v1/ofc/classify")
def trigger_ofc_classify():
    """Run classification on all OFC funds."""
    result = classify_ofc_funds()
    return {"status": "completed", "result": result}


@app.post("/api/v1/ofc/kyp")
def trigger_ofc_kyp():
    """Initialize KYP dimensions for all OFC funds."""
    result = init_kyp_for_ofc_funds()
    return {"status": "completed", "result": result}


# ═══════════════════════════════════════════════════════════════
#  Configurable Rating Templates (v5)
# ═══════════════════════════════════════════════════════════════


@app.get("/api/v1/templates")
def list_templates(user_id: str = Query("system"), template_type: str = Query(None)):
    """List rating templates. user_id='system' for built-in, or custom user_id."""
    conn = init_db()
    try:
        if user_id == "system":
            df = get_system_templates(conn, template_type or None)
        else:
            df = get_user_templates(conn, user_id, template_type or None)
        templates = []
        for _, row in df.iterrows():
            templates.append({
                "id": int(row["id"]),
                "user_id": row["user_id"],
                "name": row["name"],
                "description": row.get("description", ""),
                "template_type": row["template_type"],
                "is_system": bool(row.get("is_system", False)),
                "methodology_version": row.get("methodology_version", "1.0"),
                "factor_count": int(row.get("factor_count", 0)),
                "category_thresholds": row.get("category_thresholds_json", "[]"),
                "created_at": str(row.get("created_at", "")) if row.get("created_at") else "",
                "updated_at": str(row.get("updated_at", "")) if row.get("updated_at") else "",
            })
        return {"count": len(templates), "templates": templates}
    finally:
        conn.close()


@app.get("/api/v1/templates/{template_id}")
def get_template_detail(template_id: int):
    """Get full template with all factors and thresholds."""
    from hk_funds.rating_engine import load_template
    conn = init_db()
    try:
        tmpl = load_template(conn, template_id)
        if tmpl is None:
            raise HTTPException(status_code=404, detail=f"Template {template_id} not found")
        return {
            "id": tmpl["id"],
            "user_id": tmpl["user_id"],
            "name": tmpl["name"],
            "description": tmpl.get("description", ""),
            "template_type": tmpl["template_type"],
            "methodology_version": tmpl.get("methodology_version", "1.0"),
            "is_system": tmpl.get("is_system", False),
            "category_thresholds": tmpl["category_thresholds"],
            "factors": [
                {
                    "id": f.get("id"),
                    "factor_key": f["factor_key"],
                    "factor_label": f["factor_label"],
                    "weight": f["weight"],
                    "ordinal": f["ordinal"],
                    "config": f.get("config", {}),
                }
                for f in tmpl["factors"]
            ],
        }
    finally:
        conn.close()


@app.post("/api/v1/templates/clone")
def clone_rating_template(source_template_id: int, user_id: str,
                          new_name: str = Query("")):
    """Clone a template for a user. Returns the new template."""
    conn = init_db()
    try:
        new_id = clone_template(conn, source_template_id, user_id, new_name.strip() or "")
        if new_id is None:
            raise HTTPException(status_code=400, detail="Clone failed — source not found")
        from hk_funds.rating_engine import load_template
        tmpl = load_template(conn, new_id)
        return {
            "cloned_template_id": new_id,
            "template": {
                "id": tmpl["id"],
                "name": tmpl["name"],
                "template_type": tmpl["template_type"],
                "factor_count": len(tmpl["factors"]),
            },
        }
    finally:
        conn.close()


@app.put("/api/v1/templates/{template_id}")
def update_rating_template(template_id: int, body: dict):
    """Update a user-owned template. Cannot modify system templates."""
    conn = init_db()
    try:
        import json as _json

        tmpl = get_template(conn, template_id)
        if tmpl is None:
            raise HTTPException(status_code=404, detail=f"Template {template_id} not found")
        if tmpl["is_system"]:
            raise HTTPException(status_code=403, detail="System templates cannot be modified")
        if tmpl["user_id"] != body.get("user_id", ""):
            raise HTTPException(status_code=403,
                detail=f"Template owned by {tmpl['user_id']}, not {body.get('user_id', '')}")

        changes = []

        # Update name/description
        if body.get("name"):
            conn.execute("UPDATE hk_rating_templates SET name = ?, updated_at = now() WHERE id = ?",
                         [body["name"], template_id])
            changes.append("name")
        if body.get("description"):
            conn.execute("UPDATE hk_rating_templates SET description = ?, updated_at = now() WHERE id = ?",
                         [body["description"], template_id])
            changes.append("description")

        # Update factor weights
        if body.get("factor_weights"):
            for fk, weight in body["factor_weights"].items():
                conn.execute(
                    "UPDATE hk_template_factors SET weight = ? WHERE template_id = ? AND factor_key = ?",
                    [weight, template_id, fk])
            changes.append(f"{len(body['factor_weights'])} factor weights")

        # Update thresholds
        if body.get("category_thresholds"):
            thresholds_str = _json.dumps(body["category_thresholds"])
            conn.execute(
                "UPDATE hk_rating_templates SET category_thresholds_json = ?, updated_at = now() WHERE id = ?",
                [thresholds_str, template_id])
            changes.append("thresholds")

        return {"template_id": template_id, "updated": changes}
    finally:
        conn.close()


@app.delete("/api/v1/templates/{template_id}")
def delete_rating_template(template_id: int):
    """Delete a user-owned template. System templates cannot be deleted."""
    conn = init_db()
    try:
        success = delete_template(conn, template_id)
        if not success:
            raise HTTPException(status_code=403, detail="Cannot delete system template or not found")
        return {"deleted": True, "template_id": template_id}
    finally:
        conn.close()


@app.post("/api/v1/templates/{template_id}/compute")
def compute_ratings(template_id: int, body: dict):
    """Compute ratings using a template. body: {user_id, target_type?, target_id?}"""
    conn = init_db()
    try:
        from hk_funds.rating_engine import (
            compute_ratings_batch, compute_manager_dd_batch, compute_single_rating,
        )
        user_id = body["user_id"]
        target_type = body.get("target_type", "fund")
        target_id = body.get("target_id", 0)

        if target_id:
            rating = compute_single_rating(conn, template_id, user_id, target_type, target_id)
            if rating is None:
                raise HTTPException(status_code=404, detail=f"Cannot rate {target_type} #{target_id}")
            return {
                "template_id": template_id, "user_id": user_id,
                "target_type": target_type, "target_id": target_id,
                "overall_score": rating["overall_score"],
                "category": rating["category"],
                "factor_scores": rating.get("factor_scores", [])[:20],
            }

        if target_type == "manager":
            result = compute_manager_dd_batch(conn, template_id, user_id)
        else:
            result = compute_ratings_batch(conn, template_id, user_id)
        return result
    finally:
        conn.close()


@app.get("/api/v1/templates/{template_id}/results")
def get_rating_results(template_id: int, user_id: str = Query(...),
                       target_type: str = Query("fund"),
                       limit: int = Query(100)):
    """Get rating results for a template+user combination."""
    conn = init_db()
    try:
        import json as _json
        summary = get_user_rating_summary(conn, template_id, user_id, target_type)
        df = get_user_ratings(conn, template_id, user_id, target_type, is_latest=True)

        results = []
        for _, row in df.head(limit).iterrows():
            fs_raw = row.get("factor_scores_json", "[]")
            if isinstance(fs_raw, str):
                factor_scores = _json.loads(fs_raw)
            else:
                factor_scores = fs_raw or []
            results.append({
                "target_id": int(row["target_id"]),
                "target_name": row.get("target_name", ""),
                "overall_score": float(row["overall_score"]),
                "category": row["category"],
                "factor_count": len(factor_scores) if isinstance(factor_scores, list) else 0,
                "computed_at": str(row.get("computed_at", "")) if row.get("computed_at") else "",
            })

        summary["results"] = results
        return summary
    finally:
        conn.close()


@app.get("/api/v1/ratings/by-template/{template_id}")
def get_ratings_by_template(template_id: int, user_id: str = Query("system"),
                             target_type: str = Query("fund")):
    """Return ratings keyed by target_id for fast frontend lookup."""
    conn = init_db()
    try:
        df = get_user_ratings(conn, template_id, user_id, target_type, is_latest=True)
        result = {}
        for _, row in df.iterrows():
            result[int(row["target_id"])] = {
                "overall_score": float(row["overall_score"]) if row["overall_score"] is not None else None,
                "category": row["category"],
                "computed_at": str(row.get("computed_at", "")) if row.get("computed_at") else "",
                "target_name": row.get("target_name", ""),
            }
        return result
    finally:
        conn.close()


@app.get("/api/v1/templates/{template_id}/compute-status")
def get_compute_status(template_id: int, user_id: str = Query(...),
                       target_type: str = Query("fund")):
    """Return latest compute job status for a template+user combo."""
    conn = init_db()
    try:
        job = get_compute_job_status(conn, template_id, user_id, target_type)
        if job is None:
            return {"status": "none", "message": "No compute job found"}
        return job
    finally:
        conn.close()


@app.post("/api/v1/admin/precompute-system-templates")
def admin_precompute():
    """Admin endpoint: trigger pre-computation of all system templates."""
    import threading
    conn = init_db()
    try:
        templates_df = get_system_templates(conn)
        if templates_df is None or len(templates_df) == 0:
            return {"status": "error", "message": "No system templates found"}
        template_ids = [int(row["id"]) for _, row in templates_df.iterrows()]
    finally:
        conn.close()

    def _precompute():
        conn = init_db()
        try:
            from hk_funds.rating_engine import compute_ratings_batch, compute_manager_dd_batch
            templates_df2 = get_system_templates(conn)
            for _, tmpl in templates_df2.iterrows():
                tid = int(tmpl["id"])
                ttype = tmpl.get("template_type", "")
                tname = tmpl.get("name", "")
                logger.info(f"Admin pre-computing '{tname}' (id={tid})")
                try:
                    if ttype == "fund_risk":
                        result = compute_ratings_batch(conn, tid, "system")
                    elif ttype == "manager_dd":
                        result = compute_manager_dd_batch(conn, tid, "system")
                    else:
                        continue
                    n = result.get("total_rated", 0)
                    logger.info(f"Pre-computed '{tname}': {n} targets")
                except Exception as e:
                    logger.error(f"Failed to pre-compute '{tname}': {e}")
        finally:
            conn.close()

    threading.Thread(target=_precompute, daemon=True).start()
    return {"status": "started", "template_ids": template_ids}


# ═══════════════════════════════════════════════════════════════
#  Startup
# ═══════════════════════════════════════════════════════════════

@app.on_event("startup")
def startup():
    init_db()
    # Pre-computation happens via POST /api/v1/admin/precompute-system-templates
    logger.info("HK Fund KYP API started")


def _precompute_system_templates_async():
    """Pre-compute system template ratings in a background thread."""
    import threading

    def _precompute():
        conn = init_db()
        try:
            from hk_funds.rating_engine import (
                compute_ratings_batch, compute_manager_dd_batch,
            )
            templates_df = get_system_templates(conn)
            if templates_df is None or len(templates_df) == 0:
                logger.info("No system templates to pre-compute")
                return

            for _, tmpl in templates_df.iterrows():
                tid = int(tmpl["id"])
                ttype = tmpl.get("template_type", "")
                tname = tmpl.get("name", "")
                existing = get_user_ratings(conn, tid, "system",
                                            target_type="fund" if ttype == "fund_risk" else "manager",
                                            is_latest=True)
                if existing is not None and len(existing) > 0:
                    logger.info(f"Skipping pre-compute for '{tname}' — {len(existing)} ratings already exist")
                    continue

                logger.info(f"Pre-computing system template '{tname}' (id={tid}, type={ttype})")
                try:
                    if ttype == "fund_risk":
                        result = compute_ratings_batch(conn, tid, "system")
                    elif ttype == "manager_dd":
                        result = compute_manager_dd_batch(conn, tid, "system")
                    else:
                        continue
                    n = result.get("total_rated", 0) if "total_rated" in result else result.get("rated", 0)
                    logger.info(f"Pre-computed '{tname}': {n} targets rated")
                except Exception as e:
                    logger.error(f"Failed to pre-compute '{tname}': {e}")
        except Exception as e:
            logger.error(f"Pre-computation thread failed: {e}")
        finally:
            conn.close()

    threading.Thread(target=_precompute, daemon=True).start()
