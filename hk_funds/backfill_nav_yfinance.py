"""
Backfill NAV history from Yahoo Finance for all funds with ISINs.

Yahoo Finance supports mutual fund ISIN lookups (e.g., LU0827887357)
and returns historical NAV (Close price) data. This pipeline:
  1. Finds all funds with ISINs (from hk_funds.isin or hk_fund_share_classes)
  2. Downloads full price history via yfinance
  3. Stores NAV records in hk_fund_nav_history

Usage:
    python3 -m hk_funds.backfill_nav_yfinance           # all funds
    python3 -m hk_funds.backfill_nav_yfinance --limit 20 # test with 20
    python3 -m hk_funds.backfill_nav_yfinance --skip-existing  # only new funds
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("hk_funds.backfill_nav_yfinance")


def get_funds_with_isins(conn) -> List[Tuple[int, str, str]]:
    """Get all funds with ISINs. Returns [(fund_id, isin, fund_name), ...]."""
    rows = conn.execute("""
        SELECT DISTINCT f.id, COALESCE(NULLIF(f.isin, ''), sc.isin) as isin,
               f.fund_name_en
        FROM hk_funds f
        LEFT JOIN hk_fund_share_classes sc ON sc.fund_id = f.id
        WHERE COALESCE(NULLIF(f.isin, ''), sc.isin) IS NOT NULL
          AND COALESCE(NULLIF(f.isin, ''), sc.isin) != ''
          AND f.is_active = true
        ORDER BY f.id
    """).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


def get_existing_nav_funds(conn) -> set:
    """Get set of fund_ids that already have NAV data."""
    rows = conn.execute(
        "SELECT DISTINCT fund_id FROM hk_fund_nav_history"
    ).fetchall()
    return {r[0] for r in rows}


def fetch_nav_history(isin: str) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    """Fetch full NAV history for an ISIN from Yahoo Finance.

    Returns ([{nav_date, nav, nav_currency}, ...], error_message).
    """
    import yfinance as yf

    try:
        ticker = yf.Ticker(isin)
        info = ticker.info

        if not info or not info.get("symbol"):
            return None, f"ISIN {isin} not found on Yahoo Finance"

        # Get currency from info
        currency = info.get("currency", "USD")

        # Try periods from longest to shortest (some exchanges only support 1d/5d)
        hist = None
        for period in ["max", "10y", "5y", "1y", "6mo", "3mo", "1mo"]:
            try:
                hist = ticker.history(period=period)
                if hist is not None and len(hist) > 0:
                    break
            except Exception:
                continue

        if hist is None or len(hist) == 0:
            return None, f"No price history for {isin}"

        records = []
        for idx, row in hist.iterrows():
            nav = row.get("Close", None)
            if nav is None or nav <= 0:
                continue
            nav_date = idx.strftime("%Y-%m-%d")
            records.append({
                "nav": round(float(nav), 6),
                "nav_date": nav_date,
                "nav_currency": currency,
                "source": "yfinance",
            })

        if not records:
            return None, f"No valid NAV records for {isin}"

        return records, None

    except Exception as e:
        return None, str(e)


def backfill_fund_nav(
    conn,
    limit: Optional[int] = None,
    skip_existing: bool = False,
    delay: float = 0.5,
) -> Dict[str, int]:
    """Backfill NAV history from Yahoo Finance.

    Args:
        conn: DuckDB connection
        limit: Max funds to process (None = all)
        skip_existing: Skip funds that already have NAV data
        delay: Seconds between requests (be nice to Yahoo)

    Returns:
        {funds_processed, funds_updated, total_nav_records, errors, skipped}
    """
    from hk_funds.storage import upsert_nav_history

    funds = get_funds_with_isins(conn)
    existing_funds = get_existing_nav_funds(conn) if skip_existing else set()

    stats = {
        "funds_processed": 0,
        "funds_updated": 0,
        "total_nav_records": 0,
        "errors": 0,
        "skipped": 0,
    }

    # Deduplicate by ISIN (one ISIN may map to multiple fund IDs)
    isin_to_funds: Dict[str, List[Tuple[int, str]]] = {}
    for fund_id, isin, name in funds:
        if skip_existing and fund_id in existing_funds:
            stats["skipped"] += 1
            continue
        if isin not in isin_to_funds:
            isin_to_funds[isin] = []
        isin_to_funds[isin].append((fund_id, name))

    unique_isins = list(isin_to_funds.keys())
    if limit:
        unique_isins = unique_isins[:limit]

    logger.info(
        f"Processing {len(unique_isins)} unique ISINs "
        f"mapping to {sum(len(v) for v in isin_to_funds.values())} funds"
    )

    for i, isin in enumerate(unique_isins):
        fund_list = isin_to_funds[isin]
        stats["funds_processed"] += len(fund_list)

        try:
            records, error = fetch_nav_history(isin)

            if error:
                if "not found" not in error.lower():
                    logger.warning(f"  [{i+1}/{len(unique_isins)}] {isin}: {error}")
                stats["errors"] += 1
                continue

            if not records:
                stats["errors"] += 1
                continue

            # Store NAV for all funds matching this ISIN
            for fund_id, fund_name in fund_list:
                n = upsert_nav_history(conn, fund_id, records)
                stats["total_nav_records"] += n
                stats["funds_updated"] += 1

            if (i + 1) % 20 == 0:
                logger.info(
                    f"  [{i+1}/{len(unique_isins)}] "
                    f"Updated={stats['funds_updated']} "
                    f"NAV recs={stats['total_nav_records']} "
                    f"Errors={stats['errors']}"
                )

        except Exception as e:
            logger.warning(f"  [{i+1}/{len(unique_isins)}] {isin}: {e}")
            stats["errors"] += 1

        # Rate limiting
        if delay > 0:
            time.sleep(delay)

    return stats


def main():
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    from hk_funds.storage import init_db

    conn = init_db()

    args = sys.argv[1:]
    limit = None
    skip_existing = "--skip-existing" in args

    if "--limit" in args:
        idx = args.index("--limit")
        limit = int(args[idx + 1])

    logger.info("Starting NAV backfill from Yahoo Finance...")
    logger.info(f"  skip_existing={skip_existing}, limit={limit}")

    before_count = conn.execute(
        "SELECT COUNT(*) FROM hk_fund_nav_history"
    ).fetchone()[0]
    before_funds = conn.execute(
        "SELECT COUNT(DISTINCT fund_id) FROM hk_fund_nav_history"
    ).fetchone()[0]
    logger.info(f"  Before: {before_count} NAV records across {before_funds} funds")

    stats = backfill_fund_nav(conn, limit=limit, skip_existing=skip_existing)

    conn.commit()

    after_count = conn.execute(
        "SELECT COUNT(*) FROM hk_fund_nav_history"
    ).fetchone()[0]
    after_funds = conn.execute(
        "SELECT COUNT(DISTINCT fund_id) FROM hk_fund_nav_history"
    ).fetchone()[0]

    logger.info(
        f"Done: {stats['funds_updated']} funds updated, "
        f"{stats['total_nav_records']} NAV records added, "
        f"{stats['errors']} errors, "
        f"{stats['skipped']} skipped"
    )
    logger.info(
        f"  After: {after_count} NAV records across {after_funds} funds "
        f"(+{after_count - before_count} records, +{after_funds - before_funds} funds)"
    )

    conn.close()


if __name__ == "__main__":
    main()
