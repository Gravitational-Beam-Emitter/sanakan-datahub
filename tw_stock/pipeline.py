"""
Data pipeline — fetch Taiwan stock data from TWSE OpenAPI + TPEx + yfinance.

Usage:
    python -m tw_stock.pipeline                # fetch latest trading day
    python -m tw_stock.pipeline --init          # full init: listings + backfill + indices
    python -m tw_stock.pipeline --date 20260619 # fetch specific date
    python -m tw_stock.pipeline --no-llm        # skip LLM tagging
    python -m tw_stock.pipeline --all           # fetch last 5 trading days
"""

from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pandas as pd
import requests

from tw_stock.storage import (
    init_db,
    upsert_listed_stocks,
    upsert_daily_prices,
    upsert_market_indices,
    upsert_significant_movers,
    upsert_stock_reasons,
    upsert_daily_narratives,
    log_fetch,
    get_listed_stocks,
    get_counts,
)

logger = logging.getLogger("tw_stock.pipeline")

# Significant move threshold (Taiwan market: ±10% is daily limit, ±5% is notable)
MOVER_THRESHOLD = 5.0

# TWSE OpenAPI base
TWSE_OPENAPI = "https://openapi.twse.com.tw/v1"


def _to_date_str(d: Any) -> str:
    """Convert Timestamp/date/datetime to YYYY-MM-DD string."""
    if hasattr(d, "strftime"):
        return d.strftime("%Y-%m-%d")
    return str(d)


# ═══════════════════════════════════════════════════════════════
#  Listing fetch
# ═══════════════════════════════════════════════════════════════

def fetch_listings(db_path: Optional[str] = None) -> Dict[str, int]:
    """Fetch all TWSE and TPEx listings and store them."""
    conn = init_db(db_path)
    counts = {}

    # ── TWSE listed companies ──
    try:
        resp = requests.get(f"{TWSE_OPENAPI}/opendata/t187ap03_L", timeout=30)
        resp.raise_for_status()
        twse_data = resp.json()
        if twse_data:
            rows = []
            for item in twse_data:
                listing_date = item.get("上市日期", "").strip()
                if listing_date and len(listing_date) == 8:
                    listing_date = f"{listing_date[:4]}-{listing_date[4:6]}-{listing_date[6:8]}"
                else:
                    listing_date = None
                rows.append({
                    "code": item.get("公司代號", "").strip(),
                    "name": item.get("公司簡稱", "").strip(),
                    "name_en": item.get("英文簡稱", "").strip() or None,
                    "market": "TWSE",
                    "sector": item.get("產業別", "").strip() or None,
                    "industry": item.get("產業別", "").strip() or None,
                    "listing_date": listing_date,
                    "shares_outstanding": None,
                    "market_cap": None,
                })
            if rows:
                df = pd.DataFrame(rows)
                counts["TWSE"] = upsert_listed_stocks(conn, df)
                logger.info(f"TWSE: {counts['TWSE']} stocks upserted")
            else:
                counts["TWSE"] = 0
        else:
            counts["TWSE"] = 0
            logger.warning("TWSE: empty response")
    except Exception as e:
        logger.error(f"TWSE listing fetch failed: {e}")
        counts["TWSE"] = 0

    # ── TPEx OTC listed companies ──
    try:
        tpex_url = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"
        resp = requests.get(tpex_url, timeout=30)
        resp.raise_for_status()
        tpex_data = resp.json()
        if tpex_data:
            rows = []
            for item in tpex_data:
                listing_date = item.get("DateOfListing", "").strip()
                if listing_date and len(listing_date) == 8:
                    listing_date = f"{listing_date[:4]}-{listing_date[4:6]}-{listing_date[6:8]}"
                else:
                    listing_date = None
                rows.append({
                    "code": item.get("SecuritiesCompanyCode", "").strip(),
                    "name": item.get("CompanyAbbreviation", "").strip(),
                    "name_en": None,
                    "market": "TPEx",
                    "sector": item.get("SecuritiesIndustryCode", "").strip() or None,
                    "industry": item.get("SecuritiesIndustryCode", "").strip() or None,
                    "listing_date": listing_date,
                    "shares_outstanding": None,
                    "market_cap": None,
                })
            if rows:
                df = pd.DataFrame(rows)
                counts["TPEx"] = upsert_listed_stocks(conn, df)
                logger.info(f"TPEx: {counts['TPEx']} stocks upserted")
            else:
                counts["TPEx"] = 0
        else:
            counts["TPEx"] = 0
            logger.warning("TPEx: empty response")
    except Exception as e:
        logger.error(f"TPEx listing fetch failed: {e}")
        counts["TPEx"] = 0

    conn.close()
    return counts


