"""
Manager scoring for fund risk rating Scorecard.

Maps the 10-dimension manager DD assessment to Scorecard factors:
  - Internal Control (14% weight): maps DD dimension pass count to 5-tier rating

No new data dependencies — works entirely from existing hk_manager_dd data.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("hk_funds.manager_scoring")

# 10 DD dimensions
DD_DIMENSIONS = [
    "financial_resources", "human_resources", "internal_controls",
    "risk_governance", "segregation_duties", "compliance_function",
    "audit_function", "custodian_dd", "valuer_dd", "delegates_monitoring",
]

# Pass thresholds for individual DD dimensions
DD_PASS_SCORE = 4           # score >= 4 counts as pass (strong control)
DD_PASS_STATUSES = {"reviewed", "approved", "auto_derived"}  # status must be one of these

# Scorecard Internal Control tier mapping
# Based on how many of 10 DD dimensions pass
INTERNAL_CONTROL_TIERS = [
    # (min_pass, max_pass, tier_name, scorecard_score)
    (8, 10, "Strong",    1),
    (6, 7,  "Sufficient", 2),
    (4, 5,  "Average",   3),
    (2, 3,  "Limited",   4),
    (0, 1,  "Lacking",   5),
]


def score_internal_control(dd_dimensions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Score internal control from 10-dimension DD assessment.

    Maps the number of passed DD dimensions to the Scorecard 5-tier
    Internal Control rating (part of Fund House, 14% weight).

    Args:
        dd_dimensions: List of dicts from hk_manager_dd with keys:
            dd_dimension, assessment_status, score, findings, gaps

    Returns:
        {
            tier: "Strong" | "Sufficient" | "Average" | "Limited" | "Lacking",
            score: 1-5 (1=best, lower risk),
            passed_count: int,
            total_dimensions: 10,
            dimension_details: [{dimension, status, score, pass}, ...],
            rationale: str,
        }
    """
    total = len(DD_DIMENSIONS)
    passed = 0
    details = []

    for dim_key in DD_DIMENSIONS:
        dim_data = next(
            (d for d in dd_dimensions if d.get("dd_dimension") == dim_key),
            None,
        )
        if dim_data:
            status = dim_data.get("assessment_status", "pending")
            score_val = dim_data.get("score") or 0
            is_pass = status in DD_PASS_STATUSES and score_val >= DD_PASS_SCORE
            if is_pass:
                passed += 1
            details.append({
                "dimension": dim_key,
                "status": status,
                "score": score_val,
                "pass": is_pass,
            })
        else:
            details.append({
                "dimension": dim_key,
                "status": "not_assessed",
                "score": 0,
                "pass": False,
            })

    # Map to tier
    tier_name = "Lacking"
    tier_score = 5
    for lo, hi, name, sc in INTERNAL_CONTROL_TIERS:
        if lo <= passed <= hi:
            tier_name = name
            tier_score = sc
            break

    return {
        "tier": tier_name,
        "score": tier_score,
        "passed_count": passed,
        "total_dimensions": total,
        "dimension_details": details,
        "rationale": (
            f"{passed}/{total} DD dimensions passed (score ≥ {DD_PASS_SCORE}, "
            f"status in {DD_PASS_STATUSES}) → "
            f"Internal Control: {tier_name} (Scorecard: {tier_score})"
        ),
    }


def get_manager_scoring(conn, manager_id: int) -> Dict[str, Any]:
    """Get full manager scoring data for Scorecard integration.

    Queries hk_manager_dd and computes internal control score.
    """
    from hk_funds.storage import get_manager_dd

    dd_df = get_manager_dd(conn, manager_id)
    if dd_df is None or len(dd_df) == 0:
        return {
            "manager_id": manager_id,
            "internal_control": None,
            "error": "No DD data available for this manager",
        }

    dd_dicts = dd_df.to_dict(orient="records")
    ic_score = score_internal_control(dd_dicts)

    return {
        "manager_id": manager_id,
        "internal_control": ic_score,
    }


def score_all_managers(conn) -> List[Dict[str, Any]]:
    """Score internal control for all managers with DD data.

    Returns list of {manager_id, company_name_en, internal_control, ...}.
    """
    rows = conn.execute("""
        SELECT m.id, m.company_name_en, m.ce_number
        FROM hk_fund_managers m
        WHERE EXISTS (
            SELECT 1 FROM hk_manager_dd dd
            WHERE dd.manager_id = m.id
        )
        ORDER BY m.company_name_en
    """).fetchall()

    results = []
    for row in rows:
        mgr_id, name, ce = row[0], row[1], row[2]
        scoring = get_manager_scoring(conn, mgr_id)
        results.append({
            "manager_id": mgr_id,
            "company_name_en": name,
            "ce_number": ce,
            "internal_control": scoring.get("internal_control"),
        })

    return results
