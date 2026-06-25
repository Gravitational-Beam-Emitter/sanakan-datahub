"""
SFC Licensed Corporations pipeline — fund manager/adviser KYP.

Fetches SFC public register of licensed corporations (focus on Type 9
asset management, Type 1 dealing, Type 4 advising). Links managers to
funds via name matching, and cross-references enforcement data from the
existing name_screening table.

Usage:
    python -m hk_funds.pipeline_managers --init
    python -m hk_funds.pipeline_managers --link
"""

from __future__ import annotations

import logging
import re
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

from hk_funds.config import (
    SFC_REQUEST_DELAY,
    SFC_TIMEOUT,
    SFC_PUBLICREG_SEARCH_URL,
    SFC_PUBLICREG_JSON_URL,
)
from hk_funds.storage import (
    init_db,
    upsert_managers,
    upsert_manager_regulatory,
    upsert_manager_funds,
    get_funds,
    get_managers,
    log_fetch_start,
    log_fetch_end,
)
from hk_funds.pipeline_manager_aum import find_hk_manager_by_brand

logger = logging.getLogger("hk_funds.pipeline_managers")

_session: Optional[requests.Session] = None


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/125.0.0.0 Safari/537.36",
            "Accept": "application/json, text/html, */*",
            "Accept-Language": "en-US,en;q=0.9,zh-HK;q=0.8,zh;q=0.7",
        })
    return _session


# ═══════════════════════════════════════════════════════════════
#  SFC Licensed Corporations Scraper
# ═══════════════════════════════════════════════════════════════


def fetch_sfc_licensed_corporations() -> List[Dict[str, Any]]:
    """Fetch SFC licensed corporations from the public register.

    Focuses on Type 9 (asset management), Type 1 (dealing in securities),
    and Type 4 (advising on securities) license holders.

    Data source strategy (as of 2026-06):
      1. Old publicregWeb (ExtJS app at apps.sfc.hk) — HTML pages still load
         but JSON search returns 0 results (backend database mothballed).
      2. New WINGS platform (wings.sfc.hk) — data migrated here, but API
         returns 403 access_denied (authentication required).
      3. CSV import — primary path until a working API is available.

    For now, gracefully returns empty list so the caller can fall back to
    CSV import via POST /api/v1/import/csv.
    """
    session = _get_session()
    records = []

    # Attempt publicregWeb JSON search (may return 0 results)
    form_params = [
        # Type 9, active, corporations
        {"licstatus": "active", "roleType": "corporation", "ratype": "9",
         "start": 0, "limit": 500},
        # Type 1, active, corporations
        {"licstatus": "active", "roleType": "corporation", "ratype": "1",
         "start": 0, "limit": 500},
        # Type 4, active, corporations
        {"licstatus": "active", "roleType": "corporation", "ratype": "4",
         "start": 0, "limit": 500},
    ]

    for params in form_params:
        try:
            # Get session cookie first
            session.get(SFC_PUBLICREG_SEARCH_URL, timeout=SFC_TIMEOUT)
            resp = session.post(
                SFC_PUBLICREG_JSON_URL,
                data=params,
                timeout=SFC_TIMEOUT,
            )
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("items", [])
                if items:
                    logger.info(
                        f"Found {len(items)} records for ratype={params['ratype']}"
                    )
                    for item in items:
                        records.append(_normalize_publicreg_record(item))
                    # Check for pagination
                    total = data.get("totalCount", 0)
                    if total > len(items):
                        logger.info(
                            f"Total {total} records for ratype={params['ratype']}, "
                            f"fetching all pages..."
                        )
                        for page_start in range(500, total, 500):
                            params["start"] = page_start
                            try:
                                p_resp = session.post(
                                    SFC_PUBLICREG_JSON_URL,
                                    data=params,
                                    timeout=SFC_TIMEOUT,
                                )
                                if p_resp.status_code == 200:
                                    p_data = p_resp.json()
                                    p_items = p_data.get("items", [])
                                    for item in p_items:
                                        records.append(
                                            _normalize_publicreg_record(item)
                                        )
                            except Exception as e:
                                logger.debug(f"Page {page_start} failed: {e}")
            else:
                logger.debug(
                    f"publicregWeb returned {resp.status_code} for ratype={params['ratype']}"
                )
        except Exception as e:
            logger.debug(f"publicregWeb search failed: {e}")

    if not records:
        logger.info(
            "SFC public register returned 0 results (data migrated to WINGS). "
            "Use POST /api/v1/import/csv to import manager data. "
            "Required columns: ce_number, company_name_en, license_type, "
            "regulated_activity_1, regulated_activity_4, regulated_activity_9, "
            "license_status."
        )

    logger.info(f"Fetched {len(records)} SFC licensed corporations")
    return records


