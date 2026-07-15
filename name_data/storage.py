"""
Storage layer — DuckDB-backed store for Chinese naming data.

Tables:
  kangxi_radicals  — Kangxi 214 radicals with stroke counts
  kangxi_chars     — Characters with Kangxi stroke counts, radical, pinyin, structure
  kangxi_similar   — Characters with disputed stroke counts
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import duckdb
import pandas as pd

from name_data.config import DB_PATH, KANGXI_RADICAL_STROKES


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

    # Sequences (must be created before tables that reference them)
    conn.execute("CREATE SEQUENCE IF NOT EXISTS seq_kangxi_chars")
    conn.execute("CREATE SEQUENCE IF NOT EXISTS seq_kangxi_similar")
    conn.execute("CREATE SEQUENCE IF NOT EXISTS seq_stroke_corr")
    conn.execute("CREATE SEQUENCE IF NOT EXISTS seq_comp_elem")
    conn.execute("CREATE SEQUENCE IF NOT EXISTS seq_zodiac_comp")
    conn.execute("CREATE SEQUENCE IF NOT EXISTS seq_wuge_pattern")
    conn.execute("CREATE SEQUENCE IF NOT EXISTS seq_wuge_health")
    conn.execute("CREATE SEQUENCE IF NOT EXISTS seq_surname_pat")
    conn.execute("CREATE SEQUENCE IF NOT EXISTS seq_name_scores")
    conn.execute("CREATE SEQUENCE IF NOT EXISTS seq_hexagram_lines")
    conn.execute("CREATE SEQUENCE IF NOT EXISTS seq_hexagram_relations")
    conn.execute("CREATE SEQUENCE IF NOT EXISTS seq_fortune_history")

    # 1. Kangxi 214 radicals
    conn.execute("""
        CREATE TABLE IF NOT EXISTS kangxi_radicals (
            id          INTEGER PRIMARY KEY,
            radical     VARCHAR(4) NOT NULL,
            radical_var VARCHAR(4),
            strokes     INTEGER NOT NULL,
            name_zh     VARCHAR(32),
            name_pinyin VARCHAR(64),
            index_num   INTEGER NOT NULL UNIQUE
        )
    """)

    # 2. Kangxi character dictionary (core table)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS kangxi_chars (
            id              INTEGER PRIMARY KEY DEFAULT nextval('seq_kangxi_chars'),
            char_t          VARCHAR(4) NOT NULL UNIQUE,
            char_s          VARCHAR(4),
            unicode         VARCHAR(10),
            total_strokes   INTEGER NOT NULL,
            radical         VARCHAR(4),
            radical_index   INTEGER,
            radical_strokes INTEGER,
            extra_strokes   INTEGER,
            structure       VARCHAR(16),
            decomposition   TEXT,
            pinyin          VARCHAR(128),
            pinyin_ascii    VARCHAR(128),
            tone            INTEGER,
            five_element    VARCHAR(4),
            ji_xiong        VARCHAR(8),
            meaning         TEXT,
            is_name_char    BOOLEAN DEFAULT true,
            frequency       INTEGER,
            kangxi_page     VARCHAR(16),
            source          VARCHAR(32) DEFAULT 'unihan'
        )
    """)

    # 3. Characters with disputed stroke counts
    conn.execute("""
        CREATE TABLE IF NOT EXISTS kangxi_similar (
            id              INTEGER PRIMARY KEY DEFAULT nextval('seq_kangxi_similar'),
            char_t          VARCHAR(4) NOT NULL,
            stroke_count1   INTEGER NOT NULL,
            stroke_count2   INTEGER,
            reason          TEXT,
            recommended     INTEGER
        )
    """)

    # ── Phase 2: Name scoring reference tables ──

    # 4. 81 数理吉凶 (五格派核心)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wuge_81 (
            id          INTEGER PRIMARY KEY,
            number      INTEGER NOT NULL UNIQUE,
            ji_xiong    VARCHAR(8) NOT NULL,
            description TEXT,
            summary     VARCHAR(128),
            five_element VARCHAR(4),
            base_score  INTEGER
        )
    """)

    # 5. 三才配置 (125 combinations)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sancai_config (
            id              INTEGER PRIMARY KEY,
            heaven_element  VARCHAR(4) NOT NULL,
            man_element     VARCHAR(4) NOT NULL,
            earth_element   VARCHAR(4) NOT NULL,
            ji_xiong        VARCHAR(8) NOT NULL,
            description     TEXT,
            base_score      INTEGER
        )
    """)

    # 6. 特殊字笔画校正
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stroke_correction (
            id          INTEGER PRIMARY KEY DEFAULT nextval('seq_stroke_corr'),
            char_t      VARCHAR(4) NOT NULL UNIQUE,
            correct_strokes INTEGER NOT NULL,
            common_error INTEGER,
            reason      TEXT,
            source      VARCHAR(32) DEFAULT 'manual'
        )
    """)
    # 7. 复姓笔画
    conn.execute("""
        CREATE TABLE IF NOT EXISTS compound_surname (
            id          INTEGER PRIMARY KEY,
            surname     VARCHAR(8) NOT NULL UNIQUE,
            pinyin      VARCHAR(64),
            strokes     INTEGER NOT NULL,
            char_breakdown VARCHAR(32)
        )
    """)

    # 8. 构件五行映射 (component → five element)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS component_element (
            id          INTEGER PRIMARY KEY DEFAULT nextval('seq_comp_elem'),
            component   VARCHAR(8) NOT NULL UNIQUE,
            five_element VARCHAR(4) NOT NULL,
            category    VARCHAR(16),
            description VARCHAR(64)
        )
    """)

    # 9. 生肖喜忌元件
    conn.execute("""
        CREATE TABLE IF NOT EXISTS zodiac_component (
            id          INTEGER PRIMARY KEY DEFAULT nextval('seq_zodiac_comp'),
            zodiac      VARCHAR(4) NOT NULL,
            component   VARCHAR(8) NOT NULL,
            favorability VARCHAR(4) NOT NULL,
            reason      TEXT,
            score_mod   INTEGER DEFAULT 0
        )
    """)

    # 16. 十神
    conn.execute("""
        CREATE TABLE IF NOT EXISTS shishen (
            id              INTEGER PRIMARY KEY,
            name            VARCHAR(8) NOT NULL UNIQUE,
            relation_type   VARCHAR(8),
            yin_yang_match  VARCHAR(4),
            description     TEXT,
            traits          TEXT
        )
    """)

    # 11. 十二长生
    conn.execute("""
        CREATE TABLE IF NOT EXISTS shier_changsheng (
            id          INTEGER PRIMARY KEY,
            name        VARCHAR(8) NOT NULL UNIQUE,
            stage       INTEGER NOT NULL,
            ji_xiong    VARCHAR(8),
            description TEXT
        )
    """)

    # 12. 太乙吉凶数
    conn.execute("""
        CREATE TABLE IF NOT EXISTS taiyi_number (
            id          INTEGER PRIMARY KEY,
            number      INTEGER NOT NULL UNIQUE,
            ji_xiong    VARCHAR(8),
            description TEXT
        )
    """)

    # 13. 天运五行
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tianyun (
            id              INTEGER PRIMARY KEY,
            name            VARCHAR(8) NOT NULL UNIQUE,
            five_element    VARCHAR(4),
            description     TEXT,
            ji_xiong        VARCHAR(8)
        )
    """)

    # 14. 九宫飞星
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jiugong (
            id          INTEGER PRIMARY KEY,
            name        VARCHAR(8) NOT NULL UNIQUE,
            number      INTEGER NOT NULL UNIQUE,
            direction   VARCHAR(8),
            five_element VARCHAR(4),
            ji_xiong    VARCHAR(8),
            description TEXT
        )
    """)

    # 15. 神煞
    conn.execute("""
        CREATE TABLE IF NOT EXISTS shensha (
            id          INTEGER PRIMARY KEY,
            name        VARCHAR(16) NOT NULL UNIQUE,
            type        VARCHAR(8),
            ji_xiong    VARCHAR(8),
            description TEXT,
            rule_hint   TEXT
        )
    """)

    # 16. 五格格局特征
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wuge_pattern (
            id          INTEGER PRIMARY KEY DEFAULT nextval('seq_wuge_pattern'),
            pattern_name VARCHAR(32) NOT NULL,
            condition_desc TEXT,
            ji_xiong    VARCHAR(8),
            description TEXT
        )
    """)

    # 17. 五格健康论断
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wuge_health (
            id          INTEGER PRIMARY KEY DEFAULT nextval('seq_wuge_health'),
            pattern_desc VARCHAR(128),
            health_issue TEXT,
            body_part   VARCHAR(32),
            severity    VARCHAR(8)
        )
    """)

    # 18. 姓氏格局
    conn.execute("""
        CREATE TABLE IF NOT EXISTS surname_pattern (
            id          INTEGER PRIMARY KEY DEFAULT nextval('seq_surname_pat'),
            surname_strokes INTEGER NOT NULL,
            pattern_desc TEXT,
            ji_xiong    VARCHAR(8),
            description TEXT
        )
    """)

    # 19. 姓名评分记录
    conn.execute("""
        CREATE TABLE IF NOT EXISTS name_scores (
            id          INTEGER PRIMARY KEY DEFAULT nextval('seq_name_scores'),
            surname     VARCHAR(16),
            given_name  VARCHAR(32),
            gender      VARCHAR(2),
            birth_date  DATE,
            birth_hour  INTEGER,
            city_id     INTEGER,
            total_score INTEGER,
            bazi_score  INTEGER,
            wuge_score  INTEGER,
            sancai_score INTEGER,
            zodiac_score INTEGER,
            phonetic_score INTEGER,
            meaning_score INTEGER,
            detail_json TEXT,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── 20-25: I Ching (周易64卦) & Tuibei Tu (推背图) ──

    # 20. 八卦
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bagua (
            id              INTEGER PRIMARY KEY,
            name_zh         VARCHAR(8) NOT NULL UNIQUE,
            symbol          VARCHAR(2) NOT NULL,
            unicode         VARCHAR(8),
            wuxing          VARCHAR(4) NOT NULL,
            direction       VARCHAR(8),
            nature          VARCHAR(16),
            family_member   VARCHAR(8),
            body_part       VARCHAR(8),
            description     TEXT
        )
    """)

    # 21. 六十四卦
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hexagram_64 (
            id              INTEGER PRIMARY KEY,
            name_zh         VARCHAR(16) NOT NULL UNIQUE,
            name_pinyin     VARCHAR(64),
            upper_trigram   INTEGER NOT NULL REFERENCES bagua(id),
            lower_trigram   INTEGER NOT NULL REFERENCES bagua(id),
            binary_code     VARCHAR(6) NOT NULL,
            judgment        TEXT,
            image_text      TEXT,
            tuan_zhuan      TEXT,
            xiang_zhuan     TEXT,
            ji_xiong        VARCHAR(8),
            description     TEXT,
            five_element    VARCHAR(8)
        )
    """)

    # 22. 384爻辞
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hexagram_lines (
            id              INTEGER PRIMARY KEY DEFAULT nextval('seq_hexagram_lines'),
            hexagram_id     INTEGER NOT NULL REFERENCES hexagram_64(id),
            line_number     INTEGER NOT NULL CHECK(line_number BETWEEN 1 AND 6),
            line_name       VARCHAR(8) NOT NULL,
            yin_yang        VARCHAR(2) NOT NULL,
            is_proper       VARCHAR(8),
            line_text       TEXT NOT NULL,
            line_meaning    TEXT,
            UNIQUE(hexagram_id, line_number)
        )
    """)

    # 23. 卦变关系（错卦/综卦/互卦/变卦）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hexagram_relations (
            id              INTEGER PRIMARY KEY DEFAULT nextval('seq_hexagram_relations'),
            source_id       INTEGER NOT NULL REFERENCES hexagram_64(id),
            target_id       INTEGER NOT NULL REFERENCES hexagram_64(id),
            relation_type   VARCHAR(16) NOT NULL,
            UNIQUE(source_id, target_id, relation_type)
        )
    """)

    # 24. 推背图60象
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tuibei_tu (
            id              INTEGER PRIMARY KEY,
            image_name      VARCHAR(32),
            image_desc      TEXT,
            poem_chen       TEXT,
            poem_song       TEXT,
            commentary      TEXT,
            hexagram_id     INTEGER REFERENCES hexagram_64(id),
            historical_era  VARCHAR(32),
            ji_xiong        VARCHAR(8)
        )
    """)

    # 25. 占卜历史
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fortune_history (
            id              INTEGER PRIMARY KEY DEFAULT nextval('seq_fortune_history'),
            method          VARCHAR(16) NOT NULL,
            question        TEXT,
            primary_hexagram INTEGER REFERENCES hexagram_64(id),
            changing_lines  VARCHAR(12),
            mutual_hexagram  INTEGER REFERENCES hexagram_64(id),
            changed_hexagram INTEGER REFERENCES hexagram_64(id),
            result_json     TEXT,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Indexes
    conn.execute("CREATE INDEX IF NOT EXISTS idx_hl_hexagram ON hexagram_lines(hexagram_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_hr_source ON hexagram_relations(source_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_hr_type ON hexagram_relations(relation_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kc_strokes ON kangxi_chars(total_strokes)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kc_radical ON kangxi_chars(radical)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kc_pinyin ON kangxi_chars(pinyin_ascii)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kc_element ON kangxi_chars(five_element)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kc_radical_idx ON kangxi_chars(radical_index)")

    return conn


# ── Radical helpers ──────────────────────────────────────────────

def insert_radicals(conn, df: pd.DataFrame) -> int:
    """Insert 214 Kangxi radicals. Returns rows inserted."""
    if df.empty:
        return 0
    needed = ["id", "radical", "radical_var", "strokes", "name_zh", "name_pinyin", "index_num"]
    for col in needed:
        if col not in df.columns:
            df[col] = None
    conn.register("_tmp_rad", df[needed])
    rows = conn.execute("""
        INSERT INTO kangxi_radicals (id, radical, radical_var, strokes, name_zh, name_pinyin, index_num)
        SELECT id, radical, radical_var, strokes, name_zh, name_pinyin, index_num FROM _tmp_rad
        ON CONFLICT(id) DO UPDATE SET
            radical = excluded.radical,
            radical_var = excluded.radical_var,
            strokes = excluded.strokes,
            name_zh = excluded.name_zh,
            name_pinyin = excluded.name_pinyin,
            index_num = excluded.index_num
    """).fetchall()
    conn.unregister("_tmp_rad")
    return rows[0][0] if rows else 0


# ── Character helpers ────────────────────────────────────────────

def upsert_chars(conn, df: pd.DataFrame) -> int:
    """Upsert characters from Unihan data. Returns rows affected."""
    if df.empty:
        return 0
    needed = [
        "char_t", "unicode", "total_strokes", "radical", "radical_index",
        "radical_strokes", "extra_strokes", "pinyin", "pinyin_ascii", "tone",
        "meaning", "kangxi_page", "source"
    ]
    for col in needed:
        if col not in df.columns:
            df[col] = None
    sub = df[needed].copy()
    conn.register("_tmp_chars", sub)
    rows = conn.execute("""
        INSERT INTO kangxi_chars (char_t, unicode, total_strokes, radical, radical_index,
            radical_strokes, extra_strokes, pinyin, pinyin_ascii, tone,
            meaning, kangxi_page, source)
        SELECT char_t, unicode, total_strokes, radical, radical_index,
            radical_strokes, extra_strokes, pinyin, pinyin_ascii, tone,
            meaning, kangxi_page, source
        FROM _tmp_chars
        ON CONFLICT(char_t) DO UPDATE SET
            total_strokes = excluded.total_strokes,
            radical = excluded.radical,
            radical_index = excluded.radical_index,
            radical_strokes = excluded.radical_strokes,
            extra_strokes = excluded.extra_strokes,
            pinyin = excluded.pinyin,
            pinyin_ascii = excluded.pinyin_ascii,
            tone = excluded.tone,
            meaning = excluded.meaning,
            kangxi_page = excluded.kangxi_page,
            source = excluded.source
    """).fetchall()
    conn.unregister("_tmp_chars")
    return rows[0][0] if rows else 0


def upsert_similar(conn, df: pd.DataFrame) -> int:
    """Upsert disputed stroke count records."""
    if df.empty:
        return 0
    needed = ["char_t", "stroke_count1", "stroke_count2", "reason", "recommended"]
    for col in needed:
        if col not in df.columns:
            df[col] = None
    sub = df[needed].copy()
    conn.register("_tmp_sim", sub)
    rows = conn.execute("""
        INSERT INTO kangxi_similar (char_t, stroke_count1, stroke_count2, reason, recommended)
        SELECT char_t, stroke_count1, stroke_count2, reason, recommended FROM _tmp_sim
        ON CONFLICT DO NOTHING
    """).fetchall()
    conn.unregister("_tmp_sim")
    return rows[0][0] if rows else 0


# ── Query helpers ────────────────────────────────────────────────

def get_char(conn, char: str) -> Optional[dict]:
    """Get single character details."""
    df = conn.execute(
        "SELECT * FROM kangxi_chars WHERE char_t = ?", [char]
    ).df()
    if df.empty:
        return None
    return df.iloc[0].to_dict()


def search_chars(
    conn,
    radical: Optional[str] = None,
    strokes_min: Optional[int] = None,
    strokes_max: Optional[int] = None,
    element: Optional[str] = None,
    pinyin: Optional[str] = None,
    search: Optional[str] = None,
    name_only: bool = False,
    limit: int = 500,
    offset: int = 0,
) -> pd.DataFrame:
    """Search/filter character database."""
    where = ["1=1"]
    params = []

    if radical:
        where.append("radical = ?")
        params.append(radical)
    if strokes_min is not None:
        where.append("total_strokes >= ?")
        params.append(strokes_min)
    if strokes_max is not None:
        where.append("total_strokes <= ?")
        params.append(strokes_max)
    if element:
        where.append("five_element = ?")
        params.append(element)
    if pinyin:
        where.append("pinyin_ascii LIKE ?")
        params.append(f"%{pinyin}%")
    if search:
        where.append("char_t = ?")
        params.append(search)
    if name_only:
        where.append("is_name_char = true")

    params.extend([limit, offset])
    return conn.execute(f"""
        SELECT char_t, char_s, total_strokes, radical, pinyin, pinyin_ascii,
               tone, five_element, ji_xiong, meaning, structure, decomposition
        FROM kangxi_chars
        WHERE {' AND '.join(where)}
        ORDER BY total_strokes, char_t
        LIMIT ? OFFSET ?
    """, params).df()


def get_radicals(conn) -> pd.DataFrame:
    """List all 214 Kangxi radicals."""
    return conn.execute(
        "SELECT * FROM kangxi_radicals ORDER BY index_num"
    ).df()


def update_char_structure(conn, char_t: str, structure: str, decomposition: str) -> int:
    """Update structure and decomposition fields for a single character. Returns rows affected."""
    result = conn.execute("""
        UPDATE kangxi_chars SET structure = ?, decomposition = ?
        WHERE char_t = ? AND (structure IS NULL OR structure = '')
    """, [structure, decomposition, char_t]).fetchall()
    return result[0] if result else 0


def get_radical_strokes(radical_index: int) -> int:
    """Get the Kangxi stroke count for a radical index (1-214)."""
    return KANGXI_RADICAL_STROKES.get(radical_index, 0)


# ── Generic seed data import ───────────────────────────────────

def _insert_seed(conn, table: str, columns: list[str], rows: list[tuple], on_conflict: str = "DO NOTHING") -> int:
    """Generic seed data insert helper."""
    if not rows:
        return 0
    df = pd.DataFrame(rows, columns=columns)
    conn.register("_seed_tmp", df)
    cols = ", ".join(columns)
    placeholders = ", ".join(["?"] * len(columns))
    try:
        result = conn.execute(
            f"INSERT OR IGNORE INTO {table} ({cols}) SELECT * FROM _seed_tmp"
        ).fetchall()
    except Exception:
        result = conn.execute(
            f"INSERT INTO {table} ({cols}) SELECT * FROM _seed_tmp ON CONFLICT {on_conflict}"
        ).fetchall()
    conn.unregister("_seed_tmp")
    return result[0][0] if result else len(rows)

