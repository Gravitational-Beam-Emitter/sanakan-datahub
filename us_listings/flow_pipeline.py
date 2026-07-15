"""
ETF Flows pipeline — daily fund flow tracking for crypto ETFs.

Estimates daily flows from AUM changes:
    estimated_flow ≈ ΔAUM - (price_return × AUM_prev)

Usage:
    python -m us_listings.flow_pipeline --init
    python -m us_listings.flow_pipeline
"""

from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pandas as pd

from us_listings.config import YFINANCE_RATE_LIMIT
from us_listings.storage import (
    init_db, upsert_etf_flows, get_all_crypto_products,
    log_fetch_start, log_fetch_end,
)

logger = logging.getLogger("us_listings.flow_pipeline")


def fetch_etf_flows(etf_tickers: List[str]) -> List[Dict[str, Any]]:
    """Fetch daily flows for a list of ETF tickers from yfinance.

    Returns list of dicts with: ticker, date, close_price, volume, aum,
    estimated_flow, flow_pct
    """
    records = []

    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance not installed")
        return records

    for ticker in etf_tickers:
        try:
            stock = yf.Ticker(ticker)

            # Get last 2 days of history for flow estimation
            hist = stock.history(period="5d")
            if hist.empty or len(hist) < 2:
                continue

            today_row = hist.iloc[-1]
            prev_row = hist.iloc[-2]

            today_date = today_row.name.strftime("%Y-%m-%d") if hasattr(today_row.name, "strftime") else str(today_row.name)[:10]
            close_price = float(today_row["Close"])
            volume = int(today_row["Volume"]) if "Volume" in today_row else 0

            # Get AUM from info
            info = stock.info or {}
            aum = info.get("totalAssets") or info.get("netAssets")

            prev_close = float(prev_row["Close"])
            price_return = (close_price - prev_close) / prev_close if prev_close else 0

            # Estimate flow
            prev_aum = None
            if aum:
                prev_aum = aum / (1 + price_return) if price_return != -1 else aum
                estimated_flow = aum - prev_aum
            else:
                estimated_flow = None

            flow_pct = (estimated_flow / prev_aum * 100) if estimated_flow and prev_aum else None

            records.append({
                "ticker": ticker,
                "date": today_date,
                "close_price": close_price,
                "volume": volume,
                "aum": aum,
                "estimated_flow": round(estimated_flow, 2) if estimated_flow else None,
                "flow_pct": round(flow_pct, 4) if flow_pct else None,
                "source": "yfinance",
            })

        except Exception as e:
            logger.debug(f"Flow fetch failed for {ticker}: {e}")

        time.sleep(YFINANCE_RATE_LIMIT)

    logger.info(f"Fetched flows for {len(records)} ETFs")
    return records


def fetch_flows_daily(date_str: Optional[str] = None) -> Dict[str, Any]:
    """Fetch and store ETF flows for crypto products."""
    conn = init_db()
    summary = {"etfs_tracked": 0, "flows_stored": 0, "errors": []}

    if not date_str:
        date_str = datetime.now().strftime("%Y-%m-%d")

    log_id = log_fetch_start(conn, date_str, source="etf_flows")

    try:
        # Get crypto ETFs from crypto_products table
        crypto_df = get_all_crypto_products(conn)
        if crypto_df.empty:
            logger.info("No crypto products in DB")
            return summary

        etf_types = {"spot_etf", "futures_etf", "etp"}
        etf_tickers = crypto_df[crypto_df["product_type"].isin(etf_types)]["ticker"].tolist()
        summary["etfs_tracked"] = len(etf_tickers)

        if etf_tickers:
            records = fetch_etf_flows(etf_tickers)
            if records:
                summary["flows_stored"] = upsert_etf_flows(conn, records)
                logger.info(f"Stored {summary['flows_stored']} ETF flow records")

        log_fetch_end(conn, log_id, items_checked=len(etf_tickers),
                      new_items=summary["flows_stored"])

    except Exception as e:
        summary["errors"].append(str(e))
        logger.error(f"Flow fetch failed: {e}")
        log_fetch_end(conn, log_id, status="error", error=str(e))
    finally:
        conn.close()

    return summary


def init(db_path=None):
    return fetch_flows_daily()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    print(fetch_flows_daily())
