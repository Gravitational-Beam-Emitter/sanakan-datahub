"""
Corporate Events pipeline — Dividends + Stock Splits from yfinance.

Usage:
    python -m us_listings.corporate_events_pipeline --init
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
    init_db, upsert_dividends, upsert_splits,
    log_fetch_start, log_fetch_end, get_all_crypto_products,
)

logger = logging.getLogger("us_listings.corporate_events")

# Major US stocks + crypto tickers to track
TOP_DIVIDEND_TICKERS = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "BRK.B",
    "JPM", "V", "JNJ", "WMT", "PG", "MA", "UNH", "HD", "DIS", "BAC",
    "NFLX", "ADBE", "CRM", "AMD", "INTC", "QCOM", "TXN", "PYPL",
    "XOM", "CVX", "KO", "PEP", "MCD", "NKE", "COST", "ABBV", "MRK",
    "PFE", "LLY", "TMO", "DHR", "ABT", "NEE", "SPGI", "MS", "GS",
    "GME", "AMC", "CVNA", "AI", "PLTR", "SOFI", "RIVN", "LCID",
    "COIN", "MSTR", "MARA", "RIOT",
]


def fetch_dividends_bulk(tickers: List[str]) -> List[Dict[str, Any]]:
    """Fetch dividend info from yfinance for a list of tickers."""
    records = []
    today = datetime.now().strftime("%Y-%m-%d")

    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance not installed")
        return records

    for ticker in tickers:
        try:
            stock = yf.Ticker(ticker)
            info = stock.info or {}

            div_rate = info.get("dividendRate")
            div_yield = info.get("dividendYield")
            ex_date = info.get("exDividendDate")
            pay_date = info.get("dividendDate")
            five_yr_yield = info.get("fiveYearAvgDividendYield")
            payout_ratio = info.get("payoutRatio")
            last_div = info.get("lastDividendValue")

            if div_rate or div_yield or ex_date:
                if isinstance(ex_date, (int, float)) and ex_date > 0:
                    ex_date = datetime.fromtimestamp(ex_date).strftime("%Y-%m-%d")
                if isinstance(pay_date, (int, float)) and pay_date > 0:
                    pay_date = datetime.fromtimestamp(pay_date).strftime("%Y-%m-%d")

                records.append({
                    "ticker": ticker,
                    "announcement_date": today,
                    "ex_dividend_date": ex_date,
                    "pay_date": pay_date,
                    "dividend_rate": float(div_rate) if div_rate else None,
                    "dividend_yield": float(div_yield * 100) if div_yield else None,
                    "last_dividend_value": float(last_div) if last_div else None,
                    "payout_ratio": float(payout_ratio * 100) if payout_ratio else None,
                    "five_year_avg_yield": float(five_yr_yield * 100) if five_yr_yield else None,
                    "source": "yfinance",
                })
        except Exception as e:
            logger.debug(f"Dividend fetch failed for {ticker}: {e}")

        time.sleep(YFINANCE_RATE_LIMIT)

    logger.info(f"Fetched dividends for {len(records)} tickers")
    return records


def fetch_splits_history(tickers: List[str]) -> List[Dict[str, Any]]:
    """Fetch stock split history from yfinance for a list of tickers."""
    records = []

    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance not installed")
        return records

    for ticker in tickers:
        try:
            stock = yf.Ticker(ticker)
            splits = stock.splits
            if splits is not None and not splits.empty:
                for dt, ratio in splits.items():
                    if hasattr(dt, "strftime"):
                        split_date = dt.strftime("%Y-%m-%d")
                    else:
                        split_date = str(dt)[:10]
                    records.append({
                        "ticker": ticker,
                        "split_date": split_date,
                        "split_ratio": float(ratio),
                        "source": "yfinance",
                    })

            # Also check info for last split
            info = stock.info or {}
            last_factor = info.get("lastSplitFactor")
            last_date = info.get("lastSplitDate")
            if last_factor and last_date:
                if isinstance(last_date, (int, float)) and last_date > 0:
                    last_date = datetime.fromtimestamp(last_date).strftime("%Y-%m-%d")
                # Make a ratio string like "4:1"
                try:
                    ratio = float(last_factor.split(":")[0]) / float(last_factor.split(":")[1]) if ":" in str(last_factor) else float(last_factor)
                except (ValueError, ZeroDivisionError):
                    ratio = None

                if ratio:
                    records.append({
                        "ticker": ticker,
                        "split_date": last_date,
                        "split_ratio": ratio,
                        "source": "yfinance",
                    })

        except Exception as e:
            logger.debug(f"Split fetch failed for {ticker}: {e}")

        time.sleep(YFINANCE_RATE_LIMIT)

    logger.info(f"Fetched splits for {len(records)} records")
    return records


def fetch_corporate_events_daily() -> Dict[str, Any]:
    """Fetch dividends and splits for tracked tickers."""
    conn = init_db()
    summary = {"dividends_stored": 0, "splits_stored": 0, "errors": []}
    today = datetime.now().strftime("%Y-%m-%d")

    log_id = log_fetch_start(conn, today, source="corporate_events")

    try:
        crypto_df = get_all_crypto_products(conn)
        crypto_tickers = crypto_df["ticker"].tolist() if not crypto_df.empty else []
        all_tickers = list(dict.fromkeys(TOP_DIVIDEND_TICKERS + crypto_tickers))

        # 1. Dividends
        div_records = fetch_dividends_bulk(all_tickers)
        if div_records:
            summary["dividends_stored"] = upsert_dividends(conn, div_records)
            logger.info(f"Stored {summary['dividends_stored']} dividend records")

        # 2. Stock Splits
        split_records = fetch_splits_history(all_tickers)
        if split_records:
            summary["splits_stored"] = upsert_splits(conn, split_records)
            logger.info(f"Stored {summary['splits_stored']} split records")

        log_fetch_end(conn, log_id, items_checked=len(all_tickers),
                      new_items=summary["dividends_stored"] + summary["splits_stored"])

    except Exception as e:
        summary["errors"].append(str(e))
        logger.error(f"Corporate events fetch failed: {e}")
        log_fetch_end(conn, log_id, status="error", error=str(e))
    finally:
        conn.close()

    return summary


def init(db_path=None):
    return fetch_corporate_events_daily()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    print(fetch_corporate_events_daily())
