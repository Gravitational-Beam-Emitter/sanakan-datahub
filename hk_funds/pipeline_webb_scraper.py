"""
Webb-database.com SFC Licensed Corporations scraper.

Scrapes SFClicount.asp for ALL licensed corporations (CE numbers + names),
resolves SFC CE numbers, and stores in hk_fund_managers. Then matches
funds to managers by company name.

Usage:
    python -m hk_funds.pipeline_webb_scraper --fetch
    python -m hk_funds.pipeline_webb_scraper --resolve-ce  # resolve SFC CE numbers
    python -m hk_funds.pipeline_webb_scraper --link         # link funds to managers
"""

from __future__ import annotations

import logging
import re
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

from hk_funds.config import SFC_REQUEST_DELAY, SFC_TIMEOUT
from hk_funds.storage import (
    init_db,
    upsert_managers,
    log_fetch_start,
    log_fetch_end,
)

logger = logging.getLogger("hk_funds.pipeline_webb")

WEBB_BASE = "https://webb-database.com/dbpub"
SFC_LICOUNT_URL = f"{WEBB_BASE}/SFClicount.asp"
SFC_LICREC_URL = f"{WEBB_BASE}/SFColicrec.asp"

_session: Optional[requests.Session] = None


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/125.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9,zh-HK;q=0.8,zh;q=0.7",
        })
    return _session


def fetch_all_licensed_corporations() -> List[Dict[str, Any]]:
    """Fetch ALL SFC licensed corporations from webb-database SFClicount.asp.

    Returns list of dicts with keys:
        ce_number -- webb-database internal ID (used as temp key until SFC CE resolved)
        company_name_en -- English company name
    """
    session = _get_session()
    logger.info("Fetching SFClicount.asp (this is a ~2.4MB page)...")
    resp = session.get(SFC_LICOUNT_URL, timeout=60)
    resp.raise_for_status()

    # Parse all company links: <a href='SFClicensees.asp?p=NUM&...'>NAME</a>
    pattern = re.compile(
        r"<a\s+href='SFClicensees\.asp\?p=(\d+)&[^']*'>(.*?)</a>",
        re.IGNORECASE,
    )
    matches = pattern.findall(resp.text)

    records = []
    seen_ce = set()
    for webb_id, name in matches:
        name = name.strip()
        # Clean HTML entities
        name = name.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        if webb_id not in seen_ce:
            seen_ce.add(webb_id)
            records.append({
                "ce_number": f"WEBB-{webb_id}",  # Temporary: will be replaced by SFC CE
                "webb_id": int(webb_id),
                "company_name_en": name,
                "company_name_cn": None,
                "license_type": "Type 9",  # Default, refined later
                "license_status": "active",
                "source_url": f"{WEBB_BASE}/SFColicrec.asp?p={webb_id}",
            })

    logger.info(f"Parsed {len(records)} unique licensed corporations")
    return records


def resolve_sfc_ce_numbers(conn, batch_size: int = 50, delay: float = 1.0) -> int:
    """For each manager with a WEBB- prefix CE number, fetch the SFC CE number
    from SFColicrec.asp and update the record.

    The SFC CE number is an alphanumeric code like 'AFF275' or 'AAB893'.
    It appears on the SFColicrec.asp page as a link to the SFC public register:
        corp/{SFC_CE_NUMBER}/licences

    Returns count of successfully resolved CE numbers.
    """
    session = _get_session()

    # Get all managers with WEBB- prefix
    rows = conn.execute("""
        SELECT id, ce_number, webb_id, company_name_en
        FROM hk_fund_managers
        WHERE ce_number LIKE 'WEBB-%'
    """).fetchall()

    if not rows:
        logger.info("No WEBB-prefixed managers to resolve")
        return 0

    logger.info(f"Resolving SFC CE numbers for {len(rows)} corporations...")
    resolved = 0

    for i, (mgr_id, old_ce, webb_id, name) in enumerate(rows):
        try:
            if i > 0 and i % batch_size == 0:
                logger.info(f"  Resolved {resolved}/{i} ...")
                time.sleep(delay * 2)  # Longer pause between batches

            url = f"{SFC_LICREC_URL}?p={webb_id}"
            resp = session.get(url, timeout=SFC_TIMEOUT)

            # Find SFC CE number: corp/AFF275 format
            sfc_match = re.search(r'corp/([A-Z0-9]+)', resp.text)
            if sfc_match:
                sfc_ce = sfc_match.group(1)
                conn.execute("""
                    UPDATE hk_fund_managers
                    SET ce_number = ?, last_updated = now()
                    WHERE id = ?
                """, [sfc_ce, mgr_id])
                resolved += 1

                if resolved <= 10:  # Log first few
                    logger.info(f"  {name[:60]} -> SFC CE: {sfc_ce}")
            else:
                logger.debug(f"  No SFC CE found for {name[:60]} (webb_id={webb_id})")

            if delay:
                time.sleep(delay)

        except Exception as e:
            logger.debug(f"  Failed to resolve {name[:60]}: {e}")
            continue

    logger.info(f"Resolved {resolved}/{len(rows)} SFC CE numbers")
    return resolved


