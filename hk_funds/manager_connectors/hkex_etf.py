"""
Generic HKEX ETF data connector.

Handles ALL HKEX-listed exchange-traded products (ETFs + L&I products).
Downloads the HKEX ListOfSecurities.xlsx and extracts ISINs for all
ETP issuers at once, rather than maintaining per-issuer ISIN maps.

This is the most efficient approach since:
  1. HKEX publishes all ETP data in a single XLSX file
  2. Individual manager websites are mostly SPAs with WAF protection
  3. The XLSX file includes ISINs for all counter shares (HKD, RMB, USD)

Each connector can then inherit from this to add manager-specific
NAV/performance scraping from their website.

Usage:
    connector = HKEXETFConnector()
    all_funds = connector.get_fund_list()  # All 400+ HKEX ETPs with ISINs
    csop_funds = connector.get_funds_by_issuer("CSOP")
"""

from __future__ import annotations

import io
import logging
import re
from typing import Any, Dict, List, Optional

import requests

from hk_funds.manager_connectors.base import BaseManagerConnector

logger = logging.getLogger("hk_funds.manager_connectors.hkex_etf")

HKEX_LIST_URL = (
    "https://www.hkex.com.hk/eng/services/trading/securities/"
    "securitieslists/ListOfSecurities.xlsx"
)

# Map stock code prefix patterns → issuer name
# Based on analysis of HKEX ListOfSecurities.xlsx
ISSUER_CODE_PATTERNS: Dict[str, str] = {
    r"^028[0-9]{2}$": "CSOP",           # 028xx range
    r"^02839$": "ChinaAMC",
    r"^030[0-9]{2}$": "Various",         # Mixed 030xx range
    r"^031[0-9]{2}$": "Various",         # Mixed 031xx range
    r"^034[0-9]{2}$": "Various",         # Mixed 034xx range
    r"^072[0-9]{2}$": "CSOP",            # CSOP L&I products
    r"^073[0-9]{2}$": "CSOP",            # CSOP inverse products
    r"^075[0-9]{2}$": "CSOP",            # CSOP inverse products
    r"^077[0-9]{2}$": "CSOP",            # CSOP leveraged products
}

# Issuer name keywords → likely issuer
# Order matters: longer/more specific matches first to avoid ambiguity
ISSUER_NAME_KEYWORDS: Dict[str, str] = {
    # CSOP — multiple ETF prefixes (028xx, 072xx, 073xx, 075xx, 077xx)
    "CSOP": "CSOP",
    "FL2": "CSOP",
    "XL2": "CSOP",
    "FI2": "CSOP",
    "XI2": "CSOP",
    "FI ": "CSOP",
    # ChinaAMC — CAM-prefixed products
    "CAMNASDAQ": "ChinaAMC",
    "CAMBTC": "ChinaAMC",
    "CAMHS": "ChinaAMC",
    "CAM ": "ChinaAMC",
    # Mirae Asset / Global X
    "GX ": "Mirae Asset / Global X",
    "GLOBAL X": "Mirae Asset / Global X",
    # BlackRock / iShares
    "ISHARES": "BlackRock / iShares",
    # Hang Seng IM
    "HSESG": "Hang Seng IM",
    "HSI": "Hang Seng IM",
    "HS ": "Hang Seng IM",
    # Bosera — catch both "BOSERA" (prefix) and "BOS " (with space)
    "BOSERA": "Bosera",
    "BOS ": "Bosera",
    # E Fund
    "EFUND": "E Fund",
    "E FUND": "E Fund",
    # Harvest Global
    "HGI ": "Harvest Global",
    "HARVEST": "Harvest Global",
    # Samsung AM
    "SAMSUNG": "Samsung AM",
    # Premia Partners
    "PREMIA": "Premia Partners",
    # State Street / SPDR
    "SPDR": "State Street / SPDR",
    # Amundi
    "AMUNDI": "Amundi",
    # Invesco
    "INVESCO": "Invesco",
    # ICBC
    "ICBC": "ICBC",
    # Value Partners
    "VALUEGOLD": "Value Partners",
    # Others
    "WISE": "W.I.S.E.",
    "MBC ": "MBC",
    "CG ": "CG",
    "PP ": "PP",
    "PA ": "PA",
    "ABF ": "ABF",
    "BOCGBACLIMATE": "BOCGBACLIMATE",
    "PANDO": "PANDO",
    "PHILLIP": "Phillip",
    "PING": "Ping An",
    "TRACKER": "Tracker Fund",
    "TRMSCIKOREA": "TRMSCI",
    "TRMSCITAIWAN": "TRMSCI",
    "HSCEI": "Hang Seng IM",
    "HSTECH": "Hang Seng IM",
    "HSCMS": "Hang Seng IM",
}