def _extract_items(data: dict) -> list:
    """Extract record list from various API response shapes."""
    for key in ("list", "data", "records", "items", "corporations", "entities"):
        if key in data and isinstance(data[key], list):
            return data[key]
    if isinstance(data, list):
        return data
    return []


def _normalize_manager_record(raw: dict) -> Dict[str, Any]:
    """Normalize a raw SFC licensed corporation record.

    Handles both:
      - Old publicregWeb JSON (ExtJS model fields)
      - CSV import records
    """
    # Determine if individual or corporation
    is_corp = raw.get("isCorp") or raw.get("role_type") == "corporation"
    if not is_corp:
        is_corp = str(raw.get("isIndi", "")).lower() == "false"

    # Determine license types (from ratype param context or explicit fields)
    regulated_activities = str(
        raw.get("regulatedActivities", "")
        or raw.get("regulated_activities", "")
    )
    license_type_str = str(
        raw.get("license_type", "")
        or raw.get("licenseType", "")
        or raw.get("ratype", "")
    )

    ra1 = "1" in license_type_str or "dealing in securities" in regulated_activities.lower()
    ra4 = "4" in license_type_str or "advising on securities" in regulated_activities.lower()
    ra9 = "9" in license_type_str or "asset management" in regulated_activities.lower()

    # If activities weren't parsed, check other fields
    if not any([ra1, ra4, ra9]):
        license_lower = license_type_str.lower()
        ra1 = "type 1" in license_lower or "ra1" in license_lower
        ra4 = "type 4" in license_lower or "ra4" in license_lower
        ra9 = "type 9" in license_lower or "ra9" in license_lower

    return {
        "ce_number": str(
            raw.get("ce_number", "")
            or raw.get("ceNumber", "")
            or raw.get("ceref", "")
            or raw.get("centralEntity", "")
        ),
        "company_name_en": str(
            raw.get("company_name_en", "")
            or raw.get("companyNameEn", "")
            or raw.get("name", "")
            or raw.get("name_en", "")
        ),
        "company_name_cn": str(
            raw.get("company_name_cn", "")
            or raw.get("companyNameCn", "")
            or raw.get("companyNameTc", "")
            or raw.get("nameChi", "")
            or raw.get("name_cn", "")
        ),
        "license_type": license_type_str or "Type 9",
        "regulated_activity_1": ra1,
        "regulated_activity_4": ra4,
        "regulated_activity_9": ra9,
        "license_status": str(
            raw.get("license_status", "")
            or raw.get("status", "")
            or raw.get("licenseStatus", "")
            or raw.get("hasActiveLicence", "")
            or "active"
        ),
        "license_effective_date": raw.get("license_effective_date")
            or raw.get("effectiveDate")
            or raw.get("licenseDate"),
        "business_address": raw.get("business_address")
            or raw.get("address")
            or raw.get("businessAddress"),
        "website": raw.get("website") or raw.get("webSite"),
        "key_ro_name_en": raw.get("key_ro_name_en") or raw.get("roNameEn"),
        "key_ro_name_cn": raw.get("key_ro_name_cn") or raw.get("roNameCn"),
        "ro_count": raw.get("ro_count") or raw.get("roCount"),
        "total_licensed_staff": raw.get("total_licensed_staff") or raw.get("staffCount"),
        "has_sfc_enforcement_history": False,
        "enforcement_count": 0,
        "source_url": raw.get("source_url") or raw.get("url"),
    }


