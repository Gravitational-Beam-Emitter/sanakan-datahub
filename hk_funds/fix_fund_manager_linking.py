"""
Fix fund-manager linking: remap funds wrongly attributed to M76
(Asset Management Group Limited) to their correct HK manager entities.

M76 serves as a catch-all for many global brands because the original
linking logic didn't find the correct HK entity. This script fixes that.

Usage:
    python3 -m hk_funds.fix_fund_manager_linking [--dry-run]
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

from hk_funds.storage import init_db
from hk_funds.pipeline_manager_aum import _extract_brand

logger = logging.getLogger("hk_funds.fix_fund_manager_linking")


# Brand substring in fund_manager_name_en → HK manager ILIKE match
# These are the misattributed brands that should be remapped from M76
REMAP_RULES = [
    # (brand_in_fund_name, hk_manager_ilike_pattern, description)
    ("JPMorgan", "%JPMorgan Funds (Asia)%", "JPMorgan → JPMorgan Funds (Asia)"),
    ("BNP Paribas", "%BNP PARIBAS ASSET MANAGEMENT ASIA%", "BNP Paribas → BNP PAM Asia"),
    ("BNP PARIBAS", "%BNP PARIBAS ASSET MANAGEMENT ASIA%", "BNP Paribas → BNP PAM Asia"),
    ("UBS", "%UBS Asset Management (Hong Kong)%", "UBS → UBS AM HK"),
    ("Goldman Sachs", "%Goldman Sachs (Asia)%", "Goldman Sachs → Goldman Sachs Asia"),
    ("Goldman", "%Goldman Sachs (Asia)%", "Goldman Sachs → Goldman Sachs Asia"),
    ("Pictet", "%Pictet Asset Management (Hong Kong)%", "Pictet → Pictet AM HK"),
    ("HSBC", "%HSBC Global Asset Management (Hong Kong)%", "HSBC → HSBC Global AM HK"),
    ("Neuberger Berman", "%Neuberger Berman%", "Neuberger Berman → NB Asia"),
    ("Robeco", "%Robeco Hong Kong%", "Robeco → Robeco HK"),
    ("Jupiter", "%Jupiter Asset Management%", "Jupiter → Jupiter AM"),
    ("Zhong Ou", "%Zhong Ou Asset Management International%", "Zhong Ou → ZO AM Intl"),
]


def find_hk_manager(conn, brand: str, ilike_pattern: str) -> Optional[Tuple[int, str]]:
    """Find the best matching HK Type 9 manager."""
    # Try exact ILIKE match first
    rows = conn.execute("""
        SELECT id, company_name_en FROM hk_fund_managers
        WHERE company_name_en ILIKE ?
        AND license_status = 'active' AND regulated_activity_9 = true
        AND id != 76
        ORDER BY
            CASE WHEN company_name_en ILIKE '%Asset Management%' THEN 0 ELSE 1 END,
            CASE WHEN company_name_en ILIKE '%Hong Kong%' THEN 0 ELSE 1 END
        LIMIT 3
    """, [ilike_pattern]).fetchall()

    if rows:
        return (rows[0][0], rows[0][1])

    # Fallback: search by brand name
    rows = conn.execute("""
        SELECT id, company_name_en FROM hk_fund_managers
        WHERE company_name_en ILIKE ?
        AND license_status = 'active' AND regulated_activity_9 = true
        AND id != 76
        LIMIT 3
    """, [f"%{brand}%"]).fetchall()

    if rows:
        return (rows[0][0], rows[0][1])

    return None


def fix_fund_manager_links(conn, dry_run: bool = False) -> Dict:
    """Fix fund-manager links for funds wrongly attributed to M76.

    Returns stats dict with counts of fixed funds per brand.
    """
    stats = {
        "total_checked": 0,
        "remapped": 0,
        "skipped_no_match": 0,
        "skipped_not_m76": 0,
        "brands_fixed": {},
    }

    # Get all funds linked to M76
    m76_funds = conn.execute("""
        SELECT id, fund_name_en, fund_manager_name_en, fund_manager_id
        FROM hk_funds
        WHERE fund_manager_id = 76
        ORDER BY fund_manager_name_en, id
    """).fetchall()

    for fund_id, fund_name, mgr_name, current_mgr_id in m76_funds:
        stats["total_checked"] += 1

        if current_mgr_id != 76:
            stats["skipped_not_m76"] += 1
            continue

        if not mgr_name:
            stats["skipped_no_match"] += 1
            continue

        # Try each remap rule
        new_mgr_id = None
        new_mgr_name = None
        matched_brand = None

        for brand, ilike_pattern, desc in REMAP_RULES:
            if brand.lower() in mgr_name.lower():
                match = find_hk_manager(conn, brand, ilike_pattern)
                if match:
                    new_mgr_id, new_mgr_name = match
                    matched_brand = desc
                    break

        if not new_mgr_id:
            stats["skipped_no_match"] += 1
            continue

        if dry_run:
            logger.info(
                f"  [DRY-RUN] Fund {fund_id:4d}: M76 → M{new_mgr_id} ({new_mgr_name[:40]}) "
                f"[{matched_brand}] | {mgr_name[:50]}"
            )
        else:
            # Update hk_funds.fund_manager_id
            conn.execute(
                "UPDATE hk_funds SET fund_manager_id = ?, last_updated = now() WHERE id = ?",
                [new_mgr_id, fund_id],
            )

            # Update hk_fund_manager_funds
            # Check if link already exists for the new manager
            existing_link = conn.execute(
                "SELECT id FROM hk_fund_manager_funds WHERE fund_id = ? AND manager_id = ?",
                [fund_id, new_mgr_id],
            ).fetchone()

            if not existing_link:
                # Delete old M76 link
                conn.execute(
                    "DELETE FROM hk_fund_manager_funds WHERE fund_id = ? AND manager_id = 76",
                    [fund_id],
                )
                # Insert new link
                conn.execute(
                    """INSERT INTO hk_fund_manager_funds (fund_id, manager_id, role, is_primary)
                       VALUES (?, ?, 'fund_manager', true)""",
                    [fund_id, new_mgr_id],
                )
            else:
                # Just delete the old M76 link
                conn.execute(
                    "DELETE FROM hk_fund_manager_funds WHERE fund_id = ? AND manager_id = 76",
                    [fund_id],
                )

        stats["remapped"] += 1
        stats["brands_fixed"][matched_brand] = stats["brands_fixed"].get(matched_brand, 0) + 1

    return stats


def main():
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    dry_run = "--dry-run" in sys.argv

    conn = init_db()

    logger.info("Checking M76 fund links...")

    # Count before
    before_m76 = conn.execute(
        "SELECT COUNT(*) FROM hk_funds WHERE fund_manager_id = 76"
    ).fetchone()[0]
    logger.info(f"  Before: {before_m76} funds linked to M76")

    stats = fix_fund_manager_links(conn, dry_run=dry_run)

    if dry_run:
        logger.info(f"DRY RUN - no changes made")
        logger.info(f"  Would remap {stats['remapped']} funds")
        for brand, count in sorted(stats["brands_fixed"].items()):
            logger.info(f"    {brand}: {count} funds")
        conn.close()
        return

    conn.commit()

    # Count after
    after_m76 = conn.execute(
        "SELECT COUNT(*) FROM hk_funds WHERE fund_manager_id = 76"
    ).fetchone()[0]
    logger.info(f"  After: {after_m76} funds linked to M76")
    logger.info(f"  Remapped: {stats['remapped']} funds")
    for brand, count in sorted(stats["brands_fixed"].items()):
        logger.info(f"    {brand}: {count} funds")
    logger.info(f"  Skipped (no match): {stats['skipped_no_match']}")

    # Show updated manager fund counts
    logger.info("Updated fund counts for affected managers:")
    affected_mgrs = set()
    for brand, ilike, desc in REMAP_RULES:
        match = conn.execute("""
            SELECT id, company_name_en FROM hk_fund_managers
            WHERE company_name_en ILIKE ?
            AND license_status = 'active' AND regulated_activity_9 = true
            AND id != 76
            LIMIT 1
        """, [ilike]).fetchone()
        if match:
            affected_mgrs.add(match[0])

    for mgr_id in sorted(affected_mgrs):
        fc = conn.execute(
            "SELECT COUNT(*) FROM hk_fund_manager_funds WHERE manager_id = ?", [mgr_id]
        ).fetchone()[0]
        mgr_name = conn.execute(
            "SELECT company_name_en FROM hk_fund_managers WHERE id = ?", [mgr_id]
        ).fetchone()
        name = mgr_name[0][:50] if mgr_name else "?"
        logger.info(f"  M{mgr_id:4d} {name:50s} {fc} funds")

    conn.close()


if __name__ == "__main__":
    main()
