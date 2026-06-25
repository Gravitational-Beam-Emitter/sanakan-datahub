"""
SFC Structured Investment Products (SIP) pipeline.

Scrapes the SFC productlistWeb SIP register for all authorized unlisted
structured investment products (equity-linked investments, equity-linked
deposits, structured notes, etc.). These are all §5.5 complex products.

Usage:
    python -m hk_funds.sip_pipeline --init
"""

from __future__ import annotations

import logging
import re
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

from hk_funds.config import DB_PATH
from hk_funds.storage import init_db, upsert_funds, log_fetch_start, log_fetch_end

logger = logging.getLogger("hk_funds.sip_pipeline")

SIP_SEARCH_URL = "https://apps.sfc.hk/productlistWeb/searchProduct/SIP.do"
SIP_DOC_BASE = "https://apps.sfc.hk/productlistWeb/searchProduct/"

# Structured products are inherently complex (§5.5) and derivative
# (value derives from underlying assets/securities/baskets).
SIP_CLASSIFICATION = {
    "is_derivative_product": True,
    "is_complex_product": True,
    "complex_product_type": "structured",
    "classification_reason": "SFC-authorized unlisted structured investment product (§5.5 complex)",
    "classification_source": "sfc_sip",
}


def _get_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0"})
    s.get(SIP_SEARCH_URL + "?lang=EN", timeout=30)
    return s


def fetch_sip_list(session: requests.Session) -> List[Dict[str, Any]]:
    """POST searchBy=COMPANY to get all structured investment products."""
    logger.info("Fetching SFC structured investment products list...")
    try:
        resp = session.post(
            SIP_SEARCH_URL,
            data={
                "searchBy": "COMPANY",
                "sortBy": "NAME",
                "lang": "EN",
                "radioSearchBy": "COMPANY",
            },
            timeout=60,
        )
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"Failed to fetch SIP list: {e}")
        return []

    html = resp.text
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL)
    records = []

    for r in rows:
        tds = re.findall(r"<td[^>]*>(.*?)</td>", r, re.DOTALL)
        if len(tds) < 5:
            continue

        issuer = re.sub(r"<[^>]+>", "", tds[0]).strip()
        programme = re.sub(r"<[^>]+>", "", tds[1]).strip()
        product = re.sub(r"<[^>]+>", "", tds[2]).strip()
        auth_date_raw = re.sub(r"<[^>]+>", "", tds[3]).strip()

        if not issuer or "Introduction" in issuer:
            continue

        # Extract itemId from onclick handler
        item_id = None
        onclick_match = re.search(r"itemId=(\w+)", tds[4])
        if onclick_match:
            item_id = onclick_match.group(1)

        # Parse authorization date (DD/MM/YYYY)
        auth_date = None
        date_match = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", auth_date_raw)
        if date_match:
            auth_date = f"{date_match.group(3)}-{int(date_match.group(2)):02d}-{int(date_match.group(1)):02d}"

        sfc_auth_no = f"SIP-{item_id}" if item_id else f"SIP-{product[:50]}"

        record = {
            "sfc_authorization_no": sfc_auth_no,
            "fund_name_en": product,
            "fund_name_cn": "",
            "fund_type": "structured_product",
            "fund_structure": "unlisted_structured_product",
            "domicile": "Hong Kong",
            "currency": "HKD",
            "isin": None,
            "launch_date": auth_date,
            "authorization_date": auth_date,
            "fund_manager_name_en": issuer,
            "fund_manager_name_cn": "",
            "umbrella_fund_name": programme,
            "is_derivative_product": SIP_CLASSIFICATION["is_derivative_product"],
            "is_complex_product": SIP_CLASSIFICATION["is_complex_product"],
            "complex_product_type": SIP_CLASSIFICATION["complex_product_type"],
            "classification_reason": SIP_CLASSIFICATION["classification_reason"],
            "classification_source": SIP_CLASSIFICATION["classification_source"],
            "is_active": True,
            "source_url": SIP_SEARCH_URL,
            "source_type": "sfc_sip",
        }
        records.append(record)

    logger.info(f"Parsed {len(records)} structured investment products")
    return records


def fetch_sip_daily(date_str: str = None) -> Dict[str, Any]:
    """Download SFC SIP list and store in DB."""
    conn = init_db(DB_PATH)
    summary = {"funds_found": 0, "funds_stored": 0, "errors": []}
    today = date_str or datetime.now().strftime("%Y-%m-%d")

    log_id = log_fetch_start(conn, today, source="sfc_sip")

    session = _get_session()
    try:
        records = fetch_sip_list(session)
        summary["funds_found"] = len(records)

        if records:
            summary["funds_stored"] = upsert_funds(conn, records)
            logger.info(f"Stored {summary['funds_stored']} structured products")

            complex_count = sum(1 for r in records if r.get("is_complex_product"))
            deriv_count = sum(1 for r in records if r.get("is_derivative_product"))
            logger.info(f"SIP classification: {deriv_count} derivative, {complex_count} complex")

        log_fetch_end(conn, log_id, items_checked=len(records),
                      new_items=summary["funds_stored"])
    except Exception as e:
        summary["errors"].append(str(e))
        logger.error(f"SIP fetch failed: {e}")
        log_fetch_end(conn, log_id, status="error", error=str(e))
    finally:
        session.close()
        conn.close()

    return summary


def init(db_path=None):
    return fetch_sip_daily()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    result = fetch_sip_daily()
    print(f"\nDone: found={result['funds_found']}, stored={result['funds_stored']}")
    if result["errors"]:
        print(f"Errors: {result['errors']}")
