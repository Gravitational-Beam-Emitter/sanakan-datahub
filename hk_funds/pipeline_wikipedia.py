"""
Wikipedia enrichment pipeline — check EN/ZH Wikipedia presence for HK fund managers.

For each SFC licensed corporation, checks whether a Wikipedia page exists:
  - EN Wikipedia: search by company_name_en
  - ZH Wikipedia: search by company_name_cn (or company_name_en as fallback)

Stores: wiki_en_title, wiki_zh_title, wiki_en_categories, wiki_zh_categories.

Usage:
    python3 -m hk_funds.pipeline_wikipedia            # process all managers
    python3 -m hk_funds.pipeline_wikipedia --limit 10  # test with 10
    python3 -m hk_funds.pipeline_wikipedia --ce AAC153  # single CE
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional

import requests

from hk_funds.storage import init_db

logger = logging.getLogger("hk_funds.pipeline_wikipedia")

# Wikipedia API endpoints
EN_API = "https://en.wikipedia.org/w/api.php"
ZH_API = "https://zh.wikipedia.org/w/api.php"

HEADERS = {
    "User-Agent": "CIBOHK-Datahub/1.0 (research; contact@example.com) Python-requests/2",
}
REQUEST_DELAY = 0.3  # seconds between requests (be polite)


def _extract_brand_name(name: str, lang: str = "en") -> str:
    """Extract core brand name by stripping geographic/business/legal suffixes.

    "Morgan Stanley Asia Limited" → "Morgan Stanley"
    "宏利投資管理(香港)有限公司" → "宏利"
    """
    s = name.strip()

    if lang == "zh":
        # Chinese: remove parenthesized content, then known suffixes
        s = re.sub(r"[（(][^)）]*[)）]", "", s)  # remove (Asia), (HK) etc.
        s = re.sub(r"(有限公司|有限責任公司|股份有限公司)$", "", s)
        s = re.sub(r"(投資管理|資產管理|基金管理|證券|期貨)$", "", s)
        s = re.sub(r"(香港|亞洲|中國|國際|環球)$", "", s)
        s = s.strip()
        return s

    # English: remove parenthesized content first
    s = re.sub(r"\s*\([^)]*\)", "", s)

    # Then strip suffixes in order: legal → geo → business (so Limited → Hong Kong → Asset Management)
    legal_patterns = [
        r",?\s*Limited$", r",?\s*Ltd\.?$", r",?\s*L\.L\.C\.?$",
        r",?\s*LLC$", r",?\s*Inc\.?$", r",?\s*Corporation$", r",?\s*Corp\.?$",
        r",?\s*PLC$", r",?\s*Co\.?,?\s*$", r",?\s*Company$",
    ]
    for pat in legal_patterns:
        s = re.sub(pat, "", s, flags=re.IGNORECASE)

    geo_patterns = [
        r",?\s*Asia Pacific$", r",?\s*Asia$", r",?\s*Hong Kong$",
        r",?\s*HK$", r",?\s*Greater China$",
        r",?\s*International$", r",?\s*Global$",
    ]
    biz_patterns = [
        r",?\s*Asset Management$", r",?\s*Investment Management$",
        r",?\s*Wealth Management$", r",?\s*Fund Management$",
        r",?\s*Securities$", r",?\s*Investment$", r",?\s*Capital$",
        r",?\s*Financial$", r",?\s*Finance$", r",?\s*Investments$",
        r",?\s*Advisors$", r",?\s*Advisory$",
        r",?\s*Brokerage$", r",?\s*Futures$",
        r",?\s*Services$", r",?\s*Service$",
    ]

    # Iterative peeling: legal → geo → biz, repeat until stable
    prev = None
    while s != prev:
        prev = s
        for pat in legal_patterns:
            s = re.sub(pat, "", s, flags=re.IGNORECASE)
        for pat in geo_patterns:
            s = re.sub(pat, "", s, flags=re.IGNORECASE)
        for pat in biz_patterns:
            s = re.sub(pat, "", s, flags=re.IGNORECASE)
        s = re.sub(r",\s*$", "", s)
        s = re.sub(r"\s+", " ", s).strip()

    # Strip trailing "China" only for long names, and never for known China-banks
    _china_banks = {"bank of china", "china construction bank", "china merchants bank",
                    "china citic bank", "china everbright bank", "bank of communications"}
    if s.endswith(" China") and s.lower() not in _china_banks:
        s = s[:-6].strip()

    return s


def _try_lookup(api_url: str, search_query: str, original_name: str = "") -> Optional[Dict[str, Any]]:
    """Try Wikipedia direct lookup + search for a query string.

    Returns {title, pageid} or None. Validates results against original_name.
    """
    # 1. Direct page lookup
    try:
        resp = requests.get(
            api_url,
            params={
                "action": "query",
                "titles": search_query,
                "prop": "pageprops",
                "format": "json",
                "redirects": 1,
            },
            headers=HEADERS,
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            pages = data.get("query", {}).get("pages", {})
            for page_id, page in pages.items():
                if page_id != "-1" and "missing" not in page:
                    # Skip disambiguation pages
                    pageprops = page.get("pageprops", {})
                    if "disambiguation" in pageprops:
                        break
                    return {
                        "title": page["title"],
                        "pageid": int(page_id),
                    }
    except Exception:
        pass

    time.sleep(REQUEST_DELAY * 0.3)

    # 2. Search with validation
    try:
        resp = requests.get(
            api_url,
            params={
                "action": "query",
                "list": "search",
                "srsearch": search_query,
                "srlimit": 5,
                "format": "json",
            },
            headers=HEADERS,
            timeout=15,
        )
        if resp.status_code != 200:
            return None

        data = resp.json()
        results = data.get("query", {}).get("search", [])
        if not results:
            return None

        # Validate: check word overlap with original company name
        ref_name = original_name or search_query
        for r in results:
            if _is_relevant_match(ref_name, r["title"]):
                return {
                    "title": r["title"],
                    "pageid": r["pageid"],
                }

    except Exception:
        return None

    return None


def _is_relevant_match(company_name: str, wiki_title: str) -> bool:
    """Check if a Wikipedia page title is relevant to the company.

    Uses token overlap. Stricter threshold for CJK text (char-level tokens
    increase false-positive risk from shared bigrams like 建業/證券).
    """
    c = _tokenize(company_name)
    w = _tokenize(wiki_title)

    if not c or not w:
        return False

    overlap = c & w
    min_len = min(len(c), len(w))

    # Determine if this is predominantly CJK (single-char tokens)
    cjk_chars = sum(1 for t in c if len(t) == 1 and '\u4e00' <= t <= '\u9fff')
    is_cjk = cjk_chars >= len(c) * 0.5

    if is_cjk:
        # CJK: stricter thresholds — char-level tokens increase false-positive risk
        if min_len >= 5:
            # Large sets: 50% overlap is fine
            if len(overlap) >= min_len * 0.5:
                return True
        elif min_len >= 3:
            # Medium sets: need 60%
            if len(overlap) >= min_len * 0.6:
                return True
        else:
            # Small sets (1-2 chars): perfect match required, and also must be
            # a reasonable fraction of the larger set to avoid 摩根 vs 摩根士丹利
            if len(overlap) == min_len and len(overlap) >= max(len(c), len(w)) * 0.4:
                return True
        # Cross-size match: at least 3 chars and majority of smaller set
        if len(overlap) >= 3 and len(overlap) >= min_len * 0.6:
            return True
        return False

    # ASCII: standard word overlap
    # Direct containment: one string is subset of the other's tokens
    if len(overlap) >= min_len * 0.5:
        # Single-token overlap: must also be substantial in the larger set
        # (prevents "Anglo Chinese Corporate Finance" matching "Corporate governance")
        if len(overlap) == 1:
            if max(len(c), len(w)) > 2:
                return False
            # Both sets are small (<=2): the overlapping word must be distinctive
            # (prevents "China Merchants" matching "China Everbright" via "china")
            word = list(overlap)[0]
            if word in _generic_match_words():
                return False
        return True

    # At least 2 significant words overlap
    if len(overlap) >= 2:
        return True

    # One-word company names: single overlap is enough if the word is distinctive
    # and the wiki title is not also a single generic word
    if len(c) == 1 and len(overlap) >= 1 and len(list(c)[0]) >= 4:
        if len(w) == 1 and len(list(w)[0]) < 5:
            return False  # generic single-word title like "First", "Mason"
        return True

    return False


def _tokenize(text: str) -> set:
    """Extract significant tokens from text (handles both CJK and ASCII)."""
    # Remove parenthesized content
    text = re.sub(r"[（(][^)）]*[)）]", "", text)
    tokens = set()

    # CJK characters: treat each character as a token (skip common suffixes)
    cjk_chars = re.findall(r"[\u4e00-\u9fff\u3400-\u4dbf]", text)
    cjk_skip = {"有", "限", "公", "司", "責", "任", "股", "份", "投", "資",
                "管", "理", "證", "券", "財", "香", "港",
                "亞", "洲", "中", "國", "際", "環", "球", "及", "其"}
    for ch in cjk_chars:
        if ch not in cjk_skip:
            tokens.add(ch)

    # ASCII words: 3+ chars, not stopwords
    for w in re.findall(r"[a-zA-Z]{3,}", text.lower()):
        if w not in _stopwords():
            tokens.add(w)

    return tokens



def search_wikipedia(name: str, lang: str = "en") -> Optional[Dict[str, Any]]:
    """Search Wikipedia for a page matching the company name.

    Multi-pass strategy:
      1. Search with full name
      2. Search with extracted brand name
    Returns {title, pageid} or None.
    """
    api_url = EN_API if lang == "en" else ZH_API

    # Pass 1: full name (validate against it)
    result = _try_lookup(api_url, name, original_name=name)
    if result:
        return result

    time.sleep(REQUEST_DELAY * 0.5)

    # Pass 2: brand name (validate against original full name)
    brand = _extract_brand_name(name, lang)
    if brand and brand != name and len(brand) >= 3:
        result = _try_lookup(api_url, brand, original_name=name)
        if result:
            return result

    return None


def get_zh_from_en(pageid: int) -> Optional[Dict[str, str]]:
    """Get the ZH Wikipedia equivalent of an EN page via langlinks API.

    Returns {title, pageid} or None.
    """
    try:
        resp = requests.get(
            EN_API,
            params={
                "action": "query",
                "prop": "langlinks",
                "lllang": "zh",
                "pageids": pageid,
                "format": "json",
            },
            headers=HEADERS,
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            pages = data.get("query", {}).get("pages", {})
            page_data = pages.get(str(pageid), {})
            langlinks = page_data.get("langlinks", [])
            for ll in langlinks:
                if ll.get("lang") == "zh":
                    return {"title": ll["title"]}
        return None
    except Exception:
        return None


def get_categories(pageid: int, lang: str = "en", limit: int = 20) -> List[str]:
    """Fetch categories for a Wikipedia page."""
    api_url = EN_API if lang == "en" else ZH_API

    try:
        resp = requests.get(
            api_url,
            params={
                "action": "query",
                "prop": "categories",
                "pageids": pageid,
                "cllimit": min(limit, 50),
                "format": "json",
            },
            headers=HEADERS,
            timeout=15,
        )
        if resp.status_code != 200:
            return []

        data = resp.json()
        pages = data.get("query", {}).get("pages", {})
        page_data = pages.get(str(pageid), {})
        categories = page_data.get("categories", [])

        # Extract category names without "Category:" prefix
        cat_names = []
        for cat in categories:
            title = cat.get("title", "")
            if title.startswith("Category:"):
                title = title[9:]
            cat_names.append(title)

        return cat_names
    except Exception:
        return []



def _stopwords() -> set:
    return {
        "the", "and", "for", "inc", "ltd", "llc", "limited", "company",
        "corporation", "corp", "group", "holdings", "holding", "international",
        "asia", "hong", "kong", "hongkong", "management", "asset",
        "securities", "capital", "investment", "financial", "finance",
    }


def _generic_match_words() -> set:
    """Words too generic to be the sole basis for a match (lowercase)."""
    return {
        "china", "chinese", "asia", "asian", "international", "global",
        "group", "holdings", "holding", "capital", "financial", "finance",
        "investment", "investments", "management", "asset", "securities",
        "first", "corporation", "corporate", "company", "limited",
        "hong", "kong", "hongkong", "services", "service",
    }


def ensure_wiki_columns(conn):
    """Add Wikipedia enrichment columns if they don't exist."""
    new_columns = [
        ("wiki_en_title", "VARCHAR"),
        ("wiki_zh_title", "VARCHAR"),
        ("wiki_en_categories", "VARCHAR"),
        ("wiki_zh_categories", "VARCHAR"),
    ]
    for col_name, col_type in new_columns:
        try:
            conn.execute(
                f"ALTER TABLE hk_fund_managers ADD COLUMN IF NOT EXISTS {col_name} {col_type}"
            )
        except Exception:
            # DuckDB might not support ADD COLUMN IF NOT EXISTS
            try:
                conn.execute(
                    f"ALTER TABLE hk_fund_managers ADD COLUMN {col_name} {col_type}"
                )
            except Exception:
                pass


