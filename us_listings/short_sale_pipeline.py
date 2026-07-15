"""
Short Sale Activity pipeline — enhanced short sale signals.

Since FINRA daily short sale volume data requires Cloudflare-cleared access,
this pipeline uses yfinance to track short sale metrics daily and compute
risk signals (high short interest %, crowded short trades, etc.).

Usage:
    python -m us_listings.short_sale_pipeline --init
"""

from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from us_listings.config import YFINANCE_RATE_LIMIT
from us_listings.storage import (
    init_db, upsert_short_activity, log_fetch_start, log_fetch_end,
    get_all_crypto_products,
)

logger = logging.getLogger("us_listings.short_sale")

# High short interest candidates + popular tickers
SHORT_SALE_TICKERS = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "BRK.B",
    "JPM", "V", "JNJ", "WMT", "PG", "MA", "UNH", "HD", "DIS", "BAC",
    "NFLX", "ADBE", "CRM", "AMD", "INTC", "QCOM", "TXN", "PYPL",
    "GME", "AMC", "CVNA", "AI", "PLTR", "SOFI", "RIVN", "LCID",
    "COIN", "MSTR", "MARA", "RIOT", "BYND", "SPCE", "NKLA", "MULN",
    "TOP", "FFIE", "HOLO", "MCOM", "CYN", "WISA", "WETG",
]


def fetch_short_sale_activity(tickers: List[str]) -> List[Dict[str, Any]]:
    """Fetch enhanced short sale metrics from yfinance."""
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

            short_interest = info.get("sharesShort")
            short_pct_float = info.get("shortPercentOfFloat")
            short_ratio = info.get("shortRatio")
            avg_volume = info.get("averageVolume")
            shares_outstanding = info.get("sharesOutstanding")
            float_shares = info.get("floatShares")
            short_prior = info.get("sharesShortPriorMonth")
            held_pct_insiders = info.get("heldPercentInsiders")
            held_pct_institutions = info.get("heldPercentInstitutions")

            if not (short_interest or short_pct_float):
                continue

            # Compute signals
            pct_float = float(short_pct_float * 100) if short_pct_float else None

            # Calculate short interest change vs prior month
            short_change_pct = None
            if short_interest and short_prior and short_prior > 0:
                short_change_pct = (short_interest - short_prior) / short_prior * 100

            # Days to cover
            days_to_cover = float(short_ratio) if short_ratio else None
            if not days_to_cover and short_interest and avg_volume and avg_volume > 0:
                days_to_cover = short_interest / avg_volume

            # Risk level classification
            risk_level = "normal"
            if pct_float and pct_float > 40:
                risk_level = "extreme"
            elif pct_float and pct_float > 25:
                risk_level = "high"
            elif pct_float and pct_float > 10:
                risk_level = "elevated"

            # Short squeeze score (simple heuristic)
            squeeze_score = 0
            if pct_float and pct_float > 20:
                squeeze_score += min(50, int(pct_float))
            if days_to_cover and days_to_cover > 3:
                squeeze_score += min(30, int(days_to_cover * 5))
            if short_change_pct and short_change_pct > 10:
                squeeze_score += min(20, int(short_change_pct / 2))
            if held_pct_insiders and held_pct_insiders > 0.2:
                squeeze_score += 10

            records.append({
                "ticker": ticker,
                "date": today,
                "short_interest": int(short_interest) if short_interest else None,
                "short_pct_float": round(pct_float, 2) if pct_float else None,
                "days_to_cover": round(days_to_cover, 2) if days_to_cover else None,
                "avg_volume": int(avg_volume) if avg_volume else None,
                "float_shares": int(float_shares) if float_shares else None,
                "short_change_pct": round(short_change_pct, 2) if short_change_pct else None,
                "insider_ownership_pct": round(float(held_pct_insiders * 100), 2) if held_pct_insiders else None,
                "institutional_ownership_pct": round(float(held_pct_institutions * 100), 2) if held_pct_institutions else None,
                "risk_level": risk_level,
                "squeeze_score": squeeze_score,
                "source": "yfinance",
            })

        except Exception as e:
            logger.debug(f"Short sale fetch failed for {ticker}: {e}")

        time.sleep(YFINANCE_RATE_LIMIT)

    logger.info(f"Fetched short sale activity for {len(records)} tickers")
    return records


def fetch_short_sale_daily() -> Dict[str, Any]:
    """Fetch and store short sale activity metrics."""
    conn = init_db()
    summary = {"stored": 0, "errors": []}
    today = datetime.now().strftime("%Y-%m-%d")

    log_id = log_fetch_start(conn, today, source="short_sale_activity")

    try:
        crypto_df = get_all_crypto_products(conn)
        crypto_tickers = crypto_df["ticker"].tolist() if not crypto_df.empty else []
        all_tickers = list(dict.fromkeys(SHORT_SALE_TICKERS + crypto_tickers))

        records = fetch_short_sale_activity(all_tickers)
        if records:
            summary["stored"] = upsert_short_activity(conn, records)
            logger.info(f"Stored {summary['stored']} short sale activity records")

            # Log high-risk tickers
            high_risk = [r for r in records if r.get("risk_level") in ("high", "extreme")]
            if high_risk:
                tickers_high = ", ".join(f"{r['ticker']}({r['short_pct_float']:.1f}%)" for r in high_risk[:10])
                logger.info(f"High short interest: {tickers_high}")

        log_fetch_end(conn, log_id, items_checked=len(all_tickers),
                      new_items=summary["stored"])

    except Exception as e:
        summary["errors"].append(str(e))
        logger.error(f"Short sale fetch failed: {e}")
        log_fetch_end(conn, log_id, status="error", error=str(e))
    finally:
        conn.close()

    return summary


def init(db_path=None):
    return fetch_short_sale_daily()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    print(fetch_short_sale_daily())
