"""
REST API — FastAPI server exposing macroeconomic data.

Start with:  uvicorn app.api:app --reload
OpenAPI docs: http://localhost:8000/docs
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from app.categories import DataCategory, get_category, sources_by_category, category_label, SOURCE_CATEGORY
from app.config import FRED_API_KEY, EIA_API_KEY
from app.storage import (
    init_db,
    get_indicators,
    get_indicator,
    get_data,
    get_data_batch,
    get_latest_batch,
    search_indicators,
    get_all_tags,
    get_indicators_by_tag,
)
from app.pipeline import run_once

app = FastAPI(
    title="Eco Data API",
    description="Unified economic intelligence — three data categories: **Macroeconomic** (21 sources: FRED, AKShare, World Bank, BoJ, EIA, etc.), **Country Risk Ratings** (FATF/INCSR/Basel AML, OFAC Sanctions, TI CPI), **Name Screening** (OpenSanctions 383K entities, Chinese+English fuzzy matching).",
    version="1.4.0",
)

# ── Startup ──────────────────────────────────────────────────


@app.on_event("startup")
def _startup():
    init_db()  # ensure tables exist


# ── Indicators ───────────────────────────────────────────────


@app.get("/api/v1/indicators", tags=["Macro"])
def list_indicators(
    source: Optional[str] = Query(None, description="Filter by source: us, cn, global_, hk, jp, euro, uk, de, au, ca, ch, bond, futures, shipping, banks, alt, llm, defi, energy, ai, ai_co, aml, sanctions, name_screening"),
    tag: Optional[str] = Query(None, description="Filter by tag (e.g. 通胀, 就业, AI算力, 数据中心)"),
):
    """List all available indicators with metadata. Filter by source and/or tag."""
    conn = init_db()
    try:
        if tag:
            df = get_indicators_by_tag(conn, tag)
        else:
            df = get_indicators(conn, source=source)
        return _df_to_list(df)
    finally:
        conn.close()


@app.get("/api/v1/indicators/search", tags=["Macro"])
def search_indicators_api(q: str = Query(..., description="Search query")):
    """Search indicators by keyword (name, description, tags, or source)."""
    conn = init_db()
    try:
        df = search_indicators(conn, q)
        return _df_to_list(df)
    finally:
        conn.close()


@app.get("/api/v1/tags", tags=["Macro"])
def list_tags():
    """List all tags with indicator counts. Browse data by topic without knowing keywords."""
    conn = init_db()
    try:
        return get_all_tags(conn)
    finally:
        conn.close()


@app.get("/api/v1/indicators/{indicator_id}", tags=["Macro"])
def get_indicator_detail(indicator_id: int):
    """Get a single indicator's metadata."""
    conn = init_db()
    try:
        row = get_indicator(conn, indicator_id)
        if row is None:
            raise HTTPException(404, f"Indicator {indicator_id} not found")
        # Parse params JSON
        if isinstance(row.get("params"), str):
            import json
            try:
                row["params"] = json.loads(row["params"])
            except (json.JSONDecodeError, TypeError):
                pass
        # Convert timestamp
        if row.get("last_updated"):
            row["last_updated"] = str(row["last_updated"])
        return row
    finally:
        conn.close()


# ── Data ─────────────────────────────────────────────────────


@app.get("/api/v1/data/{indicator_id}", tags=["Macro"])
def query_data(
    indicator_id: int,
    start: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    limit: int = Query(1000, ge=1, le=50000, description="Max rows to return"),
):
    """Query time-series data for an indicator."""
    conn = init_db()
    try:
        # Verify indicator exists
        meta = get_indicator(conn, indicator_id)
        if meta is None:
            raise HTTPException(404, f"Indicator {indicator_id} not found")

        df = get_data(conn, indicator_id, start=start, end=end, limit=limit)
        return {
            "indicator": meta,
            "count": len(df),
            "data": _df_to_records(df),
        }
    finally:
        conn.close()


@app.get("/api/v1/data/{indicator_id}/latest", tags=["Macro"])
def latest_value(indicator_id: int):
    """Get the most recent observation for an indicator."""
    conn = init_db()
    try:
        meta = get_indicator(conn, indicator_id)
        if meta is None:
            raise HTTPException(404, f"Indicator {indicator_id} not found")

        df = get_data(conn, indicator_id, limit=1)
        if df.empty:
            return {"indicator": meta, "latest": None}
        return {
            "indicator": meta,
            "latest": {"date": str(df.iloc[0]["date"]), "value": df.iloc[0]["value"]},
        }
    finally:
        conn.close()


class BatchRequest(BaseModel):
    ids: List[int]
    start: Optional[str] = None
    end: Optional[str] = None
    limit: int = 60


