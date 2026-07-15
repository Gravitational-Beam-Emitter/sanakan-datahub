"""
Storage layer — DuckDB-backed store for SK Hynix cross-market arbitrage data.

Tables:
  hynix_instruments     — Master instrument catalog
  hynix_daily_prices    — Daily OHLCV + NAV for all instruments
  hynix_fx_rates        — Daily FX rates
  hynix_arbitrage       — Computed premium/discount vs base
  hynix_fetch_log       — Fetch audit trail
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import duckdb
import pandas as pd

from hynix.config import DB_PATH


def _norm_date(date: str) -> str:
    """Normalize date string to YYYY-MM-DD."""
    date = date.replace("-", "").replace("/", "")
    if len(date) == 8:
        return f"{date[:4]}-{date[4:6]}-{date[6:8]}"
    return date


def _conn(db_path: Optional[str] = None, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    path = db_path or DB_PATH
    return duckdb.connect(path, read_only=read_only)


def init_db(db_path: Optional[str] = None, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """Create tables and indexes if they don't exist. Returns connection."""
    conn = _conn(db_path, read_only=read_only)
    if read_only:
        return conn

    # 1. Instrument catalog
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hynix_instruments (
            ticker VARCHAR NOT NULL,
            name VARCHAR NOT NULL,
            market VARCHAR NOT NULL,
            currency VARCHAR NOT NULL,
            instrument_type VARCHAR NOT NULL,
            leverage DOUBLE DEFAULT 1.0,
            tracking_ratio DOUBLE,
            skh_weight DOUBLE DEFAULT 1.0,
            yf_ticker VARCHAR,
            note VARCHAR,
            is_active BOOLEAN DEFAULT true,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(ticker)
        )
    """)

    # 2. Daily prices
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hynix_daily_prices (
            date DATE NOT NULL,
            ticker VARCHAR NOT NULL,
            open DOUBLE,
            high DOUBLE,
            low DOUBLE,
            close DOUBLE,
            volume BIGINT,
            nav DOUBLE,
            change_pct DOUBLE,
            PRIMARY KEY(date, ticker)
        )
    """)

    # 3. FX rates
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hynix_fx_rates (
            date DATE NOT NULL,
            from_ccy VARCHAR NOT NULL,
            to_ccy VARCHAR NOT NULL,
            rate DOUBLE NOT NULL,
            PRIMARY KEY(date, from_ccy, to_ccy)
        )
    """)

    # 4. Arbitrage comparison
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hynix_arbitrage (
            date DATE NOT NULL,
            ticker VARCHAR NOT NULL,
            price_local DOUBLE,
            price_krw DOUBLE,
            base_price_krw DOUBLE,
            nav_local DOUBLE,
            nav_krw DOUBLE,
            tracking_ratio_used DOUBLE,
            equivalent_krw_per_share DOUBLE,
            premium_pct DOUBLE,
            nav_premium_pct DOUBLE,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(date, ticker)
        )
    """)

    # 5. Fetch log
    conn.execute("CREATE SEQUENCE IF NOT EXISTS seq_hynix_fetch_log")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hynix_fetch_log (
            id INTEGER PRIMARY KEY DEFAULT nextval('seq_hynix_fetch_log'),
            fetch_date DATE NOT NULL,
            status VARCHAR NOT NULL DEFAULT 'success',
            instruments_count INTEGER DEFAULT 0,
            prices_count INTEGER DEFAULT 0,
            fx_count INTEGER DEFAULT 0,
            arbitrage_count INTEGER DEFAULT 0,
            errors TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        )
    """)

    # Indexes
    conn.execute("CREATE INDEX IF NOT EXISTS idx_hi_market ON hynix_instruments(market)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_hi_type ON hynix_instruments(instrument_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_hdp_date ON hynix_daily_prices(date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_hdp_ticker ON hynix_daily_prices(ticker)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_hfx_date ON hynix_fx_rates(date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_harb_date ON hynix_arbitrage(date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_harb_ticker ON hynix_arbitrage(ticker)")

    return conn


# ═══════════════════════════════════════════════════════════════
#  UPSERT helpers
# ═══════════════════════════════════════════════════════════════

def upsert_instruments(conn: duckdb.DuckDBPyConnection, instruments: List[Dict]) -> int:
    """Batch insert/update instrument definitions. Returns row count."""
    if not instruments:
        return 0
    df = pd.DataFrame(instruments)
    needed = ["ticker", "name", "market", "currency", "instrument_type",
              "leverage", "tracking_ratio", "skh_weight", "yf_ticker", "note"]
    for col in needed:
        if col not in df.columns:
            df[col] = None
    sub = df[needed].copy()
    conn.register("_tmp_hi", sub)
    rows = conn.execute("""
        INSERT INTO hynix_instruments (ticker, name, market, currency, instrument_type,
                                        leverage, tracking_ratio, skh_weight, yf_ticker, note)
        SELECT ticker, name, market, currency, instrument_type,
               leverage, tracking_ratio, skh_weight, yf_ticker, note
        FROM _tmp_hi
        ON CONFLICT (ticker) DO UPDATE SET
            name = excluded.name,
            market = excluded.market,
            currency = excluded.currency,
            instrument_type = excluded.instrument_type,
            leverage = excluded.leverage,
            tracking_ratio = COALESCE(excluded.tracking_ratio, hynix_instruments.tracking_ratio),
            skh_weight = COALESCE(excluded.skh_weight, hynix_instruments.skh_weight),
            yf_ticker = COALESCE(excluded.yf_ticker, hynix_instruments.yf_ticker),
            note = COALESCE(excluded.note, hynix_instruments.note),
            updated_at = now()
    """).fetchall()
    conn.unregister("_tmp_hi")
    return rows[0][0] if rows else 0


def upsert_daily_prices(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> int:
    """Batch insert/update daily OHLCV + NAV. Returns row count."""
    if df.empty:
        return 0
    needed = ["date", "ticker", "open", "high", "low", "close", "volume", "nav", "change_pct"]
    for col in needed:
        if col not in df.columns:
            df[col] = None
    sub = df[needed].copy()
    conn.register("_tmp_hdp", sub)
    rows = conn.execute("""
        INSERT INTO hynix_daily_prices (date, ticker, open, high, low, close, volume, nav, change_pct)
        SELECT date, ticker, open, high, low, close, volume, nav, change_pct
        FROM _tmp_hdp
        ON CONFLICT (date, ticker) DO UPDATE SET
            open = excluded.open,
            high = excluded.high,
            low = excluded.low,
            close = excluded.close,
            volume = excluded.volume,
            nav = COALESCE(excluded.nav, hynix_daily_prices.nav),
            change_pct = excluded.change_pct
    """).fetchall()
    conn.unregister("_tmp_hdp")
    return rows[0][0] if rows else 0


def upsert_fx_rates(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> int:
    """Batch insert/update FX rates. Returns row count."""
    if df.empty:
        return 0
    needed = ["date", "from_ccy", "to_ccy", "rate"]
    for col in needed:
        if col not in df.columns:
            df[col] = None
    sub = df[needed].copy()
    conn.register("_tmp_hfx", sub)
    rows = conn.execute("""
        INSERT INTO hynix_fx_rates (date, from_ccy, to_ccy, rate)
        SELECT date, from_ccy, to_ccy, rate
        FROM _tmp_hfx
        ON CONFLICT (date, from_ccy, to_ccy) DO UPDATE SET
            rate = excluded.rate
    """).fetchall()
    conn.unregister("_tmp_hfx")
    return rows[0][0] if rows else 0


def upsert_arbitrage(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> int:
    """Batch insert/update arbitrage comparison rows. Returns row count."""
    if df.empty:
        return 0
    needed = ["date", "ticker", "price_local", "price_krw", "base_price_krw",
              "nav_local", "nav_krw", "tracking_ratio_used",
              "equivalent_krw_per_share", "premium_pct", "nav_premium_pct"]
    for col in needed:
        if col not in df.columns:
            df[col] = None
    sub = df[needed].copy()
    conn.register("_tmp_harb", sub)
    rows = conn.execute("""
        INSERT INTO hynix_arbitrage (date, ticker, price_local, price_krw, base_price_krw,
                                      nav_local, nav_krw, tracking_ratio_used,
                                      equivalent_krw_per_share, premium_pct, nav_premium_pct)
        SELECT date, ticker, price_local, price_krw, base_price_krw,
               nav_local, nav_krw, tracking_ratio_used,
               equivalent_krw_per_share, premium_pct, nav_premium_pct
        FROM _tmp_harb
        ON CONFLICT (date, ticker) DO UPDATE SET
            price_local = excluded.price_local,
            price_krw = excluded.price_krw,
            base_price_krw = excluded.base_price_krw,
            nav_local = excluded.nav_local,
            nav_krw = excluded.nav_krw,
            tracking_ratio_used = excluded.tracking_ratio_used,
            equivalent_krw_per_share = excluded.equivalent_krw_per_share,
            premium_pct = excluded.premium_pct,
            nav_premium_pct = excluded.nav_premium_pct,
            updated_at = now()
    """).fetchall()
    conn.unregister("_tmp_harb")
    return rows[0][0] if rows else 0


# ═══════════════════════════════════════════════════════════════
#  Query helpers
# ═══════════════════════════════════════════════════════════════

def get_instruments(conn: duckdb.DuckDBPyConnection, market: str = None,
                    active_only: bool = True) -> pd.DataFrame:
    """List instruments with optional market filter."""
    where = ["1=1"]
    params: list = []
    if market:
        where.append("market = ?")
        params.append(market)
    if active_only:
        where.append("is_active = true")
    return conn.execute(f"""
        SELECT ticker, name, market, currency, instrument_type, leverage,
               tracking_ratio, skh_weight, yf_ticker, note
        FROM hynix_instruments
        WHERE {' AND '.join(where)}
        ORDER BY market, ticker
    """, params).df()


def get_daily_prices(conn: duckdb.DuckDBPyConnection, ticker: str,
                     start: str = None, end: str = None, limit: int = 60) -> pd.DataFrame:
    """Get price history for an instrument."""
    where = ["ticker = ?"]
    params: list = [ticker]
    if start:
        where.append("date >= ?")
        params.append(_norm_date(start))
    if end:
        where.append("date <= ?")
        params.append(_norm_date(end))
    params.append(limit)
    return conn.execute(f"""
        SELECT date, open, high, low, close, volume, nav, change_pct
        FROM hynix_daily_prices
        WHERE {' AND '.join(where)}
        ORDER BY date DESC
        LIMIT ?
    """, params).df()


def get_prices_for_date(conn: duckdb.DuckDBPyConnection, date: str) -> pd.DataFrame:
    """Get all instrument prices for a given date."""
    return conn.execute("""
        SELECT p.date, p.ticker, i.name, i.market, i.currency, i.instrument_type,
               i.leverage, i.tracking_ratio,
               p.open, p.high, p.low, p.close, p.volume, p.nav, p.change_pct
        FROM hynix_daily_prices p
        JOIN hynix_instruments i ON p.ticker = i.ticker
        WHERE p.date = ?
        ORDER BY i.market, i.ticker
    """, [_norm_date(date)]).df()


def get_fx_rates(conn: duckdb.DuckDBPyConnection, date: str) -> Dict[str, float]:
    """Get FX rates for a given date. Returns {from_ccy_to_ccy: rate}."""
    rows = conn.execute("""
        SELECT from_ccy, to_ccy, rate
        FROM hynix_fx_rates
        WHERE date = ?
    """, [_norm_date(date)]).fetchall()
    return {f"{r[0]}{r[1]}": r[2] for r in rows}


def get_fx_history(conn: duckdb.DuckDBPyConnection, from_ccy: str = "USD",
                   to_ccy: str = "KRW", limit: int = 60) -> pd.DataFrame:
    """Get FX rate history."""
    return conn.execute("""
        SELECT date, rate
        FROM hynix_fx_rates
        WHERE from_ccy = ? AND to_ccy = ?
        ORDER BY date DESC
        LIMIT ?
    """, [from_ccy, to_ccy, limit]).df()


def get_arbitrage(conn: duckdb.DuckDBPyConnection, date: str) -> pd.DataFrame:
    """Get arbitrage comparison for a date, with instrument info joined."""
    return conn.execute("""
        SELECT a.date, a.ticker, i.name, i.market, i.currency, i.instrument_type,
               i.leverage, i.tracking_ratio AS static_ratio,
               a.price_local, a.price_krw, a.base_price_krw,
               a.nav_local, a.nav_krw, a.tracking_ratio_used,
               a.equivalent_krw_per_share, a.premium_pct, a.nav_premium_pct
        FROM hynix_arbitrage a
        JOIN hynix_instruments i ON a.ticker = i.ticker
        WHERE a.date = ?
        ORDER BY a.premium_pct DESC NULLS LAST
    """, [_norm_date(date)]).df()


def get_arbitrage_history(conn: duckdb.DuckDBPyConnection, ticker: str,
                          start: str = None, end: str = None, limit: int = 60) -> pd.DataFrame:
    """Get premium/discount time series for an instrument."""
    where = ["ticker = ?"]
    params: list = [ticker]
    if start:
        where.append("date >= ?")
        params.append(_norm_date(start))
    if end:
        where.append("date <= ?")
        params.append(_norm_date(end))
    params.append(limit)
    return conn.execute(f"""
        SELECT date, price_local, price_krw, base_price_krw,
               equivalent_krw_per_share, premium_pct, nav_premium_pct
        FROM hynix_arbitrage
        WHERE {' AND '.join(where)}
        ORDER BY date DESC
        LIMIT ?
    """, params).df()


def get_available_dates(conn: duckdb.DuckDBPyConnection, limit: int = 30) -> List[str]:
    """Get list of dates with arbitrage data."""
    rows = conn.execute("""
        SELECT DISTINCT date FROM hynix_arbitrage
        ORDER BY date DESC LIMIT ?
    """, [limit]).fetchall()
    return [str(r[0]) for r in rows]


def get_latest_summary(conn: duckdb.DuckDBPyConnection) -> Optional[Dict[str, Any]]:
    """Get the latest arbitrage snapshot with all instruments."""
    row = conn.execute("SELECT MAX(date) FROM hynix_arbitrage").fetchone()
    if row is None or row[0] is None:
        return None
    latest_date = str(row[0])
    df = get_arbitrage(conn, latest_date)
    fx = get_fx_rates(conn, latest_date)
    return {
        "date": latest_date,
        "fx_rates": fx,
        "base_ticker": "000660.KS",
        "base_price_krw": df.iloc[0]["base_price_krw"] if not df.empty else None,
        "instruments": df.to_dict(orient="records"),
    }


def get_counts(conn: duckdb.DuckDBPyConnection) -> Dict[str, Any]:
    """Get overall database counts."""
    instruments = conn.execute(
        "SELECT COUNT(*) FROM hynix_instruments WHERE is_active = true"
    ).fetchone()[0]
    dates = conn.execute(
        "SELECT COUNT(DISTINCT date) FROM hynix_daily_prices"
    ).fetchone()[0]
    arb_dates = conn.execute(
        "SELECT COUNT(DISTINCT date) FROM hynix_arbitrage"
    ).fetchone()[0]
    return {
        "total_instruments": instruments,
        "trading_days_with_prices": dates,
        "trading_days_with_arbitrage": arb_dates,
    }


def log_fetch(conn: duckdb.DuckDBPyConnection, date: str, status: str = "success",
              instruments_count: int = 0, prices_count: int = 0,
              fx_count: int = 0, arbitrage_count: int = 0,
              errors: str = "") -> int:
    """Record a fetch operation in the log."""
    conn.execute("""
        INSERT INTO hynix_fetch_log (fetch_date, status, instruments_count,
                                      prices_count, fx_count, arbitrage_count, errors)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, [_norm_date(date), status, instruments_count, prices_count,
          fx_count, arbitrage_count, errors])
    return conn.execute("SELECT MAX(id) FROM hynix_fetch_log").fetchone()[0]


def get_fetch_status(conn: duckdb.DuckDBPyConnection, days: int = 7) -> pd.DataFrame:
    """Get recent fetch log entries."""
    return conn.execute("""
        SELECT id, fetch_date, status, instruments_count, prices_count,
               fx_count, arbitrage_count, errors, created_at
        FROM hynix_fetch_log
        WHERE fetch_date >= (SELECT MAX(fetch_date) FROM hynix_fetch_log) - ?
        ORDER BY fetch_date DESC
    """, [days]).df()
