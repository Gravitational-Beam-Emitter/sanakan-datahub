"""
API — FastAPI app for Chinese naming data (port 8008).

Endpoints:
  GET  /api/v1/health
  GET  /api/v1/characters/search
  GET  /api/v1/characters/{char}
  GET  /api/v1/radicals
  GET  /api/v1/stats
"""

from __future__ import annotations

import math
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from name_data.pipeline import (
    init_pipeline, show_stats,
    score_name, score_name_batch,
    divine_by_coins, divine_by_numbers,
    interpret_hexagram, get_wrong_hexagram, get_reverse_hexagram,
    calculate_bazi, generate_names,
    consult_tuibei, list_tuibei, get_tuibei, list_tuibei_eras,
    get_daily_fortune, compute_daily_fortune,
)
from name_data.huangli import get_daily_almanac
from name_data.calendar import (
    gregorian_to_ganzhi, day_ganzhi, year_ganzhi, month_ganzhi,
    compute_solar_terms, get_solar_term, SOLAR_TERM_NAMES,
)
from name_data.storage import get_char, get_radicals, init_db, search_chars

app = FastAPI(
    title="Name Data API — 取名数据服务",
    description="Kangxi dictionary, character search, and naming data.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _serialize(df):
    """Convert DataFrame to JSON-safe list of dicts."""
    import pandas as pd
    records = df.to_dict(orient="records")
    for r in records:
        for k, v in r.items():
            if isinstance(v, (float,)) and (pd.isna(v) or math.isinf(v)):
                r[k] = None
    return records


# ── Health ───────────────────────────────────────────────────────

@app.get("/api/v1/health")
def health():
    conn = init_db(read_only=True)
    try:
        char_count = conn.execute("SELECT COUNT(*) FROM kangxi_chars").fetchone()[0]
        rad_count = conn.execute("SELECT COUNT(*) FROM kangxi_radicals").fetchone()[0]
        return {
            "status": "ok",
            "module": "name_data",
            "characters": char_count,
            "radicals": rad_count,
        }
    finally:
        conn.close()


# ── Character search ────────────────────────────────────────────

@app.get("/api/v1/characters/search")
def character_search(
    q: Optional[str] = Query(None, description="Search by character or pinyin"),
    radical: Optional[str] = Query(None, description="Filter by radical (部首)"),
    strokes_min: Optional[int] = Query(None, description="Min Kangxi strokes"),
    strokes_max: Optional[int] = Query(None, description="Max Kangxi strokes"),
    element: Optional[str] = Query(None, description="Five element: 金/木/水/火/土"),
    tone: Optional[int] = Query(None, description="Pinyin tone 1-5"),
    name_only: bool = Query(False, description="Only name-suitable characters"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """Search/filter Chinese characters. Supports filtering by radical, strokes, element, tone."""
    conn = init_db(read_only=True)
    try:
        df = search_chars(
            conn,
            radical=radical,
            strokes_min=strokes_min,
            strokes_max=strokes_max,
            element=element,
            pinyin=q,
            search=q if q and len(q) == 1 else None,
            name_only=name_only,
            limit=limit,
            offset=offset,
        )
        return {"count": len(df), "results": _serialize(df)}
    finally:
        conn.close()


# ── Single character ─────────────────────────────────────────────

@app.get("/api/v1/characters/{char}")
def character_detail(char: str):
    """Get full details for a single character."""
    conn = init_db(read_only=True)
    try:
        result = get_char(conn, char)
        if result is None:
            raise HTTPException(status_code=404, detail=f"Character '{char}' not found")
        return result
    finally:
        conn.close()


# ── Radicals ─────────────────────────────────────────────────────

@app.get("/api/v1/radicals")
def radical_list():
    """List all 214 Kangxi radicals."""
    conn = init_db(read_only=True)
    try:
        df = get_radicals(conn)
        return {"count": len(df), "radicals": _serialize(df)}
    finally:
        conn.close()


# ── Stats ────────────────────────────────────────────────────────

@app.get("/api/v1/stats")
def statistics():
    """Database statistics."""
    conn = init_db(read_only=True)
    try:
        total = conn.execute("SELECT COUNT(*) FROM kangxi_chars").fetchone()[0]
        radicals = conn.execute("SELECT COUNT(*) FROM kangxi_radicals").fetchone()[0]
        with_pinyin = conn.execute(
            "SELECT COUNT(*) FROM kangxi_chars WHERE pinyin != ''"
        ).fetchone()[0]
        with_meaning = conn.execute(
            "SELECT COUNT(*) FROM kangxi_chars WHERE meaning != ''"
        ).fetchone()[0]
        by_element = {}
        for el in ["金", "木", "水", "火", "土"]:
            cnt = conn.execute(
                "SELECT COUNT(*) FROM kangxi_chars WHERE five_element = ?", [el]
            ).fetchone()[0]
            by_element[el] = cnt
        return {
            "total_characters": total,
            "radicals": radicals,
            "with_pinyin": with_pinyin,
            "with_meaning": with_meaning,
            "by_element": by_element,
        }
    finally:
        conn.close()


# ── Name Scoring ─────────────────────────────────────────────────

@app.post("/api/v1/name/score")
def name_score(body: dict):
    """Score a single name with bazi, wuge, sancai, zodiac, phonetic, and meaning."""
    result = score_name(
        surname=body.get("surname", ""),
        given_name=body.get("given_name", ""),
        birth_year=body.get("birth_year"),
        birth_month=body.get("birth_month"),
        birth_day=body.get("birth_day"),
        birth_hour=body.get("birth_hour", 12),
        gender=body.get("gender", "男"),
    )
    return result


@app.post("/api/v1/name/score-batch")
def name_score_batch(body: dict):
    """Score multiple names and return ranked by score."""
    names = body.get("names", [])
    results = score_name_batch(
        names=[(n["surname"], n["given_name"]) for n in names],
        birth_year=body.get("birth_year"),
        birth_month=body.get("birth_month"),
        birth_day=body.get("birth_day"),
        birth_hour=body.get("birth_hour", 12),
        gender=body.get("gender", "男"),
    )
    return {"count": len(results), "results": results}


@app.post("/api/v1/name/generate")
def name_generate(body: dict):
    """Generate auspicious name candidates based on birth info and surname.

    Returns 30+ candidate names with scores, ranked by total score.
    """
    result = generate_names(
        surname=body.get("surname", ""),
        birth_year=int(body["birth_year"]),
        birth_month=int(body["birth_month"]),
        birth_day=int(body["birth_day"]),
        birth_hour=body.get("birth_hour", 12),
        gender=body.get("gender", "男"),
        num_names=body.get("num_names", 30),
    )
    return result


@app.post("/api/v1/bazi/calculate")
def bazi_calculate(body: dict):
    """Calculate BaZi (八字) four pillars."""
    result = calculate_bazi(
        year=int(body["year"]),
        month=int(body["month"]),
        day=int(body["day"]),
        hour=int(body.get("hour", 12)),
    )
    return result


@app.get("/api/v1/wuge/calculate")
def wuge_calculate(
    surname: str = Query(..., description="Surname (姓)"),
    given_name: str = Query(..., description="Given name (名)"),
):
    """Calculate Wu Ge (五格) stroke grids."""
    from name_data.pipeline import calculate_wuge
    return calculate_wuge(surname, given_name)


# ── I Ching Divination ────────────────────────────────────────────

@app.get("/api/v1/divine/coins")
def divine_coins():
    """Coin divination (金钱卦). Returns primary, mutual, and changed hexagrams."""
    return divine_by_coins()


@app.get("/api/v1/divine/numbers")
def divine_numbers(
    a: int = Query(..., ge=1, description="Upper trigram number (1-8)"),
    b: int = Query(..., ge=1, description="Lower trigram number (1-8)"),
    c: int = Query(..., ge=1, description="Changing line number (1-6)"),
):
    """Number divination (数字卦)."""
    return divine_by_numbers(a, b, c)


@app.get("/api/v1/hexagram/{hexagram_id}")
def hexagram_detail(hexagram_id: int):
    """Get full hexagram details with all 6 lines."""
    return interpret_hexagram(hexagram_id)


@app.get("/api/v1/hexagram/{hexagram_id}/wrong")
def hexagram_wrong(hexagram_id: int):
    """Get wrong hexagram (错卦) — all 6 lines flipped."""
    return get_wrong_hexagram(hexagram_id)


@app.get("/api/v1/hexagram/{hexagram_id}/reverse")
def hexagram_reverse(hexagram_id: int):
    """Get reverse hexagram (综卦) — upside down."""
    return get_reverse_hexagram(hexagram_id)


# ── Tui Bei Tu (推背图) ──────────────────────────────────────────────

@app.get("/api/v1/tuibei-tu")
def tuibei_list(
    era: Optional[str] = Query(None, description="Filter by historical era"),
):
    """List all 60 Tui Bei Tu prophecies, optionally filtered by historical era."""
    results = list_tuibei(era=era)
    return {"count": len(results), "results": results}


@app.get("/api/v1/tuibei-tu/eras")
def tuibei_eras():
    """List historical eras covered by Tui Bei Tu."""
    results = list_tuibei_eras()
    return {"count": len(results), "eras": results}


@app.get("/api/v1/tuibei-tu/divine")
def tuibei_divine(
    method: str = Query("random", description="Consultation method: random, hexagram, or index"),
    hexagram_id: Optional[int] = Query(None, description="Hexagram ID for 'hexagram' method, or index for 'index' method"),
):
    """Consult Tui Bei Tu (推背图). Methods: random (抽签), hexagram (卦象推演), index (按序号)."""
    result = consult_tuibei(method=method, hexagram_id=hexagram_id)
    if result.get("tuibei") and "error" in result["tuibei"]:
        raise HTTPException(status_code=404, detail=result["tuibei"]["error"])
    return result


@app.get("/api/v1/tuibei-tu/{tuibei_id}")
def tuibei_detail(tuibei_id: int):
    """Get a single Tui Bei Tu prophecy with full hexagram details."""
    result = get_tuibei(tuibei_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


# ── Chinese Calendar (农历) ────────────────────────────────────────

@app.get("/api/v1/calendar/today")
def calendar_today():
    """Get full Chinese calendar info for today."""
    from datetime import date
    return gregorian_to_ganzhi(date.today())


@app.get("/api/v1/calendar/date/{date_str}")
def calendar_date(date_str: str):
    """Get full Chinese calendar info for a specific date (YYYY-MM-DD)."""
    from datetime import date as dt_date
    try:
        d = dt_date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date: {date_str}. Use YYYY-MM-DD.")
    return gregorian_to_ganzhi(d)


@app.get("/api/v1/calendar/day-ganzhi/{date_str}")
def calendar_day_ganzhi(date_str: str):
    """Get day stem-branch (日干支) for a date."""
    from datetime import date as dt_date
    try:
        d = dt_date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date: {date_str}.")
    gz = day_ganzhi(d)
    return {"date": date_str, "day_ganzhi": gz}


@app.get("/api/v1/calendar/solar-terms/{year}")
def calendar_solar_terms(year: int):
    """Get all 24 solar terms for a year."""
    result = get_solar_term(year=year)
    return result


@app.get("/api/v1/calendar/current-term")
def calendar_current_term(
    year: int = Query(..., description="Year"),
    month: int = Query(..., description="Month"),
    day: int = Query(..., description="Day"),
):
    """Get the current solar term for a given date."""
    result = get_solar_term(year=year, month=month, day=day)
    return result


# ── Daily Fortune (每日运势) ───────────────────────────────────────────

@app.get("/api/v1/daily-fortune/today")
def daily_fortune_today():
    """Get today's pre-computed fortune: calendar info, daily hexagram, fortune level."""
    from datetime import date
    return get_daily_fortune(date.today())


@app.get("/api/v1/daily-fortune/{date_str}")
def daily_fortune_date(date_str: str):
    """Get daily fortune for a specific date (YYYY-MM-DD)."""
    from datetime import date as dt_date
    try:
        d = dt_date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date: {date_str}. Use YYYY-MM-DD.")
    return get_daily_fortune(d)


# ── Chinese Almanac (黄历) ───────────────────────────────────────────

@app.get("/api/v1/huangli/today")
def huangli_today():
    """Get today's Chinese almanac (黄历): jianchu gods, yellow/black path,
    28 lunar mansions, Peng Zu taboos, and daily suitable/avoid activities.
    Based on 《协纪辨方书》(Qing Dynasty official almanac)."""
    from datetime import date
    return get_daily_almanac(date.today())


@app.get("/api/v1/huangli/{date_str}")
def huangli_date(date_str: str):
    """Get Chinese almanac for a specific date (YYYY-MM-DD)."""
    from datetime import date as dt_date
    try:
        d = dt_date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date: {date_str}. Use YYYY-MM-DD.")
    return get_daily_almanac(d)


# ── Admin ────────────────────────────────────────────────────────

@app.post("/api/v1/admin/init")
def admin_init():
    """Initialize database: import radicals and characters from Unihan data."""
    result = init_pipeline()
    return {"status": "ok", **result}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8008)
