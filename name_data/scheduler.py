"""
Scheduler — daily fortune pre-computation at midnight.

Computes and caches the day's calendar info + daily I Ching hexagram.
Runs once per day at 00:07 (off-peak minute).

Usage:
    python -m name_data.scheduler
"""

from __future__ import annotations

import logging
import time

from apscheduler.schedulers.background import BackgroundScheduler

from name_data.pipeline import compute_daily_fortune

logger = logging.getLogger("name_data.scheduler")


def start_scheduler() -> BackgroundScheduler:
    """Start the daily fortune scheduler. Returns scheduler instance."""
    scheduler = BackgroundScheduler()

    scheduler.add_job(
        _run,
        "cron",
        hour=0,
        minute=7,  # 00:07 — avoid :00/:30 contention
        id="name_data_daily_fortune",
        name="Name Data daily fortune pre-computation (00:07 daily)",
        misfire_grace_time=3600,
    )

    scheduler.start()
    logger.info("Name Data scheduler started — daily fortune at 00:07")
    return scheduler


def _run() -> None:
    import datetime as dt
    today = dt.date.today()
    logger.info(f"Computing daily fortune for {today}...")
    start = time.time()
    result = compute_daily_fortune(today)
    elapsed = time.time() - start
    logger.info(
        f"Daily fortune computed in {elapsed:.1f}s — "
        f"hexagram={result['daily_hexagram']['name']} "
        f"fortune={result['fortune_level']}"
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
