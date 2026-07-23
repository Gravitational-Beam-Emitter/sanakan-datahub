"""
Scheduler — dual cron jobs for auction + fund flow data.

Usage:
    python3 -m a_share_money_flow.scheduler

Cron jobs:
  - 09:28 Mon-Fri: pre-market auction rush fetch
  - 14:57 Mon-Fri: fund flow rankings fetch (just before 3pm close)
"""

from __future__ import annotations

import logging
import time

from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger("a_share_money_flow.scheduler")


def start_scheduler() -> BackgroundScheduler:
    """Start the dual cron scheduler."""
    scheduler = BackgroundScheduler()

    scheduler.add_job(
        _run_auction,
        "cron",
        day_of_week="mon-fri",
        hour=9,
        minute=28,  # 9:28 — right after call auction matching at 9:25
        id="money_flow_auction",
        name="A-share auction rush fetch (09:28 Mon-Fri)",
        misfire_grace_time=600,  # 10 min grace
    )

    scheduler.add_job(
        _run_fund_flow,
        "cron",
        day_of_week="mon-fri",
        hour=14,
        minute=57,  # 14:57 — just before 3pm market close
        id="money_flow_fund_flow",
        name="A-share fund flow fetch (14:57 Mon-Fri)",
        misfire_grace_time=600,
    )

    scheduler.start()
    logger.info("Money flow scheduler started — auction 9:28 + fund_flow 14:57 Mon-Fri")
    return scheduler


def _run_auction() -> None:
    from a_share_money_flow.pipeline import fetch_auction
    from a_share_money_flow.storage import init_db

    logger.info("Running auction rush fetch...")
    start = time.time()
    conn = init_db()
    try:
        result = fetch_auction(conn)
        elapsed = time.time() - start
        logger.info(f"Auction fetch done in {elapsed:.1f}s — {result.get('status')}")
    except Exception as e:
        logger.error(f"Auction fetch failed: {e}")
    finally:
        conn.close()


def _run_fund_flow() -> None:
    from a_share_money_flow.pipeline import fetch_fund_flow
    from a_share_money_flow.storage import init_db

    logger.info("Running fund flow fetch...")
    start = time.time()
    conn = init_db()
    try:
        result = fetch_fund_flow(conn)
        elapsed = time.time() - start
        logger.info(f"Fund flow fetch done in {elapsed:.1f}s — {result.get('status')}")
    except Exception as e:
        logger.error(f"Fund flow fetch failed: {e}")
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
