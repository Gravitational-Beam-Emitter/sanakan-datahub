"""
Data pipeline — fetch SK Hynix cross-market prices and compute arbitrage.

Usage:
    python -m hynix.pipeline                   # fetch latest trading day
    python -m hynix.pipeline --init            # seed instruments + backfill 90d
    python -m hynix.pipeline --date 20260714   # fetch specific date
    python -m hynix.pipeline --all             # fetch all tracked dates (last 90d)
"""

from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import yfinance as yf

from hynix.config import INSTRUMENTS, FX_PAIRS, BASE_TICKER, DEFAULT_LOOKBACK_DAYS
from hynix.storage import (
    init_db,
    _norm_date,
    upsert_instruments,
    upsert_daily_prices,
    upsert_fx_rates,
    upsert_arbitrage,
    log_fetch,
    get_instruments,
    get_counts,
)

logger = logging.getLogger("hynix.pipeline")


# ═══════════════════════════════════════════════════════════════
#  Instrument seeding
# ═══════════════════════════════════════════════════════════════

def seed_instruments(db_path: Optional[str] = None) -> int:
    """Seed the instrument catalog into DuckDB."""
    conn = init_db(db_path)
    count = upsert_instruments(conn, INSTRUMENTS)
    conn.close()
    logger.info(f"Seeded {count} instruments")
    return count


# ═══════════════════════════════════════════════════════════════
#  FX rate fetch
# ═══════════════════════════════════════════════════════════════

