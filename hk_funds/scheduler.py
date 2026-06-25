"""
Scheduler — auto-fetch HK fund KYP data pipelines.

Usage:
    python -m hk_funds.scheduler

Registers cron jobs (all HKT):
  Mon 11:07  SFC fund list refresh + classification
  Mon 11:17  HKEX listed securities refresh (ETF, L&I, REIT)
  Mon 11:27  SFC structured investment products (SIP) refresh
  Mon 11:37  SFC licensed corporations refresh + link + enforcement
  Mon 12:07  Re-classify + re-link (after all fetches complete)
  Daily 09:37 Manager enforcement cross-check
"""

from __future__ import annotations

import logging
import time
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler

from hk_funds.storage import init_db
from hk_funds.pipeline_funds import fetch_funds_daily, classify_all_funds
from hk_funds.hkex_pipeline import fetch_hkex_daily
from hk_funds.sip_pipeline import fetch_sip_daily
from hk_funds.pipeline_managers import (
    fetch_managers_daily,
    link_funds_to_managers,
    cross_check_enforcement,
)
from hk_funds.manager_profile_extractor import process_all_connected

logger = logging.getLogger("hk_funds.scheduler")


def start_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler()

    # Job 1: SFC fund list — Monday 11:07 HKT
    scheduler.add_job(
        _fetch_funds_and_classify,
        "cron",
        day_of_week="mon", hour=11, minute=7,
        id="hk_funds_weekly",
        name="SFC fund list + classify (Mon 11:07 HKT)",
        misfire_grace_time=3600,
    )

    # Job 2: HKEX listed securities — Monday 11:17 HKT
    scheduler.add_job(
        _fetch_hkex,
        "cron",
        day_of_week="mon", hour=11, minute=17,
        id="hkex_weekly",
        name="HKEX listed securities (Mon 11:17 HKT)",
        misfire_grace_time=3600,
    )

    # Job 3: SFC structured investment products — Monday 11:27 HKT
    scheduler.add_job(
        _fetch_sip,
        "cron",
        day_of_week="mon", hour=11, minute=27,
        id="sip_weekly",
        name="SFC structured products (Mon 11:27 HKT)",
        misfire_grace_time=3600,
    )

    # Job 4: SFC licensed corporations — Monday 11:37 HKT
    scheduler.add_job(
        _fetch_managers_and_link,
        "cron",
        day_of_week="mon", hour=11, minute=37,
        id="hk_managers_weekly",
        name="SFC managers + link + enforcement (Mon 11:37 HKT)",
        misfire_grace_time=3600,
    )

    # Job 5: Re-classify + re-link after all data refreshes — Monday 12:07 HKT
    scheduler.add_job(
        _classify_and_link,
        "cron",
        day_of_week="mon", hour=12, minute=7,
        id="hk_classify_link_weekly",
        name="Re-classify + Re-link (Mon 12:07 HKT)",
        misfire_grace_time=3600,
    )

    # Job 6: Manager enforcement cross-check — Daily 09:37 HKT (weekdays)
    scheduler.add_job(
        _enforcement_check,
        "cron",
        day_of_week="mon-fri", hour=9, minute=37,
        id="hk_enforcement_daily",
        name="Manager enforcement cross-check (Daily 09:37 HKT)",
        misfire_grace_time=3600,
    )

    # Job 7: Manager profile extraction + DD scoring — 1st Monday monthly 13:07 HKT
    scheduler.add_job(
        _extract_profiles_and_score,
        "cron",
        day="1-7", day_of_week="mon", hour=13, minute=7,
        id="hk_profile_monthly",
        name="Manager profile extract + DD score (1st Mon 13:07 HKT)",
        misfire_grace_time=3600,
    )

    scheduler.start()
    logger.info(
        "HK Fund KYP Scheduler started — "
        "Mon 11:07 SFC funds, Mon 11:17 HKEX, Mon 11:27 SIP, "
        "Mon 11:37 managers, Mon 12:07 classify+link, Daily 09:37 enforcement, "
        "1st Mon 13:07 profile extract+DD (HKT)"
    )
    return scheduler


def _fetch_funds_and_classify() -> None:
    logger.info("Running SFC fund list fetch...")
    t0 = time.time()
    result = fetch_funds_daily()
    logger.info(f"Funds done in {time.time() - t0:.1f}s — "
                f"found={result.get('funds_found', 0)}, stored={result.get('funds_stored', 0)}")


