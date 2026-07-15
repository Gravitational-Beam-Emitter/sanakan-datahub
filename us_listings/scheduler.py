"""
Scheduler — auto-fetch all US equities data pipelines.

Usage:
    python -m us_listings.scheduler

Registers cron jobs (all HKT):
  07:07  New listings
  07:37  Crypto refresh
  08:07  Insider + Earnings
  08:27  Risk + ETF Flows
  08:47  Corporate events (dividends + splits)
  09:07  Suspensions + Enforcement + Threshold + Short activity + Lockup + Options
  Mon 09:07  13F Holdings
  Mon 09:27  ATS / Dark Pool
"""

from __future__ import annotations

import logging
import time
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler

from us_listings.pipeline import fetch_listings_for_month
from us_listings.crypto_pipeline import full_refresh
from us_listings.insider_pipeline import fetch_insider_daily
from us_listings.earnings_pipeline import fetch_earnings_daily
from us_listings.holdings_pipeline import fetch_holdings_daily
from us_listings.risk_pipeline import fetch_risk_daily
from us_listings.flow_pipeline import fetch_flows_daily
from us_listings.corporate_events_pipeline import fetch_corporate_events_daily
from us_listings.suspension_pipeline import fetch_suspension_daily
from us_listings.enforcement_pipeline import fetch_enforcement_daily
from us_listings.threshold_pipeline import fetch_threshold_daily
from us_listings.ats_pipeline import fetch_ats_daily
from us_listings.short_sale_pipeline import fetch_short_sale_daily
from us_listings.lockup_pipeline import fetch_lockup_daily
from us_listings.options_flow_pipeline import fetch_options_flow_daily

logger = logging.getLogger("us_listings.scheduler")


def start_scheduler() -> BackgroundScheduler:
    """Start the daily fetch scheduler. Returns scheduler instance."""
    scheduler = BackgroundScheduler()

    # Job 1: New listings fetch — 07:07 HKT daily
    scheduler.add_job(
        _fetch_listings,
        "cron",
        hour=7, minute=7,
        id="us_listings_daily_fetch",
        name="US new listings (07:07 HKT)",
        misfire_grace_time=3600,
    )

    # Job 2: Crypto products refresh — 07:37 HKT weekdays
    scheduler.add_job(
        _refresh_crypto,
        "cron",
        day_of_week="mon-fri", hour=7, minute=37,
        id="us_crypto_daily_refresh",
        name="Crypto products refresh (07:37 Mon-Fri HKT)",
        misfire_grace_time=3600,
    )

    # Job 3: Insider trades + Earnings calendar — 08:07 HKT weekdays
    scheduler.add_job(
        _fetch_insider_and_earnings,
        "cron",
        day_of_week="mon-fri", hour=8, minute=7,
        id="us_insider_earnings_daily",
        name="Insider + Earnings (08:07 Mon-Fri HKT)",
        misfire_grace_time=3600,
    )

    # Job 4: Risk data (short interest + FTD) + ETF flows — 08:27 HKT weekdays
    scheduler.add_job(
        _fetch_risk_and_flows,
        "cron",
        day_of_week="mon-fri", hour=8, minute=27,
        id="us_risk_flows_daily",
        name="Risk + ETF Flows (08:27 Mon-Fri HKT)",
        misfire_grace_time=3600,
    )

    # Job 5: Corporate events (dividends + splits) — 08:47 HKT weekdays
    scheduler.add_job(
        _fetch_corporate_events,
        "cron",
        day_of_week="mon-fri", hour=8, minute=47,
        id="us_corporate_events_daily",
        name="Dividends + Splits (08:47 Mon-Fri HKT)",
        misfire_grace_time=3600,
    )

    # Job 6: Suspensions + Enforcement + Threshold + Short activity + Lockup + Options — 09:07 HKT weekdays
    scheduler.add_job(
        _fetch_extended_pipelines,
        "cron",
        day_of_week="mon-fri", hour=9, minute=7,
        id="us_extended_pipelines_daily",
        name="Suspension+Enforcement+Threshold+Short+Lockup+Options (09:07 Mon-Fri HKT)",
        misfire_grace_time=7200,
    )

    # Job 7: Institutional holdings 13F — Monday 09:07 HKT (within extended batch)
    # Job 8: ATS / Dark Pool — Monday 09:27 HKT
    scheduler.add_job(
        _fetch_holdings,
        "cron",
        day_of_week="mon", hour=9, minute=27,
        id="us_holdings_weekly",
        name="13F Holdings + ATS (Mon 09:27 HKT)",
        misfire_grace_time=7200,
    )

    scheduler.start()
    logger.info(
        "US Listings scheduler started — "
        "07:07 listings, 07:37 crypto, 08:07 insider+earnings, "
        "08:27 risk+flows, 08:47 corp-events, 09:07 extended, Mon 09:27 holdings+ATS (HKT)"
    )
    return scheduler


