"""
Manager website scraping orchestrator.

Runs all registered manager connectors against the fund database.
Each connector scrapes a specific manager's website to extract
fund ISINs, NAVs, fees, benchmarks, and performance data.

Usage:
    python -m hk_funds.pipeline_manager_scrape --all       # All registered connectors
    python -m hk_funds.pipeline_manager_scrape --ce AFF275  # Specific CE number
    python -m hk_funds.pipeline_manager_scrape --list       # List available connectors
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

from hk_funds.manager_connectors import (
    get_connector_for_manager,
    get_all_registered_ce_numbers,
    get_website_for_manager,
    list_managers_needing_connectors,
)
from hk_funds.manager_connectors.base import MANAGER_WEBSITES
from hk_funds.storage import init_db

logger = logging.getLogger("hk_funds.pipeline_manager_scrape")


def scrape_manager(conn, ce_number: str, date_str: str = None) -> Optional[Dict[str, Any]]:
    """Run a single manager connector.

    Returns stats dict or None if no connector is registered.
    """
    connector = get_connector_for_manager(ce_number)
    if connector is None:
        logger.warning(f"No connector registered for CE {ce_number}")
        return None

    mgr_info = conn.execute(
        "SELECT id, company_name_en, website FROM hk_fund_managers WHERE ce_number = ?",
        [ce_number]
    ).fetchone()

    manager_id = mgr_info[0] if mgr_info else None
    mgr_name = mgr_info[1] if mgr_info else ce_number
    website = mgr_info[2] if mgr_info and mgr_info[2] else get_website_for_manager(ce_number)

    logger.info(f"Scraping {mgr_name} [{ce_number}] website={website}")

    stats = connector.scrape_and_store(conn, date_str)
    stats["ce_number"] = ce_number
    stats["manager_name"] = mgr_name

    # Collect manager AUM (separate step, doesn't require scrape_and_store override)
    if manager_id:
        try:
            if connector._collect_manager_aum(conn, ce_number, manager_id):
                stats["aum_collected"] = True
        except Exception as e:
            logger.warning(f"AUM collection failed for CE {ce_number}: {e}")

    return stats


def scrape_all_managers(date_str: str = None) -> Dict[str, Any]:
    """Run all registered manager connectors.

    Returns summary with per-manager stats.
    """
    conn = init_db()
    today = date_str or datetime.now().strftime("%Y-%m-%d")
    summary = {
        "date": today,
        "managers_attempted": 0,
        "total_funds_found": 0,
        "total_isins_updated": 0,
        "total_navs_stored": 0,
        "total_details_updated": 0,
        "total_aum_collected": 0,
        "per_manager": [],
        "errors": [],
    }

    ce_numbers = get_all_registered_ce_numbers()
    logger.info(f"Running {len(ce_numbers)} registered connectors...")

    for ce in ce_numbers:
        try:
            stats = scrape_manager(conn, ce, today)
            if stats:
                summary["managers_attempted"] += 1
                summary["total_funds_found"] += stats.get("funds_found", 0)
                summary["total_isins_updated"] += stats.get("isins_updated", 0)
                summary["total_navs_stored"] += stats.get("navs_stored", 0)
                summary["total_details_updated"] += stats.get("details_updated", 0)
                if stats.get("aum_collected"):
                    summary["total_aum_collected"] += 1
                summary["per_manager"].append(stats)
        except Exception as e:
            logger.error(f"Failed to scrape CE {ce}: {e}")
            summary["errors"].append({"ce_number": ce, "error": str(e)})

    conn.close()
    return summary


def update_manager_websites(conn) -> int:
    """Populate website URLs for managers from the MANAGER_WEBSITES dict.

    Returns count of updated managers.
    """
    count = 0
    for ce_number, website in MANAGER_WEBSITES.items():
        before = conn.execute(
            "SELECT COUNT(*) FROM hk_fund_managers WHERE ce_number = ? AND (website IS NULL OR website = '')",
            [ce_number]
        ).fetchone()[0]
        if before > 0:
            conn.execute(
                "UPDATE hk_fund_managers SET website = ? WHERE ce_number = ? AND (website IS NULL OR website = '')",
                [website, ce_number]
            )
            count += before
    logger.info(f"Updated {count} manager websites")
    return count


# ═══════════════════════════════════════════════════════════════
#  CLI entry
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s"
    )

    if "--list" in sys.argv:
        conn = init_db(read_only=True)
        print("=== Registered Connectors ===")
        for ce in get_all_registered_ce_numbers():
            from hk_funds.manager_connectors import _registry
            cls = _registry[ce]
            website = get_website_for_manager(ce) or ""
            print(f"  CE {ce}: {cls.__name__} ({website})")

        print("\n=== Managers Needing Connectors ===")
        needs = list_managers_needing_connectors(conn, min_funds=5)
        for m in needs:
            if not m["has_connector"]:
                print(f"  CE {m['ce_number']:10s}: {m['company_name_en'][:50]:50s} ({m['fund_count']} funds)")
        conn.close()

    elif "--all" in sys.argv:
        conn = init_db()
        try:
            update_manager_websites(conn)
        finally:
            conn.close()
        result = scrape_all_managers()
        print(f"\nManagers scraped: {result['managers_attempted']}")
        print(f"Funds found: {result['total_funds_found']}")
        print(f"ISINs updated: {result['total_isins_updated']}")
        print(f"NAVs stored: {result['total_navs_stored']}")
        print(f"Details updated: {result['total_details_updated']}")
        if result["errors"]:
            print(f"Errors: {result['errors']}")

    elif "--ce" in sys.argv:
        idx = sys.argv.index("--ce")
        if idx + 1 < len(sys.argv):
            ce = sys.argv[idx + 1]
            conn = init_db()
            try:
                update_manager_websites(conn)
                stats = scrape_manager(conn, ce)
                if stats:
                    print(stats)
            finally:
                conn.close()
        else:
            print("Usage: --ce <CE_NUMBER>")

    elif "--update-websites" in sys.argv:
        conn = init_db()
        try:
            update_manager_websites(conn)
        finally:
            conn.close()

    else:
        print("Usage: python -m hk_funds.pipeline_manager_scrape --all | --ce <CE> | --list | --update-websites")
