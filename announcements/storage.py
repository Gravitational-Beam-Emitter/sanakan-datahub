"""
Storage layer — DuckDB-backed store for company announcements data.

Tables:
  announcements — multi-market filings and announcements with metadata + text
  fetch_log    — daily fetch audit log
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List, Optional

import duckdb
import pandas as pd

from announcements.config import DB_PATH, LOOKBACK_DAYS


def _conn(db_path: Optional[str] = None, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    path = db_path or DB_PATH
    return duckdb.connect(path, read_only=read_only)


def init_db(db_path: Optional[str] = None, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """Create tables and indexes if they don't exist. Returns connection."""
    conn = _conn(db_path, read_only=read_only)
    if read_only:
        return conn

    conn.execute("CREATE SEQUENCE IF NOT EXISTS announcements_seq START 1")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS announcements (
            id INTEGER PRIMARY KEY DEFAULT nextval('announcements_seq'),
            ticker VARCHAR NOT NULL,
            market VARCHAR NOT NULL,
            company_name VARCHAR NOT NULL,
            title VARCHAR,
            announcement_date DATE NOT NULL,
            source VARCHAR NOT NULL,
            filing_type VARCHAR,
            source_url VARCHAR,
            local_file_path VARCHAR,
            text_content TEXT,
            file_type VARCHAR,
            created_at TIMESTAMP DEFAULT now(),
            UNIQUE(ticker, market, source, filing_type, announcement_date, title)
        )
    """)

    conn.execute("CREATE SEQUENCE IF NOT EXISTS ann_fetch_log_seq START 1")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fetch_log (
            id INTEGER PRIMARY KEY DEFAULT nextval('ann_fetch_log_seq'),
            fetch_date DATE NOT NULL,
            source VARCHAR DEFAULT 'announcements',
            items_checked INTEGER DEFAULT 0,
            new_items INTEGER DEFAULT 0,
            status VARCHAR DEFAULT 'ok',
            error_message TEXT,
            started_at TIMESTAMP DEFAULT now(),
            completed_at TIMESTAMP
        )
    """)

    conn.execute("CREATE INDEX IF NOT EXISTS idx_ann_date ON announcements(announcement_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ann_ticker ON announcements(ticker)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ann_market ON announcements(market)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ann_source ON announcements(source)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fl_date ON fetch_log(fetch_date)")

    return conn


# ── Announcements CRUD ──

def upsert_announcements(conn: duckdb.DuckDBPyConnection, records: List[Dict[str, Any]]) -> int:
    """Batch insert/update announcements. Returns row count."""
    if not records:
        return 0

    df = pd.DataFrame(records)
    needed = [
        "ticker", "market", "company_name", "title",
        "announcement_date", "source", "filing_type",
        "source_url", "local_file_path", "text_content", "file_type",
    ]
    for col in needed:
        if col not in df.columns:
            df[col] = None

    sub = df[needed].copy()
    conn.register("_tmp_ann", sub)
    try:
        rows = conn.execute("""
            INSERT INTO announcements (
                ticker, market, company_name, title,
                announcement_date, source, filing_type,
                source_url, local_file_path, text_content, file_type
            )
            SELECT
                ticker, market, company_name, title,
                announcement_date, source, filing_type,
                source_url, local_file_path, text_content, file_type
            FROM _tmp_ann
            ON CONFLICT (ticker, market, source, filing_type, announcement_date, title)
            DO UPDATE SET
                company_name = excluded.company_name,
                source_url = excluded.source_url,
                local_file_path = excluded.local_file_path,
                text_content = excluded.text_content,
                file_type = excluded.file_type,
                created_at = now()
        """).fetchall()
    finally:
        conn.unregister("_tmp_ann")
    return rows[0][0] if rows else 0


def query_announcements(
    conn: duckdb.DuckDBPyConnection,
    ticker: Optional[str] = None,
    market: Optional[str] = None,
    source: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    limit: int = 100,
) -> pd.DataFrame:
    """Filtered query for announcements (without text_content for list views)."""
    where = ["1=1"]
    params: list = []

    if ticker:
        where.append("ticker = ?")
        params.append(ticker)
    if market:
        where.append("market = ?")
        params.append(market)
    if source:
        where.append("source = ?")
        params.append(source)
    if start:
        where.append("announcement_date >= ?")
        params.append(start)
    if end:
        where.append("announcement_date <= ?")
        params.append(end)

    sql = f"""
        SELECT id, ticker, market, company_name, title,
               announcement_date, source, filing_type,
               source_url, local_file_path, file_type, created_at
        FROM announcements
        WHERE {' AND '.join(where)}
        ORDER BY announcement_date DESC, ticker
        LIMIT ?
    """
    params.append(limit)
    return conn.execute(sql, params).df()


