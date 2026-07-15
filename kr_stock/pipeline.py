"""
Data pipeline — fetch Korean stock data from FinanceDataReader + DART + yfinance.

Usage:
    python -m kr_stock.pipeline                # fetch latest trading day
    python -m kr_stock.pipeline --init          # full init: listings + backfill + indices
    python -m kr_stock.pipeline --date 20260619 # fetch specific date
    python -m kr_stock.pipeline --no-llm        # skip LLM tagging
    python -m kr_stock.pipeline --all           # fetch last 5 trading days
"""

from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pandas as pd

from kr_stock.storage import (
    init_db,
    _norm_date,
    upsert_listed_stocks,
    upsert_daily_prices,
    upsert_market_indices,
    upsert_significant_movers,
    upsert_stock_reasons,
    upsert_daily_narratives,
    upsert_dart_filings,
    upsert_foreign_flows,
    upsert_stock_metrics,
    upsert_stock_financials,
    upsert_analyst_data,
    log_fetch,
    get_listed_stocks,
    get_counts,
)
from kr_stock.config import DART_API_KEY

logger = logging.getLogger("kr_stock.pipeline")

# Significant move threshold (Korean market: ±30% is limit, ±10% is notable)
MOVER_THRESHOLD = 10.0


# ═══════════════════════════════════════════════════════════════
#  Listing fetch
# ═══════════════════════════════════════════════════════════════

def fetch_listings(db_path: Optional[str] = None) -> Dict[str, int]:
    """Fetch all KOSPI/KOSDAQ/KONEX listings and store them."""
    import FinanceDataReader as fdr

    conn = init_db(db_path)
    counts = {}

    for market in ["KOSPI", "KOSDAQ", "KONEX"]:
        try:
            df = fdr.StockListing(market)
            if df.empty:
                logger.warning(f"{market}: no data")
                counts[market] = 0
                continue

            df = df.rename(columns={
                "Code": "code", "Name": "name", "Market": "market",
                "Dept": "sector", "Marcap": "market_cap",
                "Stocks": "shares_outstanding", "MarketId": "market_id",
            })
            # Sector from Dept if available; industry may come from tagging later
            count = upsert_listed_stocks(conn, df)
            counts[market] = count
            logger.info(f"{market}: {count} stocks upserted")
        except Exception as e:
            logger.error(f"{market} listing fetch failed: {e}")
            counts[market] = 0

    conn.close()
    return counts


# ═══════════════════════════════════════════════════════════════
#  Price fetch
# ═══════════════════════════════════════════════════════════════

def _to_date_str(d: Any) -> str:
    """Convert Timestamp/date/datetime to YYYY-MM-DD string."""
    if hasattr(d, "strftime"):
        return d.strftime("%Y-%m-%d")
    return str(d)


