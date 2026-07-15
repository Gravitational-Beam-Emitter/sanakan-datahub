"""
Storage layer — DuckDB-backed store for US listings and crypto products.

Tables:
  new_listings    — newly listed US stocks (IPOs, direct listings, SPACs)
  crypto_products — comprehensive list of crypto-related US-listed products
  fetch_log       — daily fetch audit log
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List, Optional

import duckdb
import pandas as pd

from us_listings.config import DB_PATH, LOOKBACK_DAYS


def _conn(db_path: Optional[str] = None, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    path = db_path or DB_PATH
    return duckdb.connect(path, read_only=read_only)


def init_db(db_path: Optional[str] = None, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """Create tables and indexes if they don't exist. Returns connection."""
    conn = _conn(db_path, read_only=read_only)
    if read_only:
        return conn

    # Sequences must be created before tables that reference them
    conn.execute("CREATE SEQUENCE IF NOT EXISTS listings_seq START 1")
    conn.execute("CREATE SEQUENCE IF NOT EXISTS crypto_seq START 1")
    conn.execute("CREATE SEQUENCE IF NOT EXISTS uslistings_fetch_log_seq START 1")
    conn.execute("CREATE SEQUENCE IF NOT EXISTS insider_seq START 1")
    conn.execute("CREATE SEQUENCE IF NOT EXISTS earnings_seq START 1")
    conn.execute("CREATE SEQUENCE IF NOT EXISTS holdings_seq START 1")
    conn.execute("CREATE SEQUENCE IF NOT EXISTS si_seq START 1")
    conn.execute("CREATE SEQUENCE IF NOT EXISTS ftd_seq START 1")
    conn.execute("CREATE SEQUENCE IF NOT EXISTS flows_seq START 1")
    conn.execute("CREATE SEQUENCE IF NOT EXISTS div_seq START 1")
    conn.execute("CREATE SEQUENCE IF NOT EXISTS splits_seq START 1")
    conn.execute("CREATE SEQUENCE IF NOT EXISTS suspension_seq START 1")
    conn.execute("CREATE SEQUENCE IF NOT EXISTS enforcement_seq START 1")
    conn.execute("CREATE SEQUENCE IF NOT EXISTS threshold_seq START 1")
    conn.execute("CREATE SEQUENCE IF NOT EXISTS ats_seq START 1")
    conn.execute("CREATE SEQUENCE IF NOT EXISTS ssa_seq START 1")
    conn.execute("CREATE SEQUENCE IF NOT EXISTS lockup_seq START 1")
    conn.execute("CREATE SEQUENCE IF NOT EXISTS options_seq START 1")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS new_listings (
            id INTEGER PRIMARY KEY DEFAULT nextval('listings_seq'),
            ticker VARCHAR NOT NULL,
            company_name VARCHAR NOT NULL,
            listing_date DATE NOT NULL,
            listing_type VARCHAR NOT NULL,
            exchange VARCHAR,
            offer_price DOUBLE,
            shares_offered BIGINT,
            description VARCHAR,
            source VARCHAR DEFAULT 'nasdaq',
            source_url VARCHAR,
            is_crypto BOOLEAN DEFAULT false,
            crypto_product_id INTEGER,
            created_at TIMESTAMP DEFAULT now(),
            UNIQUE(ticker, listing_date)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS crypto_products (
            id INTEGER PRIMARY KEY DEFAULT nextval('crypto_seq'),
            ticker VARCHAR UNIQUE NOT NULL,
            company_name VARCHAR NOT NULL,
            product_type VARCHAR NOT NULL,
            underlying_asset VARCHAR,
            listing_date DATE,
            expense_ratio DOUBLE,
            aum DOUBLE,
            market_cap DOUBLE,
            description VARCHAR,
            issuer VARCHAR,
            is_active BOOLEAN DEFAULT true,
            data_source VARCHAR DEFAULT 'known_list',
            last_updated TIMESTAMP DEFAULT now(),
            created_at TIMESTAMP DEFAULT now()
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS fetch_log (
            id INTEGER PRIMARY KEY DEFAULT nextval('uslistings_fetch_log_seq'),
            fetch_date DATE NOT NULL,
            source VARCHAR DEFAULT 'nasdaq_ipo',
            items_checked INTEGER DEFAULT 0,
            new_items INTEGER DEFAULT 0,
            status VARCHAR DEFAULT 'ok',
            error_message TEXT,
            started_at TIMESTAMP DEFAULT now(),
            completed_at TIMESTAMP
        )
    """)

    # Indexes
    conn.execute("CREATE INDEX IF NOT EXISTS idx_nl_date ON new_listings(listing_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_nl_ticker ON new_listings(ticker)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_nl_type ON new_listings(listing_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_nl_crypto ON new_listings(is_crypto)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cp_ticker ON crypto_products(ticker)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cp_type ON crypto_products(product_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cp_asset ON crypto_products(underlying_asset)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fl_date2 ON fetch_log(fetch_date)")

    # ── New tables for expanded US equities data ──

    conn.execute("""
        CREATE TABLE IF NOT EXISTS insider_trades (
            id INTEGER PRIMARY KEY DEFAULT nextval('insider_seq'),
            ticker VARCHAR NOT NULL,
            company_name VARCHAR NOT NULL,
            insider_name VARCHAR,
            insider_title VARCHAR,
            transaction_type VARCHAR,
            shares DOUBLE,
            price_per_share DOUBLE,
            total_value DOUBLE,
            shares_owned_after DOUBLE,
            filing_date DATE NOT NULL,
            transaction_date DATE,
            is_10b5_1 BOOLEAN DEFAULT false,
            source_url VARCHAR,
            fetched_at TIMESTAMP DEFAULT now(),
            UNIQUE(ticker, insider_name, filing_date, transaction_type, shares)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS earnings_calendar (
            id INTEGER PRIMARY KEY DEFAULT nextval('earnings_seq'),
            ticker VARCHAR NOT NULL,
            company_name VARCHAR NOT NULL,
            report_type VARCHAR NOT NULL,
            fiscal_period_end DATE,
            filing_date DATE NOT NULL,
            source_url VARCHAR,
            fetched_at TIMESTAMP DEFAULT now(),
            UNIQUE(ticker, report_type, filing_date)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS institutional_holdings (
            id INTEGER PRIMARY KEY DEFAULT nextval('holdings_seq'),
            filer_cik VARCHAR NOT NULL,
            filer_name VARCHAR NOT NULL,
            ticker VARCHAR NOT NULL,
            cusip VARCHAR,
            security_name VARCHAR,
            shares DOUBLE,
            market_value DOUBLE,
            quarter_end DATE NOT NULL,
            filing_date DATE NOT NULL,
            source_url VARCHAR,
            fetched_at TIMESTAMP DEFAULT now()
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS short_interest (
            id INTEGER PRIMARY KEY DEFAULT nextval('si_seq'),
            ticker VARCHAR NOT NULL,
            settlement_date DATE NOT NULL,
            short_interest BIGINT,
            avg_daily_volume BIGINT,
            days_to_cover DOUBLE,
            short_pct_float DOUBLE,
            source VARCHAR DEFAULT 'yfinance',
            fetched_at TIMESTAMP DEFAULT now(),
            UNIQUE(ticker, settlement_date)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS fails_to_deliver (
            id INTEGER PRIMARY KEY DEFAULT nextval('ftd_seq'),
            ticker VARCHAR NOT NULL,
            date DATE NOT NULL,
            quantity BIGINT,
            price DOUBLE,
            source VARCHAR DEFAULT 'sec',
            fetched_at TIMESTAMP DEFAULT now()
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS etf_flows (
            id INTEGER PRIMARY KEY DEFAULT nextval('flows_seq'),
            ticker VARCHAR NOT NULL,
            date DATE NOT NULL,
            close_price DOUBLE,
            volume BIGINT,
            aum DOUBLE,
            estimated_flow DOUBLE,
            flow_pct DOUBLE,
            source VARCHAR DEFAULT 'yfinance',
            fetched_at TIMESTAMP DEFAULT now(),
            UNIQUE(ticker, date)
        )
    """)

    # ── Round 2: Extended data tables ──

    conn.execute("""
        CREATE TABLE IF NOT EXISTS dividends (
            id INTEGER PRIMARY KEY DEFAULT nextval('div_seq'),
            ticker VARCHAR NOT NULL,
            announcement_date DATE,
            ex_dividend_date DATE,
            pay_date DATE,
            dividend_rate DOUBLE,
            dividend_yield DOUBLE,
            last_dividend_value DOUBLE,
            payout_ratio DOUBLE,
            five_year_avg_yield DOUBLE,
            source VARCHAR DEFAULT 'yfinance',
            fetched_at TIMESTAMP DEFAULT now(),
            UNIQUE(ticker, ex_dividend_date)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS stock_splits (
            id INTEGER PRIMARY KEY DEFAULT nextval('splits_seq'),
            ticker VARCHAR NOT NULL,
            split_date DATE NOT NULL,
            split_ratio DOUBLE,
            source VARCHAR DEFAULT 'yfinance',
            fetched_at TIMESTAMP DEFAULT now(),
            UNIQUE(ticker, split_date)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS trading_suspensions (
            id INTEGER PRIMARY KEY DEFAULT nextval('suspension_seq'),
            ticker VARCHAR NOT NULL,
            company_name VARCHAR,
            suspension_type VARCHAR,
            reason VARCHAR,
            effective_date DATE,
            filing_date DATE NOT NULL,
            source_url VARCHAR,
            fetched_at TIMESTAMP DEFAULT now()
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS enforcement_actions (
            id INTEGER PRIMARY KEY DEFAULT nextval('enforcement_seq'),
            enforcement_type VARCHAR NOT NULL,
            entity_name VARCHAR,
            ticker VARCHAR,
            penalty_amount DOUBLE,
            description VARCHAR,
            filing_date DATE NOT NULL,
            source_url VARCHAR,
            fetched_at TIMESTAMP DEFAULT now()
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS threshold_securities (
            id INTEGER PRIMARY KEY DEFAULT nextval('threshold_seq'),
            ticker VARCHAR NOT NULL,
            security_name VARCHAR,
            market_category VARCHAR,
            is_threshold BOOLEAN DEFAULT true,
            date DATE NOT NULL,
            source VARCHAR DEFAULT 'nasdaq_regsho',
            fetched_at TIMESTAMP DEFAULT now(),
            UNIQUE(ticker, date)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS ats_filings (
            id INTEGER PRIMARY KEY DEFAULT nextval('ats_seq'),
            ats_name VARCHAR,
            filer_cik VARCHAR NOT NULL,
            filer_name VARCHAR,
            filing_type VARCHAR,
            volume_estimate DOUBLE,
            securities_traded VARCHAR,
            description VARCHAR,
            filing_date DATE NOT NULL,
            source_url VARCHAR,
            fetched_at TIMESTAMP DEFAULT now()
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS short_sale_activity (
            id INTEGER PRIMARY KEY DEFAULT nextval('ssa_seq'),
            ticker VARCHAR NOT NULL,
            date DATE NOT NULL,
            short_interest BIGINT,
            short_pct_float DOUBLE,
            days_to_cover DOUBLE,
            avg_volume BIGINT,
            float_shares BIGINT,
            short_change_pct DOUBLE,
            insider_ownership_pct DOUBLE,
            institutional_ownership_pct DOUBLE,
            risk_level VARCHAR,
            squeeze_score INTEGER,
            source VARCHAR DEFAULT 'yfinance',
            fetched_at TIMESTAMP DEFAULT now(),
            UNIQUE(ticker, date)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS lockup_expiry (
            id INTEGER PRIMARY KEY DEFAULT nextval('lockup_seq'),
            ticker VARCHAR NOT NULL,
            company_name VARCHAR,
            listing_date DATE,
            listing_type VARCHAR,
            lockup_end_date DATE NOT NULL,
            lockup_period_days INTEGER,
            days_remaining INTEGER,
            estimated_shares_unlocking BIGINT,
            estimated_value DOUBLE,
            status VARCHAR DEFAULT 'active',
            fetched_at TIMESTAMP DEFAULT now(),
            UNIQUE(ticker, lockup_end_date)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS options_flow (
            id INTEGER PRIMARY KEY DEFAULT nextval('options_seq'),
            ticker VARCHAR NOT NULL,
            date DATE NOT NULL,
            expiration_date VARCHAR,
            total_call_volume BIGINT,
            total_put_volume BIGINT,
            total_call_oi BIGINT,
            total_put_oi BIGINT,
            put_call_vol_ratio DOUBLE,
            put_call_oi_ratio DOUBLE,
            vol_oi_ratio DOUBLE,
            max_call_strike DOUBLE,
            max_call_volume BIGINT,
            max_put_strike DOUBLE,
            max_put_volume BIGINT,
            is_unusual BOOLEAN DEFAULT false,
            sentiment VARCHAR,
            source VARCHAR DEFAULT 'yfinance',
            fetched_at TIMESTAMP DEFAULT now(),
            UNIQUE(ticker, date, expiration_date)
        )
    """)

    # Sequences for new tables
    for seq in ["insider_seq", "earnings_seq", "holdings_seq", "si_seq", "ftd_seq", "flows_seq",
                "div_seq", "splits_seq", "suspension_seq", "enforcement_seq", "threshold_seq",
                "ats_seq", "ssa_seq", "lockup_seq", "options_seq"]:
        conn.execute(f"CREATE SEQUENCE IF NOT EXISTS {seq} START 1")

    # Indexes for new tables — round 1
    conn.execute("CREATE INDEX IF NOT EXISTS idx_it_ticker ON insider_trades(ticker)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_it_fdate ON insider_trades(filing_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ec_ticker ON earnings_calendar(ticker)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ec_fdate ON earnings_calendar(filing_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ih_ticker ON institutional_holdings(ticker)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ih_filer ON institutional_holdings(filer_cik)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ih_qtr ON institutional_holdings(quarter_end)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_si_ticker ON short_interest(ticker)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_si_date ON short_interest(settlement_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ftd_ticker ON fails_to_deliver(ticker)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ftd_date ON fails_to_deliver(date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ef_ticker ON etf_flows(ticker)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ef_date ON etf_flows(date)")

    # Indexes for new tables — round 2
    conn.execute("CREATE INDEX IF NOT EXISTS idx_div_ticker ON dividends(ticker)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_div_exdate ON dividends(ex_dividend_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_splits_ticker ON stock_splits(ticker)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_susp_ticker ON trading_suspensions(ticker)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_susp_date ON trading_suspensions(filing_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_enf_type ON enforcement_actions(enforcement_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_enf_date ON enforcement_actions(filing_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_thresh_ticker ON threshold_securities(ticker)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_thresh_date ON threshold_securities(date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ats_filer ON ats_filings(filer_cik)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ats_date ON ats_filings(filing_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ssa_ticker ON short_sale_activity(ticker)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ssa_date ON short_sale_activity(date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ssa_risk ON short_sale_activity(risk_level)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_lockup_ticker ON lockup_expiry(ticker)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_lockup_end ON lockup_expiry(lockup_end_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_lockup_status ON lockup_expiry(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_opt_ticker ON options_flow(ticker)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_opt_date ON options_flow(date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_opt_unusual ON options_flow(is_unusual)")

    return conn


# ═══════════════════════════════════════════════════════════════
#  New Listings CRUD
# ═══════════════════════════════════════════════════════════════

def upsert_listings(conn: duckdb.DuckDBPyConnection, listings: List[Dict[str, Any]]) -> int:
    """Batch insert/update new listings. Returns row count."""
    if not listings:
        return 0

    df = pd.DataFrame(listings)
    needed = [
        "ticker", "company_name", "listing_date", "listing_type",
        "exchange", "offer_price", "shares_offered", "description",
        "source", "source_url", "is_crypto", "crypto_product_id",
    ]
    for col in needed:
        if col not in df.columns:
            df[col] = None

    sub = df[needed].copy()
    conn.register("_tmp_nl", sub)
    try:
        rows = conn.execute("""
            INSERT INTO new_listings (
                ticker, company_name, listing_date, listing_type,
                exchange, offer_price, shares_offered, description,
                source, source_url, is_crypto, crypto_product_id
            )
            SELECT
                ticker, company_name, listing_date, listing_type,
                exchange, offer_price, shares_offered, description,
                source, source_url, is_crypto, crypto_product_id
            FROM _tmp_nl
            ON CONFLICT (ticker, listing_date) DO UPDATE SET
                company_name = excluded.company_name,
                listing_type = excluded.listing_type,
                exchange = excluded.exchange,
                offer_price = excluded.offer_price,
                shares_offered = excluded.shares_offered,
                description = excluded.description,
                source = excluded.source,
                source_url = excluded.source_url,
                is_crypto = excluded.is_crypto,
                crypto_product_id = excluded.crypto_product_id
        """).fetchall()
    finally:
        conn.unregister("_tmp_nl")
    return rows[0][0] if rows else 0


def get_listings(
    conn: duckdb.DuckDBPyConnection,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    listing_type: Optional[str] = None,
    exchange: Optional[str] = None,
    is_crypto: Optional[bool] = None,
    limit: int = 100,
) -> pd.DataFrame:
    """Query new listings with optional filters."""
    where = ["1=1"]
    params: list = []

    if start_date:
        where.append("listing_date >= ?")
        params.append(start_date)
    if end_date:
        where.append("listing_date <= ?")
        params.append(end_date)
    if listing_type:
        where.append("listing_type = ?")
        params.append(listing_type)
    if exchange:
        where.append("exchange = ?")
        params.append(exchange)
    if is_crypto is not None:
        where.append("is_crypto = ?")
        params.append(is_crypto)

    sql = f"""
        SELECT * FROM new_listings
        WHERE {' AND '.join(where)}
        ORDER BY listing_date DESC, ticker
        LIMIT ?
    """
    params.append(limit)
    return conn.execute(sql, params).df()


def get_listing_by_ticker(conn: duckdb.DuckDBPyConnection, ticker: str) -> Optional[Dict[str, Any]]:
    """Get listing details for a specific ticker."""
    row = conn.execute(
        "SELECT * FROM new_listings WHERE ticker = ? ORDER BY listing_date DESC LIMIT 1",
        [ticker.upper()],
    ).fetchone()
    if not row:
        return None
    cols = [desc[0] for desc in conn.description]
    return dict(zip(cols, row))


def get_upcoming_listings(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Get upcoming/filed IPOs (not yet priced)."""
    return conn.execute("""
        SELECT * FROM new_listings
        WHERE listing_type = 'Upcoming'
        ORDER BY listing_date DESC, ticker
    """).df()


def get_listing_summary(
    conn: duckdb.DuckDBPyConnection, start_date: str, end_date: str
) -> Dict[str, Any]:
    """Get summary statistics for a date range."""
    row = conn.execute("""
        SELECT
            COUNT(*) AS total,
            COUNT(DISTINCT ticker) AS tickers,
            COUNT(CASE WHEN listing_type = 'IPO' THEN 1 END) AS ipos,
            COUNT(CASE WHEN listing_type = 'Direct Listing' THEN 1 END) AS direct_listings,
            COUNT(CASE WHEN listing_type = 'SPAC' THEN 1 END) AS spacs,
            COUNT(CASE WHEN listing_type = 'Upcoming' THEN 1 END) AS upcoming,
            COUNT(CASE WHEN is_crypto THEN 1 END) AS crypto_count
        FROM new_listings
        WHERE listing_date >= ? AND listing_date <= ?
    """, [start_date, end_date]).fetchone()

    if not row:
        return {"total": 0}
    return {
        "start": start_date, "end": end_date,
        "total": int(row[0]), "tickers": int(row[1]),
        "ipos": int(row[2]), "direct_listings": int(row[3]),
        "spacs": int(row[4]), "upcoming": int(row[5]),
        "crypto_count": int(row[6]),
    }


def get_available_listing_dates(conn: duckdb.DuckDBPyConnection, limit: int = 60) -> List[str]:
    """Get list of dates with listing data."""
    rows = conn.execute("""
        SELECT DISTINCT listing_date FROM new_listings
        ORDER BY listing_date DESC LIMIT ?
    """, [limit]).fetchall()
    return [str(r[0]) for r in rows]


def get_monthly_listing_counts(conn: duckdb.DuckDBPyConnection, months: int = 12) -> pd.DataFrame:
    """Get monthly listing counts."""
    return conn.execute(f"""
        SELECT
            DATE_TRUNC('month', listing_date) AS month,
            COUNT(*) AS total,
            COUNT(CASE WHEN is_crypto THEN 1 END) AS crypto_count
        FROM new_listings
        WHERE listing_date >= DATE_TRUNC('month', CURRENT_DATE) - INTERVAL {months} MONTH
        GROUP BY month
        ORDER BY month DESC
    """).df()


# ═══════════════════════════════════════════════════════════════
#  Crypto Products CRUD
# ═══════════════════════════════════════════════════════════════

def upsert_crypto_products(conn: duckdb.DuckDBPyConnection, products: List[Dict[str, Any]]) -> int:
    """Batch insert/update crypto products. Returns row count."""
    if not products:
        return 0

    df = pd.DataFrame(products)
    needed = [
        "ticker", "company_name", "product_type", "underlying_asset",
        "listing_date", "expense_ratio", "aum", "market_cap",
        "description", "issuer", "is_active", "data_source",
    ]
    for col in needed:
        if col not in df.columns:
            df[col] = None

    sub = df[needed].copy()
    conn.register("_tmp_cp", sub)
    try:
        rows = conn.execute("""
            INSERT INTO crypto_products (
                ticker, company_name, product_type, underlying_asset,
                listing_date, expense_ratio, aum, market_cap,
                description, issuer, is_active, data_source, last_updated
            )
            SELECT
                ticker, company_name, product_type, underlying_asset,
                listing_date, expense_ratio, aum, market_cap,
                description, issuer, is_active, data_source, now()
            FROM _tmp_cp
            ON CONFLICT (ticker) DO UPDATE SET
                company_name = excluded.company_name,
                product_type = excluded.product_type,
                underlying_asset = excluded.underlying_asset,
                listing_date = COALESCE(excluded.listing_date, crypto_products.listing_date),
                expense_ratio = COALESCE(excluded.expense_ratio, crypto_products.expense_ratio),
                aum = COALESCE(excluded.aum, crypto_products.aum),
                market_cap = COALESCE(excluded.market_cap, crypto_products.market_cap),
                description = COALESCE(excluded.description, crypto_products.description),
                issuer = COALESCE(excluded.issuer, crypto_products.issuer),
                is_active = excluded.is_active,
                data_source = excluded.data_source,
                last_updated = now()
        """).fetchall()
    finally:
        conn.unregister("_tmp_cp")
    return rows[0][0] if rows else 0


def get_all_crypto_products(
    conn: duckdb.DuckDBPyConnection,
    product_type: Optional[str] = None,
    underlying_asset: Optional[str] = None,
    active_only: bool = True,
) -> pd.DataFrame:
    """Get all crypto products with optional filters."""
    where = ["1=1"]
    params: list = []

    if product_type:
        where.append("product_type = ?")
        params.append(product_type)
    if underlying_asset:
        where.append("underlying_asset = ?")
        params.append(underlying_asset)
    if active_only:
        where.append("is_active = true")

    return conn.execute(f"""
        SELECT * FROM crypto_products
        WHERE {' AND '.join(where)}
        ORDER BY product_type, ticker
    """, params).df()


def get_crypto_product_by_ticker(
    conn: duckdb.DuckDBPyConnection, ticker: str
) -> Optional[Dict[str, Any]]:
    """Get a single crypto product by ticker."""
    row = conn.execute(
        "SELECT * FROM crypto_products WHERE ticker = ?", [ticker.upper()]
    ).fetchone()
    if not row:
        return None
    cols = [desc[0] for desc in conn.description]
    return dict(zip(cols, row))


def get_crypto_stats(conn: duckdb.DuckDBPyConnection) -> Dict[str, Any]:
    """Get crypto product statistics."""
    total = conn.execute("SELECT COUNT(*) FROM crypto_products WHERE is_active = true").fetchone()
    by_type = conn.execute("""
        SELECT product_type, COUNT(*) AS cnt
        FROM crypto_products WHERE is_active = true
        GROUP BY product_type ORDER BY cnt DESC
    """).df()
    by_asset = conn.execute("""
        SELECT underlying_asset, COUNT(*) AS cnt
        FROM crypto_products WHERE is_active = true
        GROUP BY underlying_asset ORDER BY cnt DESC
    """).df()

    return {
        "total": int(total[0]) if total else 0,
        "by_type": by_type.to_dict(orient="records"),
        "by_asset": by_asset.to_dict(orient="records"),
    }


def get_crypto_tickers_set(conn: duckdb.DuckDBPyConnection) -> set:
    """Get set of all crypto tickers for fast lookup."""
    rows = conn.execute(
        "SELECT ticker FROM crypto_products WHERE is_active = true"
    ).fetchall()
    return {str(r[0]) for r in rows}


def mark_listings_as_crypto(conn: duckdb.DuckDBPyConnection) -> int:
    """Update new_listings.is_crypto based on crypto_products table. Returns count updated."""
    rows = conn.execute("""
        UPDATE new_listings
        SET is_crypto = true,
            crypto_product_id = (
                SELECT id FROM crypto_products cp
                WHERE cp.ticker = new_listings.ticker AND cp.is_active = true
                LIMIT 1
            )
        WHERE ticker IN (SELECT ticker FROM crypto_products WHERE is_active = true)
        AND is_crypto = false
    """).fetchall()
    return rows[0][0] if rows else 0


# ═══════════════════════════════════════════════════════════════
#  Fetch Log
# ═══════════════════════════════════════════════════════════════

def log_fetch_start(conn: duckdb.DuckDBPyConnection, fetch_date: str, source: str = "nasdaq_ipo") -> int:
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


def log_fetch_end(conn: duckdb.DuckDBPyConnection, log_id: int,
                  items_checked: int = 0, new_items: int = 0,
                  status: str = "ok", error: str = "") -> None:
    """Update fetch log with completion status."""
    conn.execute(
        """UPDATE fetch_log
           SET items_checked = ?, new_items = ?, status = ?,
               error_message = ?, completed_at = now()
           WHERE id = ?""",
        [items_checked, new_items, status, error or None, log_id],
    )


def cleanup_old_records(conn: duckdb.DuckDBPyConnection, retention_days: int = LOOKBACK_DAYS) -> int:
    """Delete listing records older than retention_days. Returns count deleted."""
    cutoff = date.today() - timedelta(days=retention_days)
    rows = conn.execute(
        "DELETE FROM new_listings WHERE listing_date < ? AND listing_type != 'Upcoming'",
        [cutoff.isoformat()],
    ).fetchall()
    count = rows[0][0] if rows else 0
    if count:
        conn.execute("CHECKPOINT")
    return count


def get_listing_count(conn: duckdb.DuckDBPyConnection) -> int:
    """Get total new listings count."""
    row = conn.execute("SELECT COUNT(*) FROM new_listings").fetchone()
    return int(row[0]) if row else 0


def get_crypto_product_count(conn: duckdb.DuckDBPyConnection) -> int:
    """Get total crypto products count."""
    row = conn.execute("SELECT COUNT(*) FROM crypto_products WHERE is_active = true").fetchone()
    return int(row[0]) if row else 0


def get_last_fetch_date(conn: duckdb.DuckDBPyConnection, source: str = "nasdaq_ipo") -> Optional[str]:
    """Get the most recent fetch date for a source."""
    row = conn.execute(
        "SELECT MAX(fetch_date) FROM fetch_log WHERE source = ? AND status = 'ok'",
        [source],
    ).fetchone()
    return str(row[0]) if row and row[0] else None


def get_fetch_status(conn: duckdb.DuckDBPyConnection, days: int = 7) -> pd.DataFrame:
    """Get recent fetch log entries."""
    return conn.execute("""
        SELECT fetch_date, source, items_checked, new_items,
               status, started_at, completed_at
        FROM fetch_log
        ORDER BY fetch_date DESC
        LIMIT ?
    """, [days]).df()


def get_crypto_new_additions(conn: duckdb.DuckDBPyConnection, days: int = 30) -> pd.DataFrame:
    """Get recently added crypto products."""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    return conn.execute("""
        SELECT * FROM crypto_products
        WHERE created_at >= ?
        ORDER BY created_at DESC
    """, [cutoff]).df()


# ═══════════════════════════════════════════════════════════════
#  Insider Trades CRUD
# ═══════════════════════════════════════════════════════════════

def upsert_insider_trades(conn: duckdb.DuckDBPyConnection, trades: List[Dict[str, Any]]) -> int:
    if not trades:
        return 0
    df = pd.DataFrame(trades)
    needed = ["ticker", "company_name", "insider_name", "insider_title",
              "transaction_type", "shares", "price_per_share", "total_value",
              "shares_owned_after", "filing_date", "transaction_date",
              "is_10b5_1", "source_url"]
    for col in needed:
        if col not in df.columns:
            df[col] = None
    sub = df[needed].copy()
    conn.register("_tmp_it", sub)
    try:
        rows = conn.execute("""
            INSERT INTO insider_trades (ticker, company_name, insider_name, insider_title,
                transaction_type, shares, price_per_share, total_value,
                shares_owned_after, filing_date, transaction_date, is_10b5_1, source_url)
            SELECT ticker, company_name, insider_name, insider_title,
                transaction_type, shares, price_per_share, total_value,
                shares_owned_after, filing_date, transaction_date, is_10b5_1, source_url
            FROM _tmp_it
            ON CONFLICT (ticker, insider_name, filing_date, transaction_type, shares) DO NOTHING
        """).fetchall()
    finally:
        conn.unregister("_tmp_it")
    return rows[0][0] if rows else 0


def get_insider_trades(conn, ticker=None, start_date=None, end_date=None, limit=100):
    where = ["1=1"]; params = []
    if ticker: where.append("ticker = ?"); params.append(ticker.upper())
    if start_date: where.append("filing_date >= ?"); params.append(start_date)
    if end_date: where.append("filing_date <= ?"); params.append(end_date)
    params.append(limit)
    return conn.execute(f"""
        SELECT * FROM insider_trades WHERE {' AND '.join(where)}
        ORDER BY filing_date DESC LIMIT ?
    """, params).df()


# ═══════════════════════════════════════════════════════════════
#  Earnings Calendar CRUD
# ═══════════════════════════════════════════════════════════════

def upsert_earnings(conn: duckdb.DuckDBPyConnection, earnings: List[Dict[str, Any]]) -> int:
    if not earnings:
        return 0
    df = pd.DataFrame(earnings)
    needed = ["ticker", "company_name", "report_type", "fiscal_period_end", "filing_date", "source_url"]
    for col in needed:
        if col not in df.columns:
            df[col] = None
    sub = df[needed].copy()
    conn.register("_tmp_ec", sub)
    try:
        rows = conn.execute("""
            INSERT INTO earnings_calendar (ticker, company_name, report_type, fiscal_period_end, filing_date, source_url)
            SELECT ticker, company_name, report_type, fiscal_period_end, filing_date, source_url
            FROM _tmp_ec
            ON CONFLICT (ticker, report_type, filing_date) DO NOTHING
        """).fetchall()
    finally:
        conn.unregister("_tmp_ec")
    return rows[0][0] if rows else 0


def get_earnings(conn, ticker=None, start_date=None, end_date=None, report_type=None, limit=100):
    where = ["1=1"]; params = []
    if ticker: where.append("ticker = ?"); params.append(ticker.upper())
    if start_date: where.append("filing_date >= ?"); params.append(start_date)
    if end_date: where.append("filing_date <= ?"); params.append(end_date)
    if report_type: where.append("report_type = ?"); params.append(report_type)
    params.append(limit)
    return conn.execute(f"""
        SELECT * FROM earnings_calendar WHERE {' AND '.join(where)}
        ORDER BY filing_date DESC LIMIT ?
    """, params).df()


def get_upcoming_earnings(conn, limit=50):
    return conn.execute("""
        SELECT * FROM earnings_calendar
        WHERE filing_date >= CURRENT_DATE
        ORDER BY filing_date ASC LIMIT ?
    """, [limit]).df()


# ═══════════════════════════════════════════════════════════════
#  Institutional Holdings CRUD
# ═══════════════════════════════════════════════════════════════

def upsert_holdings(conn: duckdb.DuckDBPyConnection, holdings: List[Dict[str, Any]]) -> int:
    if not holdings:
        return 0
    df = pd.DataFrame(holdings)
    needed = ["filer_cik", "filer_name", "ticker", "cusip", "security_name",
              "shares", "market_value", "quarter_end", "filing_date", "source_url"]
    for col in needed:
        if col not in df.columns:
            df[col] = None
    sub = df[needed].copy()
    conn.register("_tmp_ih", sub)
    try:
        rows = conn.execute("""
            INSERT INTO institutional_holdings (filer_cik, filer_name, ticker, cusip, security_name,
                shares, market_value, quarter_end, filing_date, source_url)
            SELECT filer_cik, filer_name, ticker, cusip, security_name,
                shares, market_value, quarter_end, filing_date, source_url
            FROM _tmp_ih
            ON CONFLICT DO NOTHING
        """).fetchall()
    finally:
        conn.unregister("_tmp_ih")
    return rows[0][0] if rows else 0


def get_holdings(conn, ticker=None, filer_cik=None, quarter_end=None, limit=100):
    where = ["1=1"]; params = []
    if ticker: where.append("ticker = ?"); params.append(ticker.upper())
    if filer_cik: where.append("filer_cik = ?"); params.append(filer_cik)
    if quarter_end: where.append("quarter_end = ?"); params.append(quarter_end)
    params.append(limit)
    return conn.execute(f"""
        SELECT * FROM institutional_holdings WHERE {' AND '.join(where)}
        ORDER BY quarter_end DESC, market_value DESC LIMIT ?
    """, params).df()


# ═══════════════════════════════════════════════════════════════
#  Short Interest CRUD
# ═══════════════════════════════════════════════════════════════

def upsert_short_interest(conn: duckdb.DuckDBPyConnection, records: List[Dict[str, Any]]) -> int:
    if not records:
        return 0
    df = pd.DataFrame(records)
    needed = ["ticker", "settlement_date", "short_interest", "avg_daily_volume",
              "days_to_cover", "short_pct_float", "source"]
    for col in needed:
        if col not in df.columns:
            df[col] = None
    sub = df[needed].copy()
    conn.register("_tmp_si", sub)
    try:
        rows = conn.execute("""
            INSERT INTO short_interest (ticker, settlement_date, short_interest,
                avg_daily_volume, days_to_cover, short_pct_float, source)
            SELECT ticker, settlement_date, short_interest,
                avg_daily_volume, days_to_cover, short_pct_float, source
            FROM _tmp_si
            ON CONFLICT (ticker, settlement_date) DO UPDATE SET
                short_interest = excluded.short_interest,
                avg_daily_volume = excluded.avg_daily_volume,
                days_to_cover = excluded.days_to_cover,
                short_pct_float = excluded.short_pct_float,
                source = excluded.source
        """).fetchall()
    finally:
        conn.unregister("_tmp_si")
    return rows[0][0] if rows else 0


def get_short_interest(conn, ticker=None, limit=50):
    where = ["1=1"]; params = []
    if ticker: where.append("ticker = ?"); params.append(ticker.upper())
    params.append(limit)
    return conn.execute(f"""
        SELECT * FROM short_interest WHERE {' AND '.join(where)}
        ORDER BY settlement_date DESC LIMIT ?
    """, params).df()


# ═══════════════════════════════════════════════════════════════
#  Fails-to-Deliver CRUD
# ═══════════════════════════════════════════════════════════════

def upsert_ftd(conn: duckdb.DuckDBPyConnection, records: List[Dict[str, Any]]) -> int:
    if not records:
        return 0
    df = pd.DataFrame(records)
    needed = ["ticker", "date", "quantity", "price", "source"]
    for col in needed:
        if col not in df.columns:
            df[col] = None
    sub = df[needed].copy()
    conn.register("_tmp_ftd", sub)
    try:
        rows = conn.execute("""
            INSERT INTO fails_to_deliver (ticker, date, quantity, price, source)
            SELECT ticker, date, quantity, price, source FROM _tmp_ftd
            ON CONFLICT DO NOTHING
        """).fetchall()
    finally:
        conn.unregister("_tmp_ftd")
    return rows[0][0] if rows else 0


def get_ftd(conn, ticker=None, start_date=None, end_date=None, limit=100):
    where = ["1=1"]; params = []
    if ticker: where.append("ticker = ?"); params.append(ticker.upper())
    if start_date: where.append("date >= ?"); params.append(start_date)
    if end_date: where.append("date <= ?"); params.append(end_date)
    params.append(limit)
    return conn.execute(f"""
        SELECT * FROM fails_to_deliver WHERE {' AND '.join(where)}
        ORDER BY date DESC LIMIT ?
    """, params).df()


# ═══════════════════════════════════════════════════════════════
#  ETF Flows CRUD
# ═══════════════════════════════════════════════════════════════

def upsert_etf_flows(conn: duckdb.DuckDBPyConnection, records: List[Dict[str, Any]]) -> int:
    if not records:
        return 0
    df = pd.DataFrame(records)
    needed = ["ticker", "date", "close_price", "volume", "aum", "estimated_flow", "flow_pct", "source"]
    for col in needed:
        if col not in df.columns:
            df[col] = None
    sub = df[needed].copy()
    conn.register("_tmp_ef", sub)
    try:
        rows = conn.execute("""
            INSERT INTO etf_flows (ticker, date, close_price, volume, aum, estimated_flow, flow_pct, source)
            SELECT ticker, date, close_price, volume, aum, estimated_flow, flow_pct, source
            FROM _tmp_ef
            ON CONFLICT (ticker, date) DO UPDATE SET
                close_price = excluded.close_price,
                volume = excluded.volume,
                aum = excluded.aum,
                estimated_flow = excluded.estimated_flow,
                flow_pct = excluded.flow_pct,
                source = excluded.source
        """).fetchall()
    finally:
        conn.unregister("_tmp_ef")
    return rows[0][0] if rows else 0


def get_etf_flows(conn, ticker=None, start_date=None, end_date=None, limit=30):
    where = ["1=1"]; params = []
    if ticker: where.append("ticker = ?"); params.append(ticker.upper())
    if start_date: where.append("date >= ?"); params.append(start_date)
    if end_date: where.append("date <= ?"); params.append(end_date)
    params.append(limit)
    return conn.execute(f"""
        SELECT * FROM etf_flows WHERE {' AND '.join(where)}
        ORDER BY date DESC LIMIT ?
    """, params).df()


# ═══════════════════════════════════════════════════════════════
#  Dividends CRUD
# ═══════════════════════════════════════════════════════════════

def upsert_dividends(conn, records):
    if not records: return 0
    df = pd.DataFrame(records)
    needed = ["ticker", "announcement_date", "ex_dividend_date", "pay_date",
              "dividend_rate", "dividend_yield", "last_dividend_value",
              "payout_ratio", "five_year_avg_yield", "source"]
    for col in needed:
        if col not in df.columns: df[col] = None
    sub = df[needed].copy()
    conn.register("_tmp_div", sub)
    try:
        rows = conn.execute("""
            INSERT INTO dividends (ticker, announcement_date, ex_dividend_date, pay_date,
                dividend_rate, dividend_yield, last_dividend_value, payout_ratio, five_year_avg_yield, source)
            SELECT ticker, announcement_date, ex_dividend_date, pay_date,
                dividend_rate, dividend_yield, last_dividend_value, payout_ratio, five_year_avg_yield, source
            FROM _tmp_div
            ON CONFLICT (ticker, ex_dividend_date) DO UPDATE SET
                dividend_rate = excluded.dividend_rate,
                dividend_yield = excluded.dividend_yield,
                pay_date = excluded.pay_date,
                payout_ratio = excluded.payout_ratio
        """).fetchall()
    finally:
        conn.unregister("_tmp_div")
    return rows[0][0] if rows else 0


def get_dividends(conn, ticker=None, start_date=None, end_date=None, limit=100):
    where = ["1=1"]; params = []
    if ticker: where.append("ticker = ?"); params.append(ticker.upper())
    if start_date: where.append("ex_dividend_date >= ?"); params.append(start_date)
    if end_date: where.append("ex_dividend_date <= ?"); params.append(end_date)
    params.append(limit)
    return conn.execute(f"""
        SELECT * FROM dividends WHERE {' AND '.join(where)}
        ORDER BY ex_dividend_date DESC LIMIT ?
    """, params).df()


# ═══════════════════════════════════════════════════════════════
#  Stock Splits CRUD
# ═══════════════════════════════════════════════════════════════

def upsert_splits(conn, records):
    if not records: return 0
    df = pd.DataFrame(records)
    needed = ["ticker", "split_date", "split_ratio", "source"]
    for col in needed:
        if col not in df.columns: df[col] = None
    sub = df[needed].copy()
    conn.register("_tmp_splits", sub)
    try:
        rows = conn.execute("""
            INSERT INTO stock_splits (ticker, split_date, split_ratio, source)
            SELECT ticker, split_date, split_ratio, source FROM _tmp_splits
            ON CONFLICT (ticker, split_date) DO NOTHING
        """).fetchall()
    finally:
        conn.unregister("_tmp_splits")
    return rows[0][0] if rows else 0


def get_splits(conn, ticker=None, limit=50):
    where = ["1=1"]; params = []
    if ticker: where.append("ticker = ?"); params.append(ticker.upper())
    params.append(limit)
    return conn.execute(f"""
        SELECT * FROM stock_splits WHERE {' AND '.join(where)}
        ORDER BY split_date DESC LIMIT ?
    """, params).df()


# ═══════════════════════════════════════════════════════════════
#  Trading Suspensions CRUD
# ═══════════════════════════════════════════════════════════════

def upsert_suspensions(conn, records):
    if not records: return 0
    df = pd.DataFrame(records)
    needed = ["ticker", "company_name", "suspension_type", "reason", "effective_date", "filing_date", "source_url"]
    for col in needed:
        if col not in df.columns: df[col] = None
    sub = df[needed].copy()
    conn.register("_tmp_susp", sub)
    try:
        rows = conn.execute("""
            INSERT INTO trading_suspensions (ticker, company_name, suspension_type, reason, effective_date, filing_date, source_url)
            SELECT ticker, company_name, suspension_type, reason, effective_date, filing_date, source_url FROM _tmp_susp
            ON CONFLICT DO NOTHING
        """).fetchall()
    finally:
        conn.unregister("_tmp_susp")
    return rows[0][0] if rows else 0


def get_suspensions(conn, ticker=None, start_date=None, end_date=None, limit=100):
    where = ["1=1"]; params = []
    if ticker: where.append("ticker = ?"); params.append(ticker.upper())
    if start_date: where.append("filing_date >= ?"); params.append(start_date)
    if end_date: where.append("filing_date <= ?"); params.append(end_date)
    params.append(limit)
    return conn.execute(f"""
        SELECT * FROM trading_suspensions WHERE {' AND '.join(where)}
        ORDER BY filing_date DESC LIMIT ?
    """, params).df()


# ═══════════════════════════════════════════════════════════════
#  Enforcement Actions CRUD
# ═══════════════════════════════════════════════════════════════

def upsert_enforcement(conn, records):
    if not records: return 0
    df = pd.DataFrame(records)
    needed = ["enforcement_type", "entity_name", "ticker", "penalty_amount", "description", "filing_date", "source_url"]
    for col in needed:
        if col not in df.columns: df[col] = None
    sub = df[needed].copy()
    conn.register("_tmp_enf", sub)
    try:
        rows = conn.execute("""
            INSERT INTO enforcement_actions (enforcement_type, entity_name, ticker, penalty_amount, description, filing_date, source_url)
            SELECT enforcement_type, entity_name, ticker, penalty_amount, description, filing_date, source_url FROM _tmp_enf
            ON CONFLICT DO NOTHING
        """).fetchall()
    finally:
        conn.unregister("_tmp_enf")
    return rows[0][0] if rows else 0


def get_enforcement(conn, enforcement_type=None, start_date=None, end_date=None, limit=100):
    where = ["1=1"]; params = []
    if enforcement_type: where.append("enforcement_type = ?"); params.append(enforcement_type)
    if start_date: where.append("filing_date >= ?"); params.append(start_date)
    if end_date: where.append("filing_date <= ?"); params.append(end_date)
    params.append(limit)
    return conn.execute(f"""
        SELECT * FROM enforcement_actions WHERE {' AND '.join(where)}
        ORDER BY filing_date DESC LIMIT ?
    """, params).df()


# ═══════════════════════════════════════════════════════════════
#  Threshold Securities CRUD
# ═══════════════════════════════════════════════════════════════

def upsert_threshold_securities(conn, records):
    if not records: return 0
    df = pd.DataFrame(records)
    needed = ["ticker", "security_name", "market_category", "is_threshold", "date", "source"]
    for col in needed:
        if col not in df.columns: df[col] = None
    sub = df[needed].copy()
    conn.register("_tmp_ts", sub)
    try:
        rows = conn.execute("""
            INSERT INTO threshold_securities (ticker, security_name, market_category, is_threshold, date, source)
            SELECT ticker, security_name, market_category, is_threshold, date, source FROM _tmp_ts
            ON CONFLICT (ticker, date) DO UPDATE SET is_threshold = excluded.is_threshold
        """).fetchall()
    finally:
        conn.unregister("_tmp_ts")
    return rows[0][0] if rows else 0


def get_threshold_securities(conn, ticker=None, date=None, limit=100):
    where = ["1=1"]; params = []
    if ticker: where.append("ticker = ?"); params.append(ticker.upper())
    if date: where.append("date = ?"); params.append(date)
    params.append(limit)
    return conn.execute(f"""
        SELECT * FROM threshold_securities WHERE {' AND '.join(where)}
        ORDER BY date DESC LIMIT ?
    """, params).df()


# ═══════════════════════════════════════════════════════════════
#  ATS Filings CRUD
# ═══════════════════════════════════════════════════════════════

def upsert_ats_filings(conn, records):
    if not records: return 0
    df = pd.DataFrame(records)
    needed = ["ats_name", "filer_cik", "filer_name", "filing_type",
              "volume_estimate", "securities_traded", "description", "filing_date", "source_url"]
    for col in needed:
        if col not in df.columns: df[col] = None
    sub = df[needed].copy()
    conn.register("_tmp_ats", sub)
    try:
        rows = conn.execute("""
            INSERT INTO ats_filings (ats_name, filer_cik, filer_name, filing_type,
                volume_estimate, securities_traded, description, filing_date, source_url)
            SELECT ats_name, filer_cik, filer_name, filing_type,
                volume_estimate, securities_traded, description, filing_date, source_url FROM _tmp_ats
            ON CONFLICT DO NOTHING
        """).fetchall()
    finally:
        conn.unregister("_tmp_ats")
    return rows[0][0] if rows else 0


def get_ats_filings(conn, filer_cik=None, start_date=None, end_date=None, limit=50):
    where = ["1=1"]; params = []
    if filer_cik: where.append("filer_cik = ?"); params.append(filer_cik)
    if start_date: where.append("filing_date >= ?"); params.append(start_date)
    if end_date: where.append("filing_date <= ?"); params.append(end_date)
    params.append(limit)
    return conn.execute(f"""
        SELECT * FROM ats_filings WHERE {' AND '.join(where)}
        ORDER BY filing_date DESC LIMIT ?
    """, params).df()


# ═══════════════════════════════════════════════════════════════
#  Short Sale Activity CRUD
# ═══════════════════════════════════════════════════════════════

def upsert_short_activity(conn, records):
    if not records: return 0
    df = pd.DataFrame(records)
    needed = ["ticker", "date", "short_interest", "short_pct_float", "days_to_cover",
              "avg_volume", "float_shares", "short_change_pct", "insider_ownership_pct",
              "institutional_ownership_pct", "risk_level", "squeeze_score", "source"]
    for col in needed:
        if col not in df.columns: df[col] = None
    sub = df[needed].copy()
    conn.register("_tmp_ssa", sub)
    try:
        rows = conn.execute("""
            INSERT INTO short_sale_activity (ticker, date, short_interest, short_pct_float, days_to_cover,
                avg_volume, float_shares, short_change_pct, insider_ownership_pct,
                institutional_ownership_pct, risk_level, squeeze_score, source)
            SELECT ticker, date, short_interest, short_pct_float, days_to_cover,
                avg_volume, float_shares, short_change_pct, insider_ownership_pct,
                institutional_ownership_pct, risk_level, squeeze_score, source FROM _tmp_ssa
            ON CONFLICT (ticker, date) DO UPDATE SET
                short_interest = excluded.short_interest,
                short_pct_float = excluded.short_pct_float,
                days_to_cover = excluded.days_to_cover,
                short_change_pct = excluded.short_change_pct,
                risk_level = excluded.risk_level,
                squeeze_score = excluded.squeeze_score
        """).fetchall()
    finally:
        conn.unregister("_tmp_ssa")
    return rows[0][0] if rows else 0


def get_short_activity(conn, ticker=None, risk_level=None, limit=50):
    where = ["1=1"]; params = []
    if ticker: where.append("ticker = ?"); params.append(ticker.upper())
    if risk_level: where.append("risk_level = ?"); params.append(risk_level)
    params.append(limit)
    return conn.execute(f"""
        SELECT * FROM short_sale_activity WHERE {' AND '.join(where)}
        ORDER BY squeeze_score DESC, date DESC LIMIT ?
    """, params).df()


# ═══════════════════════════════════════════════════════════════
#  Lockup Expiry CRUD
# ═══════════════════════════════════════════════════════════════

def upsert_lockup_expiry(conn, records):
    if not records: return 0
    df = pd.DataFrame(records)
    needed = ["ticker", "company_name", "listing_date", "listing_type",
              "lockup_end_date", "lockup_period_days", "days_remaining",
              "estimated_shares_unlocking", "estimated_value", "status"]
    for col in needed:
        if col not in df.columns: df[col] = None
    sub = df[needed].copy()
    conn.register("_tmp_lockup", sub)
    try:
        rows = conn.execute("""
            INSERT INTO lockup_expiry (ticker, company_name, listing_date, listing_type,
                lockup_end_date, lockup_period_days, days_remaining,
                estimated_shares_unlocking, estimated_value, status)
            SELECT ticker, company_name, listing_date, listing_type,
                lockup_end_date, lockup_period_days, days_remaining,
                estimated_shares_unlocking, estimated_value, status FROM _tmp_lockup
            ON CONFLICT (ticker, lockup_end_date) DO UPDATE SET
                days_remaining = excluded.days_remaining,
                status = excluded.status,
                estimated_shares_unlocking = excluded.estimated_shares_unlocking,
                estimated_value = excluded.estimated_value
        """).fetchall()
    finally:
        conn.unregister("_tmp_lockup")
    return rows[0][0] if rows else 0


def get_lockup_expiry(conn, ticker=None, status=None, limit=50):
    where = ["1=1"]; params = []
    if ticker: where.append("ticker = ?"); params.append(ticker.upper())
    if status: where.append("status = ?"); params.append(status)
    params.append(limit)
    return conn.execute(f"""
        SELECT * FROM lockup_expiry WHERE {' AND '.join(where)}
        ORDER BY days_remaining ASC LIMIT ?
    """, params).df()


# ═══════════════════════════════════════════════════════════════
#  Options Flow CRUD
# ═══════════════════════════════════════════════════════════════

def upsert_options_flow(conn, records):
    if not records: return 0
    df = pd.DataFrame(records)
    needed = ["ticker", "date", "expiration_date", "total_call_volume", "total_put_volume",
              "total_call_oi", "total_put_oi", "put_call_vol_ratio", "put_call_oi_ratio",
              "vol_oi_ratio", "max_call_strike", "max_call_volume", "max_put_strike",
              "max_put_volume", "is_unusual", "sentiment", "source"]
    for col in needed:
        if col not in df.columns: df[col] = None
    sub = df[needed].copy()
    conn.register("_tmp_opt", sub)
    try:
        rows = conn.execute("""
            INSERT INTO options_flow (ticker, date, expiration_date, total_call_volume, total_put_volume,
                total_call_oi, total_put_oi, put_call_vol_ratio, put_call_oi_ratio,
                vol_oi_ratio, max_call_strike, max_call_volume, max_put_strike,
                max_put_volume, is_unusual, sentiment, source)
            SELECT ticker, date, expiration_date, total_call_volume, total_put_volume,
                total_call_oi, total_put_oi, put_call_vol_ratio, put_call_oi_ratio,
                vol_oi_ratio, max_call_strike, max_call_volume, max_put_strike,
                max_put_volume, is_unusual, sentiment, source FROM _tmp_opt
            ON CONFLICT (ticker, date, expiration_date) DO UPDATE SET
                total_call_volume = excluded.total_call_volume,
                total_put_volume = excluded.total_put_volume,
                total_call_oi = excluded.total_call_oi,
                total_put_oi = excluded.total_put_oi,
                put_call_vol_ratio = excluded.put_call_vol_ratio,
                put_call_oi_ratio = excluded.put_call_oi_ratio,
                vol_oi_ratio = excluded.vol_oi_ratio,
                is_unusual = excluded.is_unusual,
                sentiment = excluded.sentiment
        """).fetchall()
    finally:
        conn.unregister("_tmp_opt")
    return rows[0][0] if rows else 0


def get_options_flow(conn, ticker=None, is_unusual=None, limit=50):
    where = ["1=1"]; params = []
    if ticker: where.append("ticker = ?"); params.append(ticker.upper())
    if is_unusual is not None: where.append("is_unusual = ?"); params.append(is_unusual)
    params.append(limit)
    return conn.execute(f"""
        SELECT * FROM options_flow WHERE {' AND '.join(where)}
        ORDER BY date DESC, vol_oi_ratio DESC LIMIT ?
    """, params).df()
