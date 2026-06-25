"""
Populate hk_fund_performance from NAV history data.

Calculates returns (1M, 3M, 6M, 1Y, 3Y, 5Y) for each fund from
hk_fund_nav_history and stores them in hk_fund_performance.

Used by rating_engine for profitable_product_ratio factor (10% weight).

Usage:
    python3 -m hk_funds.pipeline_fund_performance
"""

from __future__ import annotations
import logging
from datetime import date
from typing import Optional

from hk_funds.storage import init_db

logger = logging.getLogger("hk_funds.pipeline_fund_performance")


def get_nav_at_date(conn, fund_id: int, target_date: date, window_days: int = 7):
    """Get the NAV closest to target_date within ±window_days."""
    row = conn.execute("""
        SELECT nav, nav_date FROM hk_fund_nav_history
        WHERE fund_id = ?
          AND nav_date BETWEEN ? - INTERVAL '1 day' * ? AND ? + INTERVAL '1 day' * ?
        ORDER BY ABS(nav_date - ?) ASC
        LIMIT 1
    """, [fund_id, target_date, window_days, target_date, window_days, target_date]).fetchone()
    if row:
        return row[0], row[1]
    return None, None


def get_latest_nav(conn, fund_id: int):
    """Get the most recent NAV for a fund."""
    row = conn.execute("""
        SELECT nav, nav_date FROM hk_fund_nav_history
        WHERE fund_id = ?
        ORDER BY nav_date DESC LIMIT 1
    """, [fund_id]).fetchone()
    if row:
        return row[0], row[1]
    return None, None


def calculate_returns(conn, fund_id: int) -> dict:
    """Calculate returns for a fund across multiple time horizons.

    Returns dict with keys matching hk_fund_performance columns.
    """
    latest_nav, latest_date = get_latest_nav(conn, fund_id)
    if not latest_nav or not latest_date:
        return {}

    result = {
        "fund_id": fund_id,
        "data_points_used": conn.execute(
            "SELECT COUNT(*) FROM hk_fund_nav_history WHERE fund_id = ?",
            [fund_id],
        ).fetchone()[0],
    }

    horizons = [
        ("return_1m_pct", 30),
        ("return_3m_pct", 90),
        ("return_6m_pct", 180),
        ("return_1y_pct", 365),
        ("return_3y_annualized_pct", 1095),
        ("return_5y_annualized_pct", 1825),
    ]

    for col, days in horizons:
        target = date.today()
        # Calculate target date
        from datetime import timedelta
        target_date = target - timedelta(days=days)
        past_nav, past_date = get_nav_at_date(conn, fund_id, target_date)
        if past_nav and past_date and past_nav > 0:
            simple_return = (latest_nav - past_nav) / past_nav * 100
            if days <= 365:
                result[col] = round(simple_return, 4)
            else:
                # Annualize for multi-year periods
                years = (latest_date - past_date).days / 365.25
                if years > 0:
                    annualized = ((latest_nav / past_nav) ** (1 / years) - 1) * 100
                    result[col] = round(annualized, 4)

    return result


def populate_performance(conn, overwrite: bool = False):
    """Populate hk_fund_performance for all funds with NAV history."""
    fund_ids = conn.execute(
        "SELECT DISTINCT fund_id FROM hk_fund_nav_history"
    ).fetchall()

    stored = 0
    skipped = 0

    for (fund_id,) in fund_ids:
        if not overwrite:
            existing = conn.execute(
                "SELECT id FROM hk_fund_performance WHERE fund_id = ?", [fund_id]
            ).fetchone()
            if existing:
                skipped += 1
                continue

        returns = calculate_returns(conn, fund_id)
        if not returns or not any(
            returns.get(k) is not None
            for k in [
                "return_1m_pct", "return_3m_pct", "return_6m_pct",
                "return_1y_pct", "return_3y_annualized_pct", "return_5y_annualized_pct",
            ]
        ):
            continue

        # Upsert
        today = date.today()
        conn.execute("""
            INSERT INTO hk_fund_performance (
                fund_id, ytd_return_pct,
                return_1m_pct, return_3m_pct, return_6m_pct,
                return_1y_pct, return_3y_annualized_pct, return_5y_annualized_pct,
                data_points_used, calculation_date, last_updated
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (fund_id) DO UPDATE SET
                ytd_return_pct = EXCLUDED.ytd_return_pct,
                return_1m_pct = EXCLUDED.return_1m_pct,
                return_3m_pct = EXCLUDED.return_3m_pct,
                return_6m_pct = EXCLUDED.return_6m_pct,
                return_1y_pct = EXCLUDED.return_1y_pct,
                return_3y_annualized_pct = EXCLUDED.return_3y_annualized_pct,
                return_5y_annualized_pct = EXCLUDED.return_5y_annualized_pct,
                data_points_used = EXCLUDED.data_points_used,
                calculation_date = EXCLUDED.calculation_date,
                last_updated = EXCLUDED.last_updated
        """, [
            fund_id,
            returns.get("ytd_return_pct"),
            returns.get("return_1m_pct"),
            returns.get("return_3m_pct"),
            returns.get("return_6m_pct"),
            returns.get("return_1y_pct"),
            returns.get("return_3y_annualized_pct"),
            returns.get("return_5y_annualized_pct"),
            returns.get("data_points_used", 0),
            today,
            today,
        ])
        stored += 1

        if stored % 100 == 0:
            logger.info(f"  Progress: {stored} funds processed")

    return stored, skipped


def main():
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    overwrite = "--overwrite" in sys.argv

    conn = init_db()
    stored, skipped = populate_performance(conn, overwrite=overwrite)
    conn.commit()

    total = conn.execute("SELECT COUNT(*) FROM hk_fund_performance").fetchone()[0]
    logger.info(f"Done: stored {stored} new, skipped {skipped} existing, total {total} in DB")

    # Summary of returns coverage
    for col in [
        "return_1m_pct", "return_3m_pct", "return_6m_pct",
        "return_1y_pct", "return_3y_annualized_pct", "return_5y_annualized_pct",
    ]:
        c = conn.execute(
            f"SELECT COUNT(*) FROM hk_fund_performance WHERE {col} IS NOT NULL"
        ).fetchone()[0]
        logger.info(f"  {col}: {c} funds")

    conn.close()


if __name__ == "__main__":
    main()
