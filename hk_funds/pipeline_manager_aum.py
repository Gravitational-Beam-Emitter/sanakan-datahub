"""
Manager AUM estimation pipeline — populate hk_fund_manager_aum.

Data sources:
  1. Fund-level AUM aggregation by brand → HK manager mapping
     e.g., "FIL Investment Management (Luxembourg)" → "FIL Investment Management (Hong Kong)"
  2. Webb-site / Wikipedia for manager size proxy
  3. Future: SFC Wings API, manager website scraping, Morningstar

The management_scale factor (12% weight) uses manager_aum_range type:
  Score 1: AUM >= 1T HKD   (best)
  Score 2: AUM >= 100B HKD
  Score 3: AUM >= 10B HKD
  Score 4: AUM >= 1B HKD
  Score 5: AUM < 1B HKD    (worst)
  Default: 3 (no data)

Usage:
    python3 -m hk_funds.pipeline_manager_aum              # populate AUM
    python3 -m hk_funds.pipeline_manager_aum --dry-run    # preview only
"""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from hk_funds.storage import init_db

logger = logging.getLogger("hk_funds.pipeline_manager_aum")


# Brand mapping: fund manager name substring → HK manager name match
# Maps Luxembourg/Europe fund management entities to their HK affiliates
BRAND_TO_HK_MANAGER = {
    "FIL": "FIL Investment Management (Hong Kong)",
    "BlackRock": "BlackRock Asset Management North Asia",
    "Amundi": "Amundi Hong Kong",
    "Schroders": "Schroder Investment Management (Hong Kong)",
    "Schroder": "Schroder Investment Management (Hong Kong)",
    "BNY Mellon": "BNY Mellon Investment Management Hong Kong",
    "Allianz Global Investors": "Allianz Global Investors Asia Pacific",
    "Allianz": "Allianz Global Investors Asia Pacific",
    "Capital International": "Capital International",
    "Ninety One": "Ninety One Hong Kong",
    "HSBC": "HSBC Global Asset Management (Hong Kong)",
    "JPMorgan": "JPMorgan Asset Management (Asia Pacific)",
    "J.P. Morgan": "JPMorgan Asset Management (Asia Pacific)",
    "Morgan Stanley": "Morgan Stanley Investment Management",
    "Goldman Sachs": "Goldman Sachs Asset Management (Hong Kong)",
    "UBS": "UBS Asset Management (Hong Kong)",
    "Pictet": "Pictet Asset Management (Hong Kong)",
    "Invesco": "Invesco Asset Management Asia",
    "Fidelity": "FIL Investment Management (Hong Kong)",  # Fidelity = FIL
    "Franklin Templeton": "Franklin Templeton Investments (Asia)",
    "Templeton": "Franklin Templeton Investments (Asia)",
    "PIMCO": "PIMCO Asia",
    "Manulife": "Manulife Investment Management (Hong Kong)",
    "Value Partners": "Value Partners Hong Kong",
    "BEA Union": "BEA Union Investment Management",
    "Hang Seng": "Hang Seng Investment Management",
    "BOCI-Prudential": "BOCI-Prudential Asset Management",
    "China Asset Management": "China Asset Management (Hong Kong)",
    "E Fund": "E Fund Management (Hong Kong)",
    "Harvest": "Harvest Global Investments",
    "CSOP": "CSOP Asset Management",
    "Bosera": "Bosera Asset Management (International)",
    "ICBC": "ICBC Asset Management (Global)",
    "CCB": "CCB International Asset Management",
    "CICC": "CICC Asset Management",
    "HFT": "HFT Investment Management (HK)",
    "Da Cheng": "Da Cheng International Asset Management",
    "Hua An": "Hua An Asset Management (Hong Kong)",
    "GF": "GF Asset Management (Hong Kong)",
    # Additional brand mappings from fund-manager linking
    "AllianceBernstein": "AllianceBernstein Hong Kong",
    "Alliance Bernstein": "AllianceBernstein Hong Kong",
    "abrdn": "abrdn Hong Kong",
    "T. Rowe": "T. Rowe Price Hong Kong",
    "Baring": "Baring Asset Management (Asia)",
    "MFS": "MFS International (Hong Kong)",
    "Wellington": "Wellington Management Hong Kong",
    "MSIM": "Morgan Stanley Asia",
    "Principal Global Investors": "Principal Asset Management Company (Asia)",
    "Rongtong": "Rongtong Global Investment",
    "Eastspring": "Eastspring Investments (Hong Kong)",
    "First Sentier": "First Sentier Investors (Hong Kong)",
    "Janus Henderson": "Janus Henderson Investors",
    "Muzinich": "Muzinich",
    "Allspring": "Allspring Global Investments",
}