# ═══════════════════════════════════════════════════════════════
#  Price fetch
# ═══════════════════════════════════════════════════════════════

def _parse_roc_date(roc_date: str) -> str:
    """Convert ROC date (YYYMMDD, 6-7 digits) to Gregorian (YYYY-MM-DD).

    ROC year 100 = 2011. 7-digit format means year >= 100.
    E.g., 1150618 → 2026-06-18, 990618 → 2010-06-18.
    """
    try:
        if not roc_date or roc_date == "--":
            return roc_date
        # Normalize: ROC dates are 3-digit year + 2-digit month + 2-digit day = 7 chars
        # If 6 chars, year is 2-digit (0-pad needed)
        if len(roc_date) == 6:
            roc_date = "0" + roc_date  # e.g. 0990618 → 0990618 (year 99)
        elif len(roc_date) != 7:
            return roc_date
        roc_year = int(roc_date[:3])
        gregorian_year = roc_year + 1911
        return f"{gregorian_year}-{roc_date[3:5]}-{roc_date[5:7]}"
    except (ValueError, IndexError):
        return roc_date


def fetch_daily_prices(date: str, db_path: Optional[str] = None,
                        tickers: Optional[List[str]] = None) -> int:
    """Fetch daily OHLCV for a date using TWSE OpenAPI + yfinance fallback.

    Uses TWSE STOCK_DAY_ALL for all TWSE stocks at once.
    Uses yfinance for TPEx stocks.
    """
    conn = init_db(db_path)

    if tickers is None:
        df_stocks = get_listed_stocks(conn, active_only=True, limit=10000)
        tickers_twse = df_stocks[df_stocks["market"] == "TWSE"]["code"].tolist()
        tickers_tpex = df_stocks[df_stocks["market"] == "TPEx"]["code"].tolist()
    else:
        df_stocks = get_listed_stocks(conn, active_only=True, limit=10000)
        twse_set = set(df_stocks[df_stocks["market"] == "TWSE"]["code"].tolist())
        tickers_twse = [t for t in tickers if t in twse_set]
        tickers_tpex = [t for t in tickers if t not in twse_set]

    rows = []

    # ── TWSE stocks via STOCK_DAY_ALL (batch, single API call) ──
    if tickers_twse:
        try:
            resp = requests.get(f"{TWSE_OPENAPI}/exchangeReport/STOCK_DAY_ALL", timeout=30)
            resp.raise_for_status()
            all_data = resp.json()
            if all_data:
                code_set = set(tickers_twse)
                for item in all_data:
                    code = item.get("Code", "").strip()
                    if code not in code_set:
                        continue
                    try:
                        open_price = float(item["OpeningPrice"]) if item.get("OpeningPrice") and item["OpeningPrice"] != "--" else None
                        high_price = float(item["HighestPrice"]) if item.get("HighestPrice") and item["HighestPrice"] != "--" else None
                        low_price = float(item["LowestPrice"]) if item.get("LowestPrice") and item["LowestPrice"] != "--" else None
                        close_price = float(item["ClosingPrice"]) if item.get("ClosingPrice") and item["ClosingPrice"] != "--" else None
                        volume = int(float(item["TradeVolume"])) if item.get("TradeVolume") and item["TradeVolume"] != "--" else None

                        # Change is price change in TWD; compute pct from close and change
                        change_val = float(item["Change"]) if item.get("Change") and item["Change"] != "--" else None
                        if change_val is not None and close_price is not None and close_price != 0:
                            prev_close = close_price - change_val
                            change_pct = (change_val / prev_close) * 100 if prev_close != 0 else None
                        else:
                            change_pct = None

                        rows.append({
                            "date": _parse_roc_date(item.get("Date", "")),
                            "code": code,
                            "open": open_price,
                            "high": high_price,
                            "low": low_price,
                            "close": close_price,
                            "volume": volume,
                            "change_pct": round(change_pct, 2) if change_pct is not None else None,
                        })
                    except (ValueError, TypeError, KeyError):
                        continue
                logger.info(f"TWSE STOCK_DAY_ALL: {len(rows)} rows matched")
        except Exception as e:
            logger.warning(f"TWSE STOCK_DAY_ALL failed: {e}, falling back to yfinance")

    # ── TPEx stocks via yfinance ──
    if tickers_tpex:
        try:
            import yfinance as yf
            for code in tickers_tpex[:200]:  # Batch limit for yfinance
                try:
                    ticker = yf.Ticker(f"{code}.TWO")
                    nd = _norm_date(date)
                    df = ticker.history(start=nd, end=(pd.to_datetime(nd) + timedelta(days=1)).strftime("%Y-%m-%d"))
                    if df.empty:
                        continue
                    for idx, row_ in df.iterrows():
                        rows.append({
                            "date": _to_date_str(idx),
                            "code": code,
                            "open": float(row_["Open"]) if pd.notna(row_["Open"]) else None,
                            "high": float(row_["High"]) if pd.notna(row_["High"]) else None,
                            "low": float(row_["Low"]) if pd.notna(row_["Low"]) else None,
                            "close": float(row_["Close"]) if pd.notna(row_["Close"]) else None,
                            "volume": int(row_["Volume"]) if pd.notna(row_["Volume"]) else None,
                            "change_pct": None,
                        })
                except Exception:
                    continue
        except ImportError:
            logger.warning("yfinance not installed — skipping TPEx prices")
        except Exception as e:
            logger.warning(f"yfinance TPEx fetch error: {e}")

    count = 0
    if rows:
        df_prices = pd.DataFrame(rows)
        count = upsert_daily_prices(conn, df_prices)
        logger.info(f"Prices for {_to_date_str(date)}: {count} records")

    conn.close()
    return count