def fetch_daily_prices(date: str, db_path: Optional[str] = None,
                        tickers: Optional[List[str]] = None) -> int:
    """Fetch daily OHLCV for a date. If tickers is None, fetch all active stocks.

    Uses FinanceDataReader. Falls back to yfinance if needed.
    """
    import FinanceDataReader as fdr

    if tickers is None:
        conn_ro = init_db(db_path, read_only=True)
        try:
            df_stocks = get_listed_stocks(conn_ro, active_only=True, limit=10000)
            tickers = df_stocks["code"].tolist()
        finally:
            conn_ro.close()

    if not tickers:
        logger.warning("No tickers to fetch prices for")
        return 0

    norm_date = date.replace("-", "")[:8]
    rows = []
    success = 0
    fail = 0
    total_count = 0
    batch_size = 200

    for code in tickers:
        try:
            df = fdr.DataReader(code, norm_date, norm_date)
            if df.empty:
                continue
            # Index is Date, columns: Open, High, Low, Close, Volume, Change
            for idx, row in df.iterrows():
                rows.append({
                    "date": _to_date_str(idx),
                    "code": code,
                    "open": float(row.get("Open", 0)) if pd.notna(row.get("Open")) else None,
                    "high": float(row.get("High", 0)) if pd.notna(row.get("High")) else None,
                    "low": float(row.get("Low", 0)) if pd.notna(row.get("Low")) else None,
                    "close": float(row.get("Close", 0)) if pd.notna(row.get("Close")) else None,
                    "volume": int(row.get("Volume", 0)) if pd.notna(row.get("Volume")) else None,
                    "change_pct": float(row.get("Change", 0)) * 100 if pd.notna(row.get("Change")) else None,
                })
            success += 1
        except Exception:
            fail += 1
            continue

        # Flush batch to DB and release lock
        if len(rows) >= batch_size:
            conn = init_db(db_path)
            try:
                df_prices = pd.DataFrame(rows)
                total_count += upsert_daily_prices(conn, df_prices)
            finally:
                conn.close()
            rows.clear()
            logger.info(f"Prices for {_to_date_str(date)}: {success} stocks ok, {fail} failed so far ({total_count} records flushed)")

    if rows:
        conn = init_db(db_path)
        try:
            df_prices = pd.DataFrame(rows)
            total_count += upsert_daily_prices(conn, df_prices)
        finally:
            conn.close()

    if total_count > 0:
        logger.info(f"Prices for {_to_date_str(date)}: {total_count} records total ({success} stocks ok, {fail} failed)")
    else:
        logger.warning(f"No price data for {date}")

    return total_count


