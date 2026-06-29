"""
Fast backfill — fetch 8-K filings from SEC daily index only, skip exhibits.

Usage:
    python -m us_corp_actions.backfill 2025-01-01 2026-06-09
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta
from typing import List, Optional

from us_corp_actions.pipeline import (
    classify_and_prepare,
    fetch_filings_by_date,
    _load_ticker_map,
)
from us_corp_actions.storage import (
    init_db,
    upsert_corporate_actions,
    cleanup_old_records,
)

logger = logging.getLogger("us_corp_actions.backfill")


def backfill_range(
    start_date: str,
    end_date: str,
    db_path: Optional[str] = None,
    fetch_exhibits: bool = False,
) -> dict:
    """Backfill corporate actions for a date range using daily index only.

    Optimized for speed:
    - Skips RSS feed (won't have old dates anyway)
    - Uses daily index directly (complete coverage)
    - Optionally skips exhibit text (biggest time saver, fetch_exhibits=False)

    Returns summary dict with per-day results.
    """
    conn = init_db(db_path)
    ticker_map = _load_ticker_map(conn)

    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")

    total_filings = 0
    total_stored = 0
    days_processed = 0
    errors = []

    d = start
    while d <= end:
        date_str = d.strftime("%Y-%m-%d")

        if d.weekday() >= 5:  # Skip weekends
            d += timedelta(days=1)
            continue

        try:
            filings = fetch_filings_by_date(date_str)
            n = len(filings)
            total_filings += n

            if filings:
                actions = classify_and_prepare(
                    conn, filings, ticker_map,
                    fetch_items=True,
                    fetch_exhibits=fetch_exhibits,
                )
                if actions:
                    count = upsert_corporate_actions(conn, actions)
                    total_stored += count
                    logger.info(f"{date_str}: {n} filings → {count} actions")
                else:
                    logger.info(f"{date_str}: {n} filings → 0 actions")
            else:
                logger.info(f"{date_str}: no filings (holiday or SEC closed)")

            days_processed += 1

        except Exception as e:
            msg = f"{date_str}: ERROR - {e}"
            logger.error(msg)
            errors.append(msg)

        d += timedelta(days=1)
        time.sleep(0.3)  # Gentle gap between days

    # Cleanup
    cleaned = cleanup_old_records(conn)
    if cleaned:
        logger.info(f"Cleaned up {cleaned} expired records")

    conn.close()

    return {
        "start": start_date,
        "end": end_date,
        "days_processed": days_processed,
        "total_filings": total_filings,
        "total_stored": total_stored,
        "errors": errors,
    }


if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    start = sys.argv[1] if len(sys.argv) > 1 else "2025-01-01"
    end = sys.argv[2] if len(sys.argv) > 2 else date.today().isoformat()

    logger.info(f"Backfill: {start} → {end}")
    t0 = time.time()
    result = backfill_range(start, end, fetch_exhibits=False)
    elapsed = time.time() - t0

    logger.info(
        f"Done in {elapsed/60:.0f}m — "
        f"{result['days_processed']} days, "
        f"{result['total_filings']} filings, "
        f"{result['total_stored']} actions stored"
    )
    if result["errors"]:
        logger.info(f"Errors: {len(result['errors'])} days")
        for e in result["errors"][:10]:
            logger.info(f"  {e}")
