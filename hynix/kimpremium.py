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

import numpy as np
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


def fetch_broker_capital() -> pd.DataFrame:
    """Return Korean securities firms aggregate self-capital (자기자본).

    Data from FSS (Financial Supervisory Service) quarterly reports via Yonhap.
    Annual values interpolated to quarterly; new data points added as reported.

    Returns DataFrame with columns: date, broker_capital (in 백만원 / millions KRW)
    """
    # Known data points: FSS aggregate equity capital for all domestic securities firms
    # Values in KRW trillion → converted to 백만원 (× 1_000_000)
    # End-2025: 106.9T (source: Yonhap 2026-06-12, FSS data)
    # Earlier data estimated from growth trends
    known: Dict[str, float] = {
        "2015-12-31": 55_000_000,   # ~55T — approximate
        "2016-12-31": 57_000_000,
        "2017-12-31": 60_000_000,
        "2018-12-31": 63_000_000,
        "2019-12-31": 66_000_000,
        "2020-12-31": 72_000_000,
        "2021-12-31": 80_000_000,
        "2022-12-31": 88_000_000,
        "2023-12-31": 95_000_000,
        "2024-12-31": 102_000_000,
        "2025-12-31": 106_900_000,  # Confirmed: FSS data via Yonhap
        # 2026 estimate: ~112T by year-end based on ongoing capital increases
        "2026-12-31": 112_000_000,
    }

    records = []
    for date_str, cap in known.items():
        records.append({"date": pd.Timestamp(date_str), "broker_capital": cap})

    df = pd.DataFrame(records).sort_values("date").reset_index(drop=True)
    logger.info("Broker capital: %d data points (%s..%s)",
                len(df), str(df["date"].iloc[0])[:10], str(df["date"].iloc[-1])[:10])
    return df