def _extract_brand(name: str) -> str:
    """Extract brand name from a fund manager name.

    "FIL Investment Management (Luxembourg) S.à r.l." → "FIL"
    "BlackRock (Luxembourg) S.A." → "BlackRock"
    "INVESCO Management S.A." → "INVESCO"
    """
    s = name.strip()
    s = re.sub(r"\s*\([^)]*\)", "", s)  # Remove parentheses
    # Legal suffixes
    s = re.sub(r"\s+S\.à\s*r\.l\.?$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+S\.a\.?\s*r\.?l\.?$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+S\.A\.?$", "", s)
    s = re.sub(r"\s+Sarl$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+GmbH$", "", s)
    s = re.sub(r"\s+Ltd\.?$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+Limited$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+Inc\.?$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+PLC$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+AG$", "", s, flags=re.IGNORECASE)
    # Business suffixes
    s = re.sub(r"\s+(?:Investment|Asset|Fund)\s+Management\s*(?:Company)?\s*$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+Management\s*(?:Company)?\s*$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+Investors?\s*$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+Fund\s+Managers?\s*$", "", s, flags=re.IGNORECASE)
    # Geographic suffixes
    s = re.sub(r"\s+(?:Luxembourg|Europe|International|Global|Asia|Pacific|Ireland|Hong\s+Kong|HK)\s*$", "", s, flags=re.IGNORECASE)
    # Trailing ampersand etc
    s = re.sub(r"\s+&\s*Co\.?\s*$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+\(.*\)$", "", s)
    return s.strip()


def find_hk_manager_by_brand(conn, fund_manager_name: str) -> Optional[Tuple[int, str]]:
    """Find a HK Type 9 manager matching the brand of a fund manager.

    Returns (manager_id, company_name_en) or None.
    """
    brand = _extract_brand(fund_manager_name)
    if not brand or len(brand) < 2:
        return None

    # Try BRAND_TO_HK_MANAGER mapping first
    for brand_key, hk_search in BRAND_TO_HK_MANAGER.items():
        if brand_key.lower() in fund_manager_name.lower():
            rows = conn.execute(
                """SELECT id, company_name_en FROM hk_fund_managers
                   WHERE company_name_en ILIKE ?
                   AND license_status = 'active'
                   AND regulated_activity_9 = true
                   LIMIT 1""",
                [f"%{hk_search}%"],
            ).fetchall()
            if rows:
                return (rows[0][0], rows[0][1])

    # Fallback: direct brand name search
    rows = conn.execute(
        """SELECT id, company_name_en FROM hk_fund_managers
           WHERE company_name_en ILIKE ?
           AND license_status = 'active'
           AND regulated_activity_9 = true
           LIMIT 3""",
        [f"%{brand}%"],
    ).fetchall()

    if not rows:
        return None

    # If multiple matches, prefer the one that looks like an asset management entity
    for r in rows:
        name = r[1].lower()
        if any(kw in name for kw in ["asset management", "investment management", "fund management"]):
            return (r[0], r[1])

    return (rows[0][0], rows[0][1])


