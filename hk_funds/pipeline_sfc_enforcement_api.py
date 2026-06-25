"""
SFC enforcement pipeline — fetch all enforcement news from SFC e-Distribution API.

Uses the SFC search API to get all press release titles, filters for enforcement-
related ones, parses company names / fine amounts / dates from titles, matches
against hk_fund_managers, and stores in hk_manager_regulatory_history.

Usage:
    python3 -m hk_funds.pipeline_sfc_enforcement_api              # full run
    python3 -m hk_funds.pipeline_sfc_enforcement_api --dry-run    # preview only
    python3 -m hk_funds.pipeline_sfc_enforcement_api --overwrite  # refetch all
"""

from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

import requests

from hk_funds.storage import init_db, upsert_manager_regulatory

logger = logging.getLogger("hk_funds.pipeline_sfc_enforcement_api")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
}

SEARCH_URL = "https://apps.sfc.hk/edistributionWeb/api/news/search"
CONTENT_URL = "https://apps.sfc.hk/edistributionWeb/api/news/content"
PAGE_SIZE = 200  # Max per page

# Keywords that indicate an enforcement-related press release
ENFORCEMENT_KEYWORDS = [
    "reprimand", "fine", "fined", "ban", "banned", "suspend", "suspension",
    "revoke", "revocation", "disciplinary", "discipline",
    "convict", "conviction", "jail", "imprison", "sentenced",
    "prosecut", "guilt", "insider dealing", "market misconduct",
    "compensation order", "disqualif", "cold-shoulder",
    "unlicensed", "regulatory breach", "money laundering",
    "false trading", "ramp-and-dump", "sponsor failure",
    "court", "tribunal", "enforcement action",
]


def _is_enforcement(title: str) -> bool:
    """Check if a press release title is enforcement-related."""
    t = title.lower()
    # Must mention SFC action — skip purely informational announcements
    has_action = any(kw in t for kw in [
        "reprimand", "fine", "fined", "ban", "banned", "suspend", "revok",
        "convict", "jail", "imprison", "sentenc", "prosecut", "disqualif",
        "disciplinary action", "disciplinary proceedings",
        "compensation order", "cold-shoulder", "insider dealing",
        "market misconduct tribunal", "unlicensed", "regulatory breach",
        "money laundering", "false trading",
    ])
    if not has_action:
        return False

    # Exclude non-enforcement
    exclude = [
        "sfat affirms sfc decision",  # appeals court affirmations — duplicates
    ]
    if any(ex in t for ex in exclude):
        return False

    return True


