"""
SFC Open-ended Fund Company (OFC) Register pipeline.

Fetches the SFC OFC public register, parses all OFC umbrella + sub-fund
entries, and stores them appropriately:

  - Public OFCs → hk_funds table (SFC-authorized, distributable to retail)
  - Private OFCs → hk_non_authorized_funds table (PI-only, $8M HKD portfolio)

Also runs classification and risk rating on stored funds.

Usage:
    python -m hk_funds.pipeline_ofc --fetch        # Fetch and store OFC data
    python -m hk_funds.pipeline_ofc --rate-all      # Run risk rating on all OFCs
    python -m hk_funds.pipeline_ofc --init           # Fetch + classify + rate
"""

from __future__ import annotations

import logging
import re
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import requests

from hk_funds.config import SFC_REQUEST_DELAY, SFC_TIMEOUT
from hk_funds.storage import (
    init_db,
    upsert_funds,
    upsert_non_authorized_funds,
    get_funds,
    get_non_authorized_funds,
    log_fetch_start,
    log_fetch_end,
)

logger = logging.getLogger("hk_funds.pipeline_ofc")

SFC_OFC_REGISTER_URL = "https://apps.sfc.hk/productlistWeb/searchProduct/OFC.do?lang=EN"

_session: Optional[requests.Session] = None


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/125.0.0.0 Safari/537.36",
            "Accept": "text/html, */*",
            "Accept-Language": "en-US,en;q=0.9,zh-HK;q=0.8,zh;q=0.7",
        })
    return _session


# ═══════════════════════════════════════════════════════════════
#  HTML Fetcher
# ═══════════════════════════════════════════════════════════════

def fetch_ofc_page() -> str:
    """Download the SFC OFC register page HTML. Returns raw HTML string."""
    session = _get_session()
    logger.info("Fetching SFC OFC register from %s", SFC_OFC_REGISTER_URL)
    resp = session.get(SFC_OFC_REGISTER_URL, timeout=SFC_TIMEOUT)
    resp.raise_for_status()
    if len(resp.text) < 10000:
        raise RuntimeError(f"OFC page too short ({len(resp.text)} bytes) — likely blocked or empty")
    logger.info("Downloaded OFC page: %d bytes", len(resp.text))
    return resp.text


# ═══════════════════════════════════════════════════════════════
#  HTML Parser
# ═══════════════════════════════════════════════════════════════

