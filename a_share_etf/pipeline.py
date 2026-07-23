"""
Data pipeline — fetch daily A-share ETF flow data from AKShare.

Usage:
    python -m a_share_etf.pipeline              # fetch latest trading day
    python -m a_share_etf.pipeline --date 20260722  # fetch specific date
"""

from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from a_share_etf.config import DB_PATH
from a_share_etf.etf_classification import classify_etf
from a_share_etf.storage import (
    init_db,
    upsert_etf_daily,
    upsert_sector_flows,
    upsert_margin,
    upsert_overview,
    get_previous_margin,
)

logger = logging.getLogger("a_share_etf.pipeline")

# AKShare fund_etf_spot_em column mapping (Chinese → English)
_ETF_COL_MAP = {
    "代码": "code",
    "名称": "name",
    "最新价": "price",
    "涨跌幅": "change_pct",
    "成交量": "volume",
    "成交额": "amount",
    "换手率": "turnover_rate",
    "IOPV实时估值": "iopv",
    "基金折价率": "discount_rate",
    "主力净流入-净额": "main_inflow",
    "主力净流入-净占比": "main_inflow_pct",
    "超大单净流入-净额": "super_large_inflow",
    "大单净流入-净额": "large_inflow",
    "中单净流入-净额": "medium_inflow",
    "小单净流入-净额": "small_inflow",
}


def fetch_daily(date: str, db_path: Optional[str] = None) -> Dict[str, Any]:
    """Fetch and store ETF flow + margin data for one trading day.

    Args:
        date: Trading date in YYYYMMDD format
        db_path: Optional DuckDB path override

    Returns:
        Summary dict with counts
    """
    import akshare as ak

    date_norm = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
    summary: Dict[str, Any] = {
        "date": date_norm, "etf_count": 0, "sector_count": 0,
        "margin_updated": False, "overview_updated": False, "errors": [],
    }

    conn = init_db(db_path)

    try:
        # ── 1. Fetch ETF spot data ──
        try:
            df_etf = ak.fund_etf_spot_em()
            logger.info(f"Fetched {len(df_etf)} ETFs from fund_etf_spot_em")
        except Exception as e:
            err = f"AKShare fund_etf_spot_em failed: {e}"
            summary["errors"].append(err)
            logger.error(err)
            conn.close()
            return summary

        if df_etf.empty:
            summary["errors"].append("ETF spot data is empty")
            conn.close()
            return summary

        # Rename and select columns
        df_etf = df_etf.rename(columns=_ETF_COL_MAP)
        keep_cols = list(_ETF_COL_MAP.values())
        df_etf = df_etf[[c for c in keep_cols if c in df_etf.columns]].copy()

        # Add date and sector
        df_etf["date"] = pd.to_datetime(date_norm)
        df_etf["sector"] = df_etf["name"].apply(classify_etf)

        # Ensure numeric types
        numeric_cols = [
            "price", "change_pct", "volume", "amount", "turnover_rate",
            "iopv", "discount_rate", "main_inflow", "main_inflow_pct",
            "super_large_inflow", "large_inflow", "medium_inflow", "small_inflow",
        ]
        for c in numeric_cols:
            if c in df_etf.columns:
                df_etf[c] = pd.to_numeric(df_etf[c], errors="coerce")

        # Volume as integer
        if "volume" in df_etf.columns:
            df_etf["volume"] = df_etf["volume"].fillna(0).astype("Int64")

        # Store per-ETF data
        etf_count = upsert_etf_daily(conn, df_etf)
        summary["etf_count"] = etf_count
        logger.info(f"[{date_norm}] {etf_count} ETFs stored")

        # ── 2. Aggregate by sector ──
        sector_df = df_etf.groupby("sector").agg(
            etf_count=("code", "count"),
            total_inflow=("main_inflow", "sum"),
            total_amount=("amount", "sum"),
        ).reset_index()
        sector_df["avg_inflow"] = sector_df["total_inflow"] / sector_df["etf_count"]
        sector_df["date"] = pd.to_datetime(date_norm)

        sector_count = upsert_sector_flows(conn, sector_df)
        summary["sector_count"] = sector_count
        logger.info(f"[{date_norm}] {sector_count} sector flows stored")

        # ── 3. Fetch margin balance ──
        try:
            sh = ak.macro_china_market_margin_sh()
            sz = ak.macro_china_market_margin_sz()
            sh_val = float(sh[sh["日期"] == sh["日期"].max()]["融资融券余额"].iloc[0]) / 1e8  # 元→亿元
            sz_val = float(sz[sz["日期"] == sz["日期"].max()]["融资融券余额"].iloc[0]) / 1e8
            total_margin = sh_val + sz_val

            # Compute daily change
            prev_margin = get_previous_margin(conn, date_norm)
            daily_change = (total_margin - prev_margin) if prev_margin is not None else 0.0

            upsert_margin(conn, date_norm, sh_val, sz_val, total_margin, daily_change)
            summary["margin_updated"] = True
            logger.info(f"[{date_norm}] margin: SH={sh_val:.2f}亿 SZ={sz_val:.2f}亿 total={total_margin:.2f}亿 change={daily_change:+.2f}亿")
        except Exception as e:
            err = f"Margin balance fetch failed: {e}"
            summary["errors"].append(err)
            logger.error(err)
            total_margin = 0.0
            daily_change = 0.0

        # ── 4. Market overview (merged proxy) ──
        total_etf_inflow = float(df_etf["main_inflow"].sum()) if "main_inflow" in df_etf.columns else 0.0

        # Fetch market-wide fund flow for reference
        market_main_inflow = None
        try:
            mf = ak.stock_market_fund_flow()
            if not mf.empty:
                latest = mf.iloc[-1]
                market_main_inflow = float(latest.get("主力净流入-净额", np.nan))
                logger.info(f"[{date_norm}] market main inflow: {market_main_inflow:.2f}")
        except Exception as e:
            logger.warning(f"Market fund flow fetch failed: {e}")

        # Merged proxy: ETF net inflow covers margin outflow
        # margin_change positive = margin increased (money flowing IN to margin)
        # margin_change negative = margin decreased (money flowing OUT of margin)
        # merged_proxy = ETF_inflow + (-margin_change) = ETF_inflow - margin_change
        # When margin is decreasing (margin_change < 0), -margin_change > 0 adds to inflow
        merged_proxy = total_etf_inflow - daily_change

        upsert_overview(conn, date_norm, total_etf_inflow, etf_count,
                        total_margin, daily_change, merged_proxy, market_main_inflow)
        summary["overview_updated"] = True
        logger.info(f"[{date_norm}] overview: ETF inflow={total_etf_inflow:.2f} "
                     f"margin={total_margin:.2f} change={daily_change:+.2f} merged={merged_proxy:+.2f}")

    finally:
        conn.close()

    return summary


