"""
HK Fund KYP storage — DuckDB schema, CRUD functions for all 8 tables.

Tables:
    hk_funds                     — SFC authorized fund list
    hk_fund_classifications      — Classification detail/rationale
    hk_fund_managers             — SFC licensed corporations
    hk_fund_manager_funds        — M:N fund-manager link
    hk_manager_regulatory_history — Enforcement actions against managers
    hk_fund_documents             — Offering documents tracker
    hkex_listed_funds             — HKEX-listed fund products
    hk_fetch_log                  — Audit trail
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import duckdb
import pandas as pd

from hk_funds.config import DB_PATH

logger = logging.getLogger("hk_funds.storage")


def init_db(db_path: str = DB_PATH, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """Initialize database — creates tables, sequences, indexes if missing."""
    conn = duckdb.connect(db_path, read_only=read_only)
    if read_only:
        return conn

    # ═══════════════════════════════════════════════════════
    #  Sequences
    # ═══════════════════════════════════════════════════════
    for seq in [
        "hk_funds_seq", "hk_fund_class_seq", "hk_fund_managers_seq",
        "hk_fmf_seq", "hk_mgr_reg_seq", "hk_fund_docs_seq",
        "hkex_listed_funds_seq", "hk_fetch_log_seq",
        "hk_kyp_dim_seq", "hk_kyp_log_seq", "hk_fund_risk_seq",
        "hk_mgr_dd_seq", "hk_mgr_profile_seq", "hk_na_fund_seq",
        "hk_rating_tmpl_seq", "hk_rating_factor_seq", "hk_user_rating_seq",
        "hk_compute_jobs_seq",
    ]:
        conn.execute(f"CREATE SEQUENCE IF NOT EXISTS {seq}")

    # ═══════════════════════════════════════════════════════
    #  Table: hk_funds
    # ═══════════════════════════════════════════════════════
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hk_funds (
            id INTEGER PRIMARY KEY DEFAULT nextval('hk_funds_seq'),
            sfc_authorization_no VARCHAR UNIQUE NOT NULL,
            fund_name_en VARCHAR NOT NULL,
            fund_name_cn VARCHAR,
            fund_type VARCHAR NOT NULL,
            fund_structure VARCHAR,
            domicile VARCHAR,
            currency VARCHAR,
            isin VARCHAR,
            bloomberg_ticker VARCHAR,
            launch_date DATE,
            authorization_date DATE,
            fund_manager_name_en VARCHAR,
            fund_manager_name_cn VARCHAR,
            fund_manager_id INTEGER,
            trustee_custodian VARCHAR,
            management_fee_pct DOUBLE,
            performance_fee_pct DOUBLE,
            nav DOUBLE,
            nav_date DATE,
            subscription_mode VARCHAR,
            redemption_frequency VARCHAR,
            min_subscription_hkd DOUBLE,
            min_subscription_usd DOUBLE,
            is_derivative_product BOOLEAN DEFAULT false,
            is_complex_product BOOLEAN DEFAULT false,
            complex_product_type VARCHAR DEFAULT 'non_complex',
            classification_reason VARCHAR,
            classification_source VARCHAR,
            is_active BOOLEAN DEFAULT true,
            source_url VARCHAR,
            last_updated TIMESTAMP DEFAULT now(),
            created_at TIMESTAMP DEFAULT now()
        )
    """)

    # ═══════════════════════════════════════════════════════
    #  Table: hk_fund_classifications
    # ═══════════════════════════════════════════════════════
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hk_fund_classifications (
            id INTEGER PRIMARY KEY DEFAULT nextval('hk_fund_class_seq'),
            fund_id INTEGER NOT NULL,
            sfc_complex_list_match BOOLEAN DEFAULT false,
            derivative_exposure_pct DOUBLE,
            is_synthetic_replication BOOLEAN DEFAULT false,
            is_leveraged BOOLEAN DEFAULT false,
            leverage_ratio DOUBLE,
            is_inverse BOOLEAN DEFAULT false,
            is_structured BOOLEAN DEFAULT false,
            has_nested_derivatives BOOLEAN DEFAULT false,
            uses_derivatives_for_non_hedging BOOLEAN DEFAULT false,
            has_secondary_market BOOLEAN,
            has_transparent_info BOOLEAN,
            loss_exceeds_principal BOOLEAN DEFAULT false,
            has_complex_payoff BOOLEAN DEFAULT false,
            illiquid_or_hard_to_value BOOLEAN,
            classification_determination VARCHAR,
            last_reviewed_date DATE,
            reviewed_by VARCHAR,
            created_at TIMESTAMP DEFAULT now(),
            UNIQUE(fund_id)
        )
    """)

    # ═══════════════════════════════════════════════════════
    #  Table: hk_fund_managers
    # ═══════════════════════════════════════════════════════
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hk_fund_managers (
            id INTEGER PRIMARY KEY DEFAULT nextval('hk_fund_managers_seq'),
            ce_number VARCHAR UNIQUE NOT NULL,
            company_name_en VARCHAR NOT NULL,
            company_name_cn VARCHAR,
            license_type VARCHAR NOT NULL,
            regulated_activity_1 BOOLEAN DEFAULT false,
            regulated_activity_4 BOOLEAN DEFAULT false,
            regulated_activity_9 BOOLEAN DEFAULT false,
            license_status VARCHAR DEFAULT 'active',
            license_effective_date DATE,
            business_address VARCHAR,
            website VARCHAR,
            key_ro_name_en VARCHAR,
            key_ro_name_cn VARCHAR,
            ro_count INTEGER,
            total_licensed_staff INTEGER,
            has_sfc_enforcement_history BOOLEAN DEFAULT false,
            enforcement_count INTEGER DEFAULT 0,
            source_url VARCHAR,
            last_updated TIMESTAMP DEFAULT now(),
            created_at TIMESTAMP DEFAULT now()
        )
    """)

    # ═══════════════════════════════════════════════════════
    #  Table: hk_fund_manager_funds
    # ═══════════════════════════════════════════════════════
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hk_fund_manager_funds (
            id INTEGER PRIMARY KEY DEFAULT nextval('hk_fmf_seq'),
            fund_id INTEGER NOT NULL,
            manager_id INTEGER NOT NULL,
            role VARCHAR DEFAULT 'manager',
            is_primary BOOLEAN DEFAULT true,
            UNIQUE(fund_id, manager_id, role)
        )
    """)

    # ═══════════════════════════════════════════════════════
    #  Table: hk_manager_regulatory_history
    # ═══════════════════════════════════════════════════════
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hk_manager_regulatory_history (
            id INTEGER PRIMARY KEY DEFAULT nextval('hk_mgr_reg_seq'),
            manager_id INTEGER NOT NULL,
            source VARCHAR NOT NULL,
            source_ref_no VARCHAR,
            action_type VARCHAR NOT NULL,
            action_date DATE NOT NULL,
            penalty_amount_hkd DOUBLE,
            description_en VARCHAR,
            description_cn VARCHAR,
            source_url VARCHAR,
            created_at TIMESTAMP DEFAULT now()
        )
    """)

    # ═══════════════════════════════════════════════════════
    #  Table: hk_fund_documents
    # ═══════════════════════════════════════════════════════
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hk_fund_documents (
            id INTEGER PRIMARY KEY DEFAULT nextval('hk_fund_docs_seq'),
            fund_id INTEGER NOT NULL,
            document_type VARCHAR NOT NULL,
            language VARCHAR DEFAULT 'en',
            document_date DATE,
            sfc_authorization_date DATE,
            source_url VARCHAR,
            local_path VARCHAR,
            is_current BOOLEAN DEFAULT true,
            created_at TIMESTAMP DEFAULT now()
        )
    """)

    # ═══════════════════════════════════════════════════════
    #  Table: hkex_listed_funds
    # ═══════════════════════════════════════════════════════
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hkex_listed_funds (
            id INTEGER PRIMARY KEY DEFAULT nextval('hkex_listed_funds_seq'),
            stock_code VARCHAR NOT NULL,
            fund_name_en VARCHAR,
            fund_name_cn VARCHAR,
            etf_type VARCHAR,
            underlying_index VARCHAR,
            management_fee DOUBLE,
            aum DOUBLE,
            is_sfc_authorized BOOLEAN DEFAULT false,
            sfc_fund_id INTEGER,
            source VARCHAR DEFAULT 'hkex',
            last_updated TIMESTAMP DEFAULT now(),
            UNIQUE(stock_code)
        )
    """)

    # ═══════════════════════════════════════════════════════
    #  Table: hk_fetch_log
    # ═══════════════════════════════════════════════════════
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hk_fetch_log (
            id INTEGER PRIMARY KEY DEFAULT nextval('hk_fetch_log_seq'),
            fetch_date DATE NOT NULL,
            source VARCHAR NOT NULL,
            items_checked INTEGER DEFAULT 0,
            new_items INTEGER DEFAULT 0,
            updated_items INTEGER DEFAULT 0,
            status VARCHAR DEFAULT 'ok',
            error_message TEXT,
            started_at TIMESTAMP DEFAULT now(),
            completed_at TIMESTAMP
        )
    """)

    # ═══════════════════════════════════════════════════════
    #  Table: hk_kyp_dimensions — 10-dimension KYP assessment
    # ═══════════════════════════════════════════════════════
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hk_kyp_dimensions (
            id INTEGER PRIMARY KEY DEFAULT nextval('hk_kyp_dim_seq'),
            fund_id INTEGER NOT NULL,
            dimension VARCHAR NOT NULL,
            assessment_status VARCHAR DEFAULT 'pending',
            data_source VARCHAR,
            assessed_by VARCHAR,
            assessment_date DATE,
            next_review_date DATE,
            score INTEGER CHECK (score >= 0 AND score <= 5),
            findings TEXT,
            gaps TEXT,
            supporting_documents TEXT,
            created_at TIMESTAMP DEFAULT now(),
            last_updated TIMESTAMP DEFAULT now(),
            UNIQUE(fund_id, dimension)
        )
    """)

    # ═══════════════════════════════════════════════════════
    #  Table: hk_kyp_assessment_log — KYP audit trail
    # ═══════════════════════════════════════════════════════
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hk_kyp_assessment_log (
            id INTEGER PRIMARY KEY DEFAULT nextval('hk_kyp_log_seq'),
            fund_id INTEGER NOT NULL,
            dimension VARCHAR NOT NULL,
            action VARCHAR NOT NULL,
            previous_status VARCHAR,
            new_status VARCHAR,
            previous_score INTEGER,
            new_score INTEGER,
            changed_by VARCHAR,
            change_reason TEXT,
            created_at TIMESTAMP DEFAULT now()
        )
    """)

    # ═══════════════════════════════════════════════════════
    #  Table: hk_fund_risk_ratings — 5-tier risk rating
    # ═══════════════════════════════════════════════════════
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hk_fund_risk_ratings (
            id INTEGER PRIMARY KEY DEFAULT nextval('hk_fund_risk_seq'),
            fund_id INTEGER NOT NULL UNIQUE,
            overall_risk_score DECIMAL(3,1),
            risk_category VARCHAR,
            methodology_version VARCHAR,
            is_automated BOOLEAN DEFAULT true,
            override_reason TEXT,
            overridden_by VARCHAR,
            override_date DATE,
            score_breakdown TEXT,
            supporting_rationale TEXT,
            last_calculated TIMESTAMP DEFAULT now(),
            created_at TIMESTAMP DEFAULT now()
        )
    """)

    # ═══════════════════════════════════════════════════════
    #  Table: hk_manager_dd — 10-dimension manager DD
    # ═══════════════════════════════════════════════════════
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hk_manager_dd (
            id INTEGER PRIMARY KEY DEFAULT nextval('hk_mgr_dd_seq'),
            manager_id INTEGER NOT NULL,
            dd_dimension VARCHAR NOT NULL,
            assessment_status VARCHAR DEFAULT 'pending',
            data_source VARCHAR,
            assessed_by VARCHAR,
            assessment_date DATE,
            next_review_date DATE,
            score INTEGER CHECK (score >= 0 AND score <= 5),
            findings TEXT,
            gaps TEXT,
            created_at TIMESTAMP DEFAULT now(),
            last_updated TIMESTAMP DEFAULT now(),
            UNIQUE(manager_id, dd_dimension)
        )
    """)

    # ═══════════════════════════════════════════════════════
    #  Table: hk_fund_manager_aum — manager AUM tracking
    # ═══════════════════════════════════════════════════════
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hk_fund_manager_aum (
            id INTEGER PRIMARY KEY DEFAULT nextval('hk_mgr_dd_seq'),
            manager_id INTEGER NOT NULL,
            aum DOUBLE,
            aum_currency VARCHAR DEFAULT 'USD',
            aum_date DATE,
            aum_source VARCHAR DEFAULT 'manager_website',
            aum_raw_text VARCHAR,
            created_at TIMESTAMP DEFAULT now(),
            last_updated TIMESTAMP DEFAULT now(),
            UNIQUE(manager_id)
        )
    """)

    # ═══════════════════════════════════════════════════════
    #  Table: hk_manager_profiles — company profile from website scraping
    # ═══════════════════════════════════════════════════════
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hk_manager_profiles (
            id INTEGER PRIMARY KEY DEFAULT nextval('hk_mgr_profile_seq'),
            manager_id INTEGER NOT NULL UNIQUE,
            company_description_en TEXT,
            company_description_cn TEXT,
            founding_year INTEGER,
            total_staff INTEGER,
            investment_professionals INTEGER,
            offices JSON,
            key_personnel JSON,
            awards JSON,
            aum_usd DOUBLE,
            aum_date VARCHAR,
            investment_philosophy TEXT,
            asset_classes JSON,
            institutional_clients BOOLEAN,
            regulatory_licenses JSON,
            data_source VARCHAR,
            extraction_date DATE,
            last_updated TIMESTAMP DEFAULT now(),
            created_at TIMESTAMP DEFAULT now()
        )
    """)

    # ═══════════════════════════════════════════════════════
    #  Table: hk_rating_templates — configurable rating templates
    # ═══════════════════════════════════════════════════════
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hk_rating_templates (
            id INTEGER PRIMARY KEY DEFAULT nextval('hk_rating_tmpl_seq'),
            user_id VARCHAR NOT NULL DEFAULT 'system',
            name VARCHAR NOT NULL,
            description TEXT,
            template_type VARCHAR NOT NULL CHECK (template_type IN ('fund_risk', 'manager_dd')),
            methodology_version VARCHAR DEFAULT '1.0',
            is_system BOOLEAN DEFAULT false,
            category_thresholds_json TEXT,
            created_at TIMESTAMP DEFAULT now(),
            updated_at TIMESTAMP DEFAULT now()
        )
    """)

    # ═══════════════════════════════════════════════════════
    #  Table: hk_template_factors — factors within a template
    # ═══════════════════════════════════════════════════════
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hk_template_factors (
            id INTEGER PRIMARY KEY DEFAULT nextval('hk_rating_factor_seq'),
            template_id INTEGER NOT NULL,
            factor_key VARCHAR NOT NULL,
            factor_label VARCHAR,
            weight DECIMAL(5,4),
            ordinal INTEGER DEFAULT 0,
            config_json TEXT,
            created_at TIMESTAMP DEFAULT now(),
            UNIQUE(template_id, factor_key)
        )
    """)

    # ═══════════════════════════════════════════════════════
    #  Table: hk_user_ratings — user-specific rating results
    # ═══════════════════════════════════════════════════════
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hk_user_ratings (
            id INTEGER PRIMARY KEY DEFAULT nextval('hk_user_rating_seq'),
            template_id INTEGER NOT NULL,
            user_id VARCHAR NOT NULL,
            target_type VARCHAR NOT NULL CHECK (target_type IN ('fund', 'manager')),
            target_id INTEGER NOT NULL,
            overall_score DECIMAL(5,2),
            category VARCHAR,
            factor_scores_json TEXT,
            methodology_version VARCHAR DEFAULT '1.0',
            computed_at TIMESTAMP DEFAULT now(),
            is_latest BOOLEAN DEFAULT true
        )
    """)

    # ═══════════════════════════════════════════════════════
    #  Table: hk_compute_jobs — track rating computation progress
    # ═══════════════════════════════════════════════════════
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hk_compute_jobs (
            id INTEGER PRIMARY KEY DEFAULT nextval('hk_compute_jobs_seq'),
            template_id INTEGER NOT NULL,
            user_id VARCHAR NOT NULL,
            target_type VARCHAR NOT NULL CHECK (target_type IN ('fund', 'manager')),
            status VARCHAR DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'done', 'failed')),
            total_targets INTEGER DEFAULT 0,
            completed_targets INTEGER DEFAULT 0,
            error_count INTEGER DEFAULT 0,
            started_at TIMESTAMP DEFAULT now(),
            completed_at TIMESTAMP
        )
    """)

    # ═══════════════════════════════════════════════════════
    #  Table: hk_non_authorized_funds — non-SFC-authorized
    # ═══════════════════════════════════════════════════════
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hk_non_authorized_funds (
            id INTEGER PRIMARY KEY DEFAULT nextval('hk_na_fund_seq'),
            fund_name_en VARCHAR NOT NULL,
            fund_name_cn VARCHAR,
            isin VARCHAR,
            bloomberg_ticker VARCHAR,
            fund_type VARCHAR,
            domicile VARCHAR,
            currency VARCHAR,
            fund_manager_name_en VARCHAR,
            fund_manager_name_cn VARCHAR,
            distribution_restriction VARCHAR DEFAULT 'pi_only',
            min_investment_hkd DOUBLE,
            is_active BOOLEAN DEFAULT true,
            data_source VARCHAR DEFAULT 'manual',
            notes TEXT,
            created_at TIMESTAMP DEFAULT now(),
            last_updated TIMESTAMP DEFAULT now()
        )
    """)

    # ═══════════════════════════════════════════════════════
    #  Indexes
    # ═══════════════════════════════════════════════════════
    for idx_col in [
        ("idx_hkf_name", "hk_funds", "fund_name_en"),
        ("idx_hkf_type", "hk_funds", "fund_type"),
        ("idx_hkf_deriv", "hk_funds", "is_derivative_product"),
        ("idx_hkf_complex2", "hk_funds", "is_complex_product"),
        ("idx_hkf_cptype", "hk_funds", "complex_product_type"),
        ("idx_hkf_domicile", "hk_funds", "domicile"),
        ("idx_hkf_isin", "hk_funds", "isin"),
        ("idx_hkf_mgr_id", "hk_funds", "fund_manager_id"),
        ("idx_hkf_auth_no", "hk_funds", "sfc_authorization_no"),
        ("idx_hkfc_fund_id", "hk_fund_classifications", "fund_id"),
        ("idx_hkfm_ce", "hk_fund_managers", "ce_number"),
        ("idx_hkfm_name", "hk_fund_managers", "company_name_en"),
        ("idx_hkfm_type", "hk_fund_managers", "license_type"),
        ("idx_hkfm_status", "hk_fund_managers", "license_status"),
        ("idx_hkfm_ra9", "hk_fund_managers", "regulated_activity_9"),
        ("idx_hkfmf_fund", "hk_fund_manager_funds", "fund_id"),
        ("idx_hkfmf_mgr", "hk_fund_manager_funds", "manager_id"),
        ("idx_hkmrh_mgr", "hk_manager_regulatory_history", "manager_id"),
        ("idx_hkmrh_date", "hk_manager_regulatory_history", "action_date"),
        ("idx_hkfd_fund", "hk_fund_documents", "fund_id"),
        ("idx_hkex_code", "hkex_listed_funds", "stock_code"),
        ("idx_hkma_mgr", "hk_fund_manager_aum", "manager_id"),
        ("idx_tmpl_user", "hk_rating_templates", "user_id"),
        ("idx_tmpl_type", "hk_rating_templates", "template_type"),
        ("idx_factor_tmpl", "hk_template_factors", "template_id"),
        ("idx_urating_tmpl", "hk_user_ratings", "template_id"),
        ("idx_urating_user", "hk_user_ratings", "user_id"),
        ("idx_urating_target", "hk_user_ratings", "target_type, target_id"),
        ("idx_urating_latest", "hk_user_ratings", "is_latest"),
        ("idx_cjob_lookup", "hk_compute_jobs", "template_id, user_id, target_type"),
    ]:
        try:
            conn.execute(f"CREATE INDEX IF NOT EXISTS {idx_col[0]} ON {idx_col[1]}({idx_col[2]})")
        except Exception:
            pass

    # Indexes for new tables
    for idx_col in [
        ("idx_kyp_fund", "hk_kyp_dimensions", "fund_id"),
        ("idx_kyp_dim", "hk_kyp_dimensions", "dimension"),
        ("idx_kyp_status", "hk_kyp_dimensions", "assessment_status"),
        ("idx_kyplog_fund", "hk_kyp_assessment_log", "fund_id"),
        ("idx_risk_fund", "hk_fund_risk_ratings", "fund_id"),
        ("idx_risk_cat", "hk_fund_risk_ratings", "risk_category"),
        ("idx_mdd_mgr", "hk_manager_dd", "manager_id"),
        ("idx_mdd_dim", "hk_manager_dd", "dd_dimension"),
        ("idx_mdd_status", "hk_manager_dd", "assessment_status"),
        ("idx_mprof_mgr", "hk_manager_profiles", "manager_id"),
        ("idx_naf_isin", "hk_non_authorized_funds", "isin"),
    ]:
        try:
            conn.execute(f"CREATE INDEX IF NOT EXISTS {idx_col[0]} ON {idx_col[1]}({idx_col[2]})")
        except Exception:
            pass

    # Unique index: prevent duplicate non-authorized fund entries
    try:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_naf_unique "
            "ON hk_non_authorized_funds(fund_name_en, fund_manager_name_en, data_source)"
        )
    except Exception:
        pass

    # Run schema migrations
    migrate_schema_v2(conn)
    migrate_schema_v3(conn)
    migrate_schema_v4(conn)
    migrate_schema_v5(conn)

    # Ensure standard rating templates exist
    try:
        from hk_funds.standard_templates import ensure_standard_templates
        ensure_standard_templates(conn)
    except Exception:
        pass  # Templates will be created when module is available

    return conn


