"""
Multi-source ISIN resolution for SFC-authorized funds.

ISIN Data Sources (investigated 2026-06-22):
  Source                    Coverage        Status
  ------                    --------        ------
  HKEX ListOfSecurities     ETFs only       IMPLEMENTED (54 ISINs)
  SFC UTMF HTML search      None            No ISIN field in SFC data
  SFC KFS documents         None            ISINs not in extractable text
  SFC Offering Documents    None            ISINs not in PDF metadata/text
  ESMA FIRDS (FULINS)       No LU file      Luxembourg ISINs not covered
  CSSF Luxembourg           No download     Official list not accessible
  ALFI fund centre          No download     Not accessible
  Morningstar HK            202 response     Requires JS/API key
  FE fundinfo / Trustnet    200 HTML        SPA, requires browser
  FundSuperMart HK          200 short       Block page
  BlackRock API             503              Bot-protected
  OpenFIGI (Bloomberg)      Partial         API available but mainly equities
  Manager websites          Full            Requires per-manager scrapers

Strategy:
  1. ETFs: HKEX ListOfSecurities.xlsx (done)
  2. Top managers: Per-manager UTMF connectors scraping fund listings
  3. Long tail: Name-based ISIN lookup on aggregator platforms
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger("hk_funds.isin_sources")

# ── ISIN validation ──────────────────────────────────────────

ISIN_PATTERN = re.compile(r'\b([A-Z]{2}[A-Z0-9]{9}\d)\b')


def validate_isin(isin: str) -> bool:
    """Validate ISIN checksum using the Luhn-like ISIN algorithm."""
    if not isin or len(isin) != 12:
        return False
    if not ISIN_PATTERN.match(isin):
        return False

    # Convert letters to numbers (A=10, B=11, ..., Z=35)
    digits = ""
    for ch in isin.upper():
        if ch.isalpha():
            digits += str(ord(ch) - 55)
        else:
            digits += ch

    # Luhn algorithm (double every other digit from right)
    total = 0
    for i, d in enumerate(reversed(digits)):
        n = int(d)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n

    return total % 10 == 0


def normalize_isin(isin: str) -> str:
    """Normalize ISIN to uppercase, no whitespace."""
    return isin.strip().upper()


# ── ISIN source registry ─────────────────────────────────────

class ISINSource:
    """Base class for an ISIN data source."""

    name: str = "base"
    description: str = ""

    def get_isins(self) -> List[Dict[str, str]]:
        """
        Return list of {fund_name, isin, [manager_name, ...]} dicts.
        """
        raise NotImplementedError

    def import_to_db(self, conn) -> Dict[str, int]:
        """Import ISINs from this source into the database."""
        records = self.get_isins()
        stats = {"found": len(records), "updated": 0, "skipped": 0, "not_matched": 0}

        for rec in records:
            isin = rec.get("isin", "").strip().upper()
            if not isin or not validate_isin(isin):
                continue

            fund_name = rec.get("fund_name", "").strip()
            manager_name = rec.get("manager_name", "").strip()

            # Try exact ISIN lookup first
            row = conn.execute(
                "SELECT id FROM hk_funds WHERE isin = ?",
                [isin]
            ).fetchone()

            if row:
                # ISIN already in DB, verify fund name
                existing = conn.execute(
                    "SELECT fund_name_en FROM hk_funds WHERE id = ?",
                    [row[0]]
                ).fetchone()
                if existing and existing[0] != fund_name:
                    # Same ISIN, different name — skip (likely correct)
                    stats["skipped"] += 1
                    continue

            # Try name matching
            if fund_name:
                # Try different match strategies
                fund_id = _find_fund_by_name(conn, fund_name, manager_name)
                if fund_id:
                    existing_isin = conn.execute(
                        "SELECT isin FROM hk_funds WHERE id = ?",
                        [fund_id]
                    ).fetchone()

                    if not existing_isin or not existing_isin[0]:
                        conn.execute(
                            "UPDATE hk_funds SET isin = ? WHERE id = ?",
                            [isin, fund_id]
                        )
                        stats["updated"] += 1
                    else:
                        stats["skipped"] += 1
                else:
                    stats["not_matched"] += 1

        return stats


def _find_fund_by_name(conn, fund_name: str, manager_name: str = "") -> Optional[int]:
    """Find a fund in hk_funds by name, with optional manager filter."""
    import re as _re

    # Normalize
    norm = lambda s: _re.sub(r'[^A-Z0-9]', '', s.upper())

    norm_name = norm(fund_name)

    query = "SELECT id, fund_name_en FROM hk_funds WHERE fund_name_en IS NOT NULL"
    rows = conn.execute(query).fetchall()

    for fid, db_name in rows:
        if not db_name:
            continue
        norm_db = norm(db_name)

        # Exact normalized match
        if norm_name == norm_db:
            return fid

        # Substring match
        if len(norm_name) > 8 and norm_name in norm_db:
            return fid

        # Token match
        tokens = [t for t in fund_name.upper().split() if len(t) >= 3]
        if tokens and all(t in db_name.upper() for t in tokens):
            return fid

    return None