def _fetch_listings() -> None:
    month = datetime.now().strftime("%Y-%m")
    logger.info(f"Running daily US new listings fetch for {month}...")
    t0 = time.time()
    result = fetch_listings_for_month(month)
    logger.info(f"Listings done in {time.time() - t0:.1f}s — "
                f"found {result['listings_found']}, stored {result['listings_stored']}")


def _refresh_crypto() -> None:
    logger.info("Running crypto products full refresh...")
    t0 = time.time()
    result = full_refresh()
    logger.info(f"Crypto done in {time.time() - t0:.1f}s — "
                f"known={result.get('known_loaded', 0)}, enriched={result.get('enriched', 0)}")


def _fetch_insider_and_earnings() -> None:
    logger.info("Running insider trades fetch...")
    t0 = time.time()
    insider = fetch_insider_daily()
    logger.info(f"Insider done in {time.time() - t0:.1f}s — "
                f"filings={insider.get('filings_found', 0)}, stored={insider.get('trades_stored', 0)}")

    logger.info("Running earnings calendar fetch...")
    t0 = time.time()
    earnings = fetch_earnings_daily()
    logger.info(f"Earnings done in {time.time() - t0:.1f}s — "
                f"found={earnings.get('found', 0)}, stored={earnings.get('stored', 0)}")


def _fetch_risk_and_flows() -> None:
    logger.info("Running risk data fetch (short interest + FTD)...")
    t0 = time.time()
    risk = fetch_risk_daily()
    logger.info(f"Risk done in {time.time() - t0:.1f}s — "
                f"SI={risk.get('si_stored', 0)}, FTD={risk.get('ftd_stored', 0)}")

    logger.info("Running ETF flows fetch...")
    t0 = time.time()
    flows = fetch_flows_daily()
    logger.info(f"ETF flows done in {time.time() - t0:.1f}s — "
                f"tracked={flows.get('etfs_tracked', 0)}, stored={flows.get('flows_stored', 0)}")


def _fetch_corporate_events() -> None:
    logger.info("Running corporate events fetch (dividends + splits)...")
    t0 = time.time()
    result = fetch_corporate_events_daily()
    logger.info(f"Corp events done in {time.time() - t0:.1f}s — "
                f"dividends={result.get('dividends_stored', 0)}, splits={result.get('splits_stored', 0)}")


def _fetch_extended_pipelines() -> None:
    """Fetch all remaining pipelines: suspensions, enforcement, threshold,
    short activity, lockup, options flow."""
    pipelines = [
        ("Suspensions", fetch_suspension_daily, "stored"),
        ("Enforcement", fetch_enforcement_daily, "stored"),
        ("Threshold", fetch_threshold_daily, "stored"),
        ("Short activity", fetch_short_sale_daily, "stored"),
        ("Lockup expiry", fetch_lockup_daily, "stored"),
        ("Options flow", fetch_options_flow_daily, "stored"),
    ]

    for name, func, key in pipelines:
        logger.info(f"Running {name} fetch...")
        t0 = time.time()
        try:
            result = func()
            logger.info(f"{name} done in {time.time() - t0:.1f}s — "
                        f"stored={result.get(key, 0)}")
        except Exception as e:
            logger.error(f"{name} failed: {e}")
        time.sleep(2)  # Brief pause between pipelines


def _fetch_holdings() -> None:
    logger.info("Running 13F institutional holdings fetch...")
    t0 = time.time()
    result = fetch_holdings_daily()
    logger.info(f"Holdings done in {time.time() - t0:.1f}s — "
                f"filings={result.get('filings_found', 0)}, stored={result.get('holdings_stored', 0)}")

    logger.info("Running ATS / dark pool fetch...")
    t0 = time.time()
    ats = fetch_ats_daily()
    logger.info(f"ATS done in {time.time() - t0:.1f}s — "
                f"filings={ats.get('filings_found', 0)}, stored={ats.get('stored', 0)}")


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
