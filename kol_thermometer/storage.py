"""
Storage layer — DuckDB-backed store for KOL thermometer data.

Tables:
  kols            — auto-discovered KOLs with scores, tiers, weights
  kol_posts       — fetched posts with engagement metrics
  stock_mentions  — parsed stock mentions with LLM sentiment
  thermometer     — daily aggregated heat per stock
  fetch_log       — fetch audit log
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import duckdb
import pandas as pd

from kol_thermometer.config import DB_PATH


def _conn(db_path: Optional[str] = None, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    path = db_path or DB_PATH
    return duckdb.connect(path, read_only=read_only)


def init_db(db_path: Optional[str] = None, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """Create tables and indexes if they don't exist. Returns connection."""
    conn = _conn(db_path, read_only=read_only)
    if read_only:
        return conn

    conn.execute("CREATE SEQUENCE IF NOT EXISTS kols_seq START 1")
    conn.execute("CREATE SEQUENCE IF NOT EXISTS kol_posts_seq START 1")
    conn.execute("CREATE SEQUENCE IF NOT EXISTS stock_mentions_seq START 1")
    conn.execute("CREATE SEQUENCE IF NOT EXISTS thermometer_seq START 1")
    conn.execute("CREATE SEQUENCE IF NOT EXISTS fetch_log_seq START 1")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS kols (
            id INTEGER PRIMARY KEY DEFAULT nextval('kols_seq'),
            platform TEXT NOT NULL,
            username TEXT NOT NULL,
            display_name TEXT,
            profile_url TEXT,
            followers INTEGER DEFAULT 0,
            avg_likes REAL DEFAULT 0,
            avg_comments REAL DEFAULT 0,
            avg_shares REAL DEFAULT 0,
            avg_views REAL DEFAULT 0,
            posts_per_week REAL DEFAULT 0,
            account_age_days INTEGER DEFAULT 0,
            verified INTEGER DEFAULT 0,
            stock_mention_ratio REAL DEFAULT 0,
            mention_price_corr REAL DEFAULT 0,
            total_score REAL DEFAULT 0,
            score_reach REAL DEFAULT 0,
            score_engagement REAL DEFAULT 0,
            score_consistency REAL DEFAULT 0,
            score_relevance REAL DEFAULT 0,
            score_impact REAL DEFAULT 0,
            tier TEXT DEFAULT 'D',
            base_weight REAL DEFAULT 0.15,
            first_seen_date TEXT,
            last_post_date TEXT,
            last_active_date TEXT,
            is_active INTEGER DEFAULT 1,
            UNIQUE(platform, username)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS kol_posts (
            id INTEGER PRIMARY KEY DEFAULT nextval('kol_posts_seq'),
            kol_id INTEGER,
            platform TEXT NOT NULL,
            post_id TEXT NOT NULL,
            post_url TEXT,
            title TEXT,
            content TEXT,
            posted_at TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            likes INTEGER DEFAULT 0,
            comments INTEGER DEFAULT 0,
            shares INTEGER DEFAULT 0,
            views INTEGER DEFAULT 0,
            UNIQUE(platform, post_id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS stock_mentions (
            id INTEGER PRIMARY KEY DEFAULT nextval('stock_mentions_seq'),
            post_id INTEGER,
            kol_id INTEGER,
            stock_code TEXT NOT NULL,
            stock_name TEXT,
            market TEXT,
            mention_context TEXT,
            sentiment_score REAL DEFAULT 0,
            sentiment_label TEXT,
            confidence REAL DEFAULT 0,
            UNIQUE(post_id, stock_code)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS thermometer (
            id INTEGER PRIMARY KEY DEFAULT nextval('thermometer_seq'),
            date TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            stock_name TEXT,
            market TEXT,
            mention_count INTEGER DEFAULT 0,
            unique_kols INTEGER DEFAULT 0,
            heat_score REAL DEFAULT 0,
            raw_heat REAL DEFAULT 0,
            sentiment_bias REAL DEFAULT 0,
            positive_count INTEGER DEFAULT 0,
            negative_count INTEGER DEFAULT 0,
            momentum REAL DEFAULT 0,
            top_kols TEXT,
            UNIQUE(date, stock_code)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS fetch_log (
            id INTEGER PRIMARY KEY DEFAULT nextval('fetch_log_seq'),
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

    conn.execute("CREATE INDEX IF NOT EXISTS idx_kols_platform ON kols(platform)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kols_tier ON kols(tier)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kols_active ON kols(is_active)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_posts_kol ON kol_posts(kol_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_posts_date ON kol_posts(posted_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_posts_platform ON kol_posts(platform)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mentions_stock ON stock_mentions(stock_code)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mentions_post ON stock_mentions(post_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_thermo_stock ON thermometer(stock_code)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_thermo_date ON thermometer(date)")

    # Migration: add sentiment breakdown columns to existing tables
    for col in ["positive_count", "negative_count"]:
        try:
            conn.execute(f"ALTER TABLE thermometer ADD COLUMN {col} INTEGER DEFAULT 0")
        except Exception:
            pass  # column already exists

    return conn


# ── KOLs ──────────────────────────────────────────────────────

def upsert_kol(conn: duckdb.DuckDBPyConnection, data: Dict[str, Any]) -> int:
    """Upsert a single KOL. Returns KOL id."""
    row = conn.execute("""
        INSERT INTO kols (platform, username, display_name, profile_url, followers,
                          avg_likes, avg_comments, avg_shares, avg_views,
                          posts_per_week, account_age_days, verified,
                          stock_mention_ratio, mention_price_corr,
                          total_score, score_reach, score_engagement,
                          score_consistency, score_relevance, score_impact,
                          tier, base_weight, first_seen_date, last_post_date,
                          last_active_date, is_active)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (platform, username) DO UPDATE SET
            display_name = excluded.display_name,
            followers = excluded.followers,
            avg_likes = excluded.avg_likes,
            avg_comments = excluded.avg_comments,
            avg_shares = excluded.avg_shares,
            avg_views = excluded.avg_views,
            posts_per_week = excluded.posts_per_week,
            account_age_days = excluded.account_age_days,
            stock_mention_ratio = excluded.stock_mention_ratio,
            mention_price_corr = excluded.mention_price_corr,
            total_score = excluded.total_score,
            score_reach = excluded.score_reach,
            score_engagement = excluded.score_engagement,
            score_consistency = excluded.score_consistency,
            score_relevance = excluded.score_relevance,
            score_impact = excluded.score_impact,
            tier = excluded.tier,
            base_weight = excluded.base_weight,
            last_post_date = excluded.last_post_date,
            last_active_date = excluded.last_active_date,
            is_active = excluded.is_active
        RETURNING id
    """, [
        data["platform"], data["username"], data.get("display_name", ""),
        data.get("profile_url", ""), data.get("followers", 0),
        data.get("avg_likes", 0.0), data.get("avg_comments", 0.0),
        data.get("avg_shares", 0.0), data.get("avg_views", 0.0),
        data.get("posts_per_week", 0.0), data.get("account_age_days", 0),
        data.get("verified", 0), data.get("stock_mention_ratio", 0.0),
        data.get("mention_price_corr", 0.0),
        data.get("total_score", 0.0), data.get("score_reach", 0.0),
        data.get("score_engagement", 0.0), data.get("score_consistency", 0.0),
        data.get("score_relevance", 0.0), data.get("score_impact", 0.0),
        data.get("tier", "D"), data.get("base_weight", 0.15),
        data.get("first_seen_date", ""), data.get("last_post_date", ""),
        data.get("last_active_date", ""), data.get("is_active", 1),
    ]).fetchone()
    return row[0] if row else 0


def upsert_kols_batch(conn: duckdb.DuckDBPyConnection, kols: List[Dict[str, Any]]) -> int:
    """Batch upsert KOLs. Returns count of affected rows."""
    count = 0
    for k in kols:
        kid = upsert_kol(conn, k)
        if kid:
            count += 1
    return count


def get_kols(
    conn: duckdb.DuckDBPyConnection,
    platform: Optional[str] = None,
    tier: Optional[str] = None,
    is_active: Optional[int] = 1,
    limit: int = 100,
) -> pd.DataFrame:
    """List KOLs with optional filters."""
    conditions = []
    params: List[Any] = []
    if platform:
        conditions.append("platform = ?")
        params.append(platform)
    if tier:
        conditions.append("tier = ?")
        params.append(tier)
    if is_active is not None:
        conditions.append("is_active = ?")
        params.append(is_active)

    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    return conn.execute(f"""
        SELECT * FROM kols {where}
        ORDER BY total_score DESC
        LIMIT ?
    """, params + [limit]).df()


def get_kol_by_id(conn: duckdb.DuckDBPyConnection, kol_id: int) -> Optional[Dict[str, Any]]:
    """Get a single KOL by ID."""
    row = conn.execute("SELECT * FROM kols WHERE id = ?", [kol_id]).fetchone()
    if row is None:
        return None
    col_names = [desc[0] for desc in conn.description]
    return dict(zip(col_names, row))


def get_kol_by_username(conn: duckdb.DuckDBPyConnection, platform: str, username: str) -> Optional[Dict[str, Any]]:
    """Get a KOL by platform + username."""
    row = conn.execute(
        "SELECT * FROM kols WHERE platform = ? AND username = ?",
        [platform, username],
    ).fetchone()
    if row is None:
        return None
    col_names = [desc[0] for desc in conn.description]
    return dict(zip(col_names, row))


def deactivate_inactive_kols(conn: duckdb.DuckDBPyConnection) -> int:
    """Deactivate KOLs inactive for >90 days. Returns count."""
    row = conn.execute("""
        UPDATE kols SET is_active = 0
        WHERE is_active = 1
          AND last_active_date < CURRENT_DATE - INTERVAL '90 days'
    """).fetchone()
    return row[0] if row else 0


def get_max_followers(conn: duckdb.DuckDBPyConnection, platform: str) -> int:
    """Get max follower count for a platform (for reach score normalization)."""
    row = conn.execute(
        "SELECT COALESCE(MAX(followers), 0) FROM kols WHERE platform = ? AND is_active = 1",
        [platform],
    ).fetchone()
    return int(row[0]) if row else 0


def touch_kol_activity(conn: duckdb.DuckDBPyConnection, kol_id: int, post_date: str) -> None:
    """Update last_post_date and last_active_date for a KOL."""
    conn.execute("""
        UPDATE kols SET
            last_post_date = GREATEST(COALESCE(last_post_date, ''), ?),
            last_active_date = GREATEST(COALESCE(last_active_date, ''), ?)
        WHERE id = ?
    """, [post_date, post_date, kol_id])


# ── Kol Posts ─────────────────────────────────────────────────

def upsert_posts_batch(conn: duckdb.DuckDBPyConnection, posts: List[Dict[str, Any]]) -> int:
    """Batch upsert posts. Returns count."""
    if not posts:
        return 0

    df = pd.DataFrame(posts)
    needed = ["kol_id", "platform", "post_id", "post_url", "title", "content",
              "posted_at", "fetched_at", "likes", "comments", "shares", "views"]
    for col in needed:
        if col not in df.columns:
            df[col] = None

    sub = df[needed].copy()
    conn.register("_tmp_kol_posts", sub)
    rows = conn.execute("""
        INSERT INTO kol_posts (kol_id, platform, post_id, post_url, title, content,
                               posted_at, fetched_at, likes, comments, shares, views)
        SELECT kol_id, platform, post_id, post_url, title, content,
               posted_at, fetched_at, likes, comments, shares, views
        FROM _tmp_kol_posts
        ON CONFLICT (platform, post_id) DO UPDATE SET
            likes = excluded.likes,
            comments = excluded.comments,
            shares = excluded.shares,
            views = excluded.views
    """).fetchall()
    conn.unregister("_tmp_kol_posts")
    return rows[0][0] if rows else 0


def get_posts_for_kol(
    conn: duckdb.DuckDBPyConnection, kol_id: int, limit: int = 50
) -> pd.DataFrame:
    """Get recent posts for a KOL."""
    return conn.execute("""
        SELECT * FROM kol_posts
        WHERE kol_id = ?
        ORDER BY posted_at DESC
        LIMIT ?
    """, [kol_id, limit]).df()


def get_recent_posts(
    conn: duckdb.DuckDBPyConnection, platform: Optional[str] = None, limit: int = 200
) -> pd.DataFrame:
    """Get recent posts across all KOLs."""
    if platform:
        return conn.execute("""
            SELECT p.*, k.username, k.display_name, k.tier
            FROM kol_posts p JOIN kols k ON p.kol_id = k.id
            WHERE p.platform = ?
            ORDER BY p.posted_at DESC LIMIT ?
        """, [platform, limit]).df()
    return conn.execute("""
        SELECT p.*, k.username, k.display_name, k.tier
        FROM kol_posts p JOIN kols k ON p.kol_id = k.id
        ORDER BY p.posted_at DESC LIMIT ?
    """, [limit]).df()


def get_posts_without_mentions(conn: duckdb.DuckDBPyConnection, limit: int = 100,
                                max_age_days: int = 7) -> pd.DataFrame:
    """Get posts that haven't been tagged for stock mentions yet.

    Only returns posts from the last max_age_days to avoid wasting LLM
    API calls on stale content that won't affect the thermometer anyway.
    """
    return conn.execute("""
        SELECT p.* FROM kol_posts p
        LEFT JOIN stock_mentions m ON p.id = m.post_id
        WHERE m.id IS NULL
          AND CAST(p.posted_at AS DATE) >= CURRENT_DATE - ?
        ORDER BY p.fetched_at ASC
        LIMIT ?
    """, [max_age_days, limit]).df()


def post_exists(conn: duckdb.DuckDBPyConnection, platform: str, post_id: str) -> bool:
    """Check if a post already exists in DB."""
    row = conn.execute(
        "SELECT 1 FROM kol_posts WHERE platform = ? AND post_id = ?",
        [platform, post_id],
    ).fetchone()
    return row is not None


# ── Stock Mentions ────────────────────────────────────────────

def upsert_mentions_batch(conn: duckdb.DuckDBPyConnection, mentions: List[Dict[str, Any]]) -> int:
    """Batch upsert stock mentions. Returns count."""
    if not mentions:
        return 0

    df = pd.DataFrame(mentions)
    needed = ["post_id", "kol_id", "stock_code", "stock_name", "market",
              "mention_context", "sentiment_score", "sentiment_label", "confidence"]
    for col in needed:
        if col not in df.columns:
            df[col] = None

    sub = df[needed].copy()
    conn.register("_tmp_mentions", sub)
    rows = conn.execute("""
        INSERT INTO stock_mentions (post_id, kol_id, stock_code, stock_name, market,
                                    mention_context, sentiment_score, sentiment_label, confidence)
        SELECT post_id, kol_id, stock_code, stock_name, market,
               mention_context, sentiment_score, sentiment_label, confidence
        FROM _tmp_mentions
        ON CONFLICT (post_id, stock_code) DO NOTHING
    """).fetchall()
    conn.unregister("_tmp_mentions")
    return rows[0][0] if rows else 0


def get_mentions(
    conn: duckdb.DuckDBPyConnection,
    stock_code: Optional[str] = None,
    kol_id: Optional[int] = None,
    platform: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    limit: int = 100,
) -> pd.DataFrame:
    """Query stock mentions with filters."""
    conditions = []
    params: List[Any] = []
    if stock_code:
        conditions.append("m.stock_code = ?")
        params.append(stock_code)
    if kol_id:
        conditions.append("m.kol_id = ?")
        params.append(kol_id)
    if platform:
        conditions.append("p.platform = ?")
        params.append(platform)
    if start:
        conditions.append("p.posted_at >= ?")
        params.append(start)
    if end:
        conditions.append("p.posted_at <= ?")
        params.append(end)

    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    return conn.execute(f"""
        SELECT m.*, p.platform, p.title as post_title, p.posted_at,
               k.username, k.display_name, k.tier
        FROM stock_mentions m
        JOIN kol_posts p ON m.post_id = p.id
        JOIN kols k ON m.kol_id = k.id
        {where}
        ORDER BY p.posted_at DESC
        LIMIT ?
    """, params + [limit]).df()


def get_mentions_for_thermometer(
    conn: duckdb.DuckDBPyConnection, lookback_days: int = 7
) -> List[Dict[str, Any]]:
    """Get mentions within lookback window for thermometer calculation.

    Returns list of dicts with kol_weight, sentiment_score, posted_at, kol_id.
    """
    rows = conn.execute("""
        SELECT m.stock_code, m.kol_id, m.sentiment_score,
               k.base_weight as kol_weight, k.tier, p.posted_at, p.platform
        FROM stock_mentions m
        JOIN kol_posts p ON m.post_id = p.id
        JOIN kols k ON m.kol_id = k.id
        WHERE CAST(p.posted_at AS DATE) >= CURRENT_DATE - ?
          AND k.is_active = 1
        ORDER BY p.posted_at DESC
    """, [lookback_days]).fetchall()

    result = []
    for row in rows:
        result.append({
            "stock_code": row[0],
            "kol_id": row[1],
            "sentiment_score": row[2],
            "kol_weight": row[3],
            "tier": row[4],
            "posted_at": str(row[5]),
            "platform": row[6],
        })
    return result


# ── Thermometer ───────────────────────────────────────────────

def upsert_thermometer(conn: duckdb.DuckDBPyConnection, records: List[Dict[str, Any]]) -> int:
    """Batch upsert thermometer daily records. Returns count."""
    if not records:
        return 0

    df = pd.DataFrame(records)
    needed = ["date", "stock_code", "stock_name", "market", "mention_count",
              "unique_kols", "heat_score", "raw_heat", "sentiment_bias",
              "positive_count", "negative_count", "momentum", "top_kols"]
    for col in needed:
        if col not in df.columns:
            df[col] = None

    sub = df[needed].copy()
    conn.register("_tmp_thermo", sub)
    rows = conn.execute("""
        INSERT INTO thermometer (date, stock_code, stock_name, market, mention_count,
                                 unique_kols, heat_score, raw_heat, sentiment_bias,
                                 positive_count, negative_count, momentum, top_kols)
        SELECT date, stock_code, stock_name, market, mention_count,
               unique_kols, heat_score, raw_heat, sentiment_bias,
               positive_count, negative_count, momentum, top_kols
        FROM _tmp_thermo
        ON CONFLICT (date, stock_code) DO UPDATE SET
            mention_count = excluded.mention_count,
            unique_kols = excluded.unique_kols,
            heat_score = excluded.heat_score,
            raw_heat = excluded.raw_heat,
            sentiment_bias = excluded.sentiment_bias,
            positive_count = excluded.positive_count,
            negative_count = excluded.negative_count,
            momentum = excluded.momentum,
            top_kols = excluded.top_kols
    """).fetchall()
    conn.unregister("_tmp_thermo")
    return rows[0][0] if rows else 0


def get_thermometer(
    conn: duckdb.DuckDBPyConnection,
    date: Optional[str] = None,
    market: Optional[str] = None,
    min_heat: Optional[float] = None,
    limit: int = 50,
) -> pd.DataFrame:
    """Get current thermometer, optionally filtered."""
    conditions = []
    params: List[Any] = []
    if date:
        conditions.append("date = ?")
        params.append(date)
    else:
        conditions.append("date = (SELECT MAX(date) FROM thermometer)")
    if market:
        conditions.append("market = ?")
        params.append(market)
    if min_heat is not None:
        conditions.append("heat_score >= ?")
        params.append(min_heat)

    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    return conn.execute(f"""
        SELECT * FROM thermometer {where}
        ORDER BY heat_score DESC
        LIMIT ?
    """, params + [limit]).df()


def get_thermometer_history(
    conn: duckdb.DuckDBPyConnection,
    stock_code: str,
    days: int = 30,
) -> pd.DataFrame:
    """Get thermometer history for a specific stock."""
    return conn.execute("""
        SELECT * FROM thermometer
        WHERE stock_code = ?
        ORDER BY date DESC
        LIMIT ?
    """, [stock_code, days]).df()


def get_past_heats(
    conn: duckdb.DuckDBPyConnection, stock_code: str, exclude_date: str, days: int = 7
) -> List[float]:
    """Get past heat scores for momentum calculation."""
    rows = conn.execute("""
        SELECT heat_score FROM thermometer
        WHERE stock_code = ? AND date < ?
        ORDER BY date DESC
        LIMIT ?
    """, [stock_code, exclude_date, days]).fetchall()
    return [float(r[0]) for r in rows]


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


# ── Stats for MCP / health ────────────────────────────────────

def get_stats(conn: duckdb.DuckDBPyConnection) -> Dict[str, Any]:
    """Get database statistics for health/MCP status."""
    kol_count = conn.execute("SELECT COUNT(*) FROM kols WHERE is_active = 1").fetchone()
    post_count = conn.execute("SELECT COUNT(*) FROM kol_posts").fetchone()
    mention_count = conn.execute("SELECT COUNT(*) FROM stock_mentions").fetchone()
    thermo_days = conn.execute("SELECT COUNT(DISTINCT date) FROM thermometer").fetchone()
    tier_dist = conn.execute("""
        SELECT tier, COUNT(*) FROM kols WHERE is_active = 1
        GROUP BY tier ORDER BY tier
    """).df()
    platform_dist = conn.execute("""
        SELECT platform, COUNT(*) FROM kols WHERE is_active = 1
        GROUP BY platform ORDER BY COUNT(*) DESC
    """).df()

    return {
        "active_kols": int(kol_count[0]) if kol_count else 0,
        "total_posts": int(post_count[0]) if post_count else 0,
        "total_mentions": int(mention_count[0]) if mention_count else 0,
        "thermometer_days": int(thermo_days[0]) if thermo_days else 0,
        "tier_distribution": tier_dist.to_dict(orient="records"),
        "platform_distribution": platform_dist.to_dict(orient="records"),
    }
