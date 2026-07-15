"""
abrdn (Aberdeen Investments) connector.

abrdn's HK investor site (www.aberdeeninvestments.com) provides a fund centre
that calls an internal REST API at /api/gateway/funds/overview which returns
all SFC-authorized funds with ISINs for each share class.

Strategy:
  1. POST to the funds/overview API with country=HKG, skip=0, take=100
  2. Extract ISINs, fund names from the JSON response
  3. Deduplicate by fund id (one ISIN per base fund)
  4. Match fund names to hk_funds via case-insensitive matching

CE: ARO687 — abrdn Hong Kong Limited
Also: abrdn Investments Luxembourg S.A. (AQY805)
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

from hk_funds.manager_connectors.base import BaseManagerConnector, register_connector

logger = logging.getLogger("hk_funds.manager_connectors.abrdn")

API_URL = "https://www.aberdeeninvestments.com/api/gateway/funds/overview"


@register_connector
class AbrdnConnector(BaseManagerConnector):
    """Extracts fund data from abrdn's fund overview API."""

    manager_ce_numbers = ["ARO687", "AQY805"]
    base_url = "https://www.aberdeeninvestments.com"

    request_delay: float = 1.0
    request_timeout: int = 30

    _ABRDN_MANAGER_SQL = (
        "AND (LOWER(fund_manager_name_en) LIKE '%abrdn%'"
        "  OR LOWER(fund_manager_name_en) LIKE '%aberdeen%')"
    )

    # ── Fund List ───────────────────────────────────────────────

    def get_fund_list(self) -> List[Dict[str, Any]]:
        """Fetch all funds from the abrdn overview API."""
        body = {
            "countryInvestors": {
                "countryCode": "HKG",
                "investorType": "4,1",
                "jurisdiction": "Live",
                "literatureAuthorization": "1",
            },
            "searchQuery": {"id": "", "name": "", "isin": ""},
            "language": "en-HK",
            "site": "Investor",
            "skip": 0,
            "take": 100,
            "filters": [],
            "tab": "overview",
        }

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.aberdeeninvestments.com/en-hk/investor/funds/view-all-funds",
        }

        try:
            resp = requests.post(
                API_URL,
                json=body,
                headers=headers,
                timeout=self.request_timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error(f"abrdn: API fetch failed: {e}")
            return []

        raw_funds = data.get("content", {}).get("overview", [])
        logger.info(f"abrdn: API returned {len(raw_funds)} funds")

        funds = self._extract_fund_data(raw_funds)
        logger.info(f"abrdn: extracted {len(funds)} unique funds")
        return funds

    def _extract_fund_data(self, raw_funds: List[Dict]) -> List[Dict[str, Any]]:
        """Extract clean fund data from API response."""
        results = []

        for fund in raw_funds:
            if not isinstance(fund, dict):
                continue

            fund_name = (fund.get("name") or "").strip()
            fund_id = fund.get("id", "")

            if not fund_name or not fund_id:
                continue

            shareclasses = fund.get("shareclasses") or []
            if not shareclasses:
                continue

            # Take first share class as the primary ISIN
            first_sc = shareclasses[0]
            isin = (first_sc.get("isin") or "").strip()

            if not isin:
                continue

            results.append({
                "fund_name": fund_name,
                "fund_id": fund_id,
                "isin": isin,
                "share_class_name": first_sc.get("shareclassName", ""),
                "source_type": "manager_website",
            })

        return results

    # ── Name Matching ──────────────────────────────────────────

    def _match_fund_name(self, conn, extracted_name: str) -> Optional[int]:
        """Match abrdn fund name to hk_funds.id.

        API names:
          - "abrdn SICAV I - All China Sustainable Equity Fund"
          - "abrdn Liquidity Fund (Lux) - US Dollar Fund"

        DB names are identical or very close (case may differ slightly).
        """
        if not extracted_name:
            return None

        name = extracted_name.strip()

        candidates = [name]

        # Normalize dash variants (en-dash vs hyphen)
        if "–" in name:
            candidates.append(name.replace("–", "-"))
        if "-" in name:
            candidates.append(name.replace("-", "–"))

        for candidate in candidates:
            c = re.sub(r"\s+", " ", candidate.lower().strip())

            # Exact match
            row = conn.execute(
                f"""SELECT id, fund_name_en FROM hk_funds
                   WHERE LOWER(fund_name_en) = ? AND is_active = true
                   {self._ABRDN_MANAGER_SQL}
                   LIMIT 1""",
                [c],
            ).fetchone()
            if row:
                return row[0]

            # LIKE both ways
            word_count = len(c.split())
            if word_count >= 2 and len(c) >= 10:
                row = conn.execute(
                    f"""SELECT id, fund_name_en FROM hk_funds
                       WHERE LOWER(fund_name_en) LIKE ? AND is_active = true
                       {self._ABRDN_MANAGER_SQL}
                       LIMIT 1""",
                    [f"%{c}%"],
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
                       {self._ABRDN_MANAGER_SQL}
                       LIMIT 1""",
                    params,
                ).fetchone()
                if row:
                    return row[0]

        return None

    # ── Main Interface ─────────────────────────────────────────

    def get_fund_details(self, isin: str) -> Optional[Dict[str, Any]]:
        """Not implemented for abrdn."""
        return None

    def scrape_and_store(self, conn, date_str: str = None) -> Dict[str, int]:
        from hk_funds.storage import update_fund_from_manager

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

                data = {
                    "isin": isin,
                    "source_type": "manager_website",
                }

                if update_fund_from_manager(conn, hk_fund_id, data):
                    stats["details_updated"] += 1

                update_fund_from_manager(conn, hk_fund_id, {"isin": isin})
                stats["isins_updated"] += 1

            logger.info(
                f"abrdn scrape complete: "
                f"Matched={stats['matched']}/{stats['funds_found']}, "
                f"ISINs={stats['isins_updated']}, "
                f"Errors={stats['errors']}"
            )

        except Exception as e:
            logger.error(f"abrdn scrape failed: {e}")
            stats["errors"] += 1

        return stats
