"""
Webb-site.qizai.ai enrichment pipeline — enrich HK fund managers with Webb-site data.

Fetches for each SFC licensed corporation:
  - Incorporation date (company age)
  - Licenses with effective dates (license tenure)
  - Website URLs
  - Name change history
  - Derived metrics: license tenure vs company age, license coverage

Usage:
    python3 -m hk_funds.pipeline_webb_site            # process all managers
    python3 -m hk_funds.pipeline_webb_site --limit 10 # test with 10
    python3 -m hk_funds.pipeline_webb_site --ce AAA121 # single CE
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

import requests

from hk_funds.storage import init_db

logger = logging.getLogger("hk_funds.pipeline_webb_site")

BASE_URL = "https://webb-site.qizai.ai"
SEARCH_URL = f"{BASE_URL}/api/search/search"
COMPREHENSIVE_URL = f"{BASE_URL}/api/org/comprehensive"
OFFICERS_URL = f"{BASE_URL}/en/org"  # /{PersonID}/officers (SSR page)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json",
}

REQUEST_DELAY = 0.5  # seconds between requests


def search_by_ce(ce_number: str) -> Optional[Dict[str, Any]]:
    """Search webb-site by SFC CE number, return the org match if found."""
    try:
        resp = requests.get(
            SEARCH_URL,
            params={"search_key": ce_number, "limit": 5},
            headers=HEADERS,
            timeout=30,
        )
        if resp.status_code != 200:
            return None

        data = resp.json()
        if data.get("code") != 200:
            return None

        items = data.get("result", {}).get("items", [])
        # Find the org with exact SFCID match
        for item in items:
            if item.get("is_org") and item.get("SFCID") == ce_number:
                return item
        # Fallback: first org in results
        for item in items:
            if item.get("is_org"):
                return item

        return None
    except Exception as e:
        logger.debug(f"Search failed for {ce_number}: {e}")
        return None


def get_comprehensive(pid: int) -> Optional[Dict[str, Any]]:
    """Get comprehensive org data by PersonID."""
    try:
        resp = requests.get(
            COMPREHENSIVE_URL,
            params={"pid": pid},
            headers=HEADERS,
            timeout=30,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        if data.get("code") != 200:
            return None
        return data.get("result", {})
    except Exception as e:
        logger.debug(f"Comprehensive failed for {pid}: {e}")
        return None


def extract_enrichment(
    search_item: Dict[str, Any],
    comprehensive: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Extract enrichment fields from webb-site search + comprehensive data."""
    enrichment = {
        "webb_id": search_item.get("PersonID"),
        "inc_date": search_item.get("inc_date"),
        "company_name_en": search_item.get("org_name"),
        "company_name_cn": search_item.get("cName"),
    }

    if comprehensive:
        basic = comprehensive.get("basic_info") or {}
        sfc_info = comprehensive.get("sfc_info") or {}

        # Websites
        websites = basic.get("websites") or []
        if websites:
            enrichment["website"] = websites[0]

        # Incorporation date (more accurate from comprehensive)
        if basic.get("formed"):
            enrichment["inc_date"] = basic["formed"]

        # License effective date (earliest active license start date)
        activities = sfc_info.get("activities") or []
        start_dates = []
        for act in activities:
            if act.get("startDate"):
                start_dates.append(act["startDate"])
        if start_dates:
            enrichment["license_effective_date"] = min(start_dates)

        # Name change history
        name_history = comprehensive.get("name_history") or []
        enrichment["name_history_count"] = len(name_history)

    return enrichment


def update_manager_enrichment(conn, ce_number: str, enrichment: Dict[str, Any]) -> bool:
    """Update a single manager with webb-site enrichment data."""
    updates = []
    params = []

    field_map = {
        "webb_id": ("webb_id", "INTEGER"),
        "inc_date": ("inc_date", "DATE"),
        "website": ("website", "VARCHAR"),
        "license_effective_date": ("license_effective_date", "DATE"),
        "company_name_cn": ("company_name_cn", "VARCHAR"),
        "name_history_count": ("name_history_count", "INTEGER"),
    }

    for key, (col, _type) in field_map.items():
        if key in enrichment and enrichment[key] is not None:
            updates.append(f"{col} = ?")
            params.append(enrichment[key])

    if not updates:
        return False

    updates.append("last_updated = now()")
    params.append(ce_number)

    sql = f"UPDATE hk_fund_managers SET {', '.join(updates)} WHERE ce_number = ?"
    conn.execute(sql, params)
    return True


def ensure_webb_columns(conn):
    """Add webb-site enrichment columns if they don't exist."""
    new_columns = [
        ("webb_id", "INTEGER"),
        ("inc_date", "DATE"),
        ("name_history_count", "INTEGER"),
    ]
    for col_name, col_type in new_columns:
        try:
            conn.execute(
                f"ALTER TABLE hk_fund_managers ADD COLUMN IF NOT EXISTS {col_name} {col_type}"
            )
        except Exception:
            pass


def main():
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    conn = init_db()
    ensure_webb_columns(conn)

    # Determine scope
    args = sys.argv[1:]

    if "--ce" in args:
        idx = args.index("--ce")
        ce = args[idx + 1]
        ce_numbers = [(ce, None)]
    else:
        limit = None
        if "--limit" in args:
            idx = args.index("--limit")
            limit = int(args[idx + 1])

        query = """
            SELECT ce_number, webb_id FROM hk_fund_managers
            WHERE ce_number IS NOT NULL AND ce_number != ''
            AND license_status = 'active'
            AND regulated_activity_9 = true
            AND (webb_id IS NULL OR website IS NULL OR inc_date IS NULL OR name_history_count IS NULL)
            ORDER BY ce_number
        """
        if limit:
            query += f" LIMIT {limit}"

        rows = conn.execute(query).fetchall()
        logger.info(f"Found {len(rows)} managers to enrich")

        # Build list of (ce_number, known_webb_id_or_none)
        ce_numbers = [(row[0], row[1]) for row in rows]

    enriched = 0
    failed = 0
    not_found = 0

    for i, (ce_number, known_pid) in enumerate(ce_numbers):
        try:
            if known_pid:
                # Skip search, use stored webb_id directly
                pid = known_pid
            else:
                # Search
                search_item = search_by_ce(ce_number)
                if not search_item:
                    not_found += 1
                    if (i + 1) % 50 == 0:
                        logger.info(
                            f"  Progress: {i+1}/{len(ce_numbers)} — "
                            f"enriched={enriched}, not_found={not_found}, failed={failed}"
                        )
                    continue
                pid = search_item.get("PersonID")
                time.sleep(REQUEST_DELAY * 0.5)

            # Comprehensive
            comprehensive = get_comprehensive(pid)
            time.sleep(REQUEST_DELAY)

            # Build a minimal search_item for extract_enrichment if we skipped search
            if known_pid:
                search_item = {"PersonID": pid}
            # Extract & store
            enrichment = extract_enrichment(search_item, comprehensive)
            if update_manager_enrichment(conn, ce_number, enrichment):
                enriched += 1

            if (i + 1) % 50 == 0:
                logger.info(
                    f"  Progress: {i+1}/{len(ce_numbers)} — "
                    f"enriched={enriched}, not_found={not_found}, failed={failed}"
                )

        except Exception as e:
            failed += 1
            logger.warning(f"  Failed for {ce_number}: {e}")
            time.sleep(REQUEST_DELAY * 2)

    conn.commit()
    conn.close()

    logger.info(
        f"Done: enriched={enriched}, not_found={not_found}, failed={failed}"
    )


if __name__ == "__main__":
    main()
