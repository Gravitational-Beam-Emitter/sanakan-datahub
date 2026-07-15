"""
Capital Group (Capital International Management Company) connector.

Extracts fund ISINs and share class data from capitalgroup.com.
The site embeds ALL fund data as JSON in a single HTML page — no
Playwright needed, just HTTP GET + JSON parse.

Strategy:
  1. HTTP GET the fund centre page
  2. Extract data-attribute-fundata JSON from the HTML
  3. Parse 29 Luxembourg SICAV funds with 436+ share classes
  4. Extract ISINs, SEDOLs, Morningstar IDs for every share class
  5. Match to hk_funds by ISIN first, then by name with Capital Group constraint

CE: AOK434 — Capital International Management Company Limited
(SFC Type 9 license for Capital Group's HK entity)
"""

from __future__ import annotations

import html as html_mod
import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from hk_funds.manager_connectors.base import BaseManagerConnector, register_connector

logger = logging.getLogger("hk_funds.manager_connectors.capital_group")

FUND_CENTRE_URL = (
    "https://www.capitalgroup.com/individual-investors/hk/en/"
    "investments/fund-centre.html"
)

# Map common currency codes
CURRENCY_MAP = {
    "USD": "USD", "HKD": "HKD", "EUR": "EUR", "GBP": "GBP",
    "CHF": "CHF", "JPY": "JPY", "SGD": "SGD", "AUD": "AUD",
    "CNH": "CNH", "CNY": "CNH", "RMB": "CNH",
    "CAD": "CAD", "NZD": "NZD", "SEK": "SEK", "NOK": "NOK",
    "DKK": "DKK", "ZAR": "ZAR", "PLN": "PLN", "CZK": "CZK",
    "HUF": "HUF", "ILS": "ILS", "MXN": "MXN", "BRL": "BRL",
    "INR": "INR", "KRW": "KRW", "TWD": "TWD", "THB": "THB",
}