def parse_ofc_html(html: str) -> Tuple[List[Dict], List[Dict]]:
    """Parse OFC register HTML into umbrella OFCs and sub-fund records.

    The SFC OFC table has 5 columns (with Investment Manager commented out):
      col 0: OFC name with CE number   e.g. "Junxin Global Capital OFC (BXX086)"
      col 1: Sub-fund name with CE     e.g. "Junxin CY Global Fund (BXX087)" or empty
      col 2: [commented out] Investment Manager
      col 3: Private / Public
      col 4: Registration / Authorisation Date (DD/MM/YYYY)

    The HTML for col 3+4 is malformed: <td>Private<td>21/5/2026</td></td>
    We strip comments first, then parse the remaining tds.

    Returns:
        (umbrella_ofcs, sub_fund_records)
        - umbrella_ofcs: list of dicts with OFC metadata
        - sub_fund_records: list of fund record dicts ready for storage
    """
    # Find the second <table> element (the data table)
    tables = re.findall(r'<table[^>]*>(.*?)</table>', html, re.DOTALL)
    if len(tables) < 2:
        raise RuntimeError(f"Expected at least 2 tables, found {len(tables)}")

    tbody = tables[1]
    rows = re.findall(r'<tr>(.*?)</tr>', tbody, re.DOTALL)
    logger.info("Found %d rows in OFC data table", len(rows))

    ce_pattern = re.compile(r'\(([A-Z]{3}\d{3})\)')
    umbrella_ofcs: Dict[str, dict] = {}  # keyed by CE number
    sub_fund_records: List[dict] = []

    for row_html in rows:
        # Strip HTML comments first (avoids matching tds inside <!-- -->)
        row_no_comments = re.sub(r'<!--.*?-->', '', row_html, flags=re.DOTALL)

        # Extract td contents
        tds = re.findall(r'<td[^>]*>(.*?)</td>', row_no_comments, re.DOTALL)

        # Extract manager from original HTML (inside comment)
        mgr_match = re.search(r'<!--\s*<td[^>]*>(.*?)</td>\s*-->', row_html)
        manager = mgr_match.group(1).strip() if mgr_match else ''

        if len(tds) < 2:
            continue  # skip header / empty rows

        # Clean td text (strip nested HTML tags)
        ofc_raw = re.sub(r'<[^>]+>', '', tds[0]).strip()
        subfund_raw = re.sub(r'<[^>]+>', '', tds[1]).strip() if len(tds) > 1 else ''
        rest_raw = re.sub(r'<[^>]+>', '', tds[2]).strip() if len(tds) > 2 else ''

        # Extract CE numbers
        ofc_ce_list = ce_pattern.findall(ofc_raw)
        subfund_ce_list = ce_pattern.findall(subfund_raw)
        ofc_ce = ofc_ce_list[0] if ofc_ce_list else ''
        subfund_ce = subfund_ce_list[0] if subfund_ce_list else ''

        # Clean names (remove CE number suffix)
        ofc_name = re.sub(r'\s*\([A-Z]{3}\d{3}\)\s*$', '', ofc_raw).strip()
        subfund_name = re.sub(r'\s*\([A-Z]{3}\d{3}\)\s*$', '', subfund_raw).strip() if subfund_raw else ''

        # Parse Private/Public indicator and date
        pub_priv = 'Unknown'
        date_str = ''
        if rest_raw:
            if 'Private' in rest_raw:
                pub_priv = 'Private'
            elif 'Public' in rest_raw:
                pub_priv = 'Public'
            date_match = re.search(r'(\d{1,2}/\d{1,2}/\d{4})', rest_raw)
            if date_match:
                date_str = date_match.group(1)

        # Parse date to ISO format
        iso_date = _parse_ofc_date(date_str)

        # Register umbrella OFC
        if ofc_ce and ofc_ce not in umbrella_ofcs:
            umbrella_ofcs[ofc_ce] = {
                "name": ofc_name,
                "ce": ofc_ce,
                "manager": manager,
                "pub_priv": pub_priv,
                "date": iso_date,
                "date_raw": date_str,
            }

        # Create sub-fund record (or umbrella record if no sub-fund)
        if subfund_raw.strip():
            sub_fund_records.append({
                "fund_name_en": subfund_name,
                "ce_number": subfund_ce,
                "umbrella_ofc_name": ofc_name,
                "umbrella_ofc_ce": ofc_ce,
                "fund_manager_name_en": manager,
                "pub_priv": pub_priv,
                "registration_date": iso_date,
                "is_sub_fund": True,
            })
        else:
            # Umbrella OFC without sub-funds — still track it
            sub_fund_records.append({
                "fund_name_en": ofc_name,
                "ce_number": ofc_ce,
                "umbrella_ofc_name": None,
                "umbrella_ofc_ce": None,
                "fund_manager_name_en": manager,
                "pub_priv": pub_priv,
                "registration_date": iso_date,
                "is_sub_fund": False,
            })

    logger.info(
        "Parsed %d umbrella OFCs + %d total records (%d sub-funds)",
        len(umbrella_ofcs),
        len(sub_fund_records),
        sum(1 for r in sub_fund_records if r["is_sub_fund"]),
    )
    return list(umbrella_ofcs.values()), sub_fund_records


def _parse_ofc_date(date_str: str) -> Optional[str]:
    """Parse DD/MM/YYYY date from OFC register to ISO format."""
    if not date_str:
        return None
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


# ═══════════════════════════════════════════════════════════════
#  Storage
# ═══════════════════════════════════════════════════════════════

