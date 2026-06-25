"""
Auto-derived Manager DD pipeline — populate hk_manager_dd from Webb-site + Wikipedia data.

For each active Type 9 manager, derives scores (1-5) for 4 DD dimensions:
  - financial_resources → shareholder_strength factor (5%)
  - internal_controls → internal_governance factor (2%)
  - risk_governance → risk_control_system factor (8%)
  - human_resources → investment_research factor (5%)

Total automated weight unlocked: 17% (in addition to existing 18%)

Scoring methodology (1=weakest, 5=strongest):

  financial_resources:
    Based on: Wikipedia presence, company age, has_website
    Rationale: Well-known firms with long history = stronger financial backing

  internal_controls:
    Based on: name_history_count, company age, has_website
    Rationale: Fewer name changes + older = more stable governance

  risk_governance:
    Based on: license_years, multi-license (RA1+RA4+RA9), company age
    Rationale: Longer license tenure + more regulated activities = better risk systems

  human_resources:
    Based on: Wikipedia presence, fund_count, company age
    Rationale: Larger firms with more funds = bigger research teams

Usage:
    python3 -m hk_funds.pipeline_manager_dd            # process all managers
    python3 -m hk_funds.pipeline_manager_dd --limit 10 # test with 10
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any, Dict, List, Optional

from hk_funds.storage import init_db

logger = logging.getLogger("hk_funds.pipeline_manager_dd")


def score_financial_resources(
    has_wiki_en: bool,
    has_wiki_zh: bool,
    establishment_years: Optional[float],
    has_website: bool,
    aum_usd: Optional[float] = None,
    institutional_clients: Optional[bool] = None,
    awards_count: int = 0,
) -> tuple:
    """Derive financial_resources score (1-5).

    Strong financial backing ← AUM, institutional clients, brand, history.
    """
    score = 3  # default middle

    # AUM: strongest direct signal of financial resources
    if aum_usd is not None:
        if aum_usd >= 100_000_000_000:    # $100B+
            score += 2
        elif aum_usd >= 10_000_000_000:   # $10B+
            score += 1
        elif aum_usd >= 1_000_000_000:    # $1B+
            score += 0
        elif aum_usd < 100_000_000:       # < $100M
            score -= 1

    # Institutional clients signal stronger financial backing
    if institutional_clients:
        score += 0

    # Wikipedia presence signals recognizable brand
    if has_wiki_en and has_wiki_zh:
        score += 1
    elif has_wiki_en or has_wiki_zh:
        score += 0

    # Company age
    if establishment_years is not None:
        if establishment_years >= 20:
            score += 1
        elif establishment_years >= 10:
            score += 0
        elif establishment_years < 3:
            score -= 1

    if not has_website:
        score -= 1

    # Awards: bonus for industry recognition
    if awards_count >= 5:
        score += 0

    score = max(1, min(5, score))

    rationale_parts = []
    if aum_usd is not None:
        if aum_usd >= 1e9:
            rationale_parts.append(f"AUM ${aum_usd/1e9:.1f}B")
        else:
            rationale_parts.append(f"AUM ${aum_usd/1e6:.0f}M")
    if institutional_clients:
        rationale_parts.append("institutional clients")
    if has_wiki_en and has_wiki_zh:
        rationale_parts.append("EN+ZH Wikipedia presence")
    elif has_wiki_en:
        rationale_parts.append("EN Wikipedia presence")
    elif has_wiki_zh:
        rationale_parts.append("ZH Wikipedia presence")
    if establishment_years is not None:
        rationale_parts.append(f"{establishment_years:.1f}yr history")
    if has_website:
        rationale_parts.append("has website")
    else:
        rationale_parts.append("no website")
    if awards_count > 0:
        rationale_parts.append(f"{awards_count} awards")

    return score, "; ".join(rationale_parts)


def score_internal_controls(
    name_history_count: Optional[int],
    establishment_years: Optional[float],
    has_website: bool,
) -> tuple:
    """Derive internal_controls score (1-5).

    Stable governance ← fewer name changes, mature company, transparent.
    """
    score = 3

    # Name changes: fewer = more stable governance
    if name_history_count is not None:
        if name_history_count == 0:
            score += 1
        elif name_history_count >= 3:
            score -= 1

    # Company age: older = more mature internal controls
    if establishment_years is not None:
        if establishment_years >= 15:
            score += 1
        elif establishment_years >= 5:
            score += 0
        elif establishment_years < 3:
            score -= 1

    if not has_website:
        score -= 1

    score = max(1, min(5, score))

    rationale_parts = []
    if name_history_count is not None:
        rationale_parts.append(f"{name_history_count} name changes")
    if establishment_years is not None:
        rationale_parts.append(f"{establishment_years:.1f}yr history")
    if has_website:
        rationale_parts.append("has website")

    return score, "; ".join(rationale_parts)


def score_risk_governance(
    license_years: Optional[float],
    establishment_years: Optional[float],
    ra1: bool,
    ra4: bool,
    ra9: bool,
) -> tuple:
    """Derive risk_governance score (1-5).

    Robust risk systems ← long license tenure, multiple regulated activities.
    """
    score = 3

    # License tenure: longer = more compliance experience
    if license_years is not None:
        if license_years >= 15:
            score += 1
        elif license_years >= 8:
            score += 0
        elif license_years < 3:
            score -= 1

    # Multiple RA licenses = more regulatory oversight
    ra_count = sum([ra1, ra4, ra9])
    if ra_count >= 2:
        score += 0  # already fairly standard
    if ra_count == 3:
        score += 1  # all three = heavily regulated

    # Company older than license = existed before SFC licensing = legacy stability
    if establishment_years is not None and license_years is not None:
        if establishment_years > license_years + 5:
            score += 0  # existed long before licensing
        elif establishment_years < license_years:
            score -= 1  # licensed before incorporation? unusual

    score = max(1, min(5, score))

    rationale_parts = []
    if license_years is not None:
        rationale_parts.append(f"{license_years:.1f}yr license")
    ra_labels = []
    if ra1: ra_labels.append("RA1")
    if ra4: ra_labels.append("RA4")
    if ra9: ra_labels.append("RA9")
    rationale_parts.append(f"licenses: {','.join(ra_labels)}")
    if establishment_years is not None:
        rationale_parts.append(f"{establishment_years:.1f}yr history")

    return score, "; ".join(rationale_parts)


def score_human_resources(
    has_wiki_en: bool,
    has_wiki_zh: bool,
    fund_count: int,
    establishment_years: Optional[float],
    has_website: bool = False,
    total_staff: Optional[int] = None,
    investment_professionals: Optional[int] = None,
) -> tuple:
    """Derive human_resources score (1-5).

    Strong research teams ← staff count, PM data, firm scale.
    Real profile data overrides proxy signals when available.
    """
    score = 2.0  # Lower base — most are small boutiques

    # Real staff data: much stronger signal than fund count proxy
    if total_staff is not None and total_staff > 0:
        if total_staff >= 5000:
            score += 2.0
        elif total_staff >= 1000:
            score += 1.5
        elif total_staff >= 200:
            score += 1.0
        elif total_staff >= 50:
            score += 0.5
        elif total_staff < 10:
            score -= 0.5
    elif investment_professionals is not None and investment_professionals > 0:
        if investment_professionals >= 200:
            score += 2.0
        elif investment_professionals >= 50:
            score += 1.5
        elif investment_professionals >= 20:
            score += 1.0
        elif investment_professionals >= 5:
            score += 0.5
    else:
        # Proxy: fund count as rough scale indicator
        if fund_count >= 50:
            score += 1.5
        elif fund_count >= 20:
            score += 1.0
        elif fund_count >= 5:
            score += 0.5
        elif fund_count >= 1:
            score += 0.25

    # Wikipedia: signals larger organization
    if has_wiki_en and has_wiki_zh:
        score += 1.0
    elif has_wiki_en or has_wiki_zh:
        score += 0.5

    # Company age
    if establishment_years is not None:
        if establishment_years >= 20:
            score += 0.5
        elif establishment_years >= 10:
            score += 0.25
        elif establishment_years < 3:
            score -= 0.5

    if has_website:
        score += 0.5
    else:
        score -= 0.25

    score = max(1, min(5, round(score)))

    rationale_parts = []
    if total_staff is not None:
        rationale_parts.append(f"{total_staff} total staff")
    if investment_professionals is not None:
        rationale_parts.append(f"{investment_professionals} inv professionals")
    if has_wiki_en and has_wiki_zh:
        rationale_parts.append("EN+ZH Wikipedia")
    elif has_wiki_en:
        rationale_parts.append("EN Wikipedia")
    elif has_wiki_zh:
        rationale_parts.append("ZH Wikipedia")
    rationale_parts.append(f"{fund_count} funds")
    if establishment_years is not None:
        rationale_parts.append(f"{establishment_years:.1f}yr history")
    if has_website:
        rationale_parts.append("has website")

    return score, "; ".join(rationale_parts)


def score_segregation_duties(
    ra1: bool,
    ra4: bool,
    ra9: bool,
    fund_count: int,
    establishment_years: Optional[float],
    has_website: bool,
) -> tuple:
    """Derive segregation_duties score (1-5).

    Segregation of front-office (trading/dealing) from back-office (settlement,
    accounting). Multi-license = more regulatory requirements for segregation.
    """
    # Base score by license tier — strongest signal for segregation needs
    if ra1 and ra4 and ra9:
        score = 4.0  # all three = full segregation required
    elif ra1 and ra9:
        score = 3.5  # dealing + AM = needs segregation
    elif ra4 and ra9:
        score = 3.0  # advising + AM = some segregation
    else:
        score = 2.5  # RA9 only = minimal segregation requirements

    # Company age: mature firms have established segregation
    if establishment_years is not None:
        if establishment_years >= 15:
            score += 0.5
        elif establishment_years >= 8:
            score += 0.25
        elif establishment_years < 3:
            score -= 0.5

    # Fund count: more funds = more operational complexity
    if fund_count >= 20:
        score += 0.5
    elif fund_count >= 5:
        score += 0.25

    if has_website:
        score += 0.25

    score = max(1, min(5, round(score)))

    rationale_parts = []
    ra_labels = []
    if ra1: ra_labels.append("RA1")
    if ra4: ra_labels.append("RA4")
    if ra9: ra_labels.append("RA9")
    rationale_parts.append(f"licenses: {','.join(ra_labels)}")
    if fund_count > 0:
        rationale_parts.append(f"{fund_count} funds")
    if establishment_years is not None:
        rationale_parts.append(f"{establishment_years:.1f}yr history")
    if has_website:
        rationale_parts.append("has website")

    return score, "; ".join(rationale_parts)


def score_compliance_function(
    license_years: Optional[float],
    establishment_years: Optional[float],
    ra1: bool,
    ra4: bool,
    ra9: bool,
    has_website: bool,
    has_wiki: bool,
) -> tuple:
    """Derive compliance_function score (1-5).

    Mature compliance ← license tenure (primary), multi-license, public transparency.
    """
    # Base score by license tenure — primary signal
    if license_years is not None:
        if license_years >= 20:
            score = 4.5
        elif license_years >= 15:
            score = 4.0
        elif license_years >= 10:
            score = 3.5
        elif license_years >= 5:
            score = 3.0
        elif license_years >= 3:
            score = 2.5
        else:
            score = 2.0
    else:
        score = 2.5

    # Multi-license = more regulatory touchpoints
    ra_count = sum([ra1, ra4, ra9])
    if ra_count == 3:
        score += 0.5
    elif ra_count >= 2:
        score += 0.25

    # Wikipedia = institutional-grade compliance
    if has_wiki:
        score += 0.5

    # Company older than license = pre-SFC legacy
    if establishment_years is not None and license_years is not None:
        if establishment_years > license_years + 5:
            score += 0.25

    score = max(1, min(5, round(score)))

    rationale_parts = []
    if license_years is not None:
        rationale_parts.append(f"{license_years:.1f}yr license")
    ra_labels = []
    if ra1: ra_labels.append("RA1")
    if ra4: ra_labels.append("RA4")
    if ra9: ra_labels.append("RA9")
    rationale_parts.append(f"licenses: {','.join(ra_labels)}")
    if has_website:
        rationale_parts.append("has website")
    if has_wiki:
        rationale_parts.append("has Wikipedia")

    return score, "; ".join(rationale_parts)


def score_audit_function(
    has_wiki: bool,
    establishment_years: Optional[float],
    fund_count: int,
    has_website: bool,
) -> tuple:
    """Derive audit_function score (1-5).

    Strong audit ← public scrutiny (Wikipedia primary), firm maturity, scale.
    """
    # Base: wiki = institutional grade, signals external audit requirements
    if has_wiki:
        score = 3.5
    else:
        score = 2.5

    # Company age: older = more audit history and regulatory scrutiny
    if establishment_years is not None:
        if establishment_years >= 20:
            score += 1.0
        elif establishment_years >= 10:
            score += 0.5
        elif establishment_years >= 5:
            score += 0.25
        elif establishment_years < 3:
            score -= 0.5

    # Fund count: more funds = more audit infrastructure
    if fund_count >= 20:
        score += 0.5
    elif fund_count >= 5:
        score += 0.25

    if has_website:
        score += 0.25

    score = max(1, min(5, round(score)))

    rationale_parts = []
    if has_wiki:
        rationale_parts.append("Wikipedia presence")
    if establishment_years is not None:
        rationale_parts.append(f"{establishment_years:.1f}yr history")
    if fund_count > 0:
        rationale_parts.append(f"{fund_count} funds")
    if has_website:
        rationale_parts.append("has website")

    return score, "; ".join(rationale_parts)


def score_custodian_dd(
    fund_count: int,
    establishment_years: Optional[float],
    has_website: bool,
    ra1: bool = False,
    ra4: bool = False,
    ra9: bool = False,
) -> tuple:
    """Derive custodian_dd score (1-5).

    Custodian oversight. RA1 firms that deal in securities handle client assets
    directly and need stronger custodian arrangements.
    """
    ra_count = sum([ra1, ra4, ra9])

    # Base: multi-license firms face more custodian requirements
    if ra_count >= 3:
        score = 3.5
    elif ra_count >= 2:
        score = 3.0
    else:
        score = 2.5

    # RA1 specifically = dealing with client assets = custodian critical
    if ra1:
        score += 0.5

    # Company maturity
    if establishment_years is not None:
        if establishment_years >= 15:
            score += 0.5
        elif establishment_years >= 5:
            score += 0.25
        elif establishment_years < 3:
            score -= 0.5

    # Fund count boost
    if fund_count >= 20:
        score += 0.5
    elif fund_count >= 5:
        score += 0.25

    if has_website:
        score += 0.25

    score = max(1, min(5, round(score)))

    rationale_parts = []
    ra_labels = []
    if ra1: ra_labels.append("RA1")
    if ra4: ra_labels.append("RA4")
    if ra9: ra_labels.append("RA9")
    rationale_parts.append(f"licenses: {','.join(ra_labels)}")
    if fund_count > 0:
        rationale_parts.append(f"{fund_count} funds")
    if establishment_years is not None:
        rationale_parts.append(f"{establishment_years:.1f}yr history")
    if has_website:
        rationale_parts.append("has website")

    return score, "; ".join(rationale_parts)


def score_valuer_dd(
    fund_count: int,
    license_years: Optional[float],
    has_website: bool,
    ra1: bool = False,
    ra4: bool = False,
    ra9: bool = False,
) -> tuple:
    """Derive valuer_dd score (1-5).

    Fund valuation oversight. RA1 firms dealing in securities value more
    complex instruments and need stronger valuation processes.
    """
    ra_count = sum([ra1, ra4, ra9])

    # Base by license complexity
    if ra_count >= 3:
        score = 3.5
    elif ra1 and ra9:
        score = 3.0  # dealing + AM = values own positions
    elif ra_count >= 2:
        score = 2.5
    else:
        score = 2.0

    # License tenure: longer = more valuation experience
    if license_years is not None:
        if license_years >= 15:
            score += 1.0
        elif license_years >= 8:
            score += 0.5
        elif license_years >= 3:
            score += 0.25
        else:
            score -= 0.5

    # Fund count
    if fund_count >= 20:
        score += 0.5
    elif fund_count >= 5:
        score += 0.25

    if has_website:
        score += 0.25

    score = max(1, min(5, round(score)))

    rationale_parts = []
    ra_labels = []
    if ra1: ra_labels.append("RA1")
    if ra4: ra_labels.append("RA4")
    if ra9: ra_labels.append("RA9")
    rationale_parts.append(f"licenses: {','.join(ra_labels)}")
    if license_years is not None:
        rationale_parts.append(f"{license_years:.1f}yr license")
    if fund_count > 0:
        rationale_parts.append(f"{fund_count} funds")
    if has_website:
        rationale_parts.append("has website")

    return score, "; ".join(rationale_parts)


def score_delegates_monitoring(
    fund_count: int,
    establishment_years: Optional[float],
    has_website: bool,
    has_wiki: bool,
    ra1: bool = False,
    ra4: bool = False,
    ra9: bool = False,
) -> tuple:
    """Derive delegates_monitoring score (1-5).

    Delegate oversight. More regulated activities = more delegates to monitor.
    Wikipedia = institutional-grade operations = stronger delegate oversight.
    """
    ra_count = sum([ra1, ra4, ra9])

    # Base: more licenses = more outsourced functions to monitor
    if has_wiki:
        score = 3.5  # institutional-grade oversight
    elif ra_count >= 3:
        score = 3.0
    elif ra_count >= 2:
        score = 2.5
    else:
        score = 2.0

    # Company age: mature firms have established delegate monitoring
    if establishment_years is not None:
        if establishment_years >= 15:
            score += 0.5
        elif establishment_years >= 8:
            score += 0.25
        elif establishment_years < 3:
            score -= 0.5

    # Fund count
    if fund_count >= 20:
        score += 0.5
    elif fund_count >= 5:
        score += 0.25

    if has_website:
        score += 0.25

    score = max(1, min(5, round(score)))

    rationale_parts = []
    ra_labels = []
    if ra1: ra_labels.append("RA1")
    if ra4: ra_labels.append("RA4")
    if ra9: ra_labels.append("RA9")
    rationale_parts.append(f"licenses: {','.join(ra_labels)}")
    if fund_count > 0:
        rationale_parts.append(f"{fund_count} funds")
    if establishment_years is not None:
        rationale_parts.append(f"{establishment_years:.1f}yr history")
    if has_website:
        rationale_parts.append("has website")
    if has_wiki:
        rationale_parts.append("Wikipedia presence")

    return score, "; ".join(rationale_parts)


def ensure_dd_columns(conn):
    """Ensure hk_manager_dd table exists."""
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS hk_manager_dd (
                id INTEGER PRIMARY KEY,
                manager_id INTEGER NOT NULL,
                dd_dimension VARCHAR NOT NULL,
                assessment_status VARCHAR DEFAULT 'auto_derived',
                data_source VARCHAR DEFAULT 'webb_site_wikipedia',
                assessed_by VARCHAR DEFAULT 'auto',
                assessment_date DATE DEFAULT CURRENT_DATE,
                next_review_date DATE,
                score INTEGER,
                findings VARCHAR,
                gaps VARCHAR,
                created_at TIMESTAMP DEFAULT now(),
                last_updated TIMESTAMP DEFAULT now(),
                UNIQUE(manager_id, dd_dimension)
            )
        """)
    except Exception:
        pass


