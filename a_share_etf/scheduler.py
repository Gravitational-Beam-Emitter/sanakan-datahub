"""
Scheduler — auto-fetch daily ETF flow data at market close.

Usage:
    python -m a_share_etf.scheduler

Registers one cron job: 15:47 every weekday (Mon-Fri).
"""

from __future__ import annotations

import logging
import time

from apscheduler.schedulers.background import BackgroundScheduler

from a_share_etf.pipeline import fetch_latest

logger = logging.getLogger("a_share_etf.scheduler")


def start_scheduler() -> BackgroundScheduler:
    """Start the daily fetch scheduler. Returns scheduler instance."""
    scheduler = BackgroundScheduler()

    scheduler.add_job(
        _run,
        "cron",
        day_of_week="mon-fri",
        hour=15,
        minute=47,  # 15:47 — avoid :00/:30 contention
        id="a_share_etf_daily_fetch",
        name="A-share ETF daily flow fetch (15:47 Mon-Fri)",
        misfire_grace_time=3600,
    )

    scheduler.start()
    logger.info("A-share ETF scheduler started — daily fetch at 15:47 Mon-Fri")
    return scheduler


def _run() -> None:
    logger.info("Running daily ETF flow fetch...")
    start = time.time()
    result = fetch_latest()
    elapsed = time.time() - start
    logger.info(f"Daily ETF fetch done in {elapsed:.1f}s — {result}")


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
