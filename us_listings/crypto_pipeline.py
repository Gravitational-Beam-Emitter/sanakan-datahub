"""
Crypto products pipeline — maintain a comprehensive list of crypto-related
US-listed products (ETFs, ETPs, crypto stocks, blockchain ETFs).

Usage:
    python -m us_listings.crypto_pipeline --init     # full refresh from known list
    python -m us_listings.crypto_pipeline             # update enrichment data
    python -m us_listings.crypto_pipeline --scan      # keyword scan SEC tickers
"""

from __future__ import annotations

import logging
import re
import sys
import time
from datetime import datetime, timedelta, date
from typing import Any, Dict, List, Optional

import requests

from us_listings.config import (
    KNOWN_CRYPTO_PRODUCTS,
    CRYPTO_KEYWORDS,
    SEC_HEADERS,
    SEC_COMPANY_TICKERS_URL,
    YFINANCE_RATE_LIMIT,
)
from us_listings.storage import (
    init_db,
    upsert_crypto_products,
    get_all_crypto_products,
    get_crypto_tickers_set,
    mark_listings_as_crypto,
    log_fetch_start,
    log_fetch_end,
    get_crypto_product_count,
)

logger = logging.getLogger("us_listings.crypto_pipeline")


# ── Known List → DB Records ──


def _known_list_to_records() -> List[Dict[str, Any]]:
    """Convert KNOWN_CRYPTO_PRODUCTS config to DB record format."""
    records = []
    for ticker, info in KNOWN_CRYPTO_PRODUCTS.items():
        records.append({
            "ticker": ticker,
            "company_name": f"{info['issuer']} {info['underlying_asset']} {'ETF' if info['product_type'] in ('spot_etf', 'futures_etf') else 'Stock'}",
            "product_type": info["product_type"],
            "underlying_asset": info["underlying_asset"],
            "listing_date": None,
            "expense_ratio": None,
            "aum": None,
            "market_cap": None,
            "description": "",
            "issuer": info["issuer"],
            "is_active": True,
            "data_source": "known_list",
        })
    return records


# ── yfinance Enrichment ──


def _enrich_from_yfinance(ticker: str) -> Dict[str, Any]:
    """Fetch additional info for a ticker from yfinance. Returns dict with enrichable fields."""
    result: Dict[str, Any] = {}
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance not installed, skipping enrichment")
        return result

    try:
        stock = yf.Ticker(ticker)
        info = stock.info or {}

        if info.get("longName"):
            result["company_name"] = str(info["longName"])
        elif info.get("shortName"):
            result["company_name"] = str(info["shortName"])

        if info.get("longBusinessSummary"):
            result["description"] = str(info["longBusinessSummary"])[:2000]

        if info.get("ipoDate"):
            result["listing_date"] = str(info["ipoDate"])

        # Market cap for stocks
        if info.get("marketCap"):
            result["market_cap"] = float(info["marketCap"])

        # AUM for ETFs
        if info.get("totalAssets"):
            result["aum"] = float(info["totalAssets"])

        # Expense ratio
        if info.get("annualReportExpenseRatio"):
            result["expense_ratio"] = float(info["annualReportExpenseRatio"]) * 100
        elif info.get("expenseRatio"):
            result["expense_ratio"] = float(info["expenseRatio"]) * 100

        quote_type = info.get("quoteType", "").lower()
        if quote_type == "etf" and not result.get("product_type"):
            # Detect crypto ETF type
            name = (info.get("longName") or "").lower()
            if "bitcoin" in name or "btc" in name:
                result["product_type"] = "spot_etf"
                result["underlying_asset"] = "Bitcoin"
            elif "ethereum" in name or "eth" in name:
                result["product_type"] = "spot_etf"
                result["underlying_asset"] = "Ethereum"

    except Exception as e:
        logger.debug(f"yfinance enrichment failed for {ticker}: {e}")

    return result


def enrich_all_products(db_path: Optional[str] = None, limit: Optional[int] = None) -> Dict[str, Any]:
    """Enrich crypto products with data from yfinance.

    Only enriches products missing listing_date, aum, or expense_ratio.
    """
    conn = init_db(db_path)
    summary = {"enriched": 0, "failed": 0, "errors": []}

    try:
        products_df = get_all_crypto_products(conn)
        if products_df.empty:
            logger.info("No crypto products to enrich")
            return summary

        enriched_records = []
        for i, (_, row) in enumerate(products_df.iterrows()):
            if limit and i >= limit:
                break

            ticker = row["ticker"]
            needs_enrichment = (
                not row["listing_date"] or
                not row["aum"] or
                not row["company_name"] or
                row["company_name"].endswith(" Stock") or
                row["company_name"].endswith(" ETF")
            )

            if not needs_enrichment:
                continue

            enrichment = _enrich_from_yfinance(ticker)
            if enrichment:
                record = {
                    "ticker": ticker,
                    "company_name": enrichment.get("company_name") or row["company_name"],
                    "product_type": row["product_type"],
                    "underlying_asset": row["underlying_asset"],
                    "listing_date": enrichment.get("listing_date") or row["listing_date"],
                    "expense_ratio": enrichment.get("expense_ratio") or row["expense_ratio"],
                    "aum": enrichment.get("aum") or row["aum"],
                    "market_cap": enrichment.get("market_cap") or row["market_cap"],
                    "description": enrichment.get("description") or row["description"] or "",
                    "issuer": row.get("issuer") or "",
                    "is_active": True,
                    "data_source": "yfinance_enriched",
                }
                enriched_records.append(record)
                summary["enriched"] += 1
            else:
                summary["failed"] += 1

            time.sleep(YFINANCE_RATE_LIMIT)

        if enriched_records:
            upsert_crypto_products(conn, enriched_records)
            logger.info(f"Enriched {len(enriched_records)} crypto products")

        # Cross-reference: update new_listings
        mark_listings_as_crypto(conn)

    except Exception as e:
        summary["errors"].append(str(e))
        logger.error(f"Enrichment failed: {e}")
    finally:
        conn.close()

    return summary


