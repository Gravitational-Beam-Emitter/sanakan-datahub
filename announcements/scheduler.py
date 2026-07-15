"""
APScheduler — daily company announcements fetch.

Usage:
    python -m announcements.scheduler

Schedule:
    Mon-Fri 08:37 HKT — after US market close data pipelines complete.
"""

from __future__ import annotations

import logging
import time

from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger("announcements.scheduler")


def _run():
    """Wrapper for daily fetch with timing log."""
    from announcements.pipeline import fetch_daily

    t0 = time.time()
    logger.info("Starting announcements daily fetch...")
    try:
        result = fetch_daily()
        elapsed = time.time() - t0
        logger.info(
            f"Announcements daily fetch complete: "
            f"total={result['total']} (us={result.get('us',0)} hk={result.get('hk',0)} cn={result.get('cn',0)}) "
            f"errors={len(result.get('errors',[]))} in {elapsed:.1f}s"
        )
    except Exception as e:
        elapsed = time.time() - t0
        logger.error(f"Announcements daily fetch failed in {elapsed:.1f}s: {e}")


def start_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        _run,
        "cron",
        day_of_week="mon-fri",
        hour=8,
        minute=37,
        id="announcements_daily_fetch",
        name="Company Announcements daily fetch (08:37 Mon-Fri HKT)",
        misfire_grace_time=3600,
    )
    scheduler.start()
    logger.info("Announcements scheduler started (daily 08:37 Mon-Fri HKT)")
    return scheduler


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    scheduler = start_scheduler()
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        scheduler.shutdown(wait=False)
