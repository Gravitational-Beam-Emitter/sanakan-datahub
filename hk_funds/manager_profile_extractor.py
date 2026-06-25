"""
LLM-based manager profile extractor — scrapes About/Team/Contact pages and
extracts structured company profile data from unstructured website text.

Feeds into:
  - manager DD 10-dimension scoring (human_resources, financial_resources, etc.)
  - fund risk rating Scorecard (Internal Control factor, 14%)
  - hk_manager_profiles table

Usage:
    python -m hk_funds.manager_profile_extractor --manager-id 123
    python -m hk_funds.manager_profile_extractor --batch --limit 20
    python -m hk_funds.manager_profile_extractor --all-connected
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

from hk_funds.manager_connectors.base import BaseManagerConnector, get_connector_registry

logger = logging.getLogger("hk_funds.manager_profile_extractor")

# ── LLM Provider Configuration (same pattern as nde_extractor.py) ──

PROVIDERS = [
    {"name": "deepseek",  "style": "openai",    "model": "deepseek-chat",
     "base_url": "https://api.deepseek.com", "env_var": "DEEPSEEK_API_KEY"},
    {"name": "anthropic", "style": "anthropic",  "model": "claude-haiku-4-5-20251001",
     "base_url": None, "env_var": "ANTHROPIC_API_KEY"},
    {"name": "qwen",      "style": "openai",    "model": "qwen-plus",
     "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "env_var": "QWEN_API_KEY"},
]


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


# ── LLM Prompts ──

SYSTEM_PROMPT = """You are a financial data analyst extracting structured company profiles
from Hong Kong fund manager websites. You analyze scraped webpage text and extract
key facts about the asset management company.

Return ONLY valid JSON. No explanation, no markdown other than the JSON block.
Use null for unknown/not-found values. Be conservative — don't hallucinate."""


EXTRACT_PROMPT = """Analyze the following webpage text scraped from a Hong Kong fund
manager's website ({company_name}, CE number: {ce_number}).

Extract these fields into a JSON object:

  - company_description_en: 1-3 sentence description of the firm (English)
  - company_description_cn: 1-2 sentence description if Chinese text is present
  - founding_year: integer year the company was founded (e.g. 1998)
  - total_staff: integer total number of employees/staff
  - investment_professionals: integer number of investment/research professionals
  - offices: list of strings, each describing a city/country office location
    (e.g. ["Hong Kong (HQ)", "Shanghai", "Singapore", "London"])
  - key_personnel: list of objects with {{name, title, bio_summary}} for senior
    executives (CEO, CIO, heads of departments). Max 8 people.
  - awards: list of objects with {{award_name, year, awarding_body}} for notable
    industry awards or recognitions. Max 10.
  - aum_usd: float total AUM in USD (convert if given in HKD or other currency;
    use 1 USD = 7.8 HKD). Can be approximate.
  - aum_date: string year or date of the AUM figure (e.g. "2025" or "2025-12-31")
  - investment_philosophy: 1-2 sentence summary of the firm's investment approach
  - asset_classes: list of asset classes the firm invests in
    (e.g. ["equity", "fixed income", "multi-asset", "alternatives", "private equity"])
  - institutional_clients: boolean whether the firm mentions institutional/pension/
    sovereign clients (not just retail)
  - regulatory_licenses: list of non-SFC licenses/registrations mentioned
    (e.g. ["SEC (US)", "CSRC (China)", "FCA (UK)"])

Guidelines:
  - If information is not found, use null for scalars, [] for arrays.
  - Staff counts: prefer exact numbers; if a range is given, use the midpoint.
  - AUM: prefer the most recent figure. Watch for "US$", "HK$", "$", "USD", "HKD".
  - Offices: include HQ location if identifiable.
  - Awards: only include named awards with clear awarding bodies (e.g. "Best Fund
    Manager 2024, Benchmark"). Skip generic "award-winning" mentions.
  - If the text is mostly about fund products rather than the company, mention
    in company_description_en.

Webpage text:
---
{page_text}
---

Return JSON:"""


