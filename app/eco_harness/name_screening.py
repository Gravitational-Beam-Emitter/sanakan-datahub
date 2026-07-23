"""
Name Screening Harness — OpenSanctions + GDELT + Chinese Court data.

Sources:
  OpenSanctions   — Bulk sanctions + PEP dataset (440K+ entities, daily)
  GDELT           — Global news search for negative news (on-demand)
  Alibaba Cloud   — Chinese court judgment/debtor data (on-demand, paid API)

Architecture:
  - OpenSanctions data is downloaded and loaded into DuckDB (name_screening table)
  - GDELT + Chinese court are searched on-demand (results cached)
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from io import BytesIO
from typing import Any, Dict, List, Optional

import pandas as pd
import requests

from app.name_matcher import NameMatcher

try:
    from app.storage import (
        init_db,
        upsert_screening_entry,
        search_screening_by_name,
        search_screening_fuzzy,
        upsert_news_cache,
        get_news_cache,
    )
    HAS_STORAGE = True
except ImportError:
    HAS_STORAGE = False

logger = logging.getLogger("eco_data.name_screening")

# OpenSanctions bulk data URL
OS_BULK_URL = "https://data.opensanctions.org/datasets/latest/default/entities.ftm.json"
OS_INDEX_URL = "https://data.opensanctions.org/datasets/latest/default/index.json"

# GDELT API
GDELT_API = "https://api.gdeltproject.org/api/v2/doc/doc"

# Sanctions-related topics to filter from OpenSanctions
SANCTION_TOPICS = {"sanction", "sanction.counter", "crime.financial"}
PEP_TOPIC = "role.pep"
RCA_TOPIC = "role.rca"  # Relative or Close Associate of PEP


class NameScreeningHarness:
    """Name screening — OpenSanctions preload + on-demand search."""

    def __init__(self, db_path: str | None = None):
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36 "
                "EcoData/1.0"
            ),
        })
        self._db_path = db_path
        self._matcher = NameMatcher()

    # ── OpenSanctions ──────────────────────────────────────────

    def load_opensanctions(self, max_entities: int = 0) -> dict:
        """Download OpenSanctions bulk NDJSON and load into name_screening table.

        max_entities: 0 = load all. Use for testing (e.g. 10000).
        Returns summary dict.
        """
        if not HAS_STORAGE:
            return {"error": "storage module not available"}

        logger.info("Downloading OpenSanctions bulk data...")
        resp = self._session.get(OS_BULK_URL, timeout=600, stream=True)
        resp.raise_for_status()

        conn = init_db(self._db_path)
        stats = {"downloaded": 0, "loaded": 0, "skipped": 0, "peps": 0, "sanctions": 0}

        # OpenSanctions is NDJSON: one JSON object per line
        for line in resp.iter_lines(decode_unicode=True):
            if not line:
                continue
            stats["downloaded"] += 1
            try:
                entity = json.loads(line)
            except json.JSONDecodeError:
                stats["skipped"] += 1
                continue

            if self._should_import(entity):
                self._import_entity(conn, entity)
                stats["loaded"] += 1
                if self._is_pep(entity):
                    stats["peps"] += 1
                if self._is_sanction(entity):
                    stats["sanctions"] += 1
            else:
                stats["skipped"] += 1

            if max_entities and stats["downloaded"] >= max_entities:
                break

        conn.close()
        logger.info(f"OpenSanctions load complete: {stats}")
        return stats

    # Datasets that indicate sanctions/AML relevance
    _SANCTION_DATASETS = {
        "us_ofac_sdn", "us_ofac_cons", "us_ofac_ns_mbs", "us_ofac_press_releases",
        "eu_fsf", "eu_journal_sanctions", "un_sc_sanctions",
        "uk_ofsi", "ua_nsdc_sanctions", "ua_war_sanctions",
        "ch_seco_sanctions", "be_fod_sanctions", "mc_fund_freezes",
        "us_trade_csl", "us_sam_exclusions", "us_bis_entity_list",
        "interpol_red_notices", "interpol_yellow_notices",
    }
    _PEP_DATASETS = {"wd_peps", "everypolitician", "ext_wikidata_peps"}

    def _should_import(self, entity: dict) -> bool:
        """Filter: import Person/Organization/Company/LegalEntity from sanctions or PEP datasets."""
        schema = entity.get("schema", "")
        if schema not in ("Person", "Organization", "Company", "LegalEntity"):
            return False

        datasets = set(entity.get("datasets", []))

        # Import if in PEP-related datasets
        if datasets & self._PEP_DATASETS:
            return True

        # Import if in sanctions-related datasets
        if datasets & self._SANCTION_DATASETS:
            return True

        # Also import if topics indicate PEP or sanctions
        topics = set(entity.get("topics", []))
        if PEP_TOPIC in topics or RCA_TOPIC in topics:
            return True
        if topics & SANCTION_TOPICS:
            return True

        return False

    @classmethod
    def _is_pep(cls, entity: dict) -> bool:
        datasets = set(entity.get("datasets", []))
        if datasets & cls._PEP_DATASETS:
            return True
        topics = entity.get("topics", [])
        return PEP_TOPIC in topics or RCA_TOPIC in topics

    @classmethod
    def _is_sanction(cls, entity: dict) -> bool:
        datasets = set(entity.get("datasets", []))
        if datasets & cls._SANCTION_DATASETS:
            return True
        return bool(set(entity.get("topics", [])) & SANCTION_TOPICS)

    def _import_entity(self, conn, entity: dict) -> None:
        """Parse an OpenSanctions entity and upsert into name_screening."""
        entity_id = entity.get("id", "")
        schema = entity.get("schema", "")
        properties = entity.get("properties", {})

        # Extract names (OpenSanctions stores names as list of [full_name, ...])
        names = properties.get("name", [])
        name_en = ""
        name_cn = ""

        for name in names:
            name_str = str(name).strip()
            if name_str:
                if NameMatcher.has_cn(name_str):
                    if not name_cn:
                        name_cn = name_str
                else:
                    if not name_en:
                        name_en = name_str

        # If we didn't find a separate Chinese name, check aliases
        aliases_raw = properties.get("alias", [])
        aliases = []
        for alias in aliases_raw:
            alias_str = str(alias).strip()
            if alias_str:
                aliases.append(alias_str)
                if not name_cn and NameMatcher.has_cn(alias_str):
                    name_cn = alias_str

        # Also check Wikidata labels for Chinese names
        if not name_cn:
            wd_labels = properties.get("wikidataLabel", [])
            for label in wd_labels:
                label_str = str(label).strip()
                if NameMatcher.has_cn(label_str):
                    name_cn = label_str
                    break

        # Normalize
        name_cn_norm = NameMatcher.normalize_cn(name_cn) if name_cn else ""
        name_pinyin = NameMatcher.to_romanization(name_cn_norm) if name_cn_norm else ""

        # Determine name type
        name_type = "individual" if schema == "Person" else "entity"

        # Risk category (check datasets first, then topics)
        datasets = set(entity.get("datasets", []))
        if datasets & self._SANCTION_DATASETS or (set(entity.get("topics", [])) & SANCTION_TOPICS):
            risk_category = "sanctions"
        elif datasets & self._PEP_DATASETS or PEP_TOPIC in entity.get("topics", []) or RCA_TOPIC in entity.get("topics", []):
            risk_category = "pep"
        else:
            risk_category = "other"

        # PEP level
        topics = entity.get("topics", [])
        pep_level = ""
        if RCA_TOPIC in topics:
            pep_level = "family"
        elif PEP_TOPIC in topics:
            pep_level = "national"

        # Countries
        countries_list = properties.get("country", [])
        countries = ", ".join(countries_list) if countries_list else ""

        # Addresses
        addrs = properties.get("address", [])
        addresses = " | ".join(str(a) for a in addrs) if addrs else ""

        # Programs / source datasets
        datasets = entity.get("datasets", [])
        programs = ", ".join(datasets) if datasets else ""

        # Source date
        source_date = ""
        first_seen = properties.get("firstSeenAt", [])
        if first_seen:
            source_date = str(first_seen[0])[:10]

        # Notes
        notes_list = properties.get("notes", [])
        notes = " | ".join(str(n) for n in notes_list) if notes_list else ""

        # Position for PEPs
        position_list = properties.get("position", [])
        if position_list:
            pos_str = ", ".join(str(p) for p in position_list)
            if notes:
                notes = f"Position: {pos_str} | {notes}"
            else:
                notes = f"Position: {pos_str}"

        entry = {
            "source": "opensanctions",
            "source_uid": entity_id,
            "name_en": name_en,
            "name_cn": name_cn,
            "name_cn_norm": name_cn_norm,
            "name_pinyin": name_pinyin,
            "name_type": name_type,
            "pep_level": pep_level,
            "risk_category": risk_category,
            "aliases": json.dumps(aliases, ensure_ascii=False) if aliases else "",
            "programs": programs,
            "countries": countries,
            "addresses": addresses,
            "source_date": source_date,
            "notes": notes,
        }

        try:
            upsert_screening_entry(conn, entry)
        except Exception:
            logger.warning(f"Failed to upsert entity {entity_id}", exc_info=True)

    def load_ofac_into_screening(self) -> int:
        """Import OFAC SDN data from SanctionsHarness into name_screening table."""
        from app.eco_harness.sanctions import SanctionsHarness

        if not HAS_STORAGE:
            return 0

        sh = SanctionsHarness()
        df = sh.ofac_sdn_list()
        if df.empty:
            logger.warning("OFAC SDN returned empty — cannot load into name_screening")
            return 0

        conn = init_db(self._db_path)
        count = 0
        for _, row in df.iterrows():
            name = str(row.get("name", ""))
            entry = {
                "source": "ofac_sdn",
                "source_uid": str(int(row.get("uid", 0))),
                "name_en": name,
                "name_cn": "",
                "name_cn_norm": "",
                "name_pinyin": "",
                "name_type": str(row.get("sdn_type", "Unknown")).lower(),
                "pep_level": "",
                "risk_category": "sanctions",
                "aliases": str(row.get("aliases", "")),
                "programs": str(row.get("programs", "")),
                "countries": str(row.get("countries", "")),
                "addresses": str(row.get("addresses", "")),
                "source_date": str(row.get("date", "")),
                "notes": str(row.get("notes", "")),
            }
            try:
                upsert_screening_entry(conn, entry)
                count += 1
            except Exception:
                pass

        conn.close()
        logger.info(f"Loaded {count} OFAC SDN entries into name_screening")
        return count

    def load_eu_into_screening(self) -> int:
        """Import EU FSF sanctions from SanctionsHarness into name_screening table."""
        from app.eco_harness.sanctions import SanctionsHarness

        if not HAS_STORAGE:
            return 0

        sh = SanctionsHarness()
        df = sh.eu_sanctions_list()
        if df.empty:
            logger.warning("EU FSF returned empty — cannot load into name_screening")
            return 0

        conn = init_db(self._db_path)
        count = 0
        for _, row in df.iterrows():
            name = str(row.get("name", ""))
            entry = {
                "source": "eu_fsf",
                "source_uid": str(row.get("uid", 0)),
                "name_en": name,
                "name_cn": "",
                "name_cn_norm": "",
                "name_pinyin": "",
                "name_type": str(row.get("entity_type", "entity")).lower(),
                "pep_level": "",
                "risk_category": "sanctions",
                "aliases": str(row.get("aliases", "")),
                "programs": str(row.get("programs", "")),
                "countries": str(row.get("countries", "")),
                "addresses": str(row.get("addresses", "")),
                "source_date": str(row.get("date", "")),
                "notes": str(row.get("notes", "")),
            }
            try:
                upsert_screening_entry(conn, entry)
                count += 1
            except Exception:
                pass

        conn.close()
        logger.info(f"Loaded {count} EU FSF entries into name_screening")
        return count

    def load_un_into_screening(self) -> int:
        """Import UN SC sanctions from SanctionsHarness into name_screening table."""
        from app.eco_harness.sanctions import SanctionsHarness

        if not HAS_STORAGE:
            return 0

        sh = SanctionsHarness()
        df = sh.un_sanctions_list()
        if df.empty:
            logger.warning("UN SC returned empty — cannot load into name_screening")
            return 0

        conn = init_db(self._db_path)
        count = 0
        for _, row in df.iterrows():
            name = str(row.get("name", ""))
            entry = {
                "source": "un_sc",
                "source_uid": str(row.get("dataid", row.get("uid", 0))),
                "name_en": name,
                "name_cn": "",
                "name_cn_norm": "",
                "name_pinyin": "",
                "name_type": str(row.get("entity_type", "entity")).lower(),
                "pep_level": "",
                "risk_category": "sanctions",
                "aliases": str(row.get("aliases", "")),
                "programs": str(row.get("sanctions_regime", "")),
                "countries": str(row.get("countries", "")),
                "addresses": str(row.get("addresses", "")),
                "source_date": str(row.get("date", "")),
                "notes": str(row.get("notes", "")),
            }
            try:
                upsert_screening_entry(conn, entry)
                count += 1
            except Exception:
                pass

        conn.close()
        logger.info(f"Loaded {count} UN SC entries into name_screening")
        return count

    # ── OpenSanctions update check ─────────────────────────────

    def check_opensanctions_version(self) -> Optional[str]:
        """Check current OpenSanctions version ID. Returns None on failure."""
        try:
            resp = self._session.get(OS_INDEX_URL, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            return data.get("version", data.get("last_export"))
        except Exception:
            return None

    # ── GDELT Negative News ────────────────────────────────────

    def search_negative_news(
        self,
        name: str,
        *,
        name_cn: str = "",
        days: int = 365,
        max_results: int = 20,
        use_cache: bool = True,
    ) -> list[dict]:
        """Search GDELT for negative news about a name.

        Uses both English and Chinese keywords. Results cached for 7 days.
        """
        if use_cache and HAS_STORAGE:
            conn = init_db(self._db_path)
            try:
                cached = get_news_cache(conn, name, max_age_hours=168)
                if not cached.empty:
                    return cached.to_dict(orient="records")
            finally:
                conn.close()

        # Build search query — combine name with AML/corruption keywords
        kw_en = "(corruption OR fraud OR \"money laundering\" OR bribery OR sanction OR crime OR investigation OR indictment)"
        kw_cn = "(腐败 OR 贪污 OR 洗钱 OR 受贿 OR 诈骗 OR 制裁 OR 调查 OR 起诉)"

        if name_cn:
            query = f'"{name}" OR "{name_cn}" {kw_en} OR {kw_cn}'
        else:
            query = f'"{name}" {kw_en} {kw_cn}'

        params = {
            "query": query,
            "mode": "artlist",
            "format": "json",
            "timespan": f"{days}d",
            "maxrecords": max_results,
            "sort": "datedesc",
        }

        try:
            resp = self._session.get(GDELT_API, params=params, timeout=30)
            # GDELT free tier rate limit: 1 request per 5 seconds
            if resp.status_code == 429:
                import time
                logger.info("GDELT rate limited, retrying in 6s...")
                time.sleep(6)
                resp = self._session.get(GDELT_API, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            logger.warning(f"GDELT search failed for: {name}")
            return []

        articles = data.get("articles", [])
        results = []
        for art in articles:
            result = {
                "search_name": name,
                "search_name_cn": name_cn,
                "source": "gdelt",
                "title": art.get("title", ""),
                "url": art.get("url", ""),
                "published_date": art.get("seendate", ""),
                "snippet": art.get("snippet", "")[:500] if art.get("snippet") else "",
                "matched_keywords": "",
            }
            results.append(result)

            # Cache to DB
            if HAS_STORAGE:
                try:
                    conn = init_db(self._db_path)
                    upsert_news_cache(conn, result)
                    conn.close()
                except Exception:
                    pass

        return results

    # ── Chinese Court (Aliyun stub — to be configured) ─────────

    def search_cn_court(
        self,
        name: str,
        *,
        id_number: str = "",
    ) -> dict:
        """Search Chinese court data via Alibaba Cloud Xinshu API.

        NOTE: Requires ALIYUN_COURT_API_KEY + ALIYUN_COURT_API_SECRET env vars.
        Returns {records: [...], total: N, source: 'aliyun_xinshu'}.

        Stub implementation — API endpoint and auth details to be configured
        when user obtains Alibaba Cloud API credentials.
        """
        import os
        api_key = os.environ.get("ALIYUN_COURT_API_KEY", "")
        api_secret = os.environ.get("ALIYUN_COURT_API_SECRET", "")

        if not api_key or not api_secret:
            return {
                "error": "Alibaba Cloud court API not configured. Set ALIYUN_COURT_API_KEY and ALIYUN_COURT_API_SECRET.",
                "records": [],
                "total": 0,
            }

        # TODO: Implement actual Alibaba Cloud API call
        # Endpoint: https://courtapi.aliyuncs.com/... (to be confirmed with actual product)
        # Auth: Alibaba Cloud AK/SK signature
        # Params: name, id_number
        # Returns: shixin, zxgg, cpws, ktgg, fygg counts + details

        return {
            "error": "Implementation pending — API endpoint details needed",
            "records": [],
            "total": 0,
        }

    # ── Comprehensive Screening ────────────────────────────────

    def screen(self, query: str, *, include_news: bool = False) -> dict:
        """Comprehensive name screening against all available sources.

        Returns:
            {
                query: str,
                matches: { sanctions: [...], peps: [...], other: [...] },
                negative_news: [...] | None,
                court_records: dict | None,
                total_hits: int,
            }
        """
        if not HAS_STORAGE:
            return {"error": "storage module not available"}

        conn = init_db(self._db_path)
        try:
            # Step 1: Get broad candidates from DB
            # Normalize Chinese queries so traditional→simplified variants match
            query_norm = NameMatcher.normalize_cn(query) if NameMatcher.has_cn(query) else ""
            candidates = search_screening_fuzzy(conn, query, limit=200, normalized_query=query_norm)

            if candidates.empty:
                return {
                    "query": query,
                    "matches": {"sanctions": [], "peps": [], "other": []},
                    "negative_news": None,
                    "court_records": None,
                    "total_hits": 0,
                }

            # Step 2: Fuzzy match with NameMatcher
            match_result = self._matcher.cross_search(query, candidates, threshold=70, limit=30)
        finally:
            conn.close()

        # Step 3: Negative news (on-demand)
        negative_news = None
        if include_news:
            is_cn = NameMatcher.has_cn(query)
            negative_news = self.search_negative_news(
                query,
                name_cn=query if is_cn else "",
                days=365,
                max_results=10,
            )

        # Step 4: Court records (on-demand for Chinese names)
        court_records = None
        if NameMatcher.has_cn(query):
            court_records = self.search_cn_court(query)

        total = match_result["total_matches"]

        return {
            "query": query,
            "matches": {
                "sanctions": match_result["sanctions"],
                "peps": match_result["peps"],
                "other": match_result["other"],
            },
            "negative_news": negative_news,
            "court_records": court_records,
            "total_hits": total,
        }

    def get_stats(self) -> dict:
        """Get name screening database statistics."""
        if not HAS_STORAGE:
            return {"error": "storage module not available"}

        conn = init_db(self._db_path)
        try:
            total = conn.execute("SELECT COUNT(*) FROM name_screening").fetchone()[0]
            by_source = conn.execute(
                "SELECT source, COUNT(*) as cnt FROM name_screening GROUP BY source ORDER BY cnt DESC"
            ).fetchall()
            by_risk = conn.execute(
                "SELECT risk_category, COUNT(*) as cnt FROM name_screening GROUP BY risk_category ORDER BY cnt DESC"
            ).fetchall()
            cn_count = conn.execute(
                "SELECT COUNT(*) FROM name_screening WHERE name_cn IS NOT NULL AND name_cn != ''"
            ).fetchone()[0]
            pep_count = conn.execute(
                "SELECT COUNT(*) FROM name_screening WHERE risk_category = 'pep'"
            ).fetchone()[0]

            return {
                "total": total,
                "with_chinese_names": cn_count,
                "peps": pep_count,
                "by_source": [{"source": r[0], "count": r[1]} for r in by_source],
                "by_risk": [{"category": r[0], "count": r[1]} for r in by_risk],
            }
        finally:
            conn.close()

    def load_tw_fsc(self) -> int:
        """Load Taiwan FSC enforcement data into name_screening table.

        Returns number of records loaded.
        """
        from app.eco_harness.tw_fsc import load_tw_fsc_into_screening
        return load_tw_fsc_into_screening(self._db_path)

    def load_hk_sfc(self) -> int:
        """Load HK SFC enforcement data into name_screening table.

        Returns number of records loaded.
        """
        from app.eco_harness.hk_sfc import load_hk_sfc_into_screening
        return load_hk_sfc_into_screening(self._db_path)

    def load_hk_hkma(self) -> int:
        """Load HKMA enforcement data into name_screening table.

        Returns number of records loaded.
        """
        from app.eco_harness.hk_hkma import load_hk_hkma_into_screening
        return load_hk_hkma_into_screening(self._db_path)
