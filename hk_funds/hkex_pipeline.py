"""
HKEX Securities List pipeline — ETF, L&I product, REIT importer.

Downloads the HKEX ListOfSecurities.xlsx daily, extracts fund-related
entries, and loads them into the hk_funds database with V4 dual-dimension
classification (§5.1A derivative + §5.5 complex).

Usage:
    python -m hk_funds.hkex_pipeline --init
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

from hk_funds.config import DB_PATH
from hk_funds.storage import init_db, upsert_funds, log_fetch_start, log_fetch_end

logger = logging.getLogger("hk_funds.hkex_pipeline")

HKEX_XLSX_URL = "https://www.hkex.com.hk/eng/services/trading/securities/securitieslists/ListOfSecurities.xlsx"

# ETF name patterns that help identify the management company
MANAGER_PREFIXES = [
    "ISHARES", "CSOP", "BMO", "VANGUARD", "ICBCCS", "CHINAAMC", "CICC",
    "PREMIA", "SAMSUNG", "MIRAE", "GLOBAL X", "FUBON", "VALUE",
    "HANG SENG", "BOSERA", "E FUND", "HARVEST", "DA HENG", "PING AN",
    "ABC", "ICBC", "BANK OF CHINA", "BOC", "CAM", "HAITONG",
    "PHILLIP", "NOMURA", "BARCLAYS", "LYXOR", "DB X-TRACKERS",
    "XTRACKERS", "INVESCO", "STATE STREET", "SPDR", "FRANKLIN",
    "JPMORGAN", "J.P. MORGAN", "UBS", "CREDIT SUISSE", "ABC",
]


def download_hkex_xlsx(session: requests.Session) -> Optional[bytes]:
    """Download the HKEX List of Securities XLSX file."""
    logger.info("Downloading HKEX securities list...")
    try:
        resp = session.get(HKEX_XLSX_URL, timeout=60,
                          headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        logger.info(f"Downloaded {len(resp.content)} bytes")
        return resp.content
    except Exception as e:
        logger.error(f"Failed to download HKEX XLSX: {e}")
        return None


def _classify_hkex_product(name: str, fund_type: str, structure: str) -> Dict[str, Any]:
    """V4 dual-dimension classification for HKEX-listed products.

    Returns dict with is_derivative_product, is_complex_product,
    complex_product_type, classification_reason.
    """
    name_upper = name.upper()

    if fund_type == "leveraged_inverse_product":
        # L&I products: derivative (intrinsic leverage) + complex (loss > principal)
        if "INVERSE" in name_upper or "反向" in name:
            cpt = "L&I"
            reason = "HKEX listed inverse product"
        elif "LEVERAGED" in name_upper or "杠杆" in name:
            cpt = "L&I"
            reason = "HKEX listed leveraged product"
        else:
            cpt = "L&I"
            reason = "HKEX listed leveraged/inverse product"
        return {
            "is_derivative_product": True,
            "is_complex_product": True,
            "complex_product_type": cpt,
            "classification_reason": reason,
            "classification_source": "hkex_list",
        }

    elif fund_type == "etf":
        if structure == "futures_etf":
            return {
                "is_derivative_product": True,
                "is_complex_product": True,
                "complex_product_type": "futures_etf",
                "classification_reason": "HKEX listed futures-based ETF",
                "classification_source": "hkex_list",
            }
        elif structure == "synthetic_etf":
            return {
                "is_derivative_product": True,
                "is_complex_product": True,
                "complex_product_type": "synthetic_etf",
                "classification_reason": "HKEX listed synthetic ETF (swap-based)",
                "classification_source": "hkex_list",
            }
        elif structure == "physical_etf":
            # Covered call ETFs use options — derivative strategy
            if "COVERED CALL" in name_upper or "BUY WRITE" in name_upper:
                return {
                    "is_derivative_product": True,
                    "is_complex_product": True,
                    "complex_product_type": "derivative_fund",
                    "classification_reason": "HKEX listed covered call ETF (writes options)",
                    "classification_source": "hkex_list",
                }
            # Crypto spot ETFs — hard-to-value underlying
            if any(kw in name_upper for kw in ("BITCOIN", "ETHER", "VIRTUAL ASSET")):
                return {
                    "is_derivative_product": False,
                    "is_complex_product": True,
                    "complex_product_type": "complex_bond",  # closest match for tokenized
                    "classification_reason": "HKEX listed virtual asset spot ETF (hard-to-value underlying)",
                    "classification_source": "hkex_list",
                }
            return {
                "is_derivative_product": False,
                "is_complex_product": False,
                "complex_product_type": "non_complex",
                "classification_reason": "HKEX listed physical ETF",
                "classification_source": "hkex_list",
            }

    elif fund_type == "reit":
        return {
            "is_derivative_product": False,
            "is_complex_product": False,
            "complex_product_type": "non_complex",
            "classification_reason": "HKEX listed REIT",
            "classification_source": "hkex_list",
        }

    return {
        "is_derivative_product": False,
        "is_complex_product": False,
        "complex_product_type": "non_complex",
        "classification_reason": None,
        "classification_source": "hkex_list",
    }


def extract_fund_products(xlsx_data: bytes) -> List[Dict[str, Any]]:
    """Parse ETF, L&I, and REIT entries from the HKEX XLSX."""
    try:
        import openpyxl
        from io import BytesIO
        wb = openpyxl.load_workbook(BytesIO(xlsx_data), data_only=True)
        ws = wb["ListOfSecurities"]
    except Exception as e:
        logger.error(f"Failed to parse XLSX: {e}")
        return []

    records = []
    for row in ws.iter_rows(min_row=4, values_only=True):
        if row[0] is None:
            continue

        code = str(row[0]).strip()
        name = (str(row[1]).strip()) if row[1] else ""
        cat = str(row[2]).strip() if row[2] else ""
        subcat = str(row[3]).strip() if row[3] else ""
        isin = str(row[5]).strip() if row[5] else ""
        expiry = str(row[6]).strip() if row[6] else ""
        trading_ccy = str(row[16]).strip() if len(row) > 16 and row[16] else ""

        # Only import fund-related products
        fund_type = None
        if cat == "Exchange Traded Products" and subcat == "Exchange Traded Funds":
            fund_type = "etf"
        elif cat == "Exchange Traded Products" and "Leveraged" in subcat:
            fund_type = "leveraged_inverse_product"
        elif cat == "Real Estate Investment Trusts":
            fund_type = "reit"
        else:
            continue

        # Determine fund structure
        if fund_type == "etf":
            name_upper = name.upper()
            if "FUTURES" in name_upper:
                structure = "futures_etf"
            elif any(kw in name_upper for kw in
                     ["SYNTHETIC", "SWAP", "X CSI 300", "X FTSE CHINA"]):
                structure = "synthetic_etf"
            else:
                structure = "physical_etf"
        elif fund_type == "leveraged_inverse_product":
            if "INVERSE" in name.upper() or "反向" in name:
                structure = "inverse_product"
            elif "LEVERAGED" in name.upper() or "杠杆" in name:
                structure = "leveraged_product"
            else:
                structure = "leveraged_inverse_product"
        else:
            structure = fund_type

        # V4 classification
        classification = _classify_hkex_product(name, fund_type, structure)

        manager_name = _extract_manager(name)

        record = {
            "sfc_authorization_no": f"HKEX-{code}",
            "fund_name_en": name,
            "fund_name_cn": "",
            "fund_type": fund_type,
            "fund_structure": structure,
            "domicile": "Hong Kong",
            "currency": trading_ccy or "HKD",
            "isin": isin,
            "launch_date": None,
            "authorization_date": None,
            "fund_manager_name_en": manager_name,
            "fund_manager_name_cn": "",
            # V4 dual-dimension fields
            "is_derivative_product": classification["is_derivative_product"],
            "is_complex_product": classification["is_complex_product"],
            "complex_product_type": classification["complex_product_type"],
            "classification_reason": classification["classification_reason"],
            "classification_source": classification["classification_source"],
            "is_active": not expiry or expiry == "",
            "source_url": HKEX_XLSX_URL,
        }
        records.append(record)

    wb.close()
    return records


def _extract_manager(name: str) -> str:
    """Try to extract management company name from ETF name."""
    name_upper = name.upper()
    for prefix in MANAGER_PREFIXES:
        if name_upper.startswith(prefix):
            idx = len(prefix)
            return name[:idx]
    parts = name.split()
    if len(parts) >= 2 and parts[0].isalpha():
        return parts[0]
    return ""


def fetch_hkex_daily(date_str: str = None) -> Dict[str, Any]:
    """Download HKEX list, extract fund products, store in DB."""
    conn = init_db(DB_PATH)
    summary = {"funds_found": 0, "funds_stored": 0, "errors": []}
    today = date_str or datetime.now().strftime("%Y-%m-%d")

    log_id = log_fetch_start(conn, today, source="hkex_list")

    session = requests.Session()
    try:
        xlsx_data = download_hkex_xlsx(session)
        if xlsx_data:
            records = extract_fund_products(xlsx_data)
            summary["funds_found"] = len(records)

            if records:
                summary["funds_stored"] = upsert_funds(conn, records)
                logger.info(f"Stored {summary['funds_stored']} HKEX fund products")

                # Log breakdown
                deriv = sum(1 for r in records if r.get("is_derivative_product"))
                complex_ = sum(1 for r in records if r.get("is_complex_product"))
                logger.info(f"HKEX classification: {deriv} derivative, {complex_} complex")

            log_fetch_end(conn, log_id, items_checked=len(records),
                          new_items=summary["funds_stored"])
    except Exception as e:
        summary["errors"].append(str(e))
        logger.error(f"HKEX fetch failed: {e}")
        log_fetch_end(conn, log_id, status="error", error=str(e))
    finally:
        session.close()
        conn.close()

    return summary


def init(db_path=None):
    return fetch_hkex_daily()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    print(fetch_hkex_daily())
