"""
SFC Authorized Fund List pipeline — fund scraping + classification engine.

Fetches the SFC public register of authorized unit trusts and mutual funds.
Applies dual-dimension classification per SFC rules:
  - §5.1A: is_derivative_product (financial nature, NDE > 50% NAV)
  - §5.5:  is_complex_product (six-factor retail understandability test)
  - complex_product_type: most specific category (derivative_fund, synthetic_etf,
    futures_etf, L&I, hedge_fund, structured, complex_bond, security_token, non_complex)

Usage:
    python -m hk_funds.pipeline_funds --init
    python -m hk_funds.pipeline_funds --classify
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
    COMPLEX_PRODUCT_NAME_KEYWORDS_EN,
    COMPLEX_PRODUCT_NAME_KEYWORDS_CN,
    DERIVATIVE_FUND_TYPES,
    DERIVATIVE_NAME_KEYWORDS_EN,
    DERIVATIVE_NAME_KEYWORDS_CN,
    FACTOR_4_KEYWORDS_EN,
    FACTOR_4_KEYWORDS_CN,
    FACTOR_5_KEYWORDS_EN,
    FACTOR_5_KEYWORDS_CN,
    FACTOR_6_KEYWORDS_EN,
    FACTOR_6_KEYWORDS_CN,
    KNOWN_CLASSIFIED_FUNDS,
    SFC_REQUEST_DELAY,
    SFC_TIMEOUT,
    SFC_UTMF_SEARCH_URL,
)
from hk_funds.storage import (
    init_db,
    upsert_funds,
    upsert_fund_classifications,
    get_funds,
    log_fetch_start,
    log_fetch_end,
    update_fund_classification,
)

logger = logging.getLogger("hk_funds.pipeline_funds")

# Session with browser-like headers
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
#  Classification Engine — SFC Dual-Dimension (§5.1A + §5.5)
# ═══════════════════════════════════════════════════════════════
#
#  Step 1: is_derivative_product (§5.1A) — financial nature
#  Step 2: is_complex_product (§5.5) — six-factor test
#  Step 3: complex_product_type — most specific category
#


def _classify_fund(fund: Dict[str, Any], nde_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Classify a fund per SFC §5.1A and §5.5 as two independent dimensions.

    Args:
        fund: Fund record from hk_funds table.
        nde_data: Optional NDE extraction data from hk_fund_classifications table
                  (derivative_exposure_pct, uses_derivatives_for_non_hedging, etc.)

    Returns (fund_update, detail_record) tuple.
    """
    fund_name_en = str(fund.get("fund_name_en") or "").lower()
    fund_name_cn = str(fund.get("fund_name_cn") or "").lower()
    fund_type = (fund.get("fund_type") or "").lower()
    auth_no = fund.get("sfc_authorization_no", "")
    isin = fund.get("isin", "")

    reason_parts = []
    source = "heuristic"
    complex_product_type = "non_complex"

    # Initialize detail record — seed with NDE data only when trustworthy.
    # LLM-extracted boolean flags (is_leveraged, is_inverse, etc.) are
    # unreliable when derivative_exposure_pct = 0 — the LLM hallucinates
    # them for funds that clearly don't use derivatives (e.g. spot ETFs).
    # Only trust NDE flags when there's actual derivative exposure (>0%).
    nde_pct = nde_data.get("derivative_exposure_pct") if nde_data else None
    nde_trusted = nde_pct is not None and nde_pct > 0

    detail = {
        "sfc_complex_list_match": False,
        "derivative_exposure_pct": nde_pct,
        "is_synthetic_replication": nde_data.get("is_synthetic_replication", False) if nde_trusted else False,
        "is_leveraged": nde_data.get("is_leveraged", False) if nde_trusted else False,
        "leverage_ratio": nde_data.get("leverage_ratio") if nde_trusted else None,
        "is_inverse": nde_data.get("is_inverse", False) if nde_trusted else False,
        "is_structured": False,
        "has_nested_derivatives": False,
        "uses_derivatives_for_non_hedging": nde_data.get("uses_derivatives_for_non_hedging", False) if nde_trusted else False,
        # Six factors (§5.5)
        "has_secondary_market": None,
        "has_transparent_info": None,
        "loss_exceeds_principal": False,
        "has_complex_payoff": False,
        "illiquid_or_hard_to_value": None,
        "classification_determination": "",
    }

    # ── Layer 0: Known classified list lookup ──
    # When an authoritative source (SFC UTMF, SIP register, etc.) has classified
    # this fund, use that classification directly — bypass heuristics.
    lookup_key = auth_no or isin
    known_classified = False
    if lookup_key and lookup_key in KNOWN_CLASSIFIED_FUNDS:
        complex_product_type = KNOWN_CLASSIFIED_FUNDS[lookup_key]
        reason_parts.append(f"SFC known list: {complex_product_type}")
        detail["sfc_complex_list_match"] = True
        known_classified = True

    # ── Layer 0.5: NDE data override ──
    # If LLM-extracted NDE data explicitly shows derivative exposure > 50%, use it
    nde_based = False
    if nde_data and source != "sfc_list":
        nde_pct = nde_data.get("derivative_exposure_pct")
        if nde_pct is not None and nde_pct > 50:
            nde_based = True
            if "nde_extraction" not in source:
                source = "nde_extraction" if source == "heuristic" else f"{source}+nde_extraction"
            reason_parts.append(f"NDE={nde_pct:.0f}% > 50% NAV (LLM extraction)")
        # Also flag if LLM explicitly confirmed investment-purpose derivative use
        # (but only when accompanied by actual NDE data)
        uses_deriv = nde_data.get("uses_derivatives_for_non_hedging")
        if uses_deriv and nde_pct is not None:
            reason_parts.append("Derivatives used for investment purposes (LLM extraction)")

    # ── Step 1: Determine is_derivative_product (§5.1A) ──
    is_derivative = _check_derivative_product(
        fund_name_en, fund_name_cn, fund_type, detail, reason_parts, source
    )

    # NDE data can independently establish derivative status
    if nde_based and not is_derivative:
        is_derivative = True

    # ── Step 2: Determine is_complex_product (§5.5 six-factor test) ──
    is_complex = _check_complex_product(
        fund_name_en, fund_name_cn, is_derivative, detail, reason_parts, source
    )

    # ── Step 3: Assign complex_product_type ──
    # When the SFC known list provides an authoritative classification, use it
    # to override the heuristic is_derivative/is_complex determinations.
    # Types that are ALWAYS derivative (value derives from underlying):
    DERIVATIVE_CPT = {"derivative_fund", "synthetic_etf", "futures_etf",
                      "L&I", "hedge_fund", "structured"}
    if known_classified and complex_product_type in DERIVATIVE_CPT:
        is_derivative = True
        is_complex = True
    elif known_classified and complex_product_type not in ("non_complex", ""):
        # complex_bond, security_token: complex but not necessarily derivative
        is_complex = True
    elif not known_classified and is_complex:
        complex_product_type = _determine_complex_type(
            fund_name_en, fund_name_cn, fund_type, is_derivative, detail
        )

    # Build determination string
    detail["classification_determination"] = (
        "; ".join(reason_parts) if reason_parts else "No complex or derivative indicators found"
    )

    fund_update = {
        "is_derivative_product": is_derivative,
        "is_complex_product": is_complex,
        "complex_product_type": complex_product_type,
        "classification_reason": "; ".join(reason_parts),
        "classification_source": source,
    }

    detail_record = {
        "fund_id": fund.get("id"),
        **detail,
        "last_reviewed_date": datetime.now().strftime("%Y-%m-%d"),
    }

    return fund_update, detail_record


