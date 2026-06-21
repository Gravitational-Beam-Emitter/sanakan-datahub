"""
Storage layer — DuckDB-backed store for Korean stock data.

Tables:
  kr_listed_stocks       — Master list of KOSPI/KOSDAQ/KONEX stocks
  kr_daily_prices        — Daily OHLCV for all stocks
  kr_market_indices      — KOSPI (KS11), KOSDAQ (KQ11) index data
  kr_significant_movers  — Stocks with ±10%+ daily moves
  kr_stock_reasons       — LLM-generated reason tags per stock
  kr_daily_narratives    — LLM-generated market narratives per day
  kr_dart_filings        — DART corporate filings
  kr_foreign_flows       — Foreign/institutional daily flow data
  kr_fetch_log           — Fetch audit trail
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import duckdb
import pandas as pd

from kr_stock.config import DB_PATH


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
        CREATE TABLE IF NOT EXISTS kr_listed_stocks (
            code VARCHAR NOT NULL,
            name VARCHAR NOT NULL,
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
        CREATE TABLE IF NOT EXISTS kr_daily_prices (
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
        CREATE TABLE IF NOT EXISTS kr_market_indices (
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

    # 4. Significant movers (default threshold: ±10%)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS kr_significant_movers (
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
        CREATE TABLE IF NOT EXISTS kr_stock_reasons (
            date DATE NOT NULL,
            code VARCHAR NOT NULL,
            reasons VARCHAR NOT NULL,
            generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(date, code)
        )
    """)

    # 6. Daily market narratives
    conn.execute("""
        CREATE TABLE IF NOT EXISTS kr_daily_narratives (
            date DATE NOT NULL,
            tag VARCHAR NOT NULL DEFAULT '',
            name VARCHAR NOT NULL,
            description TEXT,
            stocks_json VARCHAR NOT NULL DEFAULT '[]',
            generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(date, name)
        )
    """)

    # 7. DART corporate filings
    conn.execute("""
        CREATE TABLE IF NOT EXISTS kr_dart_filings (
            rcept_no VARCHAR NOT NULL,
            receipt_date DATE,
            corp_code VARCHAR,
            corp_name VARCHAR,
            report_nm VARCHAR,
            report_detail VARCHAR,
            url VARCHAR,
            fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(rcept_no)
        )
    """)

    # 8. Foreign/institutional flows
    conn.execute("""
        CREATE TABLE IF NOT EXISTS kr_foreign_flows (
            date DATE NOT NULL,
            market VARCHAR NOT NULL,
            foreign_net_buy BIGINT,
            institution_net_buy BIGINT,
            individual_net_buy BIGINT,
            PRIMARY KEY(date, market)
        )
    """)

    # 9. Fetch log
    conn.execute("CREATE SEQUENCE IF NOT EXISTS seq_kr_fetch_log")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS kr_fetch_log (
            id INTEGER PRIMARY KEY DEFAULT nextval('seq_kr_fetch_log'),
            fetch_date DATE NOT NULL,
            status VARCHAR NOT NULL DEFAULT 'success',
            listings_count INTEGER DEFAULT 0,
            prices_count INTEGER DEFAULT 0,
            movers_count INTEGER DEFAULT 0,
            filings_count INTEGER DEFAULT 0,
            tagged INTEGER DEFAULT 0,
            narratives INTEGER DEFAULT 0,
            errors TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        )
    """)

    # Indexes
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kls_market ON kr_listed_stocks(market)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kls_sector ON kr_listed_stocks(sector)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kdp_date ON kr_daily_prices(date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kdp_code ON kr_daily_prices(code)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kmi_date ON kr_market_indices(date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ksm_date ON kr_significant_movers(date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ksr_date ON kr_stock_reasons(date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kdn_date ON kr_daily_narratives(date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kdf_date ON kr_dart_filings(receipt_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kdf_corp ON kr_dart_filings(corp_name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kff_date ON kr_foreign_flows(date)")

    return conn


# ═══════════════════════════════════════════════════════════════
#  UPSERT helpers
# ═══════════════════════════════════════════════════════════════

def upsert_listed_stocks(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> int:
    """Batch insert/update listed stocks. Returns row count."""
    if df.empty:
        return 0
    needed = ["code", "name", "market", "sector", "industry",
              "listing_date", "shares_outstanding", "market_cap"]
    for col in needed:
        if col not in df.columns:
            df[col] = None
    sub = df[needed].copy()
    conn.register("_tmp_kls", sub)
    rows = conn.execute("""
        INSERT INTO kr_listed_stocks (code, name, market, sector, industry,
                                       listing_date, shares_outstanding, market_cap)
        SELECT code, name, market, sector, industry,
               listing_date, shares_outstanding, market_cap
        FROM _tmp_kls
        ON CONFLICT (code) DO UPDATE SET
            name = excluded.name,
            market = excluded.market,
            sector = excluded.sector,
            industry = excluded.industry,
            listing_date = excluded.listing_date,
            shares_outstanding = excluded.shares_outstanding,
            market_cap = excluded.market_cap,
            updated_at = now()
    """).fetchall()
    conn.unregister("_tmp_kls")
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
    conn.register("_tmp_kdp", sub)
    rows = conn.execute("""
        INSERT INTO kr_daily_prices (date, code, open, high, low, close, volume, change_pct)
        SELECT date, code, open, high, low, close, volume, change_pct
        FROM _tmp_kdp
        ON CONFLICT (date, code) DO UPDATE SET
            open = excluded.open,
            high = excluded.high,
            low = excluded.low,
            close = excluded.close,
            volume = excluded.volume,
            change_pct = excluded.change_pct
    """).fetchall()
    conn.unregister("_tmp_kdp")
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
    conn.register("_tmp_kmi", sub)
    rows = conn.execute("""
        INSERT INTO kr_market_indices (date, index_code, index_name, open, high, low, close, volume, change_pct)
        SELECT date, index_code, index_name, open, high, low, close, volume, change_pct
        FROM _tmp_kmi
        ON CONFLICT (date, index_code) DO UPDATE SET
            index_name = excluded.index_name,
            open = excluded.open,
            high = excluded.high,
            low = excluded.low,
            close = excluded.close,
            volume = excluded.volume,
            change_pct = excluded.change_pct
    """).fetchall()
    conn.unregister("_tmp_kmi")
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
    conn.register("_tmp_ksm", sub)
    rows = conn.execute("""
        INSERT INTO kr_significant_movers (date, code, name, change_pct, volume, close, market, sector, industry)
        SELECT date, code, name, change_pct, volume, close, market, sector, industry
        FROM _tmp_ksm
        ON CONFLICT (date, code) DO UPDATE SET
            name = excluded.name,
            change_pct = excluded.change_pct,
            volume = excluded.volume,
            close = excluded.close,
            market = excluded.market,
            sector = excluded.sector,
            industry = excluded.industry
    """).fetchall()
    conn.unregister("_tmp_ksm")
    return rows[0][0] if rows else 0


def upsert_stock_reasons(conn: duckdb.DuckDBPyConnection, date: str, reasons: List[Dict[str, str]]) -> int:
    """Insert or update LLM stock reason tags. Returns row count."""
    if not reasons:
        return 0
    df = pd.DataFrame(reasons)
    df["date"] = pd.to_datetime(_norm_date(date))
    conn.register("_tmp_ksr", df[["date", "code", "reasons"]])
    rows = conn.execute("""
        INSERT INTO kr_stock_reasons (date, code, reasons)
        SELECT date, code, reasons FROM _tmp_ksr
        ON CONFLICT (date, code) DO UPDATE SET
            reasons = excluded.reasons,
            generated_at = now()
    """).fetchall()
    conn.unregister("_tmp_ksr")
    return rows[0][0] if rows else 0


def upsert_daily_narratives(conn: duckdb.DuckDBPyConnection, date: str, narratives: List[Dict[str, Any]]) -> int:
    """Insert or update LLM daily narratives. Returns row count."""
    if not narratives:
        return 0
    count = 0
    for n in narratives:
        stocks_json = json.dumps(n.get("stocks", []), ensure_ascii=False)
        conn.execute("""
            INSERT INTO kr_daily_narratives (date, tag, name, description, stocks_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (date, name) DO UPDATE SET
                tag = excluded.tag,
                description = excluded.description,
                stocks_json = excluded.stocks_json,
                generated_at = now()
        """, [_norm_date(date), n.get("tag", ""), n["name"], n.get("description", ""), stocks_json])
        count += 1
    return count


def upsert_dart_filings(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> int:
    """Batch insert/update DART filings. Returns row count."""
    if df.empty:
        return 0
    needed = ["rcept_no", "receipt_date", "corp_code", "corp_name", "report_nm", "report_detail", "url"]
    for col in needed:
        if col not in df.columns:
            df[col] = None
    sub = df[needed].copy()
    conn.register("_tmp_kdf", sub)
    rows = conn.execute("""
        INSERT INTO kr_dart_filings (rcept_no, receipt_date, corp_code, corp_name, report_nm, report_detail, url)
        SELECT rcept_no, receipt_date, corp_code, corp_name, report_nm, report_detail, url
        FROM _tmp_kdf
        ON CONFLICT (rcept_no) DO UPDATE SET
            receipt_date = excluded.receipt_date,
            corp_code = excluded.corp_code,
            corp_name = excluded.corp_name,
            report_nm = excluded.report_nm,
            report_detail = excluded.report_detail,
            url = excluded.url,
            fetched_at = now()
    """).fetchall()
    conn.unregister("_tmp_kdf")
    return rows[0][0] if rows else 0


def upsert_foreign_flows(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> int:
    """Batch insert/update foreign flows. Returns row count."""
    if df.empty:
        return 0
    needed = ["date", "market", "foreign_net_buy", "institution_net_buy", "individual_net_buy"]
    for col in needed:
        if col not in df.columns:
            df[col] = None
    sub = df[needed].copy()
    conn.register("_tmp_kff", sub)
    rows = conn.execute("""
        INSERT INTO kr_foreign_flows (date, market, foreign_net_buy, institution_net_buy, individual_net_buy)
        SELECT date, market, foreign_net_buy, institution_net_buy, individual_net_buy
        FROM _tmp_kff
        ON CONFLICT (date, market) DO UPDATE SET
            foreign_net_buy = excluded.foreign_net_buy,
            institution_net_buy = excluded.institution_net_buy,
            individual_net_buy = excluded.individual_net_buy
    """).fetchall()
    conn.unregister("_tmp_kff")
    return rows[0][0] if rows else 0


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
        SELECT code, name, market, sector, industry, listing_date, market_cap
        FROM kr_listed_stocks
        WHERE {' AND '.join(where)}
        ORDER BY market_cap DESC NULLS LAST
        LIMIT ?
    """, params).df()