def _normalize_publicreg_record(raw: dict) -> Dict[str, Any]:
    """Normalize a record from publicregWeb JSON (ExtJS model fields).

    Fields from EntitySearchResultItemModel:
      ceref, name, nameChi, isIndi, isCorp, isRi, address, hasActiveLicence
    """
    return _normalize_manager_record({
        "ceref": raw.get("ceref", ""),
        "name": raw.get("name", ""),
        "nameChi": raw.get("nameChi", ""),
        "isCorp": raw.get("isCorp", raw.get("isRi")),
        "license_type": f"Type {raw.get('_ratype', '9')}" if raw.get("_ratype") else "",
        "regulated_activity_1": str(raw.get("_ratype", "")) == "1",
        "regulated_activity_4": str(raw.get("_ratype", "")) == "4",
        "regulated_activity_9": str(raw.get("_ratype", "")) == "9",
        "hasActiveLicence": raw.get("hasActiveLicence", "active"),
        "address": raw.get("address"),
    })


# ═══════════════════════════════════════════════════════════════
#  Fund-Manager Linker
# ═══════════════════════════════════════════════════════════════

def _normalize_company_name(name: str) -> str:
    """Normalize a company name for matching: lowercase, strip legal suffixes."""
    if not name:
        return ""
    name = name.lower().strip()
    # Remove common legal suffixes
    for suffix in [
        "limited", "ltd.", "ltd", "inc.", "inc", "incorporated",
        "corp.", "corp", "corporation", "plc", "p.l.c.",
        "有限公司", "有限公司", "limited liability company", "llc",
        "holdings", "group", "international",
    ]:
        name = name.replace(suffix, "")
    # Remove extra whitespace
    name = " ".join(name.split())
    return name.strip()


def link_funds_to_managers(conn) -> Dict[str, int]:
    """Match fund manager names to licensed corporations and create links.

    Uses normalized name matching, including Chinese name matching.
    Returns summary of links created.
    """
    funds_df = get_funds(conn, is_active=True, limit=10000)
    managers_df = get_managers(conn, license_status="active", limit=5000)

    if funds_df.empty or managers_df.empty:
        logger.info("No funds or managers to link")
        return {"funds_checked": len(funds_df) if not funds_df.empty else 0,
                "managers_available": len(managers_df) if not managers_df.empty else 0,
                "linked": 0, "unmatched": 0}

    # Build manager name index
    manager_names_en = {}
    manager_names_cn = {}
    for _, m in managers_df.iterrows():
        mgr = m.to_dict()
        en_norm = _normalize_company_name(mgr["company_name_en"])
        if en_norm:
            manager_names_en[en_norm] = mgr
        cn_name = (mgr.get("company_name_cn") or "").strip()
        if cn_name:
            manager_names_cn[cn_name] = mgr

    links = []
    linked = 0
    unmatched = 0

    for _, f in funds_df.iterrows():
        fund = f.to_dict()
        fund_id = fund["id"]
        mgr_en = fund.get("fund_manager_name_en")
        if not isinstance(mgr_en, str):
            mgr_en = ""
        mgr_cn = fund.get("fund_manager_name_cn")
        if not isinstance(mgr_cn, str):
            mgr_cn = ""

        matched = None

        # Try exact English match
        mgr_en_norm = _normalize_company_name(mgr_en)
        if mgr_en_norm and mgr_en_norm in manager_names_en:
            matched = manager_names_en[mgr_en_norm]
        # Try exact Chinese match
        elif mgr_cn.strip() and mgr_cn.strip() in manager_names_cn:
            matched = manager_names_cn[mgr_cn.strip()]
        # Try fuzzy English: check if manager name contains the fund manager name
        elif mgr_en_norm:
            for en_norm, mgr in manager_names_en.items():
                if mgr_en_norm in en_norm or en_norm in mgr_en_norm:
                    matched = mgr
                    break

        # Try brand mapping (Luxembourg/Europe → HK affiliate)
        if not matched and mgr_en:
            hk_match = find_hk_manager_by_brand(conn, mgr_en)
            if hk_match:
                mgr_id_matched, mgr_name_matched = hk_match
                matched = {"id": mgr_id_matched, "company_name_en": mgr_name_matched}

        if matched:
            links.append({
                "fund_id": fund_id,
                "manager_id": matched["id"],
                "role": "manager",
                "is_primary": True,
            })
            # Update fund record with manager_id
            conn.execute("UPDATE hk_funds SET fund_manager_id = ? WHERE id = ?",
                        [matched["id"], fund_id])
            linked += 1
        else:
            unmatched += 1
            if mgr_en:
                logger.debug(f"Unmatched manager: {mgr_en}")

    if links:
        upsert_manager_funds(conn, links)
        logger.info(f"Created {len(links)} fund-manager links")

    return {
        "funds_checked": len(funds_df),
        "managers_available": len(managers_df),
        "linked": linked,
        "unmatched": unmatched,
    }


