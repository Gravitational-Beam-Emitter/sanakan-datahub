"""
WINGS public register scraper — fetch SFC licensed corporations.

Uses the PUBLIC search-ce API (no auth required).
Enumerates all active corporations via keyword search + dedup.

Usage:
    python3 -m hk_funds.pipeline_wings
    python3 -m hk_funds.pipeline_wings --link   # also link funds to managers
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

import requests

from hk_funds.config import SFC_REQUEST_DELAY, SFC_TIMEOUT
from hk_funds.storage import init_db, upsert_managers, get_funds, get_managers, upsert_manager_funds

logger = logging.getLogger("hk_funds.pipeline_wings")

BASE_URL = "https://wings.sfc.hk"
SEARCH_URL = f"{BASE_URL}/api/public/register/search-ce"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json",
    "Origin": "https://wings.sfc.hk",
}

# Keywords to maximize coverage of HK licensed corps
SEARCH_KEYWORDS = [
    # Universal HK company name components
    "limited",
    "ltd",
    "management",
    "investment",
    "asset",
    "capital",
    "partners",
    "advisors",
    "wealth",
    "trust",
    "venture",
    "equity",
    "fund",
    "bank",
    "finance",
    "broker",
    "futures",
    "global",
    "asia",
    "pacific",
    "first",
    "value",
    "growth",
    "core",
    "alpha",
    "credit",
    "private",
    "institutional",
    "principal",
    # Additional terms for broader coverage
    "securities",
    "financial",
    "group",
    "holdings",
    "enterprise",
    "corporation",
    "trading",
    "markets",
    "nomura",
    "morgan",
    "goldman",
    "citigroup",
    "deutsche",
    "bnp",
    "ubs",
    "credit suisse",
    "macquarie",
    "nominee",
    "nominees",
    "insurance",
    "underwriter",
    "clearing",
    "custodian",
    "safe",
    "bull",
    "bear",
    "strategic",
    "dynamic",
    "prudential",
    "prudence",
    "alliance",
    "fidelity",
    "schroder",
    "invesco",
    "blackrock",
    "vanguard",
    "hsbc",
    "standard chartered",
    "citibank",
    "jpmorgan",
    "mirae",
    "csop",
    "china",
    "hong kong",
    "orient",
    "sun",
    "gold",
    "silver",
    "dragon",
    "phoenix",
    "meridian",
    "pinnacle",
    "apex",
    "summit",
    "peak",
    "crown",
    "regal",
    "everest",
    "ocean",
    "star",
    "nova",
    "century",
    "millennium",
    "heritage",
    "legacy",
    "pioneer",
    "frontier",
    "anchor",
    "beacon",
    "compass",
    "horizon",
    "zenith",
    "mercury",
    "orion",
    "aries",
    "taurus",
    "gemini",
    "leo",
    "virgo",
    "libra",
    "sagittarius",
]

# Known CE number prefixes for direct enumeration
CE_PREFIXES = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")


def fetch_all_corporations() -> List[Dict[str, Any]]:
    """Fetch ALL active licensed corporations from WINGS public API.

    Uses keyword search + deduplication by CE reference number.
    """
    session = requests.Session()
    session.headers.update(HEADERS)

    seen_ce = {}  # CE ref → normalized record
    total_fetched = 0

    # Phase 1: Keyword search
    logger.info(f"Phase 1: Searching {len(SEARCH_KEYWORDS)} keywords...")
    for kw in SEARCH_KEYWORDS:
        try:
            count = _search_keyword(session, kw, seen_ce)
            total_fetched += count
            logger.debug(f"  '{kw}' → {count} new (total unique: {len(seen_ce)})")
            time.sleep(SFC_REQUEST_DELAY * 0.3)  # lighter delay for public API
        except Exception as e:
            logger.warning(f"Keyword '{kw}' failed: {e}")

    # Phase 2: CE number prefix enumeration (catches corps missed by keyword)
    logger.info(f"Phase 2: Enumerating {len(CE_PREFIXES)} CE prefixes...")
    for prefix in CE_PREFIXES:
        if prefix not in "ABCDEFGH":  # most CE refs start with these
            continue
        try:
            count = _search_ce_prefix(session, prefix, seen_ce)
            total_fetched += count
            time.sleep(SFC_REQUEST_DELAY * 0.3)
        except Exception as e:
            logger.warning(f"CE prefix '{prefix}' failed: {e}")

    records = list(seen_ce.values())
    logger.info(f"Total unique corporations: {len(records)}")
    return records


def _search_keyword(session: requests.Session, keyword: str, seen: dict) -> int:
    """Search by keyword and add new records to seen dict."""
    new_count = 0
    start = 0
    max_per_page = 100

    while True:
        params = {
            "entityType": "corporation",
            "licstatus": "active",
            "searchlang": "en",
            "searchbyoption": "byname",
            "searchtext": keyword,
            "start": start,
            "limit": max_per_page,
        }
        resp = session.get(SEARCH_URL, params=params, timeout=SFC_TIMEOUT)
        if resp.status_code != 200:
            break

        data = resp.json()
        items = data.get("items") or []
        total = data.get("totalCount") or 0

        for item in items:
            ce = item.get("ceref")
            if ce and ce not in seen:
                seen[ce] = _normalize(item)
                new_count += 1

        start += max_per_page
        if start >= total or start >= 3000 or len(items) == 0:
            break

    return new_count


def _search_ce_prefix(session: requests.Session, prefix: str, seen: dict) -> int:
    """Search by CE number prefix."""
    new_count = 0
    start = 0
    max_per_page = 100

    while True:
        params = {
            "entityType": "corporation",
            "licstatus": "active",
            "searchlang": "en",
            "searchbyoption": "byceref",
            "searchtext": prefix,
            "start": start,
            "limit": max_per_page,
        }
        resp = session.get(SEARCH_URL, params=params, timeout=SFC_TIMEOUT)
        if resp.status_code != 200:
            break

        data = resp.json()
        items = data.get("items") or []
        total = data.get("totalCount") or 0

        if total == 0:
            break

        for item in items:
            ce = item.get("ceref")
            if ce and ce not in seen:
                seen[ce] = _normalize(item)
                new_count += 1

        start += max_per_page
        if start >= total or start >= 3000 or len(items) == 0:
            break

    return new_count


def _normalize(item: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize WINGS API response to our hk_fund_managers schema."""
    ra_details = item.get("raDetails") or []

    # Determine license types
    has_type_1 = any(r.get("actType") == 1 for r in ra_details)
    has_type_4 = any(r.get("actType") == 4 for r in ra_details)
    has_type_9 = any(r.get("actType") == 9 for r in ra_details)

    # Primary license type (most relevant for fund management)
    if has_type_9:
        license_type = "Type 9 (Asset Management)"
    elif has_type_1:
        license_type = "Type 1 (Dealing in Securities)"
    elif has_type_4:
        license_type = "Type 4 (Advising on Securities)"
    else:
        # Check for other types
        other_types = [r.get("actDesc") for r in ra_details]
        license_type = ", ".join(other_types) if other_types else "Unknown"

    return {
        "ce_number": item.get("ceref", ""),
        "company_name_en": item.get("name", ""),
        "company_name_cn": item.get("nameChi") or "",
        "license_type": license_type,
        "regulated_activity_1": has_type_1,
        "regulated_activity_4": has_type_4,
        "regulated_activity_9": has_type_9,
        "license_status": "active" if item.get("hasActiveLicence") else "inactive",
        "entity_type": "corporation",
        "source": "wings_public_api",
        "source_url": f"https://wings.sfc.hk/main/",
    }