# ── SEC Company Tickers Keyword Scan ──


def scan_sec_tickers_for_crypto(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Download SEC company_tickers.json and scan company names for crypto keywords.

    Returns list of newly discovered crypto products not already in the DB.
    """
    conn = init_db(db_path)
    new_products = []

    try:
        # Download current SEC tickers
        resp = requests.get(SEC_COMPANY_TICKERS_URL, headers=SEC_HEADERS, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        existing_tickers = get_crypto_tickers_set(conn)

        for entry in data.values():
            ticker = str(entry.get("ticker", "")).upper()
            name = str(entry.get("title", ""))
            cik = str(entry.get("cik_str", ""))

            if not ticker or ticker in existing_tickers:
                continue

            # Check name against crypto keywords
            name_lower = name.lower()
            matched_kw = None
            for kw in CRYPTO_KEYWORDS:
                if kw in name_lower:
                    matched_kw = kw
                    break

            if not matched_kw:
                # Also check if ticker itself is in known list
                if ticker not in KNOWN_CRYPTO_PRODUCTS:
                    continue

            logger.info(f"Discovered new crypto product: {ticker} - {name} (keyword: {matched_kw})")

            # Determine product type from name
            product_type = "blockchain"
            if "etf" in name_lower or "trust" in name_lower:
                product_type = "spot_etf"
            elif any(w in name_lower for w in ["mining", "blockchain", "crypto", "coin"]):
                product_type = "crypto_stock"

            new_products.append({
                "ticker": ticker,
                "company_name": name,
                "product_type": product_type,
                "underlying_asset": "Multi-asset",
                "listing_date": None,
                "expense_ratio": None,
                "aum": None,
                "market_cap": None,
                "description": f"Discovered via SEC keyword scan: '{matched_kw}' in company name",
                "issuer": name,
                "is_active": True,
                "data_source": "sec_keyword_scan",
            })

        if new_products:
            count = upsert_crypto_products(conn, new_products)
            logger.info(f"Added {count} new crypto products from SEC scan")
            mark_listings_as_crypto(conn)

    except Exception as e:
        logger.error(f"SEC keyword scan failed: {e}")
    finally:
        conn.close()

    return new_products


# ── Full Refresh ──


def full_refresh(db_path: Optional[str] = None) -> Dict[str, Any]:
    """Full refresh of crypto products: known list + enrichment + keyword scan.

    1. Upsert all known crypto products (base truth)
    2. Scan SEC tickers for new crypto products
    3. Enrich missing data from yfinance
    """
    conn = init_db(db_path)
    summary = {
        "known_loaded": 0,
        "new_from_scan": 0,
        "enriched": 0,
        "errors": [],
    }

    log_id = -1
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        log_id = log_fetch_start(conn, today, source="crypto_full_refresh")

        # 1. Upsert known list
        known_records = _known_list_to_records()
        if known_records:
            summary["known_loaded"] = upsert_crypto_products(conn, known_records)
            logger.info(f"Loaded {summary['known_loaded']} known crypto products")

        # 2. Sync new_listings is_crypto tags
        marked = mark_listings_as_crypto(conn)
        logger.info(f"Marked {marked} listings as crypto")

        # 3. Enrich from yfinance
        enrichment = enrich_all_products(db_path)
        summary["enriched"] = enrichment["enriched"]

        total = get_crypto_product_count(conn)
        logger.info(f"Full refresh complete: {total} active crypto products")

        log_fetch_end(conn, log_id, items_checked=len(known_records),
                      new_items=summary["known_loaded"], status="ok")

    except Exception as e:
        summary["errors"].append(str(e))
        logger.error(f"Full refresh failed: {e}")
        if log_id >= 0:
            log_fetch_end(conn, log_id, status="error", error=str(e))
    finally:
        conn.close()

    return summary


# ── CLI ──

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    if "--init" in sys.argv:
        result = full_refresh()
        print(f"\nInit result: {result}")

    elif "--scan" in sys.argv:
        new_products = scan_sec_tickers_for_crypto()
        print(f"\nFound {len(new_products)} new crypto products")
        for p in new_products:
            print(f"  {p['ticker']}: {p['company_name']} ({p['product_type']})")

    elif "--enrich" in sys.argv:
        result = enrich_all_products()
        print(f"\nEnrichment result: {result}")

    else:
        result = full_refresh()
        print(f"\nResult: {result}")
