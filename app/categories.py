"""
Data categories — three-tier classification for macro, country risk, and name screening.

Single source of truth for which source belongs to which category.
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, List


class DataCategory(str, Enum):
    MACRO = "macro"
    COUNTRY_RISK = "country_risk"
    NAME_SCREENING = "name_screening"


# ── Source → category mapping ──────────────────────────────────

SOURCE_CATEGORY: Dict[str, DataCategory] = {
    # Macroeconomic time-series (21 sources)
    "us":       DataCategory.MACRO,
    "cn":       DataCategory.MACRO,
    "hk":       DataCategory.MACRO,
    "global_":  DataCategory.MACRO,
    "jp":       DataCategory.MACRO,
    "euro":     DataCategory.MACRO,
    "uk":       DataCategory.MACRO,
    "de":       DataCategory.MACRO,
    "au":       DataCategory.MACRO,
    "ca":       DataCategory.MACRO,
    "ch":       DataCategory.MACRO,
    "bond":     DataCategory.MACRO,
    "futures":  DataCategory.MACRO,
    "shipping": DataCategory.MACRO,
    "banks":    DataCategory.MACRO,
    "alt":      DataCategory.MACRO,
    "llm":      DataCategory.MACRO,
    "defi":     DataCategory.MACRO,
    "energy":   DataCategory.MACRO,
    "ai":       DataCategory.MACRO,
    "ai_co":    DataCategory.MACRO,
    "cb":       DataCategory.MACRO,
    "optical":  DataCategory.MACRO,
    # Country risk ratings (time-series in indicators table)
    "aml":      DataCategory.COUNTRY_RISK,
    "sanctions": DataCategory.COUNTRY_RISK,
    # Name screening (entity records, separate database table)
    "name_screening": DataCategory.NAME_SCREENING,
}

CATEGORY_LABELS: Dict[DataCategory, str] = {
    DataCategory.MACRO: "宏观经济",
    DataCategory.COUNTRY_RISK: "国家风险评级",
    DataCategory.NAME_SCREENING: "名称筛查",
}

CATEGORY_LABELS_EN: Dict[DataCategory, str] = {
    DataCategory.MACRO: "Macroeconomic",
    DataCategory.COUNTRY_RISK: "Country Risk Ratings",
    DataCategory.NAME_SCREENING: "Name Screening",
}


def get_category(source: str) -> DataCategory:
    """Return the DataCategory for a given source key."""
    return SOURCE_CATEGORY.get(source, DataCategory.MACRO)


def sources_by_category(category: DataCategory) -> List[str]:
    """Return all source keys belonging to a category."""
    return sorted([k for k, v in SOURCE_CATEGORY.items() if v == category])


def category_label(category: DataCategory, en: bool = False) -> str:
    """Return human-readable label for a category."""
    return CATEGORY_LABELS_EN.get(category, category.value) if en else CATEGORY_LABELS.get(category, category.value)