def store_ofc_records(sub_fund_records: List[dict]) -> Dict[str, int]:
    """Store OFC records in the appropriate database table.

    Public OFCs → hk_funds (SFC-authorized, retail-distributable)
    Private OFCs → hk_non_authorized_funds (PI-only, $8M HKD portfolio)

    Returns counts by destination.
    """
    conn = init_db()

    public_records = []
    private_records = []

    for r in sub_fund_records:
        if r["pub_priv"] == "Public":
            public_records.append(_to_hk_fund_record(r))
        else:
            private_records.append(_to_non_auth_record(r))

    result = {"public_stored": 0, "private_stored": 0, "public_total": len(public_records),
              "private_total": len(private_records)}

    if public_records:
        result["public_stored"] = upsert_funds(conn, public_records)

    if private_records:
        result["private_stored"] = upsert_non_authorized_funds(conn, private_records)

    # Update private OFC count tracking
    public_umb = sum(1 for r in sub_fund_records if r["pub_priv"] == "Public" and not r["is_sub_fund"])
    private_umb = sum(1 for r in sub_fund_records if r["pub_priv"] == "Private" and not r["is_sub_fund"])
    public_sf = sum(1 for r in sub_fund_records if r["pub_priv"] == "Public" and r["is_sub_fund"])
    private_sf = sum(1 for r in sub_fund_records if r["pub_priv"] == "Private" and r["is_sub_fund"])

    logger.info(
        "OFC storage complete — Public: %d umbrellas + %d sub-funds, "
        "Private: %d umbrellas + %d sub-funds",
        public_umb, public_sf, private_umb, private_sf,
    )

    conn.close()
    return result


def _to_hk_fund_record(r: dict) -> dict:
    """Convert an OFC sub-fund record to hk_funds schema."""
    return {
        "sfc_authorization_no": r.get("ce_number", ""),
        "fund_name_en": r.get("fund_name_en", ""),
        "fund_type": "open_ended_fund_company",
        "domicile": "Hong Kong",
        "fund_manager_name_en": r.get("fund_manager_name_en", ""),
        "umbrella_fund_name": r.get("umbrella_ofc_name"),
        "umbrella_fund_ce": r.get("umbrella_ofc_ce"),
        "authorization_date": r.get("registration_date"),
        "is_derivative_product": False,
        "is_complex_product": False,
        "complex_product_type": "non_complex",
        "classification_source": "ofc_register",
        "is_active": True,
        "source_url": SFC_OFC_REGISTER_URL,
    }


def _to_non_auth_record(r: dict) -> dict:
    """Convert an OFC sub-fund record to hk_non_authorized_funds schema."""
    return {
        "fund_name_en": r.get("fund_name_en", ""),
        "fund_type": "open_ended_fund_company",
        "domicile": "Hong Kong",
        "fund_manager_name_en": r.get("fund_manager_name_en", ""),
        "distribution_restriction": "pi_only",
        "is_active": True,
        "data_source": "sfc_ofc_register",
        "notes": f"OFC: {r.get('umbrella_ofc_name', '')} (CE: {r.get('umbrella_ofc_ce', '')})"
                f" | Registered: {r.get('registration_date', '')}",
    }


# ═══════════════════════════════════════════════════════════════
#  Risk Rating for Non-Authorized OFCs
# ═══════════════════════════════════════════════════════════════

def rate_ofc_funds() -> dict:
    """Run risk rating on all OFC funds (both public and private).

    For public OFCs in hk_funds: use the standard rate_all_funds() path.
    For private OFCs in hk_non_authorized_funds: run a simplified rating.
    """
    from hk_funds.risk_rating import calculate_fund_risk_rating
    from hk_funds.storage import upsert_fund_risk_rating

    conn = init_db()
    summary = {"public_rated": 0, "private_rated": 0, "errors": []}

    try:
        # Rate public OFCs (in hk_funds)
        funds_df = get_funds(conn, fund_type="open_ended_fund_company", is_active=True, limit=10000)
        for _, row in funds_df.iterrows():
            fund = row.to_dict()
            try:
                rating = calculate_fund_risk_rating(fund)
                upsert_fund_risk_rating(conn, fund["id"], rating)
                summary["public_rated"] += 1
            except Exception as e:
                summary["errors"].append(f"Public OFC {fund.get('sfc_authorization_no', '?')}: {e}")

        logger.info("Rated %d public OFC funds", summary["public_rated"])

        # Rate private OFCs (in hk_non_authorized_funds)
        # These don't have a risk_ratings table entry, but we can log the rating
        na_df = get_non_authorized_funds(conn, is_active=True, limit=10000)
        private_ofcs = na_df[na_df["data_source"] == "sfc_ofc_register"] if not na_df.empty else na_df

        for _, row in private_ofcs.iterrows():
            fund = row.to_dict()
            try:
                # Build a minimal fund dict for the rating engine
                fund_for_rating = {
                    "fund_name_en": fund.get("fund_name_en", ""),
                    "fund_type": fund.get("fund_type", "open_ended_fund_company"),
                    "domicile": fund.get("domicile", "Hong Kong"),
                    "is_derivative_product": False,
                    "is_complex_product": False,
                    "complex_product_type": "non_complex",
                }
                rating = calculate_fund_risk_rating(fund_for_rating)
                # For private OFCs, store rating info in notes with full breakdown
                current_notes = fund.get("notes") or ""
                # Remove any previously appended risk data (idempotent re-run)
                risk_idx = current_notes.find(" | Risk:")
                if risk_idx >= 0:
                    current_notes = current_notes[:risk_idx]
                rating_note = (
                    f" | Risk: {rating['risk_category']} "
                    f"(score: {rating['overall_risk_score']})"
                    f" | RiskBreakdown: {rating['score_breakdown']}"
                )
                conn.execute(
                    "UPDATE hk_non_authorized_funds SET notes = ? WHERE id = ?",
                    [current_notes + rating_note, fund["id"]],
                )
                summary["private_rated"] += 1
            except Exception as e:
                summary["errors"].append(f"Private OFC {fund.get('fund_name_en', '?')}: {e}")

        logger.info("Rated %d private OFC funds", summary["private_rated"])

    finally:
        conn.close()

    return summary