def _check_derivative_product(name_en: str, name_cn: str, fund_type: str,
                               detail: dict, reason_parts: list, source: str) -> bool:
    """§5.1A: Is this a derivative product?

    A fund is a derivative product if:
    - Fund type is inherently derivative (synthetic ETF, futures ETF, L&I, hedge fund)
    - Name indicates derivative features (synthetic, swap-based, etc.)
    - NDE > 50% NAV (requires prospectus data; defaults to type-based)
    """
    # If already matched via known SFC list, check fund type too
    if source == "sfc_list":
        pass

    # Fund type check
    if fund_type in DERIVATIVE_FUND_TYPES:
        reason_parts.append(f"Derivative fund type: {fund_type}")
        if fund_type in ("synthetic_etf",):
            detail["is_synthetic_replication"] = True
        if fund_type in ("futures_etf",):
            detail["has_nested_derivatives"] = True
        if fund_type in ("leveraged_inverse_product", "leveraged_product"):
            detail["is_leveraged"] = True
        if fund_type in ("inverse_product",):
            detail["is_inverse"] = True
        if fund_type in ("hedge_fund",):
            detail["uses_derivatives_for_non_hedging"] = True
        return True

    # Name-based keyword check for derivative indicators
    en_match = any(kw in name_en for kw in DERIVATIVE_NAME_KEYWORDS_EN)
    cn_match = any(kw in name_cn for kw in DERIVATIVE_NAME_KEYWORDS_CN)

    if en_match or cn_match:
        reason_parts.append("Name indicates derivative features")

        # Set detail flags based on specific matches
        if any(kw in name_en for kw in ("synthetic", "swap-based")) or \
           any(kw in name_cn for kw in ("合成",)):
            detail["is_synthetic_replication"] = True
        if "leveraged" in name_en or "杠杆" in name_cn:
            detail["is_leveraged"] = True
        if "inverse" in name_en or "反向" in name_cn:
            detail["is_inverse"] = True
        # Strategies that use derivatives for investment (non-hedging) purposes
        # Excludes broad terms like "volatility", "alpha", "systematic",
        # "special situations", "total return" that cause false positives.
        non_hedging_kw_en = [
            "absolute return", "long/short", "long short",
            "managed futures", "arbitrage", "multi-strategy", "macro",
            "market neutral", "equity long short",
            "event driven", "global macro", "cta",
            "relative value", "distressed", "credit arbitrage",
            "convertible arbitrage", "statistical arbitrage",
            "capital structure",
        ]
        non_hedging_kw_cn = [
            "绝对回报", "管理期货", "套利", "多策略",
            "宏观", "市场中性", "股票多空", "事件驱动",
            "相对价值", "绝对收益", "另类投资",
            "股票对冲", "资本结构",
        ]
        if any(kw in name_en for kw in non_hedging_kw_en) or \
           any(kw in name_cn for kw in non_hedging_kw_cn):
            detail["uses_derivatives_for_non_hedging"] = True

        return True

    return False


