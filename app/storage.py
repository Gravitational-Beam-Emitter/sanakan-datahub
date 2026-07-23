"""
Storage layer — DuckDB-backed time-series store for macroeconomic indicators.

Tables:
  indicators   — metadata for each data series
  observations — individual data points (indicator_id, date, value)
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import duckdb
import pandas as pd

DB_PATH = Path(os.environ.get("ECO_DATA_DB", Path(__file__).resolve().parent.parent / "eco_data.duckdb"))

_migrate_lock = threading.Lock()
_migrated = False


def _conn(db_path: str | Path | None = None, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    path = str(db_path or DB_PATH)
    return duckdb.connect(path, read_only=read_only)


def migrate_db(db_path: str | Path | None = None) -> None:
    """Run DDL migrations. Safe to call multiple times — runs only once."""
    global _migrated
    if _migrated:
        return
    with _migrate_lock:
        if _migrated:
            return
        conn = _conn(db_path)
        conn.execute("CREATE SEQUENCE IF NOT EXISTS seq_indicators_id")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS indicators (
                id INTEGER PRIMARY KEY DEFAULT nextval('seq_indicators_id'),
                source VARCHAR NOT NULL,
                name VARCHAR NOT NULL,
                method VARCHAR NOT NULL,
                params VARCHAR NOT NULL DEFAULT '{}',
                description VARCHAR,
                frequency VARCHAR,
                tags VARCHAR DEFAULT '',
                last_updated TIMESTAMP,
                UNIQUE(source, method, params)
            )
        """)
        # Migration: add tags column if missing
        cols = [r[0] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name='indicators'"
        ).fetchall()]
        if "tags" not in cols:
            conn.execute("ALTER TABLE indicators ADD COLUMN tags VARCHAR DEFAULT ''")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS observations (
                indicator_id INTEGER NOT NULL,
                date DATE NOT NULL,
                value DOUBLE NOT NULL,
                fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(indicator_id, date),
                FOREIGN KEY(indicator_id) REFERENCES indicators(id)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_obs_date ON observations(indicator_id, date)")
        # Migration: add notes column if missing
        obs_cols = [r[0] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name='observations'"
        ).fetchall()]
        if "notes" not in obs_cols:
            conn.execute("ALTER TABLE observations ADD COLUMN notes VARCHAR DEFAULT ''")
        conn.close()
        _migrated = True


# ── Name Screening tables ──────────────────────────────────────

_name_screening_migrated = False