# ═══════════════════════════════════════════════════════════════
#  Schema Migration: v2 → v3 (KYP, DD, Risk Rating, Non-Authorized)
# ═══════════════════════════════════════════════════════════════

def migrate_schema_v3(conn) -> None:
    """Add v3 columns for expanded KYP/DD data to existing tables."""
    v3_columns = {
        "hk_funds": [
            ("umbrella_fund_name", "VARCHAR"),
            ("umbrella_fund_ce", "VARCHAR"),
            ("product_key_features", "TEXT"),
            ("risk_disclosure_url", "VARCHAR"),
            ("kfs_document_url", "VARCHAR"),
            ("is_etf", "BOOLEAN DEFAULT false"),
            ("underlying_assets", "TEXT"),
            ("credit_rating", "VARCHAR"),
            ("lockup_period_days", "INTEGER"),
            ("redemption_notice_days", "INTEGER"),
        ],
        "hk_fund_managers": [
            ("financial_year_end", "DATE"),
            ("total_aum_hkd", "DOUBLE"),
            ("shareholder_equity_hkd", "DOUBLE"),
            ("compliance_officer_name", "VARCHAR"),
            ("auditor_name", "VARCHAR"),
            ("custodian_name", "VARCHAR"),
            ("professional_indemnity_insurance", "BOOLEAN"),
            ("last_financial_statement_date", "DATE"),
            ("sfc_risk_profile", "VARCHAR"),
            ("dd_overall_score", "DECIMAL(3,1)"),
            ("dd_last_reviewed", "DATE"),
        ],
    }
    for table, columns in v3_columns.items():
        for col_name, col_type in columns:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col_name} {col_type}")
            except Exception:
                pass
    logger.info("Schema v3 migration complete")


# ═══════════════════════════════════════════════════════════════
#  Schema Migration: v3 → v4 (Morningstar-like enrichment)
# ═══════════════════════════════════════════════════════════════