@app.post("/api/v1/data/batch", tags=["Macro"])
def query_data_batch(body: BatchRequest):
    """Query time-series for multiple indicators in a single DB connection."""
    if not body.ids:
        raise HTTPException(400, "ids list is required")
    conn = init_db()
    try:
        data = get_data_batch(conn, body.ids, start=body.start, end=body.end, limit=body.limit)
        return {"data": data}
    finally:
        conn.close()


class LatestBatchRequest(BaseModel):
    ids: List[int]


@app.post("/api/v1/data/latest/batch", tags=["Macro"])
def latest_value_batch(body: LatestBatchRequest):
    """Get latest values for multiple indicators in a single DB connection."""
    if not body.ids:
        raise HTTPException(400, "ids list is required")
    conn = init_db()
    try:
        data = get_latest_batch(conn, body.ids)
        return {"data": data}
    finally:
        conn.close()


# ── Fetch ────────────────────────────────────────────────────


@app.post("/api/v1/fetch", tags=["Macro"])
def trigger_fetch(
    source: Optional[str] = Query(None, description="Limit to one source (us, cn, global_, hk, jp, euro, uk, de, au, ca, ch, bond, futures, shipping, banks, alt, llm, defi, energy, ai, ai_co, aml, sanctions, name_screening)"),
):
    """Trigger a data fetch. Without source=, fetches all."""
    sources = [source] if source else None
    summary = run_once(sources=sources)
    return summary


# ── Categories ─────────────────────────────────────────────────


@app.get("/api/v1/categories", tags=["System"])
def list_categories():
    """List the three data categories with source counts and descriptions."""
    conn = init_db()
    try:
        categories = []
        for cat in DataCategory:
            srcs = sources_by_category(cat)
            # Count indicators for this category
            if cat == DataCategory.NAME_SCREENING:
                # Name screening is in a separate table
                ns_count = conn.execute("SELECT COUNT(*) FROM name_screening").fetchone()[0]
                ind_count = 0
            else:
                ns_count = 0
                placeholders = ",".join(["?"] * len(srcs))
                ind_count = conn.execute(
                    f"SELECT COUNT(*) FROM indicators WHERE source IN ({placeholders})",
                    srcs,
                ).fetchone()[0]
            categories.append({
                "id": cat.value,
                "label": category_label(cat),
                "label_en": category_label(cat, en=True),
                "sources": srcs,
                "indicator_count": ind_count + ns_count,
            })
        return {
            "categories": categories,
            "total_sources": len(SOURCE_CATEGORY),
        }
    finally:
        conn.close()


# ── Risk Ratings ───────────────────────────────────────────────


@app.get("/api/v1/risk/indicators", tags=["Risk Ratings"])
def list_risk_indicators(
    source: Optional[str] = Query(None, description="Filter by risk source: aml, sanctions"),
):
    """List all country risk indicators (AML ratings, sanctions, CPI)."""
    conn = init_db()
    try:
        risk_sources = sources_by_category(DataCategory.COUNTRY_RISK)
        if source:
            if source not in risk_sources:
                raise HTTPException(400, f"Unknown risk source: {source}. Available: {risk_sources}")
            df = get_indicators(conn, source=source)
        else:
            df = get_indicators(conn, sources=risk_sources)
        return _df_to_list(df)
    finally:
        conn.close()


@app.get("/api/v1/risk/indicators/{indicator_id}", tags=["Risk Ratings"])
def get_risk_indicator(indicator_id: int):
    """Get a single risk indicator's metadata."""
    conn = init_db()
    try:
        row = get_indicator(conn, indicator_id)
        if row is None:
            raise HTTPException(404, f"Indicator {indicator_id} not found")
        # Verify it's a risk indicator
        if get_category(row["source"]) != DataCategory.COUNTRY_RISK:
            raise HTTPException(404, f"Indicator {indicator_id} is not a risk indicator")
        if isinstance(row.get("params"), str):
            import json
            try:
                row["params"] = json.loads(row["params"])
            except (json.JSONDecodeError, TypeError):
                pass
        if row.get("last_updated"):
            row["last_updated"] = str(row["last_updated"])
        return row
    finally:
        conn.close()


@app.get("/api/v1/risk/data/{indicator_id}", tags=["Risk Ratings"])
def query_risk_data(
    indicator_id: int,
    start: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    limit: int = Query(1000, ge=1, le=50000, description="Max rows to return"),
):
    """Query time-series data for a risk indicator."""
    conn = init_db()
    try:
        meta = get_indicator(conn, indicator_id)
        if meta is None:
            raise HTTPException(404, f"Indicator {indicator_id} not found")
        if get_category(meta["source"]) != DataCategory.COUNTRY_RISK:
            raise HTTPException(404, f"Indicator {indicator_id} is not a risk indicator")

        df = get_data(conn, indicator_id, start=start, end=end, limit=limit)
        return {
            "indicator": meta,
            "count": len(df),
            "data": _df_to_records(df),
        }
    finally:
        conn.close()


