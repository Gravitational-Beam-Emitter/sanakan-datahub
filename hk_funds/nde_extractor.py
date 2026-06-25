"""
NDE (Net Derivative Exposure) extractor for HK SFC-authorized funds.

Downloads SFC offering documents (KFS, prospectus) and uses LLM to extract:
  - Net Derivative Exposure as % of NAV
  - Whether derivatives are used for investment (non-hedging) purposes
  - Types of derivatives used
  - Leverage ratio if applicable

Results feed into the §5.1A derivative product classification and the
hk_fund_classifications table.

Usage:
    python -m hk_funds.nde_extractor --fund-id 123
    python -m hk_funds.nde_extractor --batch --limit 20
    python -m hk_funds.nde_extractor --update-classifications
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from io import BytesIO
from typing import Any, Dict, List, Optional

import requests

from hk_funds.storage import init_db

logger = logging.getLogger("hk_funds.nde_extractor")

# ── LLM Provider Configuration (same pattern as kr_stock/tagging.py) ──

PROVIDERS = [
    {"name": "deepseek",  "style": "openai",    "model": "deepseek-chat",
     "base_url": "https://api.deepseek.com", "env_var": "DEEPSEEK_API_KEY"},
    {"name": "anthropic", "style": "anthropic",  "model": "claude-haiku-4-5-20251001",
     "base_url": None, "env_var": "ANTHROPIC_API_KEY"},
    {"name": "qwen",      "style": "openai",    "model": "qwen-plus",
     "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "env_var": "QWEN_API_KEY"},
]

# Session with browser-like headers for PDF downloads
_session: Optional[requests.Session] = None


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/125.0.0.0 Safari/537.36",
            "Accept": "application/pdf, text/html, */*",
            "Accept-Language": "en-US,en;q=0.9,zh-HK;q=0.8,zh;q=0.7",
        })
    return _session


# ── LLM Helpers ──

def _get_active_provider() -> Optional[dict]:
    """Return first LLM provider with a configured API key."""
    for p in PROVIDERS:
        key = os.getenv(p["env_var"], "")
        if key:
            return p
    return None


def _call_llm_openai(provider: dict, system_prompt: str, user_prompt: str) -> str:
    import openai
    client = openai.OpenAI(
        api_key=os.getenv(provider["env_var"]),
        base_url=provider["base_url"],
    )
    resp = client.chat.completions.create(
        model=provider["model"],
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.1,
        max_tokens=2048,
    )
    return resp.choices[0].message.content or ""


def _call_llm_anthropic(provider: dict, system_prompt: str, user_prompt: str) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=os.getenv(provider["env_var"]))
    resp = client.messages.create(
        model=provider["model"],
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
        temperature=0.1,
        max_tokens=2048,
    )
    return resp.content[0].text


def _call_llm(system_prompt: str, user_prompt: str) -> Optional[str]:
    provider = _get_active_provider()
    if provider is None:
        logger.warning("No LLM provider configured (set ANTHROPIC_API_KEY or DEEPSEEK_API_KEY)")
        return None
    try:
        if provider["style"] == "anthropic":
            return _call_llm_anthropic(provider, system_prompt, user_prompt)
        else:
            return _call_llm_openai(provider, system_prompt, user_prompt)
    except Exception as e:
        logger.error(f"LLM call failed ({provider['name']}): {e}")
        return None


def _extract_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


# ── SFC Document Download & Text Extraction ──
#
# SFC stores source_url as an HTML listing page (getDocListNoDate.do), not a
# direct PDF link.  The two-step access pattern is:
#   1. Fetch the HTML listing page → parse getDoc.do?docId=XXXXXX links
#   2. Download the actual PDF from the getDoc.do link
#
# A warmed-up session (with cookies from the UTMF search page) is required.

SFC_DOC_BASE = "https://apps.sfc.hk/productlistWeb/searchProduct/"
SFC_WARMUP_URL = "https://apps.sfc.hk/productlistWeb/searchProduct/UTMF.do?lang=EN"


def _warm_session(session: requests.Session) -> None:
    """Ensure the session has SFC cookies by visiting the UTMF search page."""
    session.get(SFC_WARMUP_URL, timeout=30)


def _fetch_sfc_doc_listing(session: requests.Session, url: str) -> Optional[List[Dict[str, str]]]:
    """Fetch the SFC document listing HTML page and extract document links.

    Returns a list of dicts with 'title' and 'url' keys, or None on failure.
    The 'Product Key Facts Statement' entry is sorted first (preferred for NDE).
    """
    try:
        resp = session.get(url, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        logger.warning(f"Failed to fetch SFC doc listing {url}: {e}")
        return None

    html = resp.text
    if "Unexpected error" in html:
        logger.warning(f"SFC doc listing returned error page for {url}")
        return None

    docs = []
    # Parse <a onclick="javascript:window.location.href='...getDoc.do?...'">
    for m in re.finditer(
        r"<a[^>]*onClick\s*=\s*\"javascript:window\.location\.href='([^']+)'[^>]*>"
        r"\s*(.*?)\s*</a>",
        html, re.DOTALL | re.IGNORECASE,
    ):
        doc_path = m.group(1)
        doc_title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        doc_url = doc_path if doc_path.startswith("http") else f"https://apps.sfc.hk{doc_path}"
        docs.append({"title": doc_title, "url": doc_url})

    if not docs:
        logger.warning(f"No document links found in SFC listing {url}")
        return None

    # Prefer KFS (smaller, contains key derivative info) over full OD
    docs.sort(key=lambda d: 0 if "Key Facts" in d["title"] else 1)
    return docs


def download_document(url: str, timeout: int = 30) -> Optional[bytes]:
    """Download a fund document, handling SFC's two-step document access.

    If *url* is an SFC getDocListNoDate.do listing page, fetches the HTML,
    extracts the actual PDF link(s), and downloads the KFS PDF (falling back
    to the first available document).

    Returns raw PDF bytes or None.
    """
    session = _get_session()

    # Direct PDF link — download immediately
    if url.lower().endswith(".pdf") or "/getDoc.do?" in url:
        try:
            resp = session.get(url, timeout=timeout)
            if resp.status_code == 200 and len(resp.content) > 1000:
                return resp.content
        except Exception as e:
            logger.warning(f"Direct PDF download failed for {url}: {e}")
        return None

    # SFC document listing page — two-step access
    if "getDocList" in url or "productlistWeb" in url:
        _warm_session(session)
        docs = _fetch_sfc_doc_listing(session, url)
        if not docs:
            return None

        # Try each document link until we get a valid PDF
        for doc in docs:
            doc_url = doc["url"]
            logger.info(f"Downloading {doc['title'][:60]} from {doc_url[:100]}...")
            try:
                resp = session.get(doc_url, timeout=timeout)
                if resp.status_code == 200 and len(resp.content) > 1000:
                    content_type = resp.headers.get("Content-Type", "")
                    if "pdf" in content_type or doc_url.endswith(".pdf") or len(resp.content) > 5000:
                        return resp.content
                    # Some SFC responses are PDFs even without correct Content-Type
                    if resp.content[:4] == b"%PDF":
                        return resp.content
            except Exception as e:
                logger.warning(f"Failed to download {doc['title'][:60]}: {e}")
                continue

        return None

    # Unknown URL format — just try downloading
    try:
        resp = session.get(url, timeout=timeout)
        if resp.status_code == 200 and len(resp.content) > 1000:
            return resp.content
    except Exception as e:
        logger.warning(f"Download failed for {url}: {e}")
    return None


def extract_pdf_text(pdf_content: bytes) -> Optional[str]:
    """Extract text from a PDF using pypdf. Returns combined text or None."""
    try:
        from pypdf import PdfReader
    except ImportError:
        logger.error("pypdf not installed. pip install pypdf")
        return None

    try:
        reader = PdfReader(BytesIO(pdf_content))
    except Exception as e:
        logger.warning(f"Failed to open PDF: {e}")
        return None

    pages = []
    for page in reader.pages[:30]:  # First 30 pages should cover KFS + key sections
        try:
            text = page.extract_text()
            if text:
                pages.append(text)
        except Exception:
            continue

    if not pages:
        return None

    combined = "\n".join(pages)
    # Truncate to ~12000 chars to fit LLM context window comfortably
    if len(combined) > 12000:
        combined = combined[:12000] + "\n...[truncated]"
    return combined


# ── NDE Extraction via LLM ──

SYSTEM_PROMPT = """You are an SFC regulatory analyst specializing in Hong Kong fund classifications.
Your task is to extract Net Derivative Exposure (NDE) information from fund offering documents.