def get_stock_detail(conn: duckdb.DuckDBPyConnection, code: str) -> Optional[Dict[str, Any]]:
    """Get listing info for a single stock."""
    row = conn.execute("""
        SELECT code, name, market, sector, industry, listing_date, shares_outstanding, market_cap, is_active
        FROM kr_listed_stocks WHERE code = ?
    """, [code]).fetchone()
    if row is None:
        return None
    return {
        "code": row[0], "name": row[1], "market": row[2], "sector": row[3],
        "industry": row[4], "listing_date": str(row[5]) if row[5] else None,
        "shares_outstanding": row[6], "market_cap": row[7], "is_active": row[8],
    }


def get_daily_prices(conn: duckdb.DuckDBPyConnection, code: str, limit: int = 60) -> pd.DataFrame:
    """Get price history for a stock."""
    return conn.execute("""
        SELECT date, open, high, low, close, volume, change_pct
        FROM kr_daily_prices
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
        FROM kr_daily_prices
        WHERE code IN ({placeholders})
        ORDER BY code, date DESC
    """, codes).df()
    if df.empty:
        return {c: [] for c in codes}
    result = {}
    for code in codes:
        subset = df[df["code"] == code].head(limit)
        subset = subset.sort_values("date")  # chronological for sparkline
        records = subset[["date", "close", "change_pct"]].to_dict(orient="records")
        for r in records:
            r["date"] = str(r["date"])
        result[code] = records
    return result