def _check_complex_product(name_en: str, name_cn: str,
                            is_derivative: bool, detail: dict,
                            reason_parts: list, source: str) -> bool:
    """§5.5 Six-factor test for complex product classification.

    Factor ①: Is it a derivative product? (from step 1)
    Factor ②: No secondary market / hard to unwind
    Factor ③: Lack of transparent/comparable pricing info
    Factor ④: Potential loss exceeding principal
    Factor ⑤: Complex payoff structure
    Factor ⑥: Illiquid or hard to value underlying
    """
    if source == "sfc_list":
        return True  # known list entries are always complex per SFC

    factors_triggered = []

    # Factor ①: Derivative product (already computed)
    if is_derivative:
        factors_triggered.append("①_derivative_product")

    # Factor ④: Loss exceeds principal
    if any(kw in name_en for kw in FACTOR_4_KEYWORDS_EN) or \
       any(kw in name_cn for kw in FACTOR_4_KEYWORDS_CN):
        detail["loss_exceeds_principal"] = True
        if "④_loss_exceeds_principal" not in factors_triggered:
            factors_triggered.append("④_loss_exceeds_principal")
    elif detail.get("is_leveraged") or detail.get("is_inverse"):
        detail["loss_exceeds_principal"] = True
        factors_triggered.append("④_loss_exceeds_principal")

    # Factor ⑤: Complex payoff structure
    if any(kw in name_en for kw in FACTOR_5_KEYWORDS_EN) or \
       any(kw in name_cn for kw in FACTOR_5_KEYWORDS_CN):
        detail["has_complex_payoff"] = True
        factors_triggered.append("⑤_complex_payoff")
    elif detail.get("is_structured"):
        detail["has_complex_payoff"] = True
        factors_triggered.append("⑤_complex_payoff")

    # Also check complex product keywords (structured products may not be derivative)
    en_complex = any(kw in name_en for kw in COMPLEX_PRODUCT_NAME_KEYWORDS_EN)
    cn_complex = any(kw in name_cn for kw in COMPLEX_PRODUCT_NAME_KEYWORDS_CN)
    if en_complex or cn_complex:
        if not detail.get("is_structured"):
            # Check if any structural keywords matched
            if any(kw in name_en for kw in ("structured product", "accumulator", "decumulator", "knock-out", "knock-in", "barrier", "digital option", "binary", "autocallable", "range accrual", "snowball")) or \
               any(kw in name_cn for kw in ("结构性产品", "结构性票据", "累计", "敲出", "敲入", "雪球", "区间累积", "鲨鱼鳍")):
                detail["is_structured"] = True
            if not detail.get("has_complex_payoff"):
                detail["has_complex_payoff"] = True
                factors_triggered.append("⑤_complex_payoff")

    # Factor ②: No secondary market (heuristic: structured/private)
    if detail.get("is_structured"):
        detail["has_secondary_market"] = False

    # Factor ③: Transparent info (default: SFC-authorized funds have KFS)
    detail["has_transparent_info"] = True

    # Factor ⑥: Illiquid / hard to value
    if any(kw in name_en for kw in FACTOR_6_KEYWORDS_EN) or \
       any(kw in name_cn for kw in FACTOR_6_KEYWORDS_CN):
        detail["illiquid_or_hard_to_value"] = True
        factors_triggered.append("⑥_illiquid")

    is_complex = len(factors_triggered) > 0

    if is_complex:
        reason_parts.append(f"Complex per §5.5 factors: {', '.join(factors_triggered)}")

    return is_complex