def insert_managers(conn, records: List[Dict[str, Any]]) -> int:
    """Insert fetched corporations into the database."""
    upsert_managers(conn, records)
    return len(records)


def link_funds_to_managers(conn):
    """Link funds to managers by name matching (same logic as pipeline_managers)."""
    from hk_funds.pipeline_managers import link_funds_to_managers as do_link
    do_link(conn)


def main():
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    do_link = "--link" in sys.argv

    conn = init_db()
    logger.info("Fetching licensed corporations from WINGS public API...")
    records = fetch_all_corporations()

    if records:
        count = insert_managers(conn, records)
        logger.info(f"Inserted/updated {count} managers")

    if do_link:
        logger.info("Linking funds to managers...")
        link_funds_to_managers(conn)
        logger.info("Fund-manager linking complete")

    # Print stats
    total = conn.execute("SELECT COUNT(*) FROM hk_fund_managers").fetchone()[0]
    type9 = conn.execute(
        "SELECT COUNT(*) FROM hk_fund_managers WHERE regulated_activity_9 = true"
    ).fetchone()[0]
    linked = conn.execute(
        "SELECT COUNT(DISTINCT fund_id) FROM hk_fund_manager_funds"
    ).fetchone()[0]
    logger.info(f"Stats: {total} total managers, {type9} with Type 9, {linked} funds linked")

    conn.close()


if __name__ == "__main__":
    main()
