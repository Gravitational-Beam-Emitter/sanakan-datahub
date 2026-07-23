#!/usr/bin/env python3
"""Backfill 6 months of margin + overview data for A-share ETF pipeline.

AKShare fund_etf_spot_em() is current-day only, so ETF/sector history
cannot be backfilled. Margin data from macro_china_market_margin_sh/sz()
is available back to 2010.
"""

import logging
from datetime import datetime, timedelta

import akshare as ak

from a_share_etf.storage import init_db, upsert_margin, upsert_overview

logger = logging.getLogger("a_share_etf.backfill")


def backfill(db_path=None):
    conn = init_db(db_path)

    # Check existing dates to avoid duplicate work
    existing = set()
    for r in conn.execute("SELECT DISTINCT date FROM margin_daily").fetchall():
        existing.add(str(r[0]))

    # Fetch full margin history from AKShare
    logger.info("Fetching SH margin history...")
    sh = ak.macro_china_market_margin_sh()
    logger.info("Fetching SZ margin history...")
    sz = ak.macro_china_market_margin_sz()

    # Build lookup: date_str -> balance in 亿元
    sh_lookup = {}
    for _, row in sh.iterrows():
        d = str(row["日期"])[:10]
        sh_lookup[d] = float(row["融资融券余额"]) / 1e8

    sz_lookup = {}
    for _, row in sz.iterrows():
        d = str(row["日期"])[:10]
        sz_lookup[d] = float(row["融资融券余额"]) / 1e8

    all_dates = sorted(set(sh_lookup.keys()) & set(sz_lookup.keys()))

    # 6-month window
    cutoff = (datetime.now() - timedelta(days=185)).strftime("%Y-%m-%d")
    dates = [d for d in all_dates if d >= cutoff and d not in existing]

    logger.info(
        "Backfilling %d dates from %s to %s",
        len(dates), dates[0] if dates else "N/A", dates[-1] if dates else "N/A",
    )

    stored = 0
    for date_str in dates:
        sh_val = sh_lookup[date_str]
        sz_val = sz_lookup[date_str]
        total_margin = sh_val + sz_val

        # Daily change vs previous recorded date
        prev = conn.execute(
            "SELECT total_margin FROM margin_daily WHERE date < ? ORDER BY date DESC LIMIT 1",
            [date_str],
        ).fetchone()
        prev_margin = float(prev[0]) if prev and prev[0] is not None else total_margin
        daily_change = round(total_margin - prev_margin, 6)

        upsert_margin(conn, date_str, sh_val, sz_val, total_margin, daily_change)

        # Overview with margin-only data.
        # NOTE: pipeline stores ETF inflow in 元 (not 亿元), so merged_proxy
        # must also be in 元 for the chart's /1e8 conversion to work correctly.
        # -daily_change (亿元) * 1e8 = merged_proxy in 元-equivalent.
        merged_proxy = round(-daily_change * 1e8, 2)
        upsert_overview(
            conn, date_str,
            None,           # total_etf_inflow — no historical data
            0,              # total_etf_count — no historical data
            total_margin,
            daily_change,
            merged_proxy,   # reflects margin direction only for historical dates
            None,           # market_main_inflow — no historical data
        )

        stored += 1
        if stored % 40 == 0:
            logger.info("Progress: %d/%d", stored, len(dates))

    conn.close()
    logger.info("Done: %d margin + overview records backfilled", stored)
    return stored


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    backfill()