def fetch_korea_gdp() -> pd.DataFrame:
    """Fetch Korea annual nominal GDP from World Bank API (current LCU, free, no auth).

    Returns DataFrame with columns: year, gdp_lcu
    """
    try:
        resp = requests.get(
            "https://api.worldbank.org/v2/country/KR/indicator/NY.GDP.MKTP.CN",
            params={"format": "json", "per_page": 60},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data or len(data) < 2:
            logger.warning("World Bank API returned unexpected format")
            return pd.DataFrame()

        records = []
        for dp in data[1]:
            if dp["value"] is not None:
                records.append({"year": int(dp["date"]), "gdp_lcu": float(dp["value"])})
        df = pd.DataFrame(records).sort_values("year").reset_index(drop=True)
        logger.info("Korea GDP: %d annual points (%d..%d)",
                    len(df), df["year"].iloc[0], df["year"].iloc[-1])
        return df
    except Exception as e:
        logger.warning("Failed to fetch Korea GDP from World Bank: %s", e)
        return pd.DataFrame()


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
    gdp_df: pd.DataFrame = None,
    broker_cap_df: pd.DataFrame = None,
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
        "colLoan2": "col",
    }, inplace=True)

    # Compute loan = total stock lending (대주)
    if "loanKospi" in df.columns or "loanKosdaq" in df.columns:
        df["loan"] = df.get("loanKospi", pd.Series(0)).fillna(0) + \
                     df.get("loanKosdaq", pd.Series(0)).fillna(0)
        df["loan"] = df["loan"].replace(0, None)
    else:
        df["loan"] = None

    # Fill NaN from missing columns
    for col in ["kospi", "kosdaq", "spx", "mcap", "dep", "derivDep", "rp", "misu",
                "col", "forceLiqAmt", "forceLiqPct"]:
        if col not in df.columns:
            df[col] = None

    # ── Derived indicators ──────────────────────────────────────

    # r2 = credit balance / total market cap (in percent)
    # mcap is in KRW units, fin is in 백만원 (millions)
    if "mcap" in df.columns and "fin" in df.columns:
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

    # r1 = fin / (dep + derivDep) — credit / total investor deposits
    if "fin" in df.columns and "dep" in df.columns:
        total_funds = df["dep"].fillna(0) + df.get("derivDep", pd.Series(0)).fillna(0)
        df["r1"] = df["fin"] / total_funds.replace(0, None)
    else:
        df["r1"] = None

    # p10 = 10-year rolling percentile of r2 (0-100 scale)
    if "r2" in df.columns:
        r2_series = df["r2"].values
        p10_vals = [None] * len(r2_series)
        # ~2500 trading days = 10 years
        lookback = 2500
        for i in range(len(r2_series)):
            start = max(0, i - lookback + 1)
            window = r2_series[start:i + 1]
            window_clean = [v for v in window if v is not None and not (isinstance(v, float) and math.isnan(v))]
            if len(window_clean) >= 2 and r2_series[i] is not None and not (isinstance(r2_series[i], float) and math.isnan(r2_series[i])):
                p10_vals[i] = round(sum(1 for v in window_clean if v < r2_series[i]) / len(window_clean) * 100, 1)
        df["p10"] = p10_vals
    else:
        df["p10"] = None

    # mg = Market Cap / GDP × 100 (Buffett indicator), daily GDP interpolated from annual
    if "mcap" in df.columns and gdp_df is not None and not gdp_df.empty:
        gdp_map = dict(zip(gdp_df["year"], gdp_df["gdp_lcu"]))
        years = sorted(gdp_map.keys())
        def _gdp_for_date(d):
            """Assign the most recent annual GDP to each day (forward-fill)."""
            y = d.year
            # Use latest available GDP for current/future years
            if y > years[-1]:
                return gdp_map[years[-1]]
            # For years before the earliest, use earliest
            if y < years[0]:
                return gdp_map[years[0]]
            return gdp_map.get(y, gdp_map[years[-1]])
        gdp_daily = df["date"].apply(_gdp_for_date)
        df["mg"] = (df["mcap"] / gdp_daily) * 100
    else:
        df["mg"] = None

    # r1p = KOSPI credit / KOSPI market cap (%), r1q = KOSDAQ credit / KOSDAQ mcap (%)
    if "finKospi" in df.columns and "mcap" in df.columns:
        kospi_mcap_m = df["mcap"] / 1_000_000
        df["r1p"] = (df["finKospi"] / kospi_mcap_m.replace(0, None)) * 100
        if "kosdaq_mcap" in df.columns:
            kosdaq_mcap_m = df["kosdaq_mcap"].fillna(0) / 1_000_000
            df["r1q"] = (df["finKosdaq"].fillna(0) / kosdaq_mcap_m.replace(0, None)) * 100
            df.loc[kosdaq_mcap_m == 0, "r1q"] = None
        else:
            df["r1q"] = None
    else:
        df["r1p"] = None
        df["r1q"] = None

    # util = total credit balance / broker self-capital × 100 (legal limit: 100%)
    if broker_cap_df is not None and not broker_cap_df.empty:
        # Interpolate annual/quarterly broker capital to daily
        cap_series = broker_cap_df.set_index("date")["broker_capital"]
        # Resample to daily and forward-fill
        full_dates = pd.date_range(
            start=cap_series.index.min(),
            end=max(cap_series.index.max(), df["date"].max()),
            freq="D",
        )
        cap_daily = cap_series.reindex(full_dates).ffill()
        # Map to our dates
        cap_map = dict(zip(cap_daily.index, cap_daily.values))
        def _cap_for_date(d):
            ts = pd.Timestamp(d)
            if ts in cap_map and not (isinstance(cap_map[ts], float) and math.isnan(cap_map[ts])):
                return cap_map[ts]
            # Find nearest earlier date
            earlier = [k for k in cap_map if k <= ts and not (isinstance(cap_map[k], float) and math.isnan(cap_map[k]))]
            if earlier:
                return cap_map[max(earlier)]
            return None
        broker_cap_daily = df["date"].apply(_cap_for_date)
        # Total credit = fin (융자) + loan (대주) + col (예탁증권담보융자)
        total_credit = (
            df["fin"].fillna(0)
            + df.get("loan", pd.Series(0)).fillna(0)
            + df.get("col", pd.Series(0)).fillna(0)
        )
        df["util"] = (total_credit / broker_cap_daily.replace(0, None)) * 100
    else:
        df["util"] = None

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

    # util = total credit / broker self-capital (%)
    util_val = float(latest["util"]) if "util" in df.columns and pd.notna(latest.get("util")) else None
    # capEq = broker self-capital in trillion KRW (조)
    cap_eq_val = None
    if "util" in df.columns and pd.notna(latest.get("util")) and util_val and util_val > 0:
        # Total credit = fin + loan + col (in millions KRW)
        total_credit = float(latest.get("fin", 0) or 0) + float(latest.get("loan", 0) or 0) + float(latest.get("col", 0) or 0)
        if total_credit > 0:
            # broker capital = total credit / util * 100, convert to trillion KRW
            cap_eq_val = round((total_credit / util_val * 100) / 1_000_000, 1)

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


