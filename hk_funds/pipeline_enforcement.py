"""
SFC enforcement pipeline — collect enforcement actions and match to HK fund managers.

Data sources:
  - Charltons law firm SFC enforcement roundups (HTML scraping)
  - Web search / manual curated data
  - Future: SFC e-Distribution API, webb-site enforcement data

For each enforcement action:
  1. Parse company name, penalty amount, action type, date
  2. Match to hk_fund_managers by name (EN + CN)
  3. Store in hk_manager_regulatory_history
  4. Update has_sfc_enforcement_history + enforcement_count on managers

Usage:
    python3 -m hk_funds.pipeline_enforcement              # fetch + match
    python3 -m hk_funds.pipeline_enforcement --dry-run    # parse only, don't store
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, date
from typing import Any, Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

from hk_funds.storage import init_db, upsert_manager_regulatory

logger = logging.getLogger("hk_funds.pipeline_enforcement")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}

# Known Charltons enforcement roundup pages
CHARLTONS_ENFORCEMENT_PAGES = [
    "https://www.charltonslaw.com/hong-kong-sfc-enforcement-roundup-may-2026/",
    "https://www.charltonslaw.com/sfc-enforcement-actions-in-january-2026-and-december-2025/",
    "https://www.charltonslaw.com/sfc-enforcement-actions-in-january-and-february-2022/",
]

# Manually curated enforcement data from web search (backup for parsing gaps)
MANUAL_ENFORCEMENT_DATA = [
    {
        "company_name": "Saxo Capital Markets HK Limited",
        "action_type": "fine",
        "action_date": "2026-01-15",
        "penalty_amount_hkd": 4_000_000,
        "description_en": "SFC reprimanded and fined Saxo Capital Markets HK$4 million for regulatory breaches in distributing unauthorised virtual asset funds and VA-related products to retail clients. 1,446 transactions involving 32 VA Products for 130 retail clients without adequate safeguards.",
        "source": "sfc_enforcement_news",
        "source_url": "https://apps.sfc.hk/edistributionWeb/gateway/EN/news-and-announcements/news/enforcement-news/doc?refNo=26PR014",
    },
    {
        "company_name": "EFG Bank AG",
        "action_type": "fine",
        "action_date": "2025-12-01",
        "penalty_amount_hkd": 10_850_000,
        "description_en": "SFC reprimanded and fined EFG Bank AG HK$10.85 million for product due diligence failures, record-keeping deficiencies (unable to locate records for 141 bonds), and failure to report issues promptly spanning 2015-2020.",
        "source": "sfc_enforcement_news",
        "source_url": "https://apps.sfc.hk/edistributionWeb/gateway/EN/news-and-announcements/news/enforcement-news/doc?refNo=25PR122",
    },
    {
        "company_name": "Kylin International (HK) Co., Limited",
        "action_type": "fine",
        "action_date": "2026-02-01",
        "penalty_amount_hkd": 9_000_000,
        "description_en": "SFC reprimanded and fined Kylin International HK$9 million for multiple failures in managing private funds over three years, including failure to manage conflicts of interest, failure to perform reconciliations/valuations/audits, weak KYC and suitability systems, and AML/CTF record-keeping failures.",
        "source": "sfc_enforcement_news",
        "source_url": "https://apps.sfc.hk/edistributionWeb/gateway/EN/news-and-announcements/news/enforcement-news/doc?refNo=26PR025",
    },
    {
        "company_name": "Freeman Commodities Limited",
        "action_type": "fine",
        "action_date": "2025-06-01",
        "penalty_amount_hkd": 3_400_000,
        "description_en": "SFC reprimanded and fined Freeman Commodities Limited (now Arta) HK$3.4 million (would have been HK$9M but for cessation of business). RO suspended 10 months, another RO suspended 4 months. Failures: inadequate due diligence on customer-supplied systems, ineffective AML monitoring, failure to detect suspicious trading/money movements.",
        "source": "sfc_enforcement_news",
        "source_url": "https://apps.sfc.hk/edistributionWeb/gateway/EN/news-and-announcements/news/enforcement-news/doc?refNo=25PR068",
    },
    {
        "company_name": "Nerico Brothers Limited",
        "action_type": "licence_revocation",
        "action_date": "2026-05-26",
        "penalty_amount_hkd": None,
        "description_en": "SFC banned former RO Paul Wan Kai Leung for life for US$154 million client fund misappropriation. SFC previously revoked Nerico's licence and banned its director Jerff Lee Cheuk Fung for life for misuse of client funds and providing false information.",
        "source": "sfc_enforcement_news",
        "source_url": "https://apps.sfc.hk/edistributionWeb/gateway/EN/news-and-announcements/news/enforcement-news/doc?refNo=26PR067",
    },
    {
        "company_name": "UBS AG",
        "action_type": "fine",
        "action_date": "2025-10-01",
        "penalty_amount_hkd": None,
        "description_en": "SFC fined UBS for professional investor misclassification failures.",
        "source": "sfc_enforcement_news",
        "source_url": None,
    },
    {
        "company_name": "Superb Summit International Group Limited",
        "action_type": "compensation_order",
        "action_date": "2026-01-21",
        "penalty_amount_hkd": 595_000_000,
        "description_en": "Court of First Instance ordered three former senior figures to pay HK$595 million compensation. Former shadow director Yang disqualified 15 years, former ED Wu and CFO Chan disqualified 12 years for fraudulent schemes involving fictitious forestry assets and overvalued engineering technology acquisitions.",
        "source": "sfc_enforcement_news",
        "source_url": "https://apps.sfc.hk/edistributionWeb/gateway/EN/news-and-announcements/news/enforcement-news/doc?refNo=26PR011",
    },
]


def fetch_charltons_page(url: str) -> List[Dict[str, Any]]:
    """Fetch and parse enforcement actions from a Charltons enforcement roundup page.

    Returns list of dicts with keys: company_name, action_type, action_date,
    penalty_amount_hkd, description_en, source_url.
    """
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
    except Exception as e:
        logger.warning(f"Failed to fetch {url}: {e}")
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    article = soup.find("article") or soup

    # Find enforcement headings in HTML heading tags or bold tags
    headings = []
    for tag in article.find_all(["h2", "h3", "h4", "strong", "b"]):
        text = tag.get_text().strip()
        if 30 < len(text) < 250:
            if any(kw in text.lower() for kw in [
                "sfc", "fine", "ban", "suspend", "jail", "convict",
                "reprimand", "disciplinary", "penalty", "compensation",
                "million", "billion",
            ]):
                headings.append(text)

    # Skip page title / breadcrumb headings
    skip_patterns = [
        r"^SFC Enforcement\s+(?:Actions|Roundup)",
        r"^Hong Kong SFC Enforcement\s+(?:Actions|Roundup)",
        r"^Hong Kong Law\s+Archives",
    ]
    filtered = []
    for h in headings:
        if not any(re.search(pat, h) for pat in skip_patterns):
            filtered.append(h)

    # Also skip sub-headings that are clearly not about specific enforcement actions
    sub_heading_patterns = [
        r"^(?:Breaches?|Procedural|Background|Investigation|Findings?|Decision|Sanction|Penalty)\s+(?:of|and|History)",
        r"^SFC\s+(?:Disciplinary|Enforcement)\s+Action\s+under\s+s\.\d+",
        r"^Insider Dealing\s+",
        r"^The\s+(?:HK\s*)?SFC\s+(?:also\s+)?(?:found|considered|noted|determined|brought)",
    ]
    # Skip individual-level enforcement (not company-related)
    individual_patterns = [
        r"^(?:Film\s+)?Producer\s+\w+\s+\w+\s+Convicted",  # "Film Producer X Convicted"
        r"^(?:Retail\s+)?Trader\s+Convicted",  # "Retail Trader Convicted"
        r"^Hong Kong Licensed Representative\s+Suspended",  # individual rep
        r"^Former\s+\w+\s+(?:Vice\s+)?President\s+(?:of|at)\s+\w+\s+Jailed",  # individual
        r"^China\s+All\s+Access\s+Former\s+Executive\s+Director\s+Jailed",  # individual
        r"^\w+\s+\w+\s+\w+\s+(?:Jailed|Convicted|Banned|Sentenced)\s+for\s+Insider",  # individual insider
    ]
    actions = []
    for h in filtered:
        if any(re.search(pat, h) for pat in sub_heading_patterns):
            continue
        if any(re.search(pat, h) for pat in individual_patterns):
            continue
        action = _parse_enforcement_heading(h, url)
        if action and action.get("company_name"):
            actions.append(action)

    return actions


def _parse_enforcement_heading(heading: str, source_url: str) -> Optional[Dict[str, Any]]:
    """Parse a single enforcement heading into structured data."""
    action = {
        "heading": heading,
        "source_url": source_url,
        "source": "charltons",
        "company_name": None,
        "action_type": "enforcement",
        "action_date": None,
        "penalty_amount_hkd": None,
        "description_en": heading,  # Use heading as description
    }

    # --- Company name extraction ---
    company = None

    # "SFC Reprimands and Fines COMPANY HK$..."
    m = re.search(
        r"(?:Reprimands?\s+(?:and\s+)?)?(?:Fines?|Bans?|Suspends?|Revokes?)\s+"
        r"(.+?)\s+(?:HK\$\s*[\d,\.]+\s*(?:million|billion)?|for\s|over\s|in\s|under\s|$)",
        heading,
    )
    if m:
        candidate = m.group(1).strip()
        # Filter out generic phrases
        if candidate.lower() not in ("the", "a", "an", "its", "their", "former"):
            company = candidate

    if not company:
        # "SFC Obtains ... against COMPANY"
        m = re.search(r"(?:against|from)\s+(.+?)(?:\s+HK\$|\s+for\s|\s+in\s|$)", heading)
        if m:
            candidate = m.group(1).strip()
            if any(term in candidate.lower() for term in [
                "limited", "ltd", "bank", "group", "corp", "international",
                "securities", "capital", "management", "financial", "holdings",
                "company", "inc", "plc",
            ]):
                company = candidate

    if not company:
        # "Former X of/at COMPANY ..."
        m = re.search(
            r"(?:Former|Ex-)\s+\w+(?:\s+\w+)?\s+(?:of|at)\s+"
            r"(.+?)(?:\s+(?:Jailed|Convicted|Banned|Sentenced|for|over|in|$))",
            heading,
        )
        if m:
            candidate = m.group(1).strip()
            if len(candidate.split()) >= 2:
                company = candidate

    if not company:
        # "COMPANY Former Executive ... Jailed" or "COMPANY ..."
        m = re.search(
            r"^([A-Z][A-Za-z\s&\.]+(?:Limited|Ltd|Bank|Group|Corp|International|"
            r"Securities|Capital|Management|Financial|Holdings|Company|Inc|PLC|AG))",
            heading,
        )
        if m:
            company = m.group(1).strip()

    if not company:
        # "Hong Kong SFC Bans COMPANY ..."
        m = re.search(
            r"(?:Bans|Fines|Suspends|Reprimands)\s+(.+?)\s+(?:Former|for|over|in|under|HK\$|$)",
            heading,
        )
        if m:
            candidate = m.group(1).strip()
            parts = candidate.split()
            if len(parts) >= 2 and not all(
                w[0].isupper() and len(w) < 8 for w in parts[:2]
            ):
                company = candidate

    # Clean up company name
    if company:
        company = re.sub(r"\s+Former\s+.*$", "", company)
        company = re.sub(r"\s+(?:Former|Ex-)\s+.*$", "", company)
        company = company.strip()
        if len(company) < 4:
            company = None

    action["company_name"] = company

    # --- Penalty amount ---
    m = re.search(
        r"HK\$\s*([\d,\.]+)\s*(million|billion|trillion)?",
        heading,
        re.IGNORECASE,
    )
    if m:
        amount = float(m.group(1).replace(",", ""))
        unit = m.group(2)
        if unit and unit.lower() == "million":
            amount *= 1_000_000
        elif unit and unit.lower() == "billion":
            amount *= 1_000_000_000
        action["penalty_amount_hkd"] = amount

    # --- Action type ---
    hlower = heading.lower()
    # Check licence_revocation first (strongest signal)
    if re.search(r"life.*ban|banned.*life|licen[cs]e.*revok|revok.*licen[cs]e", hlower):
        action["action_type"] = "licence_revocation"
    elif "ban" in hlower and ("life" in hlower or "re-entering" in hlower):
        action["action_type"] = "licence_revocation"
    # Check fine/reprimand BEFORE ban — "Reprimands and Fines" should be fine
    elif "fine" in hlower or "fined" in hlower:
        action["action_type"] = "fine"
    elif "reprimand" in hlower:
        action["action_type"] = "reprimand"
    elif "suspend" in hlower or "suspension" in hlower:
        action["action_type"] = "suspension"
    elif "ban" in hlower or "banned" in hlower or "prohibited" in hlower:
        action["action_type"] = "ban"
    elif "jail" in hlower or "imprison" in hlower or "convict" in hlower or "guilt" in hlower:
        action["action_type"] = "prosecution"
    elif "compensation" in hlower or "disqualif" in hlower:
        action["action_type"] = "compensation_order"

    # --- Date ---
    m = re.search(
        r"(\d{1,2})\s+(January|February|March|April|May|June|July|August|"
        r"September|October|November|December)\s+(\d{4})",
        heading,
    )
    if m:
        try:
            action["action_date"] = datetime.strptime(
                f"{m.group(1)} {m.group(2)} {m.group(3)}", "%d %B %Y"
            ).strftime("%Y-%m-%d")
        except ValueError:
            pass

    if not action["action_date"]:
        m = re.search(
            r"(January|February|March|April|May|June|July|August|"
            r"September|October|November|December)\s+(\d{4})",
            heading,
        )
        if m:
            try:
                action["action_date"] = datetime.strptime(
                    f"1 {m.group(1)} {m.group(2)}", "%d %B %Y"
                ).strftime("%Y-%m-%d")
            except ValueError:
                pass

    return action


def _normalize_name(name: str) -> str:
    """Normalize a company name for matching: lowercase, strip legal suffixes."""
    n = name.lower().strip()
    n = re.sub(r"\s*\([^)]*\)", "", n)  # Remove parenthesized content
    n = re.sub(r"[,.]+", "", n)
    suffixes = [
        "limited", "ltd", "llc", "inc", "corporation", "corp", "plc",
        "company", "co", "ag", "sa", "l\.l\.c\.", "l\.p\.", "l\.l\.p\.",
    ]
    # Strip common suffixes
    for suffix in suffixes:
        n = re.sub(rf"\s+{re.escape(suffix)}\s*$", "", n)
    n = re.sub(r"\s+", " ", n).strip()
    return n


def match_manager(
    conn, company_name: str
) -> Optional[Tuple[int, str, str]]:
    """Match an enforcement company name to hk_fund_managers.

    Returns (manager_id, matched_name_en, matched_name_cn) or None.
    """
    normalized = _normalize_name(company_name)

    # Try exact match first (case-insensitive)
    rows = conn.execute(
        """SELECT id, company_name_en, company_name_cn FROM hk_fund_managers
           WHERE LOWER(company_name_en) = LOWER(?)
              OR LOWER(company_name_cn) = LOWER(?)
           LIMIT 1""",
        [company_name.strip(), company_name.strip()],
    ).fetchall()
    if rows:
        return (rows[0][0], rows[0][1], rows[0][2])

    # Try normalized match
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

            # Exact normalized match
            if norm_mgr == normalized:
                return (mgr_id, name_en, name_cn)

            # Token overlap match
            if len(normalized) >= 4 and len(norm_mgr) >= 4:
                if normalized in norm_mgr or norm_mgr in normalized:
                    score = min(len(normalized), len(norm_mgr)) / max(
                        len(normalized), len(norm_mgr)
                    )
                    if score > best_score:
                        best_score = score
                        best_match = (mgr_id, name_en, name_cn)

    # High-confidence partial match
    if best_score >= 0.8:
        return best_match

    return None


def collect_all_enforcement_actions(conn) -> List[Dict[str, Any]]:
    """Collect enforcement actions from all sources."""

    all_actions = []

    # Source 1: Charltons law firm pages
    for url in CHARLTONS_ENFORCEMENT_PAGES:
        logger.info(f"Fetching Charltons: {url}")
        actions = fetch_charltons_page(url)
        logger.info(f"  Parsed {len(actions)} actions")
        all_actions.extend(actions)
        if url != CHARLTONS_ENFORCEMENT_PAGES[-1]:
            time.sleep(1)  # Be polite

    # Source 2: Manual curated data
    logger.info(f"Adding {len(MANUAL_ENFORCEMENT_DATA)} manual enforcement records")
    all_actions.extend(MANUAL_ENFORCEMENT_DATA)

    # Deduplicate by company_name (case-insensitive)
    # Manual data takes precedence over Charltons parsing (better dates/amounts)
    seen = {}
    for a in all_actions:
        key = _normalize_name(a.get("company_name", ""))
        if not key:
            continue
        is_manual = a.get("source") != "charltons"
        if key not in seen or (is_manual and seen[key].get("source") == "charltons"):
            seen[key] = a
    deduped = list(seen.values())

    logger.info(f"Total unique enforcement actions: {len(deduped)}")
    return deduped


def main():
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    dry_run = "--dry-run" in sys.argv

    conn = init_db()

    # Collect enforcement actions
    actions = collect_all_enforcement_actions(conn)

    if dry_run:
        logger.info("DRY RUN — parsing only, not storing:")
        for a in actions:
            logger.info(
                f"  {a.get('company_name'):40s} | {a.get('action_type'):20s} | "
                f"{a.get('action_date') or '????-??-??':10s} | "
                f"HK${a.get('penalty_amount_hkd') or 0:,.0f}"
            )
        conn.close()
        return

    # Match against managers
    matched = 0
    unmatched = 0
    regulatory_records = []

    for action in actions:
        company_name = action.get("company_name")
        if not company_name:
            unmatched += 1
            continue

        match_result = match_manager(conn, company_name)
        if match_result:
            mgr_id, matched_en, matched_cn = match_result
            action_date = action.get("action_date")
            if not action_date:
                # Use today as fallback for NOT NULL constraint
                action_date = date.today().isoformat()
            regulatory_records.append({
                "manager_id": mgr_id,
                "source": action.get("source", "sfc_enforcement"),
                "source_ref_no": None,
                "action_type": action.get("action_type", "enforcement"),
                "action_date": action_date,
                "penalty_amount_hkd": action.get("penalty_amount_hkd"),
                "description_en": action.get("description_en", "")[:500] if action.get("description_en") else None,
                "description_cn": None,
                "source_url": action.get("source_url"),
            })
            matched += 1
            logger.info(
                f"  MATCHED: {company_name} -> {matched_en} (id={mgr_id})"
            )
        else:
            unmatched += 1
            logger.debug(f"  UNMATCHED: {company_name}")

    logger.info(f"Matching results: {matched} matched, {unmatched} unmatched")

    # Store in regulatory history
    if regulatory_records:
        stored = upsert_manager_regulatory(conn, regulatory_records)
        logger.info(f"Stored {stored} regulatory records")

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
    logger.info(f"Done: {total} regulatory records, {flagged} managers flagged")

    conn.close()


if __name__ == "__main__":
    main()
