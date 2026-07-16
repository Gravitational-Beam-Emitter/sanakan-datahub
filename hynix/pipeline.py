"""
Data pipeline — fetch SK Hynix cross-market prices and compute arbitrage.

Data sources (yfinance-free):
  - FinanceDataReader (KRX backend) → Korean stocks
  - FinanceDataReader (Yahoo backend) → US ADR (may fail if Yahoo blocked)
  - Alpha Vantage API           → US ADR fallback (needs ALPHA_VANTAGE_KEY)
  - EastMoney push2 API         → HK stocks
  - open.er-api.com             → FX rates (free, no key)
  - akshare fx_spot_quote       → FX rates fallback

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
import requests

from hynix.config import INSTRUMENTS, FX_PAIRS, BASE_TICKER, DEFAULT_LOOKBACK_DAYS, ALPHA_VANTAGE_KEY
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

# Shared requests session with browser-like headers
_SESSION = None

def _get_session() -> requests.Session:
    global _SESSION
    if _SESSION is None:
        _SESSION = requests.Session()
        _SESSION.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
        })
    return _SESSION


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
#  FX rate fetch (open.er-api.com + akshare fallback)
# ═══════════════════════════════════════════════════════════════

def _fetch_fx_rates(date_str: str) -> pd.DataFrame:
    """Fetch FX rates for a given date.

    Primary: open.er-api.com (free, no key, daily rates)
    Fallback: akshare fx_spot_quote (current spot only)

    Returns DataFrame with columns: date, from_ccy, to_ccy, rate
    """
    rows = []
    session = _get_session()

    # ── Primary: open.er-api.com ──
    try:
        r = session.get("https://open.er-api.com/v6/latest/USD", timeout=10)
        r.raise_for_status()
        data = r.json()
        usd_krw = data.get("rates", {}).get("KRW")
        usd_hkd = data.get("rates", {}).get("HKD")

        if usd_krw and usd_krw > 0:
            rows.append({
                "date": pd.Timestamp(date_str).date(),
                "from_ccy": "USD",
                "to_ccy": "KRW",
                "rate": round(usd_krw, 4),
            })
            logger.info(f"FX USD/KRW = {usd_krw:.2f} (open.er-api.com)")

        if usd_hkd and usd_hkd > 0 and usd_krw and usd_krw > 0:
            hkd_krw = usd_krw / usd_hkd
            rows.append({
                "date": pd.Timestamp(date_str).date(),
                "from_ccy": "HKD",
                "to_ccy": "KRW",
                "rate": round(hkd_krw, 4),
            })
            logger.info(f"FX HKD/KRW = {hkd_krw:.2f} (derived from USD/KRW ÷ USD/HKD)")
    except Exception as e:
        logger.warning(f"open.er-api.com FX fetch failed: {e}")

    # ── Fallback: akshare fx_spot_quote ──
    if not rows:
        try:
            import akshare as ak
            fx_spot = ak.fx_spot_quote()
            for _, row in fx_spot.iterrows():
                pair = str(row.get("货币对", ""))
                if "USD/KRW" in pair or "USDKRW" in pair:
                    try:
                        rate = float(row.get("买报价", 0))
                        if rate > 0:
                            rows.append({
                                "date": pd.Timestamp(date_str).date(),
                                "from_ccy": "USD",
                                "to_ccy": "KRW",
                                "rate": rate,
                            })
                    except (ValueError, TypeError):
                        pass
                if "HKD/KRW" in pair or "HKDKRW" in pair:
                    try:
                        rate = float(row.get("买报价", 0))
                        if rate > 0:
                            rows.append({
                                "date": pd.Timestamp(date_str).date(),
                                "from_ccy": "HKD",
                                "to_ccy": "KRW",
                                "rate": rate,
                            })
                    except (ValueError, TypeError):
                        pass
            if rows:
                logger.info(f"FX rates from akshare fallback: {len(rows)} pairs")
        except Exception as e:
            logger.error(f"akshare FX fallback also failed: {e}")

    return pd.DataFrame(rows)


# ═══════════════════════════════════════════════════════════════
#  Price fetch — FinanceDataReader (KR + US)
# ═══════════════════════════════════════════════════════════════

def _fetch_price_fdr(fdr_code: str, date_str: str, market: str) -> Optional[Dict[str, Any]]:
    """Fetch OHLCV via FinanceDataReader.

    - KR stocks: uses KRX backend directly (no Yahoo dependency)
    - US stocks: uses Yahoo backend (may fail if Yahoo blocked on server)
    """
    try:
        import FinanceDataReader as fdr

        start = (pd.Timestamp(date_str) - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
        end = (pd.Timestamp(date_str) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")

        df = fdr.DataReader(fdr_code, start, end)
        if df.empty:
            logger.warning(f"FDR: no data for {fdr_code} ({market}) around {date_str}")
            return None

        # Find row closest to target date
        target_dt = pd.Timestamp(date_str).date()
        df = df[df.index <= pd.Timestamp(date_str)]
        if df.empty:
            return None

        row = df.iloc[-1]
        prev_row = df.iloc[-2] if len(df) > 1 else None

        close = float(row["Close"])
        change_pct = None
        if prev_row is not None and prev_row["Close"] > 0:
            change_pct = ((close - float(prev_row["Close"])) / float(prev_row["Close"])) * 100

        return {
            "date": target_dt,
            "open": float(row["Open"]) if pd.notna(row.get("Open")) else None,
            "high": float(row["High"]) if pd.notna(row.get("High")) else None,
            "low": float(row["Low"]) if pd.notna(row.get("Low")) else None,
            "close": close,
            "volume": int(row["Volume"]) if pd.notna(row.get("Volume")) else None,
            "change_pct": change_pct,
        }
    except ImportError:
        logger.error("FinanceDataReader not installed. Install with: pip install finance-datareader")
        return None
    except Exception as e:
        logger.error(f"FDR fetch failed for {fdr_code} ({market}): {e}")
        return None


# ═══════════════════════════════════════════════════════════════
#  Price fetch — Tencent QQ (HK stocks, primary)
# ═══════════════════════════════════════════════════════════════

def _fetch_price_tencent(ticker: str, date_str: str, days: int = 30) -> Optional[Dict[str, Any]]:
    """Fetch OHLCV for HK stocks via Tencent QQ Finance API.

    ticker format: "07709" (5-digit HK code)
    Returns daily kline data in format: [date, open, close, high, low, volume]
    """
    session = _get_session()
    # Use a wider date range to ensure we capture the target date
    start = (pd.Timestamp(date_str) - pd.Timedelta(days=days)).strftime("%Y-%m-%d")
    end = (pd.Timestamp(date_str) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")

    url = (
        f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        f"?param=hk{ticker},day,{start},{end},{days + 5},qfq"
    )

    try:
        r = session.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()

        if data.get("code") != 0:
            logger.warning(f"Tencent API returned code={data.get('code')} for hk{ticker}")
            return None

        stock_data = data.get("data", {}).get(f"hk{ticker}")
        if not stock_data:
            logger.warning(f"Tencent: no data for hk{ticker}")
            return None

        days_list = stock_data.get("day") or stock_data.get("qfqday") or []
        if not days_list:
            logger.warning(f"Tencent: no daily klines for hk{ticker}")
            return None

        # Parse: [date, open, close, high, low, volume]
        target_date = pd.Timestamp(date_str).date()
        best = None
        best_idx = -1
        for i, row in enumerate(days_list):
            if len(row) < 6:
                continue
            k_date = pd.Timestamp(row[0]).date()
            if k_date <= target_date:
                best = row
                best_idx = i
            else:
                break

        if not best:
            return None

        close = float(best[2])
        change_pct = None
        if best_idx > 0:
            prev_row = days_list[best_idx - 1]
            if len(prev_row) >= 3 and float(prev_row[2]) > 0:
                change_pct = ((close - float(prev_row[2])) / float(prev_row[2])) * 100

        return {
            "date": target_date,
            "open": float(best[1]),
            "high": float(best[3]),
            "low": float(best[4]),
            "close": close,
            "volume": int(float(best[5])) if best[5] and best[5] != "0" else None,
            "change_pct": change_pct,
        }
    except Exception as e:
        logger.error(f"Tencent fetch failed for hk{ticker}: {e}")
        return None


# ═══════════════════════════════════════════════════════════════
#  Price fetch — EastMoney (HK stocks, fallback)
# ═══════════════════════════════════════════════════════════════

def _fetch_price_eastmoney(em_secid: str, date_str: str) -> Optional[Dict[str, Any]]:
    """Fetch OHLCV for HK stocks via EastMoney push2 API (fallback).

    em_secid format: "116.07709" (market_code.ticker, 116 = HK)
    """
    session = _get_session()
    beg = date_str.replace("-", "")[:8]
    end = (pd.Timestamp(date_str) + pd.Timedelta(days=1)).strftime("%Y%m%d")

    url = (
        f"https://push2his.eastmoney.com/api/qt/stock/kline/get"
        f"?secid={em_secid}"
        f"&fields1=f1,f2,f3,f4,f5,f6"
        f"&fields2=f51,f52,f53,f54,f55,f56,f57"
        f"&klt=101&fqt=0"
        f"&beg={beg}&end={end}&lmt=30"
    )

    try:
        r = session.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()

        if data.get("rc") != 0:
            return None

        result = data.get("data")
        if not result or not result.get("klines"):
            return None

        klines = result["klines"]
        target_date = pd.Timestamp(date_str).date()

        best = None
        for line in klines:
            parts = line.split(",")
            if len(parts) < 6:
                continue
            k_date = pd.Timestamp(parts[0]).date()
            if k_date <= target_date:
                best = parts
            else:
                break

        if not best:
            return None

        close = float(best[2])
        return {
            "date": target_date,
            "open": float(best[1]),
            "high": float(best[3]),
            "low": float(best[4]),
            "close": close,
            "volume": int(float(best[5])) if best[5] else None,
            "change_pct": None,
        }
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════
#  Price fetch — Alpha Vantage (US stock fallback)
# ═══════════════════════════════════════════════════════════════

def _fetch_price_alpha_vantage(symbol: str, date_str: str) -> Optional[Dict[str, Any]]:
    """Fetch OHLCV for US stocks via Alpha Vantage API.

    Requires ALPHA_VANTAGE_KEY in .env. Free tier: 25 req/day.
    """
    if not ALPHA_VANTAGE_KEY:
        logger.warning("No ALPHA_VANTAGE_KEY set — skipping Alpha Vantage fallback")
        return None

    session = _get_session()
    try:
        url = (
            f"https://www.alphavantage.co/query"
            f"?function=TIME_SERIES_DAILY"
            f"&symbol={symbol}"
            f"&outputsize=compact"
            f"&apikey={ALPHA_VANTAGE_KEY}"
        )
        r = session.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()

        # Check for error / rate limit
        if "Error Message" in data or "Note" in data:
            logger.warning(f"Alpha Vantage: {data.get('Error Message') or data.get('Note')}")
            return None

        ts = data.get("Time Series (Daily)", {})
        if not ts:
            logger.warning(f"Alpha Vantage: no time series for {symbol}")
            return None

        # Find date ≤ target
        target = pd.Timestamp(date_str).date()
        available_dates = sorted(ts.keys(), reverse=True)
        best_date = None
        for d in available_dates:
            if pd.Timestamp(d).date() <= target:
                best_date = d
                break

        if not best_date:
            return None

        row = ts[best_date]
        close = float(row["4. close"])

        return {
            "date": target,
            "open": float(row["1. open"]),
            "high": float(row["2. high"]),
            "low": float(row["3. low"]),
            "close": close,
            "volume": int(row["5. volume"]) if row.get("5. volume") else None,
            "change_pct": None,  # Alpha Vantage doesn't provide this easily
        }
    except Exception as e:
        logger.error(f"Alpha Vantage fetch failed for {symbol}: {e}")
        return None


# ═══════════════════════════════════════════════════════════════
#  Unified price fetch router
# ═══════════════════════════════════════════════════════════════

def _fetch_price(instrument: Dict, date_str: str) -> Optional[Dict[str, Any]]:
    """Fetch OHLCV for a single instrument using the appropriate backend."""
    ticker = instrument["ticker"]
    market = instrument["market"]
    fdr_code = instrument.get("fdr_code")
    em_secid = instrument.get("em_secid")

    data = None
    source = "none"

    if market == "KR":
        # Korean stocks → FinanceDataReader (KRX backend, no Yahoo)
        if fdr_code:
            data = _fetch_price_fdr(fdr_code, date_str, "KR")
            source = "fdr-krx"

    elif market == "HK":
        # HK stocks → Tencent QQ primary, EastMoney fallback
        if fdr_code:
            data = _fetch_price_tencent(fdr_code, date_str)
            source = "tencent"
        if not data and em_secid:
            data = _fetch_price_eastmoney(em_secid, date_str)
            source = "eastmoney"
        if not data and fdr_code:
            data = _fetch_price_fdr(fdr_code, date_str, "HK")
            source = "fdr-hkex"

    elif market == "US":
        # US ADR → FDR (Yahoo backend), Alpha Vantage fallback
        if fdr_code:
            data = _fetch_price_fdr(fdr_code, date_str, "US")
            source = "fdr-yahoo"
        if not data and ALPHA_VANTAGE_KEY:
            data = _fetch_price_alpha_vantage(ticker, date_str)
            source = "alphavantage"

    if data:
        data["ticker"] = ticker
        logger.debug(f"  {ticker}: OK via {source}")
    else:
        logger.warning(f"  {ticker}: FAILED (market={market})")

    return data


# ═══════════════════════════════════════════════════════════════
#  Price fetch orchestrator (replaces fetch_prices)
# ═══════════════════════════════════════════════════════════════

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
        data = _fetch_price(inst, date_str)
        if data:
            rows.append(data)
        time.sleep(0.5)  # Rate limit between instruments

    if rows:
        df = pd.DataFrame(rows)
        count = upsert_daily_prices(conn, df)
    else:
        count = 0

    conn.close()
    logger.info(f"Fetched {count} prices for {date_str}")
    return count


# ═══════════════════════════════════════════════════════════════
#  ETF NAV estimation (simplified — no yfinance dependency)
# ═══════════════════════════════════════════════════════════════

def _estimate_etf_nav(instrument: Dict, yf_ticker: str, date_str: str) -> Optional[float]:
    """Attempt to estimate ETF NAV. Returns None if unavailable.

    Without yfinance, NAV data is not easily accessible from our free sources.
    This function always returns None for now.
    A future improvement could use KRX ETF NAV API for Korean ETFs.
    """
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
        errors.append("FX rates: all sources failed")
        logger.error("No FX rates available")

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