def update_manager_wiki(conn, ce_number: str, wiki_data: Dict[str, Any]) -> bool:
    """Update a single manager with Wikipedia enrichment data."""
    updates = []
    params = []

    field_map = {
        "wiki_en_title": wiki_data.get("en_title"),
        "wiki_zh_title": wiki_data.get("zh_title"),
        "wiki_en_categories": wiki_data.get("en_categories"),
        "wiki_zh_categories": wiki_data.get("zh_categories"),
    }

    for col, value in field_map.items():
        if value is not None:
            updates.append(f"{col} = ?")
            params.append(value)

    if not updates:
        return False

    updates.append("last_updated = now()")
    params.append(ce_number)

    sql = f"UPDATE hk_fund_managers SET {', '.join(updates)} WHERE ce_number = ?"
    conn.execute(sql, params)
    return True


def main():
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    conn = init_db()
    ensure_wiki_columns(conn)

    args = sys.argv[1:]

    if "--ce" in args:
        idx = args.index("--ce")
        ce = args[idx + 1]
        rows = conn.execute(
            "SELECT ce_number, company_name_en, company_name_cn FROM hk_fund_managers WHERE ce_number = ?",
            [ce],
        ).fetchall()
    else:
        limit = None
        if "--limit" in args:
            idx = args.index("--limit")
            limit = int(args[idx + 1])

        query = """
            SELECT ce_number, company_name_en, company_name_cn FROM hk_fund_managers
            WHERE ce_number IS NOT NULL AND ce_number != ''
            AND license_status = 'active'
            AND regulated_activity_9 = true
            AND company_name_en IS NOT NULL
            AND (wiki_en_title IS NULL AND wiki_zh_title IS NULL)
            ORDER BY ce_number
        """
        if limit:
            query += f" LIMIT {limit}"

        rows = conn.execute(query).fetchall()
        logger.info(f"Found {len(rows)} managers to enrich")

    enriched = 0
    not_found_en = 0
    not_found_zh = 0

    # Cache langlinks results to avoid repeated API calls for the same EN page
    langlinks_cache: Dict[int, Optional[Dict[str, str]]] = {}

    for i, (ce_number, name_en, name_cn) in enumerate(rows):
        try:
            wiki_data = {}

            # EN Wikipedia
            if name_en:
                result_en = search_wikipedia(name_en, lang="en")
                time.sleep(REQUEST_DELAY)

                if result_en:
                    wiki_data["en_title"] = result_en["title"]
                    cats_en = get_categories(result_en["pageid"], lang="en")
                    time.sleep(REQUEST_DELAY * 0.5)
                    if cats_en:
                        wiki_data["en_categories"] = "|".join(cats_en[:15])

                    # Use langlinks to get ZH equivalent directly (most reliable)
                    pid = result_en["pageid"]
                    if pid not in langlinks_cache:
                        langlinks_cache[pid] = get_zh_from_en(pid)
                        time.sleep(REQUEST_DELAY * 0.3)
                    zh_from_en = langlinks_cache[pid]
                    if zh_from_en:
                        wiki_data["zh_title"] = zh_from_en["title"]
                        # Try to get ZH page ID for categories
                        zh_lookup = search_wikipedia(zh_from_en["title"], lang="zh")
                        if zh_lookup:
                            cats_zh = get_categories(zh_lookup["pageid"], lang="zh")
                            time.sleep(REQUEST_DELAY * 0.3)
                            if cats_zh:
                                wiki_data["zh_categories"] = "|".join(cats_zh[:15])
                else:
                    not_found_en += 1

            # ZH Wikipedia fallback: search directly if no EN→ZH link found
            # Only use Chinese name for ZH search — English names on ZH Wikipedia are unreliable
            if not wiki_data.get("zh_title") and name_cn:
                result_zh = search_wikipedia(name_cn, lang="zh")
                time.sleep(REQUEST_DELAY)
                if result_zh:
                    wiki_data["zh_title"] = result_zh["title"]
                    cats_zh = get_categories(result_zh["pageid"], lang="zh")
                    time.sleep(REQUEST_DELAY * 0.3)
                    if cats_zh:
                        wiki_data["zh_categories"] = "|".join(cats_zh[:15])
                else:
                    not_found_zh += 1
            elif not wiki_data.get("zh_title"):
                not_found_zh += 1

            if wiki_data:
                update_manager_wiki(conn, ce_number, wiki_data)
                enriched += 1

            if (i + 1) % 20 == 0:
                logger.info(
                    f"  Progress: {i+1}/{len(rows)} — "
                    f"enriched={enriched}, en_not_found={not_found_en}, zh_not_found={not_found_zh}"
                )

        except Exception as e:
            logger.warning(f"  Failed for {ce_number}: {e}")
            time.sleep(REQUEST_DELAY * 2)

    conn.commit()
    conn.close()

    logger.info(
        f"Done: enriched={enriched}, en_not_found={not_found_en}, zh_not_found={not_found_zh}"
    )


if __name__ == "__main__":
    main()
