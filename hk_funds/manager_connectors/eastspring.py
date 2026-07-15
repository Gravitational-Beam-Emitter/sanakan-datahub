"""
Eastspring Investments connector.

Eastspring's HK fund listing page (www.eastspring.com/hk/en/funds) is a
server-rendered HTML page that contains fund data in an HTML table with
ISINs embedded in `<span class="d-none">` elements and fund names in
adjacent `<a>` tags.

Strategy:
  1. GET the fund listing page (simple HTTP request, no auth)
  2. Extract ISINs, fund names from the HTML table rows
  3. Strip share class suffixes to get base fund names
  4. Deduplicate by base name + ISIN
  5. Match fund names to hk_funds via case-insensitive matching

CE: Eastspring Investments (Luxembourg) S.A. — fund manager in hk_funds
    Eastspring Investments (Hong Kong) Limited — AFO909 (HK entity)
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

from hk_funds.manager_connectors.base import BaseManagerConnector, register_connector

logger = logging.getLogger("hk_funds.manager_connectors.eastspring")

FUND_LIST_URL = "https://www.eastspring.com/hk/en/funds"


@register_connector
class EastspringConnector(BaseManagerConnector):
    """Extracts fund data from Eastspring's fund listing HTML page."""

    manager_ce_numbers = ["AFO909"]  # Eastspring Investments (Hong Kong) Limited
    base_url = "https://www.eastspring.com"

    request_delay: float = 1.0
    request_timeout: int = 30

    _EASTSPRING_MANAGER_SQL = (
        "AND (LOWER(fund_manager_name_en) LIKE '%eastspring%')"
    )

    # ── Fund List ───────────────────────────────────────────────

    def get_fund_list(self) -> List[Dict[str, Any]]:
        """Fetch all funds from the Eastspring fund listing page."""
        try:
            resp = requests.get(
                FUND_LIST_URL,
                timeout=self.request_timeout,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            resp.raise_for_status()
            html = resp.text
        except Exception as e:
            logger.error(f"Eastspring: page fetch failed: {e}")
            return []

        funds = self._extract_fund_data(html)
        logger.info(f"Eastspring: extracted {len(funds)} unique funds")
        return funds

    def _extract_fund_data(self, html: str) -> List[Dict[str, Any]]:
        """Extract fund data from the HTML table.

        Each fund row contains:
          <tr>
            <td><span class="d-none">LU0149982760</span></td>
            <td><a href="/hk/funds/.../ISIN">Fund Name</a></td>
            ...
          </tr>

        Share classes (e.g. "Fund Name - A", "Fund Name - ADM") are
        deduplicated — only the first ISIN per base fund name is kept.
        """
        import html as _html

        results = []
        seen_bases: set = set()

        # Find all rows containing ISIN spans
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL)

        for row in rows:
            # Must contain an ISIN span
            isin_match = re.search(
                r'<span[^>]*class="d-none"[^>]*>([A-Z]{2}[A-Z0-9]{10})</span>',
                row,
            )
            if not isin_match:
                continue
            isin = isin_match.group(1).strip()

            # Find the fund link
            link_match = re.search(
                r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', row
            )
            if not link_match:
                continue

            fund_url = link_match.group(1)
            raw_name = _html.unescape(
                re.sub(r'<[^>]+>', '', link_match.group(2))
            ).strip()

            if not raw_name:
                continue

            # Strip share class suffix to get base fund name
            # Patterns: "Fund Name - A", "Fund Name - ADM", "Fund Name - ADQ"
            base_name = re.sub(
                r'\s+[-–]\s*[A-Z]{1,4}\s*(?:\([^)]*\))?\s*$',
                '',
                raw_name,
            ).strip()

            # Also strip trailing currency suffixes
            base_name = re.sub(
                r'\s+[-–]\s*(?:HKD|USD|EUR|GBP|JPY|SGD|AUD|CNH|RMB)\s*$',
                '',
                base_name,
                flags=re.IGNORECASE,
            ).strip()

            # Deduplicate by base name
            base_key = base_name.lower()
            if base_key in seen_bases:
                continue
            seen_bases.add(base_key)

            # Build full product URL
            product_url = fund_url
            if fund_url and not fund_url.startswith("http"):
                product_url = "https://www.eastspring.com" + fund_url

            results.append({
                "fund_name": base_name,
                "isin": isin,
                "share_class_name": raw_name,
                "product_url": product_url,
                "source_type": "manager_website",
            })

        return results

    # ── Name Matching ──────────────────────────────────────────

    def _match_fund_name(self, conn, extracted_name: str) -> Optional[int]:
        """Match Eastspring fund name to hk_funds.id.

        Eastspring API names follow patterns like:
          - "Eastspring Investments - Asian Bond Fund"
          - "Eastspring Investments - US High Investment Grade Bond Fund"

        SFC register names are similar:
          - "Eastspring Investments - Asian Bond Fund"
          - "Eastspring Investments - US High Investment Grade Bond Fund"
        """
        if not extracted_name:
            return None

        name = extracted_name.strip()
        candidates = [name]

        # Normalize dash variants
        if "–" in name:
            candidates.append(name.replace("–", "-"))
        if "-" in name:
            candidates.append(name.replace("-", "–"))

        # Add "Fund" suffix if missing
        for i in range(len(candidates)):
            if not candidates[i].lower().endswith(" fund"):
                candidates.append(candidates[i] + " Fund")

        for candidate in candidates:
            c = re.sub(r"\s+", " ", candidate.lower().strip())

            # Exact match
            row = conn.execute(
                f"""SELECT id, fund_name_en FROM hk_funds
                   WHERE LOWER(fund_name_en) = ? AND is_active = true
                   {self._EASTSPRING_MANAGER_SQL}
                   LIMIT 1""",
                [c],
            ).fetchone()
            if row:
                return row[0]

            # LIKE match
            word_count = len(c.split())
            if word_count >= 2 and len(c) >= 10:
                row = conn.execute(
                    f"""SELECT id, fund_name_en FROM hk_funds
                       WHERE LOWER(fund_name_en) LIKE ? AND is_active = true
                       {self._EASTSPRING_MANAGER_SQL}
                       LIMIT 1""",
                    [f"%{c}%"],
                ).fetchone()
                if row:
                    return row[0]

                row = conn.execute(
                    f"""SELECT id, fund_name_en FROM hk_funds
                       WHERE ? LIKE '%' || LOWER(fund_name_en) || '%' AND is_active = true
                       {self._EASTSPRING_MANAGER_SQL}
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
                    "fund", "class", "etf", "acc", "dis", "inc", "dist",
                    "global", "investment", "funds", "the", "and", "for"
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
                       {self._EASTSPRING_MANAGER_SQL}
                       LIMIT 1""",
                    params,
                ).fetchone()
                if row:
                    return row[0]

        return None

    # ── Main Interface ─────────────────────────────────────────

    def get_fund_details(self, isin: str) -> Optional[Dict[str, Any]]:
        """Not implemented — Eastspring detail pages are SPA (JS-rendered)."""
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
                    "fund_name": fund_name,
                    "isin": isin,
                    "product_url": fund.get("product_url"),
                    "source_type": "manager_website",
                }

                if update_fund_from_manager(conn, hk_fund_id, data):
                    stats["details_updated"] += 1

                update_fund_from_manager(conn, hk_fund_id, {"isin": isin})
                stats["isins_updated"] += 1

            logger.info(
                f"Eastspring scrape complete: "
                f"Matched={stats['matched']}/{stats['funds_found']}, "
                f"ISINs={stats['isins_updated']}, "
                f"Errors={stats['errors']}"
            )

        except Exception as e:
            logger.error(f"Eastspring scrape failed: {e}")
            stats["errors"] += 1

        return stats