def fetch_latest(db_path: Optional[str] = None) -> Dict[str, Any]:
    """Fetch the most recent trading day's data."""
    import akshare as ak
    try:
        today = datetime.now()
        for offset in range(10):
            d = (today - timedelta(days=offset)).strftime("%Y%m%d")
            try:
                df = ak.fund_etf_spot_em()
                # Check if the data date matches
                data_date_col = df.get("数据日期", None)
                if data_date_col is not None and not df.empty:
                    latest_date = str(df["数据日期"].iloc[0]).replace("-", "")
                    if latest_date:
                        logger.info(f"Using data date: {latest_date}")
                        return fetch_daily(latest_date, db_path=db_path)
                # Fallback: try today
                if not df.empty:
                    logger.info(f"Found data, using date {d}")
                    return fetch_daily(d, db_path=db_path)
            except Exception as e:
                logger.debug(f"No data for {d}: {e}")
                continue
        return {"error": "No ETF data found in last 10 days"}
    except Exception as e:
        return {"error": str(e)}


def fetch_batch(dates: list[str], db_path: Optional[str] = None) -> list[Dict[str, Any]]:
    """Fetch multiple dates."""
    results = []
    for d in dates:
        results.append(fetch_daily(d, db_path=db_path))
        time.sleep(1)
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

    if "--date" in sys.argv:
        idx = sys.argv.index("--date")
        date = sys.argv[idx + 1]
        result = fetch_daily(date)
        print(f"\nResult: {result}")
    elif "--all" in sys.argv:
        today = datetime.now()
        dates = [(today - timedelta(days=i)).strftime("%Y%m%d") for i in range(10)]
        results = fetch_batch(dates)
        for r in results:
            print(f"  {r}")
    else:
        result = fetch_latest()
        print(f"\nResult: {result}")
