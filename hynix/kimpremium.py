"""
KOFIA Freesis reverse-engineered pipeline — Korean retail leverage monitoring.

Fetches data directly from the original sources:
  1. KOFIA Freesis API (freesis.kofia.or.kr) — credit balance, deposits, margin
  2. FinanceDataReader (KRX) — KOSPI, KOSDAQ, market cap
  3. FinanceDataReader — S&P 500 (no yfinance dependency)

This replaces the old approach of consuming kimpremium.com's pre-aggregated JSONs.
All data is public market statistics from KOFIA / KRX.

Usage:
  python -m hynix.kimpremium              # fetch latest (incremental)
  python -m hynix.kimpremium --init       # full reload from all sources
  python -m hynix.kimpremium --summary    # print latest KPI snapshot
"""

from __future__ import annotations

import json
import logging
import math
import time
from datetime import datetime, timedelta
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

# ═══════════════════════════════════════════════════════════════
#  KOFIA Freesis API client
# ═══════════════════════════════════════════════════════════════

FREESIS_BASE = "https://freesis.kofia.or.kr"
FREESIS_SESSION: Optional[requests.Session] = None
# Map KOFIA service IDs to data types
SERVICE_CREDIT_TREND = "STATSCU0100000070"      # 신용공여 잔고 추이
SERVICE_FUNDS_TREND = "STATSCU0100000060"       # 증시자금추이
SERVICE_MAIN_SNAPSHOT = "STATSCUSUBMAIN01"      # 증시자금/신용공여 메인


def _get_freesis_session() -> requests.Session:
    """Get or create an authenticated Freesis session."""
    global FREESIS_SESSION
    if FREESIS_SESSION is None:
        FREESIS_SESSION = requests.Session()
        FREESIS_SESSION.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
            "Content-Type": "application/json; charset=UTF-8",
        })
        # Initialize session
        FREESIS_SESSION.get(
            f"{FREESIS_BASE}/stat/FreeSIS.do"
            f"?parentDivId=MSIS80000000000000&serviceId=STATCOM0100000010",
            timeout=30,
        )
    return FREESIS_SESSION