def fetch_all_titles() -> List[Dict[str, Any]]:
    """Fetch all SFC press releases by iterating over years and months.

    The SFC search API ignores the 'page' parameter and always returns the first
    200 results. To work around this, we query by year (and by year+month for
    years with >200 releases) to stay under the 200-result window.
    """
    all_items = []
    seen = set()

    # Determine which years are available (sitemap goes back to ~1997)
    # Start from current year and go backwards
    current_year = datetime.now().year
    years_to_check = list(range(current_year, 1996, -1))

    for year in years_to_check:
        # First check total for this year
        try:
            r = requests.post(
                SEARCH_URL,
                headers=HEADERS,
                json={"lang": "EN", "year": year, "pageSize": 10},
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
            year_total = data.get("total", 0)
        except Exception as e:
            logger.warning(f"Error checking year {year}: {e}")
            continue

        if year_total == 0:
            continue

        if year_total <= 200:
            # Can fetch all at once
            _fetch_and_add(year, None, all_items, seen)
        else:
            # Fetch by month to stay under 200
            logger.info(f"  Year {year}: {year_total} items — fetching by month")
            for month in range(1, 13):
                _fetch_and_add(year, month, all_items, seen)
                time.sleep(0.05)

        if year % 5 == 0:
            logger.info(f"  Progress: through {year}, collected {len(all_items)} unique items")

    logger.info(f"Collected {len(all_items)} unique items across all years")
    return all_items


def _fetch_and_add(
    year: int, month: Optional[int], all_items: List[Dict], seen: set
):
    """Fetch results for a year or year+month and add to collection."""
    payload = {"lang": "EN", "year": year, "pageSize": 200}
    if month is not None:
        payload["month"] = month

    try:
        r = requests.post(SEARCH_URL, headers=HEADERS, json=payload, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logger.warning(f"Error fetching {year}" + (f"-{month:02d}" if month else "") + f": {e}")
        return

    for item in data.get("items", []):
        ref = item.get("newsRefNo")
        if ref and ref not in seen:
            seen.add(ref)
            all_items.append(item)


def fetch_content(ref_no: str) -> Optional[Dict[str, Any]]:
    """Fetch full content for a specific press release."""
    try:
        r = requests.get(
            CONTENT_URL,
            params={"refNo": ref_no, "lang": "EN"},
            headers=HEADERS,
            timeout=15,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.debug(f"Failed to fetch content for {ref_no}: {e}")
        return None


def _parse_fine_amount(text: str) -> Optional[float]:
    """Parse fine/penalty amount from text. Handles various formats.

    Formats:
      - "$9 million" / "HK$9 million"
      - "HK$10.85 million" / "HK$1.2 billion"
      - "$400,000" / "HK$400,000"
      - "HK$4.2 million" / "$66.4 million"
    """
    # Match: (HK|US|HKD)$ XX.X million/billion/thousand or (HK|US|HKD)$ XXX,XXX
    m = re.search(
        r"(?:HK\s*\$?\s*|HKD\s*|US\s*\$?\s*|USD\s*)\$?\s*([\d,]+(?:\.\d+)?)\s*(million|billion|trillion|thousand)?",
        text,
        re.IGNORECASE,
    )
    if not m:
        # Match "fined HK$XX" or "fine of HK$XX"
        m = re.search(
            r"(?:fine|fined|penalty|compensation)\s+(?:of\s+)?(?:HK\s*\$?\s*|HKD\s*)?\$?\s*([\d,]+(?:\.\d+)?)\s*(million|billion|trillion|thousand)?",
            text,
            re.IGNORECASE,
        )
    if not m:
        return None

    amount_str = m.group(1).replace(",", "")
    unit = (m.group(2) or "").lower()

    try:
        amount = float(amount_str)
    except ValueError:
        return None

    if unit == "million":
        amount *= 1_000_000
    elif unit == "billion":
        amount *= 1_000_000_000
    elif unit == "trillion":
        amount *= 1_000_000_000_000
    elif unit == "thousand":
        amount *= 1_000

    return amount


def _parse_action_type(title: str, html: str = "") -> str:
    """Determine enforcement action type from title and content."""
    text = (title + " " + (html or "")).lower()

    # Order matters — check strongest signals first
    if re.search(r"life.*ban|banned.*life|licen[cs]e.*revok|revok.*licen[cs]e", text):
        return "licence_revocation"
    if "jail" in text or "imprison" in text or "sentenced" in text:
        return "prosecution"
    if "convict" in text or "guilt" in text:
        return "prosecution"
    if "compensation order" in text:
        return "compensation_order"
    if "disqualif" in text:
        return "disqualification"
    if "fine" in text or "fined" in text:
        return "fine"
    if "reprimand" in text:
        return "reprimand"
    if "suspend" in text or "suspension" in text:
        return "suspension"
    if "ban" in text or "banned" in text or "prohibited" in text:
        return "ban"
    if "revok" in text:
        return "licence_revocation"
    if "cold-shoulder" in text:
        return "cold_shoulder"
    return "enforcement"


def _extract_company_name(title: str) -> Optional[str]:
    """Extract the primary company name from an enforcement title.

    Uses common patterns in SFC enforcement titles.
    """
    company_indicators = [
        "limited", "ltd", "bank", "group", "corp", "international",
        "securities", "capital", "management", "financial", "holdings",
        "company", "inc", "plc", "ag", "co.", "llc", "asia", "hong kong",
        "investment", "asset", "fund",
    ]

    # Pattern 1: "SFC reprimands and fines COMPANY $XX million for|over..."
    # Match until $amount OR for|over|in|under (whichever comes first)
    m = re.search(
        r"(?:reprimands?\s+(?:and\s+)?)?(?:fines?|bans?|suspends?|revokes?|disciplines?)\s+"
        r"(.+?)\s+(?:HK\$\s*[\d,\.]+\s*(?:million|billion|thousand)?"
        r"|\$\s*[\d,\.]+\s*(?:million|billion|thousand)?"
        r"|for\s+|over\s+|in\s+|under\s+|$)",
        title,
        re.IGNORECASE,
    )
    if m:
        candidate = m.group(1).strip()
        if not any(w in candidate.lower() for w in ["former", "director", "executive", "officer", "manager"]):
            if any(ind in candidate.lower() for ind in company_indicators):
                return _clean_company_name(candidate)

    # Pattern 2: "... against/of COMPANY" — extract company name from end
    # "against three former directors ... of Superb Summit International Group Limited"
    m = re.search(
        r"(?:against|of|from)\s+"
        r"(.+?(?:Limited|Ltd|Bank|Group|Corp|International|"
        r"Securities|Capital|Management|Financial|Holdings|"
        r"Company|Inc|PLC|AG|LLC))",
        title,
        re.IGNORECASE,
    )
    if m:
        candidate = m.group(1).strip()
        # Extract just the company name from "X of Y of Company Limited"
        # Take the longest company-name-looking part at the end
        parts = re.split(r"\s+(?:of|at)\s+", candidate)
        # Take the last part that looks like a company
        for part in reversed(parts):
            part = part.strip()
            if any(ind in part.lower() for ind in company_indicators):
                # Filter out parts that are just job titles
                if not re.match(
                    r"^(?:former|ex-|senior|chief|executive|director|officer|manager|chairman|president|vice|head|"
                    r"three|four|five|six|seven|eight|nine|ten|a\s+|the\s+|its\s+|their\s+)",
                    part, re.IGNORECASE
                ):
                    return _clean_company_name(part)

    # Pattern 3: "SFC bans/fines COMPANY ..."
    m = re.search(
        r"(?:bans?|fines?|suspends?)\s+"
        r"([A-Z][A-Za-z\s&\.\-]+?(?:Limited|Ltd|Bank|Group|Corp|International|"
        r"Securities|Capital|Management|Financial|Holdings|Company|Inc|PLC|AG|LLC|Asia))",
        title,
    )
    if m:
        candidate = m.group(1).strip()
        # Exclude individual names (too short or no company indicators)
        if any(ind in candidate.lower() for ind in company_indicators):
            return _clean_company_name(candidate)

    return None


def _clean_company_name(name: str) -> str:
    """Clean up a company name."""
    # Remove trailing " for ..." clauses
    name = re.sub(r"\s+for\s+.*$", "", name)
    name = re.sub(r"\s+over\s+.*$", "", name)
    name = re.sub(r"\s+in\s+connection\s+.*$", "", name)
    # Remove trailing commas and periods
    name = name.rstrip(",.")
    name = name.strip()
    return name


def _parse_date(date_str: str) -> Optional[str]:
    """Parse SFC issue date to YYYY-MM-DD."""
    if not date_str:
        return None
    # Format: "2026-02-09T16:30:00"
    try:
        return date_str[:10]
    except Exception:
        return None


def _normalize_name(name: str) -> str:
    """Normalize for matching: lowercase, strip legal suffixes."""
    n = name.lower().strip()
    n = re.sub(r"\s*\([^)]*\)", "", n)
    n = re.sub(r"[,.]+", "", n)
    suffixes = [
        "limited", "ltd", "llc", "inc", "corporation", "corp", "plc",
        "company", "co", "ag", "sa", "l.l.c.", "l.p.", "l.l.p.",
    ]
    for suffix in suffixes:
        n = re.sub(rf"\s+{re.escape(suffix)}\s*$", "", n)
    n = re.sub(r"\s+", " ", n).strip()
    return n


def match_manager(conn, company_name: str) -> Optional[Tuple[int, str, str]]:
    """Match an enforcement company name to hk_fund_managers.

    Returns (manager_id, matched_name_en, matched_name_cn) or None.
    """
    normalized = _normalize_name(company_name)

    # Try exact match first
    rows = conn.execute(
        """SELECT id, company_name_en, company_name_cn FROM hk_fund_managers
           WHERE LOWER(company_name_en) = LOWER(?)
              OR LOWER(company_name_cn) = LOWER(?)
           LIMIT 1""",
        [company_name.strip(), company_name.strip()],
    ).fetchall()
    if rows:
        return (rows[0][0], rows[0][1], rows[0][2])

    # Try normalized match against all active managers
    rows = conn.execute(
        """SELECT id, company_name_en, company_name_cn FROM hk_fund_managers
           WHERE license_status = 'active'""",
    ).fetchall()

    best_match = None
    best_score = 0

    for row in rows:
        mgr_id, name_en, name_cn = row[0], row[1], row[2]
        for name in [name_en, name_cn]:
            if not name:
                continue
            norm_mgr = _normalize_name(name)
            if not norm_mgr:
                continue

            if norm_mgr == normalized:
                return (mgr_id, name_en, name_cn)

            if len(normalized) >= 4 and len(norm_mgr) >= 4:
                if normalized in norm_mgr or norm_mgr in normalized:
                    score = min(len(normalized), len(norm_mgr)) / max(
                        len(normalized), len(norm_mgr)
                    )
                    if score > best_score:
                        best_score = score
                        best_match = (mgr_id, name_en, name_cn)

    if best_score >= 0.8:
        return best_match

    return None


def parse_enforcement_from_title(item: Dict[str, Any], fetch_full: bool = True) -> Optional[Dict[str, Any]]:
    """Parse enforcement data from a search result item's title.

    If fetch_full is True, fetches full content from the API for better accuracy.
    Set to False for fast dry-run mode (title-only parsing).
    """
    title = item.get("title", "")
    if not title:
        return None

    if not _is_enforcement(title):
        return None

    # Try to get more accurate info from full content
    ref_no = item.get("newsRefNo", "")
    html = ""
    if fetch_full and ref_no:
        content = fetch_content(ref_no)
        if content:
            html = content.get("html", "")
            # Content may have a more accurate title
            if content.get("title"):
                title = content["title"]

    company_name = _extract_company_name(title)
    if not company_name:
        # Try parsing from HTML
        if html:
            # Look for first company mention in HTML
            company_name = _extract_company_name(
                re.sub(r"<[^>]+>", " ", html[:2000])
            )

    penalty = _parse_fine_amount(title)
    if not penalty and html:
        penalty = _parse_fine_amount(re.sub(r"<[^>]+>", " ", html[:2000]))

    action_type = _parse_action_type(title, html)
    action_date = _parse_date(item.get("issueDate", ""))

    return {
        "company_name": company_name,
        "action_type": action_type,
        "action_date": action_date,
        "penalty_amount_hkd": penalty,
        "description_en": title[:500],
        "source": "sfc_enforcement_news",
        "source_ref_no": ref_no,
        "source_url": f"https://apps.sfc.hk/edistributionWeb/gateway/EN/news-and-announcements/news/doc?refNo={ref_no}",
    }


def main():
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    dry_run = "--dry-run" in sys.argv
    overwrite = "--overwrite" in sys.argv

    conn = init_db()

    # Step 1: Fetch all press release titles from SFC search API
    logger.info("Step 1: Fetching all press release titles from SFC API...")
    all_items = fetch_all_titles()
    logger.info(f"  Total items: {len(all_items)}")

    # Step 2: Filter for enforcement-related titles
    logger.info("Step 2: Filtering for enforcement-related press releases...")
    enforcement_items = []
    for item in all_items:
        title = item.get("title", "")
        if _is_enforcement(title):
            enforcement_items.append(item)

    logger.info(f"  Found {len(enforcement_items)} enforcement-related titles")

    # Step 3: Parse enforcement data and fetch full content where needed
    logger.info("Step 3: Parsing enforcement data...")
    if dry_run:
        logger.info("  (dry-run: title-only parsing, skipping full content fetch)")
    actions = []
    for i, item in enumerate(enforcement_items):
        if (i + 1) % 100 == 0:
            logger.info(f"  Progress: {i + 1}/{len(enforcement_items)}")

        parsed = parse_enforcement_from_title(item, fetch_full=not dry_run)
        if parsed and parsed.get("company_name"):
            actions.append(parsed)

        # Rate limiting for full content fetches
        if not dry_run and i > 0 and i % 20 == 0:
            time.sleep(0.1)

    logger.info(f"  Successfully parsed {len(actions)} actions with company names")

    if dry_run:
        logger.info("DRY RUN — preview only:")
        # Sort by fine amount desc
        actions.sort(
            key=lambda x: x.get("penalty_amount_hkd") or 0,
            reverse=True,
        )
        for a in actions[:50]:
            logger.info(
                f"  {a.get('company_name', '?'):45s} | {a.get('action_type', '?'):20s} | "
                f"{a.get('action_date', '?'):10s} | "
                f"HK${a.get('penalty_amount_hkd') or 0:,.0f} | "
                f"ref={a.get('source_ref_no', '?')}"
            )
        logger.info(f"... and {len(actions) - 50} more")
        conn.close()
        return

    # Step 4: Deduplicate by source_ref_no
    seen_refs = set()
    deduped = []
    for a in actions:
        ref = a.get("source_ref_no")
        if ref and ref not in seen_refs:
            seen_refs.add(ref)
            deduped.append(a)
    logger.info(f"  After dedup: {len(deduped)} unique actions")

    # Step 5: Match against managers
    logger.info("Step 4: Matching against hk_fund_managers...")
    matched = 0
    unmatched = 0
    regulatory_records = []

    for action in deduped:
        company_name = action.get("company_name")
        if not company_name:
            unmatched += 1
            continue

        match_result = match_manager(conn, company_name)
        if match_result:
            mgr_id, matched_en, matched_cn = match_result
            action_date = action.get("action_date")
            if not action_date:
                action_date = date.today().isoformat()

            regulatory_records.append({
                "manager_id": mgr_id,
                "source": action.get("source", "sfc_enforcement_news"),
                "source_ref_no": action.get("source_ref_no"),
                "action_type": action.get("action_type", "enforcement"),
                "action_date": action_date,
                "penalty_amount_hkd": action.get("penalty_amount_hkd"),
                "description_en": (action.get("description_en") or "")[:500],
                "description_cn": None,
                "source_url": action.get("source_url"),
            })

            matched += 1
            if matched <= 20 or matched % 50 == 0:
                logger.info(
                    f"  MATCHED: {company_name} -> {matched_en} (id={mgr_id})"
                )
        else:
            unmatched += 1

    logger.info(f"Matching: {matched} matched, {unmatched} unmatched")

    # Step 6: Store in DB
    if regulatory_records:
        # Skip existing if not overwrite
        existing_refs = set()
        if not overwrite:
            rows = conn.execute(
                "SELECT DISTINCT source_ref_no FROM hk_manager_regulatory_history WHERE source = 'sfc_enforcement_news'"
            ).fetchall()
            existing_refs = {r[0] for r in rows if r[0]}
            logger.info(f"  {len(existing_refs)} existing records, skipping duplicates")

        new_records = []
        for rec in regulatory_records:
            ref = rec.get("source_ref_no")
            if ref and ref in existing_refs:
                continue
            new_records.append(rec)

        if new_records:
            stored = upsert_manager_regulatory(conn, new_records)
            logger.info(f"Stored {stored} new regulatory records")
        else:
            logger.info("No new records to store")

        # Update enforcement flags on managers
        for rec in regulatory_records:
            mgr_id = rec["manager_id"]
            count = conn.execute(
                "SELECT COUNT(*) FROM hk_manager_regulatory_history WHERE manager_id = ?",
                [mgr_id],
            ).fetchone()[0]
            conn.execute(
                """UPDATE hk_fund_managers
                   SET has_sfc_enforcement_history = true,
                       enforcement_count = ?,
                       last_updated = now()
                   WHERE id = ?""",
                [count, mgr_id],
            )

        conn.commit()

    # Summary
    total = conn.execute(
        "SELECT COUNT(*) FROM hk_manager_regulatory_history"
    ).fetchone()[0]
    flagged = conn.execute(
        "SELECT COUNT(*) FROM hk_fund_managers WHERE has_sfc_enforcement_history = true"
    ).fetchone()[0]
    logger.info(f"Done: {total} total regulatory records, {flagged} managers flagged")

    conn.close()


if __name__ == "__main__":
    main()