def upsert_dd_score(conn, manager_id: int, dimension: str, score: int, findings: str,
                    data_source: str = "webb_site_wikipedia"):
    """Insert or update a DD dimension score."""
    conn.execute("""
        INSERT INTO hk_manager_dd (manager_id, dd_dimension, score, findings, assessment_status, data_source, assessed_by, assessment_date, last_updated)
        VALUES (?, ?, ?, ?, 'auto_derived', ?, 'auto', CURRENT_DATE, now())
        ON CONFLICT (manager_id, dd_dimension) DO UPDATE SET
            score = EXCLUDED.score,
            findings = EXCLUDED.findings,
            assessment_status = EXCLUDED.assessment_status,
            data_source = EXCLUDED.data_source,
            last_updated = now()
    """, [manager_id, dimension, score, findings, data_source])


def main():
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    conn = init_db()
    ensure_dd_columns(conn)

    args = sys.argv[1:]

    limit = None
    if "--limit" in args:
        idx = args.index("--limit")
        limit = int(args[idx + 1])

    query = """
        SELECT id, ce_number, company_name_en, company_name_cn,
               inc_date, license_effective_date, name_history_count,
               website, wiki_en_title, wiki_zh_title,
               regulated_activity_1, regulated_activity_4, regulated_activity_9,
               COALESCE((SELECT COUNT(*) FROM hk_funds f WHERE f.fund_manager_id = hk_fund_managers.id AND f.is_active = true), 0) as fund_count
        FROM hk_fund_managers
        WHERE license_status = 'active'
          AND regulated_activity_9 = true
          AND webb_id IS NOT NULL
        ORDER BY id
    """
    if limit:
        query += f" LIMIT {limit}"

    rows = conn.execute(query).fetchall()
    logger.info(f"Found {len(rows)} managers to score")

    # Load all manager profiles in one query for scoring enrichment
    profile_rows = conn.execute("""
        SELECT manager_id, total_staff, investment_professionals, aum_usd,
               institutional_clients, awards, offices
        FROM hk_manager_profiles
    """).fetchall()
    profiles = {}
    for pr in profile_rows:
        mgr_id = pr[0]
        awards_raw = pr[5]
        awards_count = 0
        if isinstance(awards_raw, str):
            try:
                awards_count = len(json.loads(awards_raw))
            except (json.JSONDecodeError, TypeError):
                pass
        elif isinstance(awards_raw, list):
            awards_count = len(awards_raw)
        profiles[mgr_id] = {
            "total_staff": pr[1],
            "investment_professionals": pr[2],
            "aum_usd": pr[3],
            "institutional_clients": pr[4],
            "awards_count": awards_count,
        }
    logger.info(f"Loaded {len(profiles)} manager profiles for scoring enrichment")

    scored = 0
    skipped = 0

    for i, row in enumerate(rows):
        try:
            mgr_id = row[0]
            ce = row[1]
            inc_date = row[4]
            lic_date = row[5]
            name_hist = row[6]
            website = row[7]
            wiki_en = row[8]
            wiki_zh = row[9]
            ra1 = bool(row[10])
            ra4 = bool(row[11])
            ra9 = bool(row[12])
            fund_count = int(row[13]) if row[13] else 0

            # Compute derived metrics
            establishment_years = None
            if inc_date:
                if isinstance(inc_date, str):
                    inc_date = date.fromisoformat(inc_date)
                establishment_years = (date.today() - inc_date).days / 365.25

            license_years = None
            if lic_date:
                if isinstance(lic_date, str):
                    lic_date = date.fromisoformat(lic_date)
                license_years = (date.today() - lic_date).days / 365.25

            has_wiki_en = bool(wiki_en)
            has_wiki_zh = bool(wiki_zh)
            has_website = bool(website)

            # Profile data for enrichment (from website scraping)
            prof = profiles.get(mgr_id, {})

            # Compute scores for all 10 DD dimensions
            fin_score, fin_rationale = score_financial_resources(
                has_wiki_en, has_wiki_zh, establishment_years, has_website,
                aum_usd=prof.get("aum_usd"),
                institutional_clients=prof.get("institutional_clients"),
                awards_count=prof.get("awards_count", 0),
            )
            int_score, int_rationale = score_internal_controls(
                name_hist, establishment_years, has_website
            )
            risk_score, risk_rationale = score_risk_governance(
                license_years, establishment_years, ra1, ra4, ra9
            )
            hr_score, hr_rationale = score_human_resources(
                has_wiki_en, has_wiki_zh, fund_count, establishment_years, has_website,
                total_staff=prof.get("total_staff"),
                investment_professionals=prof.get("investment_professionals"),
            )
            seg_score, seg_rationale = score_segregation_duties(
                ra1, ra4, ra9, fund_count, establishment_years, has_website
            )
            comp_score, comp_rationale = score_compliance_function(
                license_years, establishment_years, ra1, ra4, ra9,
                has_website, has_wiki_en or has_wiki_zh
            )
            audit_score, audit_rationale = score_audit_function(
                has_wiki_en or has_wiki_zh, establishment_years, fund_count, has_website
            )
            cust_score, cust_rationale = score_custodian_dd(
                fund_count, establishment_years, has_website, ra1, ra4, ra9
            )
            val_score, val_rationale = score_valuer_dd(
                fund_count, license_years, has_website, ra1, ra4, ra9
            )
            del_score, del_rationale = score_delegates_monitoring(
                fund_count, establishment_years, has_website, has_wiki_en or has_wiki_zh,
                ra1, ra4, ra9
            )

            # Upsert all 10 dimensions
            has_profile = bool(prof)
            fin_source = "manager_website+webb_site_wikipedia" if prof.get("aum_usd") else "webb_site_wikipedia"
            hr_source = "manager_website+webb_site_wikipedia" if (prof.get("total_staff") or prof.get("investment_professionals")) else "webb_site_wikipedia"

            upsert_dd_score(conn, mgr_id, "financial_resources", fin_score, fin_rationale, data_source=fin_source)
            upsert_dd_score(conn, mgr_id, "internal_controls", int_score, int_rationale)
            upsert_dd_score(conn, mgr_id, "risk_governance", risk_score, risk_rationale)
            upsert_dd_score(conn, mgr_id, "human_resources", hr_score, hr_rationale, data_source=hr_source)
            upsert_dd_score(conn, mgr_id, "segregation_duties", seg_score, seg_rationale)
            upsert_dd_score(conn, mgr_id, "compliance_function", comp_score, comp_rationale)
            upsert_dd_score(conn, mgr_id, "audit_function", audit_score, audit_rationale)
            upsert_dd_score(conn, mgr_id, "custodian_dd", cust_score, cust_rationale)
            upsert_dd_score(conn, mgr_id, "valuer_dd", val_score, val_rationale)
            upsert_dd_score(conn, mgr_id, "delegates_monitoring", del_score, del_rationale)

            scored += 1

            if (i + 1) % 200 == 0:
                logger.info(
                    f"  Progress: {i+1}/{len(rows)} — scored={scored}"
                )

        except Exception as e:
            logger.warning(f"  Failed for manager {row[1] if len(row) > 1 else '?'}: {e}")

    conn.commit()
    conn.close()

    logger.info(f"Done: scored={scored} managers")


if __name__ == "__main__":
    main()
