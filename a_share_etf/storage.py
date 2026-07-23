"""
Storage layer — DuckDB-backed store for A-share ETF daily flow data.

Tables:
  etf_daily              — per-ETF daily snapshot (flow + price)
  sector_flow_daily      — per-sector daily aggregation
  margin_daily           — daily margin balance (融资融券)
  market_overview_daily  — daily market overview (merged proxy)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import duckdb
import pandas as pd

from a_share_etf.config import DB_PATH


def _norm_date(date: str) -> str:
    """Normalize date string to YYYY-MM-DD for DuckDB."""
    date = date.replace("-", "").replace("/", "")
    if len(date) == 8:
        return f"{date[:4]}-{date[4:6]}-{date[6:8]}"
    return date


def _conn(db_path: Optional[str] = None, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    path = db_path or DB_PATH
    return duckdb.connect(path, read_only=read_only)


def init_db(db_path: Optional[str] = None, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """Create tables if they don't exist. Returns connection for chaining."""
    conn = _conn(db_path, read_only=read_only)
    if read_only:
        return conn

    conn.execute("""
        CREATE TABLE IF NOT EXISTS etf_daily (
            date DATE NOT NULL,
            code VARCHAR NOT NULL,
            name VARCHAR NOT NULL,
            price DOUBLE,
            change_pct DOUBLE,
            volume BIGINT,
            amount DOUBLE,
            turnover_rate DOUBLE,
            iopv DOUBLE,
            discount_rate DOUBLE,
            main_inflow DOUBLE,
            main_inflow_pct DOUBLE,
            super_large_inflow DOUBLE,
            large_inflow DOUBLE,
            medium_inflow DOUBLE,
            small_inflow DOUBLE,
            sector VARCHAR,
            fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(date, code)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS sector_flow_daily (
            date DATE NOT NULL,
            sector VARCHAR NOT NULL,
            etf_count INTEGER,
            total_inflow DOUBLE,
            total_amount DOUBLE,
            avg_inflow DOUBLE,
            PRIMARY KEY(date, sector)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS margin_daily (
            date DATE PRIMARY KEY,
            sh_margin DOUBLE,
            sz_margin DOUBLE,
            total_margin DOUBLE,
            daily_change DOUBLE
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS market_overview_daily (
            date DATE PRIMARY KEY,
            total_etf_inflow DOUBLE,
            total_etf_count INTEGER,
            margin_balance DOUBLE,
            margin_change DOUBLE,
            merged_proxy DOUBLE,
            market_main_inflow DOUBLE
        )
    """)

    conn.execute("CREATE INDEX IF NOT EXISTS idx_etf_date ON etf_daily(date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_etf_code ON etf_daily(code)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_etf_sector ON etf_daily(sector)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sector_date ON sector_flow_daily(date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_margin_date ON margin_daily(date)")

    return conn


# ── ETF Daily ─────────────────────────────────────────────────

