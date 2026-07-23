"""
Scoring — Pure functions for auction rush scoring, sector aggregation, and rankings.

No I/O. All stateless and testable.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def compute_rush_score(gap_pct: float, turnover_pct: float,
                        volume_rank_pctile: float) -> float:
    """Compute composite rush score 0-100 for a stock's auction.

    Args:
        gap_pct: (今开-昨收)/昨收 * 100 — auction price gap percentage
        turnover_pct: 竞价换手率 % — auction turnover rate
        volume_rank_pctile: 0-100 percentile of auction volume vs peers
    """
    from a_share_money_flow.config import (
        AUCTION_GAP_WEIGHT, AUCTION_TURNOVER_WEIGHT, AUCTION_VOLUME_WEIGHT,
    )

    # Gap: sigmoid-like normalization, centers at 0%, caps at ±10%
    gap_norm = _sigmoid_norm(gap_pct, center=0, scale=3) * 100

    # Turnover: percentile-like, higher is better
    turnover_norm = min(turnover_pct * 20, 100)  # 5% turnover → 100

    # Volume rank percentile is already 0-100
    volume_norm = volume_rank_pctile

    score = (
        gap_norm * AUCTION_GAP_WEIGHT +
        turnover_norm * AUCTION_TURNOVER_WEIGHT +
        volume_norm * AUCTION_VOLUME_WEIGHT
    )
    return round(min(score, 100), 1)


def _sigmoid_norm(x: float, center: float = 0, scale: float = 3) -> float:
    """Normalize a value to 0-1 range using sigmoid."""
    import math
    return 1.0 / (1.0 + math.exp(-(x - center) / scale))


def compute_percentile(values: List[float]) -> List[float]:
    """Convert raw values to percentile ranks 0-100."""
    if not values:
        return []
    n = len(values)
    # Sort and assign percentile
    indexed = sorted(enumerate(values), key=lambda x: x[1])
    result = [0.0] * n
    for rank, (orig_idx, _) in enumerate(indexed):
        result[orig_idx] = (rank / (n - 1)) * 100 if n > 1 else 50.0
    return result


def aggregate_auction_sectors(
    stocks: List[Dict[str, Any]],
    sector_field: str = "sector",
) -> List[Dict[str, Any]]:
    """Aggregate auction stocks into sector rankings.

    Each stock dict: {code, name, sector, gap_pct, volume, amount, turnover, rush_score}

    Returns list of sector dicts sorted by avg_rush_score DESC.
    """
    by_sector: Dict[str, List[Dict]] = {}
    for s in stocks:
        sec = s.get(sector_field, "其他")
        if not sec:
            sec = "其他"
        by_sector.setdefault(sec, []).append(s)

    result = []
    for sec, members in by_sector.items():
        scores = [m.get("rush_score", 0) for m in members]
        amounts = [m.get("amount", 0) or 0 for m in members]
        rush_count = sum(1 for sc in scores if sc >= 50)
        result.append({
            "sector": sec,
            "stock_count": len(members),
            "avg_rush_score": round(sum(scores) / len(scores), 1) if scores else 0,
            "max_rush_score": round(max(scores), 1) if scores else 0,
            "rush_stocks_count": rush_count,
            "total_auction_amount": sum(amounts),
            "top_stocks": [m["name"] for m in sorted(members, key=lambda x: x.get("rush_score", 0), reverse=True)[:3]],
        })

    result.sort(key=lambda x: x["avg_rush_score"], reverse=True)
    return result


def fund_flow_direction(inflow: float) -> str:
    """Classify fund flow direction."""
    if inflow > 0:
        return "inflow"
    elif inflow < 0:
        return "outflow"
    return "neutral"


def rank_by_field(items: List[Dict[str, Any]], field: str,
                   top_n: int = 20, reverse: bool = True) -> List[Dict[str, Any]]:
    """Sort a list of dicts by a numeric field and return top N."""
    sorted_items = sorted(items, key=lambda x: abs(x.get(field, 0)), reverse=reverse)
    return sorted_items[:top_n]