SFC rules (§5.1A): A fund is a "derivative product" if its net derivative exposure exceeds 50% of NAV.
NDE = gross long position - gross short position (as % of NAV).

Look for:
1. Statement of NDE (e.g. "The Fund's net derivative exposure may be up to 100% of NAV")
2. Description of derivative usage: "for investment purposes" vs "for hedging only"
3. Types of derivatives: futures, options, swaps, forwards, CFDs, structured notes
4. Leverage ratio or maximum leverage (e.g. "up to 200% leverage", "2x leveraged")
5. Whether the fund uses synthetic replication (swap-based) vs physical replication
6. Investment strategy: long/short, market neutral, arbitrage, managed futures, etc.

If the document explicitly states NDE <= 50% or "derivatives used for hedging only", note that.
If the document explicitly states NDE > 50% or "extensive use of derivatives for investment", note that.

Output ONLY valid JSON (no markdown):
{
  "nde_pct": <number|null>,
  "nde_max_pct": <number|null>,
  "uses_derivatives_for_investment": <true|false|null>,
  "derivative_types": ["swap", "futures", ...],
  "leverage_ratio": <number|null>,
  "is_synthetic_replication": <true|false|null>,
  "confidence": "<high|medium|low>",
  "evidence": "<direct quote from document>"
}"""


def extract_nde(fund_name_en: str, fund_name_cn: str, document_text: str) -> Optional[Dict[str, Any]]:
    """Extract NDE data from a fund document using LLM.

    Returns parsed JSON dict with NDE metrics, or None if extraction fails.
    """
    user_prompt = (
        f"Fund Name (EN): {fund_name_en}\n"
        f"Fund Name (CN): {fund_name_cn or 'N/A'}\n\n"
        f"Document Text (excerpt):\n{document_text}"
    )

    text = _call_llm(SYSTEM_PROMPT, user_prompt)
    if not text:
        return None

    try:
        return json.loads(_extract_json(text))
    except (json.JSONDecodeError, TypeError) as e:
        logger.error(f"Failed to parse NDE extraction JSON: {e}")
        return None


# ── Name-only fallback extraction (when no document available) ──

NAME_ONLY_PROMPT = """You are an SFC regulatory analyst. Based on the fund NAME ONLY, estimate
whether this fund is likely a derivative product under SFC §5.1A (NDE > 50% NAV).

