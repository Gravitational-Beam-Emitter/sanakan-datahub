"""
Sanctions & Corruption Data Harness — OFAC SDN + EU FSF + UN SC + TI CPI.

Sources:
  OFAC SDN — US Treasury Specially Designated Nationals (XML, daily updates)
  EU FSF   — EU Consolidated Financial Sanctions List (extracted from OpenSanctions DB)
  UN SC    — UN Security Council Consolidated List (XML, daily updates)
  TI CPI   — Transparency International Corruption Perceptions Index (Wikipedia, annual)

Each method returns pd.DataFrame with columns: date, value, notes
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import date
from io import BytesIO

import pandas as pd
import requests

logger = logging.getLogger("eco_data.sanctions")

OFAC_SDN_URL = "https://sanctionslistservice.ofac.treas.gov/api/publicationpreview/exports/sdn.xml"
OFAC_XML_NS = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/XML"
UN_SC_URL = "https://scsanctions.un.org/resources/xml/en/consolidated.xml"


class SanctionsHarness:
    """OFAC sanctions + TI Corruption Perceptions Index data access."""

    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36 "
                "EcoData/1.0"
            ),
        })

    # ── OFAC SDN ─────────────────────────────────────────────────

    def _fetch_sdn_xml(self) -> bytes:
        """Download the full SDN XML. Returns raw bytes or raises."""
        resp = self._session.get(OFAC_SDN_URL, timeout=60)
        resp.raise_for_status()
        return resp.content

    def ofac_sdn_list(self) -> pd.DataFrame:
        """
        Full OFAC SDN list — all sanctioned entities, individuals, vessels, aircraft.
        Returns DataFrame with: uid, name, sdn_type, programs, addresses, aliases.
        """
        try:
            xml_bytes = self._fetch_sdn_xml()
            tree = ET.parse(BytesIO(xml_bytes))
            root = tree.getroot()

            pub = root.find(f"{{{OFAC_XML_NS}}}publshInformation")
            pub_date = pub.find(f"{{{OFAC_XML_NS}}}Publish_Date").text if pub is not None else ""

            records = []
            for entry in root.findall(f"{{{OFAC_XML_NS}}}sdnEntry"):
                uid = entry.find(f"{{{OFAC_XML_NS}}}uid")
                last_name = entry.find(f"{{{OFAC_XML_NS}}}lastName")
                first_name = entry.find(f"{{{OFAC_XML_NS}}}firstName")
                sdn_type = entry.find(f"{{{OFAC_XML_NS}}}sdnType")
                title = entry.find(f"{{{OFAC_XML_NS}}}title")

                # Build full name
                name_parts = []
                if first_name is not None and first_name.text:
                    name_parts.append(first_name.text)
                if last_name is not None and last_name.text:
                    name_parts.append(last_name.text)
                if not name_parts and title is not None and title.text:
                    name_parts.append(title.text)
                name = " ".join(name_parts) if name_parts else "UNKNOWN"

                # Sanctions programs
                prog_list = entry.find(f"{{{OFAC_XML_NS}}}programList")
                programs = []
                if prog_list is not None:
                    for prog in prog_list.findall(f"{{{OFAC_XML_NS}}}program"):
                        if prog.text:
                            programs.append(prog.text)

                # Addresses
                addr_list = entry.find(f"{{{OFAC_XML_NS}}}addressList")
                addresses = []
                countries = set()
                if addr_list is not None:
                    for addr in addr_list.findall(f"{{{OFAC_XML_NS}}}address"):
                        parts = []
                        for tag in ("address1", "address2", "address3", "city",
                                     "stateOrProvince", "postalCode", "country"):
                            el = addr.find(f"{{{OFAC_XML_NS}}}{tag}")
                            if el is not None and el.text:
                                parts.append(el.text.strip())
                                if tag == "country":
                                    countries.add(el.text.strip())
                        if parts:
                            addresses.append(", ".join(parts))

                # Aliases (AKAs)
                aka_list = entry.find(f"{{{OFAC_XML_NS}}}akaList")
                aliases = []
                if aka_list is not None:
                    for aka in aka_list.findall(f"{{{OFAC_XML_NS}}}aka"):
                        aka_last = aka.find(f"{{{OFAC_XML_NS}}}lastName")
                        aka_first = aka.find(f"{{{OFAC_XML_NS}}}firstName")
                        aka_type = aka.find(f"{{{OFAC_XML_NS}}}type")
                        aka_parts = []
                        if aka_first is not None and aka_first.text:
                            aka_parts.append(aka_first.text)
                        if aka_last is not None and aka_last.text:
                            aka_parts.append(aka_last.text)
                        aka_name = " ".join(aka_parts)
                        aka_info = {"name": aka_name}
                        if aka_type is not None and aka_type.text:
                            aka_info["type"] = aka_type.text
                        aliases.append(aka_info)

                records.append({
                    "uid": int(uid.text) if uid is not None and uid.text else 0,
                    "name": name,
                    "sdn_type": sdn_type.text if sdn_type is not None else "Unknown",
                    "programs": ", ".join(programs),
                    "countries": ", ".join(sorted(countries)),
                    "addresses": " | ".join(addresses),
                    "aliases": ", ".join(a["name"] for a in aliases if a["name"]),
                })

            df = pd.DataFrame(records)
            df["date"] = pd.to_datetime(pub_date).strftime("%Y-%m-%d")
            df["value"] = 1
            df["notes"] = df.apply(
                lambda r: (
                    f"OFAC SDN·{r['sdn_type']}·{r['name']}·"
                    f"制裁计划:{r['programs'][:100]}·"
                    f"关联国家:{r['countries'][:80]}"
                ),
                axis=1,
            )
            return df[["date", "value", "notes", "name", "sdn_type", "programs",
                        "countries", "addresses", "aliases"]]

        except Exception:
            return pd.DataFrame()

    def ofac_sanctions_by_country(self) -> pd.DataFrame:
        """
        OFAC sanctions aggregated by country.
        Returns per-country counts of sanctioned entities, individuals, vessels, aircraft.
        """
        df = self.ofac_sdn_list()
        if df.empty:
            return df

        today = str(date.today())
        records = []

        # Explode countries (each entry may have multiple countries)
        country_entries: dict[str, dict] = {}
        for _, row in df.iterrows():
            countries_str = row.get("countries", "")
            if not countries_str or pd.isna(countries_str):
                continue
            for c in countries_str.split(", "):
                c = c.strip()
                if not c:
                    continue
                if c not in country_entries:
                    country_entries[c] = {
                        "entities": 0, "individuals": 0, "vessels": 0, "aircraft": 0,
                        "programs": set(),
                    }
                sdn_type = str(row.get("sdn_type", "")).lower()
                if sdn_type == "entity":
                    country_entries[c]["entities"] += 1
                elif sdn_type == "individual":
                    country_entries[c]["individuals"] += 1
                elif sdn_type == "vessel":
                    country_entries[c]["vessels"] += 1
                elif sdn_type == "aircraft":
                    country_entries[c]["aircraft"] += 1

                for prog in str(row.get("programs", "")).split(", "):
                    if prog.strip():
                        country_entries[c]["programs"].add(prog.strip())

        for country, stats in country_entries.items():
            total = stats["entities"] + stats["individuals"] + stats["vessels"] + stats["aircraft"]
            records.append({
                "date": today,
                "value": total,
                "notes": (
                    f"OFAC制裁·{country}·"
                    f"实体{stats['entities']}·个人{stats['individuals']}·"
                    f"船舶{stats['vessels']}·飞行器{stats['aircraft']}·"
                    f"制裁计划:{','.join(sorted(stats['programs'])[:5])}"
                ),
                "country": country,
                "entities": stats["entities"],
                "individuals": stats["individuals"],
                "vessels": stats["vessels"],
                "aircraft": stats["aircraft"],
            })

        return pd.DataFrame(records)

    def ofac_country_sanctions(self, country: str) -> pd.DataFrame:
        """Get sanctions summary for a specific country."""
        df = self.ofac_sanctions_by_country()
        if df.empty:
            return df
        return df[df["country"] == country].copy()

    def ofac_total_counts(self) -> pd.DataFrame:
        """Aggregate OFAC SDN counts: total entities, individuals, vessels, aircraft."""
        df = self.ofac_sdn_list()
        if df.empty:
            return df

        today = str(date.today())
        type_counts = df["sdn_type"].value_counts().to_dict()
        total = len(df)
        program_count = len(set(
            p for progs in df["programs"].dropna() for p in str(progs).split(", ") if p
        ))

        return pd.DataFrame([{
            "date": today,
            "value": total,
            "notes": (
                f"OFAC SDN制裁总计·{total}条·"
                f"实体{type_counts.get('Entity', 0)}·"
                f"个人{type_counts.get('Individual', 0)}·"
                f"船舶{type_counts.get('Vessel', 0)}·"
                f"飞行器{type_counts.get('Aircraft', 0)}·"
                f"制裁计划{program_count}个"
            ),
        }])

    # ── TI CPI ────────────────────────────────────────────────────

    def cpi_scores(self) -> pd.DataFrame:
        """
        Transparency International Corruption Perceptions Index.
        Parses the latest year's country table from Wikipedia.
        Returns DataFrame with: country, rank, score (0-100), rank_change.
        """
        url = "https://en.wikipedia.org/wiki/Corruption_Perceptions_Index"
        try:
            resp = self._session.get(url, timeout=15)
            resp.raise_for_status()
            html = resp.text
        except Exception:
            return pd.DataFrame()

        # Find the country data table (largest wikitable with >100 rows)
        all_tables = re.findall(r'<table[^>]*>(.*?)</table>', html, re.DOTALL)
        country_table = None
        for table in all_tables:
            rows = re.findall(r'<tr>(.*?)</tr>', table, re.DOTALL)
            if len(rows) > 100:
                country_table = table
                break

        if not country_table:
            return pd.DataFrame()

        # Determine which year this data is for
        year_match = re.search(r'<span class="mw-headline" id="(\d{4})_scores">', html)
        cpi_year = year_match.group(1) if year_match else str(date.today().year)

        rows = re.findall(r'<tr>(.*?)</tr>', country_table, re.DOTALL)
        records = []

        for row in rows[1:]:  # Skip header
            cells = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', row, re.DOTALL)
            cell_text = []
            for c in cells:
                t = re.sub(r'<[^>]+>', '', c).strip()
                t = re.sub(r'\[\d+\]', '', t).strip()  # Remove citations
                t = re.sub(r'&#160;', ' ', t)
                t = re.sub(r'&amp;', '&', t)
                cell_text.append(t)

            if len(cell_text) >= 3:
                rank = cell_text[0]
                country = cell_text[1]
                score = cell_text[2]
                rank_change = cell_text[3] if len(cell_text) > 3 else ""

                # Clean country name
                country = re.sub(r'\s*\([^)]*\)', '', country).strip()

                # Skip non-country rows
                if not country or not score:
                    continue
                if country in ('Nation or Territory', 'Score', '#', 'Rank', ''):
                    continue

                try:
                    score_val = int(score)
                except ValueError:
                    continue

                if len(country) < 3 or len(country.split()) > 8:
                    continue

                records.append({
                    "country": country,
                    "rank": int(rank) if rank.isdigit() else 0,
                    "score": score_val,
                    "rank_change": rank_change.strip(),
                })

        if not records:
            return pd.DataFrame()

        df = pd.DataFrame(records)
        today = str(date.today())
        df["date"] = today
        df["value"] = df["score"]

        df["notes"] = df.apply(
            lambda r: (
                f"TI Corruption Perceptions Index {cpi_year}·"
                f"排名第{r['rank']}/180·"
                f"评分{r['score']}/100·"
                f"排名变化:{r['rank_change'] if r['rank_change'] else '不变'}"
            ),
            axis=1,
        )

        return df[["date", "value", "notes", "country", "rank", "score", "rank_change"]]

    def cpi_country_score(self, country: str) -> pd.DataFrame:
        """Get CPI score for a specific country."""
        df = self.cpi_scores()
        if df.empty:
            return df
        result = df[df["country"] == country]
        if result.empty:
            result = df[df["country"].str.contains(country, case=False, na=False)]
        return result.head(1).copy()

    def cpi_top_risks(self, n: int = 10) -> pd.DataFrame:
        """Top N most corrupt countries (lowest CPI scores)."""
        df = self.cpi_scores()
        if df.empty:
            return df
        return df.nsmallest(n, "score")

    def cpi_cleanest(self, n: int = 10) -> pd.DataFrame:
        """Top N least corrupt countries (highest CPI scores)."""
        df = self.cpi_scores()
        if df.empty:
            return df
        return df.nlargest(n, "score")

    # ── UN Security Council Consolidated List ─────────────────────

    def _fetch_un_xml(self) -> bytes:
        """Download the UN SC consolidated sanctions XML. Returns raw bytes or raises."""
        resp = self._session.get(UN_SC_URL, timeout=60)
        resp.raise_for_status()
        return resp.content

    def un_sanctions_list(self) -> pd.DataFrame:
        """
        Full UN Security Council consolidated sanctions list.
        Parses individuals and entities from the UN XML.
        Returns DataFrame with: uid, name, entity_type, nationality, countries,
        sanctions_regime, designation, aliases, source_date.
        """
        try:
            xml_bytes = self._fetch_un_xml()
            tree = ET.parse(BytesIO(xml_bytes))
            root = tree.getroot()

            pub_date = root.get("dateGenerated", str(date.today()))[:10]

            records = []
            uid_counter = 0

            # Parse <INDIVIDUALS>
            individuals = root.find("INDIVIDUALS")
            if individuals is not None:
                for ind in individuals.findall("INDIVIDUAL"):
                    uid_counter += 1
                    dataid = ind.findtext("DATAID", "")
                    first = ind.findtext("FIRST_NAME", "") or ""
                    second = ind.findtext("SECOND_NAME", "") or ""
                    third = ind.findtext("THIRD_NAME", "") or ""
                    fourth = ind.findtext("FOURTH_NAME", "") or ""

                    # Build full name
                    name_parts = [p for p in [first, second, third, fourth] if p]
                    name = " ".join(name_parts) if name_parts else "UNKNOWN"

                    # Nationality
                    nat_el = ind.find("NATIONALITY")
                    nationalities = []
                    if nat_el is not None:
                        for v in nat_el.findall("VALUE"):
                            if v.text:
                                nationalities.append(v.text.strip())

                    # Designation
                    des_el = ind.find("DESIGNATION")
                    designations = []
                    if des_el is not None:
                        for v in des_el.findall("VALUE"):
                            if v.text:
                                designations.append(v.text.strip())

                    # Addresses and countries
                    addresses = []
                    addr_countries = set()
                    for addr in ind.findall("INDIVIDUAL_ADDRESS"):
                        parts = []
                        for tag in ("CITY", "STATE_PROVINCE", "COUNTRY", "NOTE"):
                            el = addr.find(tag)
                            if el is not None and el.text and el.text.strip():
                                parts.append(el.text.strip())
                                if tag == "COUNTRY":
                                    addr_countries.add(el.text.strip())
                        if parts:
                            addresses.append(", ".join(parts))

                    # Aliases
                    aliases = []
                    for aka in ind.findall("INDIVIDUAL_ALIAS"):
                        aname = aka.findtext("ALIAS_NAME", "")
                        if aname and aname.strip():
                            quality = aka.findtext("QUALITY", "")
                            aliases.append(f"{aname} ({quality})" if quality else aname)

                    # Sanctions regime
                    un_list_type = ind.findtext("UN_LIST_TYPE", "")

                    records.append({
                        "uid": uid_counter,
                        "dataid": dataid,
                        "name": name,
                        "entity_type": "individual",
                        "nationality": ", ".join(nationalities),
                        "countries": ", ".join(sorted(addr_countries)),
                        "sanctions_regime": un_list_type,
                        "designation": " | ".join(designations),
                        "addresses": " | ".join(addresses),
                        "aliases": ", ".join(aliases),
                        "listed_on": ind.findtext("LISTED_ON", ""),
                    })

            # Parse <ENTITIES>
            entities = root.find("ENTITIES")
            if entities is not None:
                for ent in entities.findall("ENTITY"):
                    uid_counter += 1
                    dataid = ent.findtext("DATAID", "")
                    name = ent.findtext("FIRST_NAME", "") or "UNKNOWN"

                    # Addresses and countries
                    addresses = []
                    addr_countries = set()
                    for addr in ent.findall("ENTITY_ADDRESS"):
                        parts = []
                        for tag in ("CITY", "STATE_PROVINCE", "COUNTRY", "NOTE"):
                            el = addr.find(tag)
                            if el is not None and el.text and el.text.strip():
                                parts.append(el.text.strip())
                                if tag == "COUNTRY":
                                    addr_countries.add(el.text.strip())
                        if parts:
                            addresses.append(", ".join(parts))

                    # Aliases
                    aliases = []
                    for aka in ent.findall("ENTITY_ALIAS"):
                        aname = aka.findtext("ALIAS_NAME", "")
                        if aname and aname.strip():
                            quality = aka.findtext("QUALITY", "")
                            aliases.append(f"{aname} ({quality})" if quality else aname)

                    un_list_type = ent.findtext("UN_LIST_TYPE", "")

                    records.append({
                        "uid": uid_counter,
                        "dataid": dataid,
                        "name": name,
                        "entity_type": "entity",
                        "nationality": "",
                        "countries": ", ".join(sorted(addr_countries)),
                        "sanctions_regime": un_list_type,
                        "designation": "",
                        "addresses": " | ".join(addresses),
                        "aliases": ", ".join(aliases),
                        "listed_on": ent.findtext("LISTED_ON", ""),
                    })

            if not records:
                return pd.DataFrame()

            df = pd.DataFrame(records)
            df["date"] = pub_date
            df["value"] = 1
            df["notes"] = df.apply(
                lambda r: (
                    f"UN SC制裁·{r['entity_type']}·{r['name']}·"
                    f"制裁制度:{r['sanctions_regime']}·"
                    f"国籍:{r['nationality'][:60]}·"
                    f"关联国家:{r['countries'][:80]}"
                ),
                axis=1,
            )
            return df[["date", "value", "notes", "dataid", "name", "entity_type",
                        "sanctions_regime", "nationality", "countries",
                        "addresses", "aliases", "listed_on"]]

        except Exception:
            logger.warning("Failed to fetch/parse UN SC sanctions list", exc_info=True)
            return pd.DataFrame()

    def un_sanctions_by_country(self) -> pd.DataFrame:
        """
        UN SC sanctions aggregated by country (from address countries).
        Returns per-country counts of sanctioned individuals and entities.
        """
        df = self.un_sanctions_list()
        if df.empty:
            return df

        today = str(date.today())
        records = []
        country_stats: dict[str, dict] = {}

        for _, row in df.iterrows():
            countries_str = row.get("countries", "")
            if not countries_str or pd.isna(countries_str):
                continue
            for c in countries_str.split(", "):
                c = c.strip()
                if not c:
                    continue
                if c not in country_stats:
                    country_stats[c] = {
                        "individuals": 0, "entities": 0, "regimes": set(),
                    }
                etype = str(row.get("entity_type", "")).lower()
                if etype == "individual":
                    country_stats[c]["individuals"] += 1
                elif etype == "entity":
                    country_stats[c]["entities"] += 1

                regime = str(row.get("sanctions_regime", ""))
                if regime:
                    country_stats[c]["regimes"].add(regime)

        for country, stats in country_stats.items():
            total = stats["individuals"] + stats["entities"]
            records.append({
                "date": today,
                "value": total,
                "notes": (
                    f"UN SC制裁·{country}·"
                    f"个人{stats['individuals']}·实体{stats['entities']}·"
                    f"制裁制度:{','.join(sorted(stats['regimes'])[:5])}"
                ),
                "country": country,
                "individuals": stats["individuals"],
                "entities": stats["entities"],
            })

        return pd.DataFrame(records)

    def un_total_counts(self) -> pd.DataFrame:
        """Aggregate UN SC sanctions: total individuals, entities, sanctions regimes."""
        df = self.un_sanctions_list()
        if df.empty:
            return df

        today = str(date.today())
        type_counts = df["entity_type"].value_counts().to_dict()
        total = len(df)
        regime_count = df["sanctions_regime"].nunique()

        return pd.DataFrame([{
            "date": today,
            "value": total,
            "notes": (
                f"UN SC安全理事会制裁总计·{total}条·"
                f"个人{type_counts.get('individual', 0)}·"
                f"实体{type_counts.get('entity', 0)}·"
                f"制裁制度{regime_count}个"
            ),
        }])

    def un_country_sanctions(self, country: str) -> pd.DataFrame:
        """Get UN SC sanctions summary for a specific country.

        Accepts short names (e.g., 'Iran', 'Russia', 'North Korea') and maps
        them to the full UN naming conventions.
        """
        df = self.un_sanctions_by_country()
        if df.empty:
            return df
        # Try exact match first
        result = df[df["country"] == country]
        if not result.empty:
            return result.copy()
        # Mappings for UN naming conventions
        un_name_map: dict[str, str] = {
            "Russia": "Russian Federation",
            "Iran": "Iran (Islamic Republic of)",
            "North Korea": "Democratic People's Republic of Korea",
            "Syria": "Syrian Arab Republic",
            "United States": "United States of America",
            "United Kingdom": "United Kingdom of Great Britain and Northern Ireland",
            "Tanzania": "United Republic of Tanzania",
            "Vietnam": "Viet Nam",
            "Venezuela": "Venezuela (Bolivarian Republic of)",
            "South Korea": "Republic of Korea",
            "Turkey": "Türkiye",
            "Palestine": "State of Palestine",
            "Bolivia": "Bolivia (Plurinational State of)",
            "Moldova": "Republic of Moldova",
            "Laos": "Lao People's Democratic Republic",
        }
        mapped = un_name_map.get(country)
        if mapped:
            result = df[df["country"] == mapped]
            if not result.empty:
                return result.copy()
        # Try substring match
        result = df[df["country"].str.contains(
            country.replace("(", r"\(").replace(")", r"\)"),
            case=False, na=False
        )]
        return result.head(1).copy()

    # ── EU Consolidated Financial Sanctions (via OpenSanctions DB) ─

    # Map country names → ISO 2-letter codes used in OpenSanctions
    _COUNTRY_NAME_TO_ISO: dict[str, str] = {
        "Russia": "ru", "Iran": "ir", "China": "cn", "North Korea": "kp",
        "Syria": "sy", "Belarus": "by", "Myanmar": "mm", "Venezuela": "ve",
        "Cuba": "cu", "Sudan": "sd", "Somalia": "so", "Afghanistan": "af",
        "Iraq": "iq", "Libya": "ly", "Yemen": "ye", "Zimbabwe": "zw",
        "Ukraine": "ua", "Lebanon": "lb", "Serbia": "rs", "Bosnia and Herzegovina": "ba",
        "Moldova": "md", "Georgia": "ge", "Turkey": "tr", "South Sudan": "ss",
        "Central African Republic": "cf", "Democratic Republic of the Congo": "cd",
        "Mali": "ml", "Niger": "ne", "Burkina Faso": "bf", "Guinea": "gn",
        "Guinea-Bissau": "gw", "Liberia": "lr", "Chad": "td", "Burundi": "bi",
        "Rwanda": "rw", "Uganda": "ug", "Eritrea": "er", "Tajikistan": "tj",
        "Turkmenistan": "tm", "Kyrgyzstan": "kg", "Kazakhstan": "kz",
        "Uzbekistan": "uz", "Azerbaijan": "az", "Armenia": "am",
        "Tunisia": "tn", "Morocco": "ma", "Algeria": "dz", "Egypt": "eg",
        "Nigeria": "ng", "Kenya": "ke", "Ethiopia": "et", "Tanzania": "tz",
        "South Africa": "za", "Angola": "ao", "Cameroon": "cm",
        "Ivory Coast": "ci", "Senegal": "sn", "Namibia": "na",
        "Haiti": "ht", "Bolivia": "bo", "Colombia": "co", "Peru": "pe",
        "Ecuador": "ec", "Uruguay": "uy", "Chile": "cl",
        "Brazil": "br", "Argentina": "ar", "Mexico": "mx",
        "Canada": "ca", "United States": "us", "United Kingdom": "gb",
        "France": "fr", "Germany": "de", "Italy": "it", "Spain": "es",
        "Portugal": "pt", "Netherlands": "nl", "Belgium": "be",
        "Austria": "at", "Sweden": "se", "Norway": "no", "Denmark": "dk",
        "Finland": "fi", "Poland": "pl", "Czech Republic": "cz",
        "Greece": "gr", "Hungary": "hu", "Romania": "ro", "Bulgaria": "bg",
        "Croatia": "hr", "Slovenia": "si", "Slovakia": "sk",
        "Estonia": "ee", "Latvia": "lv", "Lithuania": "lt",
        "Ireland": "ie", "Luxembourg": "lu", "Malta": "mt", "Cyprus": "cy",
        "Iceland": "is", "Switzerland": "ch", "Liechtenstein": "li",
        "Monaco": "mc", "Israel": "il", "Saudi Arabia": "sa",
        "United Arab Emirates": "ae", "Kuwait": "kw", "Qatar": "qa",
        "Bahrain": "bh", "Oman": "om", "Jordan": "jo",
        "India": "in", "Pakistan": "pk", "Bangladesh": "bd",
        "Sri Lanka": "lk", "Nepal": "np", "Laos": "la", "Vietnam": "vn",
        "Thailand": "th", "Malaysia": "my", "Singapore": "sg",
        "Indonesia": "id", "Philippines": "ph", "Myanmar": "mm",
        "Japan": "jp", "South Korea": "kr", "Taiwan": "tw",
        "Australia": "au", "New Zealand": "nz",
        "Hong Kong": "hk", "Macau": "mo", "Macao": "mo",
        "Panama": "pa", "Cayman Islands": "ky", "Bermuda": "bm",
        "Bahamas": "bs", "British Virgin Islands": "vg",
        "Isle of Man": "im", "Jersey": "je", "Guernsey": "gg",
        "Gibraltar": "gi", "Türkiye": "tr",
        "Papua New Guinea": "pg", "South Sudan": "ss",
        "Congo": "cd", "Montenegro": "me",
    }

    def _get_eu_from_db(self) -> pd.DataFrame:
        """Extract EU FSF sanctions entities from the name_screening table."""
        try:
            from app.storage import init_db
            conn = init_db()
            rows = conn.execute(
                "SELECT source_uid, name_en, name_cn, name_type, countries, "
                "programs, addresses, aliases, notes "
                "FROM name_screening "
                "WHERE source = 'opensanctions' AND programs LIKE '%eu_fsf%'"
            ).fetchall()
            conn.close()

            if not rows:
                return pd.DataFrame()

            records = []
            for i, r in enumerate(rows):
                records.append({
                    "uid": i + 1,
                    "source_uid": r[0] or "",
                    "name": r[1] or r[2] or "UNKNOWN",
                    "entity_type": r[3] or "entity",
                    "countries": r[4] or "",
                    "programs": r[5] or "",
                    "addresses": r[6] or "",
                    "aliases": r[7] or "",
                    "notes": r[8] or "",
                })

            return pd.DataFrame(records)
        except Exception:
            logger.warning("Failed to extract EU FSF data from name_screening", exc_info=True)
            return pd.DataFrame()

    def eu_sanctions_list(self) -> pd.DataFrame:
        """
        EU Consolidated Financial Sanctions List (extracted from OpenSanctions).
        Returns DataFrame with: uid, name, entity_type, countries, programs, addresses, aliases.
        """
        df = self._get_eu_from_db()
        if df.empty:
            return df

        today = str(date.today())
        df["date"] = today
        df["value"] = 1
        df["notes"] = df.apply(
            lambda r: (
                f"EU FSF制裁·{r['entity_type']}·{r['name']}·"
                f"关联国家:{r['countries'][:80]}·"
                f"制裁计划:{r['programs'][:100]}"
            ),
            axis=1,
        )
        return df[["date", "value", "notes", "uid", "name", "entity_type",
                    "countries", "programs", "addresses", "aliases"]]

    def eu_sanctions_by_country(self) -> pd.DataFrame:
        """
        EU FSF sanctions aggregated by country.
        Returns per-country counts of sanctioned entities and individuals.
        """
        df = self._get_eu_from_db()
        if df.empty:
            return df

        today = str(date.today())
        country_stats: dict[str, dict] = {}

        for _, row in df.iterrows():
            countries_str = row.get("countries", "")
            if not countries_str or pd.isna(countries_str):
                continue
            for c in countries_str.split(", "):
                c = c.strip()
                if not c:
                    continue
                if c not in country_stats:
                    country_stats[c] = {"entities": 0, "individuals": 0}
                etype = str(row.get("entity_type", "")).lower()
                if etype == "individual":
                    country_stats[c]["individuals"] += 1
                else:
                    country_stats[c]["entities"] += 1

        records = []
        for country, stats in country_stats.items():
            total = stats["entities"] + stats["individuals"]
            records.append({
                "date": today,
                "value": total,
                "notes": (
                    f"EU FSF制裁·{country}·"
                    f"实体{stats['entities']}·个人{stats['individuals']}"
                ),
                "country": country,
                "entities": stats["entities"],
                "individuals": stats["individuals"],
            })

        return pd.DataFrame(records)

    def eu_total_counts(self) -> pd.DataFrame:
        """Aggregate EU FSF sanctions: total entities, individuals."""
        df = self._get_eu_from_db()
        if df.empty:
            return df

        today = str(date.today())
        type_counts = df["entity_type"].value_counts().to_dict()
        total = len(df)

        # Count unique countries
        all_countries = set()
        for _, row in df.iterrows():
            c_str = row.get("countries", "")
            if c_str and not pd.isna(c_str):
                for c in c_str.split(", "):
                    if c.strip():
                        all_countries.add(c.strip())

        return pd.DataFrame([{
            "date": today,
            "value": total,
            "notes": (
                f"EU FSF欧盟金融制裁总计·{total}条·"
                f"实体{type_counts.get('entity', 0)}·"
                f"个人{type_counts.get('individual', 0)}·"
                f"涉及{len(all_countries)}个国家/地区"
            ),
        }])

    def eu_country_sanctions(self, country: str) -> pd.DataFrame:
        """Get EU FSF sanctions summary for a specific country.

        Accepts full country name (e.g., 'Russia') or ISO 2-letter code (e.g., 'ru').
        """
        df = self.eu_sanctions_by_country()
        if df.empty:
            return df
        # Try exact match first
        result = df[df["country"] == country]
        if not result.empty:
            return result.copy()
        # Try ISO code mapping
        iso = self._COUNTRY_NAME_TO_ISO.get(country, country.lower())
        result = df[df["country"] == iso]
        if not result.empty:
            return result.copy()
        # Try case-insensitive
        result = df[df["country"].str.lower() == country.lower()]
        return result.head(1).copy()