def extract_profile_from_text(
    company_name: str,
    ce_number: str,
    page_text: str,
) -> Optional[Dict[str, Any]]:
    """Extract structured company profile from scraped webpage text using LLM.

    Args:
        company_name: Manager company name for context
        ce_number: SFC CE number for context
        page_text: Cleaned visible text from About/Team/Contact pages

    Returns:
        Parsed dict with profile data, or None if extraction failed.
    """
    if not page_text or len(page_text) < 100:
        logger.warning(f"Insufficient page text for {company_name} ({len(page_text or '')} chars)")
        return None

    # Truncate to avoid token limits (most LLMs handle ~8K tokens comfortably)
    max_chars = 12000
    if len(page_text) > max_chars:
        page_text = page_text[:max_chars] + "\n\n[... text truncated ...]"

    prompt = EXTRACT_PROMPT.format(
        company_name=company_name,
        ce_number=ce_number,
        page_text=page_text,
    )

    response = _call_llm(SYSTEM_PROMPT, prompt)
    if not response:
        return None

    try:
        parsed = json.loads(_extract_json(response))
        if not isinstance(parsed, dict):
            logger.warning(f"LLM returned non-dict for {company_name}: {type(parsed)}")
            return None
        return parsed
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse LLM response for {company_name}: {e}")
        logger.debug(f"Raw response: {response[:500]}")
        return None


def extract_company_profile(
    connector: BaseManagerConnector,
    company_name: str,
    ce_number: str,
) -> Optional[Dict[str, Any]]:
    """Full extraction pipeline: scrape pages → LLM extract → return structured data.

    Args:
        connector: Instantiated connector for this manager
        company_name: Manager company name
        ce_number: SFC CE number

    Returns:
        Dict with profile data and metadata, or None.
    """
    logger.info(f"Scraping company profile for {company_name} ({ce_number})")

    raw = connector.scrape_company_profile()

    if not raw["combined_text"]:
        logger.warning(f"No page text scraped for {company_name}")
        return None

    logger.info(
        f"  Scraped {len(raw['source_urls'])} pages for {company_name}: "
        f"{', '.join(raw['source_urls'])} "
        f"({len(raw['combined_text'])} chars)"
    )

    profile = extract_profile_from_text(
        company_name=company_name,
        ce_number=ce_number,
        page_text=raw["combined_text"],
    )

    if profile is None:
        return None

    # Attach metadata
    profile["_meta"] = {
        "source_urls": raw["source_urls"],
        "text_length": len(raw["combined_text"]),
        "extraction_date": datetime.now().strftime("%Y-%m-%d"),
        "connector_class": connector.__class__.__name__,
    }

    return profile


# ── Batch Processing ──