def migrate_schema_v4(conn) -> None:
    """Add v4 columns for Morningstar-like fund enrichment + NAV/performance tables."""
    # New columns on hk_funds
    v4_fund_columns = [
        ("expense_ratio_pct", "DOUBLE"),
        ("front_load_pct", "DOUBLE"),
        ("back_load_pct", "DOUBLE"),
        ("benchmark_name", "VARCHAR"),
        ("fund_inception_date", "DATE"),
        ("aum", "DOUBLE"),
        ("aum_date", "DATE"),
        ("distribution_frequency", "VARCHAR"),
        ("dividend_yield_12m_pct", "DOUBLE"),
        ("source_type", "VARCHAR DEFAULT 'sfc'"),
        ("portfolio_manager_name", "VARCHAR"),
        ("product_url", "VARCHAR"),
        ("morningstar_rating", "INTEGER"),
        ("investment_objective", "VARCHAR"),
        ("ongoing_charges_pct", "DOUBLE"),
        ("min_subscription_initial_hkd", "DOUBLE"),
        ("min_subscription_additional_hkd", "DOUBLE"),
        ("fund_size_hkd", "DOUBLE"),
        ("fund_size_date", "DATE"),
        ("last_dividend_date", "DATE"),
        ("last_dividend_amount", "DOUBLE"),
        ("share_class_name", "VARCHAR"),
        ("share_class_currency", "VARCHAR"),
        ("is_distributing", "BOOLEAN"),
        ("is_hedged", "BOOLEAN"),
    ]
    for col_name, col_type in v4_fund_columns:
        try:
            conn.execute(f"ALTER TABLE hk_funds ADD COLUMN IF NOT EXISTS {col_name} {col_type}")
        except Exception:
            pass

    # New table: hk_fund_nav_history
    try:
        conn.execute("CREATE SEQUENCE IF NOT EXISTS hk_fund_nav_seq")
    except Exception:
        pass
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hk_fund_nav_history (
            id INTEGER PRIMARY KEY DEFAULT nextval('hk_fund_nav_seq'),
            fund_id INTEGER NOT NULL,
            nav_date DATE NOT NULL,
            nav DOUBLE,
            nav_currency VARCHAR DEFAULT 'HKD',
            source VARCHAR DEFAULT 'manager_website',
            created_at TIMESTAMP DEFAULT now(),
            UNIQUE(fund_id, nav_date)
        )
    """)

    # New table: hk_fund_performance
    try:
        conn.execute("CREATE SEQUENCE IF NOT EXISTS hk_fund_perf_seq")
    except Exception:
        pass
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hk_fund_performance (
            id INTEGER PRIMARY KEY DEFAULT nextval('hk_fund_perf_seq'),
            fund_id INTEGER NOT NULL UNIQUE,
            ytd_return_pct DOUBLE,
            return_1m_pct DOUBLE,
            return_3m_pct DOUBLE,
            return_6m_pct DOUBLE,
            return_1y_pct DOUBLE,
            return_3y_annualized_pct DOUBLE,
            return_5y_annualized_pct DOUBLE,
            std_dev_3y DOUBLE,
            sharpe_ratio_3y DOUBLE,
            max_drawdown_pct DOUBLE,
            max_drawdown_period VARCHAR,
            alpha_3y DOUBLE,
            beta_3y DOUBLE,
            r_squared_3y DOUBLE,
            data_points_used INTEGER,
            calculation_date DATE DEFAULT CURRENT_DATE,
            last_updated TIMESTAMP DEFAULT now()
        )
    """)

    # New table: hk_fund_holdings
    try:
        conn.execute("CREATE SEQUENCE IF NOT EXISTS hk_fund_holding_seq")
    except Exception:
        pass
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hk_fund_holdings (
            id INTEGER PRIMARY KEY DEFAULT nextval('hk_fund_holding_seq'),
            fund_id INTEGER NOT NULL,
            rank INTEGER NOT NULL,
            holding_name VARCHAR NOT NULL,
            weight_pct DOUBLE,
            ticker VARCHAR,
            isin VARCHAR,
            sector VARCHAR,
            country VARCHAR,
            asset_class VARCHAR,
            holding_date DATE,
            source VARCHAR DEFAULT 'manager_website',
            created_at TIMESTAMP DEFAULT now(),
            UNIQUE(fund_id, rank)
        )
    """)

    # New table: hk_fund_dividends
    try:
        conn.execute("CREATE SEQUENCE IF NOT EXISTS hk_fund_div_seq")
    except Exception:
        pass
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hk_fund_dividends (
            id INTEGER PRIMARY KEY DEFAULT nextval('hk_fund_div_seq'),
            fund_id INTEGER NOT NULL,
            ex_date DATE NOT NULL,
            pay_date DATE,
            dividend_amount DOUBLE NOT NULL,
            dividend_currency VARCHAR DEFAULT 'HKD',
            dividend_type VARCHAR DEFAULT 'income',
            declaration_date DATE,
            record_date DATE,
            source VARCHAR DEFAULT 'manager_website',
            created_at TIMESTAMP DEFAULT now(),
            UNIQUE(fund_id, ex_date, dividend_type)
        )
    """)

    # New table: hk_fund_share_classes
    try:
        conn.execute("CREATE SEQUENCE IF NOT EXISTS hk_fund_sc_seq")
    except Exception:
        pass
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hk_fund_share_classes (
            id INTEGER PRIMARY KEY DEFAULT nextval('hk_fund_sc_seq'),
            fund_id INTEGER NOT NULL,
            share_class_name VARCHAR NOT NULL,
            isin VARCHAR,
            currency VARCHAR DEFAULT 'HKD',
            is_hedged BOOLEAN DEFAULT false,
            distribution_type VARCHAR,
            min_initial_subscription DOUBLE,
            management_fee_pct DOUBLE,
            ongoing_charges_pct DOUBLE,
            source VARCHAR DEFAULT 'manager_website',
            created_at TIMESTAMP DEFAULT now(),
            UNIQUE(fund_id, share_class_name)
        )
    """)

    # Indexes for new tables
    for idx_name, table, col in [
        ("idx_nav_fund_date", "hk_fund_nav_history", "fund_id, nav_date"),
        ("idx_nav_date", "hk_fund_nav_history", "nav_date"),
        ("idx_perf_fund", "hk_fund_performance", "fund_id"),
        ("idx_hold_fund", "hk_fund_holdings", "fund_id"),
        ("idx_div_fund", "hk_fund_dividends", "fund_id, ex_date"),
        ("idx_sc_fund", "hk_fund_share_classes", "fund_id"),
    ]:
        try:
            conn.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table}({col})")
        except Exception:
            pass

    logger.info("Schema v4 migration complete")


# ═══════════════════════════════════════════════════════════════
#  Schema Migration: v4 → v5 (configurable rating templates)
# ═══════════════════════════════════════════════════════════════

def migrate_schema_v5(conn) -> None:
    """Add v5 tables for configurable rating templates and user ratings."""
    # Tables are created via CREATE TABLE IF NOT EXISTS in init_db()
    # This migration exists for version tracking and future column additions
    logger.info("Schema v5 migration complete (rating templates)")


# ═══════════════════════════════════════════════════════════════
#  Schema Migration: v1 → v2 (dual-dimension classification)
# ═══════════════════════════════════════════════════════════════

def migrate_schema_v2(conn) -> None:
    """Migrate from single classification field to dual-dimension schema.

    Old fields:
        is_complex BOOLEAN
        classification VARCHAR (ordinary/complex/derivatives/structured)
    New fields:
        is_derivative_product BOOLEAN  (§5.1A)
        is_complex_product BOOLEAN     (§5.5)
        complex_product_type VARCHAR   (enum)
    """
    # Check if migration already done
    cols = [row[0] for row in conn.execute("DESCRIBE hk_funds").fetchall()]
    has_new = "is_derivative_product" in cols
    has_is_complex = "is_complex" in cols
    has_classification = "classification" in cols

    # Migration fully complete if new cols exist and old cols are gone
    if has_new and not has_is_complex and not has_classification:
        return

    logger.info("Running schema migration v1 → v2 (dual-dimension classification)")

    # Step 1: Add new columns (if not already present from partial migration)
    if not has_new:
        conn.execute("ALTER TABLE hk_funds ADD COLUMN is_derivative_product BOOLEAN DEFAULT false")
        conn.execute("ALTER TABLE hk_funds ADD COLUMN is_complex_product BOOLEAN DEFAULT false")
        conn.execute("ALTER TABLE hk_funds ADD COLUMN complex_product_type VARCHAR DEFAULT 'non_complex'")

    # Step 1.5: Drop ALL indexes on hk_funds to allow DROP COLUMN later.
    # DuckDB requires dropped columns to be last in any index. Must run even
    # for partially-migrated state where new cols exist but old cols remain.
    if has_classification or has_is_complex:
        for idx_name, in conn.execute(
            "SELECT index_name FROM duckdb_indexes WHERE table_name='hk_funds'"
        ).fetchall():
            try:
                conn.execute(f"DROP INDEX IF EXISTS {idx_name}")
            except Exception:
                pass

    # Step 2: Migrate data from old classification field (if present)
    if has_classification:
        logger.info("  Migrating data from old classification field...")
        conn.execute("""
            UPDATE hk_funds SET
                is_derivative_product = (classification IN ('derivatives', 'structured')),
                is_complex_product = (classification IN ('complex', 'derivatives', 'structured')),
                complex_product_type = CASE
                    WHEN classification = 'structured' THEN 'structured'
                    WHEN classification = 'derivatives' THEN 'derivative_fund'
                    WHEN classification = 'complex' THEN 'complex_bond'
                    ELSE 'non_complex'
                END
        """)

        # Step 3: Drop old columns (all indexes were already dropped in Step 1.5)
        conn.execute("ALTER TABLE hk_funds DROP COLUMN is_complex")
        conn.execute("ALTER TABLE hk_funds DROP COLUMN classification")

    # Step 4: Add six-factor columns to hk_fund_classifications (if needed)
    class_cols = [row[0] for row in conn.execute("DESCRIBE hk_fund_classifications").fetchall()]
    for col_name, col_def in [
        ("has_secondary_market", "BOOLEAN DEFAULT NULL"),
        ("has_transparent_info", "BOOLEAN DEFAULT NULL"),
        ("loss_exceeds_principal", "BOOLEAN DEFAULT false"),
        ("has_complex_payoff", "BOOLEAN DEFAULT false"),
        ("illiquid_or_hard_to_value", "BOOLEAN DEFAULT NULL"),
    ]:
        if col_name not in class_cols:
            try:
                conn.execute(f"ALTER TABLE hk_fund_classifications ADD COLUMN {col_name} {col_def}")
            except Exception:
                pass

    # Step 5: Populate six-factor data from existing classification fields
    if "is_leveraged" in class_cols:
        conn.execute("""
            UPDATE hk_fund_classifications SET
                loss_exceeds_principal = (is_leveraged = true OR is_inverse = true),
                has_complex_payoff = (is_structured = true)
        """)

    # Step 7: Recreate all indexes on hk_funds
    for idx_name, col in [
        ("idx_hkf_deriv", "is_derivative_product"),
        ("idx_hkf_complex2", "is_complex_product"),
        ("idx_hkf_cptype", "complex_product_type"),
        ("idx_hkf_auth_no", "sfc_authorization_no"),
        ("idx_hkf_domicile", "domicile"),
        ("idx_hkf_isin", "isin"),
        ("idx_hkf_mgr_id", "fund_manager_id"),
        ("idx_hkf_name", "fund_name_en"),
        ("idx_hkf_type", "fund_type"),
    ]:
        try:
            conn.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON hk_funds({col})")
        except Exception:
            pass

    logger.info("  Schema migration complete.")


# ═══════════════════════════════════════════════════════════════
#  Fetch Log Helpers
# ═══════════════════════════════════════════════════════════════

def log_fetch_start(conn, date_str: str, source: str) -> int:
    """Insert a fetch_log row and return its id."""
    row = conn.execute("""
        INSERT INTO hk_fetch_log (fetch_date, source, status)
        VALUES (?, ?, 'running')
        RETURNING id
    """, [date_str, source]).fetchone()
    return row[0]


def log_fetch_end(conn, log_id: int, items_checked: int = 0, new_items: int = 0,
                  updated_items: int = 0, status: str = "ok", error: str = None):
    """Update fetch_log row on completion."""
    conn.execute("""
        UPDATE hk_fetch_log
        SET items_checked = ?, new_items = ?, updated_items = ?,
            status = ?, error_message = ?, completed_at = now()
        WHERE id = ?
    """, [items_checked, new_items, updated_items, status, error, log_id])


def get_fetch_status(conn, days: int = 7):
    """Return recent fetch log entries."""
    return conn.execute("""
        SELECT * FROM hk_fetch_log
        WHERE fetch_date >= current_date - ?
        ORDER BY fetch_date DESC, started_at DESC
    """, [days]).df()


# ═══════════════════════════════════════════════════════════════
#  hk_funds — CRUD
# ═══════════════════════════════════════════════════════════════

def upsert_funds(conn, records: List[Dict[str, Any]]) -> int:
    """Batch upsert fund records. Returns count of rows inserted."""
    if not records:
        return 0
    df = pd.DataFrame(records)
    needed = [
        "sfc_authorization_no", "fund_name_en", "fund_name_cn", "fund_type",
        "fund_structure", "domicile", "currency", "isin", "bloomberg_ticker",
        "launch_date", "authorization_date", "fund_manager_name_en",
        "fund_manager_name_cn", "fund_manager_id", "trustee_custodian",
        "management_fee_pct", "performance_fee_pct", "nav", "nav_date",
        "subscription_mode", "redemption_frequency", "min_subscription_hkd",
        "min_subscription_usd", "is_derivative_product", "is_complex_product",
        "complex_product_type", "classification_reason", "classification_source",
        "is_active", "source_url",
        "umbrella_fund_name", "umbrella_fund_ce",
        # v4 columns
        "expense_ratio_pct", "front_load_pct", "back_load_pct",
        "benchmark_name", "fund_inception_date", "aum", "aum_date",
        "distribution_frequency", "dividend_yield_12m_pct", "source_type",
    ]
    for col in needed:
        if col not in df.columns:
            df[col] = None
    sub = df[needed].copy()
    conn.register("_tmp_hkf", sub)
    try:
        rows = conn.execute("""
            INSERT INTO hk_funds (
                sfc_authorization_no, fund_name_en, fund_name_cn, fund_type,
                fund_structure, domicile, currency, isin, bloomberg_ticker,
                launch_date, authorization_date, fund_manager_name_en,
                fund_manager_name_cn, fund_manager_id, trustee_custodian,
                management_fee_pct, performance_fee_pct, nav, nav_date,
                subscription_mode, redemption_frequency, min_subscription_hkd,
                min_subscription_usd, is_derivative_product, is_complex_product,
                complex_product_type, classification_reason, classification_source,
                is_active, source_url, umbrella_fund_name, umbrella_fund_ce,
                expense_ratio_pct, front_load_pct, back_load_pct,
                benchmark_name, fund_inception_date, aum, aum_date,
                distribution_frequency, dividend_yield_12m_pct, source_type
            )
            SELECT
                sfc_authorization_no, fund_name_en, fund_name_cn, fund_type,
                fund_structure, domicile, currency, isin, bloomberg_ticker,
                launch_date, authorization_date, fund_manager_name_en,
                fund_manager_name_cn, fund_manager_id, trustee_custodian,
                management_fee_pct, performance_fee_pct, nav, nav_date,
                subscription_mode, redemption_frequency, min_subscription_hkd,
                min_subscription_usd, is_derivative_product, is_complex_product,
                complex_product_type, classification_reason, classification_source,
                is_active, source_url, umbrella_fund_name, umbrella_fund_ce,
                expense_ratio_pct, front_load_pct, back_load_pct,
                benchmark_name, fund_inception_date, aum, aum_date,
                distribution_frequency, dividend_yield_12m_pct, source_type
            FROM _tmp_hkf
            ON CONFLICT (sfc_authorization_no) DO UPDATE SET
                fund_name_en = excluded.fund_name_en,
                fund_name_cn = excluded.fund_name_cn,
                fund_type = excluded.fund_type,
                domicile = excluded.domicile,
                currency = excluded.currency,
                isin = excluded.isin,
                nav = excluded.nav,
                nav_date = excluded.nav_date,
                umbrella_fund_name = excluded.umbrella_fund_name,
                umbrella_fund_ce = excluded.umbrella_fund_ce,
                is_derivative_product = excluded.is_derivative_product,
                is_complex_product = excluded.is_complex_product,
                complex_product_type = excluded.complex_product_type,
                classification_reason = excluded.classification_reason,
                classification_source = excluded.classification_source,
                is_active = excluded.is_active,
                source_url = excluded.source_url,
                expense_ratio_pct = excluded.expense_ratio_pct,
                front_load_pct = excluded.front_load_pct,
                back_load_pct = excluded.back_load_pct,
                benchmark_name = excluded.benchmark_name,
                fund_inception_date = excluded.fund_inception_date,
                aum = excluded.aum,
                aum_date = excluded.aum_date,
                distribution_frequency = excluded.distribution_frequency,
                dividend_yield_12m_pct = excluded.dividend_yield_12m_pct,
                source_type = excluded.source_type,
                last_updated = now()
        """).fetchall()
    finally:
        conn.unregister("_tmp_hkf")
    return rows[0][0] if rows else 0


def get_funds(conn,
              is_derivative_product: bool = None,
              is_complex_product: bool = None,
              complex_product_type: str = None,
              fund_type: str = None,
              domicile: str = None,
              is_active: bool = True,
              search: str = None,
              limit: int = 100,
              # Deprecated params (backward compat, mapped internally):
              classification: str = None,
              is_complex: bool = None):
    """Query funds with optional filters. Supports both new dual-dimension params
    and deprecated single-dimension params (mapped internally)."""
    where = ["1=1"]; params = []

    # New dual-dimension filters
    if is_derivative_product is not None:
        where.append("is_derivative_product = ?"); params.append(is_derivative_product)
    if is_complex_product is not None:
        where.append("is_complex_product = ?"); params.append(is_complex_product)
    if complex_product_type:
        where.append("complex_product_type = ?"); params.append(complex_product_type)

    # Deprecated backward compat: map old classification to new fields
    if classification:
        if classification == "derivatives":
            where.append("is_derivative_product = true")
        elif classification == "structured":
            where.append("complex_product_type = 'structured'")
        elif classification == "complex":
            where.append("(is_complex_product = true AND is_derivative_product = false)")
        elif classification == "ordinary":
            where.append("is_complex_product = false")
    if is_complex is not None:
        where.append("is_complex_product = ?"); params.append(is_complex)

    # Other filters
    if fund_type:
        where.append("fund_type = ?"); params.append(fund_type)
    if domicile:
        where.append("domicile = ?"); params.append(domicile)
    if is_active is not None:
        where.append("is_active = ?"); params.append(is_active)
    if search:
        where.append("(fund_name_en ILIKE ? OR fund_name_cn ILIKE ? OR isin = ? OR sfc_authorization_no = ?)")
        like = f"%{search}%"
        params.extend([like, like, search, search])
    params.append(limit)
    return conn.execute(f"""
        SELECT * FROM hk_funds WHERE {' AND '.join(where)}
        ORDER BY fund_name_en LIMIT ?
    """, params).df()


def get_fund_by_id(conn, fund_id: int) -> Optional[Dict]:
    row = conn.execute("SELECT * FROM hk_funds WHERE id = ?", [fund_id]).fetchone()
    if row is None:
        return None
    cols = [desc[0] for desc in conn.description]
    return dict(zip(cols, row))


def get_complex_funds(conn, limit: int = 100):
    """§5.5: All funds where is_complex_product = true."""
    return conn.execute("""
        SELECT * FROM hk_funds WHERE is_complex_product = true
        ORDER BY complex_product_type, fund_name_en LIMIT ?
    """, [limit]).df()


def get_derivative_funds(conn, limit: int = 100):
    """§5.1A: All funds where is_derivative_product = true."""
    return conn.execute("""
        SELECT * FROM hk_funds WHERE is_derivative_product = true
        ORDER BY complex_product_type, fund_name_en LIMIT ?
    """, [limit]).df()


def get_fund_stats(conn):
    """Return aggregate stats about funds with dual-dimension breakdown."""
    total = conn.execute("SELECT COUNT(*) FROM hk_funds WHERE is_active = true").fetchone()[0]
    complex_cnt = conn.execute(
        "SELECT COUNT(*) FROM hk_funds WHERE is_active = true AND is_complex_product = true"
    ).fetchone()[0]
    derivative_cnt = conn.execute(
        "SELECT COUNT(*) FROM hk_funds WHERE is_active = true AND is_derivative_product = true"
    ).fetchone()[0]
    by_type = conn.execute("""
        SELECT complex_product_type, COUNT(*) as cnt FROM hk_funds
        WHERE is_active = true AND is_complex_product = true
        GROUP BY complex_product_type ORDER BY cnt DESC
    """).df()
    by_domicile = conn.execute("""
        SELECT domicile, COUNT(*) as cnt FROM hk_funds
        WHERE is_active = true GROUP BY domicile ORDER BY cnt DESC
    """).df()
    return {
        "total": total,
        "complex_count": complex_cnt,
        "derivative_count": derivative_cnt,
        "ordinary_count": total - complex_cnt,
        "by_complex_type": by_type.to_dict(orient="records") if not by_type.empty else [],
        "by_domicile": by_domicile.to_dict(orient="records") if not by_domicile.empty else [],
    }


# ═══════════════════════════════════════════════════════════════
#  hk_fund_classifications — CRUD
# ═══════════════════════════════════════════════════════════════

def upsert_fund_classifications(conn, records: List[Dict[str, Any]]) -> int:
    if not records:
        return 0
    df = pd.DataFrame(records)
    needed = [
        "fund_id", "sfc_complex_list_match", "derivative_exposure_pct",
        "is_synthetic_replication", "is_leveraged", "leverage_ratio",
        "is_inverse", "is_structured", "has_nested_derivatives",
        "uses_derivatives_for_non_hedging",
        "has_secondary_market", "has_transparent_info",
        "loss_exceeds_principal", "has_complex_payoff",
        "illiquid_or_hard_to_value",
        "classification_determination",
        "last_reviewed_date", "reviewed_by",
    ]
    for col in needed:
        if col not in df.columns:
            df[col] = None
    sub = df[needed].copy()
    conn.register("_tmp_hkfc", sub)
    try:
        rows = conn.execute("""
            INSERT INTO hk_fund_classifications (
                fund_id, sfc_complex_list_match, derivative_exposure_pct,
                is_synthetic_replication, is_leveraged, leverage_ratio,
                is_inverse, is_structured, has_nested_derivatives,
                uses_derivatives_for_non_hedging,
                has_secondary_market, has_transparent_info,
                loss_exceeds_principal, has_complex_payoff,
                illiquid_or_hard_to_value,
                classification_determination, last_reviewed_date, reviewed_by
            )
            SELECT
                fund_id, sfc_complex_list_match, derivative_exposure_pct,
                is_synthetic_replication, is_leveraged, leverage_ratio,
                is_inverse, is_structured, has_nested_derivatives,
                uses_derivatives_for_non_hedging,
                has_secondary_market, has_transparent_info,
                loss_exceeds_principal, has_complex_payoff,
                illiquid_or_hard_to_value,
                classification_determination, last_reviewed_date, reviewed_by
            FROM _tmp_hkfc
            ON CONFLICT (fund_id) DO UPDATE SET
                sfc_complex_list_match = excluded.sfc_complex_list_match,
                derivative_exposure_pct = excluded.derivative_exposure_pct,
                is_synthetic_replication = excluded.is_synthetic_replication,
                is_leveraged = excluded.is_leveraged,
                leverage_ratio = excluded.leverage_ratio,
                is_inverse = excluded.is_inverse,
                is_structured = excluded.is_structured,
                has_nested_derivatives = excluded.has_nested_derivatives,
                uses_derivatives_for_non_hedging = excluded.uses_derivatives_for_non_hedging,
                has_secondary_market = excluded.has_secondary_market,
                has_transparent_info = excluded.has_transparent_info,
                loss_exceeds_principal = excluded.loss_exceeds_principal,
                has_complex_payoff = excluded.has_complex_payoff,
                illiquid_or_hard_to_value = excluded.illiquid_or_hard_to_value,
                classification_determination = excluded.classification_determination,
                last_reviewed_date = excluded.last_reviewed_date,
                reviewed_by = excluded.reviewed_by
        """).fetchall()
    finally:
        conn.unregister("_tmp_hkfc")
    return rows[0][0] if rows else 0


def get_fund_classification(conn, fund_id: int) -> Optional[Dict]:
    row = conn.execute(
        "SELECT * FROM hk_fund_classifications WHERE fund_id = ?", [fund_id]
    ).fetchone()
    if row is None:
        return None
    cols = [desc[0] for desc in conn.description]
    return dict(zip(cols, row))


# ═══════════════════════════════════════════════════════════════
#  hk_fund_managers — CRUD
# ═══════════════════════════════════════════════════════════════

def upsert_managers(conn, records: List[Dict[str, Any]]) -> int:
    if not records:
        return 0
    df = pd.DataFrame(records)
    needed = [
        "ce_number", "company_name_en", "company_name_cn", "license_type",
        "regulated_activity_1", "regulated_activity_4", "regulated_activity_9",
        "license_status", "license_effective_date", "business_address", "website",
        "key_ro_name_en", "key_ro_name_cn", "ro_count", "total_licensed_staff",
        "has_sfc_enforcement_history", "enforcement_count", "source_url",
    ]
    for col in needed:
        if col not in df.columns:
            df[col] = None
    sub = df[needed].copy()
    conn.register("_tmp_hkfm", sub)
    try:
        rows = conn.execute("""
            INSERT INTO hk_fund_managers (
                ce_number, company_name_en, company_name_cn, license_type,
                regulated_activity_1, regulated_activity_4, regulated_activity_9,
                license_status, license_effective_date, business_address, website,
                key_ro_name_en, key_ro_name_cn, ro_count, total_licensed_staff,
                has_sfc_enforcement_history, enforcement_count, source_url
            )
            SELECT
                ce_number, company_name_en, company_name_cn, license_type,
                regulated_activity_1, regulated_activity_4, regulated_activity_9,
                license_status, license_effective_date, business_address, website,
                key_ro_name_en, key_ro_name_cn, ro_count, total_licensed_staff,
                has_sfc_enforcement_history, enforcement_count, source_url
            FROM _tmp_hkfm
            ON CONFLICT (ce_number) DO UPDATE SET
                company_name_en = excluded.company_name_en,
                company_name_cn = excluded.company_name_cn,
                license_type = excluded.license_type,
                license_status = excluded.license_status,
                regulated_activity_1 = excluded.regulated_activity_1,
                regulated_activity_4 = excluded.regulated_activity_4,
                regulated_activity_9 = excluded.regulated_activity_9,
                has_sfc_enforcement_history = excluded.has_sfc_enforcement_history,
                enforcement_count = excluded.enforcement_count,
                last_updated = now()
        """).fetchall()
    finally:
        conn.unregister("_tmp_hkfm")
    return rows[0][0] if rows else 0


def get_managers(conn, license_type: str = None, license_status: str = "active",
                 has_enforcement: bool = None, search: str = None, limit: int = 100):
    where = ["1=1"]; params = []
    if license_type:
        where.append("license_type LIKE ?"); params.append(f"%{license_type}%")
    if license_status:
        where.append("license_status = ?"); params.append(license_status)
    if has_enforcement is not None:
        where.append("has_sfc_enforcement_history = ?"); params.append(has_enforcement)
    if search:
        where.append("(company_name_en ILIKE ? OR company_name_cn ILIKE ? OR ce_number = ?)")
        like = f"%{search}%"
        params.extend([like, like, search])
    params.append(limit)
    return conn.execute(f"""
        SELECT * FROM hk_fund_managers WHERE {' AND '.join(where)}
        ORDER BY company_name_en LIMIT ?
    """, params).df()


def get_manager_by_id(conn, manager_id: int) -> Optional[Dict]:
    row = conn.execute("SELECT * FROM hk_fund_managers WHERE id = ?", [manager_id]).fetchone()
    if row is None:
        return None
    cols = [desc[0] for desc in conn.description]
    return dict(zip(cols, row))


def get_manager_stats(conn):
    total = conn.execute("SELECT COUNT(*) FROM hk_fund_managers WHERE license_status = 'active'").fetchone()[0]
    type9 = conn.execute(
        "SELECT COUNT(*) FROM hk_fund_managers WHERE license_status = 'active' AND regulated_activity_9 = true"
    ).fetchone()[0]
    with_enforcement = conn.execute(
        "SELECT COUNT(*) FROM hk_fund_managers WHERE has_sfc_enforcement_history = true"
    ).fetchone()[0]
    return {"total": total, "type9_count": type9, "with_enforcement": with_enforcement}


# ═══════════════════════════════════════════════════════════════
#  hk_fund_manager_funds — CRUD
# ═══════════════════════════════════════════════════════════════

def upsert_manager_funds(conn, records: List[Dict[str, Any]]) -> int:
    if not records:
        return 0
    df = pd.DataFrame(records)
    needed = ["fund_id", "manager_id", "role", "is_primary"]
    for col in needed:
        if col not in df.columns:
            df[col] = None
    sub = df[needed].copy()
    conn.register("_tmp_fmf", sub)
    try:
        rows = conn.execute("""
            INSERT INTO hk_fund_manager_funds (fund_id, manager_id, role, is_primary)
            SELECT fund_id, manager_id, role, is_primary FROM _tmp_fmf
            ON CONFLICT (fund_id, manager_id, role) DO NOTHING
        """).fetchall()
    finally:
        conn.unregister("_tmp_fmf")
    return rows[0][0] if rows else 0


def get_manager_funds(conn, manager_id: int, limit: int = 200):
    return conn.execute("""
        SELECT f.* FROM hk_funds f
        JOIN hk_fund_manager_funds fmf ON f.id = fmf.fund_id
        WHERE fmf.manager_id = ?
        ORDER BY f.fund_name_en LIMIT ?
    """, [manager_id, limit]).df()


# ═══════════════════════════════════════════════════════════════
#  hk_manager_regulatory_history — CRUD
# ═══════════════════════════════════════════════════════════════

def upsert_manager_regulatory(conn, records: List[Dict[str, Any]]) -> int:
    if not records:
        return 0
    df = pd.DataFrame(records)
    needed = [
        "manager_id", "source", "source_ref_no", "action_type",
        "action_date", "penalty_amount_hkd", "description_en",
        "description_cn", "source_url",
    ]
    for col in needed:
        if col not in df.columns:
            df[col] = None
    sub = df[needed].copy()
    conn.register("_tmp_mreg", sub)
    try:
        rows = conn.execute("""
            INSERT INTO hk_manager_regulatory_history (
                manager_id, source, source_ref_no, action_type,
                action_date, penalty_amount_hkd, description_en,
                description_cn, source_url
            )
            SELECT
                manager_id, source, source_ref_no, action_type,
                action_date, penalty_amount_hkd, description_en,
                description_cn, source_url
            FROM _tmp_mreg
            ON CONFLICT DO NOTHING
        """).fetchall()
    finally:
        conn.unregister("_tmp_mreg")
    return rows[0][0] if rows else 0


def get_manager_regulatory(conn, manager_id: int, limit: int = 100):
    return conn.execute("""
        SELECT * FROM hk_manager_regulatory_history
        WHERE manager_id = ?
        ORDER BY action_date DESC LIMIT ?
    """, [manager_id, limit]).df()


# ═══════════════════════════════════════════════════════════════
#  hk_fund_documents — CRUD
# ═══════════════════════════════════════════════════════════════

def upsert_fund_documents(conn, records: List[Dict[str, Any]]) -> int:
    if not records:
        return 0
    df = pd.DataFrame(records)
    needed = [
        "fund_id", "document_type", "language", "document_date",
        "sfc_authorization_date", "source_url", "local_path", "is_current",
    ]
    for col in needed:
        if col not in df.columns:
            df[col] = None
    sub = df[needed].copy()
    conn.register("_tmp_fdocs", sub)
    try:
        rows = conn.execute("""
            INSERT INTO hk_fund_documents (
                fund_id, document_type, language, document_date,
                sfc_authorization_date, source_url, local_path, is_current
            )
            SELECT
                fund_id, document_type, language, document_date,
                sfc_authorization_date, source_url, local_path, is_current
            FROM _tmp_fdocs
            ON CONFLICT DO NOTHING
        """).fetchall()
    finally:
        conn.unregister("_tmp_fdocs")
    return rows[0][0] if rows else 0


def get_fund_documents(conn, fund_id: int) -> pd.DataFrame:
    return conn.execute("""
        SELECT * FROM hk_fund_documents
        WHERE fund_id = ? ORDER BY document_date DESC
    """, [fund_id]).df()


# ═══════════════════════════════════════════════════════════════
#  hkex_listed_funds — CRUD
# ═══════════════════════════════════════════════════════════════

def upsert_hkex_funds(conn, records: List[Dict[str, Any]]) -> int:
    if not records:
        return 0
    df = pd.DataFrame(records)
    needed = [
        "stock_code", "fund_name_en", "fund_name_cn", "etf_type",
        "underlying_index", "management_fee", "aum",
        "is_sfc_authorized", "sfc_fund_id", "source",
    ]
    for col in needed:
        if col not in df.columns:
            df[col] = None
    sub = df[needed].copy()
    conn.register("_tmp_hkex", sub)
    try:
        rows = conn.execute("""
            INSERT INTO hkex_listed_funds (
                stock_code, fund_name_en, fund_name_cn, etf_type,
                underlying_index, management_fee, aum,
                is_sfc_authorized, sfc_fund_id, source
            )
            SELECT
                stock_code, fund_name_en, fund_name_cn, etf_type,
                underlying_index, management_fee, aum,
                is_sfc_authorized, sfc_fund_id, source
            FROM _tmp_hkex
            ON CONFLICT (stock_code) DO UPDATE SET
                fund_name_en = excluded.fund_name_en,
                fund_name_cn = excluded.fund_name_cn,
                etf_type = excluded.etf_type,
                aum = excluded.aum,
                last_updated = now()
        """).fetchall()
    finally:
        conn.unregister("_tmp_hkex")
    return rows[0][0] if rows else 0


def get_hkex_funds(conn, etf_type: str = None, limit: int = 100):
    where = ["1=1"]; params = []
    if etf_type:
        where.append("etf_type = ?"); params.append(etf_type)
    params.append(limit)
    return conn.execute(f"""
        SELECT * FROM hkex_listed_funds WHERE {' AND '.join(where)}
        ORDER BY stock_code LIMIT ?
    """, params).df()


def get_hkex_fund_by_code(conn, stock_code: str) -> Optional[Dict]:
    row = conn.execute(
        "SELECT * FROM hkex_listed_funds WHERE stock_code = ?", [stock_code]
    ).fetchone()
    if row is None:
        return None
    cols = [desc[0] for desc in conn.description]
    return dict(zip(cols, row))


# ═══════════════════════════════════════════════════════════════
#  Cross-cutting queries
# ═══════════════════════════════════════════════════════════════

def link_manager_to_fund(conn, fund_id: int, manager_id: int,
                         role: str = "manager", is_primary: bool = True) -> bool:
    """Create a fund-manager link. Returns True if inserted."""
    result = conn.execute("""
        INSERT INTO hk_fund_manager_funds (fund_id, manager_id, role, is_primary)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (fund_id, manager_id, role) DO NOTHING
    """, [fund_id, manager_id, role, is_primary]).fetchone()
    return result[0] > 0 if result else False


def update_fund_classification(conn, fund_id: int,
                               is_derivative_product: bool,
                               is_complex_product: bool,
                               complex_product_type: str,
                               reason: str,
                               source: str = "manual"):
    """Manually update a fund's dual-dimension classification."""
    conn.execute("""
        UPDATE hk_funds
        SET is_derivative_product = ?, is_complex_product = ?,
            complex_product_type = ?, classification_reason = ?,
            classification_source = ?, last_updated = now()
        WHERE id = ?
    """, [is_derivative_product, is_complex_product, complex_product_type,
          reason, source, fund_id])


