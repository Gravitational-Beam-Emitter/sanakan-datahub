"""
kimpremium.com reverse-engineered pipeline — Korean retail leverage monitoring.

Fetches the 3 JSON files that power kimpremium.com and stores them in DuckDB.
All data is public market statistics from KOFIA / KSD SEIBro, originally served
as static JSON by the site.

Data sources:
  data/series.json  — Daily time series since 1998-07-01 (23 indicators, 7138 rows)
  data/meta.json    — Latest snapshot + KPI + generation timestamp
  data/etf.json     — Leveraged ETF flow data since 2024-01-02

Usage:
  python -m hynix.kimpremium              # fetch latest
  python -m hynix.kimpremium --init       # full reload (fetches all 3 files)
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests

from hynix.storage import (
    _norm_date,
    init_db,
    upsert_kr_leverage_daily,
    upsert_kr_leverage_etf,
    upsert_kr_leverage_meta,
    get_kr_leverage_latest,
    get_kr_leverage_series,
    get_kr_leverage_etf,
)

logger = logging.getLogger("hynix.kimpremium")

BASE_URL = "https://kimpremium.com"
DATA_URLS = {
    "series": f"{BASE_URL}/data/series.json",
    "meta": f"{BASE_URL}/data/meta.json",
    "etf": f"{BASE_URL}/data/etf.json",
}

SESSION: Optional[requests.Session] = None


def _get_session() -> requests.Session:
    global SESSION
    if SESSION is None:
        SESSION = requests.Session()
        SESSION.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
        })
    return SESSION


def _fetch_json(name: str, timeout: int = 30) -> Dict[str, Any]:
    """Fetch a JSON endpoint from kimpremium.com."""
    url = DATA_URLS[name]
    session = _get_session()
    resp = session.get(url, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    logger.info("Fetched %s: %d bytes, keys=%s", name, len(resp.content), list(data.keys()))
    return data


# ═══════════════════════════════════════════════════════════════
#  Data parsing
# ═══════════════════════════════════════════════════════════════

def _parse_series(data: Dict[str, Any]) -> pd.DataFrame:
    """Parse series.json into a flat DataFrame with dates.

    The raw JSON is a dict with keys 'd', 'r2', 'p10', 'kospi', ... where each
    value is a list aligned by index to the 'd' array.
    """
    dates = data.pop("d", [])
    if not dates:
        return pd.DataFrame()

    rows = []
    for i, d in enumerate(dates):
        row = {"date": _norm_date(d)}
        for col in data:
            val = data[col][i] if i < len(data[col]) else None
            row[col] = val
        rows.append(row)

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    return df


def _parse_etf(data: Dict[str, Any]) -> Tuple[pd.DataFrame, Dict, str]:
    """Parse etf.json into a flat DataFrame + KPI dict + anchor date."""
    kpi = data.pop("kpi", {})
    anchor = data.pop("anchor", "2024-01-02")
    asof = data.pop("asof", "")
    _universe = data.pop("universe", [])
    dates = data.pop("d", [])

    if not dates:
        return pd.DataFrame(), kpi, anchor

    rows = []
    for i, d in enumerate(dates):
        row = {"date": _norm_date(d)}
        for col in data:
            val = data[col][i] if i < len(data[col]) else None
            row[col] = val
        rows.append(row)

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    return df, kpi, anchor


def fetch_all() -> Tuple[pd.DataFrame, Dict, pd.DataFrame, Dict, str]:
    """Fetch and parse all kimpremium data.

    Returns:
        (series_df, meta_raw, etf_df, etf_kpi, etf_anchor)
    """
    series_raw = _fetch_json("series")
    meta_raw = _fetch_json("meta")
    etf_raw = _fetch_json("etf")

    series_df = _parse_series(series_raw)
    etf_df, etf_kpi, anchor = _parse_etf(etf_raw)

    logger.info(
        "Parsed: series=%d rows (%s..%s), etf=%d rows (%s..%s), meta=%s",
        len(series_df),
        str(series_df["date"].iloc[0])[:10] if len(series_df) else "N/A",
        str(series_df["date"].iloc[-1])[:10] if len(series_df) else "N/A",
        len(etf_df),
        str(etf_df["date"].iloc[0])[:10] if len(etf_df) else "N/A",
        str(etf_df["date"].iloc[-1])[:10] if len(etf_df) else "N/A",
        meta_raw.get("generated", "?"),
    )

    return series_df, meta_raw, etf_df, etf_kpi, anchor


# ═══════════════════════════════════════════════════════════════
#  Storage
# ═══════════════════════════════════════════════════════════════

def store_all(
    series_df: pd.DataFrame,
    meta_raw: Dict,
    etf_df: pd.DataFrame,
    etf_kpi: Dict,
) -> Dict[str, int]:
    """Store all kimpremium data into DuckDB."""
    conn = init_db()

    try:
        series_count = upsert_kr_leverage_daily(conn, series_df)
        etf_count = upsert_kr_leverage_etf(conn, etf_df)
        meta_row = upsert_kr_leverage_meta(conn, meta_raw, etf_kpi)

        return {
            "series_rows": series_count,
            "etf_rows": etf_count,
            "meta": meta_row,
        }
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════
#  Orchestrator
# ═══════════════════════════════════════════════════════════════

def fetch_and_store() -> Dict[str, Any]:
    """Fetch latest data from kimpremium.com and store in DB."""
    t0 = time.monotonic()
    try:
        series_df, meta_raw, etf_df, etf_kpi, _anchor = fetch_all()
        counts = store_all(series_df, meta_raw, etf_df, etf_kpi)
        elapsed = round(time.monotonic() - t0, 1)
        return {
            "status": "success",
            "elapsed_s": elapsed,
            "generated": meta_raw.get("generated", "?"),
            "asof": meta_raw.get("asof", "?"),
            **counts,
        }
    except Exception:
        logger.exception("kimpremium fetch failed")
        return {
            "status": "error",
            "elapsed_s": round(time.monotonic() - t0, 1),
        }


def get_latest_summary() -> Optional[Dict[str, Any]]:
    """Get the latest KPI snapshot from the database."""
    conn = init_db(read_only=True)
    try:
        return get_kr_leverage_latest(conn)
    finally:
        conn.close()


def get_series(indicator: str = "r2", limit: int = 500) -> pd.DataFrame:
    """Get a time series from the DB."""
    conn = init_db(read_only=True)
    try:
        return get_kr_leverage_series(conn, indicator=indicator, limit=limit)
    finally:
        conn.close()


def get_etf_series(indicator: str = "thermo", limit: int = 500) -> pd.DataFrame:
    """Get an ETF time series from the DB."""
    conn = init_db(read_only=True)
    try:
        return get_kr_leverage_etf(conn, indicator=indicator, limit=limit)
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

    parser = argparse.ArgumentParser(description="kimpremium.com data fetcher")
    parser.add_argument("--init", action="store_true", help="Full reload")
    parser.add_argument("--summary", action="store_true", help="Print latest KPI snapshot")
    args = parser.parse_args()

    if args.summary:
        s = get_latest_summary()
        if s:
            print(json.dumps(s, indent=2, ensure_ascii=False, default=str))
        else:
            print("No data in DB. Run without --summary first.")
        sys.exit(0)

    result = fetch_and_store()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["status"] == "success" else 1)