def _norm_date(date: str) -> str:
    """Normalize date to YYYY-MM-DD."""
    d = date.replace("-", "").replace("/", "")
    if len(d) == 8:
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    return date


def fetch_indices(date: str = None, db_path: Optional[str] = None) -> int:
    """Fetch TAIEX (加權指數) and TPEx (櫃買指數) index data via yfinance.

    Tickers: ^TWII for TAIEX, ^TWOII for TPEx
    """
    conn = init_db(db_path)

    indices = {
        "^TWII": "TAIEX",
        "^TWOII": "TPEx Index",
    }

    rows = []
    for idx_code, idx_name in indices.items():
        try:
            import yfinance as yf
            if date:
                nd = _norm_date(date)
                ticker = yf.Ticker(idx_code)
                df = ticker.history(start=nd, end=(pd.to_datetime(nd) + timedelta(days=1)).strftime("%Y-%m-%d"))
            else:
                ticker = yf.Ticker(idx_code)
                df = ticker.history(period="3mo")

            if df.empty:
                continue

            for idx, row_ in df.iterrows():
                rows.append({
                    "date": _to_date_str(idx),
                    "index_code": idx_code,
                    "index_name": idx_name,
                    "open": float(row_["Open"]) if pd.notna(row_["Open"]) else None,
                    "high": float(row_["High"]) if pd.notna(row_["High"]) else None,
                    "low": float(row_["Low"]) if pd.notna(row_["Low"]) else None,
                    "close": float(row_["Close"]) if pd.notna(row_["Close"]) else None,
                    "volume": float(row_["Volume"]) if pd.notna(row_["Volume"]) else None,
                    "change_pct": None,
                })
        except ImportError:
            logger.warning("yfinance not installed — skipping indices")
        except Exception as e:
            logger.error(f"Index {idx_code} fetch failed: {e}")

    count = 0
    if rows:
        df_indices = pd.DataFrame(rows)
        count = upsert_market_indices(conn, df_indices)
        logger.info(f"Indices: {count} records upserted")

    conn.close()
    return count


def fetch_significant_movers(date: str, db_path: Optional[str] = None) -> int:
    """Identify stocks with significant daily moves (≥±MOVER_THRESHOLD%)."""
    conn = init_db(db_path)
    nd = _norm_date(date)

    df = conn.execute("""
        SELECT p.date, p.code, s.name, p.change_pct, p.volume, p.close,
               s.market, s.sector, s.industry
        FROM tw_daily_prices p
        JOIN tw_listed_stocks s ON p.code = s.code
        WHERE p.date = ? AND ABS(p.change_pct) >= ?
        ORDER BY ABS(p.change_pct) DESC
    """, [nd, MOVER_THRESHOLD]).df()

    if df.empty:
        logger.info(f"No significant movers for {nd}")
        conn.close()
        return 0

    count = upsert_significant_movers(conn, df)
    logger.info(f"Significant movers for {nd}: {count}")
    conn.close()
    return count


# ═══════════════════════════════════════════════════════════════
#  LLM Tagging (optional)
# ═══════════════════════════════════════════════════════════════