def _determine_complex_type(name_en: str, name_cn: str, fund_type: str,
                             is_derivative: bool, detail: dict) -> str:
    """Assign the most specific complex_product_type from COMPLEX_PRODUCT_TYPES."""
    # Structured products (ELN, accumulator, etc.)
    if detail.get("is_structured"):
        return "structured"

    # Synthetic ETF
    if fund_type in ("synthetic_etf",) or detail.get("is_synthetic_replication"):
        return "synthetic_etf"

    # Futures ETF
    if fund_type in ("futures_etf",):
        return "futures_etf"

    # Leveraged and/or Inverse
    if detail.get("is_leveraged") and detail.get("is_inverse"):
        return "L&I"
    if detail.get("is_leveraged") or detail.get("is_inverse"):
        return "L&I"

    # Hedge fund
    if fund_type in ("hedge_fund",):
        return "hedge_fund"

    # Derivative fund (NDE > 50% or uses derivatives)
    if is_derivative:
        return "derivative_fund"

    # Complex bond — check for bond-related keywords
    bond_kw = ["bond", "fixed income", "credit", "债券", "债"]
    has_bond = any(kw in name_en for kw in bond_kw) or any(kw in name_cn for kw in bond_kw)
    if has_bond and detail.get("has_complex_payoff"):
        return "complex_bond"

    # Default for complex non-derivative products
    return "complex_bond"


def build_known_classified_from_sfc(conn) -> int:
    """Populate KNOWN_CLASSIFIED_FUNDS from SFC UTMF derivative flag data.

    Queries hk_funds for all funds where the SFC officially marked them as
    derivative products (classification_source = 'sfc_utmf', is_derivative_product = true).
    Adds them to the in-memory KNOWN_CLASSIFIED_FUNDS dict so the classification
    engine can use them as authoritative lookups.

    Also adds funds with is_complex_product=true and SFC-origin classification
    to capture complex-but-not-derivative products.

    Returns the number of entries added.
    """
    added = 0

    # Load SFC-flagged derivative funds
    try:
        rows = conn.execute("""
            SELECT sfc_authorization_no, isin, complex_product_type
            FROM hk_funds
            WHERE classification_source LIKE 'sfc_utmf%'
              AND is_derivative_product = true
              AND is_active = true
        """).fetchall()
        for row in rows:
            auth_no, isin, existing_type = row
            cpt = existing_type if existing_type and existing_type != "non_complex" else "derivative_fund"
            if auth_no and auth_no not in KNOWN_CLASSIFIED_FUNDS:
                KNOWN_CLASSIFIED_FUNDS[auth_no] = cpt
                added += 1
            if isin and isin not in KNOWN_CLASSIFIED_FUNDS:
                KNOWN_CLASSIFIED_FUNDS[isin] = cpt
                added += 1
        logger.info(f"build_known_classified: added {added} SFC-flagged derivative entries")
    except Exception as e:
        logger.warning(f"build_known_classified: could not load SFC derivative data: {e}")

    # Also add explicitly complex products from previous authoritative classifications
    try:
        complex_rows = conn.execute("""
            SELECT sfc_authorization_no, isin, complex_product_type
            FROM hk_funds
            WHERE is_complex_product = true
              AND classification_source LIKE 'sfc_%'
              AND is_active = true
        """).fetchall()
        for row in complex_rows:
            auth_no, isin, cpt = row
            if cpt and cpt != "non_complex":
                if auth_no and auth_no not in KNOWN_CLASSIFIED_FUNDS:
                    KNOWN_CLASSIFIED_FUNDS[auth_no] = cpt
                    added += 1
                if isin and isin not in KNOWN_CLASSIFIED_FUNDS:
                    KNOWN_CLASSIFIED_FUNDS[isin] = cpt
                    added += 1
        logger.info(f"build_known_classified: total {len(KNOWN_CLASSIFIED_FUNDS)} entries after loading SFC data")
    except Exception as e:
        logger.warning(f"build_known_classified: could not load SFC complex data: {e}")

    return added