def search_funds_fulltext(conn, query: str, limit: int = 50):
    """Full-text search across fund names, ISIN, authorization number."""
    like = f"%{query}%"
    return conn.execute("""
        SELECT * FROM hk_funds WHERE
            is_active = true AND (
                fund_name_en ILIKE ? OR fund_name_cn ILIKE ? OR
                isin = ? OR sfc_authorization_no = ? OR
                fund_manager_name_en ILIKE ? OR fund_manager_name_cn ILIKE ?
            )
        ORDER BY fund_name_en LIMIT ?
    """, [like, like, query, query, like, like, limit]).df()


# ═══════════════════════════════════════════════════════════════
#  hk_kyp_dimensions — CRUD
# ═══════════════════════════════════════════════════════════════

KYP_DIMENSIONS = [
    "product_structure",
    "risk_profile",
    "complexity",
    "derivative_class",
    "issuer_assessment",
    "fees_charges",
    "liquidity_lockup",
    "valuation_pricing",
    "credit_quality",
    "key_terms",
]


def init_kyp_dimensions(conn, fund_id: int) -> int:
    """Create all 10 pending KYP dimensions for a fund."""
    count = 0
    for dim in KYP_DIMENSIONS:
        try:
            conn.execute("""
                INSERT INTO hk_kyp_dimensions (fund_id, dimension)
                VALUES (?, ?)
                ON CONFLICT (fund_id, dimension) DO NOTHING
            """, [fund_id, dim])
            count += 1
        except Exception:
            pass
    return count