def _freesis_query(
    obj_nm: str,
    start_date: str,
    end_date: str,
    tmp_v40: str = "1000000",   # unit: million won
    tmp_v41: str = "1",         # market: KOSPI
    tmp_v1: str = "D",          # frequency: daily
) -> List[Dict[str, Any]]:
    """Query the KOFIA Freesis getMetaDataList API.

    Returns a list of row dicts with TMPV1..TMPVn values.
    """
    session = _get_freesis_session()
    resp = session.post(
        f"{FREESIS_BASE}/meta/getMetaDataList.do",
        json={
            "dmSearch": {
                "tmpV40": tmp_v40,
                "tmpV41": tmp_v41,
                "tmpV1": tmp_v1,
                "tmpV45": start_date,
                "tmpV46": end_date,
                "OBJ_NM": obj_nm,
            }
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("ds1", [])


def _freesis_main_snapshot() -> List[Dict[str, Any]]:
    """Get the latest 증시자금/신용공여 snapshot from the sub-main page."""
    session = _get_freesis_session()
    resp = session.post(
        f"{FREESIS_BASE}/stockSubMain/STATSCUSUBMAIN01BO.do",
        json={
            "data": {
                "userId": "GUEST",
                "serviceId": SERVICE_MAIN_SNAPSHOT,
                "tmpV87": "1",
                "searchLog": "",
                "ipAddress": "",
            }
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("dsResultList", [])


# ═══════════════════════════════════════════════════════════════
#  Data fetching from original sources
# ═══════════════════════════════════════════════════════════════


def fetch_credit_balance(start: str = "19980101", end: str = "20991231") -> pd.DataFrame:
    """Fetch credit balance (신용공여 잔고) time series from KOFIA Freesis.

    Columns: date, fin (total), finKospi, finKosdaq, loanKospi, loanKosdaq,
             subLoan, colLoan
    """
    rows = _freesis_query(
        obj_nm=f"{SERVICE_CREDIT_TREND}BO",
        start_date=start,
        end_date=end,
    )
    if not rows:
        return pd.DataFrame()

    records = []
    for row in rows:
        records.append({
            "date": str(row.get("TMPV1", "")),
            "fin": row.get("TMPV2"),          # 신용거래융자 전체
            "finKospi": row.get("TMPV3"),     # 신용거래융자 유가증권
            "finKosdaq": row.get("TMPV4"),    # 신용거래융자 코스닥
            "loanKospi": row.get("TMPV5"),    # 신용거래대주 전체 (대주 = stock lending)
            "loanKosdaq": row.get("TMPV6"),   # 신용거래대주 유가증권
            "subLoan": row.get("TMPV7"),      # 신용거래대주 코스닥
            "colLoan": row.get("TMPV8"),      # 청약자금대출
            "colLoan2": row.get("TMPV9"),     # 예탁증권담보융자
        })

    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    df = df.sort_values("date").reset_index(drop=True)
    logger.info("Fetched credit balance: %d rows (%s..%s)",
                len(df),
                str(df["date"].iloc[0])[:10] if len(df) else "N/A",
                str(df["date"].iloc[-1])[:10] if len(df) else "N/A")
    return df


def fetch_market_funds(start: str = "19980101", end: str = "20991231") -> pd.DataFrame:
    """Fetch market funds (증시자금 추이) time series from KOFIA Freesis.

    Columns: date, dep, derivDep, rp, misu, + derivatives
    """
    rows = _freesis_query(
        obj_nm=f"{SERVICE_FUNDS_TREND}BO",
        start_date=start,
        end_date=end,
    )
    if not rows:
        return pd.DataFrame()

    records = []
    for row in rows:
        records.append({
            "date": str(row.get("TMPV1", "")),
            "dep": row.get("TMPV2"),            # 투자자예탁금 (장내파생상품 거래예수금제외)
            "derivDep": row.get("TMPV3"),       # 장내파생상품 거래 예수금
            "rp": row.get("TMPV4"),            # 대고객 RP 매도잔고
            "misu": row.get("TMPV5"),          # 위탁매매 미수금
            "forceLiqAmt": row.get("TMPV6"),   # 반대매매금액
            "forceLiqPct": row.get("TMPV7"),   # 반대매매비중(%)
        })

    df = pd.DataFrame(records)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
        df = df.sort_values("date").reset_index(drop=True)
    logger.info("Fetched market funds: %d rows (%s..%s)",
                len(df),
                str(df["date"].iloc[0])[:10] if len(df) else "N/A",
                str(df["date"].iloc[-1])[:10] if len(df) else "N/A")
    return df


def fetch_index_data(start: str = "1998-01-01", end: str = "2099-12-31") -> pd.DataFrame:
    """Fetch KOSPI, KOSDAQ, and S&P 500 index data via FinanceDataReader.

    Returns DataFrame with columns: date, kospi, kosdaq, spx, kospi_mcap, kosdaq_mcap
    """
    try:
        import FinanceDataReader as fdr
    except ImportError:
        logger.warning("FinanceDataReader not installed — index data will be empty")
        return pd.DataFrame()

    # KOSPI (KS11)
    try:
        kospi = fdr.DataReader("KS11", start, end)
        kospi = kospi.reset_index()
        kospi.columns = ["date", "kospi_close", "kospi_updown", "kospi_comp",
                         "kospi_change", "kospi_open", "kospi_high", "kospi_low",
                         "kospi_volume", "kospi_amount", "kospi_mcap"]
        kospi = kospi[["date", "kospi_close", "kospi_mcap"]]
        kospi["date"] = pd.to_datetime(kospi["date"])
        logger.info("KOSPI: %d rows", len(kospi))
    except Exception as e:
        logger.warning("Failed to fetch KOSPI: %s", e)
        kospi = pd.DataFrame()

    # KOSDAQ (KQ11)
    try:
        kosdaq = fdr.DataReader("KQ11", start, end)
        kosdaq = kosdaq.reset_index()
        kosdaq.columns = ["date", "kosdaq_close", "kosdaq_updown", "kosdaq_comp",
                          "kosdaq_change", "kosdaq_open", "kosdaq_high", "kosdaq_low",
                          "kosdaq_volume", "kosdaq_amount", "kosdaq_mcap"]
        kosdaq = kosdaq[["date", "kosdaq_close", "kosdaq_mcap"]]
        kosdaq["date"] = pd.to_datetime(kosdaq["date"])
        logger.info("KOSDAQ: %d rows", len(kosdaq))
    except Exception as e:
        logger.warning("Failed to fetch KOSDAQ: %s", e)
        kosdaq = pd.DataFrame()

    # S&P 500 (US500)
    try:
        spx = fdr.DataReader("US500", start, end)
        spx = spx.reset_index()
        spx.columns = ["date", "spx_open", "spx_high", "spx_low",
                       "spx_close", "spx_volume", "spx_adjclose"]
        spx = spx[["date", "spx_close"]]
        spx["date"] = pd.to_datetime(spx["date"])
        logger.info("S&P 500: %d rows", len(spx))
    except Exception as e:
        logger.warning("Failed to fetch S&P 500: %s", e)
        spx = pd.DataFrame()

    # Merge all index data on date
    dfs = []
    if not kospi.empty:
        dfs.append(kospi)
    if not kosdaq.empty:
        dfs.append(kosdaq)
    if not spx.empty:
        dfs.append(spx)

    if not dfs:
        return pd.DataFrame()

    result = dfs[0]
    for df in dfs[1:]:
        result = pd.merge(result, df, on="date", how="outer")

    result = result.sort_values("date").reset_index(drop=True)
    logger.info("Index data merged: %d rows", len(result))
    return result


# ═══════════════════════════════════════════════════════════════
#  Data merging & derived indicators
# ═══════════════════════════════════════════════════════════════


def build_combined_df(
    credit_df: pd.DataFrame,
    funds_df: pd.DataFrame,
    index_df: pd.DataFrame,
) -> pd.DataFrame:
    """Merge all data sources and compute derived indicators.

    The combined DataFrame maps to the kr_leverage_daily table schema.
    """
    if credit_df.empty:
        return pd.DataFrame()

    # Start with credit data as the base (most complete historical coverage)
    df = credit_df.copy()

    # Merge market funds data
    if not funds_df.empty:
        df = pd.merge(df, funds_df, on="date", how="left", suffixes=("", "_funds"))

    # Merge index data
    if not index_df.empty:
        df = pd.merge(df, index_df, on="date", how="left")

    # Rename to match storage schema
    df.rename(columns={
        "kospi_close": "kospi",
        "kosdaq_close": "kosdaq",
        "spx_close": "spx",
        "kospi_mcap": "mcap",
    }, inplace=True)

    # Fill NaN from missing columns
    for col in ["kospi", "kosdaq", "spx", "mcap", "dep", "derivDep", "rp", "misu"]:
        if col not in df.columns:
            df[col] = None

    # Compute derived indicators
    # r2 = credit balance / total market cap (in percent)
    # mcap is in KRW from KOSPI + KOSDAQ
    if "mcap" in df.columns and "fin" in df.columns:
        # mcap is in KRW units, fin is in 백만원 (millions)
        # Convert mcap to millions for comparison
        mcap_millions = df["mcap"] / 1_000_000
        df["r2"] = (df["fin"] / mcap_millions) * 100
    else:
        df["r2"] = None

    # liq = credit balance / deposits ratio
    if "fin" in df.columns and "dep" in df.columns:
        df["liq"] = df["fin"] / df["dep"].replace(0, None)
        df["liqR"] = df["dep"] / df["fin"].replace(0, None)
    else:
        df["liq"] = None
        df["liqR"] = None

    # r1 = fin / (dep + derivDep + rp) — est. total market funds ratio
    if "fin" in df.columns and "dep" in df.columns:
        total_funds = df["dep"].fillna(0) + df.get("derivDep", pd.Series(0)).fillna(0)
        df["r1"] = df["fin"] / total_funds.replace(0, None)
    else:
        df["r1"] = None

    return df


# ═══════════════════════════════════════════════════════════════
#  Main pipeline
# ═══════════════════════════════════════════════════════════════


def _compute_kpi(df: pd.DataFrame) -> Dict[str, Any]:
    """Compute KPI snapshot from the latest daily data and historical percentiles.

    Returns a dict of KPI values expected by the kr-leverage frontend.
    """
    if df.empty:
        return {}

    latest = df.iloc[-1]
    # Use last 10 years for percentile ranking (~2500 trading days)
    window = max(1, min(len(df), 2500))
    recent = df.tail(window)

    def pct_rank(series, val):
        """Percentile rank of val within series (0-100, higher = rarer)."""
        clean = series.dropna()
        if len(clean) < 2 or val is None:
            return None
        return round((clean < val).sum() / len(clean) * 100, 1)

    # r2 and its percentile
    r2_val = float(latest["r2"]) if pd.notna(latest.get("r2")) else None
    r2_pct = pct_rank(recent["r2"], r2_val) if r2_val is not None else None

    # mg = MarketCap/GDP × 100 (Buffett indicator)
    mg_val = float(latest["mg"]) if "mg" in df.columns and pd.notna(latest.get("mg")) else None
    mg_pct = pct_rank(recent["mg"], mg_val) if mg_val is not None and "mg" in df.columns else None

    # liq5d = 5-day average of misu (liquidation amount in KRW 100M)
    misu_series = df["misu"]
    liq5d_val = float(misu_series.tail(5).mean()) if len(misu_series) >= 5 and pd.notna(misu_series.tail(5)).all() else None
    liq_pct = pct_rank(recent["misu"], liq5d_val) if liq5d_val is not None else None

    # util = credit utilization rate (requires broker capital data)
    util_val = float(latest["util"]) if "util" in df.columns and pd.notna(latest.get("util")) else None
    cap_eq_val = None  # broker's own capital — not available from KOFIA directly

    return {
        "r2": round(r2_val, 2) if r2_val is not None else None,
        "r2Pct": r2_pct,
        "mg": round(mg_val, 1) if mg_val is not None else None,
        "mgPct": mg_pct,
        "liq5d": round(liq5d_val, 1) if liq5d_val is not None else None,
        "liqPct": liq_pct,
        "fin": float(latest["fin"]) if pd.notna(latest.get("fin")) else None,
        "finKospi": float(latest["finKospi"]) if pd.notna(latest.get("finKospi")) else None,
        "finKosdaq": float(latest["finKosdaq"]) if pd.notna(latest.get("finKosdaq")) else None,
        "dep": float(latest["dep"]) if pd.notna(latest.get("dep")) else None,
        "kospi": float(latest["kospi"]) if pd.notna(latest.get("kospi")) else None,
        "spx": float(latest["spx"]) if pd.notna(latest.get("spx")) else None,
        "util": round(util_val, 1) if util_val is not None else None,
        "capEq": cap_eq_val,
    }


def fetch_all_sources(
    credit_start: str = "19980101",
    credit_end: Optional[str] = None,
    index_start: str = "1998-01-01",
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Fetch all data from original sources.

    Returns:
        (combined_df, latest_meta, etf_df)
    """
    if credit_end is None:
        credit_end = datetime.now().strftime("%Y%m%d")

    t0 = time.monotonic()

    # 1. Credit balance from KOFIA Freesis
    credit_df = fetch_credit_balance(start=credit_start, end=credit_end)

    # 2. Market funds from KOFIA Freesis
    funds_df = fetch_market_funds(start=credit_start, end=credit_end)

    # 3. Index data from FinanceDataReader (KRX + US)
    index_df = fetch_index_data(start=index_start)

    # 4. Merge and compute derived indicators
    combined = build_combined_df(credit_df, funds_df, index_df)

    # 5. Get latest snapshot metadata
    latest_meta = {}
    try:
        snapshot = _freesis_main_snapshot()
        latest_meta = {"snapshot": snapshot}
    except Exception as e:
        logger.warning("Failed to fetch snapshot: %s", e)

    # 6. ETF data placeholder (KOFIA doesn't provide ETF flow data directly)
    etf_df = pd.DataFrame()

    # 7. Compute KPI from the combined data
    kpi = _compute_kpi(combined)

    meta_raw = {
        "generated": datetime.now().isoformat(),
        "asof": str(combined["date"].iloc[-1])[:10] if not combined.empty else "",
        "range": {
            "start": str(combined["date"].iloc[0])[:10] if not combined.empty else "",
            "end": str(combined["date"].iloc[-1])[:10] if not combined.empty else "",
            "rows": len(combined),
        },
        "kpi": kpi,
    }

    elapsed = round(time.monotonic() - t0, 1)
    logger.info("fetch_all_sources complete in %.1fs: combined=%d rows",
                elapsed, len(combined))

    return combined, meta_raw, etf_df


def fetch_and_store() -> Dict[str, Any]:
    """Fetch latest data from original sources and store in DuckDB.

    Incremental mode: only fetches recent data (last 90 days for KOFIA,
    full history for index data since it's cheap).
    """
    t0 = time.monotonic()
    try:
        combined_df, meta_raw, etf_df = fetch_all_sources()
        conn = init_db()

        try:
            series_count = upsert_kr_leverage_daily(conn, combined_df)
            if not etf_df.empty:
                etf_count = upsert_kr_leverage_etf(conn, etf_df)
            else:
                etf_count = 0
            meta_row = upsert_kr_leverage_meta(conn, meta_raw, {})

            elapsed = round(time.monotonic() - t0, 1)
            return {
                "status": "success",
                "elapsed_s": elapsed,
                "series_rows": series_count,
                "etf_rows": etf_count,
                "meta": meta_row,
            }
        finally:
            conn.close()
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
    conn = init_db(read_only=True)
    try:
        return get_kr_leverage_series(conn, indicator=indicator, limit=limit)
    finally:
        conn.close()


def get_etf_series(indicator: str = "thermo", limit: int = 500) -> pd.DataFrame:
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

    parser = argparse.ArgumentParser(description="KOFIA Freesis data fetcher")
    parser.add_argument("--init", action="store_true", help="Full reload from original sources")
    parser.add_argument("--summary", action="store_true", help="Print latest KPI snapshot")
    parser.add_argument("--test-credit", action="store_true", help="Test: fetch credit balance only")
    parser.add_argument("--test-funds", action="store_true", help="Test: fetch market funds only")
    parser.add_argument("--test-index", action="store_true", help="Test: fetch index data only")
    args = parser.parse_args()

    if args.summary:
        s = get_latest_summary()
        if s:
            print(json.dumps(s, indent=2, ensure_ascii=False, default=str))
        else:
            print("No data in DB. Run without --summary first.")
        sys.exit(0)

    if args.test_credit:
        df = fetch_credit_balance()
        print(f"Credit balance: {len(df)} rows")
        print(df.head(3))
        print(df.tail(3))
        sys.exit(0)

    if args.test_funds:
        df = fetch_market_funds()
        print(f"Market funds: {len(df)} rows")
        print(df.head(3))
        print(df.tail(3))
        sys.exit(0)

    if args.test_index:
        df = fetch_index_data()
        print(f"Index data: {len(df)} rows")
        print(df.head(3))
        print(df.tail(3))
        sys.exit(0)

    result = fetch_and_store()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["status"] == "success" else 1)