def _fetch_hkex() -> None:
    logger.info("Running HKEX securities list fetch...")
    t0 = time.time()
    result = fetch_hkex_daily()
    logger.info(f"HKEX done in {time.time() - t0:.1f}s — "
                f"found={result.get('funds_found', 0)}, stored={result.get('funds_stored', 0)}")


def _fetch_sip() -> None:
    logger.info("Running SFC structured investment products fetch...")
    t0 = time.time()
    result = fetch_sip_daily()
    logger.info(f"SIP done in {time.time() - t0:.1f}s — "
                f"found={result.get('funds_found', 0)}, stored={result.get('funds_stored', 0)}")


def _fetch_managers_and_link() -> None:
    logger.info("Running SFC licensed corporations fetch...")
    t0 = time.time()
    result = fetch_managers_daily()
    logger.info(f"Managers done in {time.time() - t0:.1f}s — "
                f"found={result.get('managers_found', 0)}, stored={result.get('managers_stored', 0)}, "
                f"links={result.get('links', {})}, enforcement={result.get('enforcement', {})}")


def _classify_and_link() -> None:
    conn = init_db()
    try:
        logger.info("Running fund re-classification...")
        t0 = time.time()
        classify_result = classify_all_funds(conn)
        logger.info(f"Classification done in {time.time() - t0:.1f}s — {classify_result}")

        logger.info("Running fund-manager re-linking...")
        t0 = time.time()
        link_result = link_funds_to_managers(conn)
        logger.info(f"Linking done in {time.time() - t0:.1f}s — {link_result}")
    except Exception as e:
        logger.error(f"Classify+link failed: {e}")
    finally:
        conn.close()


def _enforcement_check() -> None:
    conn = init_db()
    try:
        logger.info("Running manager enforcement cross-check...")
        t0 = time.time()
        result = cross_check_enforcement(conn)
        logger.info(f"Enforcement check done in {time.time() - t0:.1f}s — {result}")
    except Exception as e:
        logger.error(f"Enforcement cross-check failed: {e}")
    finally:
        conn.close()