def get_daily_movers(conn: duckdb.DuckDBPyConnection, date: str) -> pd.DataFrame:
    """Get significant movers for a date with LLM reasons."""
    return conn.execute("""
        SELECT m.*, r.reasons
        FROM kr_significant_movers m
        LEFT JOIN kr_stock_reasons r ON m.date = r.date AND m.code = r.code
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
        FROM kr_market_indices
        WHERE {' AND '.join(where)}
        ORDER BY date DESC, index_code
        LIMIT ?
    """, params).df()


def get_stock_mover_history(conn: duckdb.DuckDBPyConnection, code: str, limit: int = 60) -> pd.DataFrame:
    """Get significant mover history for a stock."""
    return conn.execute("""
        SELECT m.*, r.reasons
        FROM kr_significant_movers m
        LEFT JOIN kr_stock_reasons r ON m.date = r.date AND m.code = r.code
        WHERE m.code = ?
        ORDER BY m.date DESC
        LIMIT ?
    """, [code, limit]).df()


def get_narratives(conn: duckdb.DuckDBPyConnection, date: str) -> List[Dict[str, Any]]:
    """Get daily market narratives."""
    rows = conn.execute("""
        SELECT date, tag, name, description, stocks_json
        FROM kr_daily_narratives
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
        FROM kr_daily_narratives
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
        SELECT COALESCE(industry, '기타') AS industry,
               COUNT(*) AS count,
               AVG(change_pct) AS avg_change,
               MAX(ABS(change_pct)) AS max_change
        FROM kr_significant_movers
        WHERE date = ?
        GROUP BY industry
        ORDER BY count DESC
    """, [_norm_date(date)]).df()