# ═══════════════════════════════════════════════════════════════
#  Classification for OFC Funds
# ═══════════════════════════════════════════════════════════════

def classify_ofc_funds() -> dict:
    """Run classification engine on all OFC funds in hk_funds table."""
    from hk_funds.pipeline_funds import classify_all_funds

    conn = init_db()
    try:
        result = classify_all_funds(conn)
        logger.info("OFC classification: %s", result)
        return result
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════
#  KYP Initialization for OFC Funds
# ═══════════════════════════════════════════════════════════════

def init_kyp_for_ofc_funds() -> dict:
    """Initialize KYP dimensions for all OFC funds (public + private).

    Public OFCs use their hk_funds ID directly.
    Private OFCs use -(id) - 100000 to avoid collision with SFC fund IDs
    in the shared hk_kyp_dimensions table.
    """
    from hk_funds.storage import init_kyp_dimensions

    conn = init_db()
    summary = {"kyp_public": 0, "kyp_private": 0, "errors": []}

    try:
        # Public OFCs (in hk_funds)
        funds_df = get_funds(conn, fund_type="open_ended_fund_company", is_active=True, limit=10000)
        for _, row in funds_df.iterrows():
            try:
                init_kyp_dimensions(conn, row["id"])
                summary["kyp_public"] += 1
            except Exception as e:
                summary["errors"].append(f"Public OFC {row.get('fund_name_en', '?')}: {e}")

        logger.info("KYP initialized for %d public OFC funds", summary["kyp_public"])

        # Private OFCs (in hk_non_authorized_funds)
        na_df = get_non_authorized_funds(conn, is_active=True, limit=10000)
        private_ofcs = na_df[na_df["data_source"] == "sfc_ofc_register"] if not na_df.empty else na_df

        for _, row in private_ofcs.iterrows():
            try:
                # Use negated ID to avoid collision with SFC fund IDs
                kyp_id = -int(row["id"]) - 100000
                init_kyp_dimensions(conn, kyp_id)
                summary["kyp_private"] += 1
            except Exception as e:
                summary["errors"].append(f"Private OFC {row.get('fund_name_en', '?')}: {e}")

        logger.info("KYP initialized for %d private OFC funds", summary["kyp_private"])
    finally:
        conn.close()

    return summary


# ═══════════════════════════════════════════════════════════════
#  Main Entry Points
# ═══════════════════════════════════════════════════════════════

def fetch_ofc_daily(date_str: str = None) -> Dict[str, Any]:
    """Fetch OFC register, parse, store. Returns summary."""
    conn = init_db()
    today = date_str or datetime.now().strftime("%Y-%m-%d")
    log_id = log_fetch_start(conn, today, source="sfc_ofc_register")
    summary = {"umbrellas": 0, "records": 0, "stored": {}, "errors": []}

    try:
        html = fetch_ofc_page()
        umbrellas, records = parse_ofc_html(html)
        summary["umbrellas"] = len(umbrellas)
        summary["records"] = len(records)

        stored = store_ofc_records(records)
        summary["stored"] = stored

        items_total = stored.get("public_total", 0) + stored.get("private_total", 0)
        items_stored = stored.get("public_stored", 0) + stored.get("private_stored", 0)
        log_fetch_end(conn, log_id,
                      items_checked=len(records),
                      new_items=items_stored)

    except Exception as e:
        summary["errors"].append(str(e))
        logger.error("OFC fetch failed: %s", e)
        log_fetch_end(conn, log_id, status="error", error=str(e))
    finally:
        conn.close()

    return summary