def process_all_connected(conn, limit: int = None) -> List[Dict[str, Any]]:
    """Extract company profiles for all managers with registered connectors.

    Iterates through the connector registry, instantiates each connector,
    scrapes the manager's website, and extracts profile data via LLM.

    Returns list of {manager_id, ce_number, company_name, profile, error}.
    """
    from hk_funds.storage import upsert_manager_profile

    registry = get_connector_registry()

    # Build CE → connector class mapping
    # Also look up manager IDs from the database
    rows = conn.execute("""
        SELECT id, ce_number, company_name_en
        FROM hk_fund_managers
        WHERE license_status = 'active'
        ORDER BY id
    """).fetchall()

    manager_map = {row[1]: {"id": row[0], "name": row[2]} for row in rows}

    results = []
    processed = 0
    skipped_no_connector = 0
    skipped_no_text = 0
    skipped_llm_fail = 0

    for ce_number, connector_cls in registry.items():
        if limit and processed >= limit:
            break

        mgr = manager_map.get(ce_number)
        if not mgr:
            logger.debug(f"CE {ce_number} has connector but no DB record — skipping")
            skipped_no_connector += 1
            continue

        try:
            connector = connector_cls()
            profile = extract_company_profile(
                connector=connector,
                company_name=mgr["name"],
                ce_number=ce_number,
            )

            if profile is None:
                skipped_no_text += 1
                results.append({
                    "manager_id": mgr["id"],
                    "ce_number": ce_number,
                    "company_name": mgr["name"],
                    "profile": None,
                    "error": "No text scraped or LLM extraction failed",
                })
                continue

            # Strip _meta before storing
            meta = profile.pop("_meta", {})
            profile["extraction_date"] = meta.get("extraction_date")
            profile["data_source"] = f"manager_website:{meta.get('connector_class', '')}"

            # Store to database
            stored = upsert_manager_profile(conn, mgr["id"], profile)
            profile["_meta"] = meta
            profile["_stored"] = stored

            results.append({
                "manager_id": mgr["id"],
                "ce_number": ce_number,
                "company_name": mgr["name"],
                "profile": profile,
                "error": None,
            })

            processed += 1
            logger.info(
                f"  [{processed}] Stored profile for {mgr['name']} "
                f"(AUM={profile.get('aum_usd')}, staff={profile.get('total_staff')}, "
                f"offices={len(profile.get('offices') or [])})"
            )

        except Exception as e:
            logger.error(f"Failed to process {mgr['name']} ({ce_number}): {e}")
            skipped_llm_fail += 1
            results.append({
                "manager_id": mgr["id"],
                "ce_number": ce_number,
                "company_name": mgr["name"],
                "profile": None,
                "error": str(e),
            })

    logger.info(
        f"Profile extraction complete: "
        f"processed={processed}, "
        f"no_connector={skipped_no_connector}, "
        f"no_text={skipped_no_text}, "
        f"llm_fail={skipped_llm_fail}"
    )
    return results


# ── CLI ──

def main():
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    from hk_funds.storage import init_db

    conn = init_db()

    args = sys.argv[1:]

    if "--manager-id" in args:
        idx = args.index("--manager-id")
        mgr_id = int(args[idx + 1])
        row = conn.execute(
            "SELECT id, ce_number, company_name_en FROM hk_fund_managers WHERE id = ?",
            [mgr_id],
        ).fetchone()
        if not row:
            print(f"Manager {mgr_id} not found")
            return
        registry = get_connector_registry()
        ce = row[1]
        if ce not in registry:
            print(f"No connector registered for CE {ce} ({row[2]})")
            print(f"Registered CEs: {sorted(registry.keys())[:20]}...")
            return
        connector = registry[ce]()
        profile = extract_company_profile(connector, row[2], ce)
        if profile:
            print(json.dumps(profile, indent=2, ensure_ascii=False, default=str))
        else:
            print("Failed to extract profile")

    elif "--batch" in args or "--all-connected" in args:
        limit = None
        if "--limit" in args:
            idx = args.index("--limit")
            limit = int(args[idx + 1])
        results = process_all_connected(conn, limit=limit)
        success = sum(1 for r in results if r["profile"] is not None)
        print(f"\nDone: {success}/{len(results)} profiles extracted")

    elif "--test-url" in args:
        idx = args.index("--test-url")
        url = args[idx + 1]
        from hk_funds.manager_connectors.base import BaseManagerConnector

        class TestConnector(BaseManagerConnector):
            manager_ce_numbers = []
            base_url = url

            def get_fund_list(self):
                return []

        c = TestConnector()
        raw = c.scrape_company_profile()
        if raw["combined_text"]:
            print(f"Scraped {len(raw['combined_text'])} chars from: {raw['source_urls']}")
            print("\n--- First 500 chars ---")
            print(raw["combined_text"][:500])
        else:
            print("No text scraped")

    else:
        print(__doc__)

    conn.close()


if __name__ == "__main__":
    main()
