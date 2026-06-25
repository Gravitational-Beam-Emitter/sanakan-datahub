"""
HKEX Listed Securities data importer.

Downloads the HKEX ListOfSecurities.xlsx and extracts ISINs for all
exchange-traded products (ETFs, L&I products). Updates hk_funds records
by matching fund names to HKEX-listed security names.

Usage:
    python -m hk_funds.pipeline_hkex_import --fetch
"""

from __future__ import annotations

import io
import logging
import re
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import requests

from hk_funds.config import DB_PATH
from hk_funds.storage import init_db

logger = logging.getLogger("hk_funds.pipeline_hkex")

HKEX_SECURITIES_URL = (
    "https://www.hkex.com.hk/eng/services/trading/securities/"
    "securitieslists/ListOfSecurities.xlsx"
)

_session: Optional[requests.Session] = None


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/125.0.0.0 Safari/537.36",
        })
    return _session


def fetch_hkex_securities() -> List[Dict[str, str]]:
    """Download and parse the HKEX List of Securities XLSX.

    Returns list of dicts with:
        stock_code, name, isin, category, sub_category, board_lot
    """
    session = _get_session()
    logger.info("Downloading HKEX ListOfSecurities.xlsx...")
    resp = session.get(HKEX_SECURITIES_URL, timeout=60)
    resp.raise_for_status()

    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(resp.content), data_only=True)
    sh = wb["ListOfSecurities"]

    securities = []
    for row in sh.iter_rows(min_row=4, values_only=True):
        if not row[0]:
            continue
        stock_code = str(row[0]).strip().zfill(5)
        name = str(row[1]).strip() if row[1] else ""
        category = str(row[2]).strip() if row[2] else ""
        sub_category = str(row[3]).strip() if row[3] else ""
        board_lot = str(row[4]).strip() if row[4] else ""
        isin = str(row[5]).strip() if row[5] else ""

        if not name or not isin or isin in ("None", "ISIN"):
            continue

        securities.append({
            "stock_code": stock_code,
            "name": name,
            "isin": isin,
            "category": category,
            "sub_category": sub_category,
            "board_lot": board_lot,
        })

    logger.info(f"Parsed {len(securities)} HKEX-listed securities")
    return securities


def filter_etp(securities: List[Dict]) -> List[Dict]:
    """Filter to Exchange Traded Products (ETFs + L&I)."""
    return [s for s in securities if "Exchange Traded" in s.get("category", "")]


def match_and_update_funds(conn, etp_list: List[Dict]) -> Dict[str, int]:
    """Match HKEX-listed ETPs to hk_funds records by name and update ISINs.

    Uses multiple matching strategies:
      1. Exact name match
      2. Normalized name match (strip spaces, lowercased)
      3. Keyword/substring match for abbreviated names
    """
    stats = {"matched": 0, "updated": 0, "skipped": 0}

    # Get all funds without ISINs
    funds = conn.execute("""
        SELECT id, fund_name_en, sfc_authorization_no
        FROM hk_funds
        WHERE isin IS NULL OR isin = ''
    """).fetchall()

    fund_names = {r[0]: r[1].strip() for r in funds if r[1]}

    # Also get all funds for name-based lookup (even those with ISINs for dedup)
    all_fund_names = {}
    for r in conn.execute("SELECT id, fund_name_en FROM hk_funds").fetchall():
        if r[1]:
            all_fund_names[r[0]] = r[1].strip()

    def normalize(s: str) -> str:
        """Normalize name for comparison."""
        s = s.lower()
        s = re.sub(r'[^a-z0-9]', '', s)
        return s

    for etp in etp_list:
        hkex_name = etp["name"]
        hkex_norm = normalize(hkex_name)
        isin = etp["isin"]

        matched_id = None

        # Strategy 1: Exact match
        for fid, fname in all_fund_names.items():
            if fname.upper() == hkex_name.upper():
                matched_id = fid
                break

        # Strategy 2: Normalized match
        if not matched_id:
            for fid, fname in all_fund_names.items():
                if normalize(fname) == hkex_norm:
                    matched_id = fid
                    break

        # Strategy 3: Contains match (name in hkex or hkex in name)
        if not matched_id:
            hkex_upper = hkex_name.upper()
            for fid, fname in all_fund_names.items():
                fname_upper = fname.upper()
                if (len(hkex_upper) > 5 and hkex_upper in fname_upper) or \
                   (len(fname_upper) > 5 and fname_upper in hkex_upper):
                    matched_id = fid
                    break

        if matched_id:
            stats["matched"] += 1
            # Check if fund already has ISIN
            existing = conn.execute(
                "SELECT isin FROM hk_funds WHERE id = ?", [matched_id]
            ).fetchone()
            if not existing or not existing[0]:
                conn.execute(
                    "UPDATE hk_funds SET isin = ? WHERE id = ?",
                    [isin, matched_id]
                )
                stats["updated"] += 1
                if stats["updated"] <= 20:
                    logger.info(
                        f"  ISIN updated: {all_fund_names.get(matched_id, '?')[:50]} "
                        f"→ {isin}"
                    )
            else:
                stats["skipped"] += 1

    return stats


def import_hkex_data(date_str: str = None) -> Dict[str, Any]:
    """Main entry: download HKEX list, match ETFs, update ISINs."""
    conn = init_db()
    today = date_str or datetime.now().strftime("%Y-%m-%d")
    summary = {"total_securities": 0, "etp_count": 0, "matched": 0, "updated": 0, "errors": []}

    try:
        # Step 1: Download and parse
        securities = fetch_hkex_securities()
        summary["total_securities"] = len(securities)

        # Step 2: Filter to ETPs
        etps = filter_etp(securities)
        summary["etp_count"] = len(etps)
        logger.info(f"Found {len(etps)} exchange-traded products with ISINs")

        # Step 3: Match and update
        match_stats = match_and_update_funds(conn, etps)
        summary.update(match_stats)

        logger.info(
            f"HKEX import: matched={match_stats['matched']}, "
            f"ISINs updated={match_stats['updated']}, "
            f"skipped={match_stats['skipped']}"
        )

    except Exception as e:
        summary["errors"].append(str(e))
        logger.error(f"HKEX import failed: {e}")
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
        result = import_hkex_data()
        print(f"Total securities: {result['total_securities']}")
        print(f"ETPs: {result['etp_count']}")
        print(f"Matched to hk_funds: {result['matched']}")
        print(f"ISINs updated: {result['updated']}")
        if result["errors"]:
            print(f"Errors: {result['errors']}")
    else:
        print("Usage: python -m hk_funds.pipeline_hkex_import --fetch")
