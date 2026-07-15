"""
Options Flow pipeline — unusual options activity detection.

Tracks daily options volume, open interest, and computes unusual
activity signals (high volume/OI ratio, put/call skew, etc.).

Uses yfinance options chain data (free, delayed 15 min).

Usage:
    python -m us_listings.options_flow_pipeline --init
    python -m us_listings.options_flow_pipeline --ticker AAPL
"""

from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from us_listings.config import YFINANCE_RATE_LIMIT
from us_listings.storage import (
    init_db, upsert_options_flow, log_fetch_start, log_fetch_end,
    get_all_crypto_products,
)

logger = logging.getLogger("us_listings.options_flow")

# Ticklers with most active options markets
OPTIONS_TICKERS = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "BRK.B",
    "JPM", "V", "JNJ", "WMT", "PG", "MA", "UNH", "HD", "DIS", "BAC",
    "NFLX", "ADBE", "CRM", "AMD", "INTC", "QCOM", "TXN", "PYPL",
    "GME", "AMC", "CVNA", "AI", "PLTR", "SOFI", "RIVN", "LCID",
    "COIN", "MSTR", "MARA", "RIOT", "SPY", "QQQ", "IWM",
]


def fetch_options_flow(tickers: List[str]) -> List[Dict[str, Any]]:
    """Fetch options chain summary from yfinance. Detects unusual activity."""
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

            # Get nearest expiration options chain
            # yfinance options property returns list of expiration dates
            expirations = stock.options
            if not expirations:
                continue

            # Use nearest expiration
            nearest_exp = expirations[0]
            opt = stock.option_chain(nearest_exp)

            calls = opt.calls
            puts = opt.puts

            # Aggregate metrics
            total_call_volume = int(calls["volume"].sum()) if not calls.empty else 0
            total_put_volume = int(puts["volume"].sum()) if not puts.empty else 0
            total_call_oi = int(calls["openInterest"].sum()) if not calls.empty else 0
            total_put_oi = int(puts["openInterest"].sum()) if not puts.empty else 0

            total_volume = total_call_volume + total_put_volume
            total_oi = total_call_oi + total_put_oi

            # Put/Call ratio
            put_call_vol = round(total_put_volume / total_call_volume, 3) if total_call_volume > 0 else None
            put_call_oi = round(total_put_oi / total_call_oi, 3) if total_call_oi > 0 else None

            # Volume/OI ratio (high = unusual)
            vol_oi_ratio = round(total_volume / total_oi, 3) if total_oi > 0 else None

            # Find highest volume strike
            highest_call = calls.iloc[calls["volume"].idxmax()] if not calls.empty and calls["volume"].max() > 0 else None
            highest_put = puts.iloc[puts["volume"].idxmax()] if not puts.empty and puts["volume"].max() > 0 else None

            max_call_strike = float(highest_call["strike"]) if highest_call is not None else None
            max_call_vol = int(highest_call["volume"]) if highest_call is not None else None
            max_put_strike = float(highest_put["strike"]) if highest_put is not None else None
            max_put_vol = int(highest_put["volume"]) if highest_put is not None else None

            # Unusual activity signal
            is_unusual = False
            if vol_oi_ratio and vol_oi_ratio > 2.0:
                is_unusual = True
            if total_volume > 100000:  # >100K contracts
                is_unusual = True
            if put_call_vol and (put_call_vol > 2.0 or put_call_vol < 0.3):
                is_unusual = True

            # Sentiment based on put/call skew
            sentiment = "neutral"
            if put_call_vol and put_call_vol > 1.5:
                sentiment = "bearish"
            elif put_call_vol and put_call_vol < 0.5:
                sentiment = "bullish"

            if total_volume > 0 or total_oi > 0:
                records.append({
                    "ticker": ticker,
                    "date": today,
                    "expiration_date": nearest_exp,
                    "total_call_volume": total_call_volume,
                    "total_put_volume": total_put_volume,
                    "total_call_oi": total_call_oi,
                    "total_put_oi": total_put_oi,
                    "put_call_vol_ratio": put_call_vol,
                    "put_call_oi_ratio": put_call_oi,
                    "vol_oi_ratio": vol_oi_ratio,
                    "max_call_strike": max_call_strike,
                    "max_call_volume": max_call_vol,
                    "max_put_strike": max_put_strike,
                    "max_put_volume": max_put_vol,
                    "is_unusual": is_unusual,
                    "sentiment": sentiment,
                    "source": "yfinance",
                })

        except Exception as e:
            logger.debug(f"Options fetch failed for {ticker}: {e}")

        time.sleep(YFINANCE_RATE_LIMIT * 2)  # Options data is heavier, slow down

    logger.info(f"Fetched options flow for {len(records)} tickers")
    return records


def fetch_options_flow_daily() -> Dict[str, Any]:
    """Fetch and store options flow data."""
    conn = init_db()
    summary = {"stored": 0, "errors": []}
    today = datetime.now().strftime("%Y-%m-%d")

    log_id = log_fetch_start(conn, today, source="options_flow")

    try:
        crypto_df = get_all_crypto_products(conn)
        crypto_tickers = crypto_df["ticker"].tolist() if not crypto_df.empty else []
        all_tickers = list(dict.fromkeys(OPTIONS_TICKERS + crypto_tickers))

        records = fetch_options_flow(all_tickers)
        if records:
            summary["stored"] = upsert_options_flow(conn, records)
            logger.info(f"Stored {summary['stored']} options flow records")

            # Log unusual activity
            unusual = [r for r in records if r.get("is_unusual")]
            if unusual:
                tickers_str = ", ".join(f"{r['ticker']}({r['sentiment']})" for r in unusual[:10])
                logger.info(f"Unusual options activity: {tickers_str}")

        log_fetch_end(conn, log_id, items_checked=len(all_tickers),
                      new_items=summary["stored"])

    except Exception as e:
        summary["errors"].append(str(e))
        logger.error(f"Options flow fetch failed: {e}")
        log_fetch_end(conn, log_id, status="error", error=str(e))
    finally:
        conn.close()

    return summary


def fetch_options_for_ticker(ticker: str) -> Dict[str, Any]:
    """Fetch options flow for a single ticker."""
    records = fetch_options_flow([ticker])
    return {"ticker": ticker, "records": records}


def init(db_path=None):
    return fetch_options_flow_daily()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    if "--ticker" in sys.argv:
        idx = sys.argv.index("--ticker")
        print(fetch_options_for_ticker(sys.argv[idx + 1]))
    else:
        print(fetch_options_flow_daily())