Consider these name indicators:
- "Absolute Return", "Total Return", "Long/Short", "Market Neutral" → likely derivative
- "Managed Futures", "CTA", "Systematic", "Quantitative" → likely derivative
- "Arbitrage", "Multi-Strategy", "Macro", "Event Driven" → likely derivative
- "Synthetic", "Swap-based" → definitely derivative
- "Leveraged", "Inverse", "2x", "Bear", "Short" → definitely derivative
- "Structured Product", "ELN", "Accumulator" → complex structured product
- "Bond", "Fixed Income", "Money Market" → likely NOT derivative
- "Index Fund", "ETF" (without synthetic/leveraged) → likely NOT derivative

Output ONLY valid JSON (no markdown):
{
  "likely_derivative": <true|false>,
  "likely_nde_pct": <number|null>,
  "rationale": "<one sentence>"
}"""


def extract_nde_from_name(fund_name_en: str, fund_name_cn: str) -> Optional[Dict[str, Any]]:
    """Estimate derivative classification from fund name alone (no document).
    Used as a fallback when PDF documents are not available.
    """
    user_prompt = f"Fund: {fund_name_en}" + (f" / {fund_name_cn}" if fund_name_cn else "")

    text = _call_llm(NAME_ONLY_PROMPT, user_prompt)
    if not text:
        return None

    try:
        return json.loads(_extract_json(text))
    except (json.JSONDecodeError, TypeError) as e:
        logger.error(f"Failed to parse name-only extraction: {e}")
        return None


# ── Main Pipeline ──

def extract_nde_for_fund(conn, fund_id: int) -> Optional[Dict[str, Any]]:
    """Extract NDE data for a single fund. Downloads KFS if available.

    Returns classification detail update dict, or None if nothing new.
    """
    row = conn.execute("""
        SELECT id, fund_name_en, fund_name_cn, source_url, sfc_authorization_no
        FROM hk_funds WHERE id = ?
    """, [fund_id]).fetchone()

    if not row:
        logger.warning(f"Fund {fund_id} not found")
        return None

    fid, name_en, name_cn, source_url, auth_no = row

    result = None
    source = "name_only"

    # Try to download and parse the KFS PDF
    if source_url:
        logger.info(f"Downloading KFS for {name_en[:60]}...")
        pdf_content = download_document(source_url)
        if pdf_content:
            doc_text = extract_pdf_text(pdf_content)
            if doc_text:
                result = extract_nde(name_en, name_cn or "", doc_text)
                if result:
                    source = "kfs_document"

    # Fallback: name-only LLM classification
    if result is None:
        logger.info(f"Using name-only analysis for {name_en[:60]}...")
        result = extract_nde_from_name(name_en, name_cn or "")

    if result is None:
        return None

    # Build classification update
    update = {
        "fund_id": fid,
        "last_reviewed_date": time.strftime("%Y-%m-%d"),
    }

    if "nde_pct" in result and result["nde_pct"] is not None:
        update["derivative_exposure_pct"] = float(result["nde_pct"])

    if result.get("uses_derivatives_for_investment") is True:
        update["uses_derivatives_for_non_hedging"] = True

    if result.get("is_synthetic_replication") is True:
        update["is_synthetic_replication"] = True

    if result.get("leverage_ratio") is not None:
        update["leverage_ratio"] = float(result["leverage_ratio"])
        update["is_leveraged"] = True

    # Store the full extraction result as determination text
    update["classification_determination"] = json.dumps({
        "source": source,
        "result": result,
    }, ensure_ascii=False)

    # Upsert into hk_fund_classifications
    existing = conn.execute(
        "SELECT id FROM hk_fund_classifications WHERE fund_id = ?", [fid]
    ).fetchone()

    if existing:
        set_clauses = ", ".join(f"{k} = ?" for k in update if k != "fund_id")
        values = [update[k] for k in update if k != "fund_id"] + [fid]
        conn.execute(
            f"UPDATE hk_fund_classifications SET {set_clauses} WHERE fund_id = ?",
            values
        )
    else:
        columns = ", ".join(update.keys())
        placeholders = ", ".join("?" for _ in update)
        conn.execute(
            f"INSERT INTO hk_fund_classifications ({columns}) VALUES ({placeholders})",
            list(update.values())
        )

    logger.info(f"NDE extraction for {name_en[:60]}: source={source}, "
                f"nde={result.get('nde_pct', 'N/A')}%, "
                f"investment_use={result.get('uses_derivatives_for_investment', 'N/A')}")

    return update


def extract_nde_batch(conn, limit: int = 50, skip_existing: bool = True) -> Dict[str, int]:
    """Extract NDE for multiple funds. Process funds without existing NDE data first.

    Returns summary counts.
    """
    if skip_existing:
        # Prioritize funds without NDE data
        rows = conn.execute("""
            SELECT f.id FROM hk_funds f
            LEFT JOIN hk_fund_classifications fc ON f.id = fc.fund_id
            WHERE f.is_active = true
              AND (fc.derivative_exposure_pct IS NULL OR fc.derivative_exposure_pct = 0)
              AND (fc.uses_derivatives_for_non_hedging IS NULL
                   OR fc.uses_derivatives_for_non_hedging = false)
            ORDER BY f.id
            LIMIT ?
        """, [limit]).fetchall()
    else:
        rows = conn.execute(
            "SELECT id FROM hk_funds WHERE is_active = true ORDER BY id LIMIT ?",
            [limit]
        ).fetchall()

    if not rows:
        logger.info("No funds to process")
        return {"total": 0, "extracted": 0, "skipped": 0}

    summary = {"total": len(rows), "extracted": 0, "skipped": 0}

    for (fid,) in rows:
        try:
            result = extract_nde_for_fund(conn, fid)
            if result:
                summary["extracted"] += 1
            else:
                summary["skipped"] += 1
        except Exception as e:
            logger.error(f"Failed to extract NDE for fund {fid}: {e}")
            summary["skipped"] += 1

    logger.info(f"NDE batch complete: {summary}")
    return summary


def update_classifications_from_nde(conn) -> Dict[str, int]:
    """Update hk_funds.is_derivative_product based on extracted NDE data.

    Funds with derivative_exposure_pct > 50% or uses_derivatives_for_non_hedging = true
    are reclassified as derivative products.
    """
    # Find funds where NDE data indicates derivative product
    rows = conn.execute("""
        SELECT fc.fund_id, fc.derivative_exposure_pct, fc.uses_derivatives_for_non_hedging,
               f.fund_name_en, f.is_derivative_product, f.complex_product_type
        FROM hk_fund_classifications fc
        JOIN hk_funds f ON fc.fund_id = f.id
        WHERE f.is_active = true
          AND fc.derivative_exposure_pct > 50
          AND f.is_derivative_product = false
    """).fetchall()

    updated = 0
    for row in rows:
        fid, nde_pct, uses_deriv, name_en, was_deriv, cpt = row
        conn.execute("""
            UPDATE hk_funds
            SET is_derivative_product = true,
                complex_product_type = CASE
                    WHEN complex_product_type = 'non_complex' THEN 'derivative_fund'
                    ELSE complex_product_type
                END,
                classification_source = 'nde_extraction',
                classification_reason = CASE
                    WHEN classification_reason IS NULL THEN 'NDE > 50% NAV (LLM extraction)'
                    ELSE classification_reason || '; NDE > 50% NAV (LLM extraction)'
                END,
                last_updated = now()
            WHERE id = ?
        """, [fid])
        updated += 1
        logger.info(f"NDE reclassified as derivative: {name_en[:60]} "
                    f"(NDE={nde_pct}%, invest_use={uses_deriv})")

    logger.info(f"NDE classification update: {updated} funds reclassified as derivative")
    return {"nde_reclassified": updated}


def needs_llm() -> bool:
    """Check if any LLM provider is configured."""
    return _get_active_provider() is not None


def active_provider() -> Optional[str]:
    """Return name of active LLM provider."""
    p = _get_active_provider()
    return p["name"] if p else None


# ═══════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

    conn = init_db()
    try:
        if "--fund-id" in sys.argv:
            idx = sys.argv.index("--fund-id")
            fid = int(sys.argv[idx + 1])
            result = extract_nde_for_fund(conn, fid)
            if result:
                print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
            else:
                print("No NDE data extracted")

        elif "--batch" in sys.argv:
            limit = 50
            if "--limit" in sys.argv:
                idx = sys.argv.index("--limit")
                limit = int(sys.argv[idx + 1])
            skip = "--all" not in sys.argv
            result = extract_nde_batch(conn, limit=limit, skip_existing=skip)
            print(result)

        elif "--update-classifications" in sys.argv:
            result = update_classifications_from_nde(conn)
            print(result)

        elif "--status" in sys.argv:
            from hk_funds.storage import get_funds
            provider = active_provider()
            print(f"LLM provider: {provider or 'NONE CONFIGURED'}")
            print(f"LLM available: {needs_llm()}")

            # Count funds with NDE data
            count = conn.execute("""
                SELECT COUNT(*) FROM hk_fund_classifications
                WHERE derivative_exposure_pct IS NOT NULL
            """).fetchone()[0]
            print(f"Funds with NDE data: {count}")

            # Show current classification distribution
            dist = conn.execute("""
                SELECT is_derivative_product, is_complex_product, COUNT(*) as cnt
                FROM hk_funds WHERE is_active = true
                GROUP BY is_derivative_product, is_complex_product
                ORDER BY cnt DESC
            """).fetchall()
            print("\nClassification distribution:")
            for row in dist:
                print(f"  derivative={row[0]}, complex={row[1]}: {row[2]} funds")

        else:
            print("Usage: python -m hk_funds.nde_extractor [--fund-id N | --batch | --update-classifications | --status]")

    finally:
        conn.close()
