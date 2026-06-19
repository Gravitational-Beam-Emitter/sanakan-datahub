"""
Storage layer — DuckDB-backed store for US corporate actions data.

Tables:
  corporate_actions — SEC 8-K filings classified by corporate action type
  listed_companies   — CIK-to-ticker mapping for US-listed companies
  fetch_log          — daily fetch audit log
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import duckdb
import pandas as pd

from us_corp_actions.config import DB_PATH, LOOKBACK_DAYS


def _conn(db_path: Optional[str] = None, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    path = db_path or DB_PATH
    return duckdb.connect(path, read_only=read_only)


def init_db(db_path: Optional[str] = None, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """Create tables and indexes if they don't exist. Returns connection."""
    conn = _conn(db_path, read_only=read_only)
    if read_only:
        return conn

    conn.execute("""
        CREATE TABLE IF NOT EXISTS corporate_actions (
            filing_date DATE NOT NULL,
            cik VARCHAR NOT NULL,
            ticker VARCHAR NOT NULL,
            company_name VARCHAR NOT NULL,
            form_type VARCHAR NOT NULL,
            action_type VARCHAR NOT NULL,
            action_subtype VARCHAR,
            item_numbers VARCHAR,
            effective_date DATE,
            record_date DATE,
            pay_date DATE,
            description TEXT,
            source_url VARCHAR,
            fetched_at TIMESTAMP DEFAULT now(),
            PRIMARY KEY(filing_date, cik, form_type)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS listed_companies (
            cik VARCHAR PRIMARY KEY,
            ticker VARCHAR NOT NULL,
            company_name VARCHAR NOT NULL,
            exchange VARCHAR,
            is_active BOOLEAN DEFAULT true,
            updated_at TIMESTAMP DEFAULT now()
        )
    """)

    conn.execute("CREATE SEQUENCE IF NOT EXISTS fetch_log_seq START 1")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fetch_log (
            id INTEGER PRIMARY KEY DEFAULT nextval('fetch_log_seq'),
            fetch_date DATE NOT NULL,
            source VARCHAR DEFAULT 'SEC_EDGAR',
            filings_checked INTEGER DEFAULT 0,
            new_actions INTEGER DEFAULT 0,
            status VARCHAR DEFAULT 'ok',
            error_message TEXT,
            started_at TIMESTAMP DEFAULT now(),
            completed_at TIMESTAMP
        )
    """)

    # Indexes
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ca_date ON corporate_actions(filing_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ca_ticker ON corporate_actions(ticker)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ca_type ON corporate_actions(action_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ca_effective ON corporate_actions(effective_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_lc_ticker ON listed_companies(ticker)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fl_date ON fetch_log(fetch_date)")

    return conn


# ── Listed Companies (CIK ↔ Ticker mapping) ──

def upsert_listed_companies(conn: duckdb.DuckDBPyConnection, companies: List[Dict[str, str]]) -> int:
    """Batch upsert listed companies. Returns row count."""
    if not companies:
        return 0
    df = pd.DataFrame(companies)
    conn.register("_tmp_lc", df)
    try:
        rows = conn.execute("""
            INSERT INTO listed_companies (cik, ticker, company_name, exchange, updated_at)
            SELECT cik, ticker, company_name, exchange, now()
            FROM _tmp_lc
            ON CONFLICT (cik) DO UPDATE SET
                ticker = excluded.ticker,
                company_name = excluded.company_name,
                exchange = excluded.exchange,
                updated_at = now()
        """).fetchall()
    finally:
        conn.unregister("_tmp_lc")
    return rows[0][0] if rows else 0


def get_ticker_for_cik(conn: duckdb.DuckDBPyConnection, cik: str) -> Optional[str]:
    """Look up ticker by CIK."""
    row = conn.execute(
        "SELECT ticker FROM listed_companies WHERE cik = ?", [cik]
    ).fetchone()
    return str(row[0]) if row else None


def get_company_name_for_cik(conn: duckdb.DuckDBPyConnection, cik: str) -> Optional[str]:
    """Look up company name by CIK."""
    row = conn.execute(
        "SELECT company_name FROM listed_companies WHERE cik = ?", [cik]
    ).fetchone()
    return str(row[0]) if row else None


# ── Corporate Actions ──

def upsert_corporate_actions(conn: duckdb.DuckDBPyConnection, actions: List[Dict[str, Any]]) -> int:
    """Batch insert/update corporate actions. Returns row count."""
    if not actions:
        return 0

    df = pd.DataFrame(actions)
    needed = [
        "filing_date", "cik", "ticker", "company_name", "form_type",
        "action_type", "action_subtype", "item_numbers",
        "effective_date", "record_date", "pay_date",
        "description", "source_url",
    ]
    for col in needed:
        if col not in df.columns:
            df[col] = None

    sub = df[needed].copy()
    conn.register("_tmp_ca", sub)
    try:
        rows = conn.execute("""
            INSERT INTO corporate_actions (
                filing_date, cik, ticker, company_name, form_type,
                action_type, action_subtype, item_numbers,
                effective_date, record_date, pay_date,
                description, source_url
            )
            SELECT
                filing_date, cik, ticker, company_name, form_type,
                action_type, action_subtype, item_numbers,
                effective_date, record_date, pay_date,
                description, source_url
            FROM _tmp_ca
            ON CONFLICT (filing_date, cik, form_type) DO UPDATE SET
                ticker = excluded.ticker,
                company_name = excluded.company_name,
                action_type = excluded.action_type,
                action_subtype = excluded.action_subtype,
                item_numbers = excluded.item_numbers,
                effective_date = excluded.effective_date,
                record_date = excluded.record_date,
                pay_date = excluded.pay_date,
                description = excluded.description,
                source_url = excluded.source_url,
                fetched_at = now()
        """).fetchall()
    finally:
        conn.unregister("_tmp_ca")
    return rows[0][0] if rows else 0


# ── Fetch Log ──

def log_fetch_start(conn: duckdb.DuckDBPyConnection, fetch_date: str, source: str = "SEC_EDGAR") -> int:
    """Record a fetch attempt start. Returns log id."""
    try:
        row = conn.execute(
            """INSERT INTO fetch_log (fetch_date, source, status, started_at)
               VALUES (?, ?, 'running', now())
               RETURNING id""",
            [fetch_date, source],
        ).fetchone()
        return int(row[0]) if row else -1
    except Exception:
        # DuckDB may not support RETURNING — fall back to max(id)
        conn.execute(
            """INSERT INTO fetch_log (fetch_date, source, status, started_at)
               VALUES (?, ?, 'running', now())""",
            [fetch_date, source],
        )
        row = conn.execute("SELECT MAX(id) FROM fetch_log").fetchone()
        return int(row[0]) if row and row[0] else -1


def log_fetch_end(conn: duckdb.DuckDBPyConnection, log_id: int,
                  filings_checked: int = 0, new_actions: int = 0,
                  status: str = "ok", error: str = "") -> None:
    """Update fetch log with completion status."""
    conn.execute(
        """UPDATE fetch_log
           SET filings_checked = ?, new_actions = ?, status = ?,
               error_message = ?, completed_at = now()
           WHERE id = ?""",
        [filings_checked, new_actions, status, error or None, log_id],
    )


# ── Cleanup ──

def cleanup_old_records(conn: duckdb.DuckDBPyConnection, retention_days: int = LOOKBACK_DAYS) -> int:
    """Delete corporate actions older than retention_days. Returns count deleted."""
    cutoff = date.today() - timedelta(days=retention_days)
    rows = conn.execute(
        "DELETE FROM corporate_actions WHERE filing_date < ?",
        [cutoff.isoformat()],
    ).fetchall()
    count = rows[0][0] if rows else 0
    if count:
        conn.execute("CHECKPOINT")  # Compact DuckDB file
    return count


# ── Query helpers ──

def get_actions_by_date(conn: duckdb.DuckDBPyConnection, filing_date: str) -> pd.DataFrame:
    """Get all corporate actions for a filing date."""
    return conn.execute("""
        SELECT * FROM corporate_actions
        WHERE filing_date = ?
        ORDER BY action_type, ticker
    """, [filing_date]).df()


def get_actions_by_ticker(conn: duckdb.DuckDBPyConnection, ticker: str, limit: int = 50) -> pd.DataFrame:
    """Get corporate action history for a ticker."""
    return conn.execute("""
        SELECT * FROM corporate_actions
        WHERE ticker = ?
        ORDER BY filing_date DESC
        LIMIT ?
    """, [ticker.upper(), limit]).df()


def get_action_summary(conn: duckdb.DuckDBPyConnection,
                       start_date: str, end_date: str) -> pd.DataFrame:
    """Get action type counts by date."""
    return conn.execute("""
        SELECT filing_date, action_type, COUNT(*) AS cnt
        FROM corporate_actions
        WHERE filing_date >= ? AND filing_date <= ?
        GROUP BY filing_date, action_type
        ORDER BY filing_date DESC, cnt DESC
    """, [start_date, end_date]).df()


def get_recent_actions(conn: duckdb.DuckDBPyConnection, days: int = 7) -> pd.DataFrame:
    """Get all actions from the last N days."""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    return conn.execute("""
        SELECT filing_date, ticker, company_name, action_type, action_subtype, description
        FROM corporate_actions
        WHERE filing_date >= ?
        ORDER BY filing_date DESC, action_type
    """, [cutoff]).df()


def get_last_fetch_date(conn: duckdb.DuckDBPyConnection) -> Optional[str]:
    """Get the most recent filing date in the database."""
    row = conn.execute(
        "SELECT MAX(filing_date) FROM corporate_actions"
    ).fetchone()
    return str(row[0]) if row and row[0] else None


def get_ticker_count(conn: duckdb.DuckDBPyConnection) -> int:
    """Get count of listed companies."""
    row = conn.execute("SELECT COUNT(*) FROM listed_companies").fetchone()
    return int(row[0]) if row else 0


def get_action_count(conn: duckdb.DuckDBPyConnection) -> int:
    """Get total corporate actions count."""
    row = conn.execute("SELECT COUNT(*) FROM corporate_actions").fetchone()
    return int(row[0]) if row else 0


def get_available_action_dates(conn: duckdb.DuckDBPyConnection, limit: int = 30) -> List[str]:
    """Get list of dates with corporate actions data."""
    rows = conn.execute("""
        SELECT DISTINCT filing_date FROM corporate_actions
        ORDER BY filing_date DESC LIMIT ?
    """, [limit]).fetchall()
    return [str(r[0]) for r in rows]


def get_type_breakdown(conn: duckdb.DuckDBPyConnection,
                       date_str: str) -> pd.DataFrame:
    """Get action type breakdown for a specific date."""
    return conn.execute("""
        SELECT action_type, COUNT(*) AS cnt,
               COUNT(DISTINCT ticker) AS companies
        FROM corporate_actions
        WHERE filing_date = ?
        GROUP BY action_type
        ORDER BY cnt DESC
    """, [date_str]).df()


def get_daily_summary(conn: duckdb.DuckDBPyConnection, date_str: str) -> Dict[str, Any]:
    """Get a summary for a filing date."""
    row = conn.execute("""
        SELECT
            COUNT(*) AS total,
            COUNT(DISTINCT ticker) AS companies,
            COUNT(DISTINCT action_type) AS type_count
        FROM corporate_actions
        WHERE filing_date = ?
    """, [date_str]).fetchone()
    if row is None or row[0] == 0:
        return {"date": date_str, "total": 0}
    return {
        "date": date_str,
        "total": int(row[0]),
        "companies": int(row[1]),
        "type_count": int(row[2]),
    }


def get_fetch_status(conn: duckdb.DuckDBPyConnection, days: int = 7) -> pd.DataFrame:
    """Get recent fetch log entries."""
    return conn.execute("""
        SELECT fetch_date, source, filings_checked, new_actions,
               status, started_at, completed_at
        FROM fetch_log
        ORDER BY fetch_date DESC
        LIMIT ?
    """, [days]).df()