def aggregate_fund_aum_by_manager(conn) -> List[Dict[str, Any]]:
    """Aggregate fund_size_hkd by fund_manager_name_en and map to HK managers.

    Returns list of {manager_id, aum, aum_currency, aum_date, aum_source}
    ready for hk_fund_manager_aum.
    """
    # Get all fund AUM data grouped by manager name
    rows = conn.execute("""
        SELECT fund_manager_name_en,
               SUM(fund_size_hkd) as total_aum_hkd,
               MAX(fund_size_date) as latest_date,
               COUNT(*) as fund_count
        FROM hk_funds
        WHERE fund_size_hkd IS NOT NULL
          AND fund_manager_name_en IS NOT NULL
        GROUP BY fund_manager_name_en
        ORDER BY total_aum_hkd DESC
    """).fetchall()

    manager_aums = []
    seen_managers = set()

    for row in rows:
        fund_mgr_name, total_aum_hkd, latest_date, fund_count = row
        if not total_aum_hkd or total_aum_hkd <= 0:
            continue

        # Find matching HK manager
        hk_match = find_hk_manager_by_brand(conn, fund_mgr_name)
        if not hk_match:
            logger.debug(f"  No HK match for brand: {fund_mgr_name}")
            continue

        mgr_id, mgr_name = hk_match
        if mgr_id in seen_managers:
            # Aggregate: add to existing
            for entry in manager_aums:
                if entry["manager_id"] == mgr_id:
                    entry["aum"] += total_aum_hkd
                    entry["fund_count"] += fund_count
                    if latest_date and (not entry["aum_date"] or latest_date > entry["aum_date"]):
                        entry["aum_date"] = latest_date
                    break
        else:
            seen_managers.add(mgr_id)
            manager_aums.append({
                "manager_id": mgr_id,
                "aum": total_aum_hkd,
                "aum_currency": "HKD",
                "aum_date": latest_date,
                "aum_source": "fund_aggregation",
                "aum_raw_text": f"Aggregated from {fund_count} SFC-authorized funds managed by {fund_mgr_name}",
                "fund_count": fund_count,
            })

    return manager_aums


