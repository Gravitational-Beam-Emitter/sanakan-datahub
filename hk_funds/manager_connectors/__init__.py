"""
Manager website connectors — one per fund manager/family.

Each connector scrapes a specific fund manager's website to extract
fund ISINs, NAVs, fees, benchmarks, and performance data.

Usage:
    from hk_funds.manager_connectors import get_connector_for_manager

    conn = init_db()
    connector = get_connector_for_manager("AVL789")  # CSOP
    if connector:
        stats = connector.scrape_and_store(conn)
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Type

from hk_funds.manager_connectors.base import (
    BaseManagerConnector,
    MANAGER_WEBSITES,
    get_connector_registry,
)

logger = logging.getLogger("hk_funds.manager_connectors")


def get_connector_for_manager(ce_number: str) -> Optional[BaseManagerConnector]:
    """Get an instantiated connector for a manager by CE number.

    Returns None if no connector is registered for this CE number.
    """
    registry = get_connector_registry()
    cls = registry.get(ce_number)
    if cls is None:
        return None
    return cls()


def get_all_registered_ce_numbers() -> List[str]:
    """Return all CE numbers that have a registered connector."""
    return sorted(get_connector_registry().keys())


def get_website_for_manager(ce_number: str) -> Optional[str]:
    """Get known website URL for a manager by CE number."""
    return MANAGER_WEBSITES.get(ce_number)


def list_managers_needing_connectors(conn, min_funds: int = 10) -> List[Dict]:
    """Return managers with funds but no connector, ordered by fund count.

    This helps prioritize which connectors to build next.
    """
    registered = set(get_connector_registry().keys())
    rows = conn.execute("""
        SELECT
            m.ce_number,
            m.company_name_en,
            m.website,
            COUNT(DISTINCT fmf.fund_id) AS fund_count
        FROM hk_fund_managers m
        JOIN hk_fund_manager_funds fmf ON m.id = fmf.manager_id
        WHERE m.ce_number IS NOT NULL
        GROUP BY m.ce_number, m.company_name_en, m.website
        HAVING COUNT(DISTINCT fmf.fund_id) >= ?
        ORDER BY fund_count DESC
    """, [min_funds]).fetchall()

    return [
        {
            "ce_number": r[0],
            "company_name_en": r[1],
            "website": r[2],
            "fund_count": r[3],
            "has_connector": r[0] in registered,
        }
        for r in rows
    ]


# Import connectors to trigger registration
def _discover_connectors():
    """Import all connector modules to populate the registry."""
    import importlib
    import pkgutil

    package = __name__
    for _, module_name, _ in pkgutil.iter_modules([__path__[0]]):
        if module_name == "base":
            continue
        try:
            importlib.import_module(f".{module_name}", package)
        except Exception as e:
            logger.warning(f"Failed to load connector {module_name}: {e}")


_discover_connectors()