def get_kyp_dimensions(conn, fund_id: int):
    """Get all KYP dimension assessments for a fund."""
    return conn.execute("""
        SELECT * FROM hk_kyp_dimensions
        WHERE fund_id = ?
        ORDER BY
            CASE dimension
                WHEN 'product_structure' THEN 1 WHEN 'risk_profile' THEN 2
                WHEN 'complexity' THEN 3 WHEN 'derivative_class' THEN 4
                WHEN 'issuer_assessment' THEN 5 WHEN 'fees_charges' THEN 6
                WHEN 'liquidity_lockup' THEN 7 WHEN 'valuation_pricing' THEN 8
                WHEN 'credit_quality' THEN 9 WHEN 'key_terms' THEN 10
            END
    """, [fund_id]).df()


def upsert_kyp_dimension(conn, fund_id: int, dimension: str, updates: dict) -> bool:
    """Update a single KYP dimension. Auto-logs the change."""
    current = conn.execute(
        "SELECT assessment_status, score FROM hk_kyp_dimensions WHERE fund_id = ? AND dimension = ?",
        [fund_id, dimension]
    ).fetchone()
    prev_status = current[0] if current else None
    prev_score = current[1] if current else None

    set_clauses = []
    params = []
    for k, v in updates.items():
        set_clauses.append(f"{k} = ?")
        params.append(v)
    if "last_updated" not in updates:
        set_clauses.append("last_updated = now()")
    params.extend([fund_id, dimension])
    conn.execute(f"""
        UPDATE hk_kyp_dimensions SET {', '.join(set_clauses)}
        WHERE fund_id = ? AND dimension = ?
    """, params)

    new_status = updates.get("assessment_status", prev_status)
    new_score = updates.get("score", prev_score)
    log_kyp_action(conn, fund_id, dimension, "updated",
                   prev_status, new_status, prev_score, new_score,
                   updates.get("assessed_by"),
                   updates.get("findings", "")[:500])
    return True


def get_funds_with_kyp_gaps(conn, limit: int = 50, offset: int = 0, gaps_only: bool = False):
    """Get funds with KYP assessment status. Supports pagination and optional gap filter."""
    having_clause = "HAVING dimensions_missing > 0" if gaps_only else ""
    return conn.execute(f"""
        SELECT f.id, f.fund_name_en, f.sfc_authorization_no,
               COUNT(kd.id) as dimensions_assessed,
               (10 - COUNT(kd.id)) as dimensions_missing,
               MAX(kd.last_updated) as last_updated
        FROM hk_funds f
        LEFT JOIN hk_kyp_dimensions kd ON f.id = kd.fund_id
            AND kd.assessment_status IN ('reviewed', 'approved')
        WHERE f.is_active = true
        GROUP BY f.id, f.fund_name_en, f.sfc_authorization_no
        {having_clause}
        ORDER BY dimensions_missing DESC, f.fund_name_en
        LIMIT ? OFFSET ?
    """, [limit, offset]).df()

def get_kyp_funds_count(conn, gaps_only: bool = False):
    """Get total count of funds for KYP pagination."""
    having_clause = "HAVING dimensions_missing > 0" if gaps_only else ""
    result = conn.execute(f"""
        SELECT COUNT(*) FROM (
            SELECT COUNT(kd.id) as dimensions_assessed,
                   (10 - COUNT(kd.id)) as dimensions_missing
            FROM hk_funds f
            LEFT JOIN hk_kyp_dimensions kd ON f.id = kd.fund_id
                AND kd.assessment_status IN ('reviewed', 'approved')
            WHERE f.is_active = true
            GROUP BY f.id
            {having_clause}
        ) sub
    """).fetchone()
    return int(result[0]) if result else 0