def store_corporations(conn, records: List[Dict[str, Any]]) -> int:
    """Store licensed corporation records in hk_fund_managers.

    Uses webb_id as a temporary unique identifier (via the ce_number WEBB- prefix)
    until SFC CE numbers are resolved.
    """
    # Check if webb_id column exists, add if needed
    cols = [row[0] for row in conn.execute("DESCRIBE hk_fund_managers").fetchall()]
    if "webb_id" not in cols:
        try:
            conn.execute("ALTER TABLE hk_fund_managers ADD COLUMN webb_id INTEGER")
        except Exception:
            pass

    # Use upsert - but our ce_number has WEBB- prefix during initial import
    # After SFC CE resolution, ce_number will be updated to the real SFC CE
    # Use a two-pass approach: INSERT new records, UPDATE existing by webb_id
    count = 0
    for r in records:
        webb_id = r.get("webb_id")
        if webb_id is None:
            continue

        # Check if this webb_id already exists
        existing = conn.execute(
            "SELECT id FROM hk_fund_managers WHERE webb_id = ?",
            [webb_id]
        ).fetchone()

        if existing:
            # Update existing
            conn.execute("""
                UPDATE hk_fund_managers SET
                    company_name_en = ?,
                    company_name_cn = ?,
                    source_url = ?,
                    last_updated = now()
                WHERE id = ?
            """, [
                r["company_name_en"],
                r.get("company_name_cn"),
                r.get("source_url"),
                existing[0],
            ])
        else:
            # Insert new
            try:
                conn.execute("""
                    INSERT INTO hk_fund_managers (
                        ce_number, company_name_en, company_name_cn,
                        license_type, license_status, source_url, webb_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """, [
                    r["ce_number"],
                    r["company_name_en"],
                    r.get("company_name_cn"),
                    r.get("license_type", "Type 9"),
                    r.get("license_status", "active"),
                    r.get("source_url"),
                    webb_id,
                ])
                count += 1
            except Exception as e:
                logger.debug(f"  Insert failed for {r['company_name_en'][:60]}: {e}")

    return count


def fetch_all(date_str: str = None) -> Dict[str, Any]:
    """Main entry: fetch all corporations, store, resolve CE numbers, link to funds."""
    conn = init_db()
    today = date_str or datetime.now().strftime("%Y-%m-%d")
    summary = {"found": 0, "stored": 0, "ce_resolved": 0, "linked": 0, "errors": []}

    log_id = log_fetch_start(conn, today, source="webb_sfc_corporations")

    try:
        # Step 1: Fetch all corporations
        records = fetch_all_licensed_corporations()
        summary["found"] = len(records)

        # Step 2: Store in hk_fund_managers
        if records:
            summary["stored"] = store_corporations(conn, records)
            logger.info(f"Stored {summary['stored']} new corporations")

        # Step 3: Resolve SFC CE numbers
        summary["ce_resolved"] = resolve_sfc_ce_numbers(conn)

        # Step 4: Link funds to managers
        from hk_funds.pipeline_managers import link_funds_to_managers
        summary["linked"] = link_funds_to_managers(conn).get("linked", 0)

        log_fetch_end(conn, log_id, items_checked=summary["found"],
                      new_items=summary["stored"])

    except Exception as e:
        summary["errors"].append(str(e))
        logger.error(f"Webb scrape failed: {e}")
        log_fetch_end(conn, log_id, status="error", error=str(e))
    finally:
        conn.close()

    return summary


# ═══════════════════════════════════════════════════════════════
#  CLI entry
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s"
    )

    if "--fetch" in sys.argv:
        result = fetch_all()
        print(f"Found: {result['found']}")
        print(f"Stored: {result['stored']}")
        print(f"CE resolved: {result['ce_resolved']}")
        print(f"Linked: {result['linked']}")
        if result["errors"]:
            print(f"Errors: {result['errors']}")

    elif "--resolve-ce" in sys.argv:
        conn = init_db()
        try:
            count = resolve_sfc_ce_numbers(conn)
            print(f"Resolved {count} SFC CE numbers")
        finally:
            conn.close()

    elif "--link" in sys.argv:
        from hk_funds.pipeline_managers import link_funds_to_managers
        conn = init_db()
        try:
            result = link_funds_to_managers(conn)
            print(result)
        finally:
            conn.close()

    else:
        print("Usage: python -m hk_funds.pipeline_webb_scraper --fetch | --resolve-ce | --link")
