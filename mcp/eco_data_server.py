#!/usr/bin/env python3
"""
Eco Data MCP Server — lightweight MCP implementation over stdio.

Exposes macroeconomic and China stock data as MCP tools for AI agents.
No external MCP SDK needed — implements the JSON-RPC protocol directly.

Claude Code config (~/.claude.json or project .mcp.json):
{
  "mcpServers": {
    "eco-data": {
      "command": "python3",
      "args": ["mcp/eco_data_server.py"],
      "cwd": "/path/to/cibo eco data"
    }
  }
}
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

import duckdb

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "eco_data.duckdb")


def _conn():
    return duckdb.connect(DB_PATH, read_only=True)


# ── Tool implementations ──────────────────────────────────────


def tool_list_indicators(source: str = "") -> list[dict]:
    """List all available economic indicators with metadata."""
    conn = _conn()
    try:
        if source:
            rows = conn.execute(
                "SELECT id, source, name, method, description, frequency, last_updated "
                "FROM indicators WHERE source = ? ORDER BY id",
                [source],
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, source, name, method, description, frequency, last_updated "
                "FROM indicators ORDER BY source, id"
            ).fetchall()

        return [
            {
                "id": r[0],
                "source": r[1],
                "name": r[2],
                "method": r[3],
                "description": r[4],
                "frequency": r[5],
                "last_updated": str(r[6]) if r[6] else None,
            }
            for r in rows
        ]
    finally:
        conn.close()


def tool_query_data(
    indicator_id: int,
    start: str = "",
    end: str = "",
    limit: int = 100,
) -> dict:
    """Query time-series observations for a given indicator."""
    conn = _conn()
    try:
        # Get indicator metadata
        meta = conn.execute(
            "SELECT id, source, name, description, frequency FROM indicators WHERE id = ?",
            [indicator_id],
        ).fetchone()
        if not meta:
            return {"error": f"Indicator {indicator_id} not found"}

        # Build query
        sql = "SELECT date, value FROM observations WHERE indicator_id = ?"
        params: list[Any] = [indicator_id]
        if start:
            sql += " AND date >= ?"
            params.append(start)
        if end:
            sql += " AND date <= ?"
            params.append(end)
        sql += " ORDER BY date DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(sql, params).fetchall()
        data = [{"date": str(r[0]), "value": r[1]} for r in rows]

        return {
            "indicator": {
                "id": meta[0],
                "source": meta[1],
                "name": meta[2],
                "description": meta[3],
                "frequency": meta[4],
            },
            "count": len(data),
            "data": data,
        }
    finally:
        conn.close()


def tool_get_latest(indicator_id: int) -> dict:
    """Get the most recent observation for an indicator."""
    return tool_query_data(indicator_id, limit=1)


def tool_search_indicators(query: str) -> list[dict]:
    """Search indicators by keyword in name and description."""
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT id, source, name, method, description, frequency FROM indicators "
            "WHERE name ILIKE ? OR description ILIKE ? ORDER BY id",
            [f"%{query}%", f"%{query}%"],
        ).fetchall()
        return [
            {
                "id": r[0],
                "source": r[1],
                "name": r[2],
                "method": r[3],
                "description": r[4],
                "frequency": r[5],
            }
            for r in rows
        ]
    finally:
        conn.close()


def tool_data_summary() -> dict:
    """Get a high-level summary of the entire eco data platform:
    total indicators, total observations, breakdown by source with descriptions."""
    conn = _conn()
    try:
        total_indicators = conn.execute("SELECT COUNT(*) FROM indicators").fetchone()[0]
        total_obs = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
        by_source = conn.execute(
            "SELECT source, COUNT(*) as cnt FROM indicators GROUP BY source ORDER BY cnt DESC"
        ).fetchall()
        by_freq = conn.execute(
            "SELECT frequency, COUNT(*) as cnt FROM indicators GROUP BY frequency ORDER BY cnt DESC"
        ).fetchall()
        return {
            "total_indicators": total_indicators,
            "total_observations": total_obs,
            "sources": [
                {
                    "id": r[0],
                    "label": SOURCE_META.get(r[0], {}).get("label", r[0]),
                    "count": r[1],
                    "description": SOURCE_META.get(r[0], {}).get("description", ""),
                }
                for r in by_source
            ],
            "by_frequency": [{"frequency": r[0], "count": r[1]} for r in by_freq],
            "data_path": DB_PATH,
        }
    finally:
        conn.close()


def tool_cn_stock_status() -> dict:
    """Get China stock limit-up data status (cn_stock DuckDB)."""
    cn_db = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cn_stock", "cn_stock.duckdb")
    if not os.path.exists(cn_db):
        return {"error": "cn_stock database not found"}

    conn = duckdb.connect(cn_db, read_only=True)
    try:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        result = {"path": cn_db, "tables": {}}
        for (tname,) in tables:
            cnt = conn.execute(f'SELECT COUNT(*) FROM "{tname}"').fetchone()[0]
            result["tables"][tname] = cnt
        return result
    finally:
        conn.close()


def tool_data_sources_by_category() -> dict:
    """List all data sources grouped by three categories."""
    from app.categories import DataCategory, sources_by_category, category_label
    result = {}
    for cat in DataCategory:
        srcs = sources_by_category(cat)
        result[cat.value] = {
            "label": category_label(cat),
            "label_en": category_label(cat, en=True),
            "sources": [
                {
                    "id": s,
                    "label": SOURCE_META.get(s, {}).get("label", s),
                    "description": SOURCE_META.get(s, {}).get("description", ""),
                }
                for s in srcs
            ],
        }
    return result


def tool_list_risk_indicators(source: str = "") -> list[dict]:
    """List country risk indicators (AML, sanctions, CPI)."""
    from app.categories import DataCategory, sources_by_category
    conn = _conn()
    try:
        risk_sources = sources_by_category(DataCategory.COUNTRY_RISK)
        if source:
            if source not in risk_sources:
                return [{"error": f"Unknown risk source: {source}. Available: {risk_sources}"}]
            rows = conn.execute(
                "SELECT id, source, name, method, description, frequency, last_updated "
                "FROM indicators WHERE source = ? ORDER BY id",
                [source],
            ).fetchall()
        else:
            placeholders = ",".join(["?"] * len(risk_sources))
            rows = conn.execute(
                f"SELECT id, source, name, method, description, frequency, last_updated "
                f"FROM indicators WHERE source IN ({placeholders}) ORDER BY source, id",
                risk_sources,
            ).fetchall()
        return [
            {
                "id": r[0],
                "source": r[1],
                "name": r[2],
                "method": r[3],
                "description": r[4],
                "frequency": r[5],
                "last_updated": str(r[6]) if r[6] else None,
            }
            for r in rows
        ]
    finally:
        conn.close()


def tool_list_tags() -> list[dict]:
    """List all tags with indicator counts — browse data by topic without knowing keywords."""
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT tags FROM indicators WHERE tags IS NOT NULL AND tags != ''"
        ).fetchall()
        tag_counts: dict[str, int] = {}
        for (tag_str,) in rows:
            for t in tag_str.split(","):
                t = t.strip()
                if t:
                    tag_counts[t] = tag_counts.get(t, 0) + 1
        return sorted(
            [{"tag": tag, "count": count} for tag, count in tag_counts.items()],
            key=lambda x: x["count"], reverse=True
        )
    finally:
        conn.close()


def tool_search_name(query: str, include_news: bool = False) -> dict:
    """Comprehensive name screening against sanctions, PEP, news, and court records."""
    from app.eco_harness.name_screening import NameScreeningHarness
    nsh = NameScreeningHarness()
    return nsh.screen(query, include_news=include_news)


def tool_name_screening_stats() -> dict:
    """Get name screening database statistics."""
    from app.eco_harness.name_screening import NameScreeningHarness
    nsh = NameScreeningHarness()
    return nsh.get_stats()


# ── HK Funds tools ─────────────────────────────────────────────

_HK_DB = os.path.join(os.path.dirname(os.path.dirname(__file__)), "hk_funds.duckdb")
_US_CORP_DB = os.path.join(os.path.dirname(os.path.dirname(__file__)), "us_corp_actions.duckdb")
_US_LIST_DB = os.path.join(os.path.dirname(os.path.dirname(__file__)), "us_listings", "us_listings.duckdb")
_ANNO_DB = os.path.join(os.path.dirname(os.path.dirname(__file__)), "announcements", "announcements.duckdb")
_KR_DB = os.path.join(os.path.dirname(os.path.dirname(__file__)), "kr_stock", "kr_stock.duckdb")
_TW_DB = os.path.join(os.path.dirname(os.path.dirname(__file__)), "tw_stock", "tw_stock.duckdb")
_HYNIX_DB = os.path.join(os.path.dirname(os.path.dirname(__file__)), "hynix", "hynix.duckdb")


def _hk_conn():
    return duckdb.connect(_HK_DB, read_only=True)


def _hk_conn_rw():
    """Read-write connection for rating/write operations."""
    return duckdb.connect(_HK_DB, read_only=False)


def _us_corp_conn():
    return duckdb.connect(_US_CORP_DB, read_only=True)


def _us_list_conn():
    return duckdb.connect(_US_LIST_DB, read_only=True)


def _anno_conn():
    return duckdb.connect(_ANNO_DB, read_only=True)


def _kr_conn():
    return duckdb.connect(_KR_DB, read_only=True)


def _tw_conn():
    return duckdb.connect(_TW_DB, read_only=True)


def _hynix_conn():
    return duckdb.connect(_HYNIX_DB, read_only=True)


def _serialize_rows(rows, cols):
    """Serialize rows to list of dicts with ISO date strings."""
    result = []
    for r in rows:
        d = {}
        for i, c in enumerate(cols):
            v = r[i]
            if hasattr(v, "isoformat"):
                s = v.isoformat()
                d[c] = None if s == "NaT" else s
            elif isinstance(v, float) and (v != v or v == float("inf") or v == float("-inf")):
                d[c] = None
            else:
                d[c] = v
        result.append(d)
    return result


def tool_hk_fund_stats() -> dict:
    """Get HK fund statistics: total funds, complex/derivative breakdown, ISIN coverage, NAV records, performance records, risk distribution."""
    conn = _hk_conn()
    try:
        total = conn.execute("SELECT COUNT(*) FROM hk_funds WHERE is_active = true").fetchone()[0]
        complex_cnt = conn.execute("SELECT COUNT(*) FROM hk_funds WHERE is_active = true AND is_complex_product = true").fetchone()[0]
        deriv_cnt = conn.execute("SELECT COUNT(*) FROM hk_funds WHERE is_active = true AND is_derivative_product = true").fetchone()[0]
        ofc_cnt = conn.execute("SELECT COUNT(*) FROM hk_funds WHERE fund_type = 'open_ended_fund_company' AND is_active = true").fetchone()[0]
        priv_cnt = conn.execute("SELECT COUNT(*) FROM hk_non_authorized_funds WHERE is_active = true").fetchone()[0]
        isin_cnt = conn.execute(
            "SELECT COUNT(*) FROM hk_funds WHERE is_active = true AND isin IS NOT NULL AND isin != ''"
        ).fetchone()[0]
        nav_cnt = conn.execute("SELECT COUNT(*) FROM hk_fund_nav_history").fetchone()[0]
        nav_funds = conn.execute(
            "SELECT COUNT(DISTINCT fund_id) FROM hk_fund_nav_history"
        ).fetchone()[0]
        perf_cnt = conn.execute("SELECT COUNT(*) FROM hk_fund_performance").fetchone()[0]
        risk_dist = conn.execute(
            "SELECT risk_category, COUNT(*) FROM hk_fund_risk_ratings GROUP BY risk_category ORDER BY COUNT(*) DESC"
        ).fetchall()
        return {
            "total_active_funds": total,
            "complex_products": complex_cnt,
            "derivative_products": deriv_cnt,
            "ofc_funds": ofc_cnt,
            "private_non_authorized": priv_cnt,
            "funds_with_isin": isin_cnt,
            "nav_records": nav_cnt,
            "funds_with_nav": nav_funds,
            "performance_records": perf_cnt,
            "risk_distribution": {r[0]: r[1] for r in risk_dist},
        }
    finally:
        conn.close()


def tool_hk_fund_risk_ratings(risk_category: str = "", limit: int = 200, offset: int = 0) -> dict:
    """Get HK fund risk ratings (5-tier), optionally filtered by category."""
    conn = _hk_conn()
    try:
        sql = """
            SELECT f.id as fund_id, f.fund_name_en, f.sfc_authorization_no,
                   f.fund_manager_name_en, f.is_derivative_product,
                   f.is_complex_product, f.complex_product_type,
                   r.overall_risk_score, r.risk_category, r.is_automated,
                   r.last_calculated
            FROM hk_funds f
            LEFT JOIN hk_fund_risk_ratings r ON f.id = r.fund_id
            WHERE f.is_active = true
        """
        params: list = []
        if risk_category:
            sql += " AND r.risk_category = ?"
            params.append(risk_category)
        sql += " ORDER BY r.overall_risk_score DESC NULLS LAST LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = conn.execute(sql, params).fetchall()
        cols = ["fund_id", "fund_name_en", "sfc_authorization_no", "fund_manager_name_en",
                "is_derivative_product", "is_complex_product", "complex_product_type",
                "overall_risk_score", "risk_category", "is_automated", "last_calculated"]
        return {"count": len(rows), "ratings": _serialize_rows(rows, cols)}
    finally:
        conn.close()


def tool_hk_kyp_dimensions(fund_id: int) -> dict:
    """Get all 10 KYP due diligence dimensions for a specific HK fund."""
    conn = _hk_conn()
    try:
        rows = conn.execute("""
            SELECT dimension, assessment_status, score, data_source,
                   assessment_date, next_review_date, findings, gaps
            FROM hk_kyp_dimensions WHERE fund_id = ?
            ORDER BY CASE dimension
                WHEN 'product_structure' THEN 1 WHEN 'risk_profile' THEN 2
                WHEN 'complexity' THEN 3 WHEN 'derivative_class' THEN 4
                WHEN 'issuer_assessment' THEN 5 WHEN 'fees_charges' THEN 6
                WHEN 'liquidity_lockup' THEN 7 WHEN 'valuation_pricing' THEN 8
                WHEN 'credit_quality' THEN 9 WHEN 'key_terms' THEN 10
            END
        """, [fund_id]).fetchall()
        cols = ["dimension", "assessment_status", "score", "data_source",
                "assessment_date", "next_review_date", "findings", "gaps"]
        return {"fund_id": fund_id, "dimensions": _serialize_rows(rows, cols)}
    finally:
        conn.close()


def tool_hk_kyp_gaps(limit: int = 50) -> dict:
    """Get HK funds with incomplete KYP assessments (missing or pending dimensions)."""
    conn = _hk_conn()
    try:
        rows = conn.execute("""
            SELECT f.id, f.fund_name_en, f.sfc_authorization_no,
                   COUNT(kd.id) as dimensions_assessed,
                   (10 - COUNT(kd.id)) as dimensions_missing
            FROM hk_funds f
            LEFT JOIN hk_kyp_dimensions kd ON f.id = kd.fund_id
                AND kd.assessment_status IN ('reviewed', 'approved')
            WHERE f.is_active = true
            GROUP BY f.id, f.fund_name_en, f.sfc_authorization_no
            HAVING dimensions_missing > 0 OR COUNT(kd.id) = 0
            ORDER BY dimensions_missing DESC LIMIT ?
        """, [limit]).fetchall()
        cols = ["fund_id", "fund_name_en", "sfc_authorization_no", "dimensions_assessed", "dimensions_missing"]
        return {"count": len(rows), "funds": _serialize_rows(rows, cols)}
    finally:
        conn.close()


def tool_hk_complex_products(complex_product_type: str = "", limit: int = 100) -> dict:
    """List §5.5 complex products. Optionally filter by type: structured, derivative_fund, L&I, synthetic_etf."""
    conn = _hk_conn()
    try:
        sql = "SELECT * FROM hk_funds WHERE is_active = true AND is_complex_product = true"
        params: list = []
        if complex_product_type:
            sql += " AND complex_product_type = ?"
            params.append(complex_product_type)
        sql += " ORDER BY complex_product_type, fund_name_en LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        cols = [desc[0] for desc in conn.description]
        return {"count": len(rows), "funds": _serialize_rows(rows, cols)}
    finally:
        conn.close()


def tool_hk_derivative_products(limit: int = 100) -> dict:
    """List §5.1A derivative products (NDE > 50%, synthetic, L&I, hedge funds)."""
    conn = _hk_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM hk_funds WHERE is_active = true AND is_derivative_product = true "
            "ORDER BY complex_product_type, fund_name_en LIMIT ?", [limit]
        ).fetchall()
        cols = [desc[0] for desc in conn.description]
        return {"count": len(rows), "funds": _serialize_rows(rows, cols)}
    finally:
        conn.close()


def tool_hk_ofc_stats() -> dict:
    """Get OFC (Open-ended Fund Company) statistics: public/private counts, umbrella/sub-fund breakdown."""
    conn = _hk_conn()
    try:
        pub_umb = conn.execute(
            "SELECT COUNT(*) FROM hk_funds WHERE fund_type='open_ended_fund_company' AND is_active=true AND umbrella_fund_ce IS NULL"
        ).fetchone()[0]
        pub_sf = conn.execute(
            "SELECT COUNT(*) FROM hk_funds WHERE fund_type='open_ended_fund_company' AND is_active=true AND umbrella_fund_ce IS NOT NULL"
        ).fetchone()[0]
        priv = conn.execute(
            "SELECT COUNT(*) FROM hk_non_authorized_funds WHERE data_source='sfc_ofc_register' AND is_active=true"
        ).fetchone()[0]
        managers = conn.execute(
            "SELECT COUNT(DISTINCT fund_manager_name_en) FROM hk_funds WHERE fund_type='open_ended_fund_company' AND fund_manager_name_en IS NOT NULL"
        ).fetchone()[0]
        return {
            "public_ofcs": {"umbrellas": pub_umb, "sub_funds": pub_sf, "total": pub_umb + pub_sf},
            "private_ofcs": {"total": priv},
            "unique_managers_public": managers,
        }
    finally:
        conn.close()


def tool_hk_non_authorized_funds(distribution_restriction: str = "", limit: int = 100) -> dict:
    """List non-SFC-authorized funds (PI-only). Includes 2,231 Private OFCs + manually entered funds."""
    conn = _hk_conn()
    try:
        sql = "SELECT * FROM hk_non_authorized_funds WHERE is_active = true"
        params: list = []
        if distribution_restriction:
            sql += " AND distribution_restriction = ?"
            params.append(distribution_restriction)
        sql += " ORDER BY fund_name_en LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        cols = [desc[0] for desc in conn.description]
        return {"count": len(rows), "funds": _serialize_rows(rows, cols)}
    finally:
        conn.close()


def tool_hk_fund_managers(search: str = "", limit: int = 100) -> dict:
    """List HK fund managers (SFC licensed corporations)."""
    conn = _hk_conn()
    try:
        sql = "SELECT id, ce_number, company_name_en, company_name_cn, license_type, license_status FROM hk_fund_managers WHERE 1=1"
        params: list = []
        if search:
            sql += " AND (company_name_en ILIKE ? OR company_name_cn ILIKE ? OR ce_number = ?)"
            like = f"%{search}%"
            params.extend([like, like, search])
        sql += " ORDER BY company_name_en LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        cols = [desc[0] for desc in conn.description]
        return {"count": len(rows), "managers": _serialize_rows(rows, cols)}
    finally:
        conn.close()


def tool_hk_manager_dd(manager_id: int) -> dict:
    """Get 10-dimension due diligence assessment for a fund manager.

    Returns DD dimensions plus internal_control score derived from
    the 10-dimension pass count, mapped to the Scorecard 5-tier system
    (Strong/Sufficient/Average/Limited/Lacking).
    """
    conn = _hk_conn()
    try:
        rows = conn.execute("""
            SELECT dd_dimension, assessment_status, score, data_source,
                   assessment_date, next_review_date, findings, gaps
            FROM hk_manager_dd WHERE manager_id = ?
            ORDER BY CASE dd_dimension
                WHEN 'financial_resources' THEN 1 WHEN 'human_resources' THEN 2
                WHEN 'internal_controls' THEN 3 WHEN 'risk_governance' THEN 4
                WHEN 'segregation_duties' THEN 5 WHEN 'compliance_function' THEN 6
                WHEN 'audit_function' THEN 7 WHEN 'custodian_dd' THEN 8
                WHEN 'valuer_dd' THEN 9 WHEN 'delegates_monitoring' THEN 10
            END
        """, [manager_id]).fetchall()
        cols = ["dd_dimension", "assessment_status", "score", "data_source",
                "assessment_date", "next_review_date", "findings", "gaps"]
        dd_list = _serialize_rows(rows, cols)

        # Compute internal control score from DD dimensions
        from hk_funds.manager_scoring import score_internal_control
        ic_score = score_internal_control(dd_list)

        return {
            "manager_id": manager_id,
            "dd_dimensions": dd_list,
            "internal_control_score": ic_score,
        }
    finally:
        conn.close()


def tool_hk_fund_search(query: str, limit: int = 50) -> dict:
    """Search HK funds by name (EN/CN), ISIN, authorization number, or manager name."""
    conn = _hk_conn()
    try:
        like = f"%{query}%"
        rows = conn.execute("""
            SELECT id, fund_name_en, fund_name_cn, sfc_authorization_no, fund_type,
                   fund_manager_name_en, is_derivative_product, is_complex_product,
                   complex_product_type, domicile
            FROM hk_funds WHERE is_active = true AND (
                fund_name_en ILIKE ? OR fund_name_cn ILIKE ? OR
                isin = ? OR sfc_authorization_no = ? OR
                fund_manager_name_en ILIKE ?
            ) ORDER BY fund_name_en LIMIT ?
        """, [like, like, query, query, like, limit]).fetchall()
        cols = ["id", "fund_name_en", "fund_name_cn", "sfc_authorization_no", "fund_type",
                "fund_manager_name_en", "is_derivative_product", "is_complex_product",
                "complex_product_type", "domicile"]
        return {"count": len(rows), "query": query, "funds": _serialize_rows(rows, cols)}
    finally:
        conn.close()


def tool_hk_fund_isin_lookup(isin: str) -> dict:
    """Look up an HK fund by its ISIN code. Returns full fund details with manager info."""
    conn = _hk_conn()
    try:
        isin = isin.strip().upper()
        row = conn.execute(
            "SELECT * FROM hk_funds WHERE isin = ?", [isin]
        ).fetchone()
        if not row:
            return {"found": False, "isin": isin, "message": f"No fund found with ISIN {isin}"}
        cols = [desc[0] for desc in conn.description]
        fund = dict(zip(cols, row))
        fund = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in fund.items()}
        # Get latest NAV
        nav_row = conn.execute(
            "SELECT nav, nav_date, nav_currency FROM hk_fund_nav_history "
            "WHERE fund_id = ? ORDER BY nav_date DESC LIMIT 1", [fund["id"]]
        ).fetchone()
        if nav_row:
            fund["latest_nav"] = {"nav": nav_row[0], "nav_date": str(nav_row[1]) if nav_row[1] else None, "nav_currency": nav_row[2]}
        # Get performance
        perf_row = conn.execute(
            "SELECT * FROM hk_fund_performance WHERE fund_id = ?", [fund["id"]]
        ).fetchone()
        if perf_row:
            perf_cols = [desc[0] for desc in conn.description]
            fund["performance"] = dict(zip(perf_cols, perf_row))
            fund["performance"] = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in fund["performance"].items()}
        return {"found": True, "fund": fund}
    finally:
        conn.close()


def tool_hk_fund_isins(limit: int = 200, offset: int = 0) -> dict:
    """List HK funds that have ISIN codes, with fund type and manager."""
    conn = _hk_conn()
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM hk_funds WHERE is_active = true AND isin IS NOT NULL AND isin != ''"
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT id, fund_name_en, isin, fund_manager_name_en, fund_type, domicile "
            "FROM hk_funds WHERE is_active = true AND isin IS NOT NULL AND isin != '' "
            "ORDER BY fund_name_en LIMIT ? OFFSET ?", [limit, offset]
        ).fetchall()
        cols = ["id", "fund_name_en", "isin", "fund_manager_name_en", "fund_type", "domicile"]
        return {"total": total, "count": len(rows), "funds": _serialize_rows(rows, cols)}
    finally:
        conn.close()


def tool_hk_fund_nav_history(fund_id: int, start: str = "", end: str = "", limit: int = 500) -> dict:
    """Get NAV time series for an HK fund. Returns nav_date, nav, nav_currency."""
    conn = _hk_conn()
    try:
        fund = conn.execute(
            "SELECT id, fund_name_en FROM hk_funds WHERE id = ?", [fund_id]
        ).fetchone()
        if not fund:
            return {"error": f"Fund {fund_id} not found"}
        sql = "SELECT nav_date, nav, nav_currency FROM hk_fund_nav_history WHERE fund_id = ?"
        params: list = [fund_id]
        if start:
            sql += " AND nav_date >= ?"
            params.append(start)
        if end:
            sql += " AND nav_date <= ?"
            params.append(end)
        sql += " ORDER BY nav_date DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        cols = ["nav_date", "nav", "nav_currency"]
        return {"fund_id": fund_id, "fund_name": fund[1], "count": len(rows),
                "nav_history": _serialize_rows(rows, cols)}
    finally:
        conn.close()


def tool_hk_fund_latest_nav(fund_id: int) -> dict:
    """Get the latest NAV for an HK fund."""
    conn = _hk_conn()
    try:
        fund = conn.execute(
            "SELECT id, fund_name_en FROM hk_funds WHERE id = ?", [fund_id]
        ).fetchone()
        if not fund:
            return {"error": f"Fund {fund_id} not found"}
        row = conn.execute(
            "SELECT nav, nav_date, nav_currency FROM hk_fund_nav_history "
            "WHERE fund_id = ? ORDER BY nav_date DESC LIMIT 1", [fund_id]
        ).fetchone()
        if not row:
            return {"fund_id": fund_id, "fund_name": fund[1], "nav": None, "message": "No NAV data"}
        return {"fund_id": fund_id, "fund_name": fund[1],
                "nav": row[0], "nav_date": str(row[1]) if row[1] else None,
                "nav_currency": row[2]}
    finally:
        conn.close()


def tool_hk_fund_performance(fund_id: int) -> dict:
    """Get performance metrics for an HK fund: returns (YTD, 1M, 3M, 6M, 1Y, 3Y, 5Y annualized), risk metrics (std dev, Sharpe, max drawdown, alpha, beta, R²)."""
    conn = _hk_conn()
    try:
        fund = conn.execute(
            "SELECT id, fund_name_en FROM hk_funds WHERE id = ?", [fund_id]
        ).fetchone()
        if not fund:
            return {"error": f"Fund {fund_id} not found"}
        row = conn.execute(
            "SELECT * FROM hk_fund_performance WHERE fund_id = ?", [fund_id]
        ).fetchone()
        if not row:
            return {"fund_id": fund_id, "fund_name": fund[1], "performance": None,
                    "message": "No performance data"}
        cols = [desc[0] for desc in conn.description]
        perf = dict(zip(cols, row))
        perf = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in perf.items()}
        return {"fund_id": fund_id, "fund_name": fund[1], "performance": perf}
    finally:
        conn.close()


def tool_hk_manager_scrape_status() -> dict:
    """Get HK fund manager connector registry status: registered connectors with CE numbers, websites, and managers still needing connectors."""
    conn = _hk_conn()
    try:
        # Registered CE numbers via connectors module
        try:
            from hk_funds.manager_connectors import (
                get_all_registered_ce_numbers,
                get_website_for_manager,
                list_managers_needing_connectors,
            )
            registered = get_all_registered_ce_numbers()
            needs = list_managers_needing_connectors(conn, min_funds=3)
        except ImportError:
            registered = set()
            needs = []

        connectors = []
        for ce in sorted(registered):
            mgr = conn.execute(
                "SELECT company_name_en FROM hk_fund_managers WHERE ce_number = ?", [ce]
            ).fetchone()
            website = ""
            try:
                website = get_website_for_manager(ce) or ""
            except Exception:
                pass
            connectors.append({
                "ce_number": ce,
                "company_name_en": mgr[0] if mgr else "Unknown",
                "website": website,
            })

        top_without = []
        for m in needs[:15]:
            if not m.get("has_connector"):
                top_without.append({
                    "ce_number": m["ce_number"],
                    "company_name_en": m["company_name_en"],
                    "fund_count": m["fund_count"],
                })

        # Profile extraction stats
        profile_count = conn.execute(
            "SELECT COUNT(*) FROM hk_manager_profiles"
        ).fetchone()[0]
        profile_aum_count = conn.execute(
            "SELECT COUNT(*) FROM hk_manager_profiles WHERE aum_usd IS NOT NULL"
        ).fetchone()[0]
        profile_staff_count = conn.execute(
            "SELECT COUNT(*) FROM hk_manager_profiles WHERE total_staff IS NOT NULL"
        ).fetchone()[0]

        return {
            "registered_connectors": len(registered),
            "connectors": connectors,
            "managers_needing_connectors": len(needs),
            "top_managers_without_connectors": top_without,
            "profile_extractions": {
                "total": profile_count,
                "with_aum": profile_aum_count,
                "with_staff": profile_staff_count,
            },
        }
    finally:
        conn.close()


def tool_hk_fund_holdings(fund_id: int, limit: int = 20) -> dict:
    """Get top holdings for a HK SFC-authorized fund. Shows name, weight_pct, sector, country where available."""
    conn = _hk_conn()
    try:
        from hk_funds.storage import get_holdings
        holdings = get_holdings(conn, fund_id, limit=limit)
        return {"fund_id": fund_id, "count": len(holdings), "holdings": holdings}
    finally:
        conn.close()


def tool_hk_fund_dividends(fund_id: int, limit: int = 20) -> dict:
    """Get dividend history for a HK SFC-authorized fund. Shows ex_date, pay_date, dividend_amount, dividend_type."""
    conn = _hk_conn()
    try:
        from hk_funds.storage import get_dividends
        dividends = get_dividends(conn, fund_id, limit=limit)
        return {"fund_id": fund_id, "count": len(dividends), "dividends": dividends}
    finally:
        conn.close()


def tool_hk_fund_share_classes(fund_id: int) -> dict:
    """Get share classes for a HK SFC-authorized fund. Shows ISIN, currency, hedging, distribution type for each share class variant."""
    conn = _hk_conn()
    try:
        from hk_funds.storage import get_share_classes
        share_classes = get_share_classes(conn, fund_id)
        return {"fund_id": fund_id, "count": len(share_classes), "share_classes": share_classes}
    finally:
        conn.close()


def tool_hk_fund_portfolio_manager(fund_id: int = 0, search: str = "",
                                    limit: int = 50) -> dict:
    """Query portfolio manager info for HK SFC-authorized funds.

    If fund_id is provided, returns the portfolio manager for that fund.
    If search is provided, finds funds managed by that person/entity.
    Returns fund name, portfolio manager, fund manager, and fund type.
    """
    conn = _hk_conn()
    try:
        if fund_id:
            row = conn.execute("""
                SELECT id, fund_name_en, fund_manager_name_en,
                       portfolio_manager_name, fund_type, sfc_authorization_no
                FROM hk_funds
                WHERE id = ? AND is_active = true
            """, [fund_id]).fetchone()
            if row is None:
                return {"fund_id": fund_id, "error": "Fund not found"}
            cols = ["id", "fund_name_en", "fund_manager_name_en",
                    "portfolio_manager_name", "fund_type", "sfc_authorization_no"]
            return {"fund": dict(zip(cols, row))}

        if search:
            like = f"%{search}%"
            rows = conn.execute("""
                SELECT id, fund_name_en, fund_manager_name_en,
                       portfolio_manager_name, fund_type, sfc_authorization_no
                FROM hk_funds
                WHERE is_active = true
                  AND portfolio_manager_name IS NOT NULL
                  AND portfolio_manager_name != ''
                  AND (portfolio_manager_name ILIKE ?
                       OR fund_manager_name_en ILIKE ?)
                ORDER BY fund_name_en
                LIMIT ?
            """, [like, like, limit]).fetchall()
            cols = ["id", "fund_name_en", "fund_manager_name_en",
                    "portfolio_manager_name", "fund_type", "sfc_authorization_no"]
            return {
                "search": search,
                "count": len(rows),
                "funds": _serialize_rows(rows, cols),
            }

        # No params: return funds that have portfolio_manager_name populated
        rows = conn.execute("""
            SELECT id, fund_name_en, fund_manager_name_en,
                   portfolio_manager_name, fund_type
            FROM hk_funds
            WHERE is_active = true
              AND portfolio_manager_name IS NOT NULL
              AND portfolio_manager_name != ''
            ORDER BY fund_name_en
            LIMIT ?
        """, [limit]).fetchall()
        cols = ["id", "fund_name_en", "fund_manager_name_en",
                "portfolio_manager_name", "fund_type"]
        return {
            "count": len(rows),
            "funds_with_portfolio_manager": _serialize_rows(rows, cols),
        }
    finally:
        conn.close()


# ── HK Rating Templates ────────────────────────────────────────

def tool_hk_rating_template_list(user_id: str = "", template_type: str = "") -> dict:
    """List rating templates. If user_id provided, shows that user's templates.
    If empty, shows system templates. Filter by fund_risk or manager_dd."""
    conn = _hk_conn()
    try:
        from hk_funds.storage import get_system_templates, get_user_templates

        if user_id and user_id != "system":
            df = get_user_templates(conn, user_id, template_type or None)
        else:
            df = get_system_templates(conn, template_type or None)

        templates = []
        for _, row in df.iterrows():
            templates.append({
                "id": int(row["id"]),
                "name": row["name"],
                "description": row.get("description", ""),
                "template_type": row["template_type"],
                "is_system": bool(row.get("is_system", False)),
                "methodology_version": row.get("methodology_version", "1.0"),
                "factor_count": int(row.get("factor_count", 0)),
                "created_at": str(row.get("created_at", "")) if row.get("created_at") else "",
            })
        return {"count": len(templates), "templates": templates}
    finally:
        conn.close()


def tool_hk_rating_template_get(template_id: int) -> dict:
    """Get full template with all factors and thresholds."""
    conn = _hk_conn()
    try:
        from hk_funds.rating_engine import load_template

        tmpl = load_template(conn, template_id)
        if tmpl is None:
            return {"error": f"Template {template_id} not found"}

        # Serialize for JSON
        return {
            "id": tmpl["id"],
            "name": tmpl["name"],
            "description": tmpl.get("description", ""),
            "template_type": tmpl["template_type"],
            "methodology_version": tmpl.get("methodology_version", "1.0"),
            "is_system": tmpl.get("is_system", False),
            "category_thresholds": tmpl["category_thresholds"],
            "factors": [
                {
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


def tool_hk_rating_template_clone(source_template_id: int, user_id: str,
                                  new_name: str = "") -> dict:
    """Clone a template for a user. Source can be system or user template.
    Returns the new template with its factor definitions."""
    conn = _hk_conn_rw()
    try:
        from hk_funds.storage import clone_template

        new_name = new_name.strip() if new_name else ""
        new_id = clone_template(conn, source_template_id, user_id, new_name)
        if new_id is None:
            return {"error": "Clone failed — source template not found or clone error"}

        from hk_funds.rating_engine import load_template
        tmpl = load_template(conn, new_id)
        return {
            "cloned_template_id": new_id,
            "template": {
                "id": tmpl["id"],
                "name": tmpl["name"],
                "template_type": tmpl["template_type"],
                "is_system": tmpl.get("is_system", False),
                "factor_count": len(tmpl["factors"]),
                "factors": [
                    {"factor_key": f["factor_key"], "factor_label": f["factor_label"],
                     "weight": f["weight"], "ordinal": f["ordinal"]}
                    for f in tmpl["factors"]
                ],
            },
        }
    finally:
        conn.close()


def tool_hk_rating_template_update(template_id: int, user_id: str,
                                    factor_weights: str = "",
                                    category_thresholds: str = "",
                                    name: str = "", description: str = "") -> dict:
    """Update a user-owned template. Accepts JSON strings for factor_weights
    (e.g. '{"complexity": 0.30, "underlying_risk": 0.20, ...}') and
    category_thresholds (e.g. '[{"max":1.5,"label":"Low"},...]').
    Only the template owner can update. System templates cannot be modified."""
    conn = _hk_conn_rw()
    try:
        from hk_funds.storage import get_template, upsert_template, upsert_template_factors, get_template_factors

        tmpl = get_template(conn, template_id)
        if tmpl is None:
            return {"error": f"Template {template_id} not found"}
        if tmpl["is_system"]:
            return {"error": "System templates cannot be modified. Clone it first."}
        if tmpl["user_id"] != user_id:
            return {"error": f"Template owned by {tmpl['user_id']}, not {user_id}"}

        import json as _json
        changes = []

        # Update name/description
        if name and name.strip():
            conn.execute("""
                UPDATE hk_rating_templates SET name = ?, updated_at = now()
                WHERE id = ?
            """, [name.strip(), template_id])
            changes.append("name")

        if description and description.strip():
            conn.execute("""
                UPDATE hk_rating_templates SET description = ?, updated_at = now()
                WHERE id = ?
            """, [description.strip(), template_id])
            changes.append("description")

        # Update factor weights
        if factor_weights and factor_weights.strip():
            weights_dict = _json.loads(factor_weights)
            factors_df = get_template_factors(conn, template_id)
            updated = 0
            for _, frow in factors_df.iterrows():
                fk = frow["factor_key"]
                if fk in weights_dict:
                    conn.execute("""
                        UPDATE hk_template_factors SET weight = ?
                        WHERE template_id = ? AND factor_key = ?
                    """, [weights_dict[fk], template_id, fk])
                    updated += 1
            changes.append(f"{updated} factor weights")

        # Update category thresholds
        if category_thresholds and category_thresholds.strip():
            thresholds = _json.loads(category_thresholds)
            thresholds_str = _json.dumps(thresholds)
            conn.execute("""
                UPDATE hk_rating_templates SET category_thresholds_json = ?, updated_at = now()
                WHERE id = ?
            """, [thresholds_str, template_id])
            changes.append("thresholds")

        return {"template_id": template_id, "updated": changes}
    finally:
        conn.close()


def tool_hk_rating_compute(template_id: int, user_id: str,
                           target_type: str = "fund",
                           target_id: int = 0) -> dict:
    """Compute ratings using a template. If target_id provided, rates a single
    target (fund or manager). Otherwise rates all targets of the given type.
    Results are stored in hk_user_ratings."""
    conn = _hk_conn_rw()
    try:
        from hk_funds.rating_engine import (
            compute_ratings_batch, compute_manager_dd_batch, compute_single_rating,
        )

        if target_id:
            rating = compute_single_rating(conn, template_id, user_id, target_type, target_id)
            if rating is None:
                return {"error": f"Could not compute rating for {target_type} #{target_id}"}
            return {
                "template_id": template_id,
                "user_id": user_id,
                "target_type": target_type,
                "target_id": target_id,
                "overall_score": rating["overall_score"],
                "category": rating["category"],
                "factor_scores": rating.get("factor_scores", [])[:10],
            }

        # Batch
        if target_type == "manager":
            result = compute_manager_dd_batch(conn, template_id, user_id)
        else:
            result = compute_ratings_batch(conn, template_id, user_id)

        return result
    finally:
        conn.close()


def tool_hk_rating_results(template_id: int, user_id: str,
                           target_type: str = "fund",
                           limit: int = 100) -> dict:
    """Get rating results for a template+user combination. Returns rated
    targets with scores, categories, and factor breakdowns."""
    conn = _hk_conn()
    try:
        from hk_funds.storage import get_user_ratings, get_user_rating_summary

        summary = get_user_rating_summary(conn, template_id, user_id, target_type)
        ratings_df = get_user_ratings(
            conn, template_id, user_id, target_type, is_latest=True,
        )

        import json as _json
        results = []
        for _, row in ratings_df.head(limit).iterrows():
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


# ── US Corp Actions tools ─────────────────────────────────────

def tool_us_corp_actions(ticker: str = "", date_from: str = "", date_to: str = "",
                          limit: int = 100, offset: int = 0) -> dict:
    """Query US corporate actions (SEC 8-K filings). Filter by ticker or date range."""
    conn = _us_corp_conn()
    try:
        sql = "SELECT * FROM corp_actions WHERE 1=1"
        params: list = []
        if ticker:
            sql += " AND ticker = UPPER(?)"
            params.append(ticker)
        if date_from:
            sql += " AND filing_date >= ?"
            params.append(date_from)
        if date_to:
            sql += " AND filing_date <= ?"
            params.append(date_to)
        sql += " ORDER BY filing_date DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = conn.execute(sql, params).fetchall()
        cols = [desc[0] for desc in conn.description]
        return {"count": len(rows), "actions": _serialize_rows(rows, cols)}
    finally:
        conn.close()


def tool_us_corp_action_dates() -> dict:
    """Get available SEC filing dates in the database."""
    conn = _us_corp_conn()
    try:
        rows = conn.execute(
            "SELECT filing_date, COUNT(*) as cnt FROM corp_actions "
            "GROUP BY filing_date ORDER BY filing_date DESC LIMIT 60"
        ).fetchall()
        return {"dates": [{"date": str(r[0]), "count": r[1]} for r in rows]}
    finally:
        conn.close()


def tool_us_corp_action_summary(date_from: str = "", date_to: str = "") -> dict:
    """Get US corp action type breakdown for a date range."""
    conn = _us_corp_conn()
    try:
        sql = "SELECT item, COUNT(*) as cnt FROM corp_actions WHERE 1=1"
        params: list = []
        if date_from:
            sql += " AND filing_date >= ?"
            params.append(date_from)
        if date_to:
            sql += " AND filing_date <= ?"
            params.append(date_to)
        sql += " GROUP BY item ORDER BY cnt DESC"
        rows = conn.execute(sql, params).fetchall()
        return {"breakdown": [{"item": r[0], "count": r[1]} for r in rows]}
    finally:
        conn.close()


# ── US Listings tools ─────────────────────────────────────────

def tool_us_listings(listing_type: str = "", date_from: str = "", limit: int = 100) -> dict:
    """Query US IPO and direct listings. Filter by type (IPO, SPAC, Direct Listing) or date."""
    conn = _us_list_conn()
    try:
        sql = "SELECT * FROM listings WHERE 1=1"
        params: list = []
        if listing_type:
            sql += " AND listing_type = ?"
            params.append(listing_type)
        if date_from:
            sql += " AND listing_date >= ?"
            params.append(date_from)
        sql += " ORDER BY listing_date DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        cols = [desc[0] for desc in conn.description]
        return {"count": len(rows), "listings": _serialize_rows(rows, cols)}
    except Exception:
        return {"count": 0, "listings": [], "note": "Table may be empty; data fetched by server"}
    finally:
        conn.close()


def tool_us_crypto_products(asset: str = "", limit: int = 100) -> dict:
    """Query US crypto products (ETPs, trusts). Filter by underlying asset (BTC, ETH, etc.)."""
    conn = _us_list_conn()
    try:
        sql = "SELECT * FROM crypto_products WHERE 1=1"
        params: list = []
        if asset:
            sql += " AND underlying_asset = UPPER(?)"
            params.append(asset)
        sql += " ORDER BY aum DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        cols = [desc[0] for desc in conn.description]
        return {"count": len(rows), "products": _serialize_rows(rows, cols)}
    except Exception:
        return {"count": 0, "products": [], "note": "Table may be empty"}
    finally:
        conn.close()


def tool_us_insider_transactions(ticker: str = "", limit: int = 100) -> dict:
    """Query US insider transactions (Form 4). Filter by ticker."""
    conn = _us_list_conn()
    try:
        sql = "SELECT * FROM insider_transactions WHERE 1=1"
        params: list = []
        if ticker:
            sql += " AND ticker = UPPER(?)"
            params.append(ticker)
        sql += " ORDER BY transaction_date DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        cols = [desc[0] for desc in conn.description]
        return {"count": len(rows), "transactions": _serialize_rows(rows, cols)}
    except Exception:
        return {"count": 0, "transactions": [], "note": "Table may be empty"}
    finally:
        conn.close()


def tool_us_institutional_holdings(ticker: str = "", limit: int = 100) -> dict:
    """Query US institutional holdings (13F filings). Filter by ticker."""
    conn = _us_list_conn()
    try:
        sql = "SELECT * FROM institutional_holdings WHERE 1=1"
        params: list = []
        if ticker:
            sql += " AND ticker = UPPER(?)"
            params.append(ticker)
        sql += " ORDER BY report_date DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        cols = [desc[0] for desc in conn.description]
        return {"count": len(rows), "holdings": _serialize_rows(rows, cols)}
    except Exception:
        return {"count": 0, "holdings": [], "note": "Table may be empty"}
    finally:
        conn.close()


# ── Announcements tools ───────────────────────────────────────

def tool_announcements(market: str = "", search: str = "", date_from: str = "",
                        limit: int = 100) -> dict:
    """Query company announcements. Filter by market (HK, US, CN) or search title."""
    conn = _anno_conn()
    try:
        sql = "SELECT * FROM announcements WHERE 1=1"
        params: list = []
        if market:
            sql += " AND market = UPPER(?)"
            params.append(market)
        if search:
            sql += " AND title ILIKE ?"
            params.append(f"%{search}%")
        if date_from:
            sql += " AND date >= ?"
            params.append(date_from)
        sql += " ORDER BY date DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        cols = [desc[0] for desc in conn.description]
        return {"count": len(rows), "announcements": _serialize_rows(rows, cols)}
    except Exception:
        return {"count": 0, "announcements": [], "note": "Table may be empty"}
    finally:
        conn.close()


def tool_announcement_companies() -> dict:
    """Get tracked companies with announcement counts."""
    conn = _anno_conn()
    try:
        rows = conn.execute(
            "SELECT company_name, market, COUNT(*) as cnt FROM announcements "
            "GROUP BY company_name, market ORDER BY cnt DESC LIMIT 100"
        ).fetchall()
        return {"companies": [{"company": r[0], "market": r[1], "count": r[2]} for r in rows]}
    except Exception:
        return {"companies": [], "note": "Table may be empty"}
    finally:
        conn.close()


# ── KR Stock tools ────────────────────────────────────────────

def tool_kr_stock_stats() -> dict:
    """Get overall Korean stock database statistics: total stocks by market, trading days, filings count."""
    conn = _kr_conn()
    try:
        total = conn.execute("SELECT COUNT(*) FROM kr_listed_stocks WHERE is_active = true").fetchone()[0]
        by_market = conn.execute(
            "SELECT market, COUNT(*) FROM kr_listed_stocks WHERE is_active = true GROUP BY market"
        ).fetchall()
        days = conn.execute("SELECT COUNT(DISTINCT date) FROM kr_daily_prices").fetchone()[0]
        movers = conn.execute("SELECT COUNT(*) FROM kr_significant_movers").fetchone()[0]
        filings = conn.execute("SELECT COUNT(*) FROM kr_dart_filings").fetchone()[0]
        return {
            "total_active_stocks": total,
            "by_market": [{"market": r[0], "count": r[1]} for r in by_market],
            "trading_days": days,
            "significant_movers": movers,
            "dart_filings": filings,
        }
    finally:
        conn.close()


def tool_kr_listed_stocks(market: str = "", sector: str = "", search: str = "", limit: int = 200) -> list:
    """Search Korean listed stocks (KOSPI/KOSDAQ/KONEX). Optional market, sector, and name/code search filters."""
    conn = _kr_conn()
    try:
        where = ["is_active = true"]
        params: list = []
        if market:
            where.append("market = ?")
            params.append(market)
        if sector:
            where.append("sector = ?")
            params.append(sector)
        if search:
            where.append("(name LIKE ? OR code LIKE ?)")
            params.extend([f"%{search}%", f"%{search}%"])
        params.append(limit)
        rows = conn.execute(
            f"SELECT code, name, market, sector, industry, listing_date, market_cap "
            f"FROM kr_listed_stocks WHERE {' AND '.join(where)} "
            f"ORDER BY market_cap DESC NULLS LAST LIMIT ?",
            params,
        ).fetchall()
        cols = ["code", "name", "market", "sector", "industry", "listing_date", "market_cap"]
        return _serialize_rows(rows, cols)
    finally:
        conn.close()


def tool_kr_daily_movers(date: str = "", limit: int = 100) -> list:
    """Get significant price movers (>=10% daily change) for a date. Default: latest trading day."""
    conn = _kr_conn()
    try:
        if not date:
            row = conn.execute("SELECT MAX(date) FROM kr_significant_movers").fetchone()
            date = str(row[0]) if row and row[0] else ""
        if not date:
            return []
        rows = conn.execute("""
            SELECT m.date, m.code, m.name, m.change_pct, m.volume, m.close,
                   m.market, m.sector, m.industry, r.reasons
            FROM kr_significant_movers m
            LEFT JOIN kr_stock_reasons r ON m.date = r.date AND m.code = r.code
            WHERE m.date = ?
            ORDER BY ABS(m.change_pct) DESC
            LIMIT ?
        """, [date, limit]).fetchall()
        cols = ["date", "code", "name", "change_pct", "volume", "close", "market", "sector", "industry", "reasons"]
        return _serialize_rows(rows, cols)
    finally:
        conn.close()


def tool_kr_market_indices(index_code: str = "", limit: int = 200) -> list:
    """Get KOSPI (KS11) or KOSDAQ (KQ11) index OHLCV data."""
    conn = _kr_conn()
    try:
        where = ["1=1"]
        params: list = []
        if index_code:
            where.append("index_code = ?")
            params.append(index_code)
        params.append(limit)
        rows = conn.execute(
            f"SELECT date, index_code, index_name, open, high, low, close, volume, change_pct "
            f"FROM kr_market_indices WHERE {' AND '.join(where)} "
            f"ORDER BY date DESC LIMIT ?",
            params,
        ).fetchall()
        cols = ["date", "index_code", "index_name", "open", "high", "low", "close", "volume", "change_pct"]
        return _serialize_rows(rows, cols)
    finally:
        conn.close()


def tool_kr_foreign_flows(market: str = "", limit: int = 60) -> list:
    """Get foreign/institutional daily net buy data by market (KOSPI/KOSDAQ)."""
    conn = _kr_conn()
    try:
        where = ["1=1"]
        params: list = []
        if market:
            where.append("market = ?")
            params.append(market)
        params.append(limit)
        rows = conn.execute(
            f"SELECT date, market, foreign_net_buy, institution_net_buy, individual_net_buy "
            f"FROM kr_foreign_flows WHERE {' AND '.join(where)} "
            f"ORDER BY date DESC LIMIT ?",
            params,
        ).fetchall()
        cols = ["date", "market", "foreign_net_buy", "institution_net_buy", "individual_net_buy"]
        return _serialize_rows(rows, cols)
    finally:
        conn.close()


def tool_kr_dart_filings(corp_name: str = "", report_type: str = "", limit: int = 100) -> list:
    """Search DART corporate filings by company name or report type (e.g., 사업보고서, 감사보고서, 증권신고서)."""
    conn = _kr_conn()
    try:
        where = ["1=1"]
        params: list = []
        if corp_name:
            where.append("corp_name LIKE ?")
            params.append(f"%{corp_name}%")
        if report_type:
            where.append("report_nm LIKE ?")
            params.append(f"%{report_type}%")
        params.append(limit)
        rows = conn.execute(
            f"SELECT rcept_no, receipt_date, corp_name, report_nm, report_detail, url "
            f"FROM kr_dart_filings WHERE {' AND '.join(where)} "
            f"ORDER BY receipt_date DESC LIMIT ?",
            params,
        ).fetchall()
        cols = ["rcept_no", "receipt_date", "corp_name", "report_nm", "report_detail", "url"]
        return _serialize_rows(rows, cols)
    finally:
        conn.close()


def tool_kr_stock_detail(code: str) -> dict:
    """Get detailed info for a Korean stock: listing info, recent prices, significant mover history."""
    conn = _kr_conn()
    try:
        stock = conn.execute(
            "SELECT code, name, market, sector, industry, listing_date, market_cap, is_active "
            "FROM kr_listed_stocks WHERE code = ?", [code]
        ).fetchone()
        if not stock:
            return {"error": f"Stock {code} not found"}

        prices = conn.execute(
            "SELECT date, open, high, low, close, volume, change_pct "
            "FROM kr_daily_prices WHERE code = ? ORDER BY date DESC LIMIT 30",
            [code],
        ).fetchall()

        movers = conn.execute("""
            SELECT m.date, m.change_pct, r.reasons
            FROM kr_significant_movers m
            LEFT JOIN kr_stock_reasons r ON m.date = r.date AND m.code = r.code
            WHERE m.code = ? ORDER BY m.date DESC LIMIT 20
        """, [code]).fetchall()

        return {
            "code": stock[0], "name": stock[1], "market": stock[2],
            "sector": stock[3], "industry": stock[4],
            "listing_date": str(stock[5]) if stock[5] else None,
            "market_cap": stock[6], "is_active": stock[7],
            "recent_prices": _serialize_rows(
                prices, ["date", "open", "high", "low", "close", "volume", "change_pct"]
            ),
            "recent_movers": _serialize_rows(
                movers, ["date", "change_pct", "reasons"]
            ),
        }
    finally:
        conn.close()


def tool_kr_stock_metrics(code: str) -> dict:
    """Get valuation metrics for a Korean stock: P/E, P/B, dividend yield, beta, growth, margins, ROE, etc. from yfinance."""
    conn = _kr_conn()
    try:
        row = conn.execute("""
            SELECT code, date, market_cap, enterprise_value, pe_trailing, pe_forward,
                   pb_ratio, ps_ratio, dividend_yield, payout_ratio, beta,
                   roa, roe, gross_margin, ebitda_margin, operating_margin,
                   revenue_growth, earnings_growth, free_cashflow, operating_cashflow,
                   inst_holding_pct, insider_holding_pct, shares_outstanding, float_shares,
                   ma_50, ma_200, high_52w, low_52w
            FROM kr_stock_metrics
            WHERE code = ?
            ORDER BY date DESC
            LIMIT 1
        """, [code]).fetchone()
        if not row:
            return {"error": f"No metrics for {code}"}
        return {
            "code": row[0], "date": str(row[1]) if row[1] else None,
            "market_cap": row[2], "enterprise_value": row[3],
            "pe_trailing": row[4], "pe_forward": row[5],
            "pb_ratio": row[6], "ps_ratio": row[7],
            "dividend_yield": row[8], "payout_ratio": row[9],
            "beta": row[10], "roa": row[11], "roe": row[12],
            "gross_margin": row[13], "ebitda_margin": row[14], "operating_margin": row[15],
            "revenue_growth": row[16], "earnings_growth": row[17],
            "free_cashflow": row[18], "operating_cashflow": row[19],
            "inst_holding_pct": row[20], "insider_holding_pct": row[21],
            "shares_outstanding": row[22], "float_shares": row[23],
            "ma_50": row[24], "ma_200": row[25],
            "high_52w": row[26], "low_52w": row[27],
        }
    finally:
        conn.close()


def tool_kr_stock_financials(code: str, statement_type: str = None) -> list:
    """Get quarterly financials for a Korean stock. statement_type: BS (Balance Sheet), IS (Income Statement), CF (Cash Flow)."""
    conn = _kr_conn()
    try:
        if statement_type:
            stmt = statement_type.upper()
            if stmt not in ("BS", "IS", "CF"):
                return [{"error": "statement_type must be BS, IS, or CF"}]
            rows = conn.execute("""
                SELECT date, statement_type, metric_name, value
                FROM kr_stock_financials
                WHERE code = ? AND statement_type = ?
                ORDER BY date DESC, metric_name
            """, [code, stmt]).fetchall()
        else:
            rows = conn.execute("""
                SELECT date, statement_type, metric_name, value
                FROM kr_stock_financials
                WHERE code = ?
                ORDER BY date DESC, statement_type, metric_name
            """, [code]).fetchall()
        return _serialize_rows(rows, ["date", "statement_type", "metric_name", "value"])
    finally:
        conn.close()


def tool_kr_stock_analyst(code: str) -> dict:
    """Get analyst consensus for a Korean stock: price targets, recommendations, earnings estimates from yfinance."""
    conn = _kr_conn()
    try:
        row = conn.execute("""
            SELECT code, date, target_mean, target_high, target_low, target_median,
                   recommendation, num_analysts, earnings_estimate_avg, revenue_estimate_avg,
                   eps_trend_current, eps_trend_7d_ago, eps_trend_30d_ago
            FROM kr_analyst_data
            WHERE code = ?
            ORDER BY date DESC
            LIMIT 1
        """, [code]).fetchone()
        if not row:
            return {"error": f"No analyst data for {code}"}
        return {
            "code": row[0], "date": str(row[1]) if row[1] else None,
            "target_mean": row[2], "target_high": row[3],
            "target_low": row[4], "target_median": row[5],
            "recommendation": row[6], "num_analysts": row[7],
            "earnings_estimate_avg": row[8], "revenue_estimate_avg": row[9],
            "eps_trend_current": row[10], "eps_trend_7d_ago": row[11],
            "eps_trend_30d_ago": row[12],
        }
    finally:
        conn.close()


# ── TW Stock tools ────────────────────────────────────────────

def tool_tw_stock_stats() -> dict:
    """Get Taiwan stock database statistics: total stocks by market, trading days."""
    conn = _tw_conn()
    try:
        total = conn.execute("SELECT COUNT(*) FROM tw_listed_stocks WHERE is_active = true").fetchone()[0]
        by_market = conn.execute(
            "SELECT market, COUNT(*) FROM tw_listed_stocks WHERE is_active = true GROUP BY market"
        ).fetchall()
        days = conn.execute("SELECT COUNT(DISTINCT date) FROM tw_daily_prices").fetchone()[0]
        movers = conn.execute("SELECT COUNT(*) FROM tw_significant_movers").fetchone()[0]
        return {
            "total_active_stocks": total,
            "by_market": [{"market": r[0], "count": r[1]} for r in by_market],
            "trading_days": days,
            "significant_movers": movers,
        }
    finally:
        conn.close()


def tool_tw_listed_stocks(market: str = "", sector: str = "", search: str = "", limit: int = 200) -> list:
    """Search Taiwan listed stocks (TWSE/TPEx). Optional market, sector, and name/code search filters."""
    conn = _tw_conn()
    try:
        where = ["is_active = true"]
        params: list = []
        if market:
            where.append("market = ?")
            params.append(market)
        if sector:
            where.append("sector = ?")
            params.append(sector)
        if search:
            where.append("(name LIKE ? OR code LIKE ?)")
            params.extend([f"%{search}%", f"%{search}%"])
        params.append(limit)
        rows = conn.execute(
            f"SELECT code, name, market, sector, industry, listing_date, market_cap "
            f"FROM tw_listed_stocks WHERE {' AND '.join(where)} "
            f"ORDER BY market_cap DESC NULLS LAST LIMIT ?",
            params,
        ).fetchall()
        cols = ["code", "name", "market", "sector", "industry", "listing_date", "market_cap"]
        return _serialize_rows(rows, cols)
    finally:
        conn.close()


def tool_tw_daily_movers(date: str = "", limit: int = 100) -> list:
    """Get significant price movers (>=5% daily change) for a date. Default: latest trading day."""
    conn = _tw_conn()
    try:
        if not date:
            row = conn.execute("SELECT MAX(date) FROM tw_significant_movers").fetchone()
            date = str(row[0]) if row and row[0] else ""
        if not date:
            return []
        rows = conn.execute("""
            SELECT m.date, m.code, m.name, m.change_pct, m.volume, m.close,
                   m.market, m.sector, m.industry, r.reasons
            FROM tw_significant_movers m
            LEFT JOIN tw_stock_reasons r ON m.date = r.date AND m.code = r.code
            WHERE m.date = ?
            ORDER BY ABS(m.change_pct) DESC
            LIMIT ?
        """, [date, limit]).fetchall()
        cols = ["date", "code", "name", "change_pct", "volume", "close", "market", "sector", "industry", "reasons"]
        return _serialize_rows(rows, cols)
    finally:
        conn.close()


def tool_tw_market_indices(index_code: str = "", limit: int = 200) -> list:
    """Get TAIEX (^TWII) or TPEx (^TWOII) index OHLCV data."""
    conn = _tw_conn()
    try:
        where = ["1=1"]
        params: list = []
        if index_code:
            where.append("index_code = ?")
            params.append(index_code)
        params.append(limit)
        rows = conn.execute(
            f"SELECT date, index_code, index_name, open, high, low, close, volume, change_pct "
            f"FROM tw_market_indices WHERE {' AND '.join(where)} "
            f"ORDER BY date DESC LIMIT ?",
            params,
        ).fetchall()
        cols = ["date", "index_code", "index_name", "open", "high", "low", "close", "volume", "change_pct"]
        return _serialize_rows(rows, cols)
    finally:
        conn.close()


def tool_tw_stock_detail(code: str) -> dict:
    """Get detailed info for a Taiwan stock: listing info, recent prices, significant mover history."""
    conn = _tw_conn()
    try:
        stock = conn.execute(
            "SELECT code, name, market, sector, industry, listing_date, market_cap, is_active "
            "FROM tw_listed_stocks WHERE code = ?", [code]
        ).fetchone()
        if not stock:
            return {"error": f"Stock {code} not found"}

        prices = conn.execute(
            "SELECT date, open, high, low, close, volume, change_pct "
            "FROM tw_daily_prices WHERE code = ? ORDER BY date DESC LIMIT 30",
            [code],
        ).fetchall()

        movers = conn.execute("""
            SELECT m.date, m.change_pct, r.reasons
            FROM tw_significant_movers m
            LEFT JOIN tw_stock_reasons r ON m.date = r.date AND m.code = r.code
            WHERE m.code = ? ORDER BY m.date DESC LIMIT 20
        """, [code]).fetchall()

        return {
            "code": stock[0], "name": stock[1], "market": stock[2],
            "sector": stock[3], "industry": stock[4],
            "listing_date": str(stock[5]) if stock[5] else None,
            "market_cap": stock[6], "is_active": stock[7],
            "recent_prices": _serialize_rows(
                prices, ["date", "open", "high", "low", "close", "volume", "change_pct"]
            ),
            "recent_movers": _serialize_rows(
                movers, ["date", "change_pct", "reasons"]
            ),
        }
    finally:
        conn.close()


# ── SK Hynix Cross-Market ─────────────────────────────────────


def tool_hynix_arbitrage(date: str = "") -> dict:
    """Get SK Hynix cross-market arbitrage comparison: premium/discount across KR stock, US ADR, HK ETP, and KR ETFs."""
    conn = _hynix_conn()
    try:
        if not date:
            row = conn.execute("SELECT MAX(date) FROM hynix_arbitrage").fetchone()
            if not row or not row[0]:
                return {"error": "No data"}
            date = str(row[0])

        rows = conn.execute("""
            SELECT a.date, a.ticker, i.name, i.market, i.currency, i.instrument_type,
                   i.leverage, a.price_local, a.price_krw, a.base_price_krw,
                   a.nav_local, a.nav_krw, a.tracking_ratio_used,
                   a.equivalent_krw_per_share, a.premium_pct, a.nav_premium_pct
            FROM hynix_arbitrage a
            JOIN hynix_instruments i ON a.ticker = i.ticker
            WHERE a.date = ?
            ORDER BY a.premium_pct DESC NULLS LAST
        """, [date]).fetchall()

        if not rows:
            return {"error": f"No arbitrage data for {date}"}

        fx_rows = conn.execute(
            "SELECT from_ccy, to_ccy, rate FROM hynix_fx_rates WHERE date = ?", [date]
        ).fetchall()
        fx = {f"{r[0]}{r[1]}": r[2] for r in fx_rows}

        instruments = []
        for r in rows:
            instruments.append({
                "ticker": r[1], "name": r[2], "market": r[3], "currency": r[4],
                "instrument_type": r[5], "leverage": r[6],
                "price_local": r[7], "price_krw": r[8],
                "nav_local": r[10], "nav_krw": r[11],
                "tracking_ratio": r[12],
                "equivalent_krw_per_share": r[13],
                "premium_pct_vs_base": round(r[14], 2) if r[14] is not None else None,
                "nav_premium_pct": round(r[15], 2) if r[15] is not None else None,
            })

        return {
            "date": date,
            "base_ticker": "000660.KS",
            "base_price_krw": rows[0][9],
            "fx_rates": fx,
            "count": len(instruments),
            "instruments": instruments,
        }
    finally:
        conn.close()


def tool_hynix_instruments(market: str = "") -> dict:
    """List tracked SK Hynix instruments across markets (KR, US, HK)."""
    conn = _hynix_conn()
    try:
        if market:
            rows = conn.execute(
                "SELECT ticker, name, market, currency, instrument_type, leverage, tracking_ratio, note "
                "FROM hynix_instruments WHERE is_active = true AND market = ? ORDER BY market, ticker",
                [market],
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT ticker, name, market, currency, instrument_type, leverage, tracking_ratio, note "
                "FROM hynix_instruments WHERE is_active = true ORDER BY market, ticker",
            ).fetchall()
        return {
            "count": len(rows),
            "instruments": [
                {"ticker": r[0], "name": r[1], "market": r[2], "currency": r[3],
                 "instrument_type": r[4], "leverage": r[5],
                 "tracking_ratio": r[6], "note": r[7]}
                for r in rows
            ],
        }
    finally:
        conn.close()


def tool_hynix_prices(ticker: str = "", date: str = "", limit: int = 30) -> dict:
    """Get SK Hynix instrument price history. Leave ticker empty to get all instruments for a date."""
    conn = _hynix_conn()
    try:
        if ticker:
            rows = conn.execute("""
                SELECT date, open, high, low, close, volume, nav, change_pct
                FROM hynix_daily_prices WHERE ticker = ? ORDER BY date DESC LIMIT ?
            """, [ticker, limit]).fetchall()
            return {
                "ticker": ticker,
                "count": len(rows),
                "prices": _serialize_rows(rows, ["date", "open", "high", "low", "close", "volume", "nav", "change_pct"]),
            }
        elif date:
            rows = conn.execute("""
                SELECT p.date, p.ticker, i.name, i.market, p.close, p.nav, p.change_pct
                FROM hynix_daily_prices p
                JOIN hynix_instruments i ON p.ticker = i.ticker
                WHERE p.date = ?
                ORDER BY i.market, p.ticker
            """, [date]).fetchall()
            return {
                "date": date,
                "count": len(rows),
                "prices": [{"ticker": r[1], "name": r[2], "market": r[3],
                            "close": r[4], "nav": r[5], "change_pct": r[6]}
                           for r in rows],
            }
        else:
            return {"error": "Provide ticker or date"}
    finally:
        conn.close()


# ── Korean Retail Leverage (kimpremium.com) ─────────────────────

def tool_kr_leverage_summary() -> dict:
    """Get latest Korean retail leverage KPI snapshot: R2, forced liquidation, deposits, valuation, ETF flows, credit utilization."""
    conn = _hynix_conn()
    try:
        return _leverage_latest(conn)
    finally:
        conn.close()


def _leverage_latest(conn) -> dict:
    meta_row = conn.execute(
        "SELECT generated, asof_date, range_start, range_end, range_rows, kpi_json, etf_kpi_json FROM kr_leverage_meta WHERE id = 1"
    ).fetchone()
    if not meta_row:
        return {"error": "No KR leverage data. Run hynix pipeline with --init first."}

    latest_date = conn.execute("SELECT MAX(date) FROM kr_leverage_daily").fetchone()
    latest_etf = conn.execute("SELECT MAX(date) FROM kr_leverage_etf_daily").fetchone()

    kpi = json.loads(meta_row[5]) if meta_row[5] else {}
    etf_kpi = json.loads(meta_row[6]) if meta_row[6] else {}

    # Latest daily row for alerts
    daily = None
    if latest_date and latest_date[0]:
        row = conn.execute(
            """SELECT r2, p10, kospi, spx, fin, dep, liq, liqR, mg, util, misu
               FROM kr_leverage_daily WHERE date = ?""",
            [str(latest_date[0])],
        ).fetchone()
        if row:
            daily = {
                "date": str(latest_date[0]),
                "r2": row[0], "r2_10y_pct": row[1],
                "kospi": row[2], "spx": row[3],
                "fin_trillion": row[4], "dep_trillion": row[5],
                "liq_100m": row[6], "liq_ratio": row[7],
                "mcap_gdp_pct": row[8], "credit_util_pct": row[9],
                "misu_trillion": row[10],
            }

    return {
        "generated": meta_row[0],
        "asof": meta_row[1],
        "range": {"start": meta_row[2], "end": meta_row[3], "rows": meta_row[4]},
        "latest_daily_date": str(latest_date[0]) if latest_date and latest_date[0] else None,
        "latest_etf_date": str(latest_etf[0]) if latest_etf and latest_etf[0] else None,
        "kpi": kpi,
        "etf_kpi": etf_kpi,
        "latest_daily": daily,
    }


def tool_kr_leverage_series(indicator: str = "r2", limit: int = 200) -> dict:
    """Get a time series from Korean retail leverage data. indicator: r2, p10, kospi, kosdaq, spx, fin, finKospi, finKosdaq, dep, derivDep, rp, col, misu, liq, liqR, r1, r1p, r1q, mcap, loan, mg, util."""
    conn = _hynix_conn()
    try:
        valid = ["r2", "p10", "kospi", "kosdaq", "spx", "fin", "finKospi", "finKosdaq",
                 "dep", "derivDep", "rp", "col", "misu", "liq", "liqR", "r1", "r1p",
                 "r1q", "mcap", "loan", "mg", "util"]
        if indicator not in valid:
            return {"error": f"Unknown indicator '{indicator}'. Valid: {valid}"}

        rows = conn.execute(f"""
            SELECT date, {indicator} AS value FROM kr_leverage_daily
            WHERE {indicator} IS NOT NULL ORDER BY date DESC LIMIT ?
        """, [limit]).fetchall()
        return {
            "indicator": indicator,
            "count": len(rows),
            "data": [{"date": str(r[0]), "value": r[1]} for r in rows],
        }
    finally:
        conn.close()


def tool_kr_leverage_etf(indicator: str = "thermo", limit: int = 200) -> dict:
    """Get Korean leveraged ETF flow time series. indicator: thermo, thermoW, flow, flowW, cumFlow, cumFlowW."""
    conn = _hynix_conn()
    try:
        valid = ["r2", "thermo", "thermoW", "flow", "flowW", "cumFlow", "cumFlowW"]
        if indicator not in valid:
            return {"error": f"Unknown ETF indicator '{indicator}'. Valid: {valid}"}

        rows = conn.execute(f"""
            SELECT date, {indicator} AS value FROM kr_leverage_etf_daily
            WHERE {indicator} IS NOT NULL ORDER BY date DESC LIMIT ?
        """, [limit]).fetchall()
        return {
            "indicator": indicator,
            "count": len(rows),
            "data": [{"date": str(r[0]), "value": r[1]} for r in rows],
        }
    finally:
        conn.close()


# ── Source metadata ──────────────────────────────────────────────

SOURCE_META = {
    "us":   {"label": "US / FRED",              "provider": "Federal Reserve Economic Data", "key_required": True,  "description": "US GDP, CPI, unemployment, Fed funds, Treasury yields, credit spreads, housing, labor, PCE inflation, financial conditions, sovereign yields (8 countries), exchange rates (9 pairs)", "category": "macro"},
    "cn":   {"label": "China / AKShare",        "provider": "AKShare (东方财富/新浪)",        "key_required": False, "description": "中国 GDP, CPI, PPI, PMI, M2, LPR, 社融, 外汇储备, 房地产, 消费, 贸易, 北向资金, 融资融券, 国债收益率, 汇率", "category": "macro"},
    "global_": {"label": "Global / World Bank",  "provider": "World Bank WDI API",            "key_required": False, "description": "GDP, CPI, GDP growth, population for 8+ countries (1960-full)", "category": "macro"},
    "hk":   {"label": "Hong Kong / AKShare",    "provider": "AKShare",                       "key_required": False, "description": "香港 CPI, PPI, GDP, 失业率, 贸易, 建造, HIBOR", "category": "macro"},
    "jp":   {"label": "Japan / BoJ+AKShare",    "provider": "Bank of Japan + AKShare",       "key_required": False, "description": "日本 CPI, 失业率, 政策利率, 领先指标, Tankan调查", "category": "macro"},
    "euro": {"label": "Eurozone / AKShare",     "provider": "AKShare (Jin10财经日历)",       "key_required": False, "description": "欧元区 GDP, CPI, PPI, PMI, 失业率, 工业产出, 零售, 贸易, ZEW/Sentix情绪", "category": "macro"},
    "uk":   {"label": "UK / AKShare",           "provider": "AKShare (Jin10财经日历)",       "key_required": False, "description": "英国 GDP, CPI, 失业率, 零售, 贸易, Halifax/Rightmove房价, 央行利率", "category": "macro"},
    "de":   {"label": "Germany / AKShare",      "provider": "AKShare (Jin10财经日历)",       "key_required": False, "description": "德国 CPI, GDP, Ifo商业景气, ZEW情绪, 贸易", "category": "macro"},
    "au":   {"label": "Australia / AKShare",    "provider": "AKShare (Jin10财经日历)",       "key_required": False, "description": "澳大利亚 CPI, 失业率, 零售, 贸易, RBA利率", "category": "macro"},
    "ca":   {"label": "Canada / AKShare",       "provider": "AKShare (Jin10财经日历)",       "key_required": False, "description": "加拿大 CPI, GDP, 失业率, 贸易, BoC利率", "category": "macro"},
    "ch":   {"label": "Switzerland / AKShare",  "provider": "AKShare (Jin10财经日历)",       "key_required": False, "description": "瑞士 CPI, GDP, 贸易, SVME PMI, SNB利率", "category": "macro"},
    "bond":  {"label": "Bond Market / AKShare",  "provider": "AKShare",                       "key_required": False, "description": "中美各期限国债收益率 (2Y/5Y/10Y/30Y), 利差, 可转债指数", "category": "macro"},
    "futures": {"label": "Futures / AKShare",    "provider": "AKShare (新浪财经)",            "key_required": False, "description": "沪金/沪银/沪铜/螺纹钢/铁矿石/原油主力合约", "category": "macro"},
    "shipping": {"label": "Shipping / AKShare",  "provider": "AKShare (新浪财经)",            "key_required": False, "description": "波罗的海干散货/油轮指数 BDI/BCI/BPI/BCTI", "category": "macro"},
    "banks": {"label": "Central Bank Rates",     "provider": "AKShare (Jin10财经日历)",       "key_required": False, "description": "全球央行政策利率: ECB, BOE, BOJ, RBA, SNB, Fed, RBI, BCB, RBNZ", "category": "macro"},
    "alt":  {"label": "Alternative / Leading",   "provider": "AKShare",                       "key_required": False, "description": "SOX半导体, 原油油轮, 大宗商品/能源/农业/建材指数, 金银ETF持仓, 消费者信心, OPEC产量", "category": "macro"},
    "llm":  {"label": "LLM Ecosystem",           "provider": "GitHub + HuggingFace + PyPI",   "key_required": False, "description": "LLM生态代理指标: GitHub Stars (9 repos), HuggingFace下载量 (5 models), PyPI月下载量 (5 SDKs)", "category": "macro"},
    "defi": {"label": "DeFi & Prediction Markets","provider": "Polymarket + DeFi Llama + CoinGecko", "key_required": False, "description": "链上金融: Polymarket预测市场交易量, DeFi DEX/衍生品TVL, RWA代币化规模, CEX交易量", "category": "macro"},
    "ai":   {"label": "AI Infrastructure",           "provider": "FRED (Federal Reserve Economic Data)", "key_required": True,  "description": "AI全供应链: SOX半导体指数, Kelly数据中心指数, 云计算指数, 半导体/PCB/存储/网络设备/变压器PPI, 制造业建设(芯片fab), 铀/铜/锂价格, 核电发电, 电价, AI机器人指数", "category": "macro"},
    "ai_co": {"label": "AI Company Financials",       "provider": "Yahoo Finance (yfinance)",         "key_required": False, "description": "AI供应链企业财报: NVIDIA/TSMC/ASML/Broadcom营收利润, 微软/亚马逊/谷歌/Meta营收及CapEx, 四大云厂商合计AI基础设施投资", "category": "macro"},
    "cb":  {"label": "A-Share Concept Boards",        "provider": "AKShare (东方财富概念板块)",       "key_required": False, "description": "A股概念板块指数: 光通信/CPO/算力/数据中心/液冷/AI芯片/存储芯片/光纤/玻璃基板/Chiplet/铜缆高速连接/F5G/MicroLED/光刻机/MLCC/东数西算等 (22个板块)", "category": "macro"},
    "optical": {"label": "Global Optical Companies",   "provider": "Yahoo Finance (yfinance)",         "key_required": False, "description": "全球光通信个股财报: 美股(COHR/LITE/FN/ANET/GLW/CIEN/AAOI/CLS/CRDO), 台股(台积电/联亚/光环/稳懋/联钧/上诠/众达/华星光/光圣), 日股(古河/住友/藤仓), 韩股(三星/SK海力士) — 季度营收与净利润", "category": "macro"},
    "aml":  {"label": "AML/CFT Country Risk Ratings", "provider": "FATF + US State Dept + Basel Institute", "key_required": False, "description": "反洗钱国家风险评级: FATF黑/灰名单(26国), 美国INCSR洗钱关注国(81国), Basel AML指数综合评分(65国)", "category": "country_risk"},
    "sanctions": {"label": "Sanctions & Corruption", "provider": "OFAC + Transparency International", "key_required": False, "description": "制裁与腐败: OFAC SDN制裁名单(19,065实体/个人/船舶/飞行器), 按国家聚合制裁数量, TI腐败感知指数CPI(180国评分排名)", "category": "country_risk"},
    "name_screening": {"label": "Name Screening (中英文)", "provider": "OpenSanctions + GDELT + 阿里云法院", "key_required": False, "description": "名称筛查: OpenSanctions制裁+PEP数据库(440K+实体,含中文名), GDELT全球负面新闻, 阿里云信数科技中国法院涉诉(失信/被执行/裁判文书), 中英文模糊匹配+拼音跨文字搜索", "category": "name_screening"},
    "energy": {"label": "Energy / EIA",          "provider": "U.S. Energy Information Admin", "key_required": True,  "description": "WTI原油价格, Henry Hub天然气价格", "category": "macro"},
}


# ── Name Data & I Ching tool implementations ────────────────────

_NAME_DB = os.path.join(os.path.dirname(os.path.dirname(__file__)), "name_data", "name_data.duckdb")


def tool_name_score(surname: str, given_name: str, birth_year: int = 0,
                    birth_month: int = 0, birth_day: int = 0, birth_hour: int = 12,
                    gender: str = "男") -> dict:
    """Score a Chinese name with full BaZi/WuGe/Sancai/Zodiac/Phonetic/Meaning analysis."""
    from name_data.pipeline import score_name, calculate_wuge

    kwargs = {"surname": surname, "given_name": given_name, "gender": gender}
    if birth_year and birth_month and birth_day:
        kwargs.update(birth_year=birth_year, birth_month=birth_month,
                      birth_day=birth_day, birth_hour=birth_hour)

    result = score_name(**kwargs)
    return {
        "total_score": result["scores"]["total"],
        "verdict": result["verdict"],
        "scores": result["scores"],
        "wuge_grids": {k: {"number": v["number"], "element": v["element"],
                           "ji_xiong": v["ji_xiong"], "summary": v["summary"]}
                       for k, v in result["wuge"]["grids"].items()},
        "sancai": result.get("sancai", {}),
        "bazi": result.get("bazi", {}).get("pillars", {}),
        "zodiac": result.get("zodiac", {}).get("details", []),
        "phonetic": result.get("phonetic", {}).get("notes", []),
    }


def tool_name_bazi(year: int, month: int, day: int, hour: int = 12) -> dict:
    """Calculate BaZi (八字) four pillars: year, month, day, hour."""
    from name_data.pipeline import calculate_bazi
    result = calculate_bazi(year, month, day, hour)
    return {
        "pillars": result["pillars"],
        "day_master": result["day_master"],
        "wuxing_count": result["wuxing_count"],
        "favorable_element": result["favorable_element"],
        "zodiac": result["zodiac"],
    }


def tool_name_wuge(surname: str, given_name: str) -> dict:
    """Calculate Wu Ge (五格) stroke grids from Kangxi dictionary strokes."""
    from name_data.pipeline import calculate_wuge
    result = calculate_wuge(surname, given_name)
    grids = {}
    for k, v in result["grids"].items():
        grids[k] = {"number": v["number"], "element": v["element"],
                    "ji_xiong": v["ji_xiong"], "summary": v["summary"]}
    return {
        "grids": grids,
        "surname_strokes": result["surname_strokes"],
        "given_strokes": result["given_strokes"],
    }


def tool_iching_divine(method: str = "coins", a: int = 0, b: int = 0, c: int = 0) -> dict:
    """I Ching hexagram divination. method='coins' for coin toss, 'numbers' for 3-number method.

    For numbers: a=upper trigram(1-8), b=lower trigram(1-8), c=changing line(1-6).
    """
    from name_data.pipeline import divine_by_coins, divine_by_numbers

    if method == "numbers" and a and b and c:
        result = divine_by_numbers(a, b, c)
    else:
        result = divine_by_coins()

    primary = result["primary"]
    mutual = result.get("mutual", {})
    changed = result.get("changed", {})

    return {
        "method": result["method"],
        "primary_hexagram": {"id": primary["id"], "name": primary["name"],
                              "judgment": primary["judgment"], "ji_xiong": primary["ji_xiong"],
                              "description": primary["description"]} if primary else None,
        "changing_lines": result.get("changing_lines", []),
        "mutual_hexagram": {"id": mutual["id"], "name": mutual["name"],
                             "ji_xiong": mutual["ji_xiong"]} if mutual else None,
        "changed_hexagram": {"id": changed["id"], "name": changed["name"],
                              "ji_xiong": changed["ji_xiong"]} if changed else None,
    }


def tool_calendar_ganzhi(date_str: str = "") -> dict:
    """Get Chinese calendar (农历) info: year/month/day stem-branch (干支),
    solar term (节气), zodiac (生肖), and day cycle index.

    If date_str is empty, returns info for today.
    Format: YYYY-MM-DD (e.g., 2025-06-15).
    """
    from datetime import date as dt_date
    from name_data.calendar import gregorian_to_ganzhi
    if date_str:
        try:
            d = dt_date.fromisoformat(date_str)
        except ValueError:
            return {"error": f"Invalid date: {date_str}. Use YYYY-MM-DD."}
    else:
        d = dt_date.today()
    return gregorian_to_ganzhi(d)


def tool_tuibei_consult(method: str = "random", hexagram_id: int = 0) -> dict:
    """Consult Tui Bei Tu (推背图) — the famous Tang Dynasty prophetic text.

    Methods:
      - 'random': randomly draws one of 60 prophecies
      - 'hexagram': looks up prophecy by I Ching hexagram ID (1-64), with fallback
        to wrong/reverse hexagrams if no direct match
      - 'index': get a specific prophecy by number (1-60)

    Returns prophecy with image description, poems (谶/颂), linked hexagram,
    and historical era.
    """
    from name_data.pipeline import consult_tuibei, list_tuibei_eras
    if method == "hexagram" and hexagram_id and hexagram_id > 0:
        result = consult_tuibei(method="hexagram", hexagram_id=hexagram_id)
        tb = result.get("tuibei", {})
        return {
            "consult_method": result["consult_method"],
            "query_hexagram_id": result.get("query_hexagram_id"),
            "match_type": result.get("match_type", "none"),
            "via_hexagram": result.get("via_hexagram_name"),
            "prophecy_id": tb.get("id"),
            "image_name": tb.get("image_name"),
            "image_desc": tb.get("image_desc"),
            "poem_chen": tb.get("poem_chen"),
            "poem_song": tb.get("poem_song"),
            "commentary": tb.get("commentary"),
            "historical_era": tb.get("historical_era"),
            "hexagram": tb.get("hexagram"),
        }
    elif method == "index" and hexagram_id and hexagram_id > 0:
        result = consult_tuibei(method="index", hexagram_id=hexagram_id)
        tb = result.get("tuibei", {})
        return {
            "consult_method": "index",
            "prophecy_id": tb.get("id"),
            "image_name": tb.get("image_name"),
            "image_desc": tb.get("image_desc"),
            "poem_chen": tb.get("poem_chen"),
            "poem_song": tb.get("poem_song"),
            "commentary": tb.get("commentary"),
            "historical_era": tb.get("historical_era"),
            "hexagram": tb.get("hexagram"),
        }
    else:
        result = consult_tuibei(method="random")
        tb = result.get("tuibei", {})
        return {
            "consult_method": "random",
            "prophecy_id": tb.get("id"),
            "image_name": tb.get("image_name"),
            "image_desc": tb.get("image_desc"),
            "poem_chen": tb.get("poem_chen"),
            "poem_song": tb.get("poem_song"),
            "commentary": tb.get("commentary"),
            "historical_era": tb.get("historical_era"),
            "hexagram": tb.get("hexagram"),
        }


def tool_name_generate(surname: str, birth_year: int, birth_month: int,
                       birth_day: int, birth_hour: int = 12,
                       gender: str = "男", num_names: int = 30) -> dict:
    """Generate auspicious Chinese name candidates based on BaZi and WuGe.

    Calculates favorable five element from birth date, finds compatible stroke
    combinations, queries matching characters, and scores all candidates.
    Returns 30+ ranked name suggestions.
    """
    from name_data.pipeline import generate_names
    result = generate_names(
        surname=surname, birth_year=birth_year, birth_month=birth_month,
        birth_day=birth_day, birth_hour=birth_hour, gender=gender,
        num_names=num_names,
    )
    return {
        "bazi_summary": result["bazi_summary"],
        "stroke_analysis": result["stroke_analysis"],
        "total_candidates": result["total_candidates"],
        "top_names": [
            {
                "full_name": c["full_name"],
                "name_type": c["name_type"],
                "total_score": c["total_score"],
                "verdict": c["verdict"],
                "wuge_grids": c["wuge_grids"],
                "sancai": c.get("sancai", {}),
            }
            for c in result["candidates"]
        ],
    }


def tool_daily_fortune(date_str: str = "") -> dict:
    """Get pre-computed daily fortune (每日运势): Chinese calendar info, daily I Ching hexagram,
    and overall fortune level (大吉/吉/平/凶).

    Each day has a deterministically pre-computed hexagram via coin divination
    seeded by the date, plus stem-branch fortune assessment.

    Leave date_str empty for today. Format: YYYY-MM-DD (e.g., 2026-07-03).
    """
    from datetime import date as dt_date
    from name_data.pipeline import get_daily_fortune
    if date_str:
        try:
            d = dt_date.fromisoformat(date_str)
        except ValueError:
            return {"error": f"Invalid date: {date_str}. Use YYYY-MM-DD."}
    else:
        d = dt_date.today()
    return get_daily_fortune(d)


def tool_huangli(date_str: str = "") -> dict:
    """Get Chinese Almanac (黄历) for any date. Returns:
    - 建除十二神 (Jianchu Twelve Gods): day designation and suitable/avoid activities
    - 黄道黑道 (Yellow/Black Path): auspicious or inauspicious day officer
    - 二十八宿 (28 Lunar Mansions): daily mansion with luminary and animal
    - 彭祖百忌 (Peng Zu's Taboos): stem and branch taboos
    - 宜忌 (Yi/Ji): combined suitable and avoid activities
    - almanac_score: -100 (worst) to 100 (best)

    Based on 《协纪辨方书》(Qing Dynasty official almanac). Leave date_str empty for today.
    Format: YYYY-MM-DD (e.g., 2025-06-15).
    """
    from datetime import date as dt_date
    from name_data.huangli import get_daily_almanac
    if date_str:
        try:
            d = dt_date.fromisoformat(date_str)
        except ValueError:
            return {"error": f"Invalid date: {date_str}. Use YYYY-MM-DD."}
    else:
        d = dt_date.today()
    return get_daily_almanac(d)


def tool_data_sources() -> list[dict]:
    """Return metadata for all data sources with categories."""
    return [
        {
            "id": key,
            "label": meta["label"],
            "provider": meta["provider"],
            "key_required": meta["key_required"],
            "category": meta.get("category", "macro"),
            "description": meta["description"],
        }
        for key, meta in SOURCE_META.items()
    ]

# ── A-Share ETF Tools ────────────────────────────────────────

def _a_share_etf_conn():
    """Get a read-only connection to the a_share_etf DuckDB."""
    db_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "a_share_etf.duckdb")
    if not os.path.exists(db_path):
        return None, "a_share_etf database not found"
    return duckdb.connect(db_path, read_only=True), None


def tool_a_share_etf_status() -> dict:
    """Get A-share ETF flow database status."""
    conn, err = _a_share_etf_conn()
    if err:
        return {"error": err}
    try:
        tables = {}
        for tname in ["etf_daily", "sector_flow_daily", "margin_daily", "market_overview_daily"]:
            cnt = conn.execute(f'SELECT COUNT(*) FROM "{tname}"').fetchone()[0]
            tables[tname] = cnt
        days = conn.execute("SELECT COUNT(DISTINCT date) FROM etf_daily").fetchone()[0]
        return {"status": "ok", "trading_days": days, "tables": tables}
    finally:
        conn.close()


def tool_a_share_etf_flows(date: str = "", sector: str = "", limit: int = 50) -> dict:
    """Get A-share ETF sector flow data. Leave date empty for latest available. Leave sector empty for all sectors."""
    conn, err = _a_share_etf_conn()
    if err:
        return {"error": err}
    try:
        if date and not sector:
            df = conn.execute("""
                SELECT date, sector, etf_count, total_inflow, total_amount, avg_inflow
                FROM sector_flow_daily WHERE date = ?
                ORDER BY total_inflow DESC
            """, [date]).df()
            return {"date": date, "count": len(df), "sectors": df.to_dict(orient="records")}
        elif date and sector:
            df = conn.execute("""
                SELECT * FROM sector_flow_daily WHERE date = ? AND sector = ?
            """, [date, sector]).df()
            return {"date": date, "sector": sector, "data": df.to_dict(orient="records")}
        elif sector and not date:
            df = conn.execute("""
                SELECT * FROM sector_flow_daily WHERE sector = ?
                ORDER BY date DESC LIMIT ?
            """, [sector, limit]).df()
            return {"sector": sector, "count": len(df), "history": df.to_dict(orient="records")}
        else:
            # Latest date, all sectors
            latest = conn.execute(
                "SELECT date FROM sector_flow_daily ORDER BY date DESC LIMIT 1"
            ).fetchone()
            if not latest:
                return {"error": "No data"}
            date_str = str(latest[0])
            df = conn.execute("""
                SELECT date, sector, etf_count, total_inflow, total_amount, avg_inflow
                FROM sector_flow_daily WHERE date = ?
                ORDER BY total_inflow DESC
            """, [date_str]).df()
            return {"date": date_str, "count": len(df), "sectors": df.to_dict(orient="records")}
    finally:
        conn.close()


def tool_a_share_etf_detail(code: str = "", date: str = "", limit: int = 30) -> dict:
    """Get per-ETF detail: history for a specific ETF code, or all ETFs for a specific date."""
    conn, err = _a_share_etf_conn()
    if err:
        return {"error": err}
    try:
        if code:
            df = conn.execute("""
                SELECT date, code, name, price, change_pct, volume, amount,
                       main_inflow, main_inflow_pct, super_large_inflow,
                       large_inflow, medium_inflow, small_inflow, sector
                FROM etf_daily WHERE code = ?
                ORDER BY date DESC LIMIT ?
            """, [code, limit]).df()
            return {"code": code, "count": len(df), "history": df.to_dict(orient="records")}
        elif date:
            df = conn.execute("""
                SELECT date, code, name, price, change_pct, main_inflow,
                       main_inflow_pct, super_large_inflow, sector
                FROM etf_daily WHERE date = ?
                ORDER BY main_inflow DESC
            """, [date]).df()
            return {"date": date, "count": len(df), "etfs": df.to_dict(orient="records")}
        else:
            latest = conn.execute(
                "SELECT date FROM etf_daily ORDER BY date DESC LIMIT 1"
            ).fetchone()
            if not latest:
                return {"error": "No data"}
            date_str = str(latest[0])
            df = conn.execute("""
                SELECT date, code, name, price, change_pct, main_inflow,
                       main_inflow_pct, super_large_inflow, sector
                FROM etf_daily WHERE date = ?
                ORDER BY main_inflow DESC
                LIMIT 50
            """, [date_str]).df()
            return {"date": date_str, "count": len(df), "etfs": df.to_dict(orient="records")}
    finally:
        conn.close()


def tool_a_share_margin(limit: int = 30) -> dict:
    """Get latest A-share margin balance data (融资融券余额) with daily changes."""
    conn, err = _a_share_etf_conn()
    if err:
        return {"error": err}
    try:
        df = conn.execute("""
            SELECT date, sh_margin, sz_margin, total_margin, daily_change
            FROM margin_daily ORDER BY date DESC LIMIT ?
        """, [limit]).df()
        if df.empty:
            return {"error": "No margin data"}
        latest = df.iloc[0].to_dict()
        return {
            "latest": latest,
            "count": len(df),
            "history": df.to_dict(orient="records"),
        }
    finally:
        conn.close()


def tool_a_share_etf_overview(date: str = "", limit: int = 30) -> dict:
    """Get A-share ETF daily market overview with merged proxy (合并代理).
    Merged proxy = ETF net inflow - margin_change (reflects ETF flows covering margin outflows).
    Leave date empty for latest."""
    conn, err = _a_share_etf_conn()
    if err:
        return {"error": err}
    try:
        if date:
            row = conn.execute("""
                SELECT * FROM market_overview_daily WHERE date = ?
            """, [date]).fetchone()
            if not row:
                return {"error": f"No overview data for {date}"}
            return {
                "date": str(row[0]), "total_etf_inflow": row[1],
                "total_etf_count": row[2], "margin_balance": row[3],
                "margin_change": row[4], "merged_proxy": row[5],
                "market_main_inflow": row[6],
            }
        else:
            df = conn.execute("""
                SELECT * FROM market_overview_daily ORDER BY date DESC LIMIT ?
            """, [limit]).df()
            if df.empty:
                return {"error": "No overview data"}
            latest = df.iloc[0].to_dict()
            return {
                "latest": latest,
                "count": len(df),
                "history": df.to_dict(orient="records"),
            }
    finally:
        conn.close()


TOOLS = [
    {
        "name": "list_indicators",
        "description": "List all available economic indicators with metadata. "
                       "Optional 'source' param filters by source (us, cn, global_, hk, jp, euro, uk, de, au, ca, ch, bond, futures, shipping, banks, alt, llm, defi, energy, ai, ai_co, cb, aml, sanctions, name_screening). "
                       "Returns id, name, description, frequency, and last_updated for each indicator.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "Filter by data source"}
            },
        },
    },
    {
        "name": "query_data",
        "description": "Query time-series observations for an economic indicator. "
                       "Requires indicator_id. Supports date range filtering and limit. "
                       "Returns the indicator metadata and a list of date/value observations.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "indicator_id": {"type": "integer", "description": "Indicator ID from list_indicators"},
                "start": {"type": "string", "description": "Start date (YYYY-MM-DD), optional"},
                "end": {"type": "string", "description": "End date (YYYY-MM-DD), optional"},
                "limit": {"type": "integer", "description": "Max observations to return, default 100"},
            },
            "required": ["indicator_id"],
        },
    },
    {
        "name": "get_latest",
        "description": "Get the most recent observation for an indicator. Requires indicator_id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "indicator_id": {"type": "integer", "description": "Indicator ID from list_indicators"},
            },
            "required": ["indicator_id"],
        },
    },
    {
        "name": "search_indicators",
        "description": "Search indicators by keyword in name or description. "
                       "Useful for finding indicators about specific topics like 'PMI', 'CPI', 'GDP', 'bond', etc.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keyword (e.g. 'PMI', 'CPI', 'bond')"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "data_summary",
        "description": "Get a high-level summary of the entire eco data pipeline: "
                       "total indicators, total observations, breakdown by source and frequency.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "data_sources",
        "description": "List all 25 data sources with metadata: provider, whether an API key is required, and description of what data each source provides. Use this to understand the full scope of available data before drilling into specific indicators.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "cn_stock_status",
        "description": "Get the status of the China stock limit-up database: available tables and row counts.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "search_name",
        "description": "Comprehensive name screening against sanctions lists, PEP database, negative news, and Chinese court records. Supports both Chinese (中文) and English names with fuzzy matching and cross-script search (Chinese→Pinyin→English and vice versa). Returns matches categorized by risk: sanctions, PEP, and other. Optionally includes negative news from GDELT and Chinese court records.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Name to screen (Chinese or English)"},
                "include_news": {"type": "boolean", "description": "Also search GDELT for negative news (default false)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "name_screening_stats",
        "description": "Get name screening database statistics: total records, breakdown by source and risk category, PEP count, Chinese name coverage.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_tags",
        "description": "List all tags with indicator counts. Browse data by topic (通胀, 就业, AI算力, 数据中心, DeFi...) without knowing exact keywords. Use this to discover available data categories, then use list_indicators with a tag filter or search_indicators to drill down.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "data_sources_by_category",
        "description": "List all data sources grouped by three categories: macro (22 sources: US, China, Eurozone, Japan, A-share concept boards, etc.), country_risk (AML ratings, sanctions, CPI), and name_screening (OpenSanctions PEP/sanctions database, Chinese+English fuzzy search). Use for a structured overview of the platform.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_risk_indicators",
        "description": "List all country risk indicators — AML/CFT ratings (FATF, INCSR, Basel), sanctions (OFAC SDN by country), and corruption perception (TI CPI). Optional 'source' param filters by aml or sanctions.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "Filter by risk source: aml or sanctions (optional)"},
            },
        },
    },
    # ── HK Funds ──
    {
        "name": "hk_fund_stats",
        "description": "Get HK fund statistics: total active funds, complex/derivative product counts, OFC counts, private non-authorized fund count, risk rating distribution.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "hk_fund_risk_ratings",
        "description": "Get HK fund risk ratings (5-tier: Low/Medium-Low/Medium/Medium-High/High). Optional 'risk_category' filter. Returns fund name, scores, category, automated flag. Supports pagination.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "risk_category": {"type": "string", "description": "Filter by risk category (Low, Medium-Low, Medium, Medium-High, High)"},
                "limit": {"type": "integer", "description": "Max records (default 200)"},
                "offset": {"type": "integer", "description": "Pagination offset (default 0)"},
            },
        },
    },
    {
        "name": "hk_kyp_dimensions",
        "description": "Get all 10 KYP (Know Your Product) due diligence dimensions for a specific HK fund. Returns dimension status, score, findings, and gaps.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "fund_id": {"type": "integer", "description": "HK fund ID from hk_fund_search or hk_fund_risk_ratings"},
            },
            "required": ["fund_id"],
        },
    },
    {
        "name": "hk_kyp_gaps",
        "description": "Get HK funds with incomplete KYP assessments — missing or pending due diligence dimensions. Ordered by most incomplete first.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max funds to return (default 50)"},
            },
        },
    },
    {
        "name": "hk_complex_products",
        "description": "List SFC §5.5 complex products (14 total: structured products, derivative funds, L&I, synthetic ETFs). Filter by complex_product_type.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "complex_product_type": {"type": "string", "description": "Filter: structured, derivative_fund, L&I, synthetic_etf"},
                "limit": {"type": "integer", "description": "Max records (default 100)"},
            },
        },
    },
    {
        "name": "hk_derivative_products",
        "description": "List SFC §5.1A derivative products (71 funds: NDE > 50%, synthetic, L&I, hedge funds).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max records (default 100)"},
            },
        },
    },
    {
        "name": "hk_ofc_stats",
        "description": "Get OFC (Open-ended Fund Company) statistics: public OFC umbrella/sub-fund counts, private OFC count, unique investment manager count.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "hk_non_authorized_funds",
        "description": "List non-SFC-authorized funds (PI-only distribution). Includes 2,231 Private OFCs auto-fetched from SFC register plus manually entered funds.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "distribution_restriction": {"type": "string", "description": "Filter: pi_only, pi_800k, institutional_only, offshore_only"},
                "limit": {"type": "integer", "description": "Max records (default 100)"},
            },
        },
    },
    {
        "name": "hk_fund_managers",
        "description": "List HK fund managers (SFC licensed corporations). Search by company name (EN/CN) or CE number.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "search": {"type": "string", "description": "Search company name (EN/CN) or CE number"},
                "limit": {"type": "integer", "description": "Max records (default 100)"},
            },
        },
    },
    {
        "name": "hk_manager_dd",
        "description": "Get 10-dimension due diligence (DD) assessment for a fund manager with internal control score (mapped to Scorecard 5-tier: Strong/Sufficient/Average/Limited/Lacking). Returns DD dimensions plus derived internal_control_score.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "manager_id": {"type": "integer", "description": "Manager ID from hk_fund_managers"},
            },
            "required": ["manager_id"],
        },
    },
    {
        "name": "hk_fund_search",
        "description": "Search HK funds by name (EN/CN), ISIN, SFC authorization number, or manager name. Returns matching funds with classification info.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keyword"},
                "limit": {"type": "integer", "description": "Max results (default 50)"},
            },
            "required": ["query"],
        },
    },
    # ── HK Funds v4: ISIN, NAV, Performance, Connectors ──
    {
        "name": "hk_fund_isin_lookup",
        "description": "Look up an HK fund by ISIN code. Returns full fund details including latest NAV and performance metrics if available.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "isin": {"type": "string", "description": "ISIN code (12 characters, e.g., HK0000123456)"},
            },
            "required": ["isin"],
        },
    },
    {
        "name": "hk_fund_isins",
        "description": "List HK funds that have ISIN codes. Returns fund name, ISIN, manager, type, and domicile. Supports pagination.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max records (default 200)"},
                "offset": {"type": "integer", "description": "Pagination offset (default 0)"},
            },
        },
    },
    {
        "name": "hk_fund_nav_history",
        "description": "Get NAV (Net Asset Value) time series for an HK fund. Returns nav_date, nav, nav_currency. Supports date range filtering.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "fund_id": {"type": "integer", "description": "HK fund ID from hk_fund_search or hk_fund_risk_ratings"},
                "start": {"type": "string", "description": "Start date (YYYY-MM-DD), optional"},
                "end": {"type": "string", "description": "End date (YYYY-MM-DD), optional"},
                "limit": {"type": "integer", "description": "Max records (default 500)"},
            },
            "required": ["fund_id"],
        },
    },
    {
        "name": "hk_fund_latest_nav",
        "description": "Get the most recent NAV (Net Asset Value) for an HK fund. Returns nav, nav_date, nav_currency.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "fund_id": {"type": "integer", "description": "HK fund ID from hk_fund_search or hk_fund_risk_ratings"},
            },
            "required": ["fund_id"],
        },
    },
    {
        "name": "hk_fund_performance",
        "description": "Get performance metrics for an HK fund: YTD/1M/3M/6M/1Y/3Y/5Y returns, risk metrics (std dev, Sharpe ratio, max drawdown, alpha, beta, R-squared), and calculation date.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "fund_id": {"type": "integer", "description": "HK fund ID from hk_fund_search or hk_fund_risk_ratings"},
            },
            "required": ["fund_id"],
        },
    },
    {
        "name": "hk_manager_scrape_status",
        "description": "Get HK fund manager connector registry status: lists registered connectors with CE numbers and websites, plus top managers still needing connectors.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "hk_fund_holdings",
        "description": "Get top holdings for a HK SFC-authorized fund. Shows holding name, weight_pct, sector, country where available.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "fund_id": {"type": "integer", "description": "Fund ID from hk_fund_search or hk_fund_stats"},
                "limit": {"type": "integer", "description": "Max holdings to return (default 20)"},
            },
            "required": ["fund_id"],
        },
    },
    {
        "name": "hk_fund_dividends",
        "description": "Get dividend history for a HK SFC-authorized fund. Shows ex_date, pay_date, dividend_amount, dividend_type.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "fund_id": {"type": "integer", "description": "Fund ID from hk_fund_search"},
                "limit": {"type": "integer", "description": "Max records (default 20)"},
            },
            "required": ["fund_id"],
        },
    },
    {
        "name": "hk_fund_share_classes",
        "description": "Get share classes for a HK SFC-authorized fund. Shows ISIN, currency, hedging, distribution type for each share class variant.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "fund_id": {"type": "integer", "description": "Fund ID from hk_fund_search"},
            },
            "required": ["fund_id"],
        },
    },
    # ── HK Fund Portfolio Manager ──
    {
        "name": "hk_fund_portfolio_manager",
        "description": "Query portfolio manager info for HK SFC-authorized funds. Use fund_id to get a specific fund's portfolio manager, or search to find funds managed by a person/entity.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "fund_id": {"type": "integer", "description": "Fund ID to get portfolio manager for (optional)"},
                "search": {"type": "string", "description": "Search portfolio manager name (optional)"},
                "limit": {"type": "integer", "description": "Max results when searching (default 50)"},
            },
        },
    },
    # ── HK Rating Templates ──
    {
        "name": "hk_rating_template_list",
        "description": "List rating templates. If user_id provided, shows that user's templates (system templates if empty). Filter by fund_risk or manager_dd template_type.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "User ID (empty for system templates)"},
                "template_type": {"type": "string", "description": "Template type: fund_risk or manager_dd"},
            },
        },
    },
    {
        "name": "hk_rating_template_get",
        "description": "Get a full rating template with all factors, weights, configs, and category thresholds.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "template_id": {"type": "integer", "description": "Template ID from hk_rating_template_list"},
            },
            "required": ["template_id"],
        },
    },
    {
        "name": "hk_rating_template_clone",
        "description": "Clone a rating template for a user. Source can be system or user template. Returns new template with all factors.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source_template_id": {"type": "integer", "description": "Source template ID to clone"},
                "user_id": {"type": "string", "description": "User ID to own the new template"},
                "new_name": {"type": "string", "description": "Optional new name for cloned template"},
            },
            "required": ["source_template_id", "user_id"],
        },
    },
    {
        "name": "hk_rating_template_update",
        "description": "Update a user-owned template's factor weights, thresholds, name, or description. System templates cannot be modified.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "template_id": {"type": "integer", "description": "Template ID to update (must be user-owned)"},
                "user_id": {"type": "string", "description": "User ID (must match template owner)"},
                "factor_weights": {"type": "string", "description": "JSON: {\"factor_key\": weight, ...}"},
                "category_thresholds": {"type": "string", "description": "JSON: [{\"max\": 1.5, \"label\": \"Low\"}, ...]"},
                "name": {"type": "string", "description": "New template name"},
                "description": {"type": "string", "description": "New template description"},
            },
            "required": ["template_id", "user_id"],
        },
    },
    {
        "name": "hk_rating_compute",
        "description": "Compute ratings using a template. If target_id provided, rates a single target (fund or manager). Otherwise rates all targets. Results stored in hk_user_ratings.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "template_id": {"type": "integer", "description": "Template ID to use for rating"},
                "user_id": {"type": "string", "description": "User ID to associate ratings with"},
                "target_type": {"type": "string", "description": "Target type: fund or manager (default fund)"},
                "target_id": {"type": "integer", "description": "Specific target ID (omit to rate all)"},
            },
            "required": ["template_id", "user_id"],
        },
    },
    {
        "name": "hk_rating_results",
        "description": "Get rating results for a template+user combination. Shows category distribution and rated targets with scores.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "template_id": {"type": "integer", "description": "Template ID"},
                "user_id": {"type": "string", "description": "User ID"},
                "target_type": {"type": "string", "description": "Target type: fund or manager (default fund)"},
                "limit": {"type": "integer", "description": "Max results to return (default 100)"},
            },
            "required": ["template_id", "user_id"],
        },
    },
    # ── US Corp Actions ──
    {
        "name": "us_corp_actions",
        "description": "Query US corporate actions (SEC 8-K filings). Filter by ticker or date range. Returns filing date, ticker, company name, item, description.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Stock ticker (e.g., AAPL, TSLA)"},
                "date_from": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                "date_to": {"type": "string", "description": "End date (YYYY-MM-DD)"},
                "limit": {"type": "integer", "description": "Max records (default 100)"},
                "offset": {"type": "integer", "description": "Pagination offset (default 0)"},
            },
        },
    },
    {
        "name": "us_corp_action_dates",
        "description": "Get available SEC filing dates with counts. Use to discover what dates have data before querying.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "us_corp_action_summary",
        "description": "Get US corporate action type breakdown (item distribution) for a date range.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                "date_to": {"type": "string", "description": "End date (YYYY-MM-DD)"},
            },
        },
    },
    # ── US Listings ──
    {
        "name": "us_listings",
        "description": "Query US IPO and direct listings. Filter by type (IPO, SPAC, Direct Listing) or date range.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "listing_type": {"type": "string", "description": "Listing type: IPO, SPAC, Direct Listing"},
                "date_from": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                "limit": {"type": "integer", "description": "Max records (default 100)"},
            },
        },
    },
    {
        "name": "us_crypto_products",
        "description": "Query US crypto products (ETPs, trusts). Filter by underlying asset (BTC, ETH). Returns AUM, issuer, fee data.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "asset": {"type": "string", "description": "Underlying asset: BTC, ETH, etc."},
                "limit": {"type": "integer", "description": "Max records (default 100)"},
            },
        },
    },
    {
        "name": "us_insider_transactions",
        "description": "Query US insider transactions (SEC Form 4 filings). Filter by ticker. Shows transaction type, shares, price, insider name.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Stock ticker (e.g., AAPL)"},
                "limit": {"type": "integer", "description": "Max records (default 100)"},
            },
        },
    },
    {
        "name": "us_institutional_holdings",
        "description": "Query US institutional holdings (SEC 13F filings). Filter by ticker. Shows fund, shares held, market value, change.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Stock ticker (e.g., AAPL)"},
                "limit": {"type": "integer", "description": "Max records (default 100)"},
            },
        },
    },
    # ── Announcements ──
    {
        "name": "announcements",
        "description": "Query company announcements. Filter by market (HK, US, CN) or search title text.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "market": {"type": "string", "description": "Market filter: HK, US, CN"},
                "search": {"type": "string", "description": "Search in announcement title"},
                "date_from": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                "limit": {"type": "integer", "description": "Max records (default 100)"},
            },
        },
    },
    {
        "name": "announcement_companies",
        "description": "Get tracked companies with announcement counts per market.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    # ── KR Stock ──
    {
        "name": "kr_stock_stats",
        "description": "Get Korean stock database statistics: total stocks by market (KOSPI/KOSDAQ/KONEX), trading days, significant movers, DART filings count.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "kr_listed_stocks",
        "description": "Search Korean listed stocks. Filter by market (KOSPI, KOSDAQ, KONEX), sector, or name/code search.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "market": {"type": "string", "description": "Market: KOSPI, KOSDAQ, or KONEX"},
                "sector": {"type": "string", "description": "Sector filter"},
                "search": {"type": "string", "description": "Search by company name or stock code"},
                "limit": {"type": "integer", "description": "Max records (default 200)"},
            },
        },
    },
    {
        "name": "kr_daily_movers",
        "description": "Get stocks with significant daily price moves (>=10%) for a date. Default: latest trading day. Includes LLM-generated reasons if available.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Trading date (YYYY-MM-DD), defaults to latest"},
                "limit": {"type": "integer", "description": "Max records (default 100)"},
            },
        },
    },
    {
        "name": "kr_market_indices",
        "description": "Get KOSPI (KS11) or KOSDAQ (KQ11) index OHLCV data. Leave index_code empty for both.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "index_code": {"type": "string", "description": "KS11 (KOSPI) or KQ11 (KOSDAQ), empty for both"},
                "limit": {"type": "integer", "description": "Max records (default 200)"},
            },
        },
    },
    {
        "name": "kr_foreign_flows",
        "description": "Get foreign/institutional daily net buy data by market. Shows foreign, institutional, and individual net buy amounts.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "market": {"type": "string", "description": "KOSPI or KOSDAQ, empty for both"},
                "limit": {"type": "integer", "description": "Max records (default 60)"},
            },
        },
    },
    {
        "name": "kr_dart_filings",
        "description": "Search DART corporate filings (Korea's EDGAR equivalent). Filter by company name or report type (e.g., 사업보고서, 감사보고서, 증권신고서).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "corp_name": {"type": "string", "description": "Company name filter (partial match)"},
                "report_type": {"type": "string", "description": "Report type filter (e.g., 사업보고서 for annual reports)"},
                "limit": {"type": "integer", "description": "Max records (default 100)"},
            },
        },
    },
    {
        "name": "kr_stock_detail",
        "description": "Get detailed info for a Korean stock: listing info, recent 30-day prices, and significant mover history with reasons.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Stock code (e.g., 005930 for Samsung Electronics)"},
            },
            "required": ["code"],
        },
    },
    {
        "name": "kr_stock_metrics",
        "description": "Get valuation metrics for a Korean stock: P/E, P/B, market cap, dividend yield, beta, revenue/earnings growth, margins, ROE, ROA, institutional holdings, 50/200-day MA, 52-week high/low — all from yfinance.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Stock code (e.g., 005930 for Samsung Electronics)"},
            },
            "required": ["code"],
        },
    },
    {
        "name": "kr_stock_financials",
        "description": "Get quarterly financial statements for a Korean stock from yfinance. statement_type: BS (Balance Sheet), IS (Income Statement), CF (Cash Flow). Returns up to 4 quarters of data.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Stock code (e.g., 005930 for Samsung Electronics)"},
                "statement_type": {"type": "string", "description": "Statement type: BS (Balance Sheet), IS (Income Statement), or CF (Cash Flow). Leave empty for all."},
            },
            "required": ["code"],
        },
    },
    {
        "name": "kr_stock_analyst",
        "description": "Get analyst consensus for a Korean stock: price targets (mean/high/low/median), recommendations, number of analysts, earnings/revenue estimates, EPS trend — all from yfinance.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Stock code (e.g., 005930 for Samsung Electronics)"},
            },
            "required": ["code"],
        },
    },
    # ── TW Stock ──
    {
        "name": "tw_stock_stats",
        "description": "Get Taiwan stock database statistics: total stocks by market (TWSE/TPEx), trading days, significant movers count.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "tw_listed_stocks",
        "description": "Search Taiwan listed stocks. Filter by market (TWSE, TPEx), sector, or name/code search.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "market": {"type": "string", "description": "Market: TWSE or TPEx"},
                "sector": {"type": "string", "description": "Sector filter"},
                "search": {"type": "string", "description": "Search by company name or stock code"},
                "limit": {"type": "integer", "description": "Max records (default 200)"},
            },
        },
    },
    {
        "name": "tw_daily_movers",
        "description": "Get Taiwan stocks with significant daily price moves (>=5%) for a date. Default: latest trading day. Includes LLM-generated reasons if available.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Trading date (YYYY-MM-DD), defaults to latest"},
                "limit": {"type": "integer", "description": "Max records (default 100)"},
            },
        },
    },
    {
        "name": "tw_market_indices",
        "description": "Get TAIEX (^TWII) or TPEx (^TWOII) index OHLCV data. Leave index_code empty for both.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "index_code": {"type": "string", "description": "^TWII (TAIEX) or ^TWOII (TPEx), empty for both"},
                "limit": {"type": "integer", "description": "Max records (default 200)"},
            },
        },
    },
    {
        "name": "tw_stock_detail",
        "description": "Get detailed info for a Taiwan stock: listing info, recent 30-day prices, and significant mover history with reasons.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Stock code (e.g., 2330 for TSMC, 5347 for Vanguard)"},
            },
            "required": ["code"],
        },
    },
    # ── Name Data & I Ching ──
    {
        "name": "calendar_ganzhi",
        "description": "Get Chinese calendar (农历/干支) info for a date. Returns year stem-branch (年干支), month stem-branch (月干支), day stem-branch (日干支), current solar term (节气), zodiac animal (生肖), and the day's position in the 60-day cycle. Useful for date conversion, BaZi reference, and determining favorable/unfavorable days. Leave date_str empty for today.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date_str": {"type": "string", "description": "Date in YYYY-MM-DD format. Empty for today."},
            },
        },
    },
    {
        "name": "name_generate",
        "description": "Generate auspicious Chinese name candidates (智能取名) based on birth BaZi and surname. Calculates favorable five element, finds compatible WuGe stroke combinations, queries matching characters, and returns 30+ ranked name suggestions with scores and analysis.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "surname": {"type": "string", "description": "Surname (姓), e.g. 张"},
                "birth_year": {"type": "integer", "description": "Birth year, e.g. 1985"},
                "birth_month": {"type": "integer", "description": "Birth month (1-12)"},
                "birth_day": {"type": "integer", "description": "Birth day (1-31)"},
                "birth_hour": {"type": "integer", "description": "Birth hour (0-23), default 12"},
                "gender": {"type": "string", "description": "Gender: 男 or 女, default 男"},
                "num_names": {"type": "integer", "description": "Number of names to generate, default 30"},
            },
            "required": ["surname", "birth_year", "birth_month", "birth_day"],
        },
    },
    {
        "name": "name_score",
        "description": "Score a Chinese name (姓名评分). Full analysis: BaZi (八字), WuGe (五格), SanCai (三才), zodiac (生肖), phonetics (音调), meaning (字义). Requires surname, given_name, and optionally birth date/time.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "surname": {"type": "string", "description": "Surname (姓), e.g. 张"},
                "given_name": {"type": "string", "description": "Given name (名), e.g. 三"},
                "birth_year": {"type": "integer", "description": "Birth year, e.g. 1985"},
                "birth_month": {"type": "integer", "description": "Birth month (1-12)"},
                "birth_day": {"type": "integer", "description": "Birth day (1-31)"},
                "birth_hour": {"type": "integer", "description": "Birth hour (0-23), default 12"},
                "gender": {"type": "string", "description": "Gender: 男 or 女, default 男"},
            },
            "required": ["surname", "given_name"],
        },
    },
    {
        "name": "name_bazi",
        "description": "Calculate BaZi (八字) four pillars from birth date/time. Returns year/month/day/hour pillars, day master (日主), five element counts, favorable element, and zodiac.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "year": {"type": "integer", "description": "Birth year, e.g. 1985"},
                "month": {"type": "integer", "description": "Birth month (1-12)"},
                "day": {"type": "integer", "description": "Birth day (1-31)"},
                "hour": {"type": "integer", "description": "Birth hour (0-23), default 12"},
            },
            "required": ["year", "month", "day"],
        },
    },
    {
        "name": "name_wuge",
        "description": "Calculate Wu Ge (五格) stroke grids: Heaven/Personality/Earth/Outer/Total grids with 81-shuli analysis. Returns stroke counts, elements, and ji-xiong (吉凶) for each grid.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "surname": {"type": "string", "description": "Surname (姓), e.g. 张"},
                "given_name": {"type": "string", "description": "Given name (名), e.g. 三"},
            },
            "required": ["surname", "given_name"],
        },
    },
    {
        "name": "iching_divine",
        "description": "I Ching (周易) hexagram divination. method='coins' for coin toss (金钱卦), 'numbers' for 3-number method (数字卦/梅花易数). For numbers: a=upper trigram(1-8), b=lower trigram(1-8), c=changing line(1-6). Returns primary/mutual/changed hexagrams with judgments.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "method": {"type": "string", "description": "Divination method: 'coins' (default) or 'numbers'"},
                "a": {"type": "integer", "description": "Upper trigram number 1-8 (for numbers method)"},
                "b": {"type": "integer", "description": "Lower trigram number 1-8 (for numbers method)"},
                "c": {"type": "integer", "description": "Changing line number 1-6 (for numbers method)"},
            },
        },
    },
    {
        "name": "tuibei_consult",
        "description": "Consult Tui Bei Tu (推背图) — Tang Dynasty prophetic classic by Li Chunfeng and Yuan Tiangang. 60 prophecies covering ~2000 years of Chinese history. Methods: 'random' draws a prophecy, 'hexagram' looks up by I Ching hexagram ID (with fallback to wrong/reverse hexagrams), 'index' gets a specific prophecy by number (1-60). Returns image description, poems (谶/颂), linked hexagram, and historical era.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "method": {"type": "string", "description": "Consultation method: 'random' (default), 'hexagram', or 'index'"},
                "hexagram_id": {"type": "integer", "description": "Hexagram ID (1-64) for 'hexagram' method, or prophecy number (1-60) for 'index' method"},
            },
        },
    },
    {
        "name": "daily_fortune",
        "description": "Get pre-computed daily fortune (每日运势): Chinese calendar (year/month/day stem-branch), current solar term, zodiac, daily I Ching hexagram (deterministic per date), and overall fortune level (大吉/吉/平/凶). Each day's hexagram is generated by coin divination seeded by the date. Leave date_str empty for today.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date_str": {"type": "string", "description": "Date in YYYY-MM-DD format. Empty for today."},
            },
        },
    },
    {
        "name": "huangli",
        "description": "Get Chinese Almanac (黄历) for any date. Returns jianchu twelve gods (建除十二神), yellow/black path (黄道黑道), 28 lunar mansions (二十八宿), Peng Zu taboos (彭祖百忌), and combined daily suitable/avoid activities (宜忌). Based on 《协纪辨方书》algorithms. Each day gets a score from -100 (worst) to 100 (best). Leave date_str empty for today.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date_str": {"type": "string", "description": "Date in YYYY-MM-DD format. Empty for today."},
            },
        },
    },
    # ── Korean Retail Leverage (kimpremium.com) ──
    {
        "name": "kr_leverage_summary",
        "description": "Get Korean retail leverage KPI snapshot: R2 margin ratio, 5d-avg forced liquidation, KOSPI mcap/GDP valuation, credit utilization, leveraged ETF thermo reading, deposits. Data from KOFIA FreeSIS / KSD SEIBro (daily, 1998-present).",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "kr_leverage_series",
        "description": "Get a single-indicator time series from the Korean retail leverage database. Available indicators: r2 (margin/deposits), p10 (10y percentile), kospi, kosdaq, spx, fin/finKospi/finKosdaq (margin balance in KRW trillion), dep (investor deposits), liq (forced liquidation in 100M KRW), liqR (liq/misu ratio), mg (KOSPI mcap/GDP), util (credit/capital ratio), and more. 7138+ trading days from 1998.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "indicator": {"type": "string", "description": "Indicator code: r2, p10, kospi, spx, fin, dep, liq, liqR, mg, util, misu, r1, etc."},
                "limit": {"type": "integer", "description": "Max records (default 200)"},
            },
        },
    },
    {
        "name": "kr_leverage_etf",
        "description": "Get Korean leveraged ETF daily flow data: thermo (leverage thermometer, %), flow (daily net subscription in 100M KRW), cumFlow (cumulative net flow in KRW trillion). Covers ~38 domestic leveraged ETFs. Data from KSD SEIBro, 2024-present.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "indicator": {"type": "string", "description": "Indicator: thermo, thermoW, flow, flowW, cumFlow, cumFlowW"},
                "limit": {"type": "integer", "description": "Max records (default 200)"},
            },
        },
    },
    # ── SK Hynix Cross-Market ──
    {
        "name": "hynix_arbitrage",
        "description": "Get SK Hynix cross-market arbitrage comparison: premium/discount vs KR base stock (000660.KS) across all tracked instruments — US ADR (SKHY, 10:1 ratio), HK 2x leveraged ETP (7709.HK), KR single-stock leveraged ETFs. Shows equivalent KRW cost per share and premium percentage. Leave date empty for latest.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Date YYYY-MM-DD, empty for latest"},
            },
        },
    },
    {
        "name": "hynix_instruments",
        "description": "List tracked SK Hynix instruments across all markets: KR stock, US ADR, HK ETP, KR ETFs. Shows ticker, market, currency, instrument type, leverage, and tracking ratio.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "market": {"type": "string", "description": "Filter by market: KR, US, HK. Empty for all."},
            },
        },
    },
    {
        "name": "hynix_prices",
        "description": "Get SK Hynix instrument prices. Either provide ticker for price history, or date for all instruments on that date.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Instrument ticker (e.g., 000660.KS, SKHY, 7709.HK). Leave empty to use date-based query."},
                "date": {"type": "string", "description": "Date YYYY-MM-DD for all-instruments snapshot. Used when ticker is empty."},
                "limit": {"type": "integer", "description": "Max records for ticker history (default 30)"},
            },
        },
    },
    # ── A-Share ETF ──
    {
        "name": "a_share_etf_status",
        "description": "Get A-share ETF flow database status: available tables and row counts.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "a_share_etf_flows",
        "description": "Get A-share ETF sector flow data. Returns per-sector aggregated net inflows, ETF counts, and total turnover. Leave date empty for latest available. Leave sector empty for all sectors. Use sector param with no date for sector history.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Date YYYY-MM-DD, empty for latest"},
                "sector": {"type": "string", "description": "Sector name (e.g., 沪深300, 科创板, 信息技术, 医药). Empty for all."},
                "limit": {"type": "integer", "description": "Max records for sector history (default 50)"},
            },
        },
    },
    {
        "name": "a_share_etf_detail",
        "description": "Get per-ETF daily detail: either price+flow history for a specific ETF code, or all ETFs for a specific date. Leave both empty for latest date's top 50 ETFs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "ETF code (e.g., 510050). Empty to query by date."},
                "date": {"type": "string", "description": "Date YYYY-MM-DD. Used when code is empty."},
                "limit": {"type": "integer", "description": "Max records for ETF history (default 30)"},
            },
        },
    },
    {
        "name": "a_share_margin",
        "description": "Get A-share margin balance (融资融券余额): Shanghai + Shenzhen combined, with daily change. Shows latest snapshot and recent history.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max history records to return (default 30)"},
            },
        },
    },
    {
        "name": "a_share_etf_overview",
        "description": "Get A-share ETF daily market overview with merged proxy (合并代理). Merged proxy = total ETF net inflow - margin_change. Positive means net capital flowing into the A-share market. Leave date empty for latest.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Date YYYY-MM-DD, empty for latest"},
                "limit": {"type": "integer", "description": "Max history records (default 30)"},
            },
        },
    },
]

TOOL_MAP = {
    "list_indicators": tool_list_indicators,
    "query_data": tool_query_data,
    "get_latest": tool_get_latest,
    "search_indicators": tool_search_indicators,
    "data_summary": tool_data_summary,
    "data_sources": tool_data_sources,
    "cn_stock_status": tool_cn_stock_status,
    "search_name": tool_search_name,
    "name_screening_stats": tool_name_screening_stats,
    "list_tags": tool_list_tags,
    "data_sources_by_category": tool_data_sources_by_category,
    "list_risk_indicators": tool_list_risk_indicators,
    # HK Funds
    "hk_fund_stats": tool_hk_fund_stats,
    "hk_fund_risk_ratings": tool_hk_fund_risk_ratings,
    "hk_kyp_dimensions": tool_hk_kyp_dimensions,
    "hk_kyp_gaps": tool_hk_kyp_gaps,
    "hk_complex_products": tool_hk_complex_products,
    "hk_derivative_products": tool_hk_derivative_products,
    "hk_ofc_stats": tool_hk_ofc_stats,
    "hk_non_authorized_funds": tool_hk_non_authorized_funds,
    "hk_fund_managers": tool_hk_fund_managers,
    "hk_manager_dd": tool_hk_manager_dd,
    "hk_fund_search": tool_hk_fund_search,
    # HK Funds v4
    "hk_fund_isin_lookup": tool_hk_fund_isin_lookup,
    "hk_fund_isins": tool_hk_fund_isins,
    "hk_fund_nav_history": tool_hk_fund_nav_history,
    "hk_fund_latest_nav": tool_hk_fund_latest_nav,
    "hk_fund_performance": tool_hk_fund_performance,
    "hk_manager_scrape_status": tool_hk_manager_scrape_status,
    "hk_fund_holdings": tool_hk_fund_holdings,
    "hk_fund_dividends": tool_hk_fund_dividends,
    "hk_fund_share_classes": tool_hk_fund_share_classes,
    "hk_fund_portfolio_manager": tool_hk_fund_portfolio_manager,
    # HK Rating Templates
    "hk_rating_template_list": tool_hk_rating_template_list,
    "hk_rating_template_get": tool_hk_rating_template_get,
    "hk_rating_template_clone": tool_hk_rating_template_clone,
    "hk_rating_template_update": tool_hk_rating_template_update,
    "hk_rating_compute": tool_hk_rating_compute,
    "hk_rating_results": tool_hk_rating_results,
    # US Corp Actions
    "us_corp_actions": tool_us_corp_actions,
    "us_corp_action_dates": tool_us_corp_action_dates,
    "us_corp_action_summary": tool_us_corp_action_summary,
    # US Listings
    "us_listings": tool_us_listings,
    "us_crypto_products": tool_us_crypto_products,
    "us_insider_transactions": tool_us_insider_transactions,
    "us_institutional_holdings": tool_us_institutional_holdings,
    # Announcements
    "announcements": tool_announcements,
    "announcement_companies": tool_announcement_companies,
    # KR Stock
    "kr_stock_stats": tool_kr_stock_stats,
    "kr_listed_stocks": tool_kr_listed_stocks,
    "kr_daily_movers": tool_kr_daily_movers,
    "kr_market_indices": tool_kr_market_indices,
    "kr_foreign_flows": tool_kr_foreign_flows,
    "kr_dart_filings": tool_kr_dart_filings,
    "kr_stock_detail": tool_kr_stock_detail,
    "kr_stock_metrics": tool_kr_stock_metrics,
    "kr_stock_financials": tool_kr_stock_financials,
    "kr_stock_analyst": tool_kr_stock_analyst,
    # TW Stock
    "tw_stock_stats": tool_tw_stock_stats,
    "tw_listed_stocks": tool_tw_listed_stocks,
    "tw_daily_movers": tool_tw_daily_movers,
    "tw_market_indices": tool_tw_market_indices,
    "tw_stock_detail": tool_tw_stock_detail,
    # Name Data & I Ching
    "calendar_ganzhi": tool_calendar_ganzhi,
    "name_generate": tool_name_generate,
    "name_score": tool_name_score,
    "tuibei_consult": tool_tuibei_consult,
    "daily_fortune": tool_daily_fortune,
    "huangli": tool_huangli,
    "name_bazi": tool_name_bazi,
    "name_wuge": tool_name_wuge,
    "iching_divine": tool_iching_divine,
    # SK Hynix Cross-Market
    "hynix_arbitrage": tool_hynix_arbitrage,
    "hynix_instruments": tool_hynix_instruments,
    "hynix_prices": tool_hynix_prices,
    # Korean Retail Leverage (kimpremium.com)
    "kr_leverage_summary": tool_kr_leverage_summary,
    "kr_leverage_series": tool_kr_leverage_series,
    "kr_leverage_etf": tool_kr_leverage_etf,
    # A-Share ETF
    "a_share_etf_status": tool_a_share_etf_status,
    "a_share_etf_flows": tool_a_share_etf_flows,
    "a_share_etf_detail": tool_a_share_etf_detail,
    "a_share_margin": tool_a_share_margin,
    "a_share_etf_overview": tool_a_share_etf_overview,
}


# ── MCP JSON-RPC Protocol ─────────────────────────────────────


def _log(msg: str):
    """Write to stderr for debugging (stdout is the MCP transport)."""
    print(f"[eco-data MCP] {msg}", file=sys.stderr, flush=True)


def _send(data: dict):
    """Send a JSON-RPC message to stdout."""
    sys.stdout.write(json.dumps(data) + "\n")
    sys.stdout.flush()


def handle_request(req: dict) -> Optional[dict]:
    """Handle a single JSON-RPC request. Returns response or None for notifications."""
    method = req.get("method", "")
    req_id = req.get("id")

    _log(f"← {method}")

    # ── initialize ──
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {
                    "name": "eco-data",
                    "version": "2.0.0",
                    "description": "Global economic intelligence platform — MACRO (22 sources: US/FRED, China/AKShare, Eurozone, UK, Germany, Japan, Australia, Canada, Switzerland, Hong Kong, World Bank, bond & futures, shipping, central bank rates, alternative, LLM ecosystem, DeFi, energy/EIA, AI infrastructure, AI company financials, A-share concept boards), COUNTRY RISK (FATF/INCSR/Basel AML ratings, OFAC sanctions, TI CPI), NAME SCREENING (OpenSanctions, Chinese+English fuzzy search), HK FUNDS KYP/DD v4 (2,021 SFC-authorized + 2,231 Private OFC, ISIN lookup, 5-tier risk ratings, 10-dimension KYP, complex/derivative classification, manager DD, NAV history, performance metrics, connector scraping status), US CORP ACTIONS (SEC 8-K filings), US LISTINGS (IPOs, crypto ETPs, insider trades, institutional holdings), ANNOUNCEMENTS (HK/US/CN company announcements), KR STOCK (KOSPI/KOSDAQ, DART filings, valuation, financials, analyst), TW STOCK (TWSE/TPEx). Use data_sources_by_category for structured overview.",
                },
                "capabilities": {
                    "tools": {},
                },
            },
        }

    # ── notifications (no response) ──
    if method == "notifications/initialized":
        _log("initialized")
        return None
    if method == "notifications/cancelled":
        _log("cancelled")
        return None

    # ── tools/list ──
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": TOOLS},
        }

    # ── tools/call ──
    if method == "tools/call":
        params = req.get("params", {})
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})

        if tool_name not in TOOL_MAP:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
            }

        try:
            result = TOOL_MAP[tool_name](**tool_args)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}
                    ]
                },
            }
        except Exception as e:
            _log(f"tool error: {e}")
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {"type": "text", "text": json.dumps({"error": str(e)})}
                    ],
                    "isError": True,
                },
            }

    # ── ping ──
    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}

    # ── unknown ──
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def main():
    _log(f"starting, db={DB_PATH}")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            resp = handle_request(req)
            if resp is not None:
                _send(resp)
        except json.JSONDecodeError as e:
            _log(f"JSON parse error: {e}")
        except Exception as e:
            _log(f"unhandled error: {e}")


if __name__ == "__main__":
    main()
