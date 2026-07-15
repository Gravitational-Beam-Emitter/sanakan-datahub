"""
BNP Paribas Asset Management connector.

BNP Paribas AM HK hosts its fund explorer at www.bnpparibas-am.com.
The page calls a public REST API at api.bnpparibas-am.com/push/sharesearchv2/
which returns all fund share classes with ISINs, NAVs, and metadata.

Strategy:
  1. Fetch the share search API directly (no auth required)
  2. Extract ISINs, NAVs, fund names from the JSON
  3. Deduplicate by compart_name (one ISIN per base fund)
  4. Match fund names to hk_funds via case-insensitive matching

CE: AQU766 — BNP Paribas ASSET MANAGEMENT Luxembourg
Also handles: AXA World Funds (managed by BNP Paribas AM Lux)
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

from hk_funds.manager_connectors.base import BaseManagerConnector, register_connector

logger = logging.getLogger("hk_funds.manager_connectors.bnp_paribas")

API_URL = "https://api.bnpparibas-am.com/push/sharesearchv2/IP_HK-FSE/ENG"


@register_connector
class BNPParibasConnector(BaseManagerConnector):
    """Extracts fund data from BNP Paribas AM's public share search API."""

    manager_ce_numbers = ["AQU766"]
    base_url = "https://www.bnpparibas-am.com"

    request_delay: float = 1.0
    request_timeout: int = 30

    _BNP_MANAGER_SQL = (
        "AND (LOWER(fund_manager_name_en) LIKE '%bnp%'"
        "  OR LOWER(fund_manager_name_en) LIKE '%paribas%')"
    )

    # ── Fund List ───────────────────────────────────────────────

    def get_fund_list(self) -> List[Dict[str, Any]]:
        """Fetch all funds from the BNP share search API."""
        try:
            resp = requests.get(
                API_URL,
                timeout=self.request_timeout,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error(f"BNP: API fetch failed: {e}")
            return []

        shares = data.get("shares", [])
        logger.info(f"BNP: API returned {len(shares)} share classes")

        funds = self._extract_fund_data(shares)
        logger.info(f"BNP: extracted {len(funds)} unique funds")
        return funds

    def _extract_fund_data(self, shares: List[Dict]) -> List[Dict[str, Any]]:
        """Extract clean fund data from API share list.

        Deduplicates by compart_name — keeps first ISIN per base fund.
        """
        results = []
        seen_compartments: set = set()

        for share in shares:
            if not isinstance(share, dict):
                continue

            compart_name = (share.get("compart_name") or "").strip()
            isin = (share.get("isin_code") or "").strip()

            if not compart_name or not isin:
                continue

            # Deduplicate by compartment ID
            compart_id = share.get("compartment_id") or share.get("compart_id") or compart_name
            if compart_id in seen_compartments:
                continue
            seen_compartments.add(compart_id)

            # NAV
            nav_val = None
            nav_date = None
            nav_obj = share.get("nav")
            if isinstance(nav_obj, dict):
                try:
                    nav_val = float(nav_obj.get("value")) if nav_obj.get("value") is not None else None
                except (ValueError, TypeError):
                    pass
                nav_date_str = nav_obj.get("date")
                if nav_date_str:
                    nav_date = self._parse_date(nav_date_str)

            # AUM
            aum = None
            aum_obj = share.get("aum_comp")
            if isinstance(aum_obj, dict):
                try:
                    aum = float(aum_obj.get("value")) if aum_obj.get("value") is not None else None
                except (ValueError, TypeError):
                    pass

            currency = share.get("base_currency", "")
            val_currency = share.get("val_currency", "")
            currency_name = share.get("currency_name", "")

            # Fund inception
            first_date = share.get("first_date")

            # SRI risk
            srri = share.get("srri_risk")

            results.append({
                "fund_name": compart_name,
                "isin": isin,
                "legal_name": share.get("legal_name", ""),
                "currency": val_currency or currency,
                "currency_name": currency_name,
                "asset_class": share.get("asset_class", ""),
                "nav": nav_val,
                "nav_date": nav_date,
                "aum": aum,
                "first_date": first_date,
                "srri_risk": srri,
                "management_company": share.get("management_company", ""),
                "source_type": "manager_website",
            })

        return results

    # ── Name Matching ──────────────────────────────────────────

    def _match_fund_name(self, conn, extracted_name: str) -> Optional[int]:
        """Match BNP fund name to hk_funds.id.

        API names (ALL CAPS):
          - "BNP PARIBAS FUNDS CHINA EQUITY"
          - "AXA WF ASIAN SHORT DURATION BONDS"

        DB names (Mixed Case):
          - "BNP Paribas Funds China Equity"
          - "AXA World Funds - Asian Short Duration Bonds"
        """
        if not extracted_name:
            return None

        name = extracted_name.strip()

        # Build candidates
        candidates = [name]

        # AXA WF → AXA World Funds expansion
        axa_match = re.match(r"^AXA\s+WF\s+(.+)$", name)
        if axa_match:
            candidates.append(f"AXA World Funds - {axa_match.group(1)}")

        # Try title case for ALL-CAPS names
        candidates.append(name.title())

        # Also try stripping " BNP PARIBAS FUNDS " prefix
        for prefix in ["BNP PARIBAS FUNDS ", "BNP PARIBAS A FUND "]:
            if name.upper().startswith(prefix):
                stripped = name[len(prefix):]
                candidates.append(stripped)
                candidates.append(stripped.title())

        for candidate in candidates:
            c = re.sub(r"\s+", " ", candidate.lower().strip())

            # Exact match (case-insensitive)
            row = conn.execute(
                """SELECT id, fund_name_en FROM hk_funds
                   WHERE LOWER(fund_name_en) = ? AND is_active = true
                   LIMIT 1""",
                [c],
            ).fetchone()
            if row:
                return row[0]

            # LIKE both ways
            word_count = len(c.split())
            if word_count >= 2 and len(c) >= 10:
                row = conn.execute(
                    """SELECT id, fund_name_en FROM hk_funds
                       WHERE LOWER(fund_name_en) LIKE ? AND is_active = true
                       LIMIT 1""",
                    [f"%{c}%"],
                ).fetchone()
                if row:
                    return row[0]

                row = conn.execute(
                    """SELECT id, fund_name_en FROM hk_funds
                       WHERE ? LIKE '%' || LOWER(fund_name_en) || '%' AND is_active = true
                       LIMIT 1""",
                    [c],
                ).fetchone()
                if row:
                    return row[0]

            # Word-level matching
            keywords = [
                w for w in c.split()
                if len(w) > 2
                and w not in (
                    "fund", "funds", "class", "etf", "acc", "dis", "inc", "dist",
                    "global", "investment", "the", "and", "for"
                )
            ]
            if len(keywords) >= 2:
                conditions = " AND ".join(
                    ["LOWER(fund_name_en) LIKE ?" for _ in keywords]
                )
                params = [f"%{kw}%" for kw in keywords]
                row = conn.execute(
                    f"""SELECT id, fund_name_en FROM hk_funds
                       WHERE {conditions} AND is_active = true
                       LIMIT 1""",
                    params,
                ).fetchone()
                if row:
                    return row[0]

        return None

    # ── Main Interface ─────────────────────────────────────────

    def get_fund_details(self, isin: str) -> Optional[Dict[str, Any]]:
        """Not implemented for BNP."""
        return None

    def scrape_and_store(self, conn, date_str: str = None) -> Dict[str, int]:
        from hk_funds.storage import (
            update_fund_from_manager,
            upsert_nav_history,
        )

        today = date_str or datetime.now().strftime("%Y-%m-%d")
        stats = {
            "funds_found": 0, "matched": 0, "isins_updated": 0,
            "navs_stored": 0, "dividends_stored": 0, "details_updated": 0, "errors": 0,
        }

        try:
            funds = self.get_fund_list()
            stats["funds_found"] = len(funds)

            for idx, fund in enumerate(funds):
                fund_name = fund.get("fund_name", "")
                isin = fund.get("isin", "")

                if not fund_name or not isin:
                    continue

                hk_fund_id = self._match_fund_name(conn, fund_name)

                if not hk_fund_id:
                    if (idx + 1) % 10 == 0:
                        logger.info(
                            f"  [{idx + 1}/{len(funds)}] "
                            f"Matched={stats['matched']} (no match: {fund_name[:60]})"
                        )
                    continue

                stats["matched"] += 1

                # Build fund data
                data = {
                    "isin": isin,
                    "nav_currency": fund.get("currency"),
                    "source_type": "manager_website",
                }

                # Store fund details
                if update_fund_from_manager(conn, hk_fund_id, data):
                    stats["details_updated"] += 1

                # Set ISIN
                update_fund_from_manager(conn, hk_fund_id, {"isin": isin})
                stats["isins_updated"] += 1

                # Store NAV
                nav = fund.get("nav")
                nav_currency = fund.get("currency") or "USD"
                if nav:
                    nav_date = fund.get("nav_date") or today
                    n = upsert_nav_history(conn, hk_fund_id, [{
                        "nav": nav,
                        "nav_date": nav_date,
                        "nav_currency": nav_currency,
                        "source": "bnp_website",
                    }])
                    stats["navs_stored"] += n

                if (idx + 1) % 10 == 0:
                    logger.info(
                        f"  [{idx + 1}/{len(funds)}] "
                        f"Matched={stats['matched']} "
                        f"ISINs={stats['isins_updated']} "
                        f"NAVs={stats['navs_stored']}"
                    )

            logger.info(
                f"BNP Paribas scrape complete: "
                f"Matched={stats['matched']}/{stats['funds_found']}, "
                f"ISINs={stats['isins_updated']}, "
                f"NAVs={stats['navs_stored']}, "
                f"Details={stats['details_updated']}, "
                f"Errors={stats['errors']}"
            )

        except Exception as e:
            logger.error(f"BNP scrape failed: {e}")
            stats["errors"] += 1

        return stats