def migrate_name_screening(db_path: str | Path | None = None) -> None:
    """Create name_screening and news_cache tables. Safe to call multiple times."""
    global _name_screening_migrated
    if _name_screening_migrated:
        return
    with _migrate_lock:
        if _name_screening_migrated:
            return
        conn = _conn(db_path)
        conn.execute("CREATE SEQUENCE IF NOT EXISTS seq_namescreening_id")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS name_screening (
                id INTEGER PRIMARY KEY DEFAULT nextval('seq_namescreening_id'),
                source VARCHAR NOT NULL,
                source_uid VARCHAR,
                name_en VARCHAR,
                name_cn VARCHAR,
                name_cn_norm VARCHAR,
                name_pinyin VARCHAR,
                name_type VARCHAR,
                pep_level VARCHAR,
                risk_category VARCHAR,
                aliases VARCHAR,
                programs VARCHAR,
                countries VARCHAR,
                addresses VARCHAR,
                source_date VARCHAR,
                notes VARCHAR,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(source, source_uid)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ns_name_en ON name_screening(name_en)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ns_name_cn ON name_screening(name_cn)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ns_name_cn_norm ON name_screening(name_cn_norm)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ns_pinyin ON name_screening(name_pinyin)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ns_source ON name_screening(source)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ns_risk ON name_screening(risk_category)")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS news_screening_cache (
                id INTEGER PRIMARY KEY DEFAULT nextval('seq_namescreening_id'),
                search_name VARCHAR NOT NULL,
                search_name_cn VARCHAR,
                source VARCHAR,
                title VARCHAR,
                url VARCHAR,
                published_date VARCHAR,
                snippet TEXT,
                matched_keywords VARCHAR,
                cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_nsc_name ON news_screening_cache(search_name, cached_at)")
        conn.close()
        _name_screening_migrated = True


def upsert_screening_entry(conn: duckdb.DuckDBPyConnection, entry: dict) -> int:
    """Insert or update a name screening entry. Returns id."""
    existing = conn.execute(
        "SELECT id FROM name_screening WHERE source=? AND source_uid=?",
        [entry["source"], entry.get("source_uid", "")]
    ).fetchone()
    if existing:
        eid = existing[0]
        sets = []
        vals = []
        for k in ("name_en", "name_cn", "name_cn_norm", "name_pinyin", "name_type",
                   "pep_level", "risk_category", "aliases", "programs", "countries",
                   "addresses", "source_date", "notes"):
            if k in entry:
                sets.append(f"{k}=?")
                vals.append(entry[k])
        if sets:
            sets.append("last_updated=CURRENT_TIMESTAMP")
            vals.append(eid)
            conn.execute(f"UPDATE name_screening SET {', '.join(sets)} WHERE id=?", vals)
        return eid

    result = conn.execute("""
        INSERT INTO name_screening (source, source_uid, name_en, name_cn, name_cn_norm,
            name_pinyin, name_type, pep_level, risk_category, aliases,
            programs, countries, addresses, source_date, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id
    """, [
        entry["source"], entry.get("source_uid", ""),
        entry.get("name_en"), entry.get("name_cn"), entry.get("name_cn_norm"),
        entry.get("name_pinyin"), entry.get("name_type"), entry.get("pep_level"),
        entry.get("risk_category"), entry.get("aliases"),
        entry.get("programs"), entry.get("countries"), entry.get("addresses"),
        entry.get("source_date"), entry.get("notes"),
    ])
    return result.fetchone()[0]


def search_screening_by_name(
    conn: duckdb.DuckDBPyConnection,
    query: str,
    *,
    limit: int = 50,
) -> pd.DataFrame:
    """Search name_screening by name_en or name_cn (exact + LIKE)."""
    pattern = f"%{query}%"
    return conn.execute("""
        SELECT * FROM name_screening
        WHERE name_en ILIKE ? OR name_cn ILIKE ? OR name_cn_norm ILIKE ?
           OR name_pinyin ILIKE ? OR aliases ILIKE ?
        ORDER BY
            CASE WHEN name_en = ? OR name_cn = ? THEN 0 ELSE 1 END,
            source
        LIMIT ?
    """, [pattern, pattern, pattern, pattern, pattern, query, query, limit]).df()


def search_screening_fuzzy(
    conn: duckdb.DuckDBPyConnection,
    query: str,
    *,
    limit: int = 100,
    normalized_query: str = "",
) -> pd.DataFrame:
    """Broad search returning candidates for fuzzy matching.

    If normalized_query is provided (e.g. traditional→simplified Chinese),
    it's used alongside the original query for broader matching.

    Multi-word English queries are split into tokens and matched with AND
    logic to handle word gaps (e.g. "Hongkong Shanghai" matches
    "The Hongkong and Shanghai Banking Corporation Limited").
    """
    import re as _re
    pattern = f"%{query}%"

    # Detect if query is English multi-word (has spaces, no CJK)
    has_cjk = bool(_re.search(r"[\u4e00-\u9fff]", query))
    tokens = query.split() if not has_cjk and " " in query else []

    base_sql = """SELECT id, name_en, name_cn, name_cn_norm, name_pinyin, source,
           risk_category, pep_level, countries, aliases, addresses,
           programs, source_date, name_type, notes
    FROM name_screening WHERE """

    # Per-field ILIKE clause used across all code paths (6 fields)
    _ILIKE_FIELDS = "name_en ILIKE ? OR name_cn ILIKE ? OR name_cn_norm ILIKE ? OR name_pinyin ILIKE ? OR aliases ILIKE ? OR notes ILIKE ?"

    if tokens and len(tokens) >= 2:
        # Multi-word English: match all tokens individually
        where_parts = []
        params = []
        for t in tokens:
            where_parts.append(f"({_ILIKE_FIELDS})")
            params.extend([f"%{t}%"] * 6)
        where = " AND ".join(where_parts)
        params.append(limit)
        return conn.execute(base_sql + where + " LIMIT ?", params).df()

    if normalized_query and normalized_query != query:
        norm_pattern = f"%{normalized_query}%"
        return conn.execute(base_sql +
            f"""{_ILIKE_FIELDS}
               OR name_cn_norm ILIKE ? OR name_pinyin ILIKE ?
            LIMIT ?""",
            [pattern] * 6 + [norm_pattern, norm_pattern, limit]).df()

    return conn.execute(base_sql +
        f"""{_ILIKE_FIELDS}
           OR name_pinyin ILIKE ?
        LIMIT ?""",
        [pattern] * 6 + [pattern, limit]).df()


def upsert_news_cache(conn: duckdb.DuckDBPyConnection, entry: dict) -> int:
    """Cache a news search result."""
    result = conn.execute("""
        INSERT INTO news_screening_cache (search_name, search_name_cn, source, title, url,
            published_date, snippet, matched_keywords)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id
    """, [
        entry["search_name"], entry.get("search_name_cn"),
        entry.get("source"), entry.get("title"), entry.get("url"),
        entry.get("published_date"), entry.get("snippet"),
        entry.get("matched_keywords"),
    ])
    return result.fetchone()[0]


def get_news_cache(
    conn: duckdb.DuckDBPyConnection,
    name: str,
    max_age_hours: int = 168,
) -> pd.DataFrame:
    """Get cached news for a name within max_age_hours."""
    return conn.execute("""
        SELECT * FROM news_screening_cache
        WHERE search_name = ? AND cached_at > CURRENT_TIMESTAMP - INTERVAL '1 hour' * ?
        ORDER BY cached_at DESC
    """, [name, max_age_hours]).df()


def init_db(db_path: str | Path | None = None) -> duckdb.DuckDBPyConnection:
    """Open a DuckDB connection. DDL migrations are run once via migrate_db()."""
    migrate_db(db_path)
    migrate_name_screening(db_path)
    return _conn(db_path)


def upsert_indicator(conn: duckdb.DuckDBPyConnection, indicator: dict) -> int:
    """Insert or update an indicator, return its id."""
    params_json = json.dumps(indicator.get("params", {}), ensure_ascii=False)
    tags = indicator.get("tags", "")
    # SELECT first to avoid DuckDB FK constraint issue with ON CONFLICT
    existing = conn.execute(
        "SELECT id FROM indicators WHERE source=? AND method=? AND params=?",
        [indicator["source"], indicator["method"], params_json]
    ).fetchone()
    if existing:
        indicator_id = existing[0]
        conn.execute(
            "UPDATE indicators SET name=?, description=?, frequency=?, tags=? WHERE id=?",
            [indicator["name"], indicator.get("description", ""), indicator.get("frequency", ""), tags, indicator_id]
        )
        return indicator_id
    result = conn.execute(
        "INSERT INTO indicators (source, name, method, params, description, frequency, tags) VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id",
        [indicator["source"], indicator["name"], indicator["method"], params_json, indicator.get("description", ""), indicator.get("frequency", ""), tags]
    )
    return result.fetchone()[0]


def upsert_observations(
    conn: duckdb.DuckDBPyConnection,
    indicator_id: int,
    df: pd.DataFrame,
    *,
    date_col: str = "date",
    value_col: str = "value",
) -> int:
    """Write observation rows from a DataFrame. Skips duplicates. Returns row count inserted."""
    if df.empty:
        return 0
    # Keep date, value, and optionally notes
    has_notes = "notes" in df.columns
    keep_cols = [date_col, value_col] + (["notes"] if has_notes else [])
    df = df[keep_cols].copy()
    df.columns = ["date", "value"] + (["notes"] if has_notes else [])
    df["indicator_id"] = indicator_id

    conn.register("_tmp_obs", df)
    if has_notes:
        conn.execute("""
            INSERT INTO observations (indicator_id, date, value, notes)
            SELECT indicator_id, date, value, notes FROM _tmp_obs
            ON CONFLICT (indicator_id, date) DO UPDATE SET
                value = excluded.value,
                notes = excluded.notes,
                fetched_at = now()
        """)
    else:
        conn.execute("""
            INSERT INTO observations (indicator_id, date, value)
            SELECT indicator_id, date, value FROM _tmp_obs
            ON CONFLICT (indicator_id, date) DO UPDATE SET
                value = excluded.value,
                fetched_at = now()
        """)
    conn.unregister("_tmp_obs")
    count = 0  # DuckDB doesn't return affected row count from INSERT ... ON CONFLICT in all cases

    # Update last_updated timestamp
    conn.execute("UPDATE indicators SET last_updated = now() WHERE id = ?", [indicator_id])
    return count


def mark_indicator_updated(conn: duckdb.DuckDBPyConnection, indicator_id: int) -> None:
    conn.execute("UPDATE indicators SET last_updated = now() WHERE id = ?", [indicator_id])


def get_indicators(
    conn: duckdb.DuckDBPyConnection,
    source: str | None = None,
    sources: list[str] | None = None,
) -> pd.DataFrame:
    """Return all indicators, optionally filtered by source or multiple sources."""
    if sources:
        placeholders = ",".join(["?"] * len(sources))
        return conn.execute(
            f"SELECT * FROM indicators WHERE source IN ({placeholders}) ORDER BY source, id",
            sources,
        ).df()
    if source:
        return conn.execute(
            "SELECT * FROM indicators WHERE source = ? ORDER BY id", [source]
        ).df()
    return conn.execute("SELECT * FROM indicators ORDER BY source, id").df()


def get_indicator(conn: duckdb.DuckDBPyConnection, indicator_id: int) -> dict | None:
    """Return a single indicator as dict, or None."""
    row = conn.execute(
        "SELECT * FROM indicators WHERE id = ?", [indicator_id]
    ).fetchone()
    if row is None:
        return None
    cols = [desc[0] for desc in conn.description]
    return dict(zip(cols, row))


def get_data(
    conn: duckdb.DuckDBPyConnection,
    indicator_id: int,
    start: str | None = None,
    end: str | None = None,
    limit: int = 10000,
) -> pd.DataFrame:
    """Query observation data for an indicator, with optional date range."""
    sql = "SELECT date, value, notes FROM observations WHERE indicator_id = ?"
    params = [indicator_id]
    if start:
        sql += " AND date >= ?"
        params.append(start)
    if end:
        sql += " AND date <= ?"
        params.append(end)
    sql += " ORDER BY date DESC LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).df()


def search_indicators(conn: duckdb.DuckDBPyConnection, query: str) -> pd.DataFrame:
    """Full-text-like search across indicator name, description, and tags."""
    pattern = f"%{query}%"
    return conn.execute("""
        SELECT * FROM indicators
        WHERE name ILIKE ? OR description ILIKE ? OR source ILIKE ? OR tags ILIKE ?
        ORDER BY source, id
    """, [pattern, pattern, pattern, pattern]).df()


def get_all_tags(conn: duckdb.DuckDBPyConnection) -> list[dict]:
    """Return all unique tags with indicator counts, sorted by popularity."""
    rows = conn.execute("""
        SELECT tags FROM indicators WHERE tags IS NOT NULL AND tags != ''
    """).fetchall()
    tag_counts: dict[str, int] = {}
    for (tag_str,) in rows:
        for t in tag_str.split(","):
            t = t.strip()
            if t:
                tag_counts[t] = tag_counts.get(t, 0) + 1
    return sorted(
        [{"tag": tag, "count": count} for tag, count in tag_counts.items()],
        key=lambda x: x["count"], reverse=True
    )


def get_indicators_by_tag(conn: duckdb.DuckDBPyConnection, tag: str) -> pd.DataFrame:
    """Return indicators matching a specific tag."""
    pattern = f"%{tag}%"
    return conn.execute(
        "SELECT * FROM indicators WHERE tags ILIKE ? ORDER BY source, id",
        [pattern]
    ).df()


def get_data_batch(
    conn: duckdb.DuckDBPyConnection,
    indicator_ids: list[int],
    start: str | None = None,
    end: str | None = None,
    limit: int = 60,
) -> dict[int, list[dict]]:
    """Fetch time-series for multiple indicators in a single query. Returns {id: [{date, value}, ...]}."""
    if not indicator_ids:
        return {}

    placeholders = ",".join(["?"] * len(indicator_ids))
    sql = f"SELECT indicator_id, date, value, notes FROM observations WHERE indicator_id IN ({placeholders})"
    params: list = indicator_ids
    if start:
        sql += " AND date >= ?"
        params.append(start)
    if end:
        sql += " AND date <= ?"
        params.append(end)
    sql += " ORDER BY indicator_id, date DESC"

    rows = conn.execute(sql, params).fetchall()

    result: dict[int, list[dict]] = {iid: [] for iid in indicator_ids}
    for iid, date, value, notes in rows:
        if len(result[iid]) < limit:
            entry = {"date": str(date), "value": value}
            if notes:
                entry["notes"] = str(notes)
            result[iid].append(entry)

    return result


def get_latest_batch(
    conn: duckdb.DuckDBPyConnection,
    indicator_ids: list[int],
) -> dict[int, dict | None]:
    """Fetch latest value for multiple indicators in a single query. Returns {id: {date, value} | None}."""
    if not indicator_ids:
        return {}

    placeholders = ",".join(["?"] * len(indicator_ids))
    # Use a single subquery with ROW_NUMBER per indicator
    rows = conn.execute(f"""
        SELECT indicator_id, date, value, notes FROM (
            SELECT indicator_id, date, value, notes,
                ROW_NUMBER() OVER (PARTITION BY indicator_id ORDER BY date DESC) AS rn
            FROM observations
            WHERE indicator_id IN ({placeholders})
        ) WHERE rn = 1
    """, indicator_ids).fetchall()

    result: dict[int, dict | None] = {iid: None for iid in indicator_ids}
    for iid, date, value, notes in rows:
        entry = {"date": str(date), "value": value}
        if notes:
            entry["notes"] = str(notes)
        result[iid] = entry

    return result


def observation_count(conn: duckdb.DuckDBPyConnection, indicator_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM observations WHERE indicator_id = ?", [indicator_id]
    ).fetchone()
    return row[0] if row else 0
