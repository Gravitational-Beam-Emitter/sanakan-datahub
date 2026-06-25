"""
Standard rating templates — built-in, system-owned (is_system = true).

Four templates:
  A1. SFC 6-Factor Fund Risk (matches existing risk_rating.py)
  A2. Broker 13-Factor Scorecard (from Scorecard_Template.xlsx)
  B1. 10-Dimension DD (matches existing manager_scoring.py)
  B2. Broker 18-Indicator Manager DD (from 发行人:管理人的评级模版.xlsx)
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

logger = logging.getLogger("hk_funds.standard_templates")


# ═══════════════════════════════════════════════════════════════════
#  Template A1: SFC 6-Factor Fund Risk
# ═══════════════════════════════════════════════════════════════════

SFC_6F_TEMPLATE: Dict[str, Any] = {
    "name": "SFC 6-Factor (v1.0)",
    "description": (
        "Default SFC-aligned 6-factor weighted scoring: complexity (25%), "
        "underlying risk (25%), leverage (15%), liquidity (15%), "
        "credit quality (10%), currency/country (10%). "
        "Matches the built-in risk_rating.py engine."
    ),
    "template_type": "fund_risk",
    "is_system": True,
    "methodology_version": "1.0",
    "category_thresholds_json": json.dumps([
        {"max": 1.5, "label": "Low"},
        {"max": 2.5, "label": "Medium-Low"},
        {"max": 3.5, "label": "Medium"},
        {"max": 4.0, "label": "Medium-High"},
        {"max": 99.0, "label": "High"},
    ]),
    "factors": [
        {
            "factor_key": "complexity",
            "factor_label": "Product Complexity",
            "weight": 0.25,
            "ordinal": 1,
            "config_json": json.dumps({
                "type": "lookup",
                "field": "complex_product_type",
                "score_map": {
                    "non_complex": 1, "complex_bond": 3,
                    "derivative_fund": 4, "synthetic_etf": 4,
                    "futures_etf": 4, "structured": 4,
                    "L&I": 5, "hedge_fund": 5, "security_token": 5,
                },
                "fallback": [
                    {"field": "is_complex_product", "value": True, "score": 4},
                    {"field": "is_derivative_product", "value": True, "score": 4},
                ],
                "default_score": 1,
            }),
        },
        {
            "factor_key": "underlying_risk",
            "factor_label": "Underlying Asset Risk",
            "weight": 0.25,
            "ordinal": 2,
            "config_json": json.dumps({
                "type": "lookup",
                "field": "fund_type",
                "score_map": {
                    "money_market": 1, "short_term_bond": 2,
                    "bond": 2, "fixed_income": 2,
                    "balanced": 3, "mixed_asset": 3,
                    "equity": 4, "sector_equity": 4,
                    "commodity": 5, "crypto": 5,
                    "unit_trust": 3, "open_ended_fund_company": 3,
                },
                "keyword_adjustments": {
                    "bond": 2, "fixed income": 2, "money market": 1,
                    "short term": 2, "equity": 4, "stock": 4,
                    "commodity": 5, "crypto": 5, "bitcoin": 5,
                    "emerging market": 4, "high yield": 4,
                    "china": 4, "asia": 4, "sector": 4,
                    "leveraged": 5, "inverse": 5, "structured": 5,
                },
                "default_score": 3,
            }),
        },
        {
            "factor_key": "leverage",
            "factor_label": "Leverage / Derivatives",
            "weight": 0.15,
            "ordinal": 3,
            "config_json": json.dumps({
                "type": "lookup",
                "field": "complex_product_type",
                "score_map": {"L&I": 5},
                "keyword_adjustments": {
                    "leveraged": 5, "inverse": 5, "2x": 5, "3x": 5,
                    "bull": 4, "bear": 4,
                },
                "fallback": [
                    {"field": "is_derivative_product", "value": True, "score": 4},
                    {"field": "is_complex_product", "value": True,
                     "complex_types": ["synthetic_etf", "futures_etf", "structured"],
                     "score": 3},
                ],
                "default_score": 1,
            }),
        },
        {
            "factor_key": "liquidity",
            "factor_label": "Liquidity / Lock-up",
            "weight": 0.15,
            "ordinal": 4,
            "config_json": json.dumps({
                "type": "range",
                "field": "lockup_period_days",
                "bands": [
                    {"max": 0, "score": 1},
                    {"max": 30, "score": 2},
                    {"max": 90, "score": 3},
                    {"max": 180, "score": 4},
                    {"max": 9999, "score": 5},
                ],
                "type_boost": {
                    "structured": 2, "hedge_fund": 2,
                    "private_equity": 2, "real_estate": 1,
                },
                "default_score": 2,
            }),
        },
        {
            "factor_key": "credit_quality",
            "factor_label": "Credit Quality",
            "weight": 0.10,
            "ordinal": 5,
            "config_json": json.dumps({
                "type": "keyword",
                "field": "credit_rating",
                "patterns": {
                    "aaa": 1, "aa+": 1, "aa": 1, "aa-": 1,
                    "a+": 2, "a": 2, "a-": 2,
                    "bbb+": 3, "bbb": 3, "bbb-": 3,
                    "bb+": 4, "bb": 4, "bb-": 4,
                    "b+": 5, "b": 5, "b-": 5,
                    "ccc": 5, "cc": 5, "c": 5, "d": 5,
                    "investment grade": 2, "high yield": 4,
                    "sovereign": 2, "government": 1,
                },
                "fund_type_fallbacks": {
                    "money_market": 1, "short_term_bond": 2,
                    "bond": 2, "equity": 3,
                    "high_yield_bond": 4,
                },
                "default_score": 3,
            }),
        },
        {
            "factor_key": "currency_country",
            "factor_label": "Currency / Country Risk",
            "weight": 0.10,
            "ordinal": 6,
            "config_json": json.dumps({
                "type": "lookup",
                "field": "domicile",
                "score_map": {
                    "hong kong": 1, "singapore": 2,
                    "luxembourg": 2, "ireland": 2,
                    "uk": 2, "united kingdom": 2,
                    "switzerland": 2, "germany": 2,
                    "france": 2, "netherlands": 2,
                    "usa": 2, "us": 2, "japan": 2,
                    "australia": 2, "canada": 2,
                    "china": 3, "india": 3, "brazil": 3,
                    "emerging": 4, "frontier": 5, "offshore": 3,
                },
                "keyword_adjustments": {
                    "china": 3, "emerging": 4, "frontier": 5,
                    "asia": 3, "latin america": 4,
                    "russia": 5, "turkey": 5, "vietnam": 4,
                },
                "default_score": 3,
            }),
        },
    ],
}


# ═══════════════════════════════════════════════════════════════════
#  Template A2: Broker 13-Factor Scorecard
# ═══════════════════════════════════════════════════════════════════

BROKER_13F_TEMPLATE: Dict[str, Any] = {
    "name": "Broker 13-Factor Scorecard (v1.0)",
    "description": (
        "13-factor scorecard from broker template. Three categories: "
        "Fund House 30% (Manager Size 16%, Internal Control 14%), "
        "Fund Fundamental 40% (Fund Size 5%, Expense Ratio 3%, "
        "Portfolio Managers 7%, Fund Type 15%, Concentration 10%), "
        "Performance 30% (Sharpe 7%, StdDev 6%, MaxDD 5%, "
        "Sharpe vs BM 5%, StdDev vs BM 4%, MaxDD vs BM 3%). "
        "Rating scale: R1 (<1.8), R2 (1.8-2.3), R3 (2.3-2.9), "
        "R4 (2.9-3.3), R5 (>3.3)."
    ),
    "template_type": "fund_risk",
    "is_system": True,
    "methodology_version": "1.0",
    "category_thresholds_json": json.dumps([
        {"max": 1.8, "label": "R1"},
        {"max": 2.3, "label": "R2"},
        {"max": 2.9, "label": "R3"},
        {"max": 3.3, "label": "R4"},
        {"max": 99.0, "label": "R5"},
    ]),
    "factors": [
        # ── Fund House (30%) ──
        {
            "factor_key": "manager_size",
            "factor_label": "Manager Size (AUM)",
            "weight": 0.16,
            "ordinal": 1,
            "config_json": json.dumps({
                "type": "manager_aum_range",
                "bands": [
                    {"min": 1e12, "score": 1},       # >$1T
                    {"min": 100e9, "score": 2},      # >$100B
                    {"min": 10e9, "score": 3},       # >$10B
                    {"min": 1e9, "score": 4},        # >$1B
                    {"min": 0, "score": 5},           # <$1B
                ],
                "default_score": 3,
            }),
        },
        {
            "factor_key": "internal_control",
            "factor_label": "Internal Control",
            "weight": 0.14,
            "ordinal": 2,
            "config_json": json.dumps({
                "type": "manager_dd_score",
                "score_map": {
                    "Strong": 1, "Sufficient": 2,
                    "Average": 3, "Limited": 4, "Lacking": 5,
                },
                "default_score": 3,
            }),
        },
        # ── Fund Fundamental (40%) ──
        {
            "factor_key": "fund_size",
            "factor_label": "Fund Size",
            "weight": 0.05,
            "ordinal": 3,
            "config_json": json.dumps({
                "type": "range",
                "field": "aum",
                "bands": [
                    {"min": 1e9, "score": 1},         # >$1B
                    {"min": 100e6, "score": 2},       # >$100M
                    {"min": 10e6, "score": 3},        # >$10M
                    {"min": 1e6, "score": 4},         # >$1M
                    {"min": 0, "score": 5},
                ],
                "default_score": 3,
            }),
        },
        {
            "factor_key": "expense_ratio",
            "factor_label": "Expense Ratio",
            "weight": 0.03,
            "ordinal": 4,
            "config_json": json.dumps({
                "type": "range",
                "field": "expense_ratio_pct",
                "bands": [
                    {"max": 0.5, "score": 1},
                    {"max": 1.0, "score": 2},
                    {"max": 1.5, "score": 3},
                    {"max": 2.5, "score": 4},
                    {"max": 99, "score": 5},
                ],
                "fallback_field": "management_fee_pct",
                "default_score": 3,
            }),
        },
        {
            "factor_key": "portfolio_managers",
            "factor_label": "Portfolio Managers",
            "weight": 0.07,
            "ordinal": 5,
            "config_json": json.dumps({
                "type": "has_portfolio_manager",
                "field": "portfolio_manager_name",
                "has_value_score": 2,    # Has named PM = good
                "no_value_score": 5,     # No named PM = poor
                "default_score": 4,
            }),
        },
        {
            "factor_key": "fund_type",
            "factor_label": "Fund Type / Asset Class",
            "weight": 0.15,
            "ordinal": 6,
            "config_json": json.dumps({
                "type": "lookup",
                "field": "fund_type",
                "score_map": {
                    "money_market": 1, "short_term_bond": 1,
                    "bond": 2, "fixed_income": 2,
                    "balanced": 3, "mixed_asset": 3,
                    "equity": 4, "sector_equity": 5,
                    "commodity": 5, "crypto": 5,
                },
                "default_score": 3,
            }),
        },
        {
            "factor_key": "concentration",
            "factor_label": "Concentration Risk",
            "weight": 0.10,
            "ordinal": 7,
            "config_json": json.dumps({
                "type": "keyword",
                "field": "fund_name_en",
                "patterns": {
                    "diversified": 1, "global": 2, "broad": 2,
                    "sector": 4, "single country": 5, "thematic": 4,
                    "concentrated": 5, "focused": 4,
                    "china": 4, "emerging": 4,
                },
                "default_score": 3,
            }),
        },
        # ── Performance (30%) ──
        {
            "factor_key": "sharpe_ratio",
            "factor_label": "Sharpe Ratio (3Y)",
            "weight": 0.07,
            "ordinal": 8,
            "config_json": json.dumps({
                "type": "range",
                "field": "sharpe_ratio_3y",
                "bands": [
                    {"min": 1.5, "score": 1},
                    {"min": 1.0, "score": 2},
                    {"min": 0.5, "score": 3},
                    {"min": 0.0, "score": 4},
                    {"min": -99, "score": 5},
                ],
                "default_score": 3,
            }),
        },
        {
            "factor_key": "std_dev",
            "factor_label": "Standard Deviation (3Y)",
            "weight": 0.06,
            "ordinal": 9,
            "config_json": json.dumps({
                "type": "range",
                "field": "std_dev_3y",
                "bands": [
                    {"max": 5, "score": 1},
                    {"max": 10, "score": 2},
                    {"max": 15, "score": 3},
                    {"max": 25, "score": 4},
                    {"max": 999, "score": 5},
                ],
                "default_score": 3,
            }),
        },
        {
            "factor_key": "max_drawdown",
            "factor_label": "Max Drawdown (3Y)",
            "weight": 0.05,
            "ordinal": 10,
            "config_json": json.dumps({
                "type": "range",
                "field": "max_drawdown_pct",
                "bands": [
                    {"max": 10, "score": 1},
                    {"max": 20, "score": 2},
                    {"max": 30, "score": 3},
                    {"max": 50, "score": 4},
                    {"max": 999, "score": 5},
                ],
                "default_score": 3,
            }),
        },
        {
            "factor_key": "sharpe_vs_bm",
            "factor_label": "Sharpe vs Benchmark",
            "weight": 0.05,
            "ordinal": 11,
            "config_json": json.dumps({
                "type": "benchmark_diff",
                "metric": "sharpe_ratio_3y",
                "bands": [
                    {"min_diff": 0.5, "score": 1},
                    {"min_diff": 0.0, "score": 2},
                    {"min_diff": -0.5, "score": 3},
                    {"min_diff": -1.0, "score": 4},
                    {"min_diff": -999, "score": 5},
                ],
                "default_score": 3,
            }),
        },
        {
            "factor_key": "stddev_vs_bm",
            "factor_label": "StdDev vs Benchmark",
            "weight": 0.04,
            "ordinal": 12,
            "config_json": json.dumps({
                "type": "benchmark_diff",
                "metric": "std_dev_3y",
                "bands": [
                    {"max_diff": -5, "score": 1},
                    {"max_diff": 0, "score": 2},
                    {"max_diff": 5, "score": 3},
                    {"max_diff": 15, "score": 4},
                    {"max_diff": 999, "score": 5},
                ],
                "default_score": 3,
            }),
        },
        {
            "factor_key": "maxdd_vs_bm",
            "factor_label": "MaxDD vs Benchmark",
            "weight": 0.03,
            "ordinal": 13,
            "config_json": json.dumps({
                "type": "benchmark_diff",
                "metric": "max_drawdown_pct",
                "bands": [
                    {"max_diff": -5, "score": 1},
                    {"max_diff": 0, "score": 2},
                    {"max_diff": 10, "score": 3},
                    {"max_diff": 20, "score": 4},
                    {"max_diff": 999, "score": 5},
                ],
                "default_score": 3,
            }),
        },
    ],
}


# ═══════════════════════════════════════════════════════════════════
#  Template B1: 10-Dimension DD Manager Score
# ═══════════════════════════════════════════════════════════════════

DD_10D_TEMPLATE: Dict[str, Any] = {
    "name": "10-Dimension DD (v1.0)",
    "description": (
        "Standard 10-dimension due diligence pass-count mapping to "
        "5-tier Internal Control rating: 9-10 pass → Strong, "
        "7-8 → Sufficient, 5-6 → Average, 3-4 → Limited, 0-2 → Lacking. "
        "Matches the built-in manager_scoring.py logic."
    ),
    "template_type": "manager_dd",
    "is_system": True,
    "methodology_version": "1.0",
    "category_thresholds_json": json.dumps([
        {"max": 1, "label": "Strong"},
        {"max": 2, "label": "Sufficient"},
        {"max": 3, "label": "Average"},
        {"max": 4, "label": "Limited"},
        {"max": 5, "label": "Lacking"},
    ]),
    # Manager DD config is at template level (not per-factor)
    "dd_config_json": json.dumps({
        "dimensions": [
            "financial_resources", "human_resources", "internal_controls",
            "risk_governance", "segregation_duties", "compliance_function",
            "audit_function", "custodian_dd", "valuer_dd", "delegates_monitoring",
        ],
        "pass_score": 3,
        "pass_statuses": ["reviewed", "approved"],
        "tiers": [
            [9, 10, "Strong", 1],
            [7, 8, "Sufficient", 2],
            [5, 6, "Average", 3],
            [3, 4, "Limited", 4],
            [0, 2, "Lacking", 5],
        ],
    }),
}


# ═══════════════════════════════════════════════════════════════════
#  Template B2: Broker 18-Indicator Manager DD
# ═══════════════════════════════════════════════════════════════════

BROKER_18I_TEMPLATE: Dict[str, Any] = {
    "name": "Broker 18-Indicator DD (v1.0)",
    "description": (
        "18-indicator manager due diligence from broker template. "
        "Weighted scoring → F1-F5 classification. Indicators: "
        "成立年限 5%, 股东实力 5%, 实收资本 2%, 监管处罚 8%, "
        "员工人数 3%, 基金经理年限 5%, 内部治理 2%, 风控体系 8%, "
        "投研实力 5%, 管理规模 12%, 规模增长率 5%, 盈利产品比例 10%, "
        "管理层变动 5%, 累计基金数 5%, 机构声誉 8%, 合作渠道 7%, "
        "投资者构成 3%, 投资者服务 2%."
    ),
    "template_type": "manager_dd",
    "is_system": True,
    "methodology_version": "1.0",
    "category_thresholds_json": json.dumps([
        {"max": 1.5, "label": "F1"},
        {"max": 2.5, "label": "F2"},
        {"max": 3.5, "label": "F3"},
        {"max": 4.0, "label": "F4"},
        {"max": 5.0, "label": "F5"},
    ]),
    "factors": [
        {
            "factor_key": "establishment_years",
            "factor_label": "成立年限 (Years Since Establishment)",
            "weight": 0.05, "ordinal": 1,
            "config_json": json.dumps({
                "type": "range",
                "field": "establishment_years",
                "bands": [
                    {"min": 20, "score": 1}, {"min": 10, "score": 2},
                    {"min": 5, "score": 3}, {"min": 2, "score": 4}, {"min": 0, "score": 5},
                ],
                "source": "hk_fund_managers.inc_date",
                "default_score": 3,
            }),
        },
        {
            "factor_key": "shareholder_strength",
            "factor_label": "股东实力 (Shareholder Strength)",
            "weight": 0.05, "ordinal": 2,
            "config_json": json.dumps({
                "type": "dd_dimension",
                "dimension": "financial_resources",
                "score_map": {"5": 1, "4": 2, "3": 3, "2": 4, "1": 5},
                "default_score": 3,
            }),
        },
        {
            "factor_key": "paid_in_capital",
            "factor_label": "实收资本 (Paid-in Capital)",
            "weight": 0.02, "ordinal": 3,
            "config_json": json.dumps({
                "type": "manual", "source": "hk_fund_managers.shareholder_equity_hkd",
                "default_score": 3,
            }),
        },
        {
            "factor_key": "regulatory_penalties",
            "factor_label": "监管处罚 (Regulatory Penalties)",
            "weight": 0.08, "ordinal": 4,
            "config_json": json.dumps({
                "type": "inverse_boolean",
                "source": "hk_manager_regulatory_history",
                "has_history_score": 5, "no_history_score": 1,
                "default_score": 3,
            }),
        },
        {
            "factor_key": "employee_count",
            "factor_label": "员工人数 (Employee Count)",
            "weight": 0.03, "ordinal": 5,
            "config_json": json.dumps({
                "type": "manual", "source": "hk_fund_managers.employee_count",
                "default_score": 3,
            }),
        },
        {
            "factor_key": "fund_manager_years",
            "factor_label": "基金经理年限 (Fund Manager Years)",
            "weight": 0.05, "ordinal": 6,
            "config_json": json.dumps({
                "type": "manager_years", "source": "hk_fund_managers.license_effective_date",
                "default_score": 3,
            }),
        },
        {
            "factor_key": "internal_governance",
            "factor_label": "内部治理 (Internal Governance)",
            "weight": 0.02, "ordinal": 7,
            "config_json": json.dumps({
                "type": "dd_dimension", "dimension": "internal_controls",
                "score_map": {"5": 1, "4": 2, "3": 3, "2": 4, "1": 5},
                "default_score": 3,
            }),
        },
        {
            "factor_key": "risk_control_system",
            "factor_label": "风控体系 (Risk Control System)",
            "weight": 0.08, "ordinal": 8,
            "config_json": json.dumps({
                "type": "dd_dimension", "dimension": "risk_governance",
                "score_map": {"5": 1, "4": 2, "3": 3, "2": 4, "1": 5},
                "default_score": 3,
            }),
        },
        {
            "factor_key": "investment_research",
            "factor_label": "投研实力 (Investment Research)",
            "weight": 0.05, "ordinal": 9,
            "config_json": json.dumps({
                "type": "dd_dimension", "dimension": "human_resources",
                "score_map": {"5": 1, "4": 2, "3": 3, "2": 4, "1": 5},
                "default_score": 3,
            }),
        },
        {
            "factor_key": "management_scale",
            "factor_label": "管理规模 (Management Scale AUM)",
            "weight": 0.12, "ordinal": 10,
            "config_json": json.dumps({
                "type": "manager_aum_range",
                "bands": [
                    {"min": 1000000000000, "score": 1}, {"min": 100000000000, "score": 2},
                    {"min": 10000000000, "score": 3}, {"min": 1000000000, "score": 4},
                    {"min": 0, "score": 5},
                ],
                "default_score": 3,
            }),
        },
        {
            "factor_key": "scale_growth",
            "factor_label": "规模增长率 (AUM Growth Rate)",
            "weight": 0.05, "ordinal": 11,
            "config_json": json.dumps({
                "type": "scale_growth", "source": "hk_funds.authorization_date",
                "default_score": 3,
            }),
        },
        {
            "factor_key": "profitable_product_ratio",
            "factor_label": "盈利产品比例 (Profitable Product Ratio)",
            "weight": 0.10, "ordinal": 12,
            "config_json": json.dumps({
                "type": "profitable_product_ratio", "source": "hk_fund_performance",
                "default_score": 3,
            }),
        },
        {
            "factor_key": "management_changes",
            "factor_label": "管理层变动 (Management Changes)",
            "weight": 0.05, "ordinal": 13,
            "config_json": json.dumps({
                "type": "range",
                "field": "name_changes_per_year",
                "bands": [
                    {"max": 0.02, "score": 1},
                    {"min": 0.02, "max": 0.05, "score": 2},
                    {"min": 0.05, "max": 0.10, "score": 3},
                    {"min": 0.10, "max": 0.25, "score": 4},
                    {"min": 0.25, "score": 5},
                ],
                "source": "hk_fund_managers.name_history_count",
                "default_score": 3,
            }),
        },
        {
            "factor_key": "cumulative_fund_count",
            "factor_label": "累计基金数 (Cumulative Fund Count)",
            "weight": 0.05, "ordinal": 14,
            "config_json": json.dumps({
                "type": "range",
                "bands": [
                    {"min": 50, "score": 1}, {"min": 20, "score": 2},
                    {"min": 10, "score": 3}, {"min": 3, "score": 4}, {"min": 0, "score": 5},
                ],
                "source": "hk_funds.count_per_manager",
                "default_score": 3,
            }),
        },
        {
            "factor_key": "institutional_reputation",
            "factor_label": "机构声誉 (Institutional Reputation)",
            "weight": 0.08, "ordinal": 15,
            "config_json": json.dumps({
                "type": "range",
                "field": "wiki_presence_score",
                "bands": [
                    {"min": 2, "score": 2},
                    {"min": 1, "score": 3},
                    {"min": 0, "score": 4},
                ],
                "source": "hk_fund_managers.wiki_en_title, hk_fund_managers.wiki_zh_title",
                "default_score": 3,
            }),
        },
        {
            "factor_key": "distribution_channels",
            "factor_label": "合作渠道 (Distribution Channels)",
            "weight": 0.07, "ordinal": 16,
            "config_json": json.dumps({
                "type": "manual", "source": "manual_only",
                "default_score": 3,
            }),
        },
        {
            "factor_key": "investor_composition",
            "factor_label": "投资者构成 (Investor Composition)",
            "weight": 0.03, "ordinal": 17,
            "config_json": json.dumps({
                "type": "manual", "source": "manual_only",
                "default_score": 3,
            }),
        },
        {
            "factor_key": "investor_services",
            "factor_label": "投资者服务 (Investor Services)",
            "weight": 0.02, "ordinal": 18,
            "config_json": json.dumps({
                "type": "manual", "source": "manual_only",
                "default_score": 3,
            }),
        },
    ],
}


# ═══════════════════════════════════════════════════════════════════
#  Standard template registry
# ═══════════════════════════════════════════════════════════════════

ALL_STANDARD_TEMPLATES: List[Dict[str, Any]] = [
    SFC_6F_TEMPLATE,
    BROKER_13F_TEMPLATE,
    DD_10D_TEMPLATE,
    BROKER_18I_TEMPLATE,
]


def ensure_standard_templates(conn) -> int:
    """Ensure all standard templates exist. Idempotent.

    Returns number of templates inserted (0 if all already exist).
    """
    from hk_funds.storage import (
        get_system_templates,
        upsert_template,
        upsert_template_factors,
    )

    existing_df = get_system_templates(conn)
    existing_names = set(existing_df["name"].tolist()) if len(existing_df) > 0 else set()
    inserted = 0

    for tmpl in ALL_STANDARD_TEMPLATES:
        if tmpl["name"] in existing_names:
            continue

        factors = tmpl.pop("factors", [])
        dd_config = tmpl.pop("dd_config_json", None)

        template_id = upsert_template(conn, tmpl)

        if factors:
            upsert_template_factors(conn, template_id, factors)

        # Store DD config as a special factor for manager_dd templates
        if dd_config and tmpl["template_type"] == "manager_dd":
            upsert_template_factors(conn, template_id, [{
                "factor_key": "_dd_config",
                "factor_label": "DD Configuration",
                "weight": 1.0,
                "ordinal": 0,
                "config_json": dd_config,
            }])

        tmpl["factors"] = factors
        if dd_config:
            tmpl["dd_config_json"] = dd_config
        existing_names.add(tmpl["name"])
        inserted += 1
        logger.info(f"Created standard template: {tmpl['name']} (id={template_id})")

    return inserted