def fetch_indices(date: str = None, db_path: Optional[str] = None) -> int:
    """Fetch KOSPI (KS11) and KOSDAQ (KQ11) index data.

    If date is None, fetches last ~60 days of data.
    """
    import FinanceDataReader as fdr

    conn = init_db(db_path)

    indices = {
        "KS11": "KOSPI",
        "KQ11": "KOSDAQ",
    }

    rows = []
    for idx_code, idx_name in indices.items():
        try:
            if date:
                nd = date.replace("-", "")[:8]
                df = fdr.DataReader(idx_code, nd, nd)
            else:
                df = fdr.DataReader(idx_code)

            if df.empty:
                continue

            for idx, row in df.iterrows():
                rows.append({
                    "date": _to_date_str(idx),
                    "index_code": idx_code,
                    "index_name": idx_name,
                    "open": float(row.get("Open", 0)) if pd.notna(row.get("Open")) else None,
                    "high": float(row.get("High", 0)) if pd.notna(row.get("High")) else None,
                    "low": float(row.get("Low", 0)) if pd.notna(row.get("Low")) else None,
                    "close": float(row.get("Close", 0)) if pd.notna(row.get("Close")) else None,
                    "volume": float(row.get("Volume", 0)) if pd.notna(row.get("Volume")) else None,
                    "change_pct": float(row.get("Change", 0)) * 100 if "Change" in row and pd.notna(row.get("Change")) else None,
                })
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
    """Identify stocks with significant daily moves (≥±MOVAR_THRESHOLD%)."""
    conn = init_db(db_path)
    norm_date = date.replace("-", "")[:8]
    nd = f"{norm_date[:4]}-{norm_date[4:6]}-{norm_date[6:8]}"

    # Join prices with listing info for sector/industry
    df = conn.execute("""
        SELECT p.date, p.code, s.name, p.change_pct, p.volume, p.close,
               s.market, s.sector, s.industry
        FROM kr_daily_prices p
        JOIN kr_listed_stocks s ON p.code = s.code
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


def fetch_foreign_flows(date: str = None, db_path: Optional[str] = None) -> int:
    """Estimate foreign/institutional flows from KRX data via FinanceDataReader.

    Note: FinanceDataReader doesn't directly expose flows, so this attempts
    to pull investor trading data if available. Falls back to empty record.
    """
    import FinanceDataReader as fdr

    conn = init_db(db_path)

    # Try to get investor trading data from KRX
    rows = []
    for market in ["KOSPI", "KOSDAQ"]:
        try:
            df = fdr.DataReader("KS11" if market == "KOSPI" else "KQ11")
            if df.empty:
                continue
            # Use the latest data point as approximation
            latest = df.tail(1)
            for idx, row in latest.iterrows():
                rows.append({
                    "date": _to_date_str(idx),
                    "market": market,
                    "foreign_net_buy": None,
                    "institution_net_buy": None,
                    "individual_net_buy": None,
                })
        except Exception:
            pass

    count = 0
    if rows:
        df_flows = pd.DataFrame(rows)
        count = upsert_foreign_flows(conn, df_flows)

    conn.close()
    return count


# ═══════════════════════════════════════════════════════════════
#  yfinance fundamentals (valuation, financials, analyst)
# ═══════════════════════════════════════════════════════════════

def _safe_float(val: Any) -> Optional[float]:
    """Convert a value to float, returning None if not possible."""
    if val is None:
        return None
    try:
        f = float(val)
        return None if pd.isna(f) else f
    except (ValueError, TypeError):
        return None


def _get_today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def fetch_stock_metrics(codes: List[str], db_path: Optional[str] = None) -> int:
    """Fetch valuation metrics from yfinance.info for a list of codes.

    Only fetches stocks that have yfinance tickers (.KS for KOSPI, .KQ for KOSDAQ).
    """
    import yfinance as yf

    if not codes:
        return 0

    conn = init_db(db_path)
    today = _get_today_str()
    rows = []

    for code in codes:
        try:
            ticker_suffix = ".KQ" if code.startswith("1") or code.startswith("2") else ".KS"
            ticker = yf.Ticker(f"{code}{ticker_suffix}")
            info = ticker.info
            if not info or "symbol" not in info:
                continue

            rows.append({
                "code": code,
                "date": today,
                "market_cap": _safe_float(info.get("marketCap")),
                "enterprise_value": _safe_float(info.get("enterpriseValue")),
                "pe_trailing": _safe_float(info.get("trailingPE")),
                "pe_forward": _safe_float(info.get("forwardPE")),
                "pb_ratio": _safe_float(info.get("priceToBook")),
                "ps_ratio": _safe_float(info.get("priceToSalesTrailing12Months")),
                "dividend_yield": _safe_float(info.get("dividendYield")),
                "payout_ratio": _safe_float(info.get("payoutRatio")),
                "beta": _safe_float(info.get("beta")),
                "roa": _safe_float(info.get("returnOnAssets")),
                "roe": _safe_float(info.get("returnOnEquity")),
                "gross_margin": _safe_float(info.get("grossMargins")),
                "ebitda_margin": _safe_float(info.get("ebitdaMargins")),
                "operating_margin": _safe_float(info.get("operatingMargins")),
                "revenue_growth": _safe_float(info.get("revenueGrowth")),
                "earnings_growth": _safe_float(info.get("earningsGrowth")),
                "free_cashflow": _safe_float(info.get("freeCashflow")),
                "operating_cashflow": _safe_float(info.get("operatingCashflow")),
                "inst_holding_pct": _safe_float(info.get("heldPercentInstitutions")),
                "insider_holding_pct": _safe_float(info.get("heldPercentInsiders")),
                "shares_outstanding": _safe_float(info.get("sharesOutstanding")),
                "float_shares": _safe_float(info.get("floatShares")),
                "ma_50": _safe_float(info.get("fiftyDayAverage")),
                "ma_200": _safe_float(info.get("twoHundredDayAverage")),
                "high_52w": _safe_float(info.get("fiftyTwoWeekHigh")),
                "low_52w": _safe_float(info.get("fiftyTwoWeekLow")),
            })
        except Exception as e:
            logger.debug(f"Metrics fetch failed for {code}: {e}")
            continue

    count = 0
    if rows:
        df = pd.DataFrame(rows)
        count = upsert_stock_metrics(conn, df)
        logger.info(f"Stock metrics: {count} records for {len(rows)} stocks")

    conn.close()
    return count


def fetch_stock_financials(codes: List[str], db_path: Optional[str] = None) -> int:
    """Fetch quarterly financials (BS, IS, CF) from yfinance for a list of codes."""
    import yfinance as yf

    if not codes:
        return 0

    conn = init_db(db_path)
    rows = []

    for code in codes:
        try:
            ticker_suffix = ".KQ" if code.startswith("1") or code.startswith("2") else ".KS"
            ticker = yf.Ticker(f"{code}{ticker_suffix}")

            # Balance Sheet
            try:
                bs = ticker.quarterly_balance_sheet
                if bs is not None and not bs.empty:
                    for col_date, col_data in bs.items():
                        date_str = col_date.strftime("%Y-%m-%d") if hasattr(col_date, "strftime") else str(col_date)
                        for metric_name, val in col_data.items():
                            v = _safe_float(val)
                            if v is not None:
                                rows.append({"code": code, "date": date_str, "statement_type": "BS",
                                            "metric_name": str(metric_name), "value": v})
            except Exception:
                pass

            # Income Statement
            try:
                inc = ticker.quarterly_income_stmt
                if inc is not None and not inc.empty:
                    for col_date, col_data in inc.items():
                        date_str = col_date.strftime("%Y-%m-%d") if hasattr(col_date, "strftime") else str(col_date)
                        for metric_name, val in col_data.items():
                            v = _safe_float(val)
                            if v is not None:
                                rows.append({"code": code, "date": date_str, "statement_type": "IS",
                                            "metric_name": str(metric_name), "value": v})
            except Exception:
                pass

            # Cash Flow
            try:
                cf = ticker.quarterly_cashflow
                if cf is not None and not cf.empty:
                    for col_date, col_data in cf.items():
                        date_str = col_date.strftime("%Y-%m-%d") if hasattr(col_date, "strftime") else str(col_date)
                        for metric_name, val in col_data.items():
                            v = _safe_float(val)
                            if v is not None:
                                rows.append({"code": code, "date": date_str, "statement_type": "CF",
                                            "metric_name": str(metric_name), "value": v})
            except Exception:
                pass

        except Exception as e:
            logger.debug(f"Financials fetch failed for {code}: {e}")
            continue

    count = 0
    if rows:
        df = pd.DataFrame(rows)
        count = upsert_stock_financials(conn, df)
        logger.info(f"Stock financials: {count} records for {len(set(r['code'] for r in rows))} stocks")

    conn.close()
    return count


def fetch_analyst_data(codes: List[str], db_path: Optional[str] = None) -> int:
    """Fetch analyst consensus from yfinance for a list of codes."""
    import yfinance as yf

    if not codes:
        return 0

    conn = init_db(db_path)
    today = _get_today_str()
    rows = []

    for code in codes:
        try:
            ticker_suffix = ".KQ" if code.startswith("1") or code.startswith("2") else ".KS"
            ticker = yf.Ticker(f"{code}{ticker_suffix}")

            row = {"code": code, "date": today}

            # Analyst price targets
            try:
                targets = ticker.analyst_price_targets
                if targets:
                    row["target_mean"] = _safe_float(targets.get("mean"))
                    row["target_high"] = _safe_float(targets.get("high"))
                    row["target_low"] = _safe_float(targets.get("low"))
                    row["target_median"] = _safe_float(targets.get("median"))
            except Exception:
                pass

            # Recommendations
            try:
                recs = ticker.recommendations
                if recs is not None and not recs.empty:
                    latest = recs.iloc[-1]
                    row["recommendation"] = str(latest.get("period", "")) if "period" in latest.index else None
            except Exception:
                pass

            # Number of analysts
            try:
                info = ticker.info
                if info:
                    row["num_analysts"] = info.get("numberOfAnalystOpinions")
            except Exception:
                pass

            # Earnings estimate
            try:
                est = ticker.earnings_estimate
                if est is not None and not est.empty:
                    avg_est = est.iloc[0]
                    row["earnings_estimate_avg"] = _safe_float(avg_est.get("avg")) if "avg" in avg_est.index else None
                    row["revenue_estimate_avg"] = _safe_float(avg_est.get("avg")) if "avg" in avg_est.index else None
            except Exception:
                pass

            # EPS trend
            try:
                eps = ticker.eps_trend
                if eps is not None and not eps.empty:
                    latest_eps = eps.iloc[0]
                    row["eps_trend_current"] = _safe_float(latest_eps.get("current")) if "current" in latest_eps.index else None
                    row["eps_trend_7d_ago"] = _safe_float(latest_eps.get("7daysAgo")) if "7daysAgo" in latest_eps.index else None
                    row["eps_trend_30d_ago"] = _safe_float(latest_eps.get("30daysAgo")) if "30daysAgo" in latest_eps.index else None
            except Exception:
                pass

            # Only add if we got at least something beyond code+date
            if len(row) > 2:
                rows.append(row)
        except Exception as e:
            logger.debug(f"Analyst fetch failed for {code}: {e}")
            continue

    count = 0
    if rows:
        df = pd.DataFrame(rows)
        count = upsert_analyst_data(conn, df)
        logger.info(f"Analyst data: {count} records for {len(rows)} stocks")

    conn.close()
    return count


# ═══════════════════════════════════════════════════════════════
#  DART filings
# ═══════════════════════════════════════════════════════════════

def fetch_dart_filings(date: str = None, db_path: Optional[str] = None) -> int:
    """Fetch recent DART filings via OpenDartReader. Requires DART_API_KEY."""
    if not DART_API_KEY:
        logger.info("DART_API_KEY not set — skipping filings fetch")
        return 0

    try:
        import OpenDartReader
    except ImportError:
        logger.warning("OpenDartReader not installed. Install with: pip install opendartreader")
        return 0

    conn = init_db(db_path)

    try:
        dart = OpenDartReader(DART_API_KEY)
        # Get today or specific date
        target = _to_date_str(date) if date else datetime.now().strftime("%Y-%m-%d")

        # Search recent filings
        df = dart.list(target, target, kind="A")  # A = all types
        if df is None or df.empty:
            # Try a few days back
            dt = datetime.strptime(target, "%Y-%m-%d")
            for offset in range(1, 8):
                d = (dt - timedelta(days=offset)).strftime("%Y-%m-%d")
                df = dart.list(d, d, kind="A")
                if df is not None and not df.empty:
                    break

        if df is not None and not df.empty:
            count = upsert_dart_filings(conn, df)
            logger.info(f"DART filings: {count} records")
        else:
            count = 0
            logger.info("No DART filings found")
    except Exception as e:
        logger.error(f"DART fetch failed: {e}")
        count = 0

    conn.close()
    return count


# ═══════════════════════════════════════════════════════════════
#  Orchestrator
# ═══════════════════════════════════════════════════════════════

def fetch_daily(date: str, use_llm: bool = True, db_path: Optional[str] = None) -> Dict[str, Any]:
    """Fetch all data for a single trading day."""
    from kr_stock.tagging import tag_significant_movers, generate_market_narratives, needs_llm

    summary = {
        "date": date,
        "prices": 0,
        "movers": 0,
        "filings": 0,
        "tagged": 0,
        "narratives": 0,
        "errors": [],
    }

    # 1. Daily prices (only refresh listings periodically)
    summary["prices"] = fetch_daily_prices(date, db_path)

    # 2. Indices
    fetch_indices(date, db_path)

    # 3. Identify significant movers
    summary["movers"] = fetch_significant_movers(date, db_path)

    # 3.5 yfinance fundamentals for mover stocks (valuation, financials, analyst)
    if summary["movers"] > 0:
        try:
            conn_ro = init_db(db_path, read_only=True)
            try:
                mover_codes = conn_ro.execute("""
                    SELECT DISTINCT code FROM kr_significant_movers WHERE date = ?
                """, [_norm_date(date)]).fetchall()
            finally:
                conn_ro.close()
            codes = [r[0] for r in mover_codes]
            if codes:
                logger.info(f"[{date}] Fetching yfinance fundamentals for {len(codes)} mover stocks")
                summary["metrics"] = fetch_stock_metrics(codes, db_path)
                summary["financials"] = fetch_stock_financials(codes, db_path)
                summary["analyst"] = fetch_analyst_data(codes, db_path)
        except Exception as e:
            err = f"yfinance fundamentals failed: {e}"
            summary["errors"].append(err)
            logger.error(err)

    # 4. Foreign flows
    fetch_foreign_flows(date, db_path)

    # 5. DART filings
    summary["filings"] = fetch_dart_filings(date, db_path)

    # 6. LLM tagging
    if use_llm and needs_llm():
        try:
            df_movers = pd.DataFrame()
            try:
                from kr_stock.storage import get_daily_movers
                conn_ro = init_db(db_path, read_only=True)
                try:
                    df_movers = get_daily_movers(conn_ro, date)
                finally:
                    conn_ro.close()
            except Exception:
                pass

            if not df_movers.empty:
                reasons = tag_significant_movers(df_movers)
                if reasons:
                    conn_w = init_db(db_path)
                    try:
                        from kr_stock.storage import upsert_stock_reasons as _upsert_reasons
                        summary["tagged"] = _upsert_reasons(conn_w, date, reasons)
                    finally:
                        conn_w.close()
                    logger.info(f"[{date}] {len(reasons)} Korean stocks tagged")

                narratives = generate_market_narratives(df_movers)
                if narratives:
                    conn_w = init_db(db_path)
                    try:
                        from kr_stock.storage import upsert_daily_narratives as _upsert_narr
                        summary["narratives"] = _upsert_narr(conn_w, date, narratives)
                    finally:
                        conn_w.close()
                    logger.info(f"[{date}] {len(narratives)} Korean market narratives generated")
        except Exception as e:
            err = f"LLM tagging failed: {e}"
            summary["errors"].append(err)
            logger.error(err)
    elif use_llm:
        logger.info(f"[{date}] LLM tagging skipped — no API key configured")

    # Log the fetch
    conn_log = init_db(db_path)
    try:
        log_fetch(conn_log, date, "success",
                   listings_count=0,
                   prices_count=summary["prices"],
                   movers_count=summary["movers"],
                   filings_count=summary["filings"],
                   tagged=summary["tagged"],
                   narratives=summary["narratives"],
                   errors="; ".join(summary["errors"]))
    finally:
        conn_log.close()

    return summary


def fetch_latest(use_llm: bool = True, db_path: Optional[str] = None) -> Dict[str, Any]:
    """Fetch the most recent trading day's data."""
    import FinanceDataReader as fdr

    # Try to get KOSPI data for recent dates
    today = datetime.now()
    for offset in range(10):
        d = (today - timedelta(days=offset)).strftime("%Y-%m-%d")
        try:
            df = fdr.DataReader("KS11", d.replace("-", ""), d.replace("-", ""))
            if not df.empty:
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
    logger.info("=== Fetching all KR stock listings ===")
    result["listings"] = fetch_listings(db_path)

    # 2. Index history (backfill)
    logger.info("=== Fetching index history ===")
    result["indices"] = fetch_indices(db_path=db_path)

    # 3. Recent 5 trading days
    logger.info("=== Fetching recent prices ===")
    conn = init_db(db_path)
    try:
        stocks = get_listed_stocks(conn, active_only=True, limit=10000)
        tickers = stocks["code"].tolist()[:500]  # Start with top 500 by market cap
    finally:
        conn.close()

    today = datetime.now()
    recent_dates = []
    for offset in range(10):
        d = (today - timedelta(days=offset)).strftime("%Y-%m-%d")
        recent_dates.append(d)

    for d in reversed(recent_dates):
        import FinanceDataReader as fdr
        nd = d.replace("-", "")[:8]
        df = fdr.DataReader("KS11", nd, nd)
        if not df.empty:
            count = fetch_daily_prices(d, db_path=db_path, tickers=tickers)
            if count > 0:
                result.setdefault("prices_dates", []).append(d)
                result.setdefault("prices", 0)
                result["prices"] += count

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