def get_available_dates(conn: duckdb.DuckDBPyConnection, limit: int = 30) -> List[str]:
    """Get list of dates with price data."""
    rows = conn.execute("""
        SELECT DISTINCT date FROM kr_daily_prices
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
        FROM kr_significant_movers
        WHERE date >= ? AND date <= ?
        GROUP BY date
        ORDER BY date ASC
    """, [_norm_date(start), _norm_date(end)]).df()


def get_sector_rotation(conn: duckdb.DuckDBPyConnection, start: str, end: str, top_n: int = 15) -> list:
    """Get industry-date counts for sector rotation heatmap."""
    df = conn.execute("""
        SELECT date, COALESCE(industry, '기타') AS industry, COUNT(*) AS cnt
        FROM kr_significant_movers
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
        FROM kr_significant_movers
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
        FROM kr_significant_movers
        WHERE date = ?
    """, [_norm_date(date)]).fetchone()
    if row is None or row[0] == 0:
        return {"date": date, "mover_count": 0}

    # Index data for the date
    idx_row = conn.execute("""
        SELECT index_code, close, change_pct FROM kr_market_indices WHERE date = ?
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


def get_foreign_flows(conn: duckdb.DuckDBPyConnection, market: str = None,
                       start: str = None, end: str = None, limit: int = 60) -> pd.DataFrame:
    """Get foreign/institutional flow data."""
    where = ["1=1"]
    params: list = []
    if market:
        where.append("market = ?")
        params.append(market)
    if start:
        where.append("date >= ?")
        params.append(_norm_date(start))
    if end:
        where.append("date <= ?")
        params.append(_norm_date(end))
    params.append(limit)
    return conn.execute(f"""
        SELECT date, market, foreign_net_buy, institution_net_buy, individual_net_buy
        FROM kr_foreign_flows
        WHERE {' AND '.join(where)}
        ORDER BY date DESC, market
        LIMIT ?
    """, params).df()


def get_dart_filings(conn: duckdb.DuckDBPyConnection, corp_name: str = None,
                      report_type: str = None, start: str = None, end: str = None,
                      limit: int = 100) -> pd.DataFrame:
    """Query DART filings with optional filters."""
    where = ["1=1"]
    params: list = []
    if corp_name:
        where.append("corp_name LIKE ?")
        params.append(f"%{corp_name}%")
    if report_type:
        where.append("report_nm LIKE ?")
        params.append(f"%{report_type}%")
    if start:
        where.append("receipt_date >= ?")
        params.append(_norm_date(start))
    if end:
        where.append("receipt_date <= ?")
        params.append(_norm_date(end))
    params.append(limit)
    return conn.execute(f"""
        SELECT rcept_no, receipt_date, corp_code, corp_name, report_nm, report_detail, url
        FROM kr_dart_filings
        WHERE {' AND '.join(where)}
        ORDER BY receipt_date DESC
        LIMIT ?
    """, params).df()


def get_filing_by_rcept_no(conn: duckdb.DuckDBPyConnection, rcept_no: str) -> Optional[Dict[str, Any]]:
    """Get a single DART filing."""
    row = conn.execute("""
        SELECT rcept_no, receipt_date, corp_code, corp_name, report_nm, report_detail, url
        FROM kr_dart_filings WHERE rcept_no = ?
    """, [rcept_no]).fetchone()
    if row is None:
        return None
    return {
        "rcept_no": row[0], "receipt_date": str(row[1]) if row[1] else None,
        "corp_code": row[2], "corp_name": row[3], "report_nm": row[4],
        "report_detail": row[5], "url": row[6],
    }


def get_counts(conn: duckdb.DuckDBPyConnection) -> Dict[str, Any]:
    """Get overall database counts."""
    stocks = conn.execute("SELECT COUNT(*) FROM kr_listed_stocks WHERE is_active = true").fetchone()[0]
    prices = conn.execute("SELECT COUNT(DISTINCT date) FROM kr_daily_prices").fetchone()[0]
    movers = conn.execute("SELECT COUNT(*) FROM kr_significant_movers").fetchone()[0]
    filings = conn.execute("SELECT COUNT(*) FROM kr_dart_filings").fetchone()[0]
    by_market = conn.execute("""
        SELECT market, COUNT(*) FROM kr_listed_stocks WHERE is_active = true GROUP BY market
    """).fetchall()
    return {
        "total_stocks": stocks,
        "trading_days_with_prices": prices,
        "total_significant_movers": movers,
        "total_filings": filings,
        "by_market": [{"market": r[0], "count": r[1]} for r in by_market],
    }


def log_fetch(conn: duckdb.DuckDBPyConnection, date: str, status: str = "success",
              listings_count: int = 0, prices_count: int = 0, movers_count: int = 0,
              filings_count: int = 0, tagged: int = 0, narratives: int = 0, errors: str = "") -> int:
    """Record a fetch operation in the log."""
    conn.execute("""
        INSERT INTO kr_fetch_log (fetch_date, status, listings_count, prices_count,
                                   movers_count, filings_count, tagged, narratives, errors)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [_norm_date(date), status, listings_count, prices_count, movers_count, filings_count, tagged, narratives, errors])
    return conn.execute("SELECT MAX(id) FROM kr_fetch_log").fetchone()[0]


def get_fetch_status(conn: duckdb.DuckDBPyConnection, days: int = 7) -> pd.DataFrame:
    """Get recent fetch log entries."""
    return conn.execute("""
        SELECT id, fetch_date, status, listings_count, prices_count, movers_count,
               filings_count, tagged, narratives, errors, created_at
        FROM kr_fetch_log
        WHERE fetch_date >= (SELECT MAX(fetch_date) FROM kr_fetch_log) - ?
        ORDER BY fetch_date DESC
    """, [days]).df()