@app.get("/api/v1/risk/data/{indicator_id}/latest", tags=["Risk Ratings"])
def risk_latest(indicator_id: int):
    """Get the most recent observation for a risk indicator."""
    conn = init_db()
    try:
        meta = get_indicator(conn, indicator_id)
        if meta is None:
            raise HTTPException(404, f"Indicator {indicator_id} not found")
        if get_category(meta["source"]) != DataCategory.COUNTRY_RISK:
            raise HTTPException(404, f"Indicator {indicator_id} is not a risk indicator")

        df = get_data(conn, indicator_id, limit=1)
        if df.empty:
            return {"indicator": meta, "latest": None}
        return {
            "indicator": meta,
            "latest": {"date": str(df.iloc[0]["date"]), "value": df.iloc[0]["value"]},
        }
    finally:
        conn.close()


@app.post("/api/v1/risk/fetch", tags=["Risk Ratings"])
def risk_fetch():
    """Refresh all country risk data sources (AML, sanctions, CPI)."""
    risk_sources = sources_by_category(DataCategory.COUNTRY_RISK)
    summary = run_once(sources=risk_sources)
    return summary


# ── Name Screening ───────────────────────────────────────────


class NameScreeningRequest(BaseModel):
    query: str
    include_news: bool = False


class NameScreeningBatchRequest(BaseModel):
    queries: list[str]
    include_news: bool = False


@app.post("/api/v1/name-screening/search", tags=["Name Screening"])
def screen_name(body: NameScreeningRequest):
    """Comprehensive name screening — sanctions, PEP, negative news, Chinese court.

    Supports Chinese (中文) and English names with fuzzy matching.
    Set include_news=true to also search GDELT for negative news.
    """
    from app.eco_harness.name_screening import NameScreeningHarness
    nsh = NameScreeningHarness()
    return nsh.screen(body.query, include_news=body.include_news)


@app.post("/api/v1/name-screening/batch", tags=["Name Screening"])
def screen_name_batch(body: NameScreeningBatchRequest):
    """Batch name screening — screen multiple names at once.

    Returns results for each query plus a summary of total hits.
    """
    from app.eco_harness.name_screening import NameScreeningHarness
    nsh = NameScreeningHarness()
    results = []
    total_hits = 0
    for query in body.queries:
        r = nsh.screen(query, include_news=body.include_news)
        results.append(r)
        total_hits += r.get("total_hits", 0)
    return {"total_queries": len(body.queries), "total_hits": total_hits, "results": results}


@app.get("/api/v1/name-screening/stats", tags=["Name Screening"])
def name_screening_stats():
    """Get name screening database statistics."""
    from app.eco_harness.name_screening import NameScreeningHarness
    nsh = NameScreeningHarness()
    return nsh.get_stats()


@app.post("/api/v1/name-screening/load-opensanctions", tags=["Name Screening"])
def load_opensanctions(max_entities: int = Query(0, description="Max entities to load (0 = all)")):
    """Download and load OpenSanctions bulk data into the name screening database.

    This may take several minutes. Returns load statistics.
    """
    from app.eco_harness.name_screening import NameScreeningHarness
    nsh = NameScreeningHarness()
    return nsh.load_opensanctions(max_entities=max_entities)


# ── Health ───────────────────────────────────────────────────


@app.get("/api/v1/health", tags=["System"])
def health():
    """Service health check."""
    conn = init_db()
    try:
        count = conn.execute("SELECT COUNT(*) FROM indicators").fetchone()[0]
        obs_count = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
        return {
            "status": "ok",
            "indicators": count,
            "observations": obs_count,
        }
    finally:
        conn.close()


# ── Helpers ──────────────────────────────────────────────────


def _df_to_list(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """Convert DataFrame to list of dicts, converting types for JSON."""
    records = df.to_dict(orient="records")
    for r in records:
        for k, v in r.items():
            if isinstance(v, pd.Timestamp):
                r[k] = str(v)
            elif pd.isna(v):
                r[k] = None
        # Parse params JSON string
        if "params" in r and isinstance(r["params"], str):
            import json
            try:
                r["params"] = json.loads(r["params"])
            except (json.JSONDecodeError, TypeError):
                pass
    return records


def _df_to_records(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """Convert observations DataFrame to JSON-serializable records."""
    has_notes = "notes" in df.columns
    records = []
    for _, row in df.iterrows():
        entry = {
            "date": str(row["date"]),
            "value": row["value"],
        }
        if has_notes and row.get("notes"):
            entry["notes"] = str(row["notes"])
        records.append(entry)
    return records