# ═══════════════════════════════════════════════════════════════
#  Enforcement Cross-Referencing
# ═══════════════════════════════════════════════════════════════


def cross_check_enforcement(conn) -> Dict[str, int]:
    """Cross-check managers against SFC/HKMA enforcement in name_screening.

    Queries the existing name_screening table (from app/eco_harness) for
    enforcement actions matching each manager by company name.

    Requires the eco_data database to be at the standard path.
    """
    import duckdb
    from pathlib import Path

    eco_db = str(Path(__file__).resolve().parent.parent / "eco_data.duckdb")

    managers_df = get_managers(conn, license_status="active", limit=5000)
    if managers_df.empty:
        logger.info("No managers to cross-check")
        return {"managers_checked": 0, "managers_flagged": 0, "regulatory_records": 0}

    regulatory_records = []
    flagged = 0

    try:
        eco_conn = duckdb.connect(eco_db, read_only=True)

        for _, m in managers_df.iterrows():
            mgr = m.to_dict()
            mgr_id = mgr["id"]
            names = []
            if mgr.get("company_name_en"):
                names.append(mgr["company_name_en"])
            if mgr.get("company_name_cn"):
                names.append(mgr["company_name_cn"])

            if not names:
                continue

            mgr_flagged = False
            for name in names:
                search_name = name.replace("'", "''")  # SQL escape
                try:
                    rows = eco_conn.execute("""
                        SELECT source, source_uid, name_en, name_cn, name_type,
                               risk_category, countries, source_date, notes
                        FROM name_screening
                        WHERE (name_en ILIKE ? OR name_cn ILIKE ?)
                          AND source IN ('hk_sfc', 'hk_hkma')
                        ORDER BY source_date DESC
                    """, [f"%{search_name}%", f"%{search_name}%"]).fetchall()
                except Exception:
                    continue

                for row in rows:
                    regulatory_records.append({
                        "manager_id": mgr_id,
                        "source": row[0],
                        "source_ref_no": row[1],
                        "action_type": "enforcement",
                        "action_date": row[7].strftime("%Y-%m-%d") if row[7] else None,
                        "penalty_amount_hkd": None,
                        "description_en": str(row[2])[:500] if row[2] else None,
                        "description_cn": str(row[3])[:500] if row[3] else None,
                        "source_url": None,
                    })
                    mgr_flagged = True

            if mgr_flagged:
                flagged += 1

        eco_conn.close()
    except Exception as e:
        logger.warning(f"Could not connect to eco_data DB for enforcement cross-check: {e}")
        # Return whatever we have so far
        return {
            "managers_checked": len(managers_df),
            "managers_flagged": flagged,
            "regulatory_records": len(regulatory_records),
        }

    # Store regulatory records and update manager flags
    if regulatory_records:
        upsert_manager_regulatory(conn, regulatory_records)

        # Update enforcement flags on managers
        for _, m in managers_df.iterrows():
            mgr = m.to_dict()
            count = sum(1 for r in regulatory_records if r["manager_id"] == mgr["id"])
            if count > 0:
                conn.execute("""
                    UPDATE hk_fund_managers
                    SET has_sfc_enforcement_history = true,
                        enforcement_count = ?,
                        last_updated = now()
                    WHERE id = ?
                """, [count, mgr["id"]])

    logger.info(f"Cross-checked {len(managers_df)} managers: "
                f"{flagged} flagged, {len(regulatory_records)} regulatory records")

    return {
        "managers_checked": len(managers_df),
        "managers_flagged": flagged,
        "regulatory_records": len(regulatory_records),
    }