def classify_all_funds(conn) -> Dict[str, int]:
    """Re-run classification on all active funds. Returns summary counts."""
    # Step 0: Build KNOWN_CLASSIFIED_FUNDS from SFC data before classifying
    build_known_classified_from_sfc(conn)

    funds_df = get_funds(conn, is_active=True, limit=10000)
    if funds_df.empty:
        logger.info("No funds to classify")
        return {"total": 0, "complex": 0, "derivative": 0, "ordinary": 0}

    # Pre-load NDE extraction data for all active funds.
    # Only load data where derivative_exposure_pct is explicitly set
    # (from LLM extraction), not boolean flags from previous keyword runs.
    nde_map = {}
    try:
        nde_rows = conn.execute("""
            SELECT fund_id, derivative_exposure_pct, is_synthetic_replication,
                   is_leveraged, leverage_ratio, is_inverse,
                   uses_derivatives_for_non_hedging
            FROM hk_fund_classifications
            WHERE derivative_exposure_pct IS NOT NULL
        """).fetchall()
        for row in nde_rows:
            nde_map[row[0]] = {
                "derivative_exposure_pct": row[1],
                "is_synthetic_replication": row[2],
                "is_leveraged": row[3],
                "leverage_ratio": row[4],
                "is_inverse": row[5],
                "uses_derivatives_for_non_hedging": row[6],
            }
        if nde_map:
            logger.info(f"Loaded NDE data for {len(nde_map)} funds")
    except Exception as e:
        logger.warning(f"Could not load NDE data: {e}")

    updates = []
    details = []
    for _, row in funds_df.iterrows():
        fund = row.to_dict()
        nde_data = nde_map.get(fund.get("id"))
        fund_update, detail_record = _classify_fund(fund, nde_data)
        fund_update["id"] = fund["id"]
        detail_record["fund_id"] = fund["id"]
        updates.append(fund_update)
        details.append(detail_record)

    # Apply classification updates — preserve SFC-sourced derivative flags
    for u in updates:
        fid = u.pop("id")
        existing = conn.execute(
            "SELECT classification_source, is_derivative_product, is_complex_product, complex_product_type FROM hk_funds WHERE id = ?",
            [fid]
        ).fetchone()

        # Preserve authoritative classification sources (SFC, HKEX, etc.)
        # Don't let the heuristic engine downgrade authoritative classifications.
        existing_source = (existing[0] or "") if existing else ""
        existing_deriv = existing[1] if existing else False
        existing_complex = existing[2] if existing else False
        existing_cpt = existing[3] if existing else None

        # Normalize legacy source labels: remove redundant parts
        # "sfc_utmf+heuristic+sfc_list" → "sfc_utmf"
        # "sfc_utmf+heuristic" → "sfc_utmf"
        if existing_source:
            parts = set(existing_source.split("+"))
            parts.discard("heuristic")
            parts.discard("sfc_list")  # always duplicate of sfc_utmf
            # Rebuild: keep authoritative sources only, in canonical order
            ordered_parts = []
            for p in ["sfc_utmf", "sfc_sip", "hkex_list", "nde_extraction"]:
                if p in parts:
                    ordered_parts.append(p)
                    parts.discard(p)
            ordered_parts.extend(sorted(parts))
            existing_source = "+".join(ordered_parts)

        # Normalize internal source labels before comparison
        raw_source = u.get("classification_source", "")
        if raw_source == "sfc_list":
            raw_source = "sfc_utmf"  # KNOWN_CLASSIFIED_FUNDS lookup = SFC UTMF data

        if existing_source and existing_source not in ("heuristic", "", None):
            # Authoritative source exists — don't let heuristic downgrade it
            u["is_derivative_product"] = u["is_derivative_product"] or existing_deriv
            # Preserve the authoritative source
            if raw_source == "heuristic":
                u["classification_source"] = existing_source
            elif raw_source not in (existing_source, "", None):
                # Avoid duplicating source labels (e.g. "sfc_utmf+sfc_utmf")
                existing_parts = set(existing_source.split("+"))
                new_parts = set(raw_source.split("+"))
                merged = existing_parts | new_parts
                # Order: sfc_utmf first, then others
                ordered = []
                for p in ["sfc_utmf", "sfc_sip", "hkex_list", "heuristic", "nde_extraction"]:
                    if p in merged:
                        ordered.append(p)
                        merged.discard(p)
                ordered.extend(sorted(merged))
                u["classification_source"] = "+".join(ordered)
            else:
                u["classification_source"] = existing_source

            # Preserve authoritative complex_product_type over heuristic reclassification
            if existing_cpt and existing_cpt != "non_complex":
                new_cpt = u.get("complex_product_type", "non_complex")
                # Don't let heuristic downgrade or change an authoritative classification
                if new_cpt == "non_complex" or raw_source == "heuristic":
                    u["complex_product_type"] = existing_cpt
                # Also preserve authoritative is_derivative/is_complex flags
                if raw_source == "heuristic":
                    if existing_deriv:
                        u["is_derivative_product"] = True
                    if existing_complex:
                        u["is_complex_product"] = True

        conn.execute("""
            UPDATE hk_funds
            SET is_derivative_product = ?, is_complex_product = ?,
                complex_product_type = ?, classification_reason = ?,
                classification_source = ?, last_updated = now()
            WHERE id = ?
        """, [u["is_derivative_product"], u["is_complex_product"],
              u["complex_product_type"], u["classification_reason"],
              u["classification_source"], fid])

    # Upsert classification details
    upsert_fund_classifications(conn, details)

    counts = {"total": len(updates)}
    counts["complex"] = sum(1 for u in updates if u["is_complex_product"])
    counts["derivative"] = sum(1 for u in updates if u["is_derivative_product"])
    counts["ordinary"] = sum(1 for u in updates if not u["is_complex_product"])

    logger.info(f"Classification complete: {counts}")
    return counts