def estimate_aum_from_proxies(conn) -> List[Dict[str, Any]]:
    """Estimate AUM for managers without direct AUM data using available proxies.

    Uses: fund count, Wikipedia presence, company age, SFC license tenure, website.

    Estimation bands (conservative — errs on the low side):
      - 50+ funds → 100B HKD (score 2)
      - 20+ funds → 50B HKD (score 2)
      - 10-19 funds + both wiki → 10B HKD (score 3), else 5B (score 4)
      - 5-9 funds → 2B HKD (score 4)
      - 1-4 funds → 500M HKD (score 5)
      - Both wikis (no funds) → 1B HKD (score 4)
      - One wiki + website + age ≥ 20y → 500M HKD (score 5)
      - One wiki + website → 200M HKD (score 5)
      - Website + age ≥ 15y → 200M HKD (score 5)
      - Website + age ≥ 10y → 100M HKD (score 5)
      - Website → 50M HKD (score 5)
      - Age ≥ 10y → 50M HKD (score 5)
      - Age ≥ 5y → 20M HKD (score 5)
      - Otherwise → skip (insufficient signals)

    Only estimates for managers with at least some signals (fund links, wiki,
    website, or 5+ years incorporation history).
    """
    from datetime import date

    today = date.today()

    # Get ALL managers without AUM
    rows = conn.execute("""
        SELECT m.id, m.company_name_en,
               COALESCE(fc.cnt, 0) as fund_count,
               m.wiki_en_title IS NOT NULL as has_wiki_en,
               m.wiki_zh_title IS NOT NULL as has_wiki_zh,
               m.inc_date,
               m.license_effective_date,
               m.website
        FROM hk_fund_managers m
        LEFT JOIN (
            SELECT manager_id, COUNT(*) as cnt
            FROM hk_fund_manager_funds
            GROUP BY manager_id
        ) fc ON m.id = fc.manager_id
        WHERE m.license_status = 'active'
          AND m.regulated_activity_9 = true
          AND m.id NOT IN (SELECT manager_id FROM hk_fund_manager_aum)
        ORDER BY COALESCE(fc.cnt, 0) DESC
    """).fetchall()

    estimates = []
    for row in rows:
        mgr_id, name, fund_count, has_wiki_en, has_wiki_zh, inc_date, lic_date, website = row

        wiki_both = has_wiki_en and has_wiki_zh
        has_wiki = has_wiki_en or has_wiki_zh
        has_website = bool(website and str(website).strip())

        # Company age in years
        age_years = None
        if inc_date:
            if isinstance(inc_date, str):
                inc_date = date.fromisoformat(inc_date)
            age_years = (today - inc_date).days / 365.25

        # License tenure
        lic_years = None
        if lic_date:
            if isinstance(lic_date, str):
                lic_date = date.fromisoformat(lic_date)
            lic_years = (today - lic_date).days / 365.25

        # ================================================================
        # Tier 1: Fund-count based (strongest signal)
        # ================================================================
        if fund_count >= 50:
            estimated_aum = 100_000_000_000  # 100B HKD
            tier = "large_global"
        elif fund_count >= 20:
            estimated_aum = 50_000_000_000   # 50B HKD
            tier = "large_regional"
        elif fund_count >= 10:
            if wiki_both:
                estimated_aum = 10_000_000_000  # 10B HKD
            else:
                estimated_aum = 5_000_000_000   # 5B HKD
            tier = "medium_large"
        elif fund_count >= 5:
            estimated_aum = 2_000_000_000   # 2B HKD
            tier = "medium"
        elif fund_count >= 1:
            estimated_aum = 500_000_000     # 500M HKD
            tier = "small"

        # ================================================================
        # Tier 2: Wiki-based (brand signal)
        # ================================================================
        elif wiki_both:
            estimated_aum = 1_000_000_000   # 1B HKD (global brand)
            tier = "wiki_both"
        elif has_wiki and has_website and age_years and age_years >= 20:
            estimated_aum = 500_000_000     # 500M HKD (established brand)
            tier = "wiki_plus_web_old"
        elif has_wiki and has_website:
            estimated_aum = 200_000_000     # 200M HKD
            tier = "wiki_plus_web"

        # ================================================================
        # Tier 3: Website + age based (operation presence signal)
        # ================================================================
        elif has_website and age_years and age_years >= 15:
            estimated_aum = 200_000_000     # 200M HKD
            tier = "web_old"
        elif has_website and age_years and age_years >= 10:
            estimated_aum = 100_000_000     # 100M HKD
            tier = "web_mid"
        elif has_website:
            estimated_aum = 50_000_000      # 50M HKD
            tier = "web_only"

        # ================================================================
        # Tier 4: Age only (minimal signal — at least they exist)
        # ================================================================
        elif age_years and age_years >= 10:
            estimated_aum = 50_000_000      # 50M HKD (decade-old firm)
            tier = "age_old"
        elif age_years and age_years >= 5:
            estimated_aum = 20_000_000      # 20M HKD
            tier = "age_mid"

        else:
            # Truly unknown — no fund links, no wiki, no website, <5y history
            # Don't estimate (insufficient signals)
            continue

        # Build estimation rationale
        rationale_parts = []
        if fund_count > 0:
            rationale_parts.append(f"fund_count={fund_count}")
        if has_wiki_en or has_wiki_zh:
            wiki_str = "EN+ZH" if wiki_both else "EN" if has_wiki_en else "ZH"
            rationale_parts.append(f"wiki={wiki_str}")
        if has_website:
            rationale_parts.append(f"website")
        if age_years is not None:
            rationale_parts.append(f"age={age_years:.0f}y")
        if lic_years is not None:
            rationale_parts.append(f"license={lic_years:.0f}y")

        estimates.append({
            "manager_id": mgr_id,
            "aum": estimated_aum,
            "aum_currency": "HKD",
            "aum_date": None,
            "aum_source": f"proxy_estimation_{tier}",
            "aum_raw_text": (
                f"Estimated AUM tier: {tier} (~{estimated_aum:,.0f} HKD). "
                f"Based on: {', '.join(rationale_parts)}"
            ),
            "fund_count": fund_count,
        })

    return estimates


def ensure_aum_table(conn):
    """Ensure hk_fund_manager_aum table exists."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hk_fund_manager_aum (
            id INTEGER PRIMARY KEY,
            manager_id INTEGER NOT NULL,
            aum DOUBLE,
            aum_currency VARCHAR,
            aum_date DATE,
            aum_source VARCHAR,
            aum_raw_text VARCHAR,
            created_at TIMESTAMP DEFAULT now(),
            last_updated TIMESTAMP DEFAULT now()
        )
    """)


