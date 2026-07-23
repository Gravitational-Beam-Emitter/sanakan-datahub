"""
Storage layer — DuckDB-backed store for A-share auction & fund flow data.

Tables:
  auction_stock_daily    — per-stock auction snapshot with rush scores
  auction_sector_daily   — sector-level auction aggregation
  fund_flow_stock_daily  — per-stock daily fund flow
  fund_flow_sector_daily — sector fund flow (行业 + 概念)
  fetch_log              — fetch audit log
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import duckdb
import pandas as pd

from a_share_money_flow.config import DB_PATH


def _conn(db_path: Optional[str] = None, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    path = db_path or DB_PATH
    return duckdb.connect(path, read_only=read_only)


def init_db(db_path: Optional[str] = None, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """Create tables and indexes if they don't exist."""
    conn = _conn(db_path, read_only=read_only)
    if read_only:
        return conn

    conn.execute("CREATE SEQUENCE IF NOT EXISTS auction_stock_seq START 1")
    conn.execute("CREATE SEQUENCE IF NOT EXISTS auction_sector_seq START 1")
    conn.execute("CREATE SEQUENCE IF NOT EXISTS ff_stock_seq START 1")
    conn.execute("CREATE SEQUENCE IF NOT EXISTS ff_sector_seq START 1")
    conn.execute("CREATE SEQUENCE IF NOT EXISTS mf_fetch_log_seq START 1")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS auction_stock_daily (
            id INTEGER PRIMARY KEY DEFAULT nextval('auction_stock_seq'),
            date DATE NOT NULL,
            code VARCHAR NOT NULL,
            name VARCHAR NOT NULL,
            sector VARCHAR,
            prev_close DOUBLE,
            open_price DOUBLE,
            gap_pct DOUBLE,
            volume BIGINT,
            amount DOUBLE,
            turnover DOUBLE,
            rush_score DOUBLE,
            fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(date, code)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS auction_sector_daily (
            id INTEGER PRIMARY KEY DEFAULT nextval('auction_sector_seq'),
            date DATE NOT NULL,
            sector VARCHAR NOT NULL,
            stock_count INTEGER,
            avg_rush_score DOUBLE,
            max_rush_score DOUBLE,
            rush_stocks_count INTEGER,
            total_auction_amount DOUBLE,
            top_stocks VARCHAR,
            UNIQUE(date, sector)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS fund_flow_stock_daily (
            id INTEGER PRIMARY KEY DEFAULT nextval('ff_stock_seq'),
            date DATE NOT NULL,
            code VARCHAR NOT NULL,
            name VARCHAR NOT NULL,
            sector VARCHAR,
            latest_price DOUBLE,
            change_pct DOUBLE,
            main_inflow DOUBLE,
            main_inflow_pct DOUBLE,
            super_large_inflow DOUBLE,
            super_large_inflow_pct DOUBLE,
            large_inflow DOUBLE,
            large_inflow_pct DOUBLE,
            medium_inflow DOUBLE,
            medium_inflow_pct DOUBLE,
            small_inflow DOUBLE,
            small_inflow_pct DOUBLE,
            fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(date, code)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS fund_flow_sector_daily (
            id INTEGER PRIMARY KEY DEFAULT nextval('ff_sector_seq'),
            date DATE NOT NULL,
            sector_type VARCHAR NOT NULL,
            sector_name VARCHAR NOT NULL,
            change_pct DOUBLE,
            main_inflow DOUBLE,
            main_inflow_pct DOUBLE,
            super_large_inflow DOUBLE,
            super_large_inflow_pct DOUBLE,
            large_inflow DOUBLE,
            large_inflow_pct DOUBLE,
            medium_inflow DOUBLE,
            medium_inflow_pct DOUBLE,
            small_inflow DOUBLE,
            small_inflow_pct DOUBLE,
            top_stock VARCHAR,
            UNIQUE(date, sector_type, sector_name)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS fetch_log (
            id INTEGER PRIMARY KEY DEFAULT nextval('mf_fetch_log_seq'),
            fetch_date TEXT NOT NULL,
            source TEXT NOT NULL,
            items_checked INTEGER DEFAULT 0,
            new_items INTEGER DEFAULT 0,
            status TEXT DEFAULT 'ok',
            error_message TEXT,
            started_at TIMESTAMP,
            completed_at TIMESTAMP
        )
    """)

    conn.execute("CREATE INDEX IF NOT EXISTS idx_auc_stock_date ON auction_stock_daily(date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_auc_stock_code ON auction_stock_daily(code)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_auc_sector_date ON auction_sector_daily(date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ff_stock_date ON fund_flow_stock_daily(date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ff_stock_code ON fund_flow_stock_daily(code)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ff_sector_date ON fund_flow_sector_daily(date)")

    return conn


# ── Auction Stocks ───────────────────────────────────────────

def upsert_auction_stocks(conn: duckdb.DuckDBPyConnection, records: List[Dict[str, Any]]) -> int:
    """Batch upsert auction stock snapshots. Returns count."""
    if not records:
        return 0
    df = pd.DataFrame(records)
    needed = ["date", "code", "name", "sector", "prev_close", "open_price",
              "gap_pct", "volume", "amount", "turnover", "rush_score"]
    for col in needed:
        if col not in df.columns:
            df[col] = None
    sub = df[needed].copy()
    conn.register("_tmp_auc_stock", sub)
    rows = conn.execute("""
        INSERT INTO auction_stock_daily (date, code, name, sector, prev_close, open_price,
                                          gap_pct, volume, amount, turnover, rush_score)
        SELECT date, code, name, sector, prev_close, open_price,
               gap_pct, volume, amount, turnover, rush_score
        FROM _tmp_auc_stock
        ON CONFLICT (date, code) DO UPDATE SET
            name = excluded.name,
            sector = excluded.sector,
            prev_close = excluded.prev_close,
            open_price = excluded.open_price,
            gap_pct = excluded.gap_pct,
            volume = excluded.volume,
            amount = excluded.amount,
            turnover = excluded.turnover,
            rush_score = excluded.rush_score,
            fetched_at = now()
    """).fetchall()
    conn.unregister("_tmp_auc_stock")
    return rows[0][0] if rows else 0


def get_auction_stocks(
    conn: duckdb.DuckDBPyConnection,
    date: Optional[str] = None,
    min_gap: Optional[float] = None,
    min_score: Optional[float] = None,
    sector: Optional[str] = None,
    order_by: str = "rush_score",
    limit: int = 100,
) -> pd.DataFrame:
    """Query auction stocks with filters."""
    conditions = []
    params: List[Any] = []
    if date:
        conditions.append("date = ?")
        params.append(date)
    else:
        conditions.append("date = (SELECT MAX(date) FROM auction_stock_daily)")
    if min_gap is not None:
        conditions.append("gap_pct >= ?")
        params.append(min_gap)
    if min_score is not None:
        conditions.append("rush_score >= ?")
        params.append(min_score)
    if sector:
        conditions.append("sector = ?")
        params.append(sector)
    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    return conn.execute(f"""
        SELECT * FROM auction_stock_daily {where}
        ORDER BY {order_by} DESC LIMIT ?
    """, params + [limit]).df()


def get_auction_history(conn: duckdb.DuckDBPyConnection, code: str, days: int = 30) -> pd.DataFrame:
    """Get auction history for a stock."""
    return conn.execute("""
        SELECT * FROM auction_stock_daily
        WHERE code = ? ORDER BY date DESC LIMIT ?
    """, [code, days]).df()


# ── Auction Sectors ──────────────────────────────────────────

def upsert_auction_sectors(conn: duckdb.DuckDBPyConnection, records: List[Dict[str, Any]]) -> int:
    """Batch upsert sector auction aggregation. Returns count."""
    if not records:
        return 0
    df = pd.DataFrame(records)
    needed = ["date", "sector", "stock_count", "avg_rush_score", "max_rush_score",
              "rush_stocks_count", "total_auction_amount", "top_stocks"]
    for col in needed:
        if col not in df.columns:
            df[col] = None
    sub = df[needed].copy()
    if "top_stocks" in sub.columns:
        sub["top_stocks"] = sub["top_stocks"].apply(lambda x: ",".join(x) if isinstance(x, list) else str(x or ""))
    conn.register("_tmp_auc_sec", sub)
    rows = conn.execute("""
        INSERT INTO auction_sector_daily (date, sector, stock_count, avg_rush_score,
                                           max_rush_score, rush_stocks_count,
                                           total_auction_amount, top_stocks)
        SELECT date, sector, stock_count, avg_rush_score, max_rush_score,
               rush_stocks_count, total_auction_amount, top_stocks
        FROM _tmp_auc_sec
        ON CONFLICT (date, sector) DO UPDATE SET
            stock_count = excluded.stock_count,
            avg_rush_score = excluded.avg_rush_score,
            max_rush_score = excluded.max_rush_score,
            rush_stocks_count = excluded.rush_stocks_count,
            total_auction_amount = excluded.total_auction_amount,
            top_stocks = excluded.top_stocks
    """).fetchall()
    conn.unregister("_tmp_auc_sec")
    return rows[0][0] if rows else 0


def get_auction_sectors(
    conn: duckdb.DuckDBPyConnection,
    date: Optional[str] = None,
    limit: int = 50,
) -> pd.DataFrame:
    """Get auction sector rankings."""
    if date:
        return conn.execute("""
            SELECT * FROM auction_sector_daily WHERE date = ?
            ORDER BY avg_rush_score DESC LIMIT ?
        """, [date, limit]).df()
    return conn.execute("""
        SELECT * FROM auction_sector_daily
        WHERE date = (SELECT MAX(date) FROM auction_sector_daily)
        ORDER BY avg_rush_score DESC LIMIT ?
    """, [limit]).df()


# ── Fund Flow Stocks ─────────────────────────────────────────

def upsert_fund_flow_stocks(conn: duckdb.DuckDBPyConnection, records: List[Dict[str, Any]]) -> int:
    """Batch upsert stock fund flow data. Returns count."""
    if not records:
        return 0
    df = pd.DataFrame(records)
    needed = ["date", "code", "name", "sector", "latest_price", "change_pct",
              "main_inflow", "main_inflow_pct",
              "super_large_inflow", "super_large_inflow_pct",
              "large_inflow", "large_inflow_pct",
              "medium_inflow", "medium_inflow_pct",
              "small_inflow", "small_inflow_pct"]
    for col in needed:
        if col not in df.columns:
            df[col] = None
    sub = df[needed].copy()
    conn.register("_tmp_ff_stock", sub)
    rows = conn.execute("""
        INSERT INTO fund_flow_stock_daily (date, code, name, sector, latest_price, change_pct,
                                            main_inflow, main_inflow_pct,
                                            super_large_inflow, super_large_inflow_pct,
                                            large_inflow, large_inflow_pct,
                                            medium_inflow, medium_inflow_pct,
                                            small_inflow, small_inflow_pct)
        SELECT date, code, name, sector, latest_price, change_pct,
               main_inflow, main_inflow_pct,
               super_large_inflow, super_large_inflow_pct,
               large_inflow, large_inflow_pct,
               medium_inflow, medium_inflow_pct,
               small_inflow, small_inflow_pct
        FROM _tmp_ff_stock
        ON CONFLICT (date, code) DO UPDATE SET
            name = excluded.name,
            sector = excluded.sector,
            latest_price = excluded.latest_price,
            change_pct = excluded.change_pct,
            main_inflow = excluded.main_inflow,
            main_inflow_pct = excluded.main_inflow_pct,
            super_large_inflow = excluded.super_large_inflow,
            super_large_inflow_pct = excluded.super_large_inflow_pct,
            large_inflow = excluded.large_inflow,
            large_inflow_pct = excluded.large_inflow_pct,
            medium_inflow = excluded.medium_inflow,
            medium_inflow_pct = excluded.medium_inflow_pct,
            small_inflow = excluded.small_inflow,
            small_inflow_pct = excluded.small_inflow_pct,
            fetched_at = now()
    """).fetchall()
    conn.unregister("_tmp_ff_stock")
    return rows[0][0] if rows else 0


def get_fund_flow_stocks(
    conn: duckdb.DuckDBPyConnection,
    date: Optional[str] = None,
    direction: str = "all",
    sector: Optional[str] = None,
    min_amount: Optional[float] = None,
    order_by: str = "main_inflow",
    limit: int = 100,
) -> pd.DataFrame:
    """Query stock fund flow rankings."""
    conditions = []
    params: List[Any] = []
    if date:
        conditions.append("date = ?")
        params.append(date)
    else:
        conditions.append("date = (SELECT MAX(date) FROM fund_flow_stock_daily)")
    if direction == "inflow":
        conditions.append("main_inflow > 0")
    elif direction == "outflow":
        conditions.append("main_inflow < 0")
    if sector:
        conditions.append("sector = ?")
        params.append(sector)
    if min_amount:
        conditions.append("ABS(main_inflow) >= ?")
        params.append(min_amount)
    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    order_clause = f"ORDER BY {order_by} {'DESC' if direction != 'outflow' else 'ASC'}"
    return conn.execute(f"""
        SELECT * FROM fund_flow_stock_daily {where}
        {order_clause} LIMIT ?
    """, params + [limit]).df()


def get_fund_flow_stock_history(conn: duckdb.DuckDBPyConnection, code: str,
                                 days: int = 30) -> pd.DataFrame:
    """Get fund flow history for a stock."""
    return conn.execute("""
        SELECT * FROM fund_flow_stock_daily
        WHERE code = ? ORDER BY date DESC LIMIT ?
    """, [code, days]).df()


# ── Fund Flow Sectors ────────────────────────────────────────

def upsert_fund_flow_sectors(conn: duckdb.DuckDBPyConnection, records: List[Dict[str, Any]]) -> int:
    """Batch upsert sector fund flow data. Returns count."""
    if not records:
        return 0
    df = pd.DataFrame(records)
    needed = ["date", "sector_type", "sector_name", "change_pct",
              "main_inflow", "main_inflow_pct",
              "super_large_inflow", "super_large_inflow_pct",
              "large_inflow", "large_inflow_pct",
              "medium_inflow", "medium_inflow_pct",
              "small_inflow", "small_inflow_pct", "top_stock"]
    for col in needed:
        if col not in df.columns:
            df[col] = None
    sub = df[needed].copy()
    conn.register("_tmp_ff_sec", sub)
    rows = conn.execute("""
        INSERT INTO fund_flow_sector_daily (date, sector_type, sector_name, change_pct,
                                             main_inflow, main_inflow_pct,
                                             super_large_inflow, super_large_inflow_pct,
                                             large_inflow, large_inflow_pct,
                                             medium_inflow, medium_inflow_pct,
                                             small_inflow, small_inflow_pct, top_stock)
        SELECT date, sector_type, sector_name, change_pct,
               main_inflow, main_inflow_pct,
               super_large_inflow, super_large_inflow_pct,
               large_inflow, large_inflow_pct,
               medium_inflow, medium_inflow_pct,
               small_inflow, small_inflow_pct, top_stock
        FROM _tmp_ff_sec
        ON CONFLICT (date, sector_type, sector_name) DO UPDATE SET
            change_pct = excluded.change_pct,
            main_inflow = excluded.main_inflow,
            main_inflow_pct = excluded.main_inflow_pct,
            super_large_inflow = excluded.super_large_inflow,
            super_large_inflow_pct = excluded.super_large_inflow_pct,
            large_inflow = excluded.large_inflow,
            large_inflow_pct = excluded.large_inflow_pct,
            medium_inflow = excluded.medium_inflow,
            medium_inflow_pct = excluded.medium_inflow_pct,
            small_inflow = excluded.small_inflow,
            small_inflow_pct = excluded.small_inflow_pct,
            top_stock = excluded.top_stock
    """).fetchall()
    conn.unregister("_tmp_ff_sec")
    return rows[0][0] if rows else 0


def get_fund_flow_sectors(
    conn: duckdb.DuckDBPyConnection,
    date: Optional[str] = None,
    sector_type: str = "行业资金流",
    direction: str = "all",
    limit: int = 50,
) -> pd.DataFrame:
    """Query sector fund flow rankings."""
    conditions = ["sector_type = ?"]
    params: List[Any] = [sector_type]
    if date:
        conditions.append("date = ?")
        params.append(date)
    else:
        conditions.append("date = (SELECT MAX(date) FROM fund_flow_sector_daily)")
    if direction == "inflow":
        conditions.append("main_inflow > 0")
    elif direction == "outflow":
        conditions.append("main_inflow < 0")
    where = "WHERE " + " AND ".join(conditions)
    order_clause = "ORDER BY main_inflow DESC" if direction != "outflow" else "ORDER BY main_inflow ASC"
    return conn.execute(f"""
        SELECT * FROM fund_flow_sector_daily {where}
        {order_clause} LIMIT ?
    """, params + [limit]).df()


def get_fund_flow_sector_history(conn: duckdb.DuckDBPyConnection,
                                  sector_name: str, sector_type: str = "行业资金流",
                                  days: int = 30) -> pd.DataFrame:
    """Get fund flow history for a sector."""
    return conn.execute("""
        SELECT * FROM fund_flow_sector_daily
        WHERE sector_name = ? AND sector_type = ?
        ORDER BY date DESC LIMIT ?
    """, [sector_name, sector_type, days]).df()


# ── Fetch Log ─────────────────────────────────────────────────

def log_fetch_start(conn: duckdb.DuckDBPyConnection, source: str) -> int:
    """Log fetch start. Returns log id."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    row = conn.execute("""
        INSERT INTO fetch_log (fetch_date, source, started_at, status)
        VALUES (CURRENT_DATE, ?, ?, 'running')
        RETURNING id
    """, [source, now]).fetchone()
    return row[0] if row else 0


def log_fetch_end(conn: duckdb.DuckDBPyConnection, log_id: int, items_checked: int = 0,
                  new_items: int = 0, error: Optional[str] = None) -> None:
    """Mark fetch as completed."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    status = "error" if error else "ok"
    conn.execute("""
        UPDATE fetch_log SET items_checked = ?, new_items = ?, status = ?,
        error_message = ?, completed_at = ?
        WHERE id = ?
    """, [items_checked, new_items, status, error, now, log_id])


# ── Stats ─────────────────────────────────────────────────────

def get_stats(conn: duckdb.DuckDBPyConnection) -> Dict[str, Any]:
    """Get database statistics."""
    auc_days = conn.execute("SELECT COUNT(DISTINCT date) FROM auction_stock_daily").fetchone()
    auc_stocks = conn.execute("SELECT COUNT(*) FROM auction_stock_daily").fetchone()
    ff_days = conn.execute("SELECT COUNT(DISTINCT date) FROM fund_flow_stock_daily").fetchone()
    ff_stocks = conn.execute("SELECT COUNT(*) FROM fund_flow_stock_daily").fetchone()
    ff_sec_days = conn.execute("SELECT COUNT(DISTINCT date) FROM fund_flow_sector_daily").fetchone()
    return {
        "auction_days": int(auc_days[0]) if auc_days else 0,
        "auction_stock_records": int(auc_stocks[0]) if auc_stocks else 0,
        "fund_flow_days": int(ff_days[0]) if ff_days else 0,
        "fund_flow_stock_records": int(ff_stocks[0]) if ff_stocks else 0,
        "fund_flow_sector_days": int(ff_sec_days[0]) if ff_sec_days else 0,
    }