# ═══════════════════════════════════════════════════════════════
#  Main entry points
# ═══════════════════════════════════════════════════════════════


def fetch_managers_daily(date_str: str = None) -> Dict[str, Any]:
    """Fetch SFC licensed corporations, store, link, and cross-check."""
    conn = init_db()
    summary = {"managers_found": 0, "managers_stored": 0,
               "links": {}, "enforcement": {}, "errors": []}
    today = date_str or datetime.now().strftime("%Y-%m-%d")

    log_id = log_fetch_start(conn, today, source="sfc_managers")

    try:
        records = fetch_sfc_licensed_corporations()
        summary["managers_found"] = len(records)

        if records:
            summary["managers_stored"] = upsert_managers(conn, records)
            summary["links"] = link_funds_to_managers(conn)
            summary["enforcement"] = cross_check_enforcement(conn)

        log_fetch_end(conn, log_id, items_checked=len(records),
                      new_items=summary["managers_stored"])

    except Exception as e:
        summary["errors"].append(str(e))
        logger.error(f"Manager fetch failed: {e}")
        log_fetch_end(conn, log_id, status="error", error=str(e))
    finally:
        conn.close()

    return summary


def import_managers_csv(csv_path: str) -> Dict[str, int]:
    """Import manager records from a CSV or Excel file.

    Expected columns (CSV header or Excel first row):
      ce_number, company_name_en, company_name_cn, license_type,
      regulated_activity_1, regulated_activity_4, regulated_activity_9,
      license_status, license_effective_date, business_address, website,
      key_ro_name_en, key_ro_name_cn, ro_count, total_licensed_staff

    Boolean fields accept: true/false, yes/no, 1/0, TRUE/FALSE.
    """
    import pandas as pd
    from pathlib import Path

    path = Path(csv_path)
    if path.suffix.lower() in (".xlsx", ".xls"):
        df = pd.read_excel(csv_path)
    else:
        df = pd.read_csv(csv_path)

    records = []
    for _, row in df.iterrows():
        d = row.to_dict()
        # Normalize boolean fields
        for bool_field in ["regulated_activity_1", "regulated_activity_4", "regulated_activity_9"]:
            val = d.get(bool_field)
            if isinstance(val, str):
                d[bool_field] = val.lower() in ("true", "yes", "1")
            elif isinstance(val, (int, float)):
                d[bool_field] = bool(val)

        records.append(_normalize_manager_record(d))

    conn = init_db()
    try:
        stored = upsert_managers(conn, records)
        logger.info(f"Imported {stored} manager records from {csv_path}")
        return {"imported": len(records), "stored": stored}
    finally:
        conn.close()


def init(db_path=None):
    return fetch_managers_daily()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    if "--link" in sys.argv:
        conn = init_db()
        try:
            result = link_funds_to_managers(conn)
            print(result)
        finally:
            conn.close()
    elif "--enforcement" in sys.argv:
        conn = init_db()
        try:
            result = cross_check_enforcement(conn)
            print(result)
        finally:
            conn.close()
    elif "--import-csv" in sys.argv:
        idx = sys.argv.index("--import-csv")
        path = sys.argv[idx + 1]
        print(import_managers_csv(path))
    else:
        print(fetch_managers_daily())