def upsert_manager_aum(conn, records: List[Dict[str, Any]], overwrite: bool = True) -> int:
    """Insert or update manager AUM records.

    If overwrite=False, only inserts new records (doesn't update existing).
    """
    if not records:
        return 0

    stored = 0
    for rec in records:
        existing = conn.execute(
            "SELECT id, aum_source FROM hk_fund_manager_aum WHERE manager_id = ?",
            [rec["manager_id"]],
        ).fetchone()

        if existing:
            if not overwrite:
                # Don't overwrite existing (e.g., fund_aggregation with proxy)
                continue
            # Only overwrite if this is same or better quality source
            existing_source = existing[1] or ""
            new_source = rec.get("aum_source", "")
            if "proxy_estimation" in new_source and "proxy_estimation" not in existing_source:
                continue  # Don't overwrite actual data with estimates
            conn.execute("""
                UPDATE hk_fund_manager_aum
                SET aum = ?, aum_currency = ?, aum_date = ?,
                    aum_source = ?, aum_raw_text = ?, last_updated = now()
                WHERE manager_id = ?
            """, [
                rec["aum"],
                rec.get("aum_currency", "HKD"),
                rec.get("aum_date"),
                rec.get("aum_source", "fund_aggregation"),
                rec.get("aum_raw_text", ""),
                rec["manager_id"],
            ])
        else:
            conn.execute("""
                INSERT INTO hk_fund_manager_aum (manager_id, aum, aum_currency, aum_date, aum_source, aum_raw_text, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, now())
            """, [
                rec["manager_id"],
                rec["aum"],
                rec.get("aum_currency", "HKD"),
                rec.get("aum_date"),
                rec.get("aum_source", "fund_aggregation"),
                rec.get("aum_raw_text", ""),
            ])
        stored += 1

    return stored


def main():
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    dry_run = "--dry-run" in sys.argv

    conn = init_db()
    ensure_aum_table(conn)

    # Step 1: Aggregate fund AUM by brand → HK manager (high confidence)
    logger.info("Step 1: Aggregating fund AUM by brand → HK manager...")
    fund_aums = aggregate_fund_aum_by_manager(conn)
    logger.info(f"  Found {len(fund_aums)} managers with fund AUM data")

    # Step 2: Estimate AUM from proxies (lower confidence)
    logger.info("Step 2: Estimating AUM from proxies (fund count, Wikipedia)...")
    proxy_aums = estimate_aum_from_proxies(conn)
    logger.info(f"  Found {len(proxy_aums)} managers for proxy estimation")

    if dry_run:
        logger.info("DRY RUN — preview only:")
        logger.info("--- Fund-aggregated AUM (high confidence) ---")
        for entry in sorted(fund_aums, key=lambda x: x["aum"], reverse=True):
            mgr_name = conn.execute(
                "SELECT company_name_en FROM hk_fund_managers WHERE id = ?",
                [entry["manager_id"]],
            ).fetchone()
            name = mgr_name[0] if mgr_name else "Unknown"
            logger.info(
                f"  {name:50s} | AUM={entry['aum']:,.0f} HKD | "
                f"funds={entry.get('fund_count', '?')} | source={entry['aum_source']}"
            )
        logger.info("--- Proxy-estimated AUM (lower confidence) ---")
        for entry in sorted(proxy_aums, key=lambda x: x["aum"], reverse=True)[:20]:
            mgr_name = conn.execute(
                "SELECT company_name_en FROM hk_fund_managers WHERE id = ?",
                [entry["manager_id"]],
            ).fetchone()
            name = mgr_name[0] if mgr_name else "Unknown"
            logger.info(
                f"  {name:50s} | AUM={entry['aum']:,.0f} HKD | "
                f"funds={entry.get('fund_count', '?')} | source={entry['aum_source']}"
            )
        conn.close()
        return

    # Store fund-aggregated AUM (high confidence, overwrite existing)
    stored_fund = upsert_manager_aum(conn, fund_aums, overwrite=True)
    # Store proxy-estimated AUM (lower confidence, don't overwrite fund-aggregated)
    stored_proxy = upsert_manager_aum(conn, proxy_aums, overwrite=False)
    conn.commit()

    # Summary
    count = conn.execute("SELECT COUNT(*) FROM hk_fund_manager_aum").fetchone()[0]
    fund_sources = conn.execute(
        "SELECT COUNT(*) FROM hk_fund_manager_aum WHERE aum_source = 'fund_aggregation'"
    ).fetchone()[0]
    proxy_sources = conn.execute(
        "SELECT COUNT(*) FROM hk_fund_manager_aum WHERE aum_source LIKE 'proxy_estimation%'"
    ).fetchone()[0]
    logger.info(
        f"Done: stored {stored_fund} fund-aggregated + {stored_proxy} proxy-estimated, "
        f"total {count} in DB ({fund_sources} fund, {proxy_sources} proxy)"
    )

    conn.close()


if __name__ == "__main__":
    main()
