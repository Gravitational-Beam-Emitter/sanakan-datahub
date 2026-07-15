"""
Chinese Calendar (农历) — Solar terms, day stem-branch, lunar calendar.

Solar terms are computed astronomically: 24 points where the Sun's ecliptic
longitude is a multiple of 15° (starting from 315° = 立春).

Day stem-branch (日干支) follows a fixed 60-day cycle independent of lunar
months. Reference: Jan 1, 1900 (Gregorian) = day 11 in the cycle (甲戌).

Usage:
  from name_data.calendar import (compute_solar_terms, day_ganzhi,
      precompute_solar_terms, get_solar_term, SOLAR_TERM_NAMES)

Reference:
  Meeus, Jean. "Astronomical Algorithms", 2nd ed., Chapter 27.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Optional

# 24 Solar Terms (节气) in order, starting from 立春
SOLAR_TERM_NAMES = [
    "立春", "雨水", "惊蛰", "春分", "清明", "谷雨",
    "立夏", "小满", "芒种", "夏至", "小暑", "大暑",
    "立秋", "处暑", "白露", "秋分", "寒露", "霜降",
    "立冬", "小雪", "大雪", "冬至", "小寒", "大寒",
]

# The 12 "major" terms (节气) that define month pillar changes
# These are the even-indexed terms (0, 2, 4, ..., 22)
MAJOR_TERM_INDICES = [0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22]

# Heavenly stems and earthly branches
HEAVENLY_STEMS = ["甲", "乙", "丙", "丁", "戊", "己", "庚", "辛", "壬", "癸"]
EARTHLY_BRANCHES = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]

# Start of 60-year cycles: 1984 = 甲子年
_GANZHI_CYCLE_START = 1984

# Reference: Jan 1, 1900 = day 11 in the 60-day cycle (甲戌日)
_DAY_CYCLE_REF_DATE = date(1900, 1, 1)
_DAY_CYCLE_REF_INDEX = 10  # 0-indexed: 甲戌 = index 10

# Ecliptic longitude targets for each solar term (degrees)
# 立春 = 315°, each subsequent term adds 15°
_TERM_LONGITUDE = [(315 + 15 * i) % 360 for i in range(24)]


# ── Astronomical helpers ──────────────────────────────────────────

def _gregorian_to_jd(d: datetime) -> float:
    """Convert Gregorian datetime to Julian Day (UTC)."""
    y, m = d.year, d.month
    day = d.day + d.hour / 24.0 + d.minute / 1440.0 + d.second / 86400.0
    if m <= 2:
        y -= 1
        m += 12
    a = int(y / 100)
    b = 2 - a + int(a / 4)
    return int(365.25 * (y + 4716)) + int(30.6001 * (m + 1)) + day + b - 1524.5


def _jd_to_gregorian(jd: float) -> datetime:
    """Convert Julian Day to Gregorian datetime."""
    jd += 0.5
    z = int(jd)
    f = jd - z
    if z < 2299161:
        a = z
    else:
        alpha = int((z - 1867216.25) / 36524.25)
        a = z + 1 + alpha - int(alpha / 4)
    b = a + 1524
    c = int((b - 122.1) / 365.25)
    d_val = int(365.25 * c)
    e = int((b - d_val) / 30.6001)
    day = b - d_val - int(30.6001 * e) + f
    month = e - 1 if e < 14 else e - 13
    year = c - 4716 if month > 2 else c - 4715

    total_seconds = int(day * 86400 + 0.5)
    d = int(total_seconds // 86400)
    remaining = total_seconds - d * 86400
    h = remaining // 3600
    m = (remaining - h * 3600) // 60
    s = remaining - h * 3600 - m * 60

    return datetime(year, month, d, h, m, s)


def _sun_ecliptic_longitude(jd: float) -> float:
    """Compute Sun's apparent ecliptic longitude at given JD.

    Based on simplified VSOP87 / Meeus Chapter 25.
    Accuracy: ~0.01° for years 1900-2100.
    """
    T = (jd - 2451545.0) / 36525.0  # Julian centuries from J2000.0

    # Mean longitude (degrees)
    L0 = (280.46646 + 36000.76983 * T + 0.0003032 * T * T) % 360

    # Mean anomaly (degrees)
    M = (357.52911 + 35999.05029 * T - 0.0001537 * T * T) % 360
    M_rad = math.radians(M)

    # Equation of center
    C = (
        (1.914602 - 0.004817 * T - 0.000014 * T * T) * math.sin(M_rad)
        + (0.019993 - 0.000101 * T) * math.sin(2 * M_rad)
        + 0.000289 * math.sin(3 * M_rad)
    )

    # True longitude
    return (L0 + C) % 360


def _find_solar_term_jd(year: int, term_index: int) -> float:
    """Find the Julian Day when Sun's longitude reaches target for a solar term.

    Uses Newton's method for rapid convergence.
    term_index: 0=立春(315°), 1=雨水(330°), ..., 23=大寒(300°)
    """
    target = _TERM_LONGITUDE[term_index]

    # Initial guess: each term is ~365.2422/24 ≈ 15.2184 days apart
    # Base: approximate moment around Feb 4 for 立春
    base_jd = _gregorian_to_jd(datetime(year, 1, 1, 0, 0, 0))
    # Days from Jan 1: 立春 ≈ 34 days after Jan 1 (Feb 4)
    # + ~15.218 days per term index
    approx_offset = 34.0 + term_index * 15.2184
    guess_jd = base_jd + approx_offset

    # Newton's method: adjust JD until λ ≈ target
    for _ in range(20):
        lon = _sun_ecliptic_longitude(guess_jd)
        diff = (lon - target + 180) % 360 - 180  # signed difference in [-180, 180]
        if abs(diff) < 0.0001:  # ~0.5 seconds accuracy
            break
        # Derivative: Sun moves ~0.9856°/day
        adjust = diff / 0.9856
        guess_jd -= adjust

    return guess_jd


# ── Solar terms ────────────────────────────────────────────────────

def compute_solar_term(year: int, term_index: int) -> datetime:
    """Compute the exact datetime of a solar term for a given year.

    Args:
        year: Gregorian year
        term_index: 0=立春(Feb), 1=雨水, ..., 23=大寒(Jan next year)

    Returns:
        datetime in UTC (essentially China Standard Time accuracy)
    """
    jd = _find_solar_term_jd(year, term_index)
    return _jd_to_gregorian(jd)


def compute_solar_terms(year: int) -> list[datetime]:
    """Compute all 24 solar terms for a given year."""
    return [compute_solar_term(year, i) for i in range(24)]


def get_current_term(d: date) -> tuple[int, int]:
    """Get the current major solar term for a given Gregorian date.

    Returns (term_index, year) where term_index is the most recent
    major term (节气) that started before the given date.

    The month pillar is determined by which major term is in effect.
    """
    # Terms may be in current or previous year
    for year in [d.year, d.year - 1]:
        for idx in MAJOR_TERM_INDICES:
            term_dt = compute_solar_term(year, idx)
            term_date = term_dt.date()
            # Check if this term started before the given date
            # and the next major term starts after
            next_idx = MAJOR_TERM_INDICES[
                (MAJOR_TERM_INDICES.index(idx) + 1) % 12
            ]
            next_year = year if idx != 22 else year + 1
            next_term_dt = compute_solar_term(next_year, next_idx)

            if term_date <= d < next_term_dt.date():
                return idx, year
    return 0, d.year  # fallback


def precompute_solar_terms(start_year: int = 1900, end_year: int = 2100) -> list[dict]:
    """Pre-compute all solar terms for a range of years.

    Returns list of dicts: {year, term_index, term_name, datetime, jd}
    """
    results = []
    for year in range(start_year, end_year + 1):
        for idx in range(24):
            dt = compute_solar_term(year, idx)
            jd = _gregorian_to_jd(dt)
            results.append({
                "year": year,
                "term_index": idx,
                "term_name": SOLAR_TERM_NAMES[idx],
                "datetime": dt,
                "jd": jd,
                "is_major": idx in MAJOR_TERM_INDICES,
            })
    return results


# ── Day stem-branch (日干支) ────────────────────────────────────────

def day_ganzhi(d: date) -> str:
    """Compute day stem-branch (日干支) for a Gregorian date.

    The 60-day cycle is continuous since ancient times.
    Reference: Jan 1, 1900 = day 11 (甲戌).
    """
    days_diff = (d - _DAY_CYCLE_REF_DATE).days
    idx = (_DAY_CYCLE_REF_INDEX + days_diff) % 60
    return HEAVENLY_STEMS[idx % 10] + EARTHLY_BRANCHES[idx % 12]


def day_ganzhi_index(d: date) -> int:
    """Get 0-based index (0-59) in the 60-day cycle."""
    days_diff = (d - _DAY_CYCLE_REF_DATE).days
    return (_DAY_CYCLE_REF_INDEX + days_diff) % 60


def year_ganzhi(year: int, month: int = 6, day: int = 15) -> str:
    """Compute year stem-branch (年干支).

    The year changes at 立春 (approx Feb 4). For dates before 立春,
    use the previous year's stem-branch.

    Args:
        year: Gregorian year
        month, day: for determining if we're before 立春
    """
    lichun = compute_solar_term(year, 0)  # 立春
    if date(year, month, day) < lichun.date():
        year -= 1
    idx = (year - _GANZHI_CYCLE_START) % 60
    return HEAVENLY_STEMS[idx % 10] + EARTHLY_BRANCHES[idx % 12]


def month_ganzhi(year: int, month: int, day: int) -> str:
    """Compute month stem-branch (月干支) using exact solar terms.

    The month pillar changes at each major solar term (节气).
    Month 1 (寅月) starts at 立春.
    """
    d = date(year, month, day)
    term_idx, term_year = get_current_term(d)
    # Map term_index to month zhi
    # 立春(0)=寅, 惊蛰(2)=卯, ..., 小寒(22)=丑
    month_num = MAJOR_TERM_INDICES.index(term_idx)  # 0-11
    month_zhi = EARTHLY_BRANCHES[(month_num + 2) % 12]  # 寅=2, 卯=3, ...

    # Month stem: based on year stem of the solar cycle year.
    # Use term_year directly — do NOT call year_ganzhi() because
    # get_current_term() already accounts for 立春, and year_ganzhi()
    # would double-adjust (e.g. term_year=2024 → year_ganzhi → 2023).
    cycle_idx = (term_year - _GANZHI_CYCLE_START) % 60
    year_gan = HEAVENLY_STEMS[cycle_idx % 10]
    # Month stem start: 甲/己年→丙寅, 乙/庚年→戊寅, etc.
    month_gan_starts = {"甲": 2, "乙": 4, "丙": 6, "丁": 8, "戊": 0, "己": 2,
                         "庚": 4, "辛": 6, "壬": 8, "癸": 0}
    start = month_gan_starts[year_gan]
    month_gan = HEAVENLY_STEMS[(start + month_num) % 10]
    return month_gan + month_zhi


# ── Date conversion ───────────────────────────────────────────────

def gregorian_to_ganzhi(d: date) -> dict:
    """Convert a Gregorian date to full sexagenary information.

    Returns year/month/day stem-branch, current solar term, and zodiac.
    """
    ygz = year_ganzhi(d.year, d.month, d.day)
    mgz = month_ganzhi(d.year, d.month, d.day)
    dgz = day_ganzhi(d)
    term_idx, _ = get_current_term(d)
    term_name = SOLAR_TERM_NAMES[term_idx] if term_idx < len(SOLAR_TERM_NAMES) else ""

    zodiac_map = dict(zip(EARTHLY_BRANCHES,
        ["鼠", "牛", "虎", "兔", "龙", "蛇", "马", "羊", "猴", "鸡", "狗", "猪"]))

    return {
        "date": d.isoformat(),
        "year_ganzhi": ygz,
        "month_ganzhi": mgz,
        "day_ganzhi": dgz,
        "current_term": term_name,
        "day_cycle_index": day_ganzhi_index(d),
        "zodiac": zodiac_map.get(ygz[1], ""),
    }


# ── DB storage ────────────────────────────────────────────────────

def store_solar_terms(db_path: str = None, start_year: int = 1900,
                      end_year: int = 2100) -> dict:
    """Pre-compute and store solar terms in the database."""
    from name_data.storage import init_db

    conn = init_db(db_path=db_path, read_only=False)
    try:
        # Create table if not exists
        conn.execute("""
            CREATE TABLE IF NOT EXISTS solar_terms (
                id          INTEGER PRIMARY KEY,
                year        INTEGER NOT NULL,
                term_index  INTEGER NOT NULL,
                term_name   VARCHAR(8) NOT NULL,
                term_date   DATE NOT NULL,
                term_time   VARCHAR(8) NOT NULL,
                jd          DOUBLE NOT NULL,
                is_major    BOOLEAN DEFAULT false,
                UNIQUE(year, term_index)
            )
        """)
        conn.execute("CREATE SEQUENCE IF NOT EXISTS seq_solar_terms")

        count = 0
        for year in range(start_year, end_year + 1):
            for idx in range(24):
                dt = compute_solar_term(year, idx)
                jd = _gregorian_to_jd(dt)
                conn.execute("""
                    INSERT OR IGNORE INTO solar_terms
                        (id, year, term_index, term_name, term_date, term_time, jd, is_major)
                    VALUES (nextval('seq_solar_terms'), ?, ?, ?, ?, ?, ?, ?)
                """, [year, idx, SOLAR_TERM_NAMES[idx],
                      dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S"),
                      jd, idx in MAJOR_TERM_INDICES])
                count += 1
        conn.commit()
        return {"status": "ok", "stored": count,
                "year_range": f"{start_year}-{end_year}"}
    finally:
        conn.close()


def get_solar_term(db_path: str = None, year: int = None, month: int = None,
                   day: int = None) -> dict:
    """Find the current solar term for a given date, or list terms for a year."""
    from name_data.storage import init_db
    conn = init_db(db_path=db_path, read_only=True)
    try:
        if year and month and day:
            d = date(year, month, day)
            # Find the most recent major term before this date
            rows = conn.execute("""
                SELECT year, term_index, term_name, term_date, term_time, is_major
                FROM solar_terms
                WHERE term_date <= ?
                ORDER BY term_date DESC
                LIMIT 1
            """, [d.isoformat()]).fetchone()
            if rows:
                return {
                    "query_date": d.isoformat(),
                    "term_year": rows[0], "term_index": rows[1],
                    "term_name": rows[2], "term_date": rows[3],
                    "term_time": rows[4], "is_major": rows[5],
                }
        elif year:
            rows = conn.execute("""
                SELECT term_index, term_name, term_date, term_time, is_major
                FROM solar_terms WHERE year = ?
                ORDER BY term_index
            """, [year]).fetchall()
            return {
                "year": year,
                "terms": [
                    {"index": r[0], "name": r[1], "date": r[2],
                     "time": r[3], "is_major": r[4]}
                    for r in rows
                ],
            }
        return {"error": "Provide year, or year+month+day"}
    finally:
        conn.close()


# ── Quick test ────────────────────────────────────────────────────

if __name__ == "__main__":
    # Test solar term computation
    today = date.today()
    print(f"Today: {today}")
    print(f"Day GanZhi: {day_ganzhi(today)}")
    print(f"Full: {gregorian_to_ganzhi(today)}")

    # Show 2025 solar terms
    print("\n=== 2025 Solar Terms (major only) ===")
    for idx in range(24):
        dt = compute_solar_term(2025, idx)
        marker = " ← 节气" if idx in MAJOR_TERM_INDICES else ""
        print(f"  {SOLAR_TERM_NAMES[idx]:4s}: {dt.strftime('%Y-%m-%d %H:%M')}{marker}")
