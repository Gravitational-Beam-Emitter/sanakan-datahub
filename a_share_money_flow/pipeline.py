"""
Pipeline — Auction rush + fund flow data fetching via AKShare.

Sources:
  - Pre-market auction: ak.stock_zh_a_spot() captured at ~9:28
  - Sector fund flow: ak.stock_sector_fund_flow_rank() at ~14:55
  - Stock fund flow: ak.stock_individual_fund_flow_rank() at ~14:55

Usage:
  python3 -m a_share_money_flow.pipeline              # fetch all
  python3 -m a_share_money_flow.pipeline --source auction   # auction only
  python3 -m a_share_money_flow.pipeline --source fund-flow # fund flow only
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd

from a_share_money_flow.config import (
    AKSHARE_RATE_LIMIT,
    AUCTION_MIN_GAP_PCT,
    AUCTION_MIN_RUSH_SCORE,
    FUND_FLOW_MIN_AMOUNT,
)
from a_share_money_flow.scoring import (
    compute_rush_score,
    compute_percentile,
    aggregate_auction_sectors,
)
from a_share_money_flow.storage import (
    init_db,
    upsert_auction_stocks,
    upsert_auction_sectors,
    upsert_fund_flow_stocks,
    upsert_fund_flow_sectors,
    log_fetch_start,
    log_fetch_end,
    get_stats,
)

logger = logging.getLogger("a_share_money_flow.pipeline")

TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ═══════════════════════════════════════════════════════════════
# Auction Fetcher
# ═══════════════════════════════════════════════════════════════

def _fetch_spot_safe():
    """Call ak.stock_zh_a_spot() with error handling. Returns DataFrame or empty."""
    try:
        import akshare as ak
        df = ak.stock_zh_a_spot()
        logger.info(f"stock_zh_a_spot returned {len(df)} rows")
        return df
    except ImportError:
        logger.error("akshare not installed. pip install akshare")
        return pd.DataFrame()
    except Exception as e:
        logger.error(f"stock_zh_a_spot failed: {e}")
        return pd.DataFrame()


def fetch_auction(conn) -> Dict[str, Any]:
    """Fetch pre-market auction data and compute rush scores.

    Captures the A-share spot snapshot ASAP after 9:25 call auction.
    Extracts opening price/volume as auction signals.
    """
    log_id = log_fetch_start(conn, "auction")
    try:
        df = _fetch_spot_safe()
        if df.empty:
            log_fetch_end(conn, log_id, error="ak.stock_zh_a_spot() returned empty")
            return {"source": "auction", "status": "error", "reason": "no data from akshare"}

        # AKShare columns (Chinese) → our fields
        col_map = {
            "代码": "code",
            "名称": "name",
            "今开": "open_price",
            "昨收": "prev_close",
            "成交量": "volume",
            "成交额": "amount",
            "换手率": "turnover",
        }

        # Normalize and extract
        records = []
        volumes = []
        turnovers = []

        for _, row in df.iterrows():
            code = str(row.get("代码", ""))
            if not code:
                continue

            open_price = _safe_float(row.get("今开"))
            prev_close = _safe_float(row.get("昨收"))
            volume = _safe_int(row.get("成交量"))
            amount = _safe_float(row.get("成交额"))
            turnover = _safe_float(row.get("换手率"))

            if prev_close and prev_close > 0 and open_price and open_price > 0:
                gap_pct = (open_price - prev_close) / prev_close * 100
            else:
                gap_pct = 0.0

            volumes.append(float(volume or 0))
            turnovers.append(turnover or 0.0)
            records.append({
                "code": code,
                "name": str(row.get("名称", "")),
                "open_price": open_price,
                "prev_close": prev_close,
                "gap_pct": round(gap_pct, 2),
                "volume": volume,
                "amount": amount,
                "turnover": turnover,
            })

        if not records:
            log_fetch_end(conn, log_id, error="no valid records parsed")
            return {"source": "auction", "status": "error", "reason": "no valid records"}

        # Compute percentiles
        vol_pctiles = compute_percentile([r["volume"] for r in records])
        turnover_pctiles = compute_percentile([r["turnover"] or 0 for r in records])

        # Compute rush scores
        for i, r in enumerate(records):
            r["rush_score"] = compute_rush_score(
                gap_pct=r["gap_pct"],
                turnover_pct=r["turnover"] or 0,
                volume_rank_pctile=vol_pctiles[i],
            )
            r["sector"] = ""  # will be enriched later
            r["date"] = TODAY

        # Count how many were stored
        stored = upsert_auction_stocks(conn, records)
        logger.info(f"Auction: {stored} stocks stored, rush_score range {min(r['rush_score'] for r in records):.0f}-{max(r['rush_score'] for r in records):.0f}")

        # Aggregate sectors
        sector_records = aggregate_auction_sectors(records)
        for sr in sector_records:
            sr["date"] = TODAY
        sec_stored = upsert_auction_sectors(conn, sector_records)
        logger.info(f"Auction sectors: {sec_stored} sectors stored")

        rush_count = sum(1 for r in records if r["rush_score"] >= AUCTION_MIN_RUSH_SCORE)
        log_fetch_end(conn, log_id, items_checked=len(records), new_items=stored)

        return {
            "source": "auction",
            "status": "ok",
            "stocks_scanned": len(records),
            "stocks_stored": stored,
            "rush_candidates": rush_count,
            "sectors_stored": sec_stored,
        }

    except Exception as e:
        logger.error(f"fetch_auction failed: {e}")
        log_fetch_end(conn, log_id, error=str(e))
        return {"source": "auction", "status": "error", "reason": str(e)}


# ═══════════════════════════════════════════════════════════════
# Fund Flow Fetcher
# ═══════════════════════════════════════════════════════════════

def _fetch_sector_fund_flow_safe(indicator: str, sector_type: str):
    """Call ak.stock_sector_fund_flow_rank() with error handling."""
    try:
        import akshare as ak
        df = ak.stock_sector_fund_flow_rank(indicator=indicator, sector_type=sector_type)
        logger.info(f"sector_fund_flow_rank({indicator}, {sector_type}) returned {len(df)} rows")
        return df
    except ImportError:
        logger.error("akshare not installed")
        return pd.DataFrame()
    except Exception as e:
        logger.error(f"sector_fund_flow_rank failed: {e}")
        return pd.DataFrame()


def _fetch_stock_fund_flow_safe(indicator: str):
    """Call ak.stock_individual_fund_flow_rank() with error handling."""
    try:
        import akshare as ak
        df = ak.stock_individual_fund_flow_rank(indicator=indicator)
        logger.info(f"individual_fund_flow_rank({indicator}) returned {len(df)} rows")
        return df
    except ImportError:
        logger.error("akshare not installed")
        return pd.DataFrame()
    except Exception as e:
        logger.error(f"individual_fund_flow_rank failed: {e}")
        return pd.DataFrame()


def _parse_fund_flow_row(row, date: str, is_sector: bool = False) -> Dict[str, Any]:
    """Parse a single row from fund flow rank DataFrames to our schema."""
    if is_sector:
        record = {
            "date": date,
            "sector_type": "",
            "sector_name": str(row.get("板块名称", "")),
            "change_pct": _safe_float(row.get("涨跌幅")),
            "top_stock": str(row.get("主力净流入最大股", "")),
        }
    else:
        record = {
            "date": date,
            "code": str(row.get("代码", "")),
            "name": str(row.get("名称", "")),
            "sector": "",
            "latest_price": _safe_float(row.get("最新价")),
            "change_pct": _safe_float(row.get("涨跌幅")),
        }

    # Common fund flow fields with Chinese column names
    record.update({
        "main_inflow": _safe_float(row.get("主力净流入-净额")),
        "main_inflow_pct": _safe_float(row.get("主力净流入-净占比")),
        "super_large_inflow": _safe_float(row.get("超大单净流入-净额")),
        "super_large_inflow_pct": _safe_float(row.get("超大单净流入-净占比")),
        "large_inflow": _safe_float(row.get("大单净流入-净额")),
        "large_inflow_pct": _safe_float(row.get("大单净流入-净占比")),
        "medium_inflow": _safe_float(row.get("中单净流入-净额")),
        "medium_inflow_pct": _safe_float(row.get("中单净流入-净占比")),
        "small_inflow": _safe_float(row.get("小单净流入-净额")),
        "small_inflow_pct": _safe_float(row.get("小单净流入-净占比")),
    })
    return record


def fetch_fund_flow(conn) -> Dict[str, Any]:
    """Fetch sector and stock fund flow rankings."""
    log_id = log_fetch_start(conn, "fund_flow")
    results = {}

    try:
        # ── Sector fund flows ──
        sector_records = []
        for sec_type in ["行业资金流", "概念资金流"]:
            df = _fetch_sector_fund_flow_safe(indicator="今日", sector_type=sec_type)
            if df.empty:
                continue
            for _, row in df.iterrows():
                rec = _parse_fund_flow_row(row, TODAY, is_sector=True)
                rec["sector_type"] = sec_type
                sector_records.append(rec)
            time.sleep(AKSHARE_RATE_LIMIT)

        if sector_records:
            sec_stored = upsert_fund_flow_sectors(conn, sector_records)
            results["sectors_stored"] = sec_stored
            logger.info(f"Fund flow sectors: {sec_stored} records ({len(sector_records)} parsed)")
        else:
            results["sectors_stored"] = 0

        # ── Stock fund flows ──
        stock_df = _fetch_stock_fund_flow_safe(indicator="今日")
        stock_records = []
        for _, row in stock_df.iterrows():
            rec = _parse_fund_flow_row(row, TODAY, is_sector=False)
            if rec["code"]:
                stock_records.append(rec)

        if stock_records:
            stored = upsert_fund_flow_stocks(conn, stock_records)
            results["stocks_stored"] = stored
            logger.info(f"Fund flow stocks: {stored} records ({len(stock_records)} parsed)")

            # Top inflow/outflow counts
            inflows = sum(1 for r in stock_records if (r.get("main_inflow") or 0) > 0)
            outflows = len(stock_records) - inflows
            results["inflow_stocks"] = inflows
            results["outflow_stocks"] = outflows
        else:
            results["stocks_stored"] = 0

        log_fetch_end(conn, log_id,
                      items_checked=len(sector_records) + len(stock_records),
                      new_items=results.get("sectors_stored", 0) + results.get("stocks_stored", 0))

        results["source"] = "fund_flow"
        results["status"] = "ok"
        return results

    except Exception as e:
        logger.error(f"fetch_fund_flow failed: {e}")
        log_fetch_end(conn, log_id, error=str(e))
        return {"source": "fund_flow", "status": "error", "reason": str(e)}


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════

def _safe_float(val) -> Optional[float]:
    """Convert value to float, return None on failure."""
    if val is None:
        return None
    try:
        f = float(val)
        return f if pd.notna(f) else None
    except (ValueError, TypeError):
        return None


def _safe_int(val) -> Optional[int]:
    """Convert value to int, return None on failure."""
    if val is None:
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


# ═══════════════════════════════════════════════════════════════
# Orchestrator
# ═══════════════════════════════════════════════════════════════

def fetch_daily(source: Optional[str] = None) -> Dict[str, Any]:
    """Daily fetch: auction + fund flow.

    Args:
        source: "auction", "fund_flow", or None for both.
    """
    conn = init_db()
    results = {}

    try:
        if not source or source == "auction":
            results["auction"] = fetch_auction(conn)

        if not source or source == "fund_flow":
            time.sleep(AKSHARE_RATE_LIMIT)
            results["fund_flow"] = fetch_fund_flow(conn)

        status = "ok"
        for r in results.values():
            if r.get("status") == "error":
                status = "partial"
                break

        stats = get_stats(conn)
        return {"status": status, "date": TODAY, "results": results, "stats": stats}

    except Exception as e:
        logger.error(f"fetch_daily failed: {e}")
        return {"status": "error", "date": TODAY, "error": str(e)}
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="A-Share Money Flow Pipeline")
    parser.add_argument("--source", type=str, default=None,
                        choices=["auction", "fund_flow"],
                        help="Run single source only")
    args = parser.parse_args()

    result = fetch_daily(source=args.source)
    print(json.dumps(result, indent=2, default=str))