def upsert_etf_daily(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> int:
    """Batch insert/update per-ETF daily snapshots. Returns row count."""
    if df.empty:
        return 0

    needed = [
        "date", "code", "name", "price", "change_pct", "volume", "amount",
        "turnover_rate", "iopv", "discount_rate", "main_inflow", "main_inflow_pct",
        "super_large_inflow", "large_inflow", "medium_inflow", "small_inflow", "sector",
    ]
    for col in needed:
        if col not in df.columns:
            df[col] = None

    sub = df[needed].copy()
    conn.register("_tmp_etf", sub)
    rows = conn.execute("""
        INSERT INTO etf_daily (date, code, name, price, change_pct, volume, amount,
                                turnover_rate, iopv, discount_rate, main_inflow,
                                main_inflow_pct, super_large_inflow, large_inflow,
                                medium_inflow, small_inflow, sector)
        SELECT date, code, name, price, change_pct, volume, amount,
               turnover_rate, iopv, discount_rate, main_inflow,
               main_inflow_pct, super_large_inflow, large_inflow,
               medium_inflow, small_inflow, sector
        FROM _tmp_etf
        ON CONFLICT (date, code) DO UPDATE SET
            name = excluded.name,
            price = excluded.price,
            change_pct = excluded.change_pct,
            volume = excluded.volume,
            amount = excluded.amount,
            turnover_rate = excluded.turnover_rate,
            iopv = excluded.iopv,
            discount_rate = excluded.discount_rate,
            main_inflow = excluded.main_inflow,
            main_inflow_pct = excluded.main_inflow_pct,
            super_large_inflow = excluded.super_large_inflow,
            large_inflow = excluded.large_inflow,
            medium_inflow = excluded.medium_inflow,
            small_inflow = excluded.small_inflow,
            sector = excluded.sector,
            fetched_at = now()
    """).fetchall()
    conn.unregister("_tmp_etf")
    return rows[0][0] if rows else 0


def get_etfs_by_date(conn: duckdb.DuckDBPyConnection, date: str, sector: Optional[str] = None) -> pd.DataFrame:
    """Get all ETFs for a date, optionally filtered by sector."""
    date_norm = _norm_date(date)
    if sector:
        return conn.execute("""
            SELECT * FROM etf_daily
            WHERE date = ? AND sector = ?
            ORDER BY main_inflow DESC
        """, [date_norm, sector]).df()
    return conn.execute("""
        SELECT * FROM etf_daily
        WHERE date = ?
        ORDER BY main_inflow DESC
    """, [date_norm]).df()


def get_etf_history(conn: duckdb.DuckDBPyConnection, code: str, limit: int = 60) -> pd.DataFrame:
    """Get daily history for a specific ETF."""
    return conn.execute("""
        SELECT * FROM etf_daily
        WHERE code = ?
        ORDER BY date DESC
        LIMIT ?
    """, [code, limit]).df()


# ── Sector Flows ──────────────────────────────────────────────

def upsert_sector_flows(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> int:
    """Batch insert/update sector flow aggregations. Returns row count."""
    if df.empty:
        return 0

    needed = ["date", "sector", "etf_count", "total_inflow", "total_amount", "avg_inflow"]
    for col in needed:
        if col not in df.columns:
            df[col] = None

    sub = df[needed].copy()
    conn.register("_tmp_sector", sub)
    rows = conn.execute("""
        INSERT INTO sector_flow_daily (date, sector, etf_count, total_inflow, total_amount, avg_inflow)
        SELECT date, sector, etf_count, total_inflow, total_amount, avg_inflow
        FROM _tmp_sector
        ON CONFLICT (date, sector) DO UPDATE SET
            etf_count = excluded.etf_count,
            total_inflow = excluded.total_inflow,
            total_amount = excluded.total_amount,
            avg_inflow = excluded.avg_inflow
    """).fetchall()
    conn.unregister("_tmp_sector")
    return rows[0][0] if rows else 0


def get_sectors_by_date(conn: duckdb.DuckDBPyConnection, date: str) -> pd.DataFrame:
    """Get sector flow breakdown for a date."""
    return conn.execute("""
        SELECT * FROM sector_flow_daily
        WHERE date = ?
        ORDER BY total_inflow DESC
    """, [_norm_date(date)]).df()


def get_sector_history(conn: duckdb.DuckDBPyConnection, sector: str, limit: int = 60) -> pd.DataFrame:
    """Get daily flow history for a specific sector."""
    return conn.execute("""
        SELECT * FROM sector_flow_daily
        WHERE sector = ?
        ORDER BY date DESC
        LIMIT ?
    """, [sector, limit]).df()


# ── Margin Balance ────────────────────────────────────────────

def upsert_margin(conn: duckdb.DuckDBPyConnection, date: str, sh_margin: float,
                  sz_margin: float, total_margin: float, daily_change: float) -> int:
    """Insert or update one day's margin balance data. Returns 1 on success."""
    conn.execute("""
        INSERT INTO margin_daily (date, sh_margin, sz_margin, total_margin, daily_change)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (date) DO UPDATE SET
            sh_margin = excluded.sh_margin,
            sz_margin = excluded.sz_margin,
            total_margin = excluded.total_margin,
            daily_change = excluded.daily_change
    """, [_norm_date(date), sh_margin, sz_margin, total_margin, daily_change])
    return 1


def get_margin_by_date(conn: duckdb.DuckDBPyConnection, date: str) -> Optional[Dict[str, Any]]:
    """Get margin data for a specific date."""
    row = conn.execute("""
        SELECT date, sh_margin, sz_margin, total_margin, daily_change
        FROM margin_daily WHERE date = ?
    """, [_norm_date(date)]).fetchone()
    if row is None:
        return None
    return {
        "date": str(row[0]), "sh_margin": row[1], "sz_margin": row[2],
        "total_margin": row[3], "daily_change": row[4],
    }


def get_margin_history(conn: duckdb.DuckDBPyConnection, start: Optional[str] = None,
                       end: Optional[str] = None, limit: int = 60) -> pd.DataFrame:
    """Get margin balance history, optionally filtered by date range."""
    if start and end:
        return conn.execute("""
            SELECT * FROM margin_daily
            WHERE date >= ? AND date <= ?
            ORDER BY date ASC
        """, [_norm_date(start), _norm_date(end)]).df()
    return conn.execute("""
        SELECT * FROM margin_daily
        ORDER BY date DESC LIMIT ?
    """, [limit]).df()


def get_previous_margin(conn: duckdb.DuckDBPyConnection, date: str) -> Optional[float]:
    """Get total_margin for the most recent date before the given date."""
    row = conn.execute("""
        SELECT total_margin FROM margin_daily
        WHERE date < ?
        ORDER BY date DESC LIMIT 1
    """, [_norm_date(date)]).fetchone()
    return float(row[0]) if row and row[0] is not None else None


# ── Market Overview ───────────────────────────────────────────

def upsert_overview(conn: duckdb.DuckDBPyConnection, date: str, total_etf_inflow: float,
                    total_etf_count: int, margin_balance: float, margin_change: float,
                    merged_proxy: float, market_main_inflow: Optional[float] = None) -> int:
    """Insert or update one day's market overview. Returns 1 on success."""
    conn.execute("""
        INSERT INTO market_overview_daily (date, total_etf_inflow, total_etf_count,
                                            margin_balance, margin_change, merged_proxy,
                                            market_main_inflow)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (date) DO UPDATE SET
            total_etf_inflow = excluded.total_etf_inflow,
            total_etf_count = excluded.total_etf_count,
            margin_balance = excluded.margin_balance,
            margin_change = excluded.margin_change,
            merged_proxy = excluded.merged_proxy,
            market_main_inflow = excluded.market_main_inflow
    """, [_norm_date(date), total_etf_inflow, total_etf_count, margin_balance,
          margin_change, merged_proxy, market_main_inflow])
    return 1


def get_overview_by_date(conn: duckdb.DuckDBPyConnection, date: str) -> Optional[Dict[str, Any]]:
    """Get market overview for a specific date."""
    row = conn.execute("""
        SELECT * FROM market_overview_daily WHERE date = ?
    """, [_norm_date(date)]).fetchone()
    if row is None:
        return None
    return {
        "date": str(row[0]), "total_etf_inflow": row[1], "total_etf_count": row[2],
        "margin_balance": row[3], "margin_change": row[4], "merged_proxy": row[5],
        "market_main_inflow": row[6],
    }


def get_overview_history(conn: duckdb.DuckDBPyConnection, start: Optional[str] = None,
                         end: Optional[str] = None, limit: int = 60) -> pd.DataFrame:
    """Get market overview history, optionally filtered by date range."""
    if start and end:
        return conn.execute("""
            SELECT * FROM market_overview_daily
            WHERE date >= ? AND date <= ?
            ORDER BY date ASC
        """, [_norm_date(start), _norm_date(end)]).df()
    return conn.execute("""
        SELECT * FROM market_overview_daily
        ORDER BY date DESC LIMIT ?
    """, [limit]).df()


def get_available_dates(conn: duckdb.DuckDBPyConnection, limit: int = 30) -> List[str]:
    """Get list of dates with ETF data."""
    rows = conn.execute("""
        SELECT DISTINCT date FROM etf_daily
        ORDER BY date DESC LIMIT ?
    """, [limit]).fetchall()
    return [str(r[0]) for r in rows]