def _needs_llm() -> bool:
    from tw_stock.config import ANTHROPIC_API_KEY, DEEPSEEK_API_KEY
    return bool(ANTHROPIC_API_KEY or DEEPSEEK_API_KEY)


def tag_significant_movers(df_movers: pd.DataFrame) -> List[Dict[str, str]]:
    """Use LLM to tag significant movers with reason explanations."""
    from tw_stock.config import ANTHROPIC_API_KEY, DEEPSEEK_API_KEY

    api_key = ANTHROPIC_API_KEY or DEEPSEEK_API_KEY
    if not api_key or df_movers.empty:
        return []

    # Build a prompt with the day's movers
    stocks_str = []
    for _, row in df_movers.head(30).iterrows():
        stocks_str.append(
            f"{row['code']} {row['name']} ({row.get('market', '')}): "
            f"{row['change_pct']:+.1f}%, vol={row.get('volume', 0)}"
        )

    prompt = (
        "以下是台灣股市今日漲跌幅超過5%的股票列表，請為每檔股票簡要說明可能的漲跌原因（15字以內）：\n\n"
        + "\n".join(stocks_str)
        + "\n\n請以JSON格式回覆，格式為：[{\"code\": \"股票代碼\", \"reasons\": \"原因\"}, ...]"
    )

    provider = "anthropic" if ANTHROPIC_API_KEY else "deepseek"
    try:
        if provider == "anthropic":
            import anthropic
            client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
            resp = client.messages.create(
                model="claude-3-haiku-20240307",
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            text = resp.content[0].text
        else:
            import openai
            client = openai.OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
            resp = client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1024,
            )
            text = resp.choices[0].message.content

        # Parse JSON from response
        import re
        json_match = re.search(r"\[[\s\S]*\]", text)
        if json_match:
            return json.loads(json_match[0])
    except Exception as e:
        logger.error(f"LLM tagging failed: {e}")

    return []


def generate_market_narratives(df_movers: pd.DataFrame) -> List[Dict[str, Any]]:
    """Use LLM to generate market narratives from the day's movers."""
    from tw_stock.config import ANTHROPIC_API_KEY, DEEPSEEK_API_KEY

    api_key = ANTHROPIC_API_KEY or DEEPSEEK_API_KEY
    if not api_key or df_movers.empty:
        return []

    provider = "anthropic" if ANTHROPIC_API_KEY else "deepseek"
    try:
        if provider == "anthropic":
            import anthropic
            client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        else:
            import openai
            client = openai.OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")

        # Group by industry for narrative generation
        industries = df_movers.groupby("industry")
        industry_summaries = []
        for ind, group in industries:
            codes = group["code"].tolist()[:5]
            industry_summaries.append(f"{ind}: {', '.join(codes)}")

        prompt = (
            "以下是今日台灣股市各產業顯著波動股票：\n"
            + "\n".join(industry_summaries)
            + "\n\n請以JSON格式輸出3-5個今日市場主題，格式為："
              "[{\"tag\": \"主題標籤\", \"name\": \"主題名稱\", "
              "\"description\": \"簡要描述(50字)\", \"stocks\": [\"代碼1\", \"代碼2\"]}]"
        )

        if provider == "anthropic":
            resp = client.messages.create(
                model="claude-3-haiku-20240307",
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            text = resp.content[0].text
        else:
            resp = client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1024,
            )
            text = resp.choices[0].message.content

        import re
        json_match = re.search(r"\[[\s\S]*\]", text)
        if json_match:
            return json.loads(json_match[0])
    except Exception as e:
        logger.error(f"LLM narrative generation failed: {e}")

    return []


import json
import re


# ═══════════════════════════════════════════════════════════════
#  Orchestrator
# ═══════════════════════════════════════════════════════════════