def init_ofc_pipeline() -> dict:
    """Full OFC pipeline: fetch → store → classify → rate → KYP."""
    logger.info("=== OFC Pipeline: Starting full initialization ===")

    # Step 1: Fetch and store
    fetch_result = fetch_ofc_daily()
    if fetch_result.get("errors"):
        logger.error("OFC fetch had errors: %s", fetch_result["errors"])
        if not fetch_result.get("records"):
            return {"status": "error", "fetch": fetch_result}

    # Step 2: Classify
    classify_result = classify_ofc_funds()

    # Step 3: Risk rate
    rate_result = rate_ofc_funds()

    # Step 4: KYP
    kyp_result = init_kyp_for_ofc_funds()

    return {
        "status": "ok",
        "fetch": fetch_result,
        "classify": classify_result,
        "risk_rating": rate_result,
        "kyp": kyp_result,
    }


# ═══════════════════════════════════════════════════════════════
#  Database Stats
# ═══════════════════════════════════════════════════════════════

def get_ofc_stats() -> dict:
    """Return OFC-specific statistics from the database."""
    conn = init_db(read_only=True)
    try:
        public_count = conn.execute(
            "SELECT COUNT(*) FROM hk_funds WHERE fund_type = 'open_ended_fund_company' AND is_active = true"
        ).fetchone()[0]

        public_umb = conn.execute(
            "SELECT COUNT(*) FROM hk_funds WHERE fund_type = 'open_ended_fund_company' "
            "AND is_active = true AND umbrella_fund_ce IS NULL"
        ).fetchone()[0]

        public_sf = public_count - public_umb

        private_count = conn.execute(
            "SELECT COUNT(*) FROM hk_non_authorized_funds "
            "WHERE data_source = 'sfc_ofc_register' AND is_active = true"
        ).fetchone()[0]

        risk_rated = conn.execute(
            "SELECT COUNT(*) FROM hk_fund_risk_ratings r "
            "JOIN hk_funds f ON f.id = r.fund_id "
            "WHERE f.fund_type = 'open_ended_fund_company'"
        ).fetchone()[0]

        # Manager count from OFC data
        public_managers = conn.execute(
            "SELECT COUNT(DISTINCT fund_manager_name_en) FROM hk_funds "
            "WHERE fund_type = 'open_ended_fund_company' AND fund_manager_name_en IS NOT NULL"
        ).fetchone()[0]

        private_managers = conn.execute(
            "SELECT COUNT(DISTINCT fund_manager_name_en) FROM hk_non_authorized_funds "
            "WHERE data_source = 'sfc_ofc_register' AND fund_manager_name_en IS NOT NULL"
        ).fetchone()[0]

        return {
            "public_ofcs": {"total": public_count, "umbrellas": public_umb, "sub_funds": public_sf},
            "private_ofcs": {"total": private_count},
            "risk_rated": risk_rated,
            "unique_managers": {"public": public_managers, "private": private_managers},
        }
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    if "--fetch" in sys.argv:
        result = fetch_ofc_daily()
        print(f"Fetched: {result['umbrellas']} umbrellas, {result['records']} records")
        print(f"Stored: {result['stored']}")

    elif "--rate-all" in sys.argv:
        result = rate_ofc_funds()
        print(f"Rated: {result}")

    elif "--stats" in sys.argv:
        stats = get_ofc_stats()
        import json
        print(json.dumps(stats, indent=2))

    elif "--init" in sys.argv:
        result = init_ofc_pipeline()
        import json
        print(json.dumps(result, indent=2, default=str))

    elif "--classify" in sys.argv:
        result = classify_ofc_funds()
        print(f"Classified: {result}")

    elif "--kyp" in sys.argv:
        result = init_kyp_for_ofc_funds()
        print(f"KYP initialized: {result}")

    else:
        # Default: fetch only
        result = fetch_ofc_daily()
        print(f"Fetched: {result['umbrellas']} umbrellas, {result['records']} records")
        print(f"Stored: {result['stored']}")