def get_kyp_stats(conn):
    """Get aggregate KYP assessment statistics."""
    stats = conn.execute("""
        SELECT
            COUNT(DISTINCT f.id) as funds_with_kyp,
            COUNT(DISTINCT CASE WHEN assessed_count = 10 THEN f.id END) as funds_fully_assessed,
            COUNT(DISTINCT CASE WHEN assessed_count > 0 AND assessed_count < 10 THEN f.id END) as funds_partially_assessed,
            COUNT(DISTINCT CASE WHEN assessed_count = 0 OR assessed_count IS NULL THEN f.id END) as funds_not_started,
            COALESCE(SUM(assessed_count), 0) as total_dimensions_assessed,
            COUNT(DISTINCT f.id) * 10 as total_dimensions
        FROM hk_funds f
        LEFT JOIN (
            SELECT fund_id, COUNT(*) as assessed_count
            FROM hk_kyp_dimensions
            WHERE assessment_status IN ('reviewed', 'approved')
            GROUP BY fund_id
        ) kd ON f.id = kd.fund_id
        WHERE f.is_active = true
    """).fetchone()
    return {
        "funds_with_kyp": int(stats[0]),
        "funds_fully_assessed": int(stats[1]),
        "funds_partially_assessed": int(stats[2]),
        "funds_not_started": int(stats[3]),
        "total_dimensions_assessed": int(stats[4]),
        "total_dimensions": int(stats[5]),
        "completion_pct": round(stats[4] / stats[5] * 100, 1) if stats[5] > 0 else 0,
        "review_progress_pct": round(stats[1] / stats[0] * 100, 1) if stats[0] > 0 else 0,
    }


# ═══════════════════════════════════════════════════════════════
#  hk_kyp_assessment_log — CRUD
# ═══════════════════════════════════════════════════════════════