@register_connector
class CapitalGroupConnector(BaseManagerConnector):
    """Extracts fund ISIN/share class data from Capital Group HK website."""

    manager_ce_numbers = ["AOK434"]
    base_url = "https://www.capitalgroup.com"

    request_delay: float = 0.5
    request_timeout: int = 30

    _CG_MANAGER_SQL = (
        "AND (LOWER(fund_manager_name_en) LIKE '%capital international%'"
        " OR LOWER(fund_manager_name_en) LIKE '%capital group%'"
        " OR LOWER(fund_manager_name_en) LIKE '%capital research%')"
    )

    def _extract_fundata_json(self) -> Optional[Dict[str, Any]]:
        """Fetch the fund centre page and extract the embedded JSON."""
        try:
            resp = self._get(FUND_CENTRE_URL)
        except Exception as e:
            logger.error(f"Capital Group: failed to fetch fund centre: {e}")
            return None

        text = resp.text

        # Find the data-attribute-fundata block
        start = text.find('data-attribute-fundata="')
        if start < 0:
            logger.error("Capital Group: data-attribute-fundata not found")
            return None

        start += len('data-attribute-fundata="')

        # Find the matching closing quote
        end = start
        while end < len(text) and text[end] != '"':
            end += 1

        raw = text[start:end]
        raw = html_mod.unescape(raw)

        try:
            data = json.loads(raw)
            logger.info(
                f"Capital Group: parsed {len(data.get('funds', []))} funds"
            )
            return data
        except json.JSONDecodeError as e:
            logger.error(f"Capital Group: JSON parse error: {e}")
            return None

    def get_fund_list(self) -> List[Dict[str, Any]]:
        """Extract all Capital Group funds with ISINs and share classes."""
        data = self._extract_fundata_json()
        if not data:
            return []

        funds = []
        for fund_data in data.get("funds", []):
            fund_name = fund_data.get("name", "").strip()
            if not fund_name:
                continue

            share_classes = []
            isins = []
            seen_isins = set()

            for sc in fund_data.get("shareClasses", []):
                sc_name = sc.get("name", "").strip()

                for cur in sc.get("currencies", []):
                    isin = cur.get("isin", "").strip()
                    currency = cur.get("name", "").strip().upper()
                    sedol = cur.get("sedol", "").strip()
                    morningstar = cur.get("morningstar", "").strip()

                    if not isin:
                        continue

                    normalized_ccy = CURRENCY_MAP.get(currency, currency)

                    # Build share class display name
                    display_name = f"Class {sc_name} {normalized_ccy}"
                    if cur.get("description"):
                        display_name += f" ({cur['description']})"

                    share_classes.append({
                        "share_class_name": display_name,
                        "isin": isin,
                        "currency": normalized_ccy,
                        "sedol": sedol,
                        "morningstar_id": morningstar,
                    })

                    if isin not in seen_isins:
                        seen_isins.add(isin)
                        isins.append(isin)

            # Build fund-level metadata
            result = {
                "fund_name": fund_name,
                "product_url": (
                    f"https://www.capitalgroup.com/individual-investors/hk/en/"
                    f"investments/fund-centre.html"
                ),
                "source_type": "manager_website",
                "isins": isins,
                "share_classes": share_classes,
                "nav_entries": [],
                "base_currency": "USD",
                "fund_manager_name_en": (
                    "Capital International Management Company Limited"
                ),
                "domicile": "Luxembourg",
                "fund_type": fund_data.get("fundType", "SICAV"),
            }

            logger.info(
                f"Capital Group: {fund_name[:50]} — "
                f"{len(isins)} ISINs, {len(share_classes)} share classes"
            )
            funds.append(result)

        return funds

    # ── Name Matching ──────────────────────────────────────────

    def _match_fund_name(self, conn, extracted_name: str) -> Optional[int]:
        """Match Capital Group fund name to hk_funds.id.

        Website names: "Capital Group AMCAP Fund (LUX)"
        DB names may be: "Capital Group AMCAP Fund (LUX)" or
        "AMCAP Fund (LUX)" etc.
        """
        if not extracted_name:
            return None

        name = extracted_name.strip()
        candidates = [name]

        # Try without "Capital Group " prefix
        for prefix in ["Capital Group "]:
            if name.lower().startswith(prefix.lower()):
                stripped = name[len(prefix):]
                if stripped not in candidates:
                    candidates.append(stripped)

        # Try removing " (LUX)" suffix
        lux_removed = re.sub(r'\s*\(LUX\)\s*$', '', name, flags=re.IGNORECASE)
        if lux_removed not in candidates:
            candidates.append(lux_removed)

        for candidate in candidates:
            c = re.sub(r"\s+", " ", candidate.lower().strip())
            word_count = len(c.split())

            for query, params in [
                ("LOWER(fund_name_en) = ?", [c]),
                ("LOWER(fund_name_en) LIKE ?", [f"%{c}%"]),
            ]:
                if "LIKE" in query and word_count < 2 and len(c) < 10:
                    continue
                row = conn.execute(
                    f"""SELECT id, fund_name_en FROM hk_funds
                       WHERE {query} AND is_active = true
                       {self._CG_MANAGER_SQL}
                       LIMIT 1""",
                    params,
                ).fetchone()
                if row:
                    return row[0]

            # Keyword matching (extract meaningful words)
            keywords = [
                w for w in c.split()
                if len(w) > 2
                and w not in (
                    "fund", "class", "etf", "capital", "group",
                    "lux", "sicav", "luxembourg", "growth", "income",
                    "equity", "bond", "global", "european", "asian",
                    "market", "markets", "total", "core",
                )
            ]
            if len(keywords) >= 2:
                conditions = " AND ".join(
                    ["LOWER(fund_name_en) LIKE ?" for _ in keywords]
                )
                params_kw = [f"%{kw}%" for kw in keywords]
                row = conn.execute(
                    f"""SELECT id, fund_name_en FROM hk_funds
                       WHERE {conditions} AND is_active = true
                       {self._CG_MANAGER_SQL}
                       LIMIT 1""",
                    params_kw,
                ).fetchone()
                if row:
                    return row[0]

        return None

    # ── Main Interface ─────────────────────────────────────────

    def get_fund_details(self, isin: str) -> Optional[Dict[str, Any]]:
        """Not implemented."""
        return None

    def scrape_and_store(self, conn, date_str: str = None) -> Dict[str, int]:
        from hk_funds.storage import (
            update_fund_from_manager,
            upsert_share_classes,
        )

        today = date_str or datetime.now().strftime("%Y-%m-%d")
        stats = {
            "funds_found": 0, "matched": 0, "isins_updated": 0,
            "navs_stored": 0, "share_classes_stored": 0,
            "details_updated": 0, "errors": 0,
        }

        try:
            fund_details = self.get_fund_list()
            stats["funds_found"] = len(fund_details)

            for idx, detail in enumerate(fund_details):
                fund_name = detail.get("fund_name", "")
                isins = detail.get("isins", [])
                share_classes = detail.get("share_classes", [])
                base_ccy = detail.get("base_currency", "USD")

                if not fund_name:
                    stats["errors"] += 1
                    continue

                # Match by ISIN first
                hk_fund_id = None
                for isin in isins:
                    row = conn.execute(
                        "SELECT id FROM hk_funds WHERE isin = ?", [isin],
                    ).fetchone()
                    if row:
                        hk_fund_id = row[0]
                        break

                if not hk_fund_id:
                    for isin in isins:
                        row = conn.execute(
                            "SELECT fund_id FROM hk_fund_share_classes "
                            "WHERE isin = ?", [isin],
                        ).fetchone()
                        if row:
                            hk_fund_id = row[0]
                            break

                if not hk_fund_id:
                    hk_fund_id = self._match_fund_name(conn, fund_name)

                if not hk_fund_id:
                    logger.info(
                        f"  [{idx + 1}/{len(fund_details)}] "
                        f"no match: {fund_name[:60]}"
                    )
                    continue

                stats["matched"] += 1

                # Store fund details
                data = {
                    "fund_name": fund_name,
                    "product_url": detail.get("product_url", ""),
                    "source_type": "manager_website",
                    "fund_manager_name_en": detail.get(
                        "fund_manager_name_en", ""
                    ),
                    "currency": base_ccy,
                }
                for key in ("domicile", "fund_type"):
                    if detail.get(key):
                        data[key] = detail[key]

                if update_fund_from_manager(conn, hk_fund_id, data):
                    stats["details_updated"] += 1

                # Store ISINs and share classes
                for sc in share_classes:
                    sc_isin = sc.get("isin", "")
                    sc_data = {
                        "share_class_name": sc.get("share_class_name", ""),
                        "isin": sc_isin,
                        "currency": sc.get("currency", base_ccy),
                        "source": "capitalgroup_website",
                    }
                    upsert_share_classes(conn, hk_fund_id, [sc_data])
                    stats["share_classes_stored"] += 1

                    if sc_isin:
                        update_fund_from_manager(
                            conn, hk_fund_id, {"isin": sc_isin}
                        )
                        stats["isins_updated"] += 1

            logger.info(
                f"Capital Group scrape complete: "
                f"Matched={stats['matched']}/{stats['funds_found']}, "
                f"ISINs={stats['isins_updated']}, "
                f"ShareClasses={stats['share_classes_stored']}, "
                f"Details={stats['details_updated']}"
            )

        except Exception as e:
            logger.error(f"Capital Group scrape failed: {e}")
            stats["errors"] += 1

        return stats
