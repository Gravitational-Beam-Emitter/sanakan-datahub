"""
Scheduler — auto-fetch KOL thermometer data daily.

Usage:
    python -m kol_thermometer.scheduler

Registers cron jobs:
  - 16:37 every weekday (Mon-Fri): fetch posts + tag + rate + compute
  - 04:07 every day: clean up inactive KOLs
"""

from __future__ import annotations

import logging
import time

from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger("kol_thermometer.scheduler")


def start_scheduler() -> BackgroundScheduler:
    """Start the daily fetch scheduler. Returns scheduler instance."""
    scheduler = BackgroundScheduler()

    scheduler.add_job(
        _run_daily,
        "cron",
        day_of_week="mon-fri",
        hour=16,
        minute=37,  # 16:37 HKT = after US market open-ish, avoid :00/:30
        id="kol_thermometer_daily_fetch",
        name="KOL Thermometer daily fetch (16:37 Mon-Fri)",
        misfire_grace_time=3600,
    )

    scheduler.add_job(
        _run_cleanup,
        "cron",
        day_of_week="*",
        hour=4,
        minute=7,  # 04:07 — low traffic time
        id="kol_thermometer_cleanup",
        name="KOL Thermometer cleanup (04:07 daily)",
        misfire_grace_time=3600,
    )

    scheduler.start()
    logger.info("KOL Thermometer scheduler started")
    return scheduler


def _run_daily() -> None:
    from kol_thermometer.pipeline import fetch_daily

    logger.info("Running daily KOL fetch cycle...")
    start = time.time()
    result = fetch_daily()
    elapsed = time.time() - start
    logger.info(f"Daily KOL fetch done in {elapsed:.1f}s — {result.get('status')}")


def _run_cleanup() -> None:
    from kol_thermometer.storage import init_db, deactivate_inactive_kols, update_kol_ratings

    logger.info("Running KOL cleanup...")
    conn = init_db()
    try:
        d = deactivate_inactive_kols(conn)
        u = update_kol_ratings(conn)
        logger.info(f"Cleanup done — {d} deactivated, {u} ratings updated")
    finally:
        conn.close()


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