def log_kyp_action(conn, fund_id: int, dimension: str, action: str,
                   prev_status=None, new_status=None, prev_score=None,
                   new_score=None, changed_by=None, change_reason=None):
    """Record a KYP assessment action in the audit log."""
    conn.execute("""
        INSERT INTO hk_kyp_assessment_log (
            fund_id, dimension, action, previous_status, new_status,
            previous_score, new_score, changed_by, change_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [fund_id, dimension, action, prev_status, new_status,
          prev_score, new_score, changed_by, change_reason])


def get_kyp_assessment_history(conn, fund_id: int, limit: int = 50):
    return conn.execute("""
        SELECT * FROM hk_kyp_assessment_log
        WHERE fund_id = ? ORDER BY created_at DESC LIMIT ?
    """, [fund_id, limit]).df()


# ═══════════════════════════════════════════════════════════════
#  hk_fund_risk_ratings — CRUD
# ═══════════════════════════════════════════════════════════════

RISK_CATEGORIES = ["Low", "Medium-Low", "Medium", "Medium-High", "High"]


def upsert_fund_risk_rating(conn, fund_id: int, rating: dict) -> bool:
    conn.execute("""
        INSERT INTO hk_fund_risk_ratings (
            fund_id, overall_risk_score, risk_category, methodology_version,
            is_automated, score_breakdown, supporting_rationale, last_calculated
        ) VALUES (?, ?, ?, ?, ?, ?, ?, now())
        ON CONFLICT (fund_id) DO UPDATE SET
            overall_risk_score = excluded.overall_risk_score,
            risk_category = excluded.risk_category,
            methodology_version = excluded.methodology_version,
            is_automated = excluded.is_automated,
            score_breakdown = excluded.score_breakdown,
            supporting_rationale = excluded.supporting_rationale,
            last_calculated = now()
    """, [
        fund_id, rating.get("overall_risk_score"),
        rating.get("risk_category"),
        rating.get("methodology_version", "1.0"),
        rating.get("is_automated", True),
        rating.get("score_breakdown"),
        rating.get("supporting_rationale"),
    ])
    return True


def override_risk_rating(conn, fund_id: int, new_score: float,
                         new_category: str, reason: str, overridden_by: str) -> bool:
    conn.execute("""
        UPDATE hk_fund_risk_ratings SET
            overall_risk_score = ?, risk_category = ?,
            is_automated = false, override_reason = ?,
            overridden_by = ?, override_date = CURRENT_DATE,
            last_calculated = now()
        WHERE fund_id = ?
    """, [new_score, new_category, reason, overridden_by, fund_id])
    return True


def get_fund_risk_rating(conn, fund_id: int) -> Optional[Dict]:
    row = conn.execute(
        "SELECT * FROM hk_fund_risk_ratings WHERE fund_id = ?", [fund_id]
    ).fetchone()
    if row is None:
        return None
    cols = [desc[0] for desc in conn.description]
    return dict(zip(cols, row))


def get_all_risk_ratings(conn, risk_category: str = None, limit: int = None, offset: int = 0):
    query = """
        SELECT f.id as fund_id, f.fund_name_en, f.sfc_authorization_no,
               f.fund_manager_name_en, f.is_derivative_product,
               f.is_complex_product, f.complex_product_type,
               r.overall_risk_score, r.risk_category, r.is_automated,
               r.last_calculated
        FROM hk_funds f
        LEFT JOIN hk_fund_risk_ratings r ON f.id = r.fund_id
        WHERE f.is_active = true
    """
    params = []
    if risk_category:
        query += " AND r.risk_category = ?"
        params.append(risk_category)
    query += " ORDER BY r.overall_risk_score DESC NULLS LAST"
    if limit is not None:
        query += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])
    return conn.execute(query, params).df()


# ═══════════════════════════════════════════════════════════════
#  hk_manager_dd — CRUD
# ═══════════════════════════════════════════════════════════════

MANAGER_DD_DIMENSIONS = [
    "financial_resources", "human_resources", "internal_controls",
    "risk_governance", "segregation_duties", "compliance_function",
    "audit_function", "custodian_dd", "valuer_dd", "delegates_monitoring",
]


def init_manager_dd(conn, manager_id: int) -> int:
    count = 0
    for dim in MANAGER_DD_DIMENSIONS:
        try:
            conn.execute("""
                INSERT INTO hk_manager_dd (manager_id, dd_dimension)
                VALUES (?, ?)
                ON CONFLICT (manager_id, dd_dimension) DO NOTHING
            """, [manager_id, dim])
            count += 1
        except Exception:
            pass
    return count


def get_manager_dd(conn, manager_id: int):
    return conn.execute("""
        SELECT * FROM hk_manager_dd
        WHERE manager_id = ?
        ORDER BY
            CASE dd_dimension
                WHEN 'financial_resources' THEN 1 WHEN 'human_resources' THEN 2
                WHEN 'internal_controls' THEN 3 WHEN 'risk_governance' THEN 4
                WHEN 'segregation_duties' THEN 5 WHEN 'compliance_function' THEN 6
                WHEN 'audit_function' THEN 7 WHEN 'custodian_dd' THEN 8
                WHEN 'valuer_dd' THEN 9 WHEN 'delegates_monitoring' THEN 10
            END
    """, [manager_id]).df()


def upsert_manager_dd(conn, manager_id: int, dd_dimension: str, updates: dict) -> bool:
    set_clauses = []
    params = []
    for k, v in updates.items():
        set_clauses.append(f"{k} = ?")
        params.append(v)
    if "last_updated" not in updates:
        set_clauses.append("last_updated = now()")
    params.extend([manager_id, dd_dimension])
    conn.execute(f"""
        UPDATE hk_manager_dd SET {', '.join(set_clauses)}
        WHERE manager_id = ? AND dd_dimension = ?
    """, params)
    return True


def get_managers_with_dd_gaps(conn, limit: int = 50):
    return conn.execute("""
        SELECT m.id, m.company_name_en, m.ce_number,
               COUNT(dd.id) as dimensions_assessed,
               (10 - COUNT(dd.id)) as dimensions_missing
        FROM hk_fund_managers m
        LEFT JOIN hk_manager_dd dd ON m.id = dd.manager_id
            AND dd.assessment_status IN ('reviewed', 'approved')
        GROUP BY m.id, m.company_name_en, m.ce_number
        HAVING dimensions_missing > 0 OR COUNT(dd.id) = 0
        ORDER BY dimensions_missing DESC
        LIMIT ?
    """, [limit]).df()


# ═══════════════════════════════════════════════════════════════
#  hk_fund_manager_aum — CRUD
# ═══════════════════════════════════════════════════════════════


def upsert_manager_aum(conn, manager_id: int, aum_data: dict) -> bool:
    """Insert or update manager AUM record.

    aum_data keys: aum, aum_currency, aum_date, aum_source, aum_raw_text
    Returns True if data was stored.
    """
    aum = aum_data.get("aum")
    if aum is None:
        return False

    conn.execute("""
        INSERT INTO hk_fund_manager_aum (
            manager_id, aum, aum_currency, aum_date, aum_source, aum_raw_text
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (manager_id) DO UPDATE SET
            aum = excluded.aum,
            aum_currency = excluded.aum_currency,
            aum_date = excluded.aum_date,
            aum_source = excluded.aum_source,
            aum_raw_text = excluded.aum_raw_text,
            last_updated = now()
    """, [
        manager_id,
        aum,
        aum_data.get("aum_currency", "USD"),
        aum_data.get("aum_date"),
        aum_data.get("aum_source", "manager_website"),
        aum_data.get("aum_raw_text"),
    ])
    return True


def get_manager_aum(conn, manager_id: int) -> Optional[Dict]:
    """Get latest AUM for a manager."""
    row = conn.execute(
        "SELECT * FROM hk_fund_manager_aum WHERE manager_id = ?",
        [manager_id],
    ).fetchone()
    if row is None:
        return None
    cols = [desc[0] for desc in conn.description]
    return dict(zip(cols, row))


# ═══════════════════════════════════════════════════════════════
#  hk_manager_profiles — CRUD
# ═══════════════════════════════════════════════════════════════


def upsert_manager_profile(conn, manager_id: int, profile: dict) -> bool:
    """Insert or update a manager's company profile from website scraping.

    profile keys (from LLM extraction):
        company_description_en, company_description_cn, founding_year,
        total_staff, investment_professionals, offices (list), key_personnel (list),
        awards (list), aum_usd, aum_date, investment_philosophy, asset_classes (list),
        institutional_clients, regulatory_licenses (list),
        data_source, extraction_date

    JSON-serializable lists are stored as JSON strings.
    Returns True if stored.
    """
    def _to_json(val):
        if val is None:
            return None
        if isinstance(val, (list, dict)):
            return json.dumps(val, ensure_ascii=False)
        return val

    conn.execute("""
        INSERT INTO hk_manager_profiles (
            manager_id, company_description_en, company_description_cn,
            founding_year, total_staff, investment_professionals,
            offices, key_personnel, awards, aum_usd, aum_date,
            investment_philosophy, asset_classes, institutional_clients,
            regulatory_licenses, data_source, extraction_date
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (manager_id) DO UPDATE SET
            company_description_en = excluded.company_description_en,
            company_description_cn = excluded.company_description_cn,
            founding_year = excluded.founding_year,
            total_staff = excluded.total_staff,
            investment_professionals = excluded.investment_professionals,
            offices = excluded.offices,
            key_personnel = excluded.key_personnel,
            awards = excluded.awards,
            aum_usd = excluded.aum_usd,
            aum_date = excluded.aum_date,
            investment_philosophy = excluded.investment_philosophy,
            asset_classes = excluded.asset_classes,
            institutional_clients = excluded.institutional_clients,
            regulatory_licenses = excluded.regulatory_licenses,
            data_source = excluded.data_source,
            extraction_date = excluded.extraction_date,
            last_updated = now()
    """, [
        manager_id,
        profile.get("company_description_en"),
        profile.get("company_description_cn"),
        profile.get("founding_year"),
        profile.get("total_staff"),
        profile.get("investment_professionals"),
        _to_json(profile.get("offices")),
        _to_json(profile.get("key_personnel")),
        _to_json(profile.get("awards")),
        profile.get("aum_usd"),
        profile.get("aum_date"),
        profile.get("investment_philosophy"),
        _to_json(profile.get("asset_classes")),
        profile.get("institutional_clients"),
        _to_json(profile.get("regulatory_licenses")),
        profile.get("data_source"),
        profile.get("extraction_date"),
    ])
    return True


def get_manager_profile(conn, manager_id: int) -> Optional[Dict]:
    """Get company profile for a manager."""
    row = conn.execute(
        "SELECT * FROM hk_manager_profiles WHERE manager_id = ?",
        [manager_id],
    ).fetchone()
    if row is None:
        return None
    cols = [desc[0] for desc in conn.description]
    result = dict(zip(cols, row))

    # Parse JSON columns back to Python objects
    for json_col in ("offices", "key_personnel", "awards", "asset_classes", "regulatory_licenses"):
        val = result.get(json_col)
        if isinstance(val, str):
            try:
                result[json_col] = json.loads(val)
            except json.JSONDecodeError:
                pass

    return result


# ═══════════════════════════════════════════════════════════════
#  hk_rating_templates — CRUD
# ═══════════════════════════════════════════════════════════════


def upsert_template(conn, template: dict) -> int:
    """Insert or update a rating template. Returns template_id."""
    template_id = template.get("id")
    if template_id:
        # Update existing
        conn.execute("""
            UPDATE hk_rating_templates SET
                name = ?, description = ?, methodology_version = ?,
                category_thresholds_json = ?, updated_at = now()
            WHERE id = ?
        """, [
            template.get("name"), template.get("description"),
            template.get("methodology_version", "1.0"),
            template.get("category_thresholds_json"),
            template_id,
        ])
        return template_id
    else:
        # Insert new
        row = conn.execute("""
            INSERT INTO hk_rating_templates (
                user_id, name, description, template_type,
                methodology_version, is_system, category_thresholds_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            RETURNING id
        """, [
            template.get("user_id", "system"),
            template["name"],
            template.get("description"),
            template["template_type"],
            template.get("methodology_version", "1.0"),
            template.get("is_system", False),
            template.get("category_thresholds_json"),
        ]).fetchone()
        return row[0] if row else 0


def get_template(conn, template_id: int) -> Optional[Dict]:
    """Get a template by ID."""
    row = conn.execute(
        "SELECT * FROM hk_rating_templates WHERE id = ?", [template_id]
    ).fetchone()
    if row is None:
        return None
    cols = [desc[0] for desc in conn.description]
    return dict(zip(cols, row))


def get_user_templates(conn, user_id: str, template_type: str = None):
    """Get templates owned by a user."""
    query = """SELECT t.id, t.user_id, t.name, t.description, t.template_type,
                      t.methodology_version, t.is_system, t.category_thresholds_json,
                      t.created_at, t.updated_at, COUNT(f.id) as factor_count
               FROM hk_rating_templates t
               LEFT JOIN hk_template_factors f ON t.id = f.template_id
               WHERE t.user_id = ?"""
    params = [user_id]
    if template_type:
        query += " AND t.template_type = ?"
        params.append(template_type)
    query += " GROUP BY t.id, t.user_id, t.name, t.description, t.template_type, t.methodology_version, t.is_system, t.category_thresholds_json, t.created_at, t.updated_at ORDER BY t.is_system DESC, t.updated_at DESC"
    return conn.execute(query, params).df()


def get_system_templates(conn, template_type: str = None):
    """Get system-owned templates."""
    query = """SELECT t.id, t.user_id, t.name, t.description, t.template_type,
                      t.methodology_version, t.is_system, t.category_thresholds_json,
                      t.created_at, t.updated_at, COUNT(f.id) as factor_count
               FROM hk_rating_templates t
               LEFT JOIN hk_template_factors f ON t.id = f.template_id
               WHERE t.is_system = true"""
    params = []
    if template_type:
        query += " AND t.template_type = ?"
        params.append(template_type)
    query += " GROUP BY t.id, t.user_id, t.name, t.description, t.template_type, t.methodology_version, t.is_system, t.category_thresholds_json, t.created_at, t.updated_at ORDER BY t.name"
    return conn.execute(query, params).df()


def delete_template(conn, template_id: int) -> bool:
    """Delete a user-owned template and its factors. Cannot delete system templates."""
    row = conn.execute(
        "SELECT user_id FROM hk_rating_templates WHERE id = ?", [template_id]
    ).fetchone()
    if row is None or row[0] == 'system':
        return False
    conn.execute("DELETE FROM hk_template_factors WHERE template_id = ?", [template_id])
    conn.execute("DELETE FROM hk_user_ratings WHERE template_id = ?", [template_id])
    conn.execute("DELETE FROM hk_rating_templates WHERE id = ?", [template_id])
    return True


# ═══════════════════════════════════════════════════════════════
#  hk_template_factors — CRUD
# ═══════════════════════════════════════════════════════════════


def upsert_template_factors(conn, template_id: int, factors: List[Dict]) -> int:
    """Replace all factors for a template. Returns count of factors stored."""
    conn.execute("DELETE FROM hk_template_factors WHERE template_id = ?", [template_id])
    count = 0
    for f in factors:
        conn.execute("""
            INSERT INTO hk_template_factors (
                template_id, factor_key, factor_label, weight, ordinal, config_json
            ) VALUES (?, ?, ?, ?, ?, ?)
        """, [
            template_id, f["factor_key"], f.get("factor_label"),
            f.get("weight"), f.get("ordinal", 0),
            f.get("config_json"),
        ])
        count += 1
    return count


def get_template_factors(conn, template_id: int):
    """Get all factors for a template, ordered by ordinal."""
    return conn.execute("""
        SELECT * FROM hk_template_factors
        WHERE template_id = ? ORDER BY ordinal
    """, [template_id]).df()


# ═══════════════════════════════════════════════════════════════
#  hk_user_ratings — CRUD
# ═══════════════════════════════════════════════════════════════


def upsert_user_rating(conn, rating: dict) -> bool:
    """Store a user rating result. Marks previous latest as false."""
    conn.execute("""
        UPDATE hk_user_ratings SET is_latest = false
        WHERE template_id = ? AND user_id = ?
          AND target_type = ? AND target_id = ?
    """, [
        rating["template_id"], rating["user_id"],
        rating["target_type"], rating["target_id"],
    ])
    conn.execute("""
        INSERT INTO hk_user_ratings (
            template_id, user_id, target_type, target_id,
            overall_score, category, factor_scores_json,
            methodology_version, is_latest
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, true)
    """, [
        rating["template_id"], rating["user_id"],
        rating["target_type"], rating["target_id"],
        rating.get("overall_score"), rating.get("category"),
        rating.get("factor_scores_json"),
        rating.get("methodology_version", "1.0"),
    ])
    return True


def get_user_ratings(conn, template_id: int, user_id: str,
                     target_type: str = None, target_id: int = None,
                     is_latest: bool = True):
    """Get user rating results."""
    query = """SELECT r.*,
               CASE WHEN r.target_type = 'fund' THEN f.fund_name_en
                    ELSE m.company_name_en END as target_name
               FROM hk_user_ratings r
               LEFT JOIN hk_funds f ON r.target_type = 'fund' AND r.target_id = f.id
               LEFT JOIN hk_fund_managers m ON r.target_type = 'manager' AND r.target_id = m.id
               WHERE r.template_id = ? AND r.user_id = ?"""
    params = [template_id, user_id]
    if is_latest:
        query += " AND r.is_latest = true"
    if target_type:
        query += " AND r.target_type = ?"
        params.append(target_type)
    if target_id:
        query += " AND r.target_id = ?"
        params.append(target_id)
    query += " ORDER BY r.overall_score DESC"
    return conn.execute(query, params).df()


def get_user_rating_summary(conn, template_id: int, user_id: str,
                            target_type: str = "fund") -> dict:
    """Get category distribution for a template+user combo."""
    rows = conn.execute("""
        SELECT category, COUNT(*) as cnt
        FROM hk_user_ratings
        WHERE template_id = ? AND user_id = ?
          AND target_type = ? AND is_latest = true
        GROUP BY category ORDER BY cnt DESC
    """, [template_id, user_id, target_type]).fetchall()
    total = sum(r[1] for r in rows)
    return {
        "template_id": template_id, "user_id": user_id,
        "target_type": target_type, "total_rated": total,
        "distribution": [{"category": r[0], "count": r[1]} for r in rows],
    }


def clone_template(conn, source_template_id: int, new_user_id: str,
                   new_name: str) -> Optional[int]:
    """Deep-clone a template for a new user. Returns new template_id."""
    source = get_template(conn, source_template_id)
    if source is None:
        return None

    new_template = {
        "user_id": new_user_id,
        "name": new_name,
        "description": source.get("description", ""),
        "template_type": source["template_type"],
        "methodology_version": source.get("methodology_version", "1.0"),
        "is_system": False,
        "category_thresholds_json": source.get("category_thresholds_json"),
    }
    new_id = upsert_template(conn, new_template)

    # Copy factors
    factors_df = get_template_factors(conn, source_template_id)
    if len(factors_df) > 0:
        for _, row in factors_df.iterrows():
            conn.execute("""
                INSERT INTO hk_template_factors (
                    template_id, factor_key, factor_label, weight, ordinal, config_json
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, [
                new_id, row["factor_key"], row.get("factor_label"),
                row.get("weight"), row.get("ordinal", 0),
                row.get("config_json"),
            ])

    logger.info(f"Cloned template {source_template_id} → {new_id} for user {new_user_id}")
    return new_id


# ═══════════════════════════════════════════════════════════════
#  hk_non_authorized_funds — CRUD
# ═══════════════════════════════════════════════════════════════

def upsert_non_authorized_funds(conn, records: List[Dict[str, Any]]) -> int:
    if not records:
        return 0
    df = pd.DataFrame(records)
    needed = [
        "fund_name_en", "fund_name_cn", "isin", "bloomberg_ticker",
        "fund_type", "domicile", "currency", "fund_manager_name_en",
        "fund_manager_name_cn", "distribution_restriction",
        "min_investment_hkd", "is_active", "data_source", "notes",
    ]
    for col in needed:
        if col not in df.columns:
            df[col] = None
    sub = df[needed].copy()
    conn.register("_tmp_naf", sub)
    try:
        rows = conn.execute("""
            INSERT INTO hk_non_authorized_funds (
                fund_name_en, fund_name_cn, isin, bloomberg_ticker,
                fund_type, domicile, currency, fund_manager_name_en,
                fund_manager_name_cn, distribution_restriction,
                min_investment_hkd, is_active, data_source, notes
            )
            SELECT
                fund_name_en, fund_name_cn, isin, bloomberg_ticker,
                fund_type, domicile, currency, fund_manager_name_en,
                fund_manager_name_cn, distribution_restriction,
                min_investment_hkd, is_active, data_source, notes
            FROM _tmp_naf
            ON CONFLICT (fund_name_en, fund_manager_name_en, data_source) DO NOTHING
        """).fetchall()
    finally:
        conn.unregister("_tmp_naf")
    return rows[0][0] if rows else 0


def get_non_authorized_funds(conn, distribution_restriction: str = None,
                              is_active: bool = True, limit: int = 100):
    where = ["is_active = ?"]
    params = [is_active]
    if distribution_restriction:
        where.append("distribution_restriction = ?")
        params.append(distribution_restriction)
    params.append(limit)
    return conn.execute(f"""
        SELECT * FROM hk_non_authorized_funds
        WHERE {' AND '.join(where)}
        ORDER BY fund_name_en LIMIT ?
    """, params).df()


def get_non_authorized_fund(conn, fund_id: int) -> Optional[Dict]:
    row = conn.execute(
        "SELECT * FROM hk_non_authorized_funds WHERE id = ?", [fund_id]
    ).fetchone()
    if row is None:
        return None
    cols = [desc[0] for desc in conn.description]
    return dict(zip(cols, row))


# ═══════════════════════════════════════════════════════════════
#  hk_fund_nav_history — CRUD
# ═══════════════════════════════════════════════════════════════

def upsert_nav_history(conn, fund_id: int, records: List[Dict[str, Any]]) -> int:
    """Batch upsert NAV history records. Returns count inserted."""
    if not records:
        return 0
    count = 0
    for r in records:
        try:
            conn.execute("""
                INSERT INTO hk_fund_nav_history (fund_id, nav_date, nav, nav_currency, source)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (fund_id, nav_date) DO UPDATE SET
                    nav = excluded.nav,
                    nav_currency = excluded.nav_currency,
                    source = excluded.source
            """, [
                fund_id,
                r.get("nav_date"),
                r.get("nav"),
                r.get("nav_currency", "HKD"),
                r.get("source", "manager_website"),
            ])
            count += 1
        except Exception:
            pass
    return count


def get_nav_history(conn, fund_id: int, start: str = None, end: str = None,
                    limit: int = 500) -> pd.DataFrame:
    """Get NAV time series for a fund."""
    where = ["fund_id = ?"]
    params = [fund_id]
    if start:
        where.append("nav_date >= ?")
        params.append(start)
    if end:
        where.append("nav_date <= ?")
        params.append(end)
    params.append(limit)
    return conn.execute(f"""
        SELECT * FROM hk_fund_nav_history
        WHERE {' AND '.join(where)}
        ORDER BY nav_date DESC LIMIT ?
    """, params).df()


def get_latest_nav(conn, fund_id: int) -> Optional[Dict]:
    """Get the most recent NAV record for a fund."""
    row = conn.execute("""
        SELECT * FROM hk_fund_nav_history
        WHERE fund_id = ? AND nav IS NOT NULL
        ORDER BY nav_date DESC LIMIT 1
    """, [fund_id]).fetchone()
    if row is None:
        return None
    cols = [desc[0] for desc in conn.description]
    return dict(zip(cols, row))


# ═══════════════════════════════════════════════════════════════
#  hk_fund_performance — CRUD
# ═══════════════════════════════════════════════════════════════

def upsert_fund_performance(conn, fund_id: int, metrics: Dict[str, Any]) -> bool:
    """Store computed performance metrics. Upsert by fund_id."""
    conn.execute("""
        INSERT INTO hk_fund_performance (
            fund_id, ytd_return_pct, return_1m_pct, return_3m_pct,
            return_6m_pct, return_1y_pct, return_3y_annualized_pct,
            return_5y_annualized_pct, std_dev_3y, sharpe_ratio_3y,
            max_drawdown_pct, max_drawdown_period,
            alpha_3y, beta_3y, r_squared_3y,
            data_points_used, calculation_date, last_updated
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_DATE, now()
        )
        ON CONFLICT (fund_id) DO UPDATE SET
            ytd_return_pct = excluded.ytd_return_pct,
            return_1m_pct = excluded.return_1m_pct,
            return_3m_pct = excluded.return_3m_pct,
            return_6m_pct = excluded.return_6m_pct,
            return_1y_pct = excluded.return_1y_pct,
            return_3y_annualized_pct = excluded.return_3y_annualized_pct,
            return_5y_annualized_pct = excluded.return_5y_annualized_pct,
            std_dev_3y = excluded.std_dev_3y,
            sharpe_ratio_3y = excluded.sharpe_ratio_3y,
            max_drawdown_pct = excluded.max_drawdown_pct,
            max_drawdown_period = excluded.max_drawdown_period,
            alpha_3y = excluded.alpha_3y,
            beta_3y = excluded.beta_3y,
            r_squared_3y = excluded.r_squared_3y,
            data_points_used = excluded.data_points_used,
            calculation_date = excluded.calculation_date,
            last_updated = now()
    """, [
        fund_id,
        metrics.get("ytd_return_pct"),
        metrics.get("return_1m_pct"),
        metrics.get("return_3m_pct"),
        metrics.get("return_6m_pct"),
        metrics.get("return_1y_pct"),
        metrics.get("return_3y_annualized_pct"),
        metrics.get("return_5y_annualized_pct"),
        metrics.get("std_dev_3y"),
        metrics.get("sharpe_ratio_3y"),
        metrics.get("max_drawdown_pct"),
        metrics.get("max_drawdown_period"),
        metrics.get("alpha_3y"),
        metrics.get("beta_3y"),
        metrics.get("r_squared_3y"),
        metrics.get("data_points_used"),
    ])
    return True


def get_fund_performance(conn, fund_id: int) -> Optional[Dict]:
    """Get performance metrics for a fund."""
    row = conn.execute(
        "SELECT * FROM hk_fund_performance WHERE fund_id = ?", [fund_id]
    ).fetchone()
    if row is None:
        return None
    cols = [desc[0] for desc in conn.description]
    return dict(zip(cols, row))


def get_funds_with_performance(conn, limit: int = 100) -> pd.DataFrame:
    """Get funds that have performance data, joined with fund names."""
    return conn.execute("""
        SELECT f.id, f.fund_name_en, f.sfc_authorization_no,
               p.ytd_return_pct, p.return_1y_pct, p.return_3y_annualized_pct,
               p.sharpe_ratio_3y, p.max_drawdown_pct, p.calculation_date
        FROM hk_fund_performance p
        JOIN hk_funds f ON f.id = p.fund_id
        WHERE f.is_active = true
        ORDER BY p.ytd_return_pct DESC NULLS LAST
        LIMIT ?
    """, [limit]).df()


# ═══════════════════════════════════════════════════════════════
#  Fund-Manager Website Mapping Helper
# ═══════════════════════════════════════════════════════════════

def get_fund_id_for_stock_code(conn, stock_code: str) -> Optional[int]:
    """Map HKEX stock code to hk_funds.id via hkex_listed_funds.sfc_fund_id."""
    row = conn.execute(
        "SELECT sfc_fund_id FROM hkex_listed_funds WHERE stock_code = ?",
        [stock_code]
    ).fetchone()
    return row[0] if row and row[0] else None


def find_fund_by_isin(conn, isin: str) -> Optional[Dict]:
    """Find a fund by ISIN code."""
    row = conn.execute(
        "SELECT * FROM hk_funds WHERE isin = ?", [isin]
    ).fetchone()
    if row is None:
        return None
    cols = [desc[0] for desc in conn.description]
    return dict(zip(cols, row))


def update_fund_from_manager(conn, fund_id: int, data: Dict[str, Any]) -> bool:
    """Update fund record with data from manager website scraping.

    Only sets non-None values to avoid overwriting existing data with blanks.
    """
    settable = [
        "isin", "bloomberg_ticker", "nav", "nav_date", "currency",
        "management_fee_pct", "performance_fee_pct",
        "expense_ratio_pct", "front_load_pct", "back_load_pct",
        "benchmark_name", "fund_inception_date", "aum", "aum_date",
        "distribution_frequency", "dividend_yield_12m_pct",
        "min_subscription_hkd", "min_subscription_usd",
        "subscription_mode", "redemption_frequency",
        "kfs_document_url", "product_key_features",
        "source_type", "portfolio_manager_name", "product_url",
        "morningstar_rating", "investment_objective",
        "ongoing_charges_pct", "min_subscription_initial_hkd",
        "min_subscription_additional_hkd", "fund_size_hkd",
        "fund_size_date", "last_dividend_date", "last_dividend_amount",
        "share_class_name", "share_class_currency",
        "is_distributing", "is_hedged",
    ]
    set_clauses = []
    params = []
    for col in settable:
        if col in data and data[col] is not None:
            set_clauses.append(f"{col} = ?")
            params.append(data[col])
    if not set_clauses:
        return False
    set_clauses.append("last_updated = now()")
    params.append(fund_id)
    conn.execute(f"""
        UPDATE hk_funds SET {', '.join(set_clauses)} WHERE id = ?
    """, params)
    return True


# ═══════════════════════════════════════════════════════════════
#  hk_fund_holdings — CRUD
# ═══════════════════════════════════════════════════════════════

def upsert_holdings(conn, fund_id: int, records: List[Dict[str, Any]]) -> int:
    """Batch upsert fund holdings. Deletes old holdings for fund_id first if records provided.
    Returns count inserted."""
    if not records:
        return 0
    # Clear old holdings for this fund
    conn.execute("DELETE FROM hk_fund_holdings WHERE fund_id = ?", [fund_id])
    count = 0
    for r in records:
        try:
            conn.execute("""
                INSERT INTO hk_fund_holdings (
                    fund_id, rank, holding_name, weight_pct, ticker, isin,
                    sector, country, asset_class, holding_date, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (fund_id, rank) DO UPDATE SET
                    holding_name = excluded.holding_name,
                    weight_pct = excluded.weight_pct,
                    ticker = excluded.ticker,
                    isin = excluded.isin,
                    sector = excluded.sector,
                    country = excluded.country,
                    asset_class = excluded.asset_class,
                    holding_date = excluded.holding_date,
                    source = excluded.source
            """, [
                fund_id,
                r.get("rank"),
                r.get("holding_name"),
                r.get("weight_pct"),
                r.get("ticker"),
                r.get("isin"),
                r.get("sector"),
                r.get("country"),
                r.get("asset_class"),
                r.get("holding_date"),
                r.get("source", "manager_website"),
            ])
            count += 1
        except Exception:
            pass
    return count


def get_holdings(conn, fund_id: int, limit: int = 50) -> List[Dict]:
    """Get holdings for a fund, ordered by rank."""
    rows = conn.execute("""
        SELECT * FROM hk_fund_holdings
        WHERE fund_id = ?
        ORDER BY rank ASC LIMIT ?
    """, [fund_id, limit]).fetchall()
    if not rows:
        return []
    cols = [desc[0] for desc in conn.description]
    return [dict(zip(cols, r)) for r in rows]


# ═══════════════════════════════════════════════════════════════
#  hk_fund_dividends — CRUD
# ═══════════════════════════════════════════════════════════════

def upsert_dividends(conn, fund_id: int, records: List[Dict[str, Any]]) -> int:
    """Batch upsert dividend records. Returns count inserted."""
    if not records:
        return 0
    count = 0
    for r in records:
        try:
            conn.execute("""
                INSERT INTO hk_fund_dividends (
                    fund_id, ex_date, pay_date, dividend_amount, dividend_currency,
                    dividend_type, declaration_date, record_date, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (fund_id, ex_date, dividend_type) DO UPDATE SET
                    pay_date = excluded.pay_date,
                    dividend_amount = excluded.dividend_amount,
                    dividend_currency = excluded.dividend_currency,
                    declaration_date = excluded.declaration_date,
                    record_date = excluded.record_date,
                    source = excluded.source
            """, [
                fund_id,
                r.get("ex_date"),
                r.get("pay_date"),
                r.get("dividend_amount"),
                r.get("dividend_currency", "HKD"),
                r.get("dividend_type", "income"),
                r.get("declaration_date"),
                r.get("record_date"),
                r.get("source", "manager_website"),
            ])
            count += 1
        except Exception:
            pass
    return count


def get_dividends(conn, fund_id: int, limit: int = 50) -> List[Dict]:
    """Get dividend history for a fund, ordered by ex_date DESC."""
    rows = conn.execute("""
        SELECT * FROM hk_fund_dividends
        WHERE fund_id = ?
        ORDER BY ex_date DESC LIMIT ?
    """, [fund_id, limit]).fetchall()
    if not rows:
        return []
    cols = [desc[0] for desc in conn.description]
    return [dict(zip(cols, r)) for r in rows]


# ═══════════════════════════════════════════════════════════════
#  hk_fund_share_classes — CRUD
# ═══════════════════════════════════════════════════════════════

def upsert_share_classes(conn, fund_id: int, records: List[Dict[str, Any]]) -> int:
    """Batch upsert share class records. Returns count inserted."""
    if not records:
        return 0
    count = 0
    for r in records:
        try:
            conn.execute("""
                INSERT INTO hk_fund_share_classes (
                    fund_id, share_class_name, isin, currency, is_hedged,
                    distribution_type, min_initial_subscription,
                    management_fee_pct, ongoing_charges_pct, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (fund_id, share_class_name) DO UPDATE SET
                    isin = excluded.isin,
                    currency = excluded.currency,
                    is_hedged = excluded.is_hedged,
                    distribution_type = excluded.distribution_type,
                    min_initial_subscription = excluded.min_initial_subscription,
                    management_fee_pct = excluded.management_fee_pct,
                    ongoing_charges_pct = excluded.ongoing_charges_pct,
                    source = excluded.source
            """, [
                fund_id,
                r.get("share_class_name"),
                r.get("isin"),
                r.get("currency", "HKD"),
                r.get("is_hedged", False),
                r.get("distribution_type"),
                r.get("min_initial_subscription"),
                r.get("management_fee_pct"),
                r.get("ongoing_charges_pct"),
                r.get("source", "manager_website"),
            ])
            count += 1
        except Exception:
            pass
    return count


def get_share_classes(conn, fund_id: int) -> List[Dict]:
    """Get all share classes for a fund."""
    rows = conn.execute("""
        SELECT * FROM hk_fund_share_classes
        WHERE fund_id = ?
        ORDER BY share_class_name
    """, [fund_id]).fetchall()
    if not rows:
        return []
    cols = [desc[0] for desc in conn.description]
    return [dict(zip(cols, r)) for r in rows]


def get_portfolio_manager(conn, fund_id: int) -> Optional[Dict]:
    """Get portfolio manager info for a fund."""
    row = conn.execute("""
        SELECT id, fund_name_en, fund_manager_name_en,
               portfolio_manager_name, fund_type, sfc_authorization_no
        FROM hk_funds
        WHERE id = ? AND is_active = true
    """, [fund_id]).fetchone()
    if not row:
        return None
    return {
        "fund_id": row[0],
        "fund_name_en": row[1],
        "fund_manager_name_en": row[2],
        "portfolio_manager_name": row[3],
        "fund_type": row[4],
        "sfc_authorization_no": row[5],
    }


def search_portfolio_manager(conn, search: str, limit: int = 50) -> List[Dict]:
    """Search funds by portfolio manager or fund manager name."""
    like = f"%{search}%"
    rows = conn.execute("""
        SELECT id, fund_name_en, fund_manager_name_en,
               portfolio_manager_name, fund_type, sfc_authorization_no
        FROM hk_funds
        WHERE is_active = true
          AND portfolio_manager_name IS NOT NULL
          AND portfolio_manager_name != ''
          AND (portfolio_manager_name ILIKE ?
               OR fund_manager_name_en ILIKE ?)
        ORDER BY fund_name_en
        LIMIT ?
    """, [like, like, limit]).fetchall()
    cols = ["id", "fund_name_en", "fund_manager_name_en",
            "portfolio_manager_name", "fund_type", "sfc_authorization_no"]
    return [dict(zip(cols, r)) for r in rows]


# ═══════════════════════════════════════════════════════════════
#  Compute job tracking
# ═══════════════════════════════════════════════════════════════

def create_compute_job(conn, template_id: int, user_id: str,
                       target_type: str, total_targets: int = 0) -> int:
    """Create a compute job record. Returns job_id."""
    row = conn.execute("""
        INSERT INTO hk_compute_jobs (template_id, user_id, target_type,
                                     status, total_targets, started_at)
        VALUES (?, ?, ?, 'running', ?, now())
        RETURNING id
    """, [template_id, user_id, target_type, total_targets]).fetchone()
    return row[0]


def update_compute_job_progress(conn, job_id: int, completed: int,
                                 error_count: int = 0):
    """Update completed count for a running job."""
    conn.execute("""
        UPDATE hk_compute_jobs
        SET completed_targets = ?, error_count = ?
        WHERE id = ?
    """, [completed, error_count, job_id])


def finish_compute_job(conn, job_id: int, status: str = 'done'):
    """Mark a job as done or failed."""
    conn.execute("""
        UPDATE hk_compute_jobs
        SET status = ?, completed_at = now()
        WHERE id = ?
    """, [status, job_id])


def get_compute_job_status(conn, template_id: int, user_id: str,
                            target_type: str) -> dict | None:
    """Get the latest compute job for a template+user+type combo."""
    row = conn.execute("""
        SELECT id, template_id, user_id, target_type, status,
               total_targets, completed_targets, error_count,
               started_at, completed_at
        FROM hk_compute_jobs
        WHERE template_id = ? AND user_id = ? AND target_type = ?
        ORDER BY id DESC
        LIMIT 1
    """, [template_id, user_id, target_type]).fetchone()
    if row is None:
        return None
    cols = ["id", "template_id", "user_id", "target_type", "status",
            "total_targets", "completed_targets", "error_count",
            "started_at", "completed_at"]
    d = dict(zip(cols, row))
    for k in ["started_at", "completed_at"]:
        if d.get(k):
            d[k] = str(d[k])
    return d
