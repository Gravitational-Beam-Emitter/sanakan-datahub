"""
Scheduler — auto-fetch US corporate actions daily after market close.

Usage:
    python -m us_corp_actions.scheduler

Registers one cron job: every weekday at 06:07 HKT (≈ 18:07 ET previous day),
after most 8-K filings for the US trading day are available on SEC EDGAR.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler

from us_corp_actions.pipeline import fetch_daily

logger = logging.getLogger("us_corp_actions.scheduler")


def start_scheduler() -> BackgroundScheduler:
    """Start the daily fetch scheduler. Returns scheduler instance.

    Runs every weekday at 06:07 local time (HKT).
    This catches 8-Ks filed after the previous US trading day's close
    (US market closes at 16:00 ET ≈ 04:00 HKT next day).
    """
    scheduler = BackgroundScheduler()

    scheduler.add_job(
        _run,
        "cron",
        day_of_week="mon-fri",
        hour=6,
        minute=7,  # 06:07 HKT — avoid :00/:30 contention
        id="us_corp_actions_daily_fetch",
        name="US Corporate Actions daily fetch (06:07 Mon-Fri HKT)",
        misfire_grace_time=3600,
    )

    scheduler.start()
    logger.info(
        "US Corporate Actions scheduler started — "
        "daily 8-K fetch at 06:07 Mon-Fri HKT (≈ 18:07 ET T-1)"
    )
    return scheduler


def _run() -> None:
    """Run the daily fetch for the previous trading day."""
    # Fetch yesterday's date (US market close, available by ~06:00 HKT next day)
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    logger.info(f"Running daily US corporate actions fetch for {yesterday}...")
    start = time.time()
    result = fetch_daily(yesterday)
    elapsed = time.time() - start
    logger.info(
        f"Daily fetch done in {elapsed:.1f}s — "
        f"found {result['filings_found']} filings, "
        f"stored {result['actions_stored']} actions"
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    scheduler = start_scheduler()
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