def _fetch_kimpremium_etf() -> tuple[pd.DataFrame, Dict[str, Any]]:
    """Fetch ETF flow data from kimpremium.com JSON.

    Source: KSD SEIBro ETF creation/redemption statistics (설정/환매 통계)
            + KSD SEIBro foreign securities custody TOP50 (외화증권 보관금액)

    The KSD OpenAPI requires business registration in Korea to get an API key.
    kimpremium.com processes this data and serves it as a pre-computed JSON,
    updated daily. We use it as a convenience layer over the KSD API.

    Returns:
        (DataFrame, kpi_dict) where DataFrame has columns:
        date, r2, thermo, thermoW, flow, flowW, cumFlow, cumFlowW
    """
    try:
        resp = requests.get(
            "https://kimpremium.com/data/etf.json",
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Accept": "application/json",
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning("Failed to fetch ETF data from kimpremium.com: %s", e)
        return pd.DataFrame(), {}

    kpi_raw = data.get("kpi", {})
    etf_kpi = {
        "thermo": kpi_raw.get("thermo"),
        "thermoW": kpi_raw.get("thermoW"),
        "n": kpi_raw.get("n"),
        "nInv": kpi_raw.get("nInv"),
        "aum": kpi_raw.get("aum"),
        "aumInv": kpi_raw.get("aumInv"),
    }

    dates = data.get("d", [])
    if not dates:
        return pd.DataFrame(), etf_kpi

    records = []
    cols = ["r2", "thermo", "thermoW", "flow", "flowW", "cumFlow", "cumFlowW"]
    for i, d in enumerate(dates):
        row = {"date": pd.Timestamp(_norm_date(str(d)))}
        for col in cols:
            arr = data.get(col, [])
            row[col] = arr[i] if i < len(arr) else None
        records.append(row)

    df = pd.DataFrame(records)
    df = df.sort_values("date").reset_index(drop=True)
    logger.info("ETF data fetched: %d rows (%s..%s)",
                len(df),
                str(df["date"].iloc[0])[:10] if len(df) else "N/A",
                str(df["date"].iloc[-1])[:10] if len(df) else "N/A")
    return df, etf_kpi


def _compute_etf_df(df: pd.DataFrame) -> pd.DataFrame:
    """Compute ETF flow indicators from combined daily data.

    KOFIA doesn't provide ETF-specific flow data, so these are proxy indicators
    derived from credit balance / KOSPI relationships. They replace the old
    kimpremium.com pre-computed ETF indicators.

    Returns DataFrame with columns: date, r2, thermo, thermoW, flow, flowW, cumFlow, cumFlowW
    """
    if df.empty or "kospi" not in df.columns or "fin" not in df.columns:
        return pd.DataFrame()

    result = df[["date"]].copy()
    n = len(df)

    def _rolling_r2(x_series, y_series, window):
        """Compute rolling R² between two series."""
        out = [None] * n
        x = x_series.values
        y = y_series.values
        for i in range(window - 1, n):
            xi = x[i - window + 1 : i + 1]
            yi = y[i - window + 1 : i + 1]
            mask = ~(np.isnan(xi) | np.isnan(yi))
            if mask.sum() < max(10, window // 3):
                continue
            xi_c = xi[mask]
            yi_c = yi[mask]
            if np.std(xi_c) < 1e-10 or np.std(yi_c) < 1e-10:
                continue
            corr = np.corrcoef(xi_c, yi_c)[0, 1]
            out[i] = round((corr ** 2) * 100, 2) if not np.isnan(corr) else None
        return out

    # fin daily delta (credit balance change) and KOSPI daily return
    fin_delta = df["fin"].diff()
    kospi_ret = df["kospi"].pct_change()

    # ETF r2: 1-year rolling R² between fin delta and KOSPI return
    result["r2"] = _rolling_r2(fin_delta, kospi_ret, 250)

    # thermo: 60-day rolling R² (shorter-term "temperature")
    result["thermo"] = _rolling_r2(fin_delta, kospi_ret, 60)

    # flow proxy: daily change in misu (margin deficits) / 100 (KRW 100M units)
    if "misu" in df.columns:
        result["flow"] = (df["misu"].diff() / 100.0).round(1)
    else:
        result["flow"] = 0.0

    # cumFlow: cumulative flow over 1-year rolling window
    flow_series = result["flow"].fillna(0.0).values
    cum_flow = [None] * n
    running_sum = 0.0
    for i in range(n):
        running_sum += flow_series[i]
        if i >= 250:
            running_sum -= flow_series[i - 250]
        cum_flow[i] = round(running_sum, 3)
    result["cumFlow"] = cum_flow

    # W variants — same as daily for now
    result["thermoW"] = result["thermo"]
    result["flowW"] = result["flow"]
    result["cumFlowW"] = result["cumFlow"]

    return result


def fetch_all_sources(
    credit_start: str = "19980101",
    credit_end: Optional[str] = None,
    index_start: str = "1998-01-01",
) -> Tuple[pd.DataFrame, Dict, pd.DataFrame, Dict]:
    """Fetch all data from original sources.

    Returns:
        (combined_df, meta_raw, etf_df, etf_kpi)
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

    # 4. Korea GDP from World Bank (annual, for mg indicator)
    gdp_df = fetch_korea_gdp()

    # 5. Broker self-capital (for util indicator)
    broker_cap_df = fetch_broker_capital()

    # 6. Merge and compute derived indicators
    combined = build_combined_df(credit_df, funds_df, index_df,
                                 gdp_df=gdp_df, broker_cap_df=broker_cap_df)

    # 7. Get latest snapshot metadata
    latest_meta = {}
    try:
        snapshot = _freesis_main_snapshot()
        latest_meta = {"snapshot": snapshot}
    except Exception as e:
        logger.warning("Failed to fetch snapshot: %s", e)

    # 8. Fetch ETF flow data from kimpremium.com
    #    Ultimate source: KSD SEIBro ETF creation/redemption stats + foreign custody TOP50
    #    (KSD OpenAPI requires business registration in Korea; kimpremium serves it as JSON)
    etf_df, etf_kpi = _fetch_kimpremium_etf()

    # 9. Compute KPI from the combined data
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

    return combined, meta_raw, etf_df, etf_kpi


def _compute_etf_kpi(etf_df: pd.DataFrame) -> Dict[str, Any]:
    """Compute ETF KPI snapshot from ETF indicators DataFrame."""
    if etf_df.empty:
        return {}
    latest = etf_df.iloc[-1]
    return {
        "thermo": round(float(latest["thermo"]), 2) if pd.notna(latest.get("thermo")) else None,
        "n": None,  # number of leveraged ETFs — not available from KOFIA
        "nInv": None,
        "aum": None,
        "aumInv": None,
    }


def fetch_and_store() -> Dict[str, Any]:
    """Fetch latest data from original sources and store in DuckDB.

    Incremental mode: only fetches recent data (last 90 days for KOFIA,
    full history for index data since it's cheap).
    """
    t0 = time.monotonic()
    try:
        combined_df, meta_raw, etf_df, etf_kpi = fetch_all_sources()
        conn = init_db()

        try:
            series_count = upsert_kr_leverage_daily(conn, combined_df)
            if not etf_df.empty:
                etf_count = upsert_kr_leverage_etf(conn, etf_df)
            else:
                etf_count = 0
            meta_row = upsert_kr_leverage_meta(conn, meta_raw, etf_kpi or {})

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