def get_announcement_by_id(conn: duckdb.DuckDBPyConnection, ann_id: int) -> Optional[Dict[str, Any]]:
    """Single announcement with full text_content."""
    row = conn.execute(
        "SELECT * FROM announcements WHERE id = ?", [ann_id]
    ).fetchone()
    if not row:
        return None
    cols = [desc[0] for desc in conn.description]
    return dict(zip(cols, row))


def get_tracked_companies(conn: duckdb.DuckDBPyConnection) -> List[Dict[str, Any]]:
    """Return distinct tracked companies with announcement counts."""
    rows = conn.execute("""
        SELECT ticker, market, company_name, COUNT(*) AS announcement_count
        FROM announcements
        GROUP BY ticker, market, company_name
        ORDER BY market, ticker
    """).fetchall()
    return [
        {
            "ticker": str(r[0]),
            "market": str(r[1]),
            "company_name": str(r[2]),
            "announcement_count": int(r[3]),
        }
        for r in rows
    ]


def get_announcement_dates(conn: duckdb.DuckDBPyConnection, limit: int = 30) -> List[str]:
    """Distinct announcement dates, newest first."""
    rows = conn.execute(
        "SELECT DISTINCT announcement_date FROM announcements ORDER BY announcement_date DESC LIMIT ?",
        [limit],
    ).fetchall()
    return [str(r[0]) for r in rows]


def get_announcement_count(conn: duckdb.DuckDBPyConnection) -> int:
    """Total announcement count."""
    row = conn.execute("SELECT COUNT(*) FROM announcements").fetchone()
    return int(row[0]) if row else 0


def get_counts_by_market(conn: duckdb.DuckDBPyConnection) -> Dict[str, int]:
    """Announcement counts by market."""
    rows = conn.execute(
        "SELECT market, COUNT(*) FROM announcements GROUP BY market"
    ).fetchall()
    return {str(r[0]): int(r[1]) for r in rows}


def cleanup_old_records(conn: duckdb.DuckDBPyConnection, retention_days: int = LOOKBACK_DAYS) -> int:
    """Delete announcements older than retention_days. Returns count deleted."""
    cutoff = date.today() - timedelta(days=retention_days)
    rows = conn.execute(
        "DELETE FROM announcements WHERE announcement_date < ?",
        [cutoff.isoformat()],
    ).fetchall()
    count = rows[0][0] if rows else 0
    if count:
        conn.execute("CHECKPOINT")
    return count


# ── Fetch Log ──

def log_fetch_start(conn: duckdb.DuckDBPyConnection, fetch_date: str, source: str = "announcements") -> int:
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
        conn.execute(
            """INSERT INTO fetch_log (fetch_date, source, status, started_at)
               VALUES (?, ?, 'running', now())""",
            [fetch_date, source],
        )
        row = conn.execute("SELECT MAX(id) FROM fetch_log").fetchone()
        return int(row[0]) if row and row[0] else -1


def log_fetch_end(
    conn: duckdb.DuckDBPyConnection, log_id: int,
    items_checked: int = 0, new_items: int = 0,
    status: str = "ok", error: str = "",
) -> None:
    """Update fetch log with completion status."""
    conn.execute(
        """UPDATE fetch_log
           SET items_checked = ?, new_items = ?, status = ?,
               error_message = ?, completed_at = now()
           WHERE id = ?""",
        [items_checked, new_items, status, error or None, log_id],
    )


def get_fetch_status(conn: duckdb.DuckDBPyConnection, days: int = 7) -> pd.DataFrame:
    """Get recent fetch log entries."""
    return conn.execute("""
        SELECT fetch_date, source, items_checked, new_items,
               status, started_at, completed_at
        FROM fetch_log
        ORDER BY fetch_date DESC
        LIMIT ?
    """, [days]).df()
