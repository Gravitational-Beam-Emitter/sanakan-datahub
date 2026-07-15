"""
Storage layer — DuckDB-backed store for Taiwan stock data.

Tables:
  tw_listed_stocks       — Master list of TWSE/TPEx stocks
  tw_daily_prices        — Daily OHLCV for all stocks
  tw_market_indices      — TAIEX (^TWII), TPEx (^TWOII) index data
  tw_significant_movers  — Stocks with ±10%+ daily moves
  tw_stock_reasons       — LLM-generated reason tags per stock
  tw_daily_narratives    — LLM-generated market narratives per day
  tw_fetch_log           — Fetch audit trail
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import duckdb
import pandas as pd

from tw_stock.config import DB_PATH


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
    """Create tables and indexes if they don't exist. Returns connection."""
    conn = _conn(db_path, read_only=read_only)
    if read_only:
        return conn

    # 1. Listed stocks master
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tw_listed_stocks (
            code VARCHAR NOT NULL,
            name VARCHAR NOT NULL,
            name_en VARCHAR,
            market VARCHAR NOT NULL,
            sector VARCHAR,
            industry VARCHAR,
            listing_date DATE,
            shares_outstanding BIGINT,
            market_cap DOUBLE,
            is_active BOOLEAN DEFAULT true,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(code)
        )
    """)

    # 2. Daily OHLCV
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tw_daily_prices (
            date DATE NOT NULL,
            code VARCHAR NOT NULL,
            open DOUBLE,
            high DOUBLE,
            low DOUBLE,
            close DOUBLE,
            volume BIGINT,
            change_pct DOUBLE,
            PRIMARY KEY(date, code)
        )
    """)

    # 3. Market indices
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tw_market_indices (
            date DATE NOT NULL,
            index_code VARCHAR NOT NULL,
            index_name VARCHAR,
            open DOUBLE,
            high DOUBLE,
            low DOUBLE,
            close DOUBLE,
            volume DOUBLE,
            change_pct DOUBLE,
            PRIMARY KEY(date, index_code)
        )
    """)

    # 4. Significant movers (threshold: ±10%)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tw_significant_movers (
            date DATE NOT NULL,
            code VARCHAR NOT NULL,
            name VARCHAR NOT NULL,
            change_pct DOUBLE,
            volume BIGINT,
            close DOUBLE,
            market VARCHAR,
            sector VARCHAR,
            industry VARCHAR,
            PRIMARY KEY(date, code)
        )
    """)

    # 5. Stock LLM reasons
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tw_stock_reasons (
            date DATE NOT NULL,
            code VARCHAR NOT NULL,
            reasons VARCHAR NOT NULL,
            generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(date, code)
        )
    """)

    # 6. Daily market narratives
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tw_daily_narratives (
            date DATE NOT NULL,
            tag VARCHAR NOT NULL DEFAULT '',
            name VARCHAR NOT NULL,
            description TEXT,
            stocks_json VARCHAR NOT NULL DEFAULT '[]',
            generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(date, name)
        )
    """)

    # 7. Fetch log
    conn.execute("CREATE SEQUENCE IF NOT EXISTS seq_tw_fetch_log")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tw_fetch_log (
            id INTEGER PRIMARY KEY DEFAULT nextval('seq_tw_fetch_log'),
            fetch_date DATE NOT NULL,
            status VARCHAR NOT NULL DEFAULT 'success',
            listings_count INTEGER DEFAULT 0,
            prices_count INTEGER DEFAULT 0,
            movers_count INTEGER DEFAULT 0,
            tagged INTEGER DEFAULT 0,
            narratives INTEGER DEFAULT 0,
            errors TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        )
    """)

    # Indexes
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tls_market ON tw_listed_stocks(market)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tls_sector ON tw_listed_stocks(sector)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tdp_date ON tw_daily_prices(date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tdp_code ON tw_daily_prices(code)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tmi_date ON tw_market_indices(date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tsm_date ON tw_significant_movers(date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tsr_date ON tw_stock_reasons(date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tdn_date ON tw_daily_narratives(date)")

    return conn


# ═══════════════════════════════════════════════════════════════
#  UPSERT helpers
# ═══════════════════════════════════════════════════════════════

def upsert_listed_stocks(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> int:
    """Batch insert/update listed stocks. Returns row count."""
    if df.empty:
        return 0
    needed = ["code", "name", "name_en", "market", "sector", "industry",
              "listing_date", "shares_outstanding", "market_cap"]
    for col in needed:
        if col not in df.columns:
            df[col] = None
    sub = df[needed].copy()
    conn.register("_tmp_tls", sub)
    rows = conn.execute("""
        INSERT INTO tw_listed_stocks (code, name, name_en, market, sector, industry,
                                       listing_date, shares_outstanding, market_cap)
        SELECT code, name, name_en, market, sector, industry,
               listing_date, shares_outstanding, market_cap
        FROM _tmp_tls
        ON CONFLICT (code) DO UPDATE SET
            name = excluded.name,
            name_en = COALESCE(excluded.name_en, tw_listed_stocks.name_en),
            market = excluded.market,
            sector = excluded.sector,
            industry = excluded.industry,
            listing_date = excluded.listing_date,
            shares_outstanding = excluded.shares_outstanding,
            market_cap = excluded.market_cap,
            updated_at = now()
    """).fetchall()
    conn.unregister("_tmp_tls")
    return rows[0][0] if rows else 0


def upsert_daily_prices(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> int:
    """Batch insert/update daily OHLCV. Returns row count."""
    if df.empty:
        return 0
    needed = ["date", "code", "open", "high", "low", "close", "volume", "change_pct"]
    for col in needed:
        if col not in df.columns:
            df[col] = None
    sub = df[needed].copy()
    conn.register("_tmp_tdp", sub)
    rows = conn.execute("""
        INSERT INTO tw_daily_prices (date, code, open, high, low, close, volume, change_pct)
        SELECT date, code, open, high, low, close, volume, change_pct
        FROM _tmp_tdp
        ON CONFLICT (date, code) DO UPDATE SET
            open = excluded.open,
            high = excluded.high,
            low = excluded.low,
            close = excluded.close,
            volume = excluded.volume,
            change_pct = excluded.change_pct
    """).fetchall()
    conn.unregister("_tmp_tdp")
    return rows[0][0] if rows else 0


def upsert_market_indices(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> int:
    """Batch insert/update market index data. Returns row count."""
    if df.empty:
        return 0
    needed = ["date", "index_code", "index_name", "open", "high", "low", "close", "volume", "change_pct"]
    for col in needed:
        if col not in df.columns:
            df[col] = None
    sub = df[needed].copy()
    conn.register("_tmp_tmi", sub)
    rows = conn.execute("""
        INSERT INTO tw_market_indices (date, index_code, index_name, open, high, low, close, volume, change_pct)
        SELECT date, index_code, index_name, open, high, low, close, volume, change_pct
        FROM _tmp_tmi
        ON CONFLICT (date, index_code) DO UPDATE SET
            index_name = excluded.index_name,
            open = excluded.open,
            high = excluded.high,
            low = excluded.low,
            close = excluded.close,
            volume = excluded.volume,
            change_pct = excluded.change_pct
    """).fetchall()
    conn.unregister("_tmp_tmi")
    return rows[0][0] if rows else 0


def upsert_significant_movers(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> int:
    """Insert/update significant movers. Returns row count."""
    if df.empty:
        return 0
    needed = ["date", "code", "name", "change_pct", "volume", "close", "market", "sector", "industry"]
    for col in needed:
        if col not in df.columns:
            df[col] = None
    sub = df[needed].copy()
    conn.register("_tmp_tsm", sub)
    rows = conn.execute("""
        INSERT INTO tw_significant_movers (date, code, name, change_pct, volume, close, market, sector, industry)
        SELECT date, code, name, change_pct, volume, close, market, sector, industry
        FROM _tmp_tsm
        ON CONFLICT (date, code) DO UPDATE SET
            name = excluded.name,
            change_pct = excluded.change_pct,
            volume = excluded.volume,
            close = excluded.close,
            market = excluded.market,
            sector = excluded.sector,
            industry = excluded.industry
    """).fetchall()
    conn.unregister("_tmp_tsm")
    return rows[0][0] if rows else 0


def upsert_stock_reasons(conn: duckdb.DuckDBPyConnection, date: str, reasons: List[Dict[str, str]]) -> int:
    """Insert or update LLM stock reason tags. Returns row count."""
    if not reasons:
        return 0
    df = pd.DataFrame(reasons)
    df["date"] = pd.to_datetime(_norm_date(date))
    conn.register("_tmp_tsr", df[["date", "code", "reasons"]])
    rows = conn.execute("""
        INSERT INTO tw_stock_reasons (date, code, reasons)
        SELECT date, code, reasons FROM _tmp_tsr
        ON CONFLICT (date, code) DO UPDATE SET
            reasons = excluded.reasons,
            generated_at = now()
    """).fetchall()
    conn.unregister("_tmp_tsr")
    return rows[0][0] if rows else 0


def upsert_daily_narratives(conn: duckdb.DuckDBPyConnection, date: str, narratives: List[Dict[str, Any]]) -> int:
    """Insert or update LLM daily narratives. Returns row count."""
    if not narratives:
        return 0
    count = 0
    for n in narratives:
        stocks_json = json.dumps(n.get("stocks", []), ensure_ascii=False)
        conn.execute("""
            INSERT INTO tw_daily_narratives (date, tag, name, description, stocks_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (date, name) DO UPDATE SET
                tag = excluded.tag,
                description = excluded.description,
                stocks_json = excluded.stocks_json,
                generated_at = now()
        """, [_norm_date(date), n.get("tag", ""), n["name"], n.get("description", ""), stocks_json])
        count += 1
    return count


# ═══════════════════════════════════════════════════════════════
#  Query helpers
# ═══════════════════════════════════════════════════════════════

def get_listed_stocks(conn: duckdb.DuckDBPyConnection, market: str = None, sector: str = None,
                       search: str = None, active_only: bool = True, limit: int = 500) -> pd.DataFrame:
    """List stocks with optional filters."""
    where = ["1=1"]
    params: list = []
    if market:
        where.append("market = ?")
        params.append(market)
    if sector:
        where.append("sector = ?")
        params.append(sector)
    if active_only:
        where.append("is_active = true")
    if search:
        where.append("(name LIKE ? OR code LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])
    params.append(limit)
    return conn.execute(f"""
        SELECT code, name, name_en, market, sector, industry, listing_date, market_cap
        FROM tw_listed_stocks
        WHERE {' AND '.join(where)}
        ORDER BY market_cap DESC NULLS LAST
        LIMIT ?
    """, params).df()


def get_stock_detail(conn: duckdb.DuckDBPyConnection, code: str) -> Optional[Dict[str, Any]]:
    """Get listing info for a single stock."""
    row = conn.execute("""
        SELECT code, name, name_en, market, sector, industry, listing_date, shares_outstanding, market_cap, is_active
        FROM tw_listed_stocks WHERE code = ?
    """, [code]).fetchone()
    if row is None:
        return None
    return {
        "code": row[0], "name": row[1], "name_en": row[2], "market": row[3], "sector": row[4],
        "industry": row[5], "listing_date": str(row[6]) if row[6] else None,
        "shares_outstanding": row[7], "market_cap": row[8], "is_active": row[9],
    }


def get_daily_prices(conn: duckdb.DuckDBPyConnection, code: str, limit: int = 60) -> pd.DataFrame:
    """Get price history for a stock."""
    return conn.execute("""
        SELECT date, open, high, low, close, volume, change_pct
        FROM tw_daily_prices
        WHERE code = ?
        ORDER BY date DESC
        LIMIT ?
    """, [code, limit]).df()


def get_prices_batch(conn: duckdb.DuckDBPyConnection, codes: List[str], limit: int = 20) -> Dict[str, List[Dict]]:
    """Get recent prices for multiple stocks. Returns {code: [{date, close, change_pct}, ...]}."""
    if not codes:
        return {}
    placeholders = ",".join(["?"] * len(codes))
    df = conn.execute(f"""
        SELECT code, date, close, change_pct
        FROM tw_daily_prices
        WHERE code IN ({placeholders})
        ORDER BY code, date DESC
    """, codes).df()
    if df.empty:
        return {c: [] for c in codes}
    result = {}
    for code in codes:
        subset = df[df["code"] == code].head(limit)
        subset = subset.sort_values("date")
        records = subset[["date", "close", "change_pct"]].to_dict(orient="records")
        for r in records:
            r["date"] = str(r["date"])
        result[code] = records
    return result


def get_daily_movers(conn: duckdb.DuckDBPyConnection, date: str) -> pd.DataFrame:
    """Get significant movers for a date with LLM reasons."""
    return conn.execute("""
        SELECT m.*, r.reasons
        FROM tw_significant_movers m
        LEFT JOIN tw_stock_reasons r ON m.date = r.date AND m.code = r.code
        WHERE m.date = ?
        ORDER BY ABS(m.change_pct) DESC
    """, [_norm_date(date)]).df()


def get_market_indices(conn: duckdb.DuckDBPyConnection, index_code: str = None,
                        start: str = None, end: str = None, limit: int = 200) -> pd.DataFrame:
    """Get index data with optional filters."""
    where = ["1=1"]
    params: list = []
    if index_code:
        where.append("index_code = ?")
        params.append(index_code)
    if start:
        where.append("date >= ?")
        params.append(_norm_date(start))
    if end:
        where.append("date <= ?")
        params.append(_norm_date(end))
    params.append(limit)
    return conn.execute(f"""
        SELECT date, index_code, index_name, open, high, low, close, volume, change_pct
        FROM tw_market_indices
        WHERE {' AND '.join(where)}
        ORDER BY date DESC, index_code
        LIMIT ?
    """, params).df()


def get_stock_mover_history(conn: duckdb.DuckDBPyConnection, code: str, limit: int = 60) -> pd.DataFrame:
    """Get significant mover history for a stock."""
    return conn.execute("""
        SELECT m.*, r.reasons
        FROM tw_significant_movers m
        LEFT JOIN tw_stock_reasons r ON m.date = r.date AND m.code = r.code
        WHERE m.code = ?
        ORDER BY m.date DESC
        LIMIT ?
    """, [code, limit]).df()


def get_narratives(conn: duckdb.DuckDBPyConnection, date: str) -> List[Dict[str, Any]]:
    """Get daily market narratives."""
    rows = conn.execute("""
        SELECT date, tag, name, description, stocks_json
        FROM tw_daily_narratives
        WHERE date = ?
        ORDER BY name
    """, [_norm_date(date)]).fetchall()
    result = []
    for row in rows:
        result.append({
            "date": str(row[0]),
            "tag": row[1],
            "name": row[2],
            "description": row[3],
            "stocks": json.loads(row[4]),
        })
    return result


def get_narratives_range(conn: duckdb.DuckDBPyConnection, start: str, end: str) -> List[Dict[str, Any]]:
    """Get narratives for a date range."""
    rows = conn.execute("""
        SELECT date, tag, name, description, stocks_json
        FROM tw_daily_narratives
        WHERE date >= ? AND date <= ?
        ORDER BY date DESC, name
    """, [_norm_date(start), _norm_date(end)]).fetchall()
    result = []
    for row in rows:
        result.append({
            "date": str(row[0]),
            "tag": row[1],
            "name": row[2],
            "description": row[3],
            "stocks": json.loads(row[4]),
        })
    return result


def get_industry_summary(conn: duckdb.DuckDBPyConnection, date: str) -> pd.DataFrame:
    """Get industry distribution for significant movers on a date."""
    return conn.execute("""
        SELECT COALESCE(industry, '其他') AS industry,
               COUNT(*) AS count,
               AVG(change_pct) AS avg_change,
               MAX(ABS(change_pct)) AS max_change
        FROM tw_significant_movers
        WHERE date = ?
        GROUP BY industry
        ORDER BY count DESC
    """, [_norm_date(date)]).df()


def get_available_dates(conn: duckdb.DuckDBPyConnection, limit: int = 30) -> List[str]:
    """Get list of dates with price data."""
    rows = conn.execute("""
        SELECT DISTINCT date FROM tw_daily_prices
        ORDER BY date DESC LIMIT ?
    """, [limit]).fetchall()
    return [str(r[0]) for r in rows]


def get_trend_data(conn: duckdb.DuckDBPyConnection, start: str, end: str) -> pd.DataFrame:
    """Get daily aggregate stats for a date range."""
    return conn.execute("""
        SELECT date, COUNT(*) AS mover_count,
               AVG(change_pct) AS avg_change,
               MAX(ABS(change_pct)) AS max_change,
               SUM(CASE WHEN change_pct > 0 THEN 1 ELSE 0 END) AS up_count,
               SUM(CASE WHEN change_pct < 0 THEN 1 ELSE 0 END) AS down_count,
               COUNT(DISTINCT industry) AS sector_count
        FROM tw_significant_movers
        WHERE date >= ? AND date <= ?
        GROUP BY date
        ORDER BY date ASC
    """, [_norm_date(start), _norm_date(end)]).df()


def get_sector_rotation(conn: duckdb.DuckDBPyConnection, start: str, end: str, top_n: int = 15) -> list:
    """Get industry-date counts for sector rotation heatmap."""
    df = conn.execute("""
        SELECT date, COALESCE(industry, '其他') AS industry, COUNT(*) AS cnt
        FROM tw_significant_movers
        WHERE date >= ? AND date <= ?
        GROUP BY date, industry
        ORDER BY date ASC, cnt DESC
    """, [_norm_date(start), _norm_date(end)]).df()
    if df.empty:
        return []
    top = df.groupby("industry")["cnt"].sum().nlargest(top_n).index.tolist()
    return df[df["industry"].isin(top)].to_dict(orient="records")


def get_sector_detail(conn: duckdb.DuckDBPyConnection, start: str, end: str, sector: str) -> pd.DataFrame:
    """Get daily stats for a single sector over time."""
    return conn.execute("""
        SELECT date, COUNT(*) AS count,
               AVG(change_pct) AS avg_change,
               MAX(ABS(change_pct)) AS max_change
        FROM tw_significant_movers
        WHERE date >= ? AND date <= ? AND industry = ?
        GROUP BY date
        ORDER BY date ASC
    """, [_norm_date(start), _norm_date(end), sector]).df()


def get_daily_summary(conn: duckdb.DuckDBPyConnection, date: str) -> Dict[str, Any]:
    """Get a summary for a trading day."""
    row = conn.execute("""
        SELECT
            COUNT(*) AS mover_count,
            AVG(change_pct) AS avg_change,
            MAX(ABS(change_pct)) AS max_change,
            SUM(CASE WHEN change_pct > 0 THEN 1 ELSE 0 END) AS up_count,
            SUM(CASE WHEN change_pct < 0 THEN 1 ELSE 0 END) AS down_count,
            COUNT(DISTINCT industry) AS sector_count
        FROM tw_significant_movers
        WHERE date = ?
    """, [_norm_date(date)]).fetchone()
    if row is None or row[0] == 0:
        return {"date": date, "mover_count": 0}

    idx_row = conn.execute("""
        SELECT index_code, close, change_pct FROM tw_market_indices WHERE date = ?
    """, [_norm_date(date)]).fetchall()

    return {
        "date": date,
        "mover_count": int(row[0]),
        "avg_change": round(float(row[1]), 2) if row[1] else 0,
        "max_change": round(float(row[2]), 2) if row[2] else 0,
        "up_count": int(row[3]),
        "down_count": int(row[4]),
        "sector_count": int(row[5]),
        "indices": [{"index_code": r[0], "close": r[1], "change_pct": r[2]} for r in idx_row],
    }


def get_counts(conn: duckdb.DuckDBPyConnection) -> Dict[str, Any]:
    """Get overall database counts."""
    stocks = conn.execute("SELECT COUNT(*) FROM tw_listed_stocks WHERE is_active = true").fetchone()[0]
    prices = conn.execute("SELECT COUNT(DISTINCT date) FROM tw_daily_prices").fetchone()[0]
    movers = conn.execute("SELECT COUNT(*) FROM tw_significant_movers").fetchone()[0]
    by_market = conn.execute("""
        SELECT market, COUNT(*) FROM tw_listed_stocks WHERE is_active = true GROUP BY market
    """).fetchall()
    return {
        "total_stocks": stocks,
        "trading_days_with_prices": prices,
        "total_significant_movers": movers,
        "by_market": [{"market": r[0], "count": r[1]} for r in by_market],
    }


def log_fetch(conn: duckdb.DuckDBPyConnection, date: str, status: str = "success",
              listings_count: int = 0, prices_count: int = 0, movers_count: int = 0,
              tagged: int = 0, narratives: int = 0, errors: str = "") -> int:
    """Record a fetch operation in the log."""
    conn.execute("""
        INSERT INTO tw_fetch_log (fetch_date, status, listings_count, prices_count,
                                   movers_count, tagged, narratives, errors)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, [_norm_date(date), status, listings_count, prices_count, movers_count, tagged, narratives, errors])
    return conn.execute("SELECT MAX(id) FROM tw_fetch_log").fetchone()[0]


def get_fetch_status(conn: duckdb.DuckDBPyConnection, days: int = 7) -> pd.DataFrame:
    """Get recent fetch log entries."""
    return conn.execute("""
        SELECT id, fetch_date, status, listings_count, prices_count, movers_count,
               tagged, narratives, errors, created_at
        FROM tw_fetch_log
        WHERE fetch_date >= (SELECT MAX(fetch_date) FROM tw_fetch_log) - ?
        ORDER BY fetch_date DESC
    """, [days]).df()
