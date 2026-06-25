"""
HK Fund Risk Rating Engine — 5-tier weighted scoring per SFC guidance.

Methodology:
  - 5 tiers: Low (1), Medium-Low (2), Medium (3), Medium-High (4), High (5)
  - 6 weighted factors, each scored 1-5
  - Supports automated calculation and manual override with rationale

Usage:
    python -m hk_funds.risk_rating --rate-all
    python -m hk_funds.risk_rating --fund-id 123
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

from hk_funds.storage import (
    init_db,
    get_funds,
    upsert_fund_risk_rating,
    get_fund_risk_rating,
    get_all_risk_ratings,
    init_kyp_dimensions,
    upsert_kyp_dimension,
)

logger = logging.getLogger("hk_funds.risk_rating")

METHODOLOGY_VERSION = "1.0"

# Weighted scoring factors (must sum to 1.0)
FACTOR_WEIGHTS = {
    "complexity": 0.25,       # Product complexity
    "underlying_risk": 0.25,  # Underlying asset risk
    "leverage": 0.15,         # Leverage / derivatives exposure
    "liquidity": 0.15,        # Liquidity / lock-in
    "credit_quality": 0.10,   # Credit quality of underlying
    "currency_country": 0.10, # Currency & country risk
}

# Fund type → underlying asset risk score (1-5)
ASSET_RISK_MAP = {
    "money_market": 1,
    "short_term_bond": 2,
    "bond": 2,
    "fixed_income": 2,
    "balanced": 3,
    "mixed_asset": 3,
    "equity": 4,
    "sector_equity": 4,
    "commodity": 5,
    "crypto": 5,
    "unit_trust": 3,  # default for unspecified
    "open_ended_fund_company": 3,
}

# Domicile → currency/country risk score
DOMICILE_RISK_MAP = {
    "hong kong": 1,
    "singapore": 2,
    "luxembourg": 2,
    "ireland": 2,
    "uk": 2,
    "united kingdom": 2,
    "switzerland": 2,
    "germany": 2,
    "france": 2,
    "netherlands": 2,
    "usa": 2,
    "us": 2,
    "japan": 2,
    "australia": 2,
    "canada": 2,
    "china": 3,
    "india": 3,
    "brazil": 3,
    "emerging": 4,
    "frontier": 5,
    "offshore": 3,
}

# Complex product type → complexity score
COMPLEXITY_SCORE_MAP = {
    "non_complex": 1,
    "complex_bond": 3,
    "derivative_fund": 4,
    "synthetic_etf": 4,
    "futures_etf": 4,
    "structured": 4,
    "L&I": 5,
    "hedge_fund": 5,
    "security_token": 5,
}


def _parse_score_breakdown(record: dict) -> dict:
    """Parse score_breakdown JSON safely."""
    raw = record.get("score_breakdown", "{}")
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


def calculate_fund_risk_rating(fund: dict) -> dict:
    """Calculate risk rating for a single fund.

    Args:
        fund: dict from hk_funds table

    Returns: dict with overall_risk_score, risk_category, score_breakdown, etc.
    """
    fund_name = (fund.get("fund_name_en") or "").lower()
    fund_type = (fund.get("fund_type") or "unit_trust").lower()
    domicile = (fund.get("domicile") or "").lower()
    is_derivative = fund.get("is_derivative_product", False)
    is_complex = fund.get("is_complex_product", False)
    complex_type = fund.get("complex_product_type", "non_complex") or "non_complex"
    credit_rating = (fund.get("credit_rating") or "").lower()
    lockup_days = fund.get("lockup_period_days")

    scores = {}
    reasons = []

    # Factor 1: Complexity (weight: 0.25)
    if complex_type in COMPLEXITY_SCORE_MAP:
        complexity_score = COMPLEXITY_SCORE_MAP[complex_type]
    elif is_complex:
        complexity_score = 4
    elif is_derivative:
        complexity_score = 4
    else:
        complexity_score = 1
    scores["complexity"] = complexity_score
    if complexity_score >= 4:
        reasons.append(f"Complex product: {complex_type}")

    # Factor 2: Underlying asset risk (weight: 0.25)
    asset_score = ASSET_RISK_MAP.get(fund_type, 3)
    # Adjust for name keywords
    name_kw = {
        "bond": 2, "fixed income": 2, "money market": 1, "short term": 2,
        "balanced": 3, "mixed": 3,
        "equity": 4, "stock": 4, "growth": 4, "technology": 4,
        "commodity": 5, "crypto": 5, "bitcoin": 5,
        "high yield": 4, "emerging market": 4, "frontier": 5,
    }
    for kw, sc in name_kw.items():
        if kw in fund_name:
            asset_score = max(asset_score, sc)
    scores["underlying_risk"] = asset_score
    if asset_score >= 4:
        reasons.append(f"Higher-risk asset class: {fund_type}")

    # Factor 3: Leverage / derivatives (weight: 0.15)
    if complex_type == "L&I":
        leverage_score = 5
    elif is_derivative:
        leverage_score = 4
    elif "leveraged" in fund_name or "杠杆" in fund_name:
        leverage_score = 5
    elif "derivative" in fund_name or "衍生" in fund_name:
        leverage_score = 4
    elif "synthetic" in fund_name or "合成" in fund_name:
        leverage_score = 4
    else:
        leverage_score = 1
    scores["leverage"] = leverage_score
    if leverage_score >= 4:
        reasons.append("Significant leverage/derivatives exposure")

    # Factor 4: Liquidity (weight: 0.15)
    if lockup_days and lockup_days > 180:
        liquidity_score = 5
    elif lockup_days and lockup_days > 90:
        liquidity_score = 4
    elif lockup_days and lockup_days > 30:
        liquidity_score = 3
    elif lockup_days and lockup_days > 0:
        liquidity_score = 2
    elif complex_type in ("structured", "hedge_fund"):
        liquidity_score = 4
    elif fund_type in ("private_equity", "real_estate", "infrastructure"):
        liquidity_score = 5
    else:
        liquidity_score = 1  # Most UTMFs have daily/weekly dealing
    scores["liquidity"] = liquidity_score
    if liquidity_score >= 3:
        reasons.append(f"Reduced liquidity (lockup: {lockup_days}d)")

    # Factor 5: Credit quality (weight: 0.10)
    if "investment grade" in credit_rating or "aaa" in credit_rating or "aa" in credit_rating:
        credit_score = 1
    elif "bbb" in credit_rating or "a" in credit_rating:
        credit_score = 2
    elif "high yield" in credit_rating or "bb" in credit_rating or "b" in credit_rating:
        credit_score = 4
    elif "unrated" in credit_rating:
        credit_score = 4
    elif fund_type in ("money_market",):
        credit_score = 1
    elif fund_type in ("bond", "fixed_income", "short_term_bond"):
        credit_score = 3  # Default for bonds without explicit rating
    elif "high yield" in fund_name:
        credit_score = 4
    else:
        credit_score = 2  # Equity funds etc.
    scores["credit_quality"] = credit_score
    if credit_score >= 4:
        reasons.append(f"Higher credit risk: {credit_rating}")

    # Factor 6: Currency / country risk (weight: 0.10)
    country_score = DOMICILE_RISK_MAP.get(domicile, 3)
    # Adjust for name keywords
    if "emerging" in fund_name or "frontier" in fund_name:
        country_score = max(country_score, 4)
    if any(kw in fund_name for kw in ("china", "india", "brazil", "russia", "turkey")):
        country_score = max(country_score, 3)
    scores["currency_country"] = country_score
    if country_score >= 3:
        reasons.append(f"Domicile risk: {domicile or 'unknown'}")

    # Weighted total
    weighted_score = sum(
        FACTOR_WEIGHTS[factor] * scores[factor]
        for factor in FACTOR_WEIGHTS
    )
    weighted_score = round(weighted_score, 1)

    # Map to category
    if weighted_score <= 1.5:
        category = "Low"
    elif weighted_score <= 2.5:
        category = "Medium-Low"
    elif weighted_score <= 3.5:
        category = "Medium"
    elif weighted_score <= 4.0:
        category = "Medium-High"
    else:
        category = "High"

    return {
        "overall_risk_score": weighted_score,
        "risk_category": category,
        "methodology_version": METHODOLOGY_VERSION,
        "is_automated": True,
        "score_breakdown": json.dumps({
            "factor_scores": scores,
            "weights": FACTOR_WEIGHTS,
        }),
        "supporting_rationale": "; ".join(reasons) if reasons else "Standard risk profile",
    }


def rate_all_funds() -> dict:
    """Calculate and store risk ratings for all active funds. Returns summary."""
    conn = init_db()
    summary = {"total": 0, "rated": 0, "by_category": {}, "errors": []}

    try:
        funds_df = get_funds(conn, is_active=True, limit=10000)
        summary["total"] = len(funds_df)

        for _, row in funds_df.iterrows():
            fund = row.to_dict()
            try:
                rating = calculate_fund_risk_rating(fund)
                upsert_fund_risk_rating(conn, fund["id"], rating)

                # Also update KYP dimensions: complexity, risk_profile
                _sync_kyp_from_rating(conn, fund["id"], rating)

                summary["rated"] += 1
                cat = rating["risk_category"]
                summary["by_category"][cat] = summary["by_category"].get(cat, 0) + 1

            except Exception as e:
                summary["errors"].append(f"{fund.get('sfc_authorization_no', '?')}: {e}")

        logger.info(f"Risk rating complete: {summary['rated']} funds rated")
        logger.info(f"Distribution: {summary['by_category']}")

    finally:
        conn.close()

    return summary


def _sync_kyp_from_rating(conn, fund_id: int, rating: dict) -> None:
    """Update KYP dimensions based on risk rating results."""
    try:
        breakdown = _parse_score_breakdown(rating)
        factor_scores = breakdown.get("factor_scores", {})

        # Init all KYP dimensions for this fund
        init_kyp_dimensions(conn, fund_id)

        # Update complexity dimension
        complexity_score = factor_scores.get("complexity")
        if complexity_score is not None:
            upsert_kyp_dimension(conn, fund_id, "complexity", {
                "score": complexity_score,
                "assessment_status": "reviewed",
                "data_source": "risk_rating_engine",
                "assessment_date": datetime.now().strftime("%Y-%m-%d"),
                "findings": f"Automated complexity score: {complexity_score}/5",
            })

        # Update risk_profile dimension
        upsert_kyp_dimension(conn, fund_id, "risk_profile", {
            "score": int(rating.get("overall_risk_score", 3)),
            "assessment_status": "reviewed",
            "data_source": "risk_rating_engine",
            "assessment_date": datetime.now().strftime("%Y-%m-%d"),
            "findings": f"Risk category: {rating.get('risk_category')}; {rating.get('supporting_rationale', '')}"[:500],
        })

        # Mark derivative_class
        from hk_funds.storage import init_db as _init_db
        fund = conn.execute(
            "SELECT is_derivative_product FROM hk_funds WHERE id = ?", [fund_id]
        ).fetchone()
        if fund and fund[0]:
            upsert_kyp_dimension(conn, fund_id, "derivative_class", {
                "score": 4,
                "assessment_status": "reviewed",
                "data_source": "sfc_utmf",
                "assessment_date": datetime.now().strftime("%Y-%m-%d"),
                "findings": "Classified as derivative product by SFC",
            })
        elif fund:
            upsert_kyp_dimension(conn, fund_id, "derivative_class", {
                "score": 1,
                "assessment_status": "reviewed",
                "data_source": "sfc_utmf",
                "assessment_date": datetime.now().strftime("%Y-%m-%d"),
                "findings": "Not a derivative product per SFC §5.1A",
            })

    except Exception as e:
        logger.debug(f"KYP sync for fund {fund_id}: {e}")


def rate_single_fund(fund_id: int) -> dict:
    """Calculate and store risk rating for a specific fund."""
    conn = init_db()
    try:
        funds = conn.execute("SELECT * FROM hk_funds WHERE id = ?", [fund_id]).fetchone()
        if not funds:
            return {"error": f"Fund {fund_id} not found"}
        cols = [desc[0] for desc in conn.description]
        fund = dict(zip(cols, funds))
        rating = calculate_fund_risk_rating(fund)
        upsert_fund_risk_rating(conn, fund_id, rating)
        _sync_kyp_from_rating(conn, fund_id, rating)
        return rating
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    if "--fund-id" in sys.argv:
        idx = sys.argv.index("--fund-id")
        fid = int(sys.argv[idx + 1])
        print(rate_single_fund(fid))
    else:
        print(rate_all_funds())
