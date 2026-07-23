"""
Pure computation functions — KOL rating algo, weight calc, heat score, tier mapping.

All functions are stateless and testable with mock data. No DB or network I/O.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional


# ── KOL Rating ────────────────────────────────────────────────

def compute_reach_score(followers: int, max_followers_in_cohort: int) -> float:
    """Reach score 0-30: log-normalized follower count."""
    if followers <= 0 or max_followers_in_cohort <= 0:
        return 0.0
    ratio = math.log10(followers + 1) / math.log10(max_followers_in_cohort + 1)
    return round(ratio * 30, 2)


def compute_engagement_score(avg_likes: float, avg_comments: float,
                              avg_shares: float, followers: int,
                              platform_avg_engagement_rate: float = 0.02) -> float:
    """Engagement score 0-25: avg interactions per post, normalized vs platform avg."""
    if followers <= 0:
        return 0.0
    engagement_rate = (avg_likes + avg_comments * 2 + avg_shares * 3) / followers
    if engagement_rate <= 0:
        return 0.0
    # Compare to platform average, cap at 3x
    ratio = min(engagement_rate / max(platform_avg_engagement_rate, 0.001), 3.0)
    return round((ratio / 3.0) * 25, 2)


def compute_consistency_score(posts_per_week: float, account_age_days: int) -> float:
    """Consistency score 0-20: posting regularity + account longevity."""
    freq_score = min(posts_per_week / 7.0, 1.0) * 10  # max at 7+ posts/week
    age_score = min(account_age_days / 730.0, 1.0) * 10  # max at 2+ years
    return round(freq_score + age_score, 2)


def compute_stock_relevance_score(stock_mention_ratio: float) -> float:
    """Stock relevance score 0-15: % of content mentioning specific stocks."""
    return round(min(stock_mention_ratio, 1.0) * 15, 2)


def compute_impact_score(mention_price_corr: float) -> float:
    """Impact score 0-10: correlation between mentions and subsequent price moves.

    Args:
        mention_price_corr: correlation coefficient (-1 to 1), initially 0.
    """
    return round(max(mention_price_corr, 0) * 10, 2)


def compute_kol_score(
    followers: int,
    max_followers: int,
    avg_likes: float,
    avg_comments: float,
    avg_shares: float,
    posts_per_week: float,
    account_age_days: int,
    stock_mention_ratio: float,
    mention_price_corr: float = 0.0,
    platform_avg_engagement_rate: float = 0.02,
) -> Dict[str, Any]:
    """Compute composite KOL score 0-100 from all dimensions.

    Returns dict with total score and per-dimension breakdown.
    """
    reach = compute_reach_score(followers, max_followers)
    engagement = compute_engagement_score(
        avg_likes, avg_comments, avg_shares, followers, platform_avg_engagement_rate
    )
    consistency = compute_consistency_score(posts_per_week, account_age_days)
    relevance = compute_stock_relevance_score(stock_mention_ratio)
    impact = compute_impact_score(mention_price_corr)

    total = round(reach + engagement + consistency + relevance + impact, 2)

    return {
        "total_score": min(total, 100.0),
        "reach": reach,
        "engagement": engagement,
        "consistency": consistency,
        "stock_relevance": relevance,
        "impact": impact,
    }


def assign_tier(score: float) -> str:
    """Map composite score to tier S/A/B/C/D."""
    if score >= 80:
        return "S"
    elif score >= 65:
        return "A"
    elif score >= 50:
        return "B"
    elif score >= 35:
        return "C"
    return "D"


# ── Weight Calculation ────────────────────────────────────────

def compute_kol_weight(tier: str, platform: str) -> float:
    """Compute base weight for a KOL in the thermometer.

    weight = tier_base_weight * platform_multiplier
    """
    from kol_thermometer.config import TIER_WEIGHTS, PLATFORM_MULTIPLIERS

    tier_w = TIER_WEIGHTS.get(tier, 0.1)
    plat_m = PLATFORM_MULTIPLIERS.get(platform, 0.5)
    return round(tier_w * plat_m, 4)


def recency_decay(posted_at: str, half_life_hours: float = 48.0) -> float:
    """Compute exponential decay factor based on post age.

    decay = e^(-λt) where λ = ln(2) / half_life_hours
    """
    try:
        if "T" in posted_at:
            post_dt = datetime.fromisoformat(posted_at.replace("Z", "+00:00"))
        else:
            post_dt = datetime.strptime(posted_at, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            post_dt = datetime.strptime(posted_at[:10], "%Y-%m-%d")
        except ValueError:
            return 0.5  # default if unparseable

    now = datetime.now(timezone.utc)
    if post_dt.tzinfo is None:
        post_dt = post_dt.replace(tzinfo=timezone.utc)

    hours_ago = (now - post_dt).total_seconds() / 3600.0
    if hours_ago < 0:
        hours_ago = 0

    lam = math.log(2) / half_life_hours
    return math.exp(-lam * hours_ago)


# ── Thermometer / Heat Score ──────────────────────────────────

def compute_raw_heat(mentions: List[Dict[str, Any]]) -> float:
    """Compute raw aggregated heat from a list of stock mentions.

    Each mention dict should have: kol_weight, sentiment_score, posted_at

    raw_heat = Σ(kol_weight * sentiment_score * recency_decay)
    """
    if not mentions:
        return 0.0

    total = 0.0
    for m in mentions:
        weight = m.get("kol_weight", 0.1)
        sentiment = m.get("sentiment_score", 0.0)
        posted = m.get("posted_at", "")
        decay = recency_decay(posted)
        total += weight * sentiment * decay

    return round(total, 4)


def normalize_heat(raw_heat: float) -> float:
    """Normalize raw heat to 0-100 scale using sigmoid.

    normalized = sigmoid(raw_heat) * 100
    sigmoid(x) = 1 / (1 + e^(-x * scale))
    """
    scale = 0.5  # controls steepness; lower = wider spread
    normalized = 1.0 / (1.0 + math.exp(-raw_heat * scale))
    return round(normalized * 100, 1)


def compute_heat_score(mentions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute full heat score for a stock from its mentions.

    Returns heat (0-100), raw_heat, mention_count, sentiment_bias,
    positive_count, negative_count, neutral_count, unique_kols.
    """
    if not mentions:
        return {
            "heat_score": 0.0,
            "raw_heat": 0.0,
            "mention_count": 0,
            "unique_kols": 0,
            "sentiment_bias": 0.0,
            "positive_count": 0,
            "negative_count": 0,
            "neutral_count": 0,
        }

    raw = compute_raw_heat(mentions)
    heat = normalize_heat(raw)

    sentiments = [m.get("sentiment_score", 0.0) for m in mentions]
    kol_ids = set(m.get("kol_id") for m in mentions if m.get("kol_id"))

    positive = sum(1 for s in sentiments if s > 0.15)
    negative = sum(1 for s in sentiments if s < -0.15)
    neutral = len(sentiments) - positive - negative

    return {
        "heat_score": heat,
        "raw_heat": raw,
        "mention_count": len(mentions),
        "unique_kols": len(kol_ids),
        "sentiment_bias": round(sum(sentiments) / len(sentiments), 3) if sentiments else 0.0,
        "positive_count": positive,
        "negative_count": negative,
        "neutral_count": neutral,
    }


def compute_momentum(current_heat: float, past_heats: List[float]) -> float:
    """Compute momentum: current heat vs average of past heats.

    Positive = heating up, Negative = cooling down.
    """
    if not past_heats:
        return 0.0
    avg = sum(past_heats) / len(past_heats)
    return round(current_heat - avg, 1)


# ── Tier decay ────────────────────────────────────────────────

def apply_tier_decay(current_tier: str, days_since_last_post: int) -> str:
    """Drop tier by one level per 30 days of inactivity."""
    from kol_thermometer.config import KOL_INACTIVE_DAYS_DECAY, KOL_INACTIVE_DAYS_REMOVE

    tiers = ["S", "A", "B", "C", "D"]
    if days_since_last_post >= KOL_INACTIVE_DAYS_REMOVE:
        return "REMOVE"

    num_decays = days_since_last_post // KOL_INACTIVE_DAYS_DECAY
    if num_decays <= 0:
        return current_tier

    try:
        idx = tiers.index(current_tier)
    except ValueError:
        return current_tier

    new_idx = min(idx + num_decays, len(tiers) - 1)
    return tiers[new_idx]