def fetch_daily(date: str, use_llm: bool = True, db_path: Optional[str] = None) -> Dict[str, Any]:
    """Fetch all data for a single trading day."""
    summary = {
        "date": date,
        "prices": 0,
        "movers": 0,
        "tagged": 0,
        "narratives": 0,
        "errors": [],
    }

    conn = init_db(db_path)

    try:
        # 1. Daily prices
        summary["prices"] = fetch_daily_prices(date, db_path)

        # 2. Indices
        fetch_indices(date, db_path)

        # 3. Identify significant movers
        summary["movers"] = fetch_significant_movers(date, db_path)

        # 4. LLM tagging
        if use_llm and _needs_llm():
            try:
                df_movers = pd.DataFrame()
                try:
                    from tw_stock.storage import get_daily_movers
                    df_movers = get_daily_movers(conn, date)
                except Exception:
                    pass

                if not df_movers.empty:
                    reasons = tag_significant_movers(df_movers)
                    if reasons:
                        summary["tagged"] = upsert_stock_reasons(conn, date, reasons)
                        logger.info(f"[{date}] {len(reasons)} Taiwan stocks tagged")

                    narratives = generate_market_narratives(df_movers)
                    if narratives:
                        summary["narratives"] = upsert_daily_narratives(conn, date, narratives)
                        logger.info(f"[{date}] {len(narratives)} Taiwan market narratives generated")
            except Exception as e:
                err = f"LLM tagging failed: {e}"
                summary["errors"].append(err)
                logger.error(err)
        elif use_llm:
            logger.info(f"[{date}] LLM tagging skipped — no API key configured")

    finally:
        conn.close()

    # Log the fetch
    conn2 = init_db(db_path)
    try:
        log_fetch(conn2, date, "success",
                   listings_count=0,
                   prices_count=summary["prices"],
                   movers_count=summary["movers"],
                   tagged=summary["tagged"],
                   narratives=summary["narratives"],
                   errors="; ".join(summary["errors"]))
    finally:
        conn2.close()

    return summary


def fetch_latest(use_llm: bool = True, db_path: Optional[str] = None) -> Dict[str, Any]:
    """Fetch the most recent trading day's data."""
    today = datetime.now()
    # Taiwan market is Mon-Fri, closes at 13:30 TWT (05:30 UTC)
    # Try last 10 days to find a trading day
    for offset in range(10):
        d = (today - timedelta(days=offset)).strftime("%Y-%m-%d")
        try:
            resp = requests.get(f"{TWSE_OPENAPI}/exchangeReport/STOCK_DAY_ALL", timeout=10)
            if resp.status_code == 200 and resp.json():
                logger.info(f"Latest trading day: {d}")
                return fetch_daily(d, use_llm=use_llm, db_path=db_path)
        except Exception:
            continue

    return {"error": "No trading data found in last 10 days"}


def fetch_batch(dates: List[str], use_llm: bool = True, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Fetch multiple dates."""
    results = []
    for d in dates:
        results.append(fetch_daily(d, use_llm=use_llm, db_path=db_path))
        time.sleep(0.5)
    return results


def init_pipeline(db_path: Optional[str] = None) -> Dict[str, Any]:
    """Full initialization: listings + index history + recent prices."""
    result = {}

    # 1. Listings
    logger.info("=== Fetching all Taiwan stock listings ===")
    result["listings"] = fetch_listings(db_path)

    # 2. Index history (backfill 3 months)
    logger.info("=== Fetching index history ===")
    result["indices"] = fetch_indices(db_path=db_path)

    # 3. Recent 5 trading days
    logger.info("=== Fetching recent prices ===")
    conn = init_db(db_path)
    try:
        stocks = get_listed_stocks(conn, active_only=True, limit=10000)
        tickers = stocks["code"].tolist()
    finally:
        conn.close()

    today = datetime.now()
    recent_dates = []
    for offset in range(10):
        d = (today - timedelta(days=offset)).strftime("%Y-%m-%d")
        recent_dates.append(d)

    for d in reversed(recent_dates):
        try:
            resp = requests.get(f"{TWSE_OPENAPI}/exchangeReport/STOCK_DAY_ALL", timeout=10)
            if resp.status_code == 200 and resp.json():
                count = fetch_daily_prices(d, db_path=db_path, tickers=tickers)
                if count > 0:
                    result.setdefault("prices_dates", []).append(d)
                    result.setdefault("prices", 0)
                    result["prices"] += count
        except Exception:
            continue

    result["total_stocks"] = sum(result["listings"].values())
    return result


# ═══════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

    use_llm = "--no-llm" not in sys.argv

    if "--init" in sys.argv:
        result = init_pipeline()
        print(f"\nInit result: {result}")
    elif "--date" in sys.argv:
        idx = sys.argv.index("--date")
        date = sys.argv[idx + 1]
        result = fetch_daily(date, use_llm=use_llm)
        print(f"\nResult: {result}")
    elif "--all" in sys.argv:
        today = datetime.now()
        dates = [(today - timedelta(days=i)).strftime("%Y%m%d") for i in range(5)]
        results = fetch_batch(dates, use_llm=use_llm)
        for r in results:
            print(f"  {r}")
    else:
        result = fetch_latest(use_llm=use_llm)
        print(f"\nResult: {result}")