# ═══════════════════════════════════════════════════════════════
#  SFC Fund List Scraper
# ═══════════════════════════════════════════════════════════════


def fetch_sfc_fund_list() -> List[Dict[str, Any]]:
    """Fetch the SFC authorized Unit Trusts and Mutual Funds list.

    Uses the SFC productlistWeb search form — POSTs searchBy=COMPANY
    to retrieve all authorized UTMF products (umbrella + sub-funds).
    Parses the resulting HTML table for fund records.

    The SFC table has 6 columns:
      0: Product (CE No.) — umbrella fund/scheme
      1: Sub-fund (CE No.) — individual fund
      2: Issuer — management company
      3: Authorisation date — DD/MM/YYYY
      4: Documents — offering document links
      5: Derivative funds — Yes/No

    Only sub-fund rows (col 1 non-empty) are returned as distributable
    funds. Returns a list of fund record dicts.
    """
    session = _get_session()
    records = []

    try:
        logger.info("Fetching SFC UTMF list from productlistWeb...")
        resp = session.post(
            SFC_UTMF_SEARCH_URL,
            data={"searchBy": "COMPANY", "searchAlpha": "", "subFundType": "", "sortBy": ""},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=SFC_TIMEOUT,
        )
        if resp.status_code == 200 and len(resp.text) > 10000:
            records = _parse_utmf_html(resp.text)
            logger.info(f"UTMF HTML: parsed {len(records)} sub-funds")
        else:
            logger.warning(f"UTMF search returned status={resp.status_code}, len={len(resp.text)}")
    except Exception as e:
        logger.error(f"UTMF fetch failed: {e}")

    if not records:
        # Fallback: try the monthly XLS
        logger.info("Trying monthly XLS fallback...")
        records = _fetch_monthly_xls(session)

    if not records:
        logger.warning(
            "Could not fetch SFC fund list. "
            "Fund data can be imported via POST /api/v1/import/csv."
        )

    logger.info(f"Fetched {len(records)} funds from SFC")
    return records


def _parse_utmf_html(html: str) -> List[Dict[str, Any]]:
    """Parse SFC UTMF search result HTML table into fund records.

    Extracts CE numbers from parenthetical notations like (ADX434).
    Only returns sub-fund rows (non-empty column 1).
    """
    records = []
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        logger.warning("BeautifulSoup not installed; install bs4")
        return records

    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    if len(tables) < 2:
        logger.warning("Expected at least 2 tables in UTMF HTML")
        return records

    # The second table is the data table
    data_table = tables[1]
    rows = data_table.find_all("tr")[1:]  # skip header

    ce_pattern = re.compile(r'\(([A-Z]{3}\d{3})\)')

    for row in rows:
        cols = row.find_all("td")
        if len(cols) < 4:
            continue

        product_cell = cols[0].get_text(strip=True)
        subfund_cell = cols[1].get_text(strip=True)
        issuer = cols[2].get_text(strip=True)
        date_str = cols[3].get_text(strip=True)
        derivative_flag = cols[5].get_text(strip=True) if len(cols) > 5 else ""

        # Only process sub-fund rows
        if not subfund_cell:
            continue

        # Extract CE numbers
        subfund_ce = ce_pattern.findall(subfund_cell)
        product_ce = ce_pattern.findall(product_cell)
        sfc_auth_no = subfund_ce[0] if subfund_ce else ""

        if not sfc_auth_no:
            continue  # skip rows without identifiable CE number

        # Extract product/umbrella name (strip trailing CE number)
        product_name = re.sub(r'\s*\([A-Z]{3}\d{3}\)\s*$', '', product_cell).strip()
        subfund_name = re.sub(r'\s*\([A-Z]{3}\d{3}\)\s*$', '', subfund_cell).strip()

        # Extract offering document link.
        # SFC uses onclick="popupStatic('searchProduct/getDocListNoDate.do?...')"
        # rather than a direct href. Extract from onclick if present.
        doc_link = ""
        doc_cell = cols[4] if len(cols) > 4 else None
        if doc_cell:
            link = doc_cell.find("a")
            if link:
                href = link.get("href", "")
                onclick = link.get("onclick", "")
                if onclick:
                    # Extract path from popupStatic('...')
                    m = re.search(r"""popupStatic\(['"]([^'"]+)['"]""", onclick)
                    if m:
                        doc_link = "https://apps.sfc.hk/productlistWeb/" + m.group(1)
                elif href and not href.startswith("javascript:"):
                    doc_link = href
                    if not doc_link.startswith("http"):
                        doc_link = "https://apps.sfc.hk" + doc_link

        # Determine fund type
        fund_type = "unit_trust"
        if "Open-Ended Fund Compan" in product_cell or "OFC" in product_cell:
            fund_type = "open_ended_fund_company"

        record = {
            "sfc_authorization_no": sfc_auth_no,
            "fund_name_en": subfund_name,
            "umbrella_fund_name": product_name,
            "umbrella_fund_ce": product_ce[0] if product_ce else "",
            "fund_type": fund_type,
            "fund_manager_name_en": issuer,
            "authorization_date": _parse_utmf_date(date_str),
            "is_derivative_product": derivative_flag.strip() == "Yes",
            "classification_source": "sfc_utmf" if (derivative_flag.strip() == "Yes") else None,
            "classification_reason": "SFC Derivative funds column" if (derivative_flag.strip() == "Yes") else None,
            "source_url": doc_link or SFC_UTMF_SEARCH_URL,
            "is_active": True,
        }
        records.append(record)

    return records