class HKEXETFConnector(BaseManagerConnector):
    """Generic connector for ALL HKEX-listed exchange-traded products.

    Downloads the official HKEX securities list and extracts:
      - ISIN codes
      - Stock codes
      - Fund names
      - ETF vs L&I classification

    This serves as the foundation for per-issuer connectors. Each issuer
    connector can inherit from this and add manager-specific NAV/performance
    scraping.
    """

    manager_ce_numbers = []  # Generic — not matched to a specific manager
    base_url = "https://www.hkex.com.hk"
    _cache: Optional[List[Dict]] = None

    def _download_hkex_list(self) -> List[Dict[str, Any]]:
        """Download and parse the HKEX List of Securities."""
        session = self.session
        logger.info("Downloading HKEX ListOfSecurities.xlsx...")
        resp = session.get(HKEX_LIST_URL, timeout=60)
        resp.raise_for_status()

        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(resp.content), data_only=True)
        sh = wb["ListOfSecurities"]

        securities = []
        for row in sh.iter_rows(min_row=4, values_only=True):
            if not row[0]:
                continue
            stock_code = str(row[0]).strip().zfill(5)
            name = str(row[1]).strip() if row[1] else ""
            category = str(row[2]).strip() if row[2] else ""
            sub_category = str(row[3]).strip() if row[3] else ""
            board_lot = str(row[4]).strip() if row[4] else ""
            isin = str(row[5]).strip() if row[5] else ""

            if not name or not isin or isin in ("None", "ISIN"):
                continue

            securities.append({
                "stock_code": stock_code,
                "name": name,
                "isin": isin,
                "category": category,
                "sub_category": sub_category,
                "board_lot": board_lot,
            })

        logger.info(f"Downloaded {len(securities)} HKEX-listed securities")
        return securities

    def _guess_issuer(self, name: str) -> str:
        """Guess the fund issuer from the ETF name."""
        name_upper = name.upper()
        for keyword, issuer in ISSUER_NAME_KEYWORDS.items():
            if keyword.upper() in name_upper:
                return issuer
        # Fallback: first word of the name
        first_word = name.split()[0] if name.split() else "Unknown"
        return first_word

    def get_fund_list(self, refresh: bool = False) -> List[Dict[str, Any]]:
        """Return all HKEX-listed exchange-traded products with ISINs.

        Caches the HKEX download in memory. Use refresh=True to re-download.
        """
        if self._cache is not None and not refresh:
            raw_list = self._cache
        else:
            raw_list = self._download_hkex_list()
            self._cache = raw_list

        # Filter to ETPs
        etps = []
        for s in raw_list:
            if "Exchange Traded" not in s.get("category", ""):
                continue
            name = s["name"]
            sub_cat = s["sub_category"]

            # Classify fund type
            if "Leveraged and Inverse" in sub_cat:
                fund_type = "leveraged_inverse_product"
            elif "Money Market" in name or "MM" in name.split():
                fund_type = "money_market_etf"
            elif any(tag in name.upper() for tag in ["BTC", "ETH", "BITCOIN", "ETHEREUM", "GOLD", "DIGI GOLD"]):
                fund_type = "digital_asset_etf"
            else:
                fund_type = "etf"

            issuer = self._guess_issuer(name)

            etps.append({
                "stock_code": s["stock_code"],
                "isin": s["isin"],
                "fund_name": name,
                "fund_type": fund_type,
                "sub_category": sub_cat,
                "board_lot": s["board_lot"],
                "currency": self._guess_currency(s["stock_code"]),
                "exchange": "HKEX",
                "source": "hkex_list",
                "issuer": issuer,
            })

        logger.info(f"Found {len(etps)} exchange-traded products")
        return etps

    def get_funds_by_issuer(self, issuer: str) -> List[Dict[str, Any]]:
        """Filter fund list by issuer name."""
        all_funds = self.get_fund_list()
        return [f for f in all_funds if f.get("issuer", "").upper() == issuer.upper()]

    def get_unique_issuers(self) -> List[str]:
        """Get all unique issuer names."""
        all_funds = self.get_fund_list()
        issuers = set(f.get("issuer", "Unknown") for f in all_funds)
        return sorted(issuers)

    def _guess_currency(self, stock_code: str) -> str:
        """Guess primary trading currency from stock code.

        HKEX convention:
          - 0xxxx: HKD primary
          - 8xxxx: RMB counter
          - 9xxxx: USD counter
        """
        lic = int(stock_code)
        if lic >= 90000:
            return "USD"
        elif lic >= 80000:
            return "CNH"
        else:
            return "HKD"

    def get_fund_nav(self, isin: str) -> Optional[Dict[str, Any]]:
        """HKEX list doesn't include NAVs."""
        return None

    def get_fund_details(self, isin: str) -> Optional[Dict[str, Any]]:
        """HKEX list doesn't include fund details."""
        return None

    # ── Database import ──────────────────────────────────────

    # HKEX issuer → DB manager name substring for scoping
    ISSUER_TO_DB_MANAGER: Dict[str, List[str]] = {
        "CSOP": ["CSOP Asset Management"],
        "Mirae Asset / Global X": ["Mirae Asset Global Investments"],
        "ChinaAMC": ["China Asset Management (Hong Kong)"],
        "Hang Seng IM": ["Hang Seng Investment Management"],
        "BlackRock / iShares": ["BlackRock Asset Management North Asia", "BlackRock (Luxembourg)"],
        "E Fund": ["E Fund Management (Hong Kong)"],
        "Bosera": ["Bosera Asset Management"],
        "Premia Partners": ["Premia Partners"],
        "Samsung AM": ["Samsung"],
        "Harvest Global": ["Harvest Global Investments"],
        "ICBC": ["ICBC Asset Management"],
        "Amundi": ["Amundi Hong Kong", "Amundi Luxembourg"],
        "State Street / SPDR": ["State Street"],
        "Value Partners": ["Value Partners Hong Kong", "Value Partners Limited"],
        "Invesco": ["Invesco Hong Kong", "INVESCO Management"],
        "Ping An": ["Ping An of China Asset Management"],
        "PP": ["Ping An of China Asset Management"],
        "PPKSA": ["Ping An of China Asset Management"],
        "PA": ["Premia Partners"],
        "CG": ["CG"],
        "ABF": ["ABF"],
        "BOCGBACLIMATE": ["BOCGBACLIMATE"],
        "PANDO": ["PANDO"],
        "Phillip": ["Phillip Capital Management"],
        "W.I.S.E.": ["W.I.S.E."],
        "MBC": ["CMBC Asset Management"],
        "Tracker Fund": ["HSBC", "State Street", "Hang Seng"],
        "TRMSCI": ["TRMSCI"],
        "FB": ["Fubon"],
        "F": ["F"],
        "A": ["A"],
        "X": ["X"],
        "TR": ["TR"],
        "VP": ["Value Partners"],
    }

    def import_isins_to_db(self, conn) -> Dict[str, int]:
        """Import HKEX ISINs into hk_funds by matching fund names.

        Uses issuer-scoped matching: each HKEX ETF is locked to its manager's
        funds. ETFs with unknown issuers are skipped rather than matched against
        the whole database to avoid false positives.

        Returns {matched, updated, skipped, unmatched_issuer, unmatched_name}
        """
        etps = self.get_fund_list()
        stats = {"matched": 0, "updated": 0, "skipped": 0,
                 "unmatched_issuer": 0, "unmatched_name": 0}

        # Get all DB funds with their manager
        fund_rows = conn.execute(
            "SELECT id, fund_name_en, isin, fund_manager_name_en FROM hk_funds"
        ).fetchall()

        # Group by manager for issuer-scoped matching
        import collections
        db_by_manager = collections.defaultdict(list)
        for fid, fname, existing_isin, mgr in fund_rows:
            if fname:
                db_by_manager[mgr or ""].append((fid, fname, existing_isin))

        for etp in etps:
            etp_name = etp["fund_name"]
            etp_issuer = etp.get("issuer", "")
            isin = etp["isin"]

            db_mgr_keys = self.ISSUER_TO_DB_MANAGER.get(etp_issuer)
            if not db_mgr_keys:
                stats["unmatched_issuer"] += 1
                continue

            candidate_funds = []
            for db_mgr, fund_list in db_by_manager.items():
                if any(key.lower() in db_mgr.lower() for key in db_mgr_keys):
                    candidate_funds.extend(fund_list)

            if not candidate_funds:
                stats["unmatched_issuer"] += 1
                continue

            matched = self._match_fund(etp_name, candidate_funds)

            if matched:
                fid, existing_isin = matched
                stats["matched"] += 1
                if not existing_isin:
                    conn.execute(
                        "UPDATE hk_funds SET isin = ? WHERE id = ?",
                        [isin, fid]
                    )
                    stats["updated"] += 1
                else:
                    stats["skipped"] += 1
            else:
                stats["unmatched_name"] += 1

        return stats

    def _match_fund(
        self, hkex_name: str, candidates: List[tuple]
    ) -> Optional[tuple]:
        """Match an HKEX ETF name against DB fund candidates.

        Tries, in order:
          1. Exact normalized match
          2. Substring match (normalized HKEX name inside normalized DB name)
          3. Token-based match (all meaningful HKEX tokens appear in DB name)
          4. Subsequence match (characters of normalized HKEX name appear in
             order within normalized DB name)

        Returns (fund_id, existing_isin) or None.
        """
        norm_hkex = self._normalize_name(hkex_name)
        hkex_upper = hkex_name.upper()

        # --- Pass 1: exact or substring on normalized names ---
        for fid, fname, existing_isin in candidates:
            norm_db = self._normalize_name(fname)
            if norm_hkex == norm_db:
                return (fid, existing_isin)
            if len(norm_hkex) > 6 and norm_hkex in norm_db:
                return (fid, existing_isin)

        # --- Pass 2: token-based match ---
        # Split the HKEX name into meaningful tokens (words)
        hkex_tokens = [
            t for t in re.split(r'[\s/]+', hkex_upper) if len(t) >= 2
        ]
        if hkex_tokens:
            for fid, fname, existing_isin in candidates:
                db_upper = fname.upper()
                if all(t in db_upper for t in hkex_tokens):
                    return (fid, existing_isin)

        # --- Pass 3: subsequence match ---
        # Check if characters of norm_hkex appear in order within norm_db.
        # Requires minimum 8 characters AND at least 25% length ratio.
        # At 20-25%, issuer-prefixed short names (EFUNDAIU) match unrelated
        # funds from the same manager via the common prefix + a few letters.
        if len(norm_hkex) >= 8:
            for fid, fname, existing_isin in candidates:
                norm_db = self._normalize_name(fname)
                ratio = len(norm_hkex) / max(len(norm_db), 1)
                if ratio >= 0.25 and self._is_subsequence(norm_hkex, norm_db):
                    return (fid, existing_isin)

        return None

    @staticmethod
    def _is_subsequence(short: str, long: str) -> bool:
        """Check if all characters of `short` appear in order within `long`."""
        si = 0
        for ch in long:
            if ch == short[si]:
                si += 1
                if si == len(short):
                    return True
        return False

    @staticmethod
    def _normalize_name(name: str) -> str:
        """Normalize fund name for comparison."""
        name = name.upper()
        name = re.sub(r'[^A-Z0-9]', '', name)
        return name