def _extract_profiles_and_score() -> None:
    """Run manager profile extraction via LLM, then re-score all DD dimensions."""
    conn = init_db()
    try:
        # Step 1: Extract profiles from manager websites
        logger.info("Running manager profile extraction...")
        t0 = time.time()
        results = process_all_connected(conn)
        success = sum(1 for r in results if r["profile"] is not None)
        logger.info(
            f"Profile extraction done in {time.time() - t0:.1f}s — "
            f"{success}/{len(results)} extracted"
        )

        # Step 2: Re-run DD scoring with fresh profile data
        logger.info("Running manager DD re-scoring...")
        t0 = time.time()
        from hk_funds.pipeline_manager_dd import (
            score_financial_resources,
            score_internal_controls,
            score_risk_governance,
            score_human_resources,
            score_segregation_duties,
            score_compliance_function,
            score_audit_function,
            score_custodian_dd,
            score_valuer_dd,
            score_delegates_monitoring,
            upsert_dd_score,
        )
        from datetime import date as dt_date
        import json as _json

        ensure_mgr_dd(conn)

        # Load managers with fresh profiles
        rows = conn.execute("""
            SELECT m.id, m.inc_date, m.license_effective_date, m.name_history_count,
                   m.website, m.wiki_en_title, m.wiki_zh_title,
                   m.regulated_activity_1, m.regulated_activity_4, m.regulated_activity_9,
                   COALESCE((SELECT COUNT(*) FROM hk_funds f
                             WHERE f.fund_manager_id = m.id AND f.is_active = true), 0) as fund_count,
                   p.total_staff, p.investment_professionals, p.aum_usd,
                   p.institutional_clients, p.awards
            FROM hk_fund_managers m
            LEFT JOIN hk_manager_profiles p ON m.id = p.manager_id
            WHERE m.license_status = 'active'
              AND m.regulated_activity_9 = true
              AND m.webb_id IS NOT NULL
        """).fetchall()

        scored = 0
        for row in rows:
            mgr_id = row[0]
            inc_date = row[1]
            lic_date = row[2]
            name_hist = row[3]
            website = row[4]
            wiki_en = row[5]
            wiki_zh = row[6]
            ra1 = bool(row[7])
            ra4 = bool(row[8])
            ra9 = bool(row[9])
            fund_count = int(row[10]) if row[10] else 0
            total_staff = row[11]
            inv_prof = row[12]
            aum = row[13]
            inst_clients = row[14]
            awards_raw = row[15]

            awards_count = 0
            if isinstance(awards_raw, str):
                try:
                    awards_count = len(_json.loads(awards_raw))
                except (_json.JSONDecodeError, TypeError):
                    pass
            elif isinstance(awards_raw, list):
                awards_count = len(awards_raw)

            # Compute derived metrics
            establishment_years = None
            if inc_date:
                if isinstance(inc_date, str):
                    inc_date = dt_date.fromisoformat(inc_date)
                establishment_years = (dt_date.today() - inc_date).days / 365.25

            license_years = None
            if lic_date:
                if isinstance(lic_date, str):
                    lic_date = dt_date.fromisoformat(lic_date)
                license_years = (dt_date.today() - lic_date).days / 365.25

            has_wiki_en = bool(wiki_en)
            has_wiki_zh = bool(wiki_zh)
            has_website = bool(website)
            has_profile = bool(total_staff or aum or inv_prof)

            # Score with profile enrichment
            fin_score, fin_rationale = score_financial_resources(
                has_wiki_en, has_wiki_zh, establishment_years, has_website,
                aum_usd=aum, institutional_clients=inst_clients,
                awards_count=awards_count,
            )
            hr_score, hr_rationale = score_human_resources(
                has_wiki_en, has_wiki_zh, fund_count, establishment_years, has_website,
                total_staff=total_staff, investment_professionals=inv_prof,
            )
            int_score, int_rationale = score_internal_controls(
                name_hist, establishment_years, has_website,
            )
            risk_score, risk_rationale = score_risk_governance(
                license_years, establishment_years, ra1, ra4, ra9,
            )
            seg_score, seg_rationale = score_segregation_duties(
                ra1, ra4, ra9, fund_count, establishment_years, has_website,
            )
            comp_score, comp_rationale = score_compliance_function(
                license_years, establishment_years, ra1, ra4, ra9,
                has_website, has_wiki_en or has_wiki_zh,
            )
            audit_score, audit_rationale = score_audit_function(
                has_wiki_en or has_wiki_zh, establishment_years, fund_count, has_website,
            )
            cust_score, cust_rationale = score_custodian_dd(
                fund_count, establishment_years, has_website, ra1, ra4, ra9,
            )
            val_score, val_rationale = score_valuer_dd(
                fund_count, license_years, has_website, ra1, ra4, ra9,
            )
            del_score, del_rationale = score_delegates_monitoring(
                fund_count, establishment_years, has_website, has_wiki_en or has_wiki_zh,
                ra1, ra4, ra9,
            )

            # Data source labels
            fin_src = "manager_website+webb_site_wikipedia" if aum else "webb_site_wikipedia"
            hr_src = "manager_website+webb_site_wikipedia" if (total_staff or inv_prof) else "webb_site_wikipedia"

            upsert_dd_score(conn, mgr_id, "financial_resources", fin_score, fin_rationale, data_source=fin_src)
            upsert_dd_score(conn, mgr_id, "internal_controls", int_score, int_rationale)
            upsert_dd_score(conn, mgr_id, "risk_governance", risk_score, risk_rationale)
            upsert_dd_score(conn, mgr_id, "human_resources", hr_score, hr_rationale, data_source=hr_src)
            upsert_dd_score(conn, mgr_id, "segregation_duties", seg_score, seg_rationale)
            upsert_dd_score(conn, mgr_id, "compliance_function", comp_score, comp_rationale)
            upsert_dd_score(conn, mgr_id, "audit_function", audit_score, audit_rationale)
            upsert_dd_score(conn, mgr_id, "custodian_dd", cust_score, cust_rationale)
            upsert_dd_score(conn, mgr_id, "valuer_dd", val_score, val_rationale)
            upsert_dd_score(conn, mgr_id, "delegates_monitoring", del_score, del_rationale)

            scored += 1

        conn.commit()
        logger.info(
            f"DD re-scoring done in {time.time() - t0:.1f}s — "
            f"scored={scored} managers"
        )

    except Exception as e:
        logger.error(f"Profile extraction + DD scoring failed: {e}")
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    scheduler = start_scheduler()
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