def _parse_utmf_date(date_str: str) -> Optional[str]:
    """Parse DD/MM/YYYY date from SFC UTMF table."""
    if not date_str:
        return None
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _fetch_monthly_xls(session: requests.Session) -> List[Dict[str, Any]]:
    """Fallback: download the SFC monthly XLS of newly authorized funds.

    The XLS URL pattern is: .../Monthly-List/<Mon>-<YYYY>-AuthEng.xls
    e.g. May-2026-AuthEng.xls
    """
    import hashlib
    import io
    from datetime import datetime as dt
    from datetime import timedelta

    _ce_pat = re.compile(r'\(([A-Z]{3}\d{3})\)')
    records = []
    now = dt.now()
    months = [
        now.strftime("%B-%Y"),
        (now.replace(day=1) - timedelta(days=1)).strftime("%B-%Y"),
    ]
    for month_str in months:
        try:
            url = f"https://www.sfc.hk/-/media/files/PCIP/Monthly-List/{month_str}-AuthEng.xls"
            logger.info(f"Trying monthly XLS: {url}")
            resp = session.get(url, timeout=SFC_TIMEOUT)
            if resp.status_code != 200:
                continue

            import xlrd
            wb = xlrd.open_workbook(file_contents=resp.content)
            sh = wb.sheet_by_index(0)
            for r in range(3, sh.nrows):
                subfund_name = str(sh.cell_value(r, 1) or "").strip()
                if not subfund_name:
                    continue
                product_name = str(sh.cell_value(r, 2) or "").strip()
                promoter = str(sh.cell_value(r, 3) or "").strip()
                date_val = sh.cell_value(r, 4)

                # Extract CE number from subfund or promoter cell
                ce_match = _ce_pat.search(subfund_name) or _ce_pat.search(promoter)
                sfc_auth_no = ce_match.group(1) if ce_match else hashlib.md5(
                    f"{product_name}:{subfund_name}".encode()
                ).hexdigest()[:10].upper()

                auth_date = None
                if isinstance(date_val, float) and date_val > 10000:
                    try:
                        auth_date = (dt(1899, 12, 30) + timedelta(days=int(date_val))).strftime("%Y-%m-%d")
                    except Exception:
                        pass

                record = {
                    "sfc_authorization_no": sfc_auth_no,
                    "fund_name_en": subfund_name,
                    "umbrella_fund_name": product_name,
                    "fund_type": "unit_trust",
                    "fund_manager_name_en": re.sub(r'\s*\([^)]*\)\s*$', '', promoter).strip(),
                    "authorization_date": auth_date,
                    "source_url": url,
                    "is_active": True,
                }
                records.append(record)
            if records:
                logger.info(f"Monthly XLS: parsed {len(records)} funds from {month_str}")
                break
        except Exception as e:
            logger.debug(f"Monthly XLS {month_str} failed: {e}")

    return records