def _fetch_fx_rates(date_str: str) -> pd.DataFrame:
    """Fetch FX rates for a given date via yfinance.

    Returns DataFrame with columns: date, from_ccy, to_ccy, rate
    """
    rows = []
    yf_map = {
        ("USD", "KRW"): "KRW=X",
        ("HKD", "KRW"): "HKDKRW=X",
    }

    for (from_ccy, to_ccy), yf_ticker in yf_map.items():
        try:
            end_date = (pd.Timestamp(date_str) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
            ticker = yf.Ticker(yf_ticker)
            hist = ticker.history(start=date_str, end=end_date)
            if hist.empty:
                # Try a wider window
                start_w = (pd.Timestamp(date_str) - pd.Timedelta(days=5)).strftime("%Y-%m-%d")
                hist = ticker.history(start=start_w, end=end_date)
            if not hist.empty:
                rate = float(hist["Close"].iloc[-1])
                rows.append({
                    "date": pd.Timestamp(date_str).date(),
                    "from_ccy": from_ccy,
                    "to_ccy": to_ccy,
                    "rate": rate,
                })
            else:
                logger.warning(f"No FX data for {yf_ticker} on {date_str}")
        except Exception as e:
            logger.error(f"FX fetch failed for {from_ccy}/{to_ccy}: {e}")

    return pd.DataFrame(rows)


# ═══════════════════════════════════════════════════════════════
#  Price fetch
# ═══════════════════════════════════════════════════════════════

def _fetch_price_yfinance(yf_ticker: str, date_str: str) -> Optional[Dict[str, Any]]:
    """Fetch OHLCV for a single instrument on a given date via yfinance.

    Returns dict with: ticker, date, open, high, low, close, volume, change_pct
    """
    try:
        end_date = (pd.Timestamp(date_str) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        start_date = (pd.Timestamp(date_str) - pd.Timedelta(days=5)).strftime("%Y-%m-%d")
        ticker = yf.Ticker(yf_ticker)
        hist = ticker.history(start=start_date, end=end_date)

        if hist.empty:
            logger.warning(f"No price data for {yf_ticker} around {date_str}")
            return None

        # Find the row closest to date_str
        target_date = pd.Timestamp(date_str).tz_localize(hist.index.tz) if hist.index.tz else pd.Timestamp(date_str)
        if target_date not in hist.index:
            # Use most recent date ≤ target
            hist = hist[hist.index <= target_date]
            if hist.empty:
                return None

        row = hist.iloc[-1]
        prev_row = hist.iloc[-2] if len(hist) > 1 else None

        close = float(row["Close"])
        change_pct = None
        if prev_row is not None and prev_row["Close"] > 0:
            change_pct = ((close - float(prev_row["Close"])) / float(prev_row["Close"])) * 100

        return {
            "ticker": yf_ticker,
            "date": pd.Timestamp(date_str).date(),
            "open": float(row["Open"]) if pd.notna(row.get("Open")) else None,
            "high": float(row["High"]) if pd.notna(row.get("High")) else None,
            "low": float(row["Low"]) if pd.notna(row.get("Low")) else None,
            "close": close,
            "volume": int(row["Volume"]) if pd.notna(row.get("Volume")) else None,
            "change_pct": change_pct,
        }
    except Exception as e:
        logger.error(f"Price fetch failed for {yf_ticker}: {e}")
        return None


def fetch_prices(date_str: str, db_path: Optional[str] = None,
                 instruments: Optional[List[Dict]] = None) -> int:
    """Fetch prices for all active instruments on a given date.

    Returns number of prices fetched.
    """
    conn = init_db(db_path)

    if instruments is None:
        instruments_df = get_instruments(conn, active_only=True)
        instruments = instruments_df.to_dict(orient="records")

    rows = []
    for inst in instruments:
        yf_ticker = inst.get("yf_ticker") or inst["ticker"]
        data = _fetch_price_yfinance(yf_ticker, date_str)
        if data:
            data["ticker"] = inst["ticker"]  # Use DB ticker, not yf ticker
            rows.append(data)
        time.sleep(0.2)  # Rate limit

    if rows:
        df = pd.DataFrame(rows)
        count = upsert_daily_prices(conn, df)
    else:
        count = 0

    conn.close()
    logger.info(f"Fetched {count} prices for {date_str}")
    return count


# ═══════════════════════════════════════════════════════════════
#  ETF NAV estimation
# ═══════════════════════════════════════════════════════════════

def _estimate_etf_nav(instrument: Dict, yf_ticker: str, date_str: str) -> Optional[float]:
    """Attempt to estimate ETF NAV from yfinance.

    For most ETFs, yfinance doesn't provide NAV directly.
    We try the yfinance fund info or return None.
    """
    try:
        t = yf.Ticker(yf_ticker)
        # Some ETFs publish NAV via fast_info or history metadata
        info = t.fast_info
        nav = getattr(info, "nav_price", None)
        if nav and nav > 0:
            return float(nav)
        # Try fund info
        fund_info = t.info if hasattr(t, "info") else {}
        nav = fund_info.get("navPrice") or fund_info.get("netAssetValue")
        if nav:
            return float(nav)
    except Exception:
        pass
    return None


# ═══════════════════════════════════════════════════════════════
#  Tracking ratio estimation
# ═══════════════════════════════════════════════════════════════

def _estimate_tracking_ratio(instrument: Dict, current_price: float,
                              base_price: float, leverage: float) -> float:
    """Estimate the tracking ratio (how many KR shares per unit of instrument).

    For instruments with a fixed tracking_ratio (ADR), use that directly.
    For ETFs/ETPs without a known ratio, estimate from price comparison.

    The formula:
      tracking_ratio = (instrument_price / leverage) / base_price

    This works for both 1x and 2x instruments because:
      - For 1x unleveraged: instrument ≈ base * ratio
      - For 2x leveraged: instrument ≈ 2 * base * ratio (approximately, same day)
    """
    static = instrument.get("tracking_ratio")
    if static is not None and static > 0:
        return float(static)

    # Estimate from price
    if base_price > 0 and current_price > 0:
        unlevered_price = current_price / abs(leverage) if leverage != 0 else current_price
        return unlevered_price / base_price

    return 0.0


# ═══════════════════════════════════════════════════════════════
#  Arbitrage calculation
# ═══════════════════════════════════════════════════════════════

def _get_stored_tracking_ratio(conn, ticker: str) -> Optional[float]:
    """Get the most recently stored tracking_ratio_used for an instrument.

    Returns None if no prior data exists (first fetch for this instrument).
    """
    row = conn.execute("""
        SELECT tracking_ratio_used FROM hynix_arbitrage
        WHERE ticker = ? AND tracking_ratio_used IS NOT NULL
        ORDER BY date DESC LIMIT 1
    """, [ticker]).fetchone()
    return float(row[0]) if row and row[0] else None


def compute_arbitrage(date_str: str, db_path: Optional[str] = None) -> int:
    """Compute premium/discount for all instruments vs base (000660.KS).

    For each instrument:
      1. Convert price to KRW using FX rate
      2. Determine tracking_ratio (shares of 000660.KS per unit):
         - Static: use instrument's fixed tracking_ratio (e.g., ADR 0.1)
         - Dynamic: use previously stored tracking_ratio from arbitrage history
           (so premium reflects actual divergence from established relationship)
         - First fetch: estimate from current prices (premium will be ~0% initially)
      3. equivalent_krw_per_share = price_krw / (leverage * tracking_ratio)
      4. premium_pct = (equivalent_krw_per_share / base_price_krw - 1) * 100

    For negative-leverage instruments: premium is skipped.
    """
    conn = init_db(db_path)

    # Get prices for the date
    prices_df = conn.execute("""
        SELECT p.date, p.ticker, p.close, p.nav,
               i.currency, i.instrument_type, i.leverage, i.tracking_ratio, i.skh_weight
        FROM hynix_daily_prices p
        JOIN hynix_instruments i ON p.ticker = i.ticker
        WHERE p.date = ? AND p.close IS NOT NULL
    """, [_norm_date(date_str)]).fetchall()

    if not prices_df:
        logger.warning(f"No price data for {date_str}")
        conn.close()
        return 0

    # Get base price (000660.KS)
    base_price = None
    for row in prices_df:
        if row[1] == BASE_TICKER:
            base_price = row[2]
            break

    if base_price is None or base_price <= 0:
        logger.warning(f"No valid base price ({BASE_TICKER}) for {date_str}")
        conn.close()
        return 0

    # Get FX rates
    fx = {}
    fx_rows = conn.execute("""
        SELECT from_ccy, to_ccy, rate FROM hynix_fx_rates WHERE date = ?
    """, [_norm_date(date_str)]).fetchall()
    for r in fx_rows:
        fx[f"{r[0]}{r[1]}"] = r[2]

    # Compute arbitrage for each instrument
    arb_rows = []
    for row in prices_df:
        p_date, ticker, close, nav, currency, inst_type, leverage, static_ratio, skh_weight = row
        skh_weight = float(skh_weight) if skh_weight else 1.0

        # Skip inverse / negative leverage instruments for premium calc
        if leverage is not None and leverage < 0:
            continue

        # Convert to KRW
        if currency == "KRW":
            price_krw = close
            nav_krw = nav
        elif currency == "USD":
            usdkrw = fx.get("USDKRW", 1380.0)
            price_krw = close * usdkrw
            nav_krw = nav * usdkrw if nav else None
        elif currency == "HKD":
            hkdkrw = fx.get("HKDKRW", 177.0)
            price_krw = close * hkdkrw
            nav_krw = nav * hkdkrw if nav else None
        else:
            price_krw = close
            nav_krw = nav

        # Determine tracking ratio
        effective_leverage = abs(leverage) if leverage else 1.0
        if static_ratio is not None and static_ratio > 0:
            # Fixed ratio (e.g., ADR, base stock)
            tracking_ratio_used = float(static_ratio)
        else:
            # Dynamic ratio: use stored history, or estimate on first fetch
            stored = _get_stored_tracking_ratio(conn, ticker)
            if stored is not None and stored > 0:
                tracking_ratio_used = stored
            else:
                # First fetch: estimate from current price (premium will be ~0%)
                # tracking_ratio = unlevered_price / base_price * skh_weight
                tracking_ratio_used = (price_krw / effective_leverage * skh_weight) / base_price if base_price > 0 else 0.0
                logger.info(f"First tracking_ratio estimate for {ticker}: {tracking_ratio_used:.6f} (skh_weight={skh_weight})")

        # Equivalent KRW per share — what you pay for 1 share of SK Hynix exposure
        # = price_krw / (effective_leverage * tracking_ratio / skh_weight)
        if tracking_ratio_used and tracking_ratio_used > 0 and skh_weight > 0:
            equivalent_krw_per_share = price_krw / (effective_leverage * tracking_ratio_used / skh_weight)
        else:
            equivalent_krw_per_share = None

        # Premium/discount vs direct KR stock
        if equivalent_krw_per_share and equivalent_krw_per_share > 0 and base_price > 0:
            premium_pct = (equivalent_krw_per_share / base_price - 1.0) * 100.0
        else:
            premium_pct = None

        # NAV premium (ETF market price vs NAV)
        if nav_krw and nav_krw > 0:
            nav_premium_pct = (price_krw / nav_krw - 1.0) * 100.0
        else:
            nav_premium_pct = None

        arb_rows.append({
            "date": pd.Timestamp(date_str).date(),
            "ticker": ticker,
            "price_local": close,
            "price_krw": price_krw,
            "base_price_krw": base_price,
            "nav_local": nav,
            "nav_krw": nav_krw,
            "tracking_ratio_used": tracking_ratio_used,
            "equivalent_krw_per_share": equivalent_krw_per_share,
            "premium_pct": premium_pct,
            "nav_premium_pct": nav_premium_pct,
        })

    if arb_rows:
        df = pd.DataFrame(arb_rows)
        count = upsert_arbitrage(conn, df)
    else:
        count = 0

    conn.close()
    logger.info(f"Computed arbitrage for {count} instruments on {date_str}")
    return count


# ═══════════════════════════════════════════════════════════════
#  Daily fetch orchestrator
# ═══════════════════════════════════════════════════════════════

def fetch_daily(date_str: str, db_path: Optional[str] = None) -> Dict[str, Any]:
    """Run a full daily fetch cycle for a given date.

    1. Fetch FX rates
    2. Fetch prices for all instruments
    3. Compute arbitrage comparison
    """
    conn = init_db(db_path)
    errors = []

    # 0. Ensure instruments are seeded
    inst_count = upsert_instruments(conn, INSTRUMENTS)

    # 1. FX rates
    fx_count = 0
    fx_df = _fetch_fx_rates(date_str)
    if not fx_df.empty:
        fx_count = upsert_fx_rates(conn, fx_df)
    else:
        # Try to use previous day's FX rate
        prev_date = (pd.Timestamp(date_str) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        fx_prev = _fetch_fx_rates(prev_date)
        if not fx_prev.empty:
            fx_prev["date"] = pd.Timestamp(date_str).date()
            fx_count = upsert_fx_rates(conn, fx_prev)
            logger.info(f"Used previous day FX rates for {date_str}")

    conn.close()

    # 2. Prices
    prices_count = fetch_prices(date_str, db_path, INSTRUMENTS)

    # 3. Arbitrage
    arb_count = compute_arbitrage(date_str, db_path)

    # 4. Log
    conn = init_db(db_path)
    log_fetch(conn, date_str, status="success" if not errors else "partial",
              instruments_count=inst_count, prices_count=prices_count,
              fx_count=fx_count, arbitrage_count=arb_count,
              errors="; ".join(errors) if errors else "")
    conn.close()

    return {
        "date": date_str,
        "instruments": inst_count,
        "prices": prices_count,
        "fx_rates": fx_count,
        "arbitrage": arb_count,
        "errors": errors,
    }


def fetch_latest(db_path: Optional[str] = None) -> Dict[str, Any]:
    """Fetch the latest available trading day."""
    today = datetime.now()

    # Skip weekends
    if today.weekday() >= 5:
        today = today - timedelta(days=today.weekday() - 4)

    date_str = today.strftime("%Y-%m-%d")
    return fetch_daily(date_str, db_path)


def init_pipeline(db_path: Optional[str] = None,
                  lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> Dict[str, Any]:
    """Full init: seed instruments + backfill prices + compute arbitrage."""
    results = {"dates": [], "total_prices": 0, "total_arbitrage": 0, "errors": []}

    # Seed instruments
    seed_instruments(db_path)

    # Backfill
    end_date = datetime.now()
    start_date = end_date - timedelta(days=lookback_days)

    current = end_date
    while current >= start_date:
        if current.weekday() < 5:  # Skip weekends
            date_str = current.strftime("%Y-%m-%d")
            try:
                r = fetch_daily(date_str, db_path)
                results["dates"].append(date_str)
                results["total_prices"] += r["prices"]
                results["total_arbitrage"] += r["arbitrage"]
                logger.info(f"Backfilled {date_str}: {r['prices']} prices, {r['arbitrage']} arb")
            except Exception as e:
                msg = f"{date_str}: {e}"
                results["errors"].append(msg)
                logger.error(msg)
        current -= timedelta(days=1)

    return results


# ═══════════════════════════════════════════════════════════════
#  CLI entry point
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(description="SK Hynix cross-market arbitrage pipeline")
    parser.add_argument("--init", action="store_true", help="Full init: seed + backfill 90d")
    parser.add_argument("--date", type=str, help="Fetch specific date (YYYY-MM-DD)")
    parser.add_argument("--all", action="store_true", help="Fetch all tracked dates (last 90d)")
    parser.add_argument("--lookback", type=int, default=DEFAULT_LOOKBACK_DAYS,
                        help=f"Lookback days for --init/--all (default: {DEFAULT_LOOKBACK_DAYS})")
    args = parser.parse_args()

    if args.init:
        result = init_pipeline(lookback_days=args.lookback)
        print(f"Init complete: {len(result['dates'])} dates, "
              f"{result['total_prices']} prices, {result['total_arbitrage']} arbitrage rows")
        if result["errors"]:
            print(f"Errors: {result['errors']}")

    elif args.date:
        result = fetch_daily(args.date)
        print(f"Fetch {args.date}: {result}")

    elif args.all:
        result = init_pipeline(lookback_days=args.lookback)
        print(f"Backfill complete: {len(result['dates'])} dates")

    else:
        result = fetch_latest()
        print(f"Latest fetch: {result}")
