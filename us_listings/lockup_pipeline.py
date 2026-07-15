"""
IPO Lockup Expiry pipeline — tracks when insider lockup periods expire.

Standard IPO lockup: 180 days from listing date (underwriter standard).
Direct listings: Often no lockup or shorter window (90 days).
SPACs: PIPE investors typically have shorter lockups (varies by deal).

Calculates expected lockup expiry dates from new_listings table
and enriches with market impact estimates.

Usage:
    python -m us_listings.lockup_pipeline --init
"""

from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from us_listings.storage import (
    init_db, upsert_lockup_expiry, get_lockup_expiry,
    log_fetch_start, log_fetch_end,
)

logger = logging.getLogger("us_listings.lockup_pipeline")

# Default lockup periods by listing type (days)
DEFAULT_LOCKUP_PERIODS = {
    "IPO": 180,
    "Direct Listing": 90,
    "SPAC": 180,
    "Upcoming": None,  # Skip
}

# Pre-IPO / major lockup dates worth tracking
KNOWN_LOCKUP_EVENTS = {
    # Major recent IPOs with large lockup floats
}


def compute_lockup_expiry(conn) -> List[Dict[str, Any]]:
    """Compute lockup expiry dates from new_listings table.

    For IPOs within the last 180 days, compute expected unlock date.
    """
    today = datetime.now().strftime("%Y-%m-%d")

    # Get recent IPOs/SPACs/direct listings
    cutoff = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
    df = conn.execute("""
        SELECT ticker, company_name, listing_date, listing_type, offer_price, shares_offered
        FROM new_listings
        WHERE listing_date >= ?
          AND listing_type IN ('IPO', 'Direct Listing', 'SPAC')
        ORDER BY listing_date DESC
    """, [cutoff]).df()

    records = []
    for _, row in df.iterrows():
        ticker = row["ticker"]
        listing_type = row["listing_type"]
        listing_date_str = str(row["listing_date"])[:10]
        try:
            listing_date = datetime.strptime(listing_date_str, "%Y-%m-%d")
        except ValueError:
            continue

        lockup_days = DEFAULT_LOCKUP_PERIODS.get(listing_type)
        if not lockup_days:
            continue

        lockup_end = listing_date + timedelta(days=lockup_days)
        lockup_end_str = lockup_end.strftime("%Y-%m-%d")

        # Days until lockup expiry
        days_remaining = (lockup_end.date() - datetime.now().date()).days

        # Estimate shares unlocking
        # Typically 80-90% of float is locked up at IPO
        shares_offered = row.get("shares_offered")
        if shares_offered and shares_offered > 0:
            # Rough estimate: float beyond the 15% greenshoe is locked
            float_pct = 0.85  # 85% typically locked at IPO
            estimated_shares_unlocking = int(float(shares_offered) * float_pct)
        else:
            estimated_shares_unlocking = None

        # Estimate value at current price (will be enriched later)
        offer_price = row.get("offer_price")

        records.append({
            "ticker": ticker,
            "company_name": row.get("company_name", ""),
            "listing_date": listing_date_str,
            "listing_type": listing_type,
            "lockup_end_date": lockup_end_str,
            "lockup_period_days": lockup_days,
            "days_remaining": days_remaining,
            "estimated_shares_unlocking": estimated_shares_unlocking,
            "estimated_value": float(estimated_shares_unlocking * offer_price) if estimated_shares_unlocking and offer_price else None,
            "status": "active" if days_remaining >= 0 else "expired",
        })

    # Sort by soonest expiry first
    records.sort(key=lambda r: r["days_remaining"] if r["days_remaining"] is not None else 999)

    logger.info(f"Computed {len(records)} lockup expiry dates")
    return records


def fetch_lockup_daily() -> Dict[str, Any]:
    """Compute and store IPO lockup expiry data."""
    conn = init_db()
    summary = {"stored": 0, "errors": []}
    today = datetime.now().strftime("%Y-%m-%d")

    log_id = log_fetch_start(conn, today, source="lockup_expiry")

    try:
        records = compute_lockup_expiry(conn)
        if records:
            summary["stored"] = upsert_lockup_expiry(conn, records)
            logger.info(f"Stored {summary['stored']} lockup expiry records")

            # Log upcoming expiries (within 14 days)
            upcoming = [r for r in records if 0 <= r.get("days_remaining", 999) <= 14]
            if upcoming:
                tickers = ", ".join(f"{r['ticker']}({r['days_remaining']}d)" for r in upcoming[:10])
                logger.info(f"Upcoming lockup expiries: {tickers}")

        log_fetch_end(conn, log_id, items_checked=1, new_items=summary["stored"])

    except Exception as e:
        summary["errors"].append(str(e))
        logger.error(f"Lockup computation failed: {e}")
        log_fetch_end(conn, log_id, status="error", error=str(e))
    finally:
        conn.close()

    return summary


def init(db_path=None):
    return fetch_lockup_daily()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    print(fetch_lockup_daily())