def _normalize_fund_record(raw: dict) -> Dict[str, Any]:
    """Normalize a raw fund record from any source into the standard schema."""
    return {
        "sfc_authorization_no": str(raw.get("sfc_authorization_no", "") or ""),
        "fund_name_en": str(raw.get("fund_name_en", "") or raw.get("name_en", "") or ""),
        "fund_name_cn": str(raw.get("fund_name_cn", "") or raw.get("name_cn", "") or raw.get("nameTc", "") or ""),
        "fund_type": str(raw.get("fund_type", "") or raw.get("productType", "") or "unit_trust"),
        "fund_structure": raw.get("fund_structure") or raw.get("structure"),
        "domicile": raw.get("domicile") or raw.get("domicileCountry"),
        "currency": raw.get("currency") or raw.get("baseCurrency"),
        "isin": raw.get("isin") or raw.get("ISIN"),
        "bloomberg_ticker": raw.get("bloomberg_ticker") or raw.get("bloombergTicker"),
        "launch_date": raw.get("launch_date") or raw.get("launchDate"),
        "authorization_date": raw.get("authorization_date") or raw.get("authorizationDate") or raw.get("effectiveDate"),
        "fund_manager_name_en": str(raw.get("fund_manager_name_en", "") or raw.get("managementCompany", "") or ""),
        "fund_manager_name_cn": str(raw.get("fund_manager_name_cn", "") or raw.get("managementCompanyCn", "") or ""),
        "trustee_custodian": raw.get("trustee_custodian") or raw.get("trustee"),
        "management_fee_pct": raw.get("management_fee_pct") or raw.get("managementFee"),
        "performance_fee_pct": raw.get("performance_fee_pct") or raw.get("performanceFee"),
        "nav": raw.get("nav") or raw.get("NAV"),
        "nav_date": raw.get("nav_date") or raw.get("navDate"),
        "subscription_mode": raw.get("subscription_mode") or raw.get("subscriptionMode"),
        "redemption_frequency": raw.get("redemption_frequency") or raw.get("redemptionFrequency"),
        "min_subscription_hkd": raw.get("min_subscription_hkd"),
        "min_subscription_usd": raw.get("min_subscription_usd"),
        "is_derivative_product": False,
        "is_complex_product": False,
        "complex_product_type": "non_complex",
        "classification_reason": None,
        "classification_source": None,
        "is_active": True,
        "source_url": raw.get("source_url") or raw.get("url"),
    }


# ═══════════════════════════════════════════════════════════════
#  Main entry points
# ═══════════════════════════════════════════════════════════════


def fetch_funds_daily(date_str: str = None) -> Dict[str, Any]:
    """Fetch SFC fund list, store, and classify. Returns summary."""
    conn = init_db()
    summary = {"funds_found": 0, "funds_stored": 0, "classified": {}, "errors": []}
    today = date_str or datetime.now().strftime("%Y-%m-%d")

    log_id = log_fetch_start(conn, today, source="sfc_funds")

    try:
        records = fetch_sfc_fund_list()
        summary["funds_found"] = len(records)

        if records:
            summary["funds_stored"] = upsert_funds(conn, records)

            # Run classification
            summary["classified"] = classify_all_funds(conn)

        log_fetch_end(conn, log_id, items_checked=len(records),
                      new_items=summary["funds_stored"])

    except Exception as e:
        summary["errors"].append(str(e))
        logger.error(f"Fund fetch failed: {e}")
        log_fetch_end(conn, log_id, status="error", error=str(e))
    finally:
        conn.close()

    return summary


def init(db_path=None):
    return fetch_funds_daily()


def import_csv_funds(conn, csv_path: str) -> Dict[str, int]:
    """Import fund records from CSV. Columns should match hk_funds schema.

    CSV format (header required):
        sfc_authorization_no, fund_name_en, fund_name_cn, fund_type,
        domicile, currency, isin, launch_date, authorization_date,
        fund_manager_name_en, fund_manager_name_cn, management_fee_pct,
        nav, subscription_mode, redemption_frequency
    """
    import pandas as pd
    df = pd.read_csv(csv_path)
    records = df.to_dict(orient="records")
    stored = upsert_funds(conn, records)
    logger.info(f"CSV import: {len(records)} rows, {stored} stored")
    return {"imported": len(records), "stored": stored}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    if "--classify" in sys.argv:
        conn = init_db()
        try:
            result = classify_all_funds(conn)
            print(result)
        finally:
            conn.close()
    elif "--csv" in sys.argv:
        idx = sys.argv.index("--csv")
        conn = init_db()
        try:
            result = import_csv_funds(conn, sys.argv[idx + 1])
            print(result)
        finally:
            conn.close()
    else:
        print(fetch_funds_daily())
