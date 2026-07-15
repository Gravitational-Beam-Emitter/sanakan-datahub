"""
Schroders connector.

Extracts fund data from Schroders' micro-frontend SSR HTML. The fund centre
page is a Next.js SPA that renders fund data server-side via a Vike micro-frontend
hosted at body.nextjs.schdr.eu-central-1.isgdigital.com.

Strategy:
  1. Fetch SSR HTML from the fund search/filter page (50 funds per page for HK)
  2. Parse Chakra UI HTML structure for fund names, ISINs, NAVs
  3. Match funds to hk_funds by name with Schroders manager constraint

CE: Not yet confirmed for HK entity. Using manager constraint-based matching.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

from hk_funds.manager_connectors.base import BaseManagerConnector, register_connector

logger = logging.getLogger("hk_funds.manager_connectors.schroders")

SSR_URL = (
    "https://body.nextjs.schdr.eu-central-1.isgdigital.com"
    "/en/hk/individual/gfc/fund/search/filter/"
    "?prerender=true"
    "&baseUrl=https%3A%2F%2Fwww.schroders.com%2Fen-hk%2Fhk%2Findividual%2Ffund-centre%2F"
    "&pageSize=100"
)


@register_connector
class SchrodersConnector(BaseManagerConnector):
    """Extracts fund data from Schroders SSR fund centre HTML."""

    manager_ce_numbers = ["ACJ591"]
    base_url = "https://www.schroders.com"

    request_delay: float = 2.0
    request_timeout: int = 60

    _SCHRODERS_MANAGER_SQL = (
        "AND (LOWER(fund_manager_name_en) LIKE '%schroder%'"
        " OR LOWER(fund_manager_name_en) LIKE '%schroders%')"
    )

    def __init__(self):
        super().__init__()

    # ── Fund List ───────────────────────────────────────────────

    def get_fund_list(self) -> List[Dict[str, Any]]:
        """Fetch and parse the SSR fund centre HTML for all available fund data."""
        resp = self._get(SSR_URL)
        html = resp.text
        return self._parse_ssr_html(html)

    def _parse_ssr_html(self, html: str) -> List[Dict[str, Any]]:
        """Parse Chakra UI SSR HTML to extract fund data.

        HTML structure per fund:
          <h2 class="chakra-heading heading_name">[FUND NAME]</h2>
          ...<p>ISIN</p><p class="...1ooc7no...">[ISIN]</p>...
          <h2 class="chakra-heading heading_priceEndValue">
            <span class="currency-code">[CCY]</span> [NAV]
          </h2>
          ...<p>Date</p>...<time class="timestamp">[DATE]</time>...
        """
        # Split by fund name headings
        fund_blocks = re.split(
            r'<h2 class="chakra-heading heading_name[^"]*">', html
        )

        funds = []
        for block in fund_blocks[1:]:  # skip preamble
            # Fund name
            name_match = re.match(r'([^<]+)</h2>', block)
            if not name_match:
                continue
            name = name_match.group(1).strip()

            # ISIN
            isin_match = re.search(
                r'<p[^>]*>ISIN</p>.*?<p[^>]*class="[^"]*1ooc7no[^"]*"[^>]*>'
                r'([A-Z]{2}[A-Z0-9]{10})</p>',
                block, re.DOTALL,
            )
            isin = isin_match.group(1) if isin_match else ""

            # Share class launch date
            launch_match = re.search(
                r'Share class launch date</p>.*?<time[^>]*>([^<]+)</time>',
                block, re.DOTALL,
            )
            launch_date = launch_match.group(1) if launch_match else ""

            # NAV: <span class="currency-code">CCY</span> VALUE
            nav_match = re.search(
                r'heading_priceEndValue[^>]*>.*?'
                r'<span class="currency-code">([A-Z]{3})</span>\s*'
                r'([\d,]+\.?\d*)',
                block, re.DOTALL,
            )
            currency = nav_match.group(1) if nav_match else ""
            nav = nav_match.group(2) if nav_match else ""

            # NAV Date
            date_match = re.search(
                r'<p[^>]*>Date</p>.*?<time[^>]*>([^<]+)</time>',
                block, re.DOTALL,
            )
            nav_date = date_match.group(1) if date_match else ""

            fund = {
                "fund_name": name,
                "isin": isin,
                "share_class_launch_date": launch_date,
                "nav_currency": currency,
                "nav": nav.replace(",", "") if nav else "",
                "nav_date": nav_date,
            }
            funds.append(fund)

        logger.info(f"Schroders: parsed {len(funds)} funds from SSR HTML")
        return funds

    # ── Name Matching ──────────────────────────────────────────

    def _match_fund_name(self, conn, extracted_name: str) -> Optional[int]:
        """Match Schroders fund name to hk_funds.id.

        Schroders names follow patterns like:
          - "Schroder Asian Asset Income Fund A Accumulation HKD"
          - "Schroder International Selection Fund All China Equity A Acc USD"
          - "Schroder ISF Global Gold A Accumulation USD"
          - "Schroder Global Multi-Asset Thematic Fund Class A USD Acc"

        SFC register names are typically the bare fund name without umbrella prefix.
        """
        if not extracted_name:
            return None

        name = extracted_name.strip()

        # Strip share class suffix patterns (from right to left)
        # "A Accumulation HKD", "C Distribution USD", etc.
        name = re.sub(
            r'\s+[A-I]\s+(Accumulation|Distribution|Acc|Dist)\s+[A-Z]{3}(\s+Hedged)?$',
            '', name, flags=re.IGNORECASE,
        )
        # "Class A USD Acc", "Class C EUR Dist", etc.
        name = re.sub(
            r'\s+Class\s+[A-I]\s+[A-Z]{3}\s+(Acc|Dist|Inc)$',
            '', name, flags=re.IGNORECASE,
        )
        # Trailing share class abbreviations: "A Acc USD", "C Inc EUR", etc.
        name = re.sub(
            r'\s+[A-I]\s+(Acc|Dist|Inc)\s+[A-Z]{3}$',
            '', name, flags=re.IGNORECASE,
        )
        # Trailing currency only
        name = re.sub(r'\s+[A-Z]{3}$', '', name)

        # Strip Schroders umbrella prefixes (ordered longest-first)
        prefixes = [
            "Schroder International Selection Fund ",
            "Schroders International Selection Fund ",
            "Schroder ISF ",
            "Schroders ISF ",
            "Schroder ",
            "Schroders ",
        ]
        candidates = [name]
        for prefix in prefixes:
            if name.lower().startswith(prefix.lower()):
                candidates.append(name[len(prefix):])

        for candidate in candidates:
            c = re.sub(r"\s+", " ", candidate.lower().strip())
            c = re.sub(r"\s+(fund|class\s+\w+|etf)\s*$", "", c, flags=re.IGNORECASE)
            # Normalize commas in parentheticals for matching (SSR may omit commas)
            c_normalized = re.sub(r"\(([^)]+)\)", lambda m: "(" + m.group(1).replace(",", "") + ")", c)

            word_count = len(c.split())

            for query, params in [
                ("LOWER(fund_name_en) = ?", [c]),
                # LIKE matching: only if candidate is multi-word (avoids "growth" → "Multi-Asset Growth")
                ("LOWER(fund_name_en) LIKE ?", [f"%{c}%"]),
                # Reverse: SSR name contains the SFC fund name (only useful for longer candidates)
                ("? LIKE '%' || LOWER(fund_name_en) || '%'", [c]),
            ]:
                # Skip LIKE patterns if candidate is a single short word
                if "LIKE" in query and word_count < 2 and len(c) < 10:
                    continue
                if "? LIKE" in query and word_count < 2:
                    continue
                row = conn.execute(
                    f"""SELECT id, fund_name_en FROM hk_funds
                       WHERE {query} AND is_active = true
                       {self._SCHRODERS_MANAGER_SQL}
                       LIMIT 1""",
                    params,
                ).fetchone()
                if row:
                    return row[0]

            # Also try with comma-normalized version
            if c_normalized != c:
                for query, params in [
                    ("LOWER(fund_name_en) = ?", [c_normalized]),
                    ("? LIKE '%' || LOWER(fund_name_en) || '%'", [c_normalized]),
                ]:
                    if "? LIKE" in query and word_count < 2:
                        continue
                    row = conn.execute(
                        f"""SELECT id, fund_name_en FROM hk_funds
                           WHERE {query} AND is_active = true
                           {self._SCHRODERS_MANAGER_SQL}
                           LIMIT 1""",
                        params,
                    ).fetchone()
                    if row:
                        return row[0]

            # Word-level matching as last resort
            keywords = [w for w in c.split() if len(w) > 3
                       and w not in ("fund", "class", "etf", "accumulation", "distribution",
                                     "international", "selection", "acc", "dist", "inc")]
            if len(keywords) >= 2:
                conditions = " AND ".join(
                    ["LOWER(fund_name_en) LIKE ?" for _ in keywords]
                )
                params = [f"%{kw}%" for kw in keywords]
                row = conn.execute(
                    f"""SELECT id, fund_name_en FROM hk_funds
                       WHERE {conditions} AND is_active = true
                       {self._SCHRODERS_MANAGER_SQL}
                       LIMIT 1""",
                    params,
                ).fetchone()
                if row:
                    return row[0]

        return None

    # ── Main Interface ─────────────────────────────────────────

    def get_fund_details(self, isin: str) -> Optional[Dict[str, Any]]:
        """Not implemented — Schroders SSR page doesn't have a public fund detail API."""
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
                nav_str = fund.get("nav", "")
                nav_date_str = fund.get("nav_date", "")
                currency = fund.get("nav_currency", "USD")

                if not fund_name:
                    stats["errors"] += 1
                    continue

                # Match to SFC register
                hk_fund_id = self._match_fund_name(conn, fund_name)

                if not hk_fund_id:
                    if (idx + 1) % 10 == 0:
                        logger.info(
                            f"  [{idx + 1}/{len(funds)}] "
                            f"Matched={stats['matched']} (no match: {fund_name[:60]})"
                        )
                    continue

                stats["matched"] += 1

                # Build data dict
                data = {
                    "fund_name": fund_name,
                    "isin": isin,
                    "nav_currency": currency,
                    "product_url": (
                        "https://www.schroders.com/en-hk/hk/individual/fund-centre/"
                    ),
                    "source_type": "manager_website",
                }

                # Parse share class launch date
                launch = fund.get("share_class_launch_date", "")
                if launch:
                    parsed = self._parse_date(launch)
                    if parsed:
                        data["share_class_inception_date"] = parsed

                # Store fund details
                if update_fund_from_manager(conn, hk_fund_id, data):
                    stats["details_updated"] += 1

                # Set ISIN
                if isin:
                    update_fund_from_manager(conn, hk_fund_id, {"isin": isin})
                    stats["isins_updated"] += 1

                # Store NAV
                if nav_str and nav_date_str:
                    try:
                        nav_val = float(nav_str)
                        nav_date_parsed = self._parse_date(nav_date_str)
                        if nav_date_parsed:
                            n = upsert_nav_history(conn, hk_fund_id, [{
                                "nav": nav_val,
                                "nav_date": nav_date_parsed,
                                "nav_currency": currency,
                                "source": "schroders_website",
                            }])
                            stats["navs_stored"] += n
                    except ValueError:
                        pass

                if (idx + 1) % 10 == 0:
                    logger.info(
                        f"  [{idx + 1}/{len(funds)}] "
                        f"Matched={stats['matched']} "
                        f"ISINs={stats['isins_updated']} "
                        f"NAVs={stats['navs_stored']}"
                    )

            logger.info(
                f"Schroders scrape complete: "
                f"Matched={stats['matched']}/{stats['funds_found']}, "
                f"ISINs={stats['isins_updated']}, "
                f"NAVs={stats['navs_stored']}, "
                f"Details={stats['details_updated']}, "
                f"Errors={stats['errors']}"
            )

        except Exception as e:
            logger.error(f"Schroders scrape failed: {e}")
            stats["errors"] += 1

        return stats
