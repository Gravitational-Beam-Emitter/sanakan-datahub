"""
Fix KYP gaps — 3 fixes in one script.

Fix #1: derivative_class auto-fill (score=1 for non-derivative, score=4 for derivative)
Fix #2: Initialize KYP + risk rating for HKEX and other unrated active funds
Fix #3: Compute dd_overall_score from hk_manager_dd → issuer_assessment auto-fill

Usage:
    python3 -m hk_funds.fix_kyp_gaps [--dry-run]
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime

from hk_funds.storage import init_db, init_kyp_dimensions, upsert_kyp_dimension
from hk_funds.risk_rating import calculate_fund_risk_rating, upsert_fund_risk_rating, _sync_kyp_from_rating

logger = logging.getLogger("hk_funds.fix_kyp_gaps")


def fix_1_derivative_class(conn, dry_run: bool = False) -> dict:
    """Re-sync derivative_class for all rated funds (applies the new else branch)."""
    stats = {"total": 0, "updated": 0}
    funds = conn.execute("""
        SELECT DISTINCT f.id, f.is_derivative_product
        FROM hk_funds f
        JOIN hk_fund_risk_ratings r ON f.id = r.fund_id
        WHERE f.is_active = true
    """).fetchall()

    for fund_id, is_deriv in funds:
        stats["total"] += 1
        if is_deriv:
            score, findings = 4, "Classified as derivative product by SFC"
        else:
            score, findings = 1, "Not a derivative product per SFC §5.1A"

        if not dry_run:
            upsert_kyp_dimension(conn, fund_id, "derivative_class", {
                "score": score,
                "assessment_status": "reviewed",
                "data_source": "sfc_utmf",
                "assessment_date": datetime.now().strftime("%Y-%m-%d"),
                "findings": findings,
            })
            stats["updated"] += 1
        else:
            stats["updated"] += 1

    return stats


def fix_2_hkex_kyp_init(conn, dry_run: bool = False) -> dict:
    """Initialize KYP dimensions + risk ratings for all active funds missing them."""
    stats = {"kyp_init": 0, "rated": 0, "errors": 0}

    # Find active funds without KYP dimensions
    missing_kyp = conn.execute("""
        SELECT f.id FROM hk_funds f
        WHERE f.is_active = true
        AND f.id NOT IN (SELECT DISTINCT fund_id FROM hk_kyp_dimensions)
    """).fetchall()

    for (fund_id,) in missing_kyp:
        stats["kyp_init"] += 1
        if not dry_run:
            init_kyp_dimensions(conn, fund_id)

    # Find active funds without risk ratings
    unrated = conn.execute("""
        SELECT f.id FROM hk_funds f
        WHERE f.is_active = true
        AND f.id NOT IN (SELECT DISTINCT fund_id FROM hk_fund_risk_ratings)
    """).fetchall()

    for (fund_id,) in unrated:
        try:
            fund_row = conn.execute(
                "SELECT * FROM hk_funds WHERE id = ?", [fund_id]
            ).fetchone()
            if not fund_row:
                continue
            cols = [desc[0] for desc in conn.description]
            fund = dict(zip(cols, fund_row))

            rating = calculate_fund_risk_rating(fund)
            if not dry_run:
                upsert_fund_risk_rating(conn, fund_id, rating)
                _sync_kyp_from_rating(conn, fund_id, rating)
            stats["rated"] += 1
        except Exception as e:
            stats["errors"] += 1
            logger.warning(f"Fund {fund_id} rating failed: {e}")

    return stats


def fix_3_issuer_assessment_from_manager_dd(conn, dry_run: bool = False) -> dict:
    """Compute dd_overall_score from hk_manager_dd dimension scores,
    update hk_fund_managers, and auto-fill issuer_assessment KYP dimension."""
    stats = {"dd_overall_computed": 0, "issuer_assessment_filled": 0, "skipped": 0}

    # Step 1: Compute overall DD score per manager (average of 10 dimensions)
    manager_scores = conn.execute("""
        SELECT manager_id, ROUND(AVG(score), 1) as overall_score
        FROM hk_manager_dd
        WHERE score IS NOT NULL
        GROUP BY manager_id
    """).fetchall()

    for manager_id, overall_score in manager_scores:
        stats["dd_overall_computed"] += 1
        if not dry_run:
            conn.execute("""
                UPDATE hk_fund_managers
                SET dd_overall_score = ?, last_updated = now()
                WHERE id = ?
            """, [overall_score, manager_id])

    # Step 2: Map manager DD → issuer_assessment for linked active funds
    # Score mapping: DD 1-5 → KYP 1-5 (direct mapping)
    #   1 = Strong (low risk), 5 = Lacking (high risk)
    score_labels = {
        1: "Strong internal controls",
        2: "Sufficient internal controls",
        3: "Average internal controls",
        4: "Limited internal controls",
        5: "Lacking internal controls",
    }

    linkable = conn.execute("""
        SELECT f.id, m.dd_overall_score
        FROM hk_funds f
        JOIN hk_fund_managers m ON f.fund_manager_id = m.id
        WHERE f.is_active = true AND m.dd_overall_score IS NOT NULL
    """).fetchall()

    # Fallback: if fund_manager_id FK is not populated, use name-based matching
    if not linkable:
        logger.info("  fund_manager_id FK is empty, trying name-based matching...")
        linkable = conn.execute("""
            SELECT f.id, m.dd_overall_score
            FROM hk_funds f
            JOIN hk_fund_managers m ON f.fund_manager_name_en = m.company_name_en
            WHERE f.is_active = true AND m.dd_overall_score IS NOT NULL
        """).fetchall()

    for fund_id, overall_score in linkable:
        score_int = int(round(overall_score))
        findings = score_labels.get(score_int, f"Manager DD overall score: {overall_score}")

        if not dry_run:
            init_kyp_dimensions(conn, fund_id)
            upsert_kyp_dimension(conn, fund_id, "issuer_assessment", {
                "score": score_int,
                "assessment_status": "reviewed",
                "data_source": "manager_dd",
                "assessment_date": datetime.now().strftime("%Y-%m-%d"),
                "findings": findings,
            })
        stats["issuer_assessment_filled"] += 1

    return stats


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    dry_run = "--dry-run" in sys.argv
    if dry_run:
        logger.info("DRY RUN MODE — no changes will be made")

    conn = init_db()

    # ── Before stats ──
    before = conn.execute("""
        SELECT
            COUNT(DISTINCT f.id) as total_active,
            COUNT(DISTINCT CASE WHEN kd.assessment_status IN ('reviewed', 'approved') AND kd.dimension = 'derivative_class' THEN f.id END) as deriv_done,
            COUNT(DISTINCT CASE WHEN kd.assessment_status IN ('reviewed', 'approved') AND kd.dimension = 'risk_profile' THEN f.id END) as risk_done,
            COUNT(DISTINCT CASE WHEN kd.assessment_status IN ('reviewed', 'approved') AND kd.dimension = 'issuer_assessment' THEN f.id END) as issuer_done,
            COUNT(DISTINCT CASE WHEN kd.fund_id IS NOT NULL THEN f.id END) as funds_with_kyp_rows
        FROM hk_funds f
        LEFT JOIN hk_kyp_dimensions kd ON f.id = kd.fund_id
        WHERE f.is_active = true
    """).fetchone()

    logger.info(f"BEFORE: {before[0]} active, deriv={before[1]}, risk={before[2]}, issuer={before[3]}, with_any_kyp={before[4]}")

    # ── Fix #1: derivative_class for all rated funds ──
    logger.info("Fix #1: derivative_class auto-fill...")
    s1 = fix_1_derivative_class(conn, dry_run=dry_run)
    logger.info(f"  derivative_class updated: {s1['updated']}/{s1['total']}")

    # ── Fix #2: HKEX + unrated funds KYP init + risk rating ──
    logger.info("Fix #2: KYP init + risk rating for missing funds...")
    s2 = fix_2_hkex_kyp_init(conn, dry_run=dry_run)
    logger.info(f"  KYP initialized: {s2['kyp_init']}, rated: {s2['rated']}, errors: {s2['errors']}")

    # ── Fix #3: issuer_assessment from manager DD ──
    logger.info("Fix #3: issuer_assessment from manager DD...")
    s3 = fix_3_issuer_assessment_from_manager_dd(conn, dry_run=dry_run)
    logger.info(f"  dd_overall_score computed: {s3['dd_overall_computed']}, issuer_assessment filled: {s3['issuer_assessment_filled']}")

    # ── After stats ──
    after = conn.execute("""
        SELECT
            COUNT(DISTINCT f.id) as total_active,
            COUNT(DISTINCT CASE WHEN kd.assessment_status IN ('reviewed', 'approved') AND kd.dimension = 'derivative_class' THEN f.id END) as deriv_done,
            COUNT(DISTINCT CASE WHEN kd.assessment_status IN ('reviewed', 'approved') AND kd.dimension = 'risk_profile' THEN f.id END) as risk_done,
            COUNT(DISTINCT CASE WHEN kd.assessment_status IN ('reviewed', 'approved') AND kd.dimension = 'issuer_assessment' THEN f.id END) as issuer_done,
            COUNT(DISTINCT CASE WHEN kd.assessment_status IN ('reviewed', 'approved') AND kd.dimension = 'complexity' THEN f.id END) as complexity_done
        FROM hk_funds f
        LEFT JOIN hk_kyp_dimensions kd ON f.id = kd.fund_id
        WHERE f.is_active = true
    """).fetchone()

    logger.info(f"AFTER:  {after[0]} active, deriv={after[1]}, risk={after[2]}, issuer={after[3]}, complexity={after[4]}")

    # ── Overall completion ──
    total_dims = after[0] * 10
    done_dims = conn.execute("""
        SELECT COUNT(*) FROM hk_kyp_dimensions
        WHERE assessment_status IN ('reviewed', 'approved')
    """).fetchone()[0]
    logger.info(f"Overall completion: {done_dims}/{total_dims} ({round(done_dims/total_dims*100, 1)}%)")

    if dry_run:
        logger.info("DRY RUN — rolling back")
        conn.close()
        return

    conn.commit()
    logger.info("All fixes committed.")
    conn.close()


if __name__ == "__main__":
    main()
