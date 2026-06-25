"""
Generic template-driven rating engine for fund risk and manager DD.

Loads a template from the database, builds context for each target
(fund or manager), scores each factor via its config_json scoring type,
computes a weighted average, and maps to a category via thresholds.

Supported factor scoring types:
  - lookup: exact match on a field value → score_map
  - keyword: pattern matching against a text field
  - range: numeric bands (min/max → score)
  - benchmark_diff: fund metric vs benchmark equivalent
  - manager_aum_range: manager AUM size bands
  - manager_dd_score: internal control tier from manager_scoring
  - has_portfolio_manager: boolean check for named PM
  - dd_dimension: map DD dimension score to factor score
  - inverse_boolean: check for regulatory history presence
  - manual: always returns default_score (for user-filled data)
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("hk_funds.rating_engine")


# ═══════════════════════════════════════════════════════════════════
#  Template loading
# ═══════════════════════════════════════════════════════════════════


def load_template(conn, template_id: int) -> Optional[Dict[str, Any]]:
    """Load a full template with its factors from the database."""
    from hk_funds.storage import get_template, get_template_factors

    tmpl = get_template(conn, template_id)
    if tmpl is None:
        return None

    factors_df = get_template_factors(conn, template_id)
    factors = []
    for _, row in factors_df.iterrows():
        factor = {
            "id": int(row["id"]),
            "factor_key": row["factor_key"],
            "factor_label": row["factor_label"],
            "weight": float(row["weight"]),
            "ordinal": int(row.get("ordinal", 0)),
        }
        config_str = row.get("config_json", "{}")
        if config_str:
            factor["config"] = json.loads(config_str) if isinstance(config_str, str) else config_str
        else:
            factor["config"] = {}
        factors.append(factor)

    # Parse thresholds
    thresholds_str = tmpl.get("category_thresholds_json", "[]")
    if isinstance(thresholds_str, str):
        thresholds = json.loads(thresholds_str)
    else:
        thresholds = thresholds_str or []

    return {
        "id": tmpl["id"],
        "user_id": tmpl["user_id"],
        "name": tmpl["name"],
        "description": tmpl.get("description", ""),
        "template_type": tmpl["template_type"],
        "methodology_version": tmpl.get("methodology_version", "1.0"),
        "is_system": tmpl.get("is_system", False),
        "category_thresholds": thresholds,
        "factors": sorted(factors, key=lambda f: f["ordinal"]),
    }


# ═══════════════════════════════════════════════════════════════════
#  Factor scoring
# ═══════════════════════════════════════════════════════════════════


def compute_factor_score(config: dict, context: dict) -> int:
    """Compute a single factor's score (1-5) given its config and context.

    Args:
        config: The factor's config_json (type, field, score_map, bands, etc.)
        context: Context dict with fund/manager data fields.

    Returns:
        Integer score 1-5 (1 = lowest risk / best).
    """
    config_type = config.get("type", "manual")
    default = config.get("default_score", 3)

    handler = _SCORING_HANDLERS.get(config_type)
    if handler is None:
        logger.warning(f"Unknown factor type: {config_type}, using default={default}")
        return default

    try:
        score = handler(config, context)
        if score is None:
            return default
        return max(1, min(5, int(score)))
    except Exception:
        logger.exception(f"Error scoring factor type={config_type}")
        return default


def _score_lookup(config: dict, ctx: dict) -> Optional[int]:
    """Lookup: exact match on field value → score_map, with fallbacks."""
    field = config.get("field", "")
    value = _resolve_field(ctx, field)
    if value is None:
        value_str = ""
    else:
        value_str = str(value).lower().strip()

    # Direct lookup in score_map
    score_map = config.get("score_map", {})
    if value_str in score_map:
        return score_map[value_str]

    # Normalized lookup (handle underscores/spaces)
    for k, v in score_map.items():
        if k.replace("_", " ") == value_str or k == value_str.replace(" ", "_"):
            return v

    # Keyword adjustments on fund name or other fields
    keyword_adjustments = config.get("keyword_adjustments", {})
    text_fields = [str(ctx.get(f, "")).lower() for f in ["fund_name_en", "name", "fund_type", "description"]]
    combined = " ".join(text_fields)
    best_score = None
    for kw, score in keyword_adjustments.items():
        if kw.lower() in combined:
            if best_score is None or score > best_score:
                best_score = score
    if best_score is not None:
        return best_score

    # Fallback chain: check additional fields
    for fb in config.get("fallback", []):
        fb_field = fb.get("field", "")
        fb_value = _resolve_field(ctx, fb_field)
        if fb_value is None:
            continue
        if "value" in fb:
            expected = fb["value"]
            if fb_value == expected:
                return fb["score"]
        if "complex_types" in fb:
            if str(fb_value).lower() in [t.lower() for t in fb["complex_types"]]:
                return fb["score"]

    return None


def _score_keyword(config: dict, ctx: dict) -> Optional[int]:
    """Keyword: match patterns against a text field."""
    field = config.get("field", "fund_name_en")
    text = str(_resolve_field(ctx, field) or "").lower()
    patterns = config.get("patterns", {})
    best_score = None
    for pattern, score in patterns.items():
        if pattern.lower() in text:
            if best_score is None or score > best_score:
                best_score = score
    if best_score is not None:
        return best_score

    # Fund type fallbacks
    ft_fallbacks = config.get("fund_type_fallbacks", {})
    fund_type = str(ctx.get("fund_type", "")).lower()
    if fund_type in ft_fallbacks:
        return ft_fallbacks[fund_type]

    return None


def _score_range(config: dict, ctx: dict) -> Optional[int]:
    """Range: compare numeric value against bands (min/max → score)."""
    field = config.get("field", "")
    value = _resolve_field(ctx, field)
    if value is None:
        # Try fallback field
        fb_field = config.get("fallback_field", "")
        if fb_field:
            value = _resolve_field(ctx, fb_field)
    if value is None:
        return None

    try:
        num_val = float(value)
    except (ValueError, TypeError):
        return None

    bands = config.get("bands", [])
    for band in bands:
        band_min = band.get("min")
        band_max = band.get("max")
        if band_min is not None and num_val < band_min:
            continue
        if band_max is not None and num_val > band_max:
            continue
        score = band.get("score")
        if score is not None:
            return score

    # Type boost: add to score based on fund type/complex type
    type_boost = config.get("type_boost", {})
    for type_field in ["complex_product_type", "fund_type"]:
        type_val = str(ctx.get(type_field, "")).lower()
        if type_val in type_boost:
            boost = type_boost[type_val]
            # Find default band score and add boost
            return min(5, 2 + boost)  # approximate

    return None


def _score_benchmark_diff(config: dict, ctx: dict) -> Optional[int]:
    """Benchmark diff: compare fund metric vs benchmark."""
    metric = config.get("metric", "")
    fund_val = _resolve_field(ctx, metric)
    bm_val = _resolve_field(ctx, f"benchmark_{metric}")

    if fund_val is None:
        return None

    try:
        fund_num = float(fund_val)
    except (ValueError, TypeError):
        return None

    # If no benchmark data, use a neutral score
    if bm_val is None:
        return None

    try:
        bm_num = float(bm_val)
    except (ValueError, TypeError):
        return None

    diff = fund_num - bm_num

    bands = config.get("bands", [])
    for band in bands:
        min_diff = band.get("min_diff")
        max_diff = band.get("max_diff")
        if min_diff is not None and diff < min_diff:
            continue
        if max_diff is not None and diff > max_diff:
            continue
        return band.get("score")

    return None


def _score_manager_aum_range(config: dict, ctx: dict) -> Optional[int]:
    """Manager AUM range: compare AUM in HKD against size bands."""
    aum = _resolve_field(ctx, "manager_aum_hkd")
    if aum is None:
        aum = _resolve_field(ctx, "manager_aum")
    if aum is None:
        return None

    try:
        aum_num = float(aum)
    except (ValueError, TypeError):
        return None

    bands = config.get("bands", [])
    for band in bands:
        band_min = band.get("min")
        band_max = band.get("max")
        if band_min is not None and aum_num < band_min:
            continue
        if band_max is not None and aum_num > band_max:
            continue
        return band.get("score")

    return None


def _score_manager_dd_score(config: dict, ctx: dict) -> Optional[int]:
    """Manager DD score: map internal control tier to score."""
    tier = ctx.get("internal_control_tier", "")
    score_map = config.get("score_map", {})
    if tier in score_map:
        return score_map[tier]
    return None


def _score_has_portfolio_manager(config: dict, ctx: dict) -> Optional[int]:
    """Has portfolio manager: check if fund has a named PM."""
    field = config.get("field", "portfolio_manager_name")
    value = _resolve_field(ctx, field)
    if value and str(value).strip():
        return config.get("has_value_score", 2)
    return config.get("no_value_score", 5)


def _score_dd_dimension(config: dict, ctx: dict) -> Optional[int]:
    """DD dimension: map a specific DD dimension score to factor score."""
    dim = config.get("dimension", "")
    dd_scores = ctx.get("dd_scores", {})
    dim_score = dd_scores.get(dim)
    if dim_score is None:
        return None
    score_map = config.get("score_map", {})
    # Score map keys are strings (JSON serialization)
    return score_map.get(str(dim_score))


def _score_inverse_boolean(config: dict, ctx: dict) -> Optional[int]:
    """Inverse boolean with enforcement count bands + proxy for clean managers.

    Uses regulatory_count for managers with enforcement history:
      1 record → 2, 2-3 → 3, 4-5 → 4, 6+ → 5

    For managers without enforcement, uses proxy signals to estimate
    regulatory risk: short license tenure, young company, no website.
    """
    has_history = ctx.get("has_regulatory_history", False)
    if has_history:
        count = ctx.get("regulatory_count", 1)
        if count >= 6:
            return 5
        elif count >= 4:
            return 4
        elif count >= 2:
            return 3
        elif count >= 1:
            return 2
        return config.get("has_history_score", 5)

    # Proxy regulatory risk for managers without enforcement history.
    # A clean record is good, but a short track record = less certainty.
    lic_years = ctx.get("license_years")
    est_years = ctx.get("establishment_years")
    name_changes = ctx.get("name_history_count")

    risk_score = 1  # clean and well-established

    # Very short license tenure = limited regulatory track record
    if lic_years is not None and lic_years < 2:
        risk_score = 3
    elif lic_years is not None and lic_years < 4:
        risk_score = 2
    elif lic_years is not None and lic_years >= 10:
        risk_score = 1  # long clean track record = minimal risk

    # Frequent name changes = potential red flag (restructuring/evasion)
    if name_changes is not None and name_changes >= 3:
        risk_score = max(risk_score, 2)

    # Very young company with short license = compounding uncertainty
    if est_years is not None and est_years < 2 and risk_score < 3:
        risk_score = max(risk_score, 2)

    return risk_score


def _score_manager_years(config: dict, ctx: dict) -> Optional[int]:
    """Manager years: score based on license tenure (years since SFC license).

    Uses license_years from context (computed from license_effective_date).
    """
    license_years = ctx.get("license_years")
    if license_years is None:
        return None

    bands = config.get("bands", [
        {"min": 20, "score": 1},
        {"min": 15, "score": 2},
        {"min": 10, "score": 3},
        {"min": 5, "score": 4},
        {"max": 5, "score": 5},
    ])

    for band in bands:
        band_min = band.get("min")
        band_max = band.get("max")
        if band_min is not None and license_years < band_min:
            continue
        if band_max is not None and license_years >= band_max:
            continue
        return band.get("score")

    return None


def _score_profitable_product_ratio(config: dict, ctx: dict) -> Optional[int]:
    """Profitable product ratio: score based on % of funds with positive returns.

    Uses profitable_ratio from context (computed in _build_manager_context).
    """
    ratio = ctx.get("profitable_ratio")
    if ratio is None:
        return None

    bands = config.get("bands", [
        {"min": 0.8, "score": 1},
        {"min": 0.6, "score": 2},
        {"min": 0.4, "score": 3},
        {"min": 0.2, "score": 4},
        {"max": 0.2, "score": 5},
    ])

    for band in bands:
        band_min = band.get("min")
        band_max = band.get("max")
        if band_min is not None and ratio < band_min:
            continue
        if band_max is not None and ratio > band_max:
            continue
        return band.get("score")

    return None


def _score_scale_growth(config: dict, ctx: dict) -> Optional[int]:
    """Scale growth: score based on % of funds authorized in last 3 years.

    Uses scale_growth_ratio from context.
    Higher ratio = more recent fund launches = higher growth = better score.
    """
    ratio = ctx.get("scale_growth_ratio")
    if ratio is None:
        return None

    bands = config.get("bands", [
        {"min": 0.3, "score": 1},
        {"min": 0.2, "score": 2},
        {"min": 0.1, "score": 3},
        {"min": 0.05, "score": 4},
        {"max": 0.05, "score": 5},
    ])

    for band in bands:
        band_min = band.get("min")
        band_max = band.get("max")
        if band_min is not None and ratio < band_min:
            continue
        if band_max is not None and ratio > band_max:
            continue
        return band.get("score")

    return None


def _score_employee_count(config: dict, ctx: dict) -> Optional[int]:
    """Employee count: score based on estimated employee count."""
    count = ctx.get("estimated_employee_count")
    if count is None:
        return None

    bands = config.get("bands", [
        {"min": 500, "score": 1},
        {"min": 200, "score": 2},
        {"min": 50, "score": 3},
        {"min": 10, "score": 4},
        {"max": 10, "score": 5},
    ])

    for band in bands:
        band_min = band.get("min")
        band_max = band.get("max")
        if band_min is not None and count < band_min:
            continue
        if band_max is not None and count > band_max:
            continue
        return band.get("score")

    return None


def _score_paid_in_capital(config: dict, ctx: dict) -> Optional[int]:
    """Paid-in capital: score based on estimated capital in HKD."""
    capital = ctx.get("estimated_capital_hkd")
    if capital is None:
        return None

    bands = config.get("bands", [
        {"min": 100_000_000, "score": 1},
        {"min": 50_000_000, "score": 2},
        {"min": 10_000_000, "score": 3},
        {"min": 1_000_000, "score": 4},
        {"max": 1_000_000, "score": 5},
    ])

    for band in bands:
        band_min = band.get("min")
        band_max = band.get("max")
        if band_min is not None and capital < band_min:
            continue
        if band_max is not None and capital > band_max:
            continue
        return band.get("score")

    return None


def _score_manual(config: dict, ctx: dict) -> Optional[int]:
    """Manual: always returns None → default_score used."""
    return None


_SCORING_HANDLERS = {
    "lookup": _score_lookup,
    "keyword": _score_keyword,
    "range": _score_range,
    "benchmark_diff": _score_benchmark_diff,
    "manager_aum_range": _score_manager_aum_range,
    "manager_dd_score": _score_manager_dd_score,
    "has_portfolio_manager": _score_has_portfolio_manager,
    "dd_dimension": _score_dd_dimension,
    "inverse_boolean": _score_inverse_boolean,
    "manager_years": _score_manager_years,
    "profitable_product_ratio": _score_profitable_product_ratio,
    "employee_count": _score_employee_count,
    "paid_in_capital": _score_paid_in_capital,
    "scale_growth": _score_scale_growth,
    "manual": _score_manual,
}


# ═══════════════════════════════════════════════════════════════════
#  Context builders
# ═══════════════════════════════════════════════════════════════════


def _resolve_field(ctx: dict, field: str) -> Any:
    """Resolve a dotted or simple field name from context."""
    if not field:
        return None
    parts = field.split(".")
    value = ctx
    for part in parts:
        if isinstance(value, dict):
            value = value.get(part)
        else:
            return None
    return value


def _build_fund_context(conn, fund_row: dict) -> dict:
    """Build evaluation context for a single fund."""
    fund_id = fund_row["id"]
    ctx = dict(fund_row)

    # Add performance data
    try:
        perf = conn.execute(
            "SELECT * FROM hk_fund_performance WHERE fund_id = ?", [fund_id]
        ).fetchone()
        if perf:
            col_names = [desc[0] for desc in conn.description]
            for i, col in enumerate(col_names):
                if col not in ctx:
                    ctx[col] = perf[i]
    except Exception:
        pass

    # Add classification data
    try:
        cls_row = conn.execute(
            "SELECT * FROM hk_fund_classifications WHERE fund_id = ?", [fund_id]
        ).fetchone()
        if cls_row:
            col_names = [desc[0] for desc in conn.description]
            for i, col in enumerate(col_names):
                if col not in ctx:
                    ctx[col] = cls_row[i]
    except Exception:
        pass

    # Add manager AUM (via fund→manager link)
    try:
        mgr_row = conn.execute("""
            SELECT m.id, m.company_name_en, a.aum as manager_aum, a.aum_currency
            FROM hk_fund_manager_funds fmf
            JOIN hk_fund_managers m ON fmf.manager_id = m.id
            LEFT JOIN hk_fund_manager_aum a ON a.manager_id = m.id
            WHERE fmf.fund_id = ? AND fmf.is_primary = true
            LIMIT 1
        """, [fund_id]).fetchone()
        if mgr_row:
            ctx["manager_id"] = mgr_row[0]
            ctx["manager_name"] = mgr_row[1]
            ctx["manager_aum"] = mgr_row[2]
            ctx["manager_aum_currency"] = mgr_row[3]
            # Convert AUM to HKD approximately for size bands
            if mgr_row[2] is not None:
                currency = mgr_row[3] or "USD"
                ctx["manager_aum_hkd"] = _to_hkd(mgr_row[2], currency)
    except Exception:
        pass

    # Add internal control scoring
    if ctx.get("manager_id"):
        try:
            from hk_funds.manager_scoring import get_manager_scoring
            ic = get_manager_scoring(conn, int(ctx["manager_id"]))
            if ic.get("internal_control"):
                ctx["internal_control_tier"] = ic["internal_control"]["tier"]
                ctx["internal_control_score"] = ic["internal_control"]["score"]
        except Exception:
            pass

    return ctx


def _build_manager_context(conn, manager_row: dict) -> dict:
    """Build evaluation context for a single manager."""
    manager_id = manager_row["id"]
    ctx = dict(manager_row)

    # Add DD dimension scores
    try:
        dd_rows = conn.execute("""
            SELECT dd_dimension, score, assessment_status
            FROM hk_manager_dd WHERE manager_id = ?
        """, [manager_id]).fetchall()
        dd_scores = {}
        for row in dd_rows:
            dd_scores[row[0]] = row[1]
        ctx["dd_scores"] = dd_scores
        ctx["dd_count"] = len(dd_rows)
    except Exception:
        ctx["dd_scores"] = {}
        ctx["dd_count"] = 0

    # Add manager AUM
    try:
        aum_row = conn.execute(
            "SELECT aum, aum_currency FROM hk_fund_manager_aum WHERE manager_id = ?",
            [manager_id]
        ).fetchone()
        if aum_row:
            ctx["manager_aum"] = aum_row[0]
            ctx["manager_aum_currency"] = aum_row[1]
            if aum_row[0] is not None:
                ctx["manager_aum_hkd"] = _to_hkd(aum_row[0], aum_row[1] or "USD")
    except Exception:
        pass

    # Estimate AUM for managers without real AUM data (3616 managers).
    # Uses license combo, wiki, webb-id, and establishment years as size signals.
    if "manager_aum_hkd" not in ctx:
        try:
            ra1 = bool(ctx.get("regulated_activity_1"))
            ra4 = bool(ctx.get("regulated_activity_4"))
            ra9 = bool(ctx.get("regulated_activity_9"))
            ra_count = sum([ra1, ra4, ra9])
            has_wiki = bool(ctx.get("wiki_en_title") or ctx.get("wiki_zh_title"))
            has_webb = bool(ctx.get("webb_id"))
            est_years = ctx.get("establishment_years")

            if ra_count >= 3 and has_wiki:
                est_aum = 5_000_000_000   # 5B HKD → score 3
            elif ra_count >= 3:
                est_aum = 1_500_000_000   # 1.5B HKD → score 3
            elif ra_count >= 2 and has_wiki:
                est_aum = 2_000_000_000   # 2B HKD → score 3
            elif ra_count >= 2:
                est_aum = 600_000_000     # 600M HKD → score 4
            elif has_wiki:
                est_aum = 300_000_000     # 300M HKD → score 4
            elif has_webb and est_years and est_years >= 15:
                est_aum = 150_000_000     # 150M HKD → score 5
            elif has_webb:
                est_aum = 80_000_000      # 80M HKD → score 5
            else:
                est_aum = 30_000_000      # 30M HKD → score 5 (very small)

            ctx["manager_aum"] = est_aum
            ctx["manager_aum_hkd"] = est_aum
            ctx["manager_aum_currency"] = "HKD (estimated)"
        except Exception:
            pass

    # Check regulatory history
    try:
        reg_count = conn.execute(
            "SELECT COUNT(*) FROM hk_manager_regulatory_history WHERE manager_id = ?",
            [manager_id]
        ).fetchone()
        ctx["has_regulatory_history"] = (reg_count[0] > 0) if reg_count else False
        ctx["regulatory_count"] = reg_count[0] if reg_count else 0
    except Exception:
        ctx["has_regulatory_history"] = False
        ctx["regulatory_count"] = 0

    # Fund count per manager
    try:
        fc = conn.execute("""
            SELECT COUNT(*) FROM hk_fund_manager_funds WHERE manager_id = ?
        """, [manager_id]).fetchone()
        ctx["fund_count"] = fc[0] if fc else 0
    except Exception:
        ctx["fund_count"] = 0

    # If no direct fund links, estimate from AUM (for cumulative_fund_count scoring)
    if ctx["fund_count"] == 0:
        try:
            aum_hkd = ctx.get("manager_aum_hkd")
            if aum_hkd and aum_hkd >= 100_000_000_000:
                ctx["fund_count"] = 30
            elif aum_hkd and aum_hkd >= 10_000_000_000:
                ctx["fund_count"] = 15
            elif aum_hkd and aum_hkd >= 1_000_000_000:
                ctx["fund_count"] = 6
            elif aum_hkd and aum_hkd >= 500_000_000:
                ctx["fund_count"] = 3
            elif aum_hkd and aum_hkd >= 200_000_000:
                ctx["fund_count"] = 2
            elif aum_hkd and aum_hkd >= 50_000_000:
                ctx["fund_count"] = 1
        except Exception:
            pass

    # Profitable product ratio (from fund performance data)
    try:
        perf_rows = conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN COALESCE(p.return_1y_pct, p.return_6m_pct, p.return_3m_pct, 0) > 0 THEN 1 ELSE 0 END) as profitable
            FROM hk_fund_performance p
            JOIN hk_funds f ON f.id = p.fund_id
            WHERE f.fund_manager_id = ?
        """, [manager_id]).fetchone()
        if perf_rows and perf_rows[0] > 0:
            ctx["profitable_ratio"] = perf_rows[1] / perf_rows[0]
            ctx["funds_with_performance"] = perf_rows[0]
        else:
            # Proxy: estimate profitable ratio from available signals
            aum_hkd_proxy = ctx.get("manager_aum_hkd")
            fc_proxy = ctx.get("fund_count", 0)
            est_years_proxy = ctx.get("establishment_years")
            lic_years_proxy = ctx.get("license_years")
            has_wiki = ctx.get("has_wikipedia_en") or ctx.get("has_wikipedia_zh")
            has_web = bool(ctx.get("website"))

            if fc_proxy >= 50 or (aum_hkd_proxy and aum_hkd_proxy >= 100_000_000_000):
                ctx["profitable_ratio"] = 0.75
                ctx["funds_with_performance"] = max(fc_proxy, 1)
            elif fc_proxy >= 10 or (aum_hkd_proxy and aum_hkd_proxy >= 10_000_000_000):
                ctx["profitable_ratio"] = 0.55
                ctx["funds_with_performance"] = max(fc_proxy, 1)
            elif fc_proxy >= 3 or (aum_hkd_proxy and aum_hkd_proxy >= 1_000_000_000):
                ctx["profitable_ratio"] = 0.45  # Score 3
                ctx["funds_with_performance"] = max(fc_proxy, 1)
            elif fc_proxy >= 1 or (aum_hkd_proxy and aum_hkd_proxy >= 500_000_000):
                ctx["profitable_ratio"] = 0.35  # Score 4
                ctx["funds_with_performance"] = max(fc_proxy, 1)
            elif aum_hkd_proxy and aum_hkd_proxy >= 100_000_000:
                ctx["profitable_ratio"] = 0.25  # Score 4
                ctx["funds_with_performance"] = 1
            elif aum_hkd_proxy and aum_hkd_proxy >= 50_000_000:
                ctx["profitable_ratio"] = 0.18  # Score 5
                ctx["funds_with_performance"] = 1
            elif aum_hkd_proxy and aum_hkd_proxy >= 20_000_000:
                ctx["profitable_ratio"] = 0.12  # Score 5
                ctx["funds_with_performance"] = 1
            elif has_wiki and est_years_proxy and est_years_proxy >= 10:
                ctx["profitable_ratio"] = 0.15  # Score 5
                ctx["funds_with_performance"] = 1
            elif has_web or (lic_years_proxy and lic_years_proxy >= 5):
                ctx["profitable_ratio"] = 0.10  # Score 5
                ctx["funds_with_performance"] = 1
            else:
                ctx["profitable_ratio"] = 0.10  # Score 5 — truly unknown
    except Exception:
        pass

    # Scale growth: ratio of funds authorized in last 3 years
    try:
        growth_rows = conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN f.authorization_date >= CURRENT_DATE - INTERVAL '3 years' THEN 1 ELSE 0 END) as recent
            FROM hk_funds f
            WHERE f.fund_manager_id = ? AND f.authorization_date IS NOT NULL
        """, [manager_id]).fetchone()
        if growth_rows and growth_rows[0] > 0:
            ctx["scale_growth_ratio"] = growth_rows[1] / growth_rows[0]
            ctx["recent_fund_count"] = growth_rows[1]
        else:
            # Proxy: composite growth score from license tenure, AUM, name changes,
            # website, and multi-license status. Produces granular ratios for better
            # differentiation across 5 score bands.
            lic_years = ctx.get("license_years")
            aum_hkd = ctx.get("manager_aum_hkd")
            name_changes = ctx.get("name_history_count")
            has_web = bool(ctx.get("website"))
            ra1 = bool(ctx.get("regulated_activity_1"))
            ra4 = bool(ctx.get("regulated_activity_4"))
            ra9 = bool(ctx.get("regulated_activity_9"))

            # Base ratio from license tenure (newer = faster growth phase)
            if lic_years and lic_years < 2:
                ratio = 0.28
            elif lic_years and lic_years < 3:
                ratio = 0.22
            elif lic_years and lic_years < 5:
                ratio = 0.16
            elif lic_years and lic_years < 7:
                ratio = 0.11
            elif lic_years and lic_years < 10:
                ratio = 0.07
            elif lic_years and lic_years < 15:
                ratio = 0.04
            elif lic_years and lic_years >= 20:
                ratio = 0.02
            else:
                ratio = 0.03

            # AUM adjustment (larger = more resources for growth)
            if aum_hkd and aum_hkd >= 100_000_000_000:
                ratio += 0.10
            elif aum_hkd and aum_hkd >= 10_000_000_000:
                ratio += 0.06
            elif aum_hkd and aum_hkd >= 1_000_000_000:
                ratio += 0.03
            elif aum_hkd and aum_hkd >= 200_000_000:
                ratio += 0.01

            # Name changes (restructuring/rebranding = growth/expansion)
            if name_changes is not None and name_changes >= 3:
                ratio += 0.03
            elif name_changes is not None and name_changes >= 1:
                ratio += 0.01

            # Website (active online presence)
            if has_web:
                ratio += 0.02

            # Multi-license (more regulated activities = more business lines)
            ra_count = sum([1 for ra in [ra1, ra4, ra9] if ra])
            if ra_count >= 3:
                ratio += 0.03
            elif ra_count >= 2:
                ratio += 0.02
            elif ra_count >= 1:
                ratio += 0.01

            ctx["scale_growth_ratio"] = round(min(ratio, 0.45), 4)
    except Exception:
        pass

    # Employee count estimation (from AUM, fund_count, wiki, website)
    try:
        aum_hkd = ctx.get("manager_aum_hkd")
        fc = ctx.get("fund_count", 0)
        has_wiki = ctx.get("has_wikipedia_en") or ctx.get("has_wikipedia_zh")
        has_web = bool(ctx.get("website"))

        if fc >= 50:
            ctx["estimated_employee_count"] = 500
        elif fc >= 20:
            ctx["estimated_employee_count"] = 200
        elif aum_hkd and aum_hkd >= 100_000_000_000:  # 100B+ HKD → large firm
            ctx["estimated_employee_count"] = 300
        elif fc >= 5:
            ctx["estimated_employee_count"] = 50
        elif aum_hkd and aum_hkd >= 10_000_000_000:   # 10B+ HKD → medium-large
            ctx["estimated_employee_count"] = 100
        elif fc >= 1:
            ctx["estimated_employee_count"] = 10
        elif aum_hkd and aum_hkd >= 1_000_000_000:    # 1B+ HKD → medium
            ctx["estimated_employee_count"] = 30
        elif aum_hkd and aum_hkd >= 500_000_000:      # 500M+ HKD → small-medium
            ctx["estimated_employee_count"] = 15
        elif aum_hkd and aum_hkd >= 100_000_000:      # 100M+ HKD → small
            ctx["estimated_employee_count"] = 8
        elif has_wiki and has_web:
            ctx["estimated_employee_count"] = 20
        elif has_web:
            ctx["estimated_employee_count"] = 5
        else:
            ctx["estimated_employee_count"] = 2
    except Exception:
        pass

    # Paid-in capital estimation (from AUM, fund_count, wiki, establishment_years)
    # Order: check fund_count tiers first, then AUM tiers, then signals.
    # Avoids fc>=1 catching everyone before AUM checks.
    try:
        aum_hkd = ctx.get("manager_aum_hkd")
        fc = ctx.get("fund_count", 0)
        has_both_wiki = ctx.get("has_wikipedia_en") and ctx.get("has_wikipedia_zh")
        est_years = ctx.get("establishment_years")

        if fc >= 50:
            ctx["estimated_capital_hkd"] = 500_000_000
        elif fc >= 20:
            ctx["estimated_capital_hkd"] = 100_000_000
        elif aum_hkd and aum_hkd >= 100_000_000_000:
            ctx["estimated_capital_hkd"] = 200_000_000
        elif fc >= 5:
            ctx["estimated_capital_hkd"] = 30_000_000
        elif aum_hkd and aum_hkd >= 10_000_000_000:
            ctx["estimated_capital_hkd"] = 50_000_000
        elif has_both_wiki:
            ctx["estimated_capital_hkd"] = 50_000_000
        elif aum_hkd and aum_hkd >= 5_000_000_000:
            ctx["estimated_capital_hkd"] = 25_000_000
        elif aum_hkd and aum_hkd >= 2_000_000_000:
            ctx["estimated_capital_hkd"] = 15_000_000
        elif aum_hkd and aum_hkd >= 1_000_000_000:
            ctx["estimated_capital_hkd"] = 8_000_000
        elif fc >= 1 and aum_hkd and aum_hkd >= 500_000_000:
            ctx["estimated_capital_hkd"] = 5_000_000
        elif aum_hkd and aum_hkd >= 500_000_000:
            ctx["estimated_capital_hkd"] = 3_000_000
        elif fc >= 1:
            ctx["estimated_capital_hkd"] = 10_000_000
        elif aum_hkd and aum_hkd >= 200_000_000:
            ctx["estimated_capital_hkd"] = 2_000_000
        elif aum_hkd and aum_hkd >= 100_000_000:
            ctx["estimated_capital_hkd"] = 1_500_000
        elif est_years and est_years >= 10 and ctx.get("website"):
            ctx["estimated_capital_hkd"] = 5_000_000
        elif ctx.get("website"):
            ctx["estimated_capital_hkd"] = 2_000_000
        elif aum_hkd and aum_hkd >= 50_000_000:
            ctx["estimated_capital_hkd"] = 800_000
        else:
            ctx["estimated_capital_hkd"] = 500_000
    except Exception:
        pass

    # Add internal control scoring
    try:
        from hk_funds.manager_scoring import get_manager_scoring
        ic = get_manager_scoring(conn, manager_id)
        if ic.get("internal_control"):
            ctx["internal_control_tier"] = ic["internal_control"]["tier"]
            ctx["internal_control_score"] = ic["internal_control"]["score"]
    except Exception:
        pass

    # Compute years since establishment (from webb-site inc_date)
    from datetime import date
    if ctx.get("inc_date"):
        try:
            inc = ctx["inc_date"]
            if isinstance(inc, str):
                inc = date.fromisoformat(inc)
            ctx["establishment_years"] = (date.today() - inc).days / 365.25
        except Exception:
            pass

    # Compute license tenure years (from webb-site license_effective_date)
    if ctx.get("license_effective_date"):
        try:
            le = ctx["license_effective_date"]
            if isinstance(le, str):
                le = date.fromisoformat(le)
            ctx["license_years"] = (date.today() - le).days / 365.25
        except Exception:
            pass

    # License tenure / company age ratio (0-1, higher = license older relative to company)
    if ctx.get("establishment_years") and ctx.get("license_years"):
        try:
            ctx["license_tenure_ratio"] = min(
                ctx["license_years"] / max(ctx["establishment_years"], 0.1), 1.0
            )
        except Exception:
            pass

    # Name change frequency (from webb-site name_history_count)
    if ctx.get("name_history_count") is not None and ctx.get("establishment_years"):
        try:
            ctx["name_changes_per_year"] = float(ctx["name_history_count"]) / max(ctx["establishment_years"], 0.1)
        except Exception:
            pass

    # Proxy scores for manual factors (distribution_channels, investor_composition, investor_services)
    # Based on available data: AUM, fund count, website, wiki, license types, company age
    _compute_proxy_scores(ctx)

    # Composite institutional reputation score (wiki + website + AUM + fund count + age)
    has_en = bool(ctx.get("wiki_en_title"))
    has_zh = bool(ctx.get("wiki_zh_title"))
    ctx["has_wikipedia_en"] = has_en
    ctx["has_wikipedia_zh"] = has_zh

    # Start with wiki presence base
    if has_en and has_zh:
        rep_score = 2.0
    elif has_en or has_zh:
        rep_score = 1.0
    else:
        rep_score = 0.0

    # Add signals
    aum_hkd = ctx.get("manager_aum_hkd")
    fund_count = ctx.get("fund_count", 0)
    est_years = ctx.get("establishment_years")
    has_web = bool(ctx.get("website"))
    has_webb = bool(ctx.get("webb_id"))

    if has_web and (has_en or has_zh):
        rep_score += 1.0  # website + wiki = transparent institution
    elif has_web:
        rep_score += 0.5  # website alone = some institutional presence

    if fund_count >= 20 or (aum_hkd and aum_hkd >= 10_000_000_000):
        rep_score += 1.0  # large multi-fund manager
    elif fund_count >= 5 or (aum_hkd and aum_hkd >= 1_000_000_000):
        rep_score += 0.5  # mid-size manager

    if est_years and est_years >= 20:
        rep_score += 0.5  # long-established

    if has_webb:
        rep_score += 0.5  # followed by Webb-Site

    ctx["wiki_presence_score"] = round(min(rep_score, 5), 2)

    return ctx


def _to_hkd(aum: float, currency: str) -> float:
    """Convert AUM to approximate HKD for size band comparison."""
    rates = {"USD": 7.83, "HKD": 1.0, "CNY": 1.08, "EUR": 8.45, "GBP": 9.95, "JPY": 0.052, "SGD": 5.82}
    return aum * rates.get(currency.upper(), 7.83)


def _compute_proxy_scores(ctx: dict):
    """Compute proxy scores for manual-only rating factors from available data.

    Populates ctx with:
      - distribution_channels_score (0-5+)
      - investor_composition_score (0-4+)
      - investor_services_score (0-4+)
    """
    try:
        aum_hkd = ctx.get("manager_aum_hkd")
        fund_count = ctx.get("fund_count", 0)
        has_web = bool(ctx.get("website"))
        has_wiki_en = bool(ctx.get("has_wikipedia_en"))
        has_wiki_zh = bool(ctx.get("has_wikipedia_zh"))
        has_wiki = has_wiki_en or has_wiki_zh
        est_years = ctx.get("establishment_years")
        lic_years = ctx.get("license_years")
        ra1 = bool(ctx.get("regulated_activity_1"))
        ra4 = bool(ctx.get("regulated_activity_4"))

        # ---- Distribution Channels Score (0-5+) ----
        # Higher score = more/better distribution channels
        dc_score = 0.0
        if has_web:
            dc_score += 1.0
        # AUM tiers: larger → more distribution channels
        if aum_hkd and aum_hkd >= 100_000_000_000:   # 100B+
            dc_score += 2.0
        elif aum_hkd and aum_hkd >= 10_000_000_000:  # 10B+
            dc_score += 1.5
        elif aum_hkd and aum_hkd >= 1_000_000_000:   # 1B+
            dc_score += 1.0
        elif aum_hkd and aum_hkd >= 100_000_000:     # 100M+
            dc_score += 0.5
        # Fund count: more funds → more distribution partners
        if fund_count >= 20:
            dc_score += 1.5
        elif fund_count >= 10:
            dc_score += 1.0
        elif fund_count >= 3:
            dc_score += 0.5
        elif fund_count >= 1:
            dc_score += 0.25
        # Wiki + license indicators
        if has_wiki:
            dc_score += 0.5
        if ra1 or ra4:  # Additional regulated activities
            dc_score += 0.5
        if est_years and est_years >= 20:
            dc_score += 0.5
        elif est_years and est_years >= 10:
            dc_score += 0.25
        ctx["distribution_channels_score"] = min(dc_score, 5)

        # ---- Investor Composition Score (0-4+) ----
        # Higher score = more institutional investors
        ic_score = 0.0
        if aum_hkd and aum_hkd >= 100_000_000_000:
            ic_score += 2.5
        elif aum_hkd and aum_hkd >= 10_000_000_000:
            ic_score += 2.0
        elif aum_hkd and aum_hkd >= 1_000_000_000:
            ic_score += 1.5
        elif aum_hkd and aum_hkd >= 500_000_000:
            ic_score += 1.0
        elif aum_hkd and aum_hkd >= 100_000_000:
            ic_score += 0.5
        elif aum_hkd and aum_hkd:  # Any AUM
            ic_score += 0.25
        if fund_count >= 20:
            ic_score += 1.0
        elif fund_count >= 5:
            ic_score += 0.5
        elif fund_count >= 1:
            ic_score += 0.25
        if has_wiki_en and has_wiki_zh:
            ic_score += 0.5
        elif has_wiki:
            ic_score += 0.25
        if est_years and est_years >= 30:
            ic_score += 0.5
        elif est_years and est_years >= 15:
            ic_score += 0.25
        if ra1 and ra4:  # Multiple licenses → broader client base
            ic_score += 0.25
        ctx["investor_composition_score"] = round(min(ic_score, 4), 2)

        # ---- Investor Services Score (0-4+) ----
        # Higher score = better investor services
        is_score = 0.0
        if has_web:
            is_score += 1.0
        if aum_hkd and aum_hkd >= 10_000_000_000:
            is_score += 1.5
        elif aum_hkd and aum_hkd >= 1_000_000_000:
            is_score += 1.0
        elif aum_hkd and aum_hkd >= 100_000_000:
            is_score += 0.5
        elif aum_hkd and aum_hkd:  # Any AUM
            is_score += 0.25
        if fund_count >= 20:
            is_score += 1.0
        elif fund_count >= 5:
            is_score += 0.5
        elif fund_count >= 1:
            is_score += 0.25
        if has_wiki:
            is_score += 0.5
        if lic_years and lic_years >= 15:
            is_score += 0.5
        elif lic_years and lic_years >= 8:
            is_score += 0.25
        if ra1 or ra4:
            is_score += 0.25
        ctx["investor_services_score"] = round(min(is_score, 4), 2)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════
#  Rating computation
# ═══════════════════════════════════════════════════════════════════


def compute_category(score: float, thresholds: List[Dict]) -> str:
    """Map a weighted score to category label using thresholds.

    Thresholds are ordered [{max: X, label: "..."}, ...] in ascending order.
    """
    for t in thresholds:
        if score <= t.get("max", 99):
            return t.get("label", "Unknown")
    return thresholds[-1].get("label", "Unknown") if thresholds else "Unknown"


def compute_weighted_rating(template: dict, context: dict) -> dict:
    """Compute a weighted rating for a single target against a template.

    Args:
        template: Loaded template dict with 'factors' list and 'category_thresholds'.
        context: Evaluation context dict built by _build_fund_context or _build_manager_context.

    Returns:
        {overall_score, category, factor_scores: [{key, label, score, weight, weighted}, ...]}
    """
    factors = template["factors"]
    total_weight = 0.0
    weighted_sum = 0.0
    factor_scores = []

    for factor in factors:
        weight = float(factor["weight"])
        config = factor.get("config", {})
        score = compute_factor_score(config, context)
        weighted = score * weight
        weighted_sum += weighted
        total_weight += weight

        factor_scores.append({
            "factor_key": factor["factor_key"],
            "factor_label": factor["factor_label"],
            "score": score,
            "weight": weight,
            "weighted": round(weighted, 4),
        })

    # Normalize if weights don't sum to 1.0
    if total_weight > 0 and abs(total_weight - 1.0) > 0.001:
        overall = weighted_sum / total_weight
    else:
        overall = weighted_sum

    overall = round(overall, 2)
    category = compute_category(overall, template["category_thresholds"])

    return {
        "overall_score": overall,
        "category": category,
        "factor_scores": factor_scores,
        "weights_sum": round(total_weight, 4),
    }


# ═══════════════════════════════════════════════════════════════════
#  Batch computation
# ═══════════════════════════════════════════════════════════════════


def compute_ratings_batch(
    conn, template_id: int, user_id: str
) -> Dict[str, Any]:
    """Compute ratings for all funds using a fund_risk template.

    Args:
        conn: Database connection.
        template_id: Template ID to use.
        user_id: User ID to associate ratings with.

    Returns:
        {template_id, template_name, total_rated, distribution: [...], errors: [...]}
    """
    from hk_funds.storage import upsert_user_rating, create_compute_job, \
        update_compute_job_progress, finish_compute_job

    template = load_template(conn, template_id)
    if template is None:
        return {"error": f"Template {template_id} not found"}
    if template["template_type"] != "fund_risk":
        return {"error": f"Template {template_id} is not a fund_risk template"}

    # Get all active funds
    funds = conn.execute("""
        SELECT * FROM hk_funds
        WHERE is_active = true
        ORDER BY id
    """).fetchall()
    col_names = [desc[0] for desc in conn.description]

    job_id = create_compute_job(conn, template_id, user_id, 'fund', len(funds))

    results = []
    errors = []
    distribution = {}

    for idx, fund_row in enumerate(funds):
        fund_dict = dict(zip(col_names, fund_row))
        fund_id = fund_dict["id"]
        try:
            ctx = _build_fund_context(conn, fund_dict)
            rating = compute_weighted_rating(template, ctx)

            # Mark previous ratings as not latest
            conn.execute("""
                UPDATE hk_user_ratings SET is_latest = false
                WHERE template_id = ? AND user_id = ?
                  AND target_type = 'fund' AND target_id = ?
            """, [template_id, user_id, fund_id])

            # Store new rating
            upsert_user_rating(conn, {
                "template_id": template_id,
                "user_id": user_id,
                "target_type": "fund",
                "target_id": fund_id,
                "overall_score": rating["overall_score"],
                "category": rating["category"],
                "factor_scores_json": json.dumps(rating["factor_scores"]),
                "methodology_version": template["methodology_version"],
                "computed_at": "now()",
                "is_latest": True,
            })

            results.append({
                "target_id": fund_id,
                "target_name": fund_dict.get("fund_name_en", ""),
                "overall_score": rating["overall_score"],
                "category": rating["category"],
            })

            dist_key = rating["category"]
            distribution[dist_key] = distribution.get(dist_key, 0) + 1

        except Exception as e:
            logger.exception(f"Error rating fund {fund_id}")
            errors.append({"target_id": fund_id, "error": str(e)})

        # Update progress every 50 targets
        if (idx + 1) % 50 == 0:
            update_compute_job_progress(conn, job_id, idx + 1, len(errors))

    finish_compute_job(conn, job_id, 'failed' if len(errors) == len(funds) else 'done')

    return {
        "job_id": job_id,
        "template_id": template_id,
        "template_name": template["name"],
        "total_rated": len(results),
        "distribution": [
            {"category": k, "count": v}
            for k, v in sorted(distribution.items())
        ],
        "errors": errors[:20],
    }


def compute_ratings_batch_by_ids(
    conn, template_id: int, user_id: str, fund_ids: List[int]
) -> Dict[str, Any]:
    """Compute ratings for specific funds only."""
    from hk_funds.storage import upsert_user_rating

    template = load_template(conn, template_id)
    if template is None:
        return {"error": f"Template {template_id} not found"}

    results = []
    for fund_id in fund_ids:
        fund_row = conn.execute(
            "SELECT * FROM hk_funds WHERE id = ?", [fund_id]
        ).fetchone()
        if fund_row is None:
            continue
        col_names = [desc[0] for desc in conn.description]
        fund_dict = dict(zip(col_names, fund_row))
        try:
            ctx = _build_fund_context(conn, fund_dict)
            rating = compute_weighted_rating(template, ctx)

            conn.execute("""
                UPDATE hk_user_ratings SET is_latest = false
                WHERE template_id = ? AND user_id = ?
                  AND target_type = 'fund' AND target_id = ?
            """, [template_id, user_id, fund_id])

            upsert_user_rating(conn, {
                "template_id": template_id,
                "user_id": user_id,
                "target_type": "fund",
                "target_id": fund_id,
                "overall_score": rating["overall_score"],
                "category": rating["category"],
                "factor_scores_json": json.dumps(rating["factor_scores"]),
                "methodology_version": template["methodology_version"],
                "computed_at": "now()",
                "is_latest": True,
            })

            results.append({
                "target_id": fund_id,
                "target_name": fund_dict[1],  # fund_name_en
                "overall_score": rating["overall_score"],
                "category": rating["category"],
            })
        except Exception as e:
            logger.exception(f"Error rating fund {fund_id}")
            results.append({"target_id": fund_id, "error": str(e)})

    return {
        "template_id": template_id,
        "template_name": template["name"],
        "rated": len([r for r in results if "error" not in r]),
        "results": results,
    }


def compute_manager_dd_batch(
    conn, template_id: int, user_id: str
) -> Dict[str, Any]:
    """Compute ratings for all managers using a manager_dd template.

    Supports both:
      - DD_10D_TEMPLATE: pass-count of DD dimensions → tier
      - BROKER_18I_TEMPLATE: 18 individually weighted factors → category
    """
    from hk_funds.storage import upsert_user_rating, create_compute_job, \
        update_compute_job_progress, finish_compute_job

    template = load_template(conn, template_id)
    if template is None:
        return {"error": f"Template {template_id} not found"}
    if template["template_type"] != "manager_dd":
        return {"error": f"Template {template_id} is not a manager_dd template"}

    # Check if this is the pass-count style (DD_10D) or factor-weighted (18I)
    is_pass_count = _is_pass_count_template(template)

    # Mark all existing ratings as not latest for this template+user+type
    conn.execute("""
        UPDATE hk_user_ratings SET is_latest = false
        WHERE template_id = ? AND user_id = ? AND target_type = 'manager'
    """, [template_id, user_id])

    # Get all Type 9 (asset management) managers — the target universe for DD rating
    managers = conn.execute("""
        SELECT * FROM hk_fund_managers
        WHERE license_status = 'active' AND regulated_activity_9 = true
        ORDER BY id
    """).fetchall()
    col_names = [desc[0] for desc in conn.description]

    job_id = create_compute_job(conn, template_id, user_id, 'manager', len(managers))

    results = []
    errors = []
    distribution = {}

    for idx, mgr_row in enumerate(managers):
        mgr_dict = dict(zip(col_names, mgr_row))
        manager_id = mgr_dict["id"]
        try:
            ctx = _build_manager_context(conn, mgr_dict)

            if is_pass_count:
                rating = _compute_pass_count_rating(template, ctx)
            else:
                rating = compute_weighted_rating(template, ctx)

            # Mark previous as not latest
            conn.execute("""
                UPDATE hk_user_ratings SET is_latest = false
                WHERE template_id = ? AND user_id = ?
                  AND target_type = 'manager' AND target_id = ?
            """, [template_id, user_id, manager_id])

            upsert_user_rating(conn, {
                "template_id": template_id,
                "user_id": user_id,
                "target_type": "manager",
                "target_id": manager_id,
                "overall_score": rating["overall_score"],
                "category": rating["category"],
                "factor_scores_json": json.dumps(rating.get("factor_scores", [])),
                "methodology_version": template["methodology_version"],
                "computed_at": "now()",
                "is_latest": True,
            })

            results.append({
                "target_id": manager_id,
                "target_name": mgr_dict.get("company_name_en", ""),
                "overall_score": rating["overall_score"],
                "category": rating["category"],
            })

            dist_key = rating["category"]
            distribution[dist_key] = distribution.get(dist_key, 0) + 1

        except Exception as e:
            logger.exception(f"Error rating manager {manager_id}")
            errors.append({"target_id": manager_id, "error": str(e)})

        # Update progress every 50 targets
        if (idx + 1) % 50 == 0:
            update_compute_job_progress(conn, job_id, idx + 1, len(errors))

    finish_compute_job(conn, job_id, 'failed' if len(errors) == len(managers) else 'done')

    return {
        "job_id": job_id,
        "template_id": template_id,
        "template_name": template["name"],
        "total_rated": len(results),
        "distribution": [
            {"category": k, "count": v}
            for k, v in sorted(distribution.items())
        ],
        "errors": errors[:20],
    }


def _is_pass_count_template(template: dict) -> bool:
    """Check if this is a pass-count style DD template (no individual factors)."""
    factors = template.get("factors", [])
    if len(factors) == 1 and factors[0]["factor_key"] == "_dd_config":
        return True
    # Also check: if there are no real weighted factors (all _dd_config)
    real_factors = [f for f in factors if f["factor_key"] != "_dd_config"]
    return len(real_factors) == 0


def _compute_pass_count_rating(template: dict, ctx: dict) -> dict:
    """Compute rating for pass-count DD template (DD_10D).

    Reads dd_config from the _dd_config factor.
    """
    dd_factor = None
    for f in template["factors"]:
        if f["factor_key"] == "_dd_config":
            dd_factor = f
            break

    if dd_factor is None:
        return {"overall_score": 3.0, "category": "Average", "factor_scores": []}

    config = dd_factor.get("config", {})
    if isinstance(config, str):
        config = json.loads(config)

    pass_score = config.get("pass_score", 3)
    pass_statuses = set(config.get("pass_statuses", ["reviewed", "approved"]))
    dimensions = config.get("dimensions", [])
    tiers = config.get("tiers", [
        [9, 10, "Strong", 1], [7, 8, "Sufficient", 2],
        [5, 6, "Average", 3], [3, 4, "Limited", 4], [0, 2, "Lacking", 5],
    ])

    dd_scores = ctx.get("dd_scores", {})
    passed = 0
    total = len(dimensions)
    details = []

    for dim in dimensions:
        dim_score = dd_scores.get(dim)
        dim_status = dd_scores.get(f"{dim}_status", "pending")
        if dim_score is not None and dim_score >= pass_score:
            passed += 1
            details.append({"dimension": dim, "pass": True, "score": dim_score})
        else:
            details.append({"dimension": dim, "pass": False, "score": dim_score or 0})

    # Map to tier
    tier_name = "Lacking"
    tier_score = 5
    for lo, hi, name, sc in tiers:
        if lo <= passed <= hi:
            tier_name = name
            tier_score = sc
            break

    return {
        "overall_score": float(tier_score),
        "category": tier_name,
        "factor_scores": details,
        "passed_count": passed,
        "total_dimensions": total,
    }


def compute_single_rating(
    conn, template_id: int, user_id: str,
    target_type: str, target_id: int
) -> Optional[Dict[str, Any]]:
    """Compute and store rating for a single target."""
    from hk_funds.storage import upsert_user_rating

    template = load_template(conn, template_id)
    if template is None:
        return None

    if target_type == "fund":
        fund_row = conn.execute(
            "SELECT * FROM hk_funds WHERE id = ?", [target_id]
        ).fetchone()
        if fund_row is None:
            return None
        col_names = [desc[0] for desc in conn.description]
        fund_dict = dict(zip(col_names, fund_row))
        ctx = _build_fund_context(conn, fund_dict)
        rating = compute_weighted_rating(template, ctx)
    elif target_type == "manager":
        mgr_row = conn.execute(
            "SELECT * FROM hk_fund_managers WHERE id = ?", [target_id]
        ).fetchone()
        if mgr_row is None:
            return None
        col_names = [desc[0] for desc in conn.description]
        mgr_dict = dict(zip(col_names, mgr_row))
        ctx = _build_manager_context(conn, mgr_dict)
        if _is_pass_count_template(template):
            rating = _compute_pass_count_rating(template, ctx)
        else:
            rating = compute_weighted_rating(template, ctx)
    else:
        return None

    # Mark previous as not latest
    conn.execute("""
        UPDATE hk_user_ratings SET is_latest = false
        WHERE template_id = ? AND user_id = ?
          AND target_type = ? AND target_id = ?
    """, [template_id, user_id, target_type, target_id])

    upsert_user_rating(conn, {
        "template_id": template_id,
        "user_id": user_id,
        "target_type": target_type,
        "target_id": target_id,
        "overall_score": rating["overall_score"],
        "category": rating["category"],
        "factor_scores_json": json.dumps(rating.get("factor_scores", [])),
        "methodology_version": template["methodology_version"],
        "computed_at": "now()",
        "is_latest": True,
    })

    return rating
