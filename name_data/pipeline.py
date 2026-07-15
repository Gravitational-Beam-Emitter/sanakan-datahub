"""
Pipeline — Import Unihan data into the Kangxi dictionary database.

Sources:
  Unihan_IRGSources.txt   → kRSUnicode (radical.extra_strokes), kTotalStrokes
  Unihan_Readings.txt     → kMandarin (pinyin), kDefinition (meaning)
  Unihan_Variants.txt     → kSimplifiedVariant, kTraditionalVariant
  Unihan_DictionaryIndices.txt → kKangXi (Kangxi page reference)

Kangxi stroke count formula:
  kangxi_total = radical_strokes(radical_index) + extra_strokes
  where radical_strokes comes from the 214 Kangxi radical table,
  and radical_index.extra_strokes comes from kRSUnicode.

Usage:
  python -m name_data.pipeline --init          # Full init: radicals + import from Unihan
  python -m name_data.pipeline --import-chars  # Import characters only
  python -m name_data.pipeline --import-radicals  # Import radicals only
  python -m name_data.pipeline --stats         # Show database statistics
"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

import pandas as pd

from name_data.config import DB_PATH, KANGXI_RADICAL_STROKES
from name_data.seed_data.kangxi_radicals import RADICALS_214
from name_data.storage import (
    get_radicals,
    get_radical_strokes,
    init_db,
    insert_radicals,
    search_chars,
    upsert_chars,
    upsert_similar,
)

log = logging.getLogger("name_data.pipeline")

# Default Unihan data directory
UNIHAN_DIR = Path("/tmp")


def _parse_rs_unicode(rs_value: str) -> tuple[int, int]:
    """Parse kRSUnicode field e.g. '163.4' → (radical_index=163, extra=4).
    Returns (0, 0) on parse failure. Handles '-1' extra for radical-as-character cases.
    """
    match = re.match(r"(\d+)\.(-?\d+)", rs_value)
    if match:
        rad = int(match.group(1))
        extra = int(match.group(2))
        # -1 means the character IS the radical form itself
        if extra == -1:
            extra = 0
        return rad, extra
    return 0, 0


def _parse_pinyin_mandarin(value: str) -> tuple[str, str, int]:
    """Parse kMandarin field e.g. 'wáng' → (pinyin, pinyin_ascii, tone).
    Returns ('', '', 0) on parse failure or if no tone detected.
    """
    pinyin = value.strip().lower()
    # Map tone marks to numbers
    tone_map = {
        "ā": 1, "á": 2, "ǎ": 3, "à": 4,
        "ē": 1, "é": 2, "ě": 3, "è": 4,
        "ī": 1, "í": 2, "ǐ": 3, "ì": 4,
        "ō": 1, "ó": 2, "ǒ": 3, "ò": 4,
        "ū": 1, "ú": 2, "ǔ": 3, "ù": 4,
        "ǖ": 1, "ǘ": 2, "ǚ": 3, "ǜ": 4,
    }
    tone = 5  # neutral/no tone
    pinyin_ascii = ""
    for ch in pinyin:
        if ch in tone_map:
            tone = tone_map[ch]
            # Convert to ASCII base
            bases = {1: "aeiou", 2: "aeiou", 3: "aeiou", 4: "aeiou",
                     "ā": "a", "á": "a", "ǎ": "a", "à": "a",
                    "ē": "e", "é": "e", "ě": "e", "è": "e",
                    "ī": "i", "í": "i", "ǐ": "i", "ì": "i",
                    "ō": "o", "ó": "o", "ǒ": "o", "ò": "o",
                    "ū": "u", "ú": "u", "ǔ": "u", "ù": "u",
                    "ǖ": "v", "ǘ": "v", "ǚ": "v", "ǜ": "v"}
            base = {"ā": "a", "á": "a", "ǎ": "a", "à": "a",
                   "ē": "e", "é": "e", "ě": "e", "è": "e",
                   "ī": "i", "í": "i", "ǐ": "i", "ì": "i",
                   "ō": "o", "ó": "o", "ǒ": "o", "ò": "o",
                   "ū": "u", "ú": "u", "ǔ": "u", "ù": "u",
                   "ǖ": "v", "ǘ": "v", "ǚ": "v", "ǜ": "v"}.get(ch, ch)
            pinyin_ascii += base
        elif ch.isalpha():
            pinyin_ascii += ch
    if not pinyin_ascii:
        pinyin_ascii = pinyin
    return pinyin, pinyin_ascii, tone


def import_radicals(db_path: str = None) -> int:
    """Import 214 Kangxi radicals from seed data. Returns rows inserted."""
    conn = init_db(db_path=db_path)
    try:
        df = pd.DataFrame(RADICALS_214, columns=[
            "id", "radical", "radical_var", "strokes", "name_zh", "name_pinyin", "index_num"
        ])
        n = insert_radicals(conn, df)
        log.info("Imported %d Kangxi radicals", n)
        return n
    finally:
        conn.close()


def _parse_unihan_line(line: str) -> tuple[str, str, str] | None:
    """Parse a Unihan data line → (codepoint, field, value)."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    parts = line.split("\t")
    if len(parts) < 3:
        return None
    return parts[0], parts[1], parts[2]


def _codepoint_to_char(cp: str) -> str:
    """Convert U+XXXX to actual character."""
    cp = cp.replace("U+", "").replace("U+", "")
    try:
        return chr(int(cp, 16))
    except (ValueError, OverflowError):
        return ""


def import_chars(db_path: str = None, unihan_dir: str = None) -> dict:
    """Import characters from Unihan data files. Returns import stats."""
    unihan_dir = Path(unihan_dir or UNIHAN_DIR)
    conn = init_db(db_path=db_path)

    # Dictionaries to accumulate data per character
    char_data: dict[str, dict] = {}  # keyed by codepoint (e.g. "5F20")

    try:
        # ── Pass 1: kRSUnicode + kTotalStrokes ──
        irg_path = unihan_dir / "Unihan_IRGSources.txt"
        if irg_path.exists():
            seen = 0
            with open(irg_path, "r", encoding="utf-8") as f:
                for line in f:
                    parsed = _parse_unihan_line(line)
                    if not parsed:
                        continue
                    cp, field, value = parsed
                    cp_key = cp.replace("U+", "")
                    if cp_key not in char_data:
                        char_data[cp_key] = {"unicode": cp}
                    if field == "kRSUnicode":
                        rad_idx, extra = _parse_rs_unicode(value)
                        char_data[cp_key]["radical_index"] = rad_idx
                        char_data[cp_key]["extra_strokes"] = extra
                        rad_strokes = get_radical_strokes(rad_idx)
                        char_data[cp_key]["radical_strokes"] = rad_strokes
                        char_data[cp_key]["total_strokes"] = rad_strokes + extra
                    elif field == "kTotalStrokes":
                        # Store as reference but prefer computed value
                        if "total_strokes" not in char_data[cp_key]:
                            char_data[cp_key]["total_strokes_ref"] = int(value)
                    seen += 1
                    if seen % 50000 == 0:
                        log.info("IRGSources: processed %d lines...", seen)
            log.info("IRGSources: processed %d lines total, %d chars with RS data",
                     seen, len(char_data))

        # ── Pass 2: kMandarin + kDefinition ──
        rdg_path = unihan_dir / "Unihan_Readings.txt"
        if rdg_path.exists():
            seen = 0
            with open(rdg_path, "r", encoding="utf-8") as f:
                for line in f:
                    parsed = _parse_unihan_line(line)
                    if not parsed:
                        continue
                    cp, field, value = parsed
                    cp_key = cp.replace("U+", "")
                    if cp_key not in char_data:
                        continue
                    if field == "kMandarin":
                        py, py_asc, tone = _parse_pinyin_mandarin(value)
                        char_data[cp_key]["pinyin"] = py
                        char_data[cp_key]["pinyin_ascii"] = py_asc
                        char_data[cp_key]["tone"] = tone
                    elif field == "kDefinition":
                        char_data[cp_key]["meaning"] = value
                    seen += 1
                    if seen % 50000 == 0:
                        log.info("Readings: processed %d lines...", seen)
            log.info("Readings: processed %d lines total", seen)

        # ── Pass 3: kSimplifiedVariant / kTraditionalVariant ──
        var_path = unihan_dir / "Unihan_Variants.txt"
        sim_trad: dict[str, str] = {}  # trad cp → simp char
        if var_path.exists():
            seen = 0
            with open(var_path, "r", encoding="utf-8") as f:
                for line in f:
                    parsed = _parse_unihan_line(line)
                    if not parsed:
                        continue
                    cp, field, value = parsed
                    cp_key = cp.replace("U+", "")
                    # kSimplifiedVariant: this char is the traditional, value is simplified
                    if field == "kSimplifiedVariant":
                        sim_cp = value.split("<")[0].replace("U+", "")  # Remove source tags
                        sim_char = _codepoint_to_char(sim_cp)
                        sim_trad[cp_key] = sim_char
                    elif field == "kTraditionalVariant":
                        trad_cp = value.split("<")[0].replace("U+", "")
                        # The traditional char has a simplified variant (this char is simp)
                        trad_char = _codepoint_to_char(trad_cp)
                    seen += 1
            log.info("Variants: processed %d lines", seen)

        # ── Pass 4: kKangXi page ──
        kx_path = unihan_dir / "Unihan_DictionaryIndices.txt"
        if kx_path.exists():
            seen = 0
            with open(kx_path, "r", encoding="utf-8") as f:
                for line in f:
                    parsed = _parse_unihan_line(line)
                    if not parsed:
                        continue
                    cp, field, value = parsed
                    cp_key = cp.replace("U+", "")
                    if cp_key not in char_data:
                        continue
                    if field == "kKangXi":
                        char_data[cp_key]["kangxi_page"] = value
                    seen += 1
            log.info("DictionaryIndices: processed %d lines", seen)

        # ── Build DataFrame and insert ──
        rows = []
        for cp, d in char_data.items():
            char = _codepoint_to_char(d.get("unicode", f"U+{cp}"))
            if not char:
                continue
            rad_idx = d.get("radical_index", 0)
            rows.append({
                "char_t": char,
                "unicode": d.get("unicode", f"U+{cp}"),
                "total_strokes": d.get("total_strokes", d.get("total_strokes_ref", 0)),
                "radical": "",
                "radical_index": rad_idx,
                "radical_strokes": d.get("radical_strokes", get_radical_strokes(rad_idx)),
                "extra_strokes": d.get("extra_strokes", 0),
                "pinyin": d.get("pinyin", ""),
                "pinyin_ascii": d.get("pinyin_ascii", ""),
                "tone": d.get("tone", 5),
                "meaning": d.get("meaning", ""),
                "kangxi_page": d.get("kangxi_page", ""),
                "source": "unihan",
            })

        # Add simplified variant chars
        for cp, sim_char in sim_trad.items():
            found = False
            for r in rows:
                if r["unicode"] == f"U+{cp}":
                    r["char_s"] = sim_char
                    found = True
                    break

        df = pd.DataFrame(rows)
        log.info("Built %d character rows for import", len(df))

        n = upsert_chars(conn, df)
        log.info("Upserted %d characters", n)

        # Post-import: populate radical chars from radical_index
        log.info("Populating radical characters from index mapping...")
        rad_df = conn.execute("SELECT index_num, radical FROM kangxi_radicals").df()
        for _, row in rad_df.iterrows():
            conn.execute(
                "UPDATE kangxi_chars SET radical = ? WHERE radical_index = ? AND (radical = '' OR radical IS NULL)",
                [row["radical"], row["index_num"]]
            )

        return {
            "total_parsed": len(char_data),
            "imported": n,
            "with_radical": sum(1 for d in char_data.values() if d.get("radical_index", 0) > 0),
            "with_pinyin": sum(1 for d in char_data.values() if d.get("pinyin")),
            "with_meaning": sum(1 for d in char_data.values() if d.get("meaning")),
            "simp_pairs": len(sim_trad),
        }

    finally:
        conn.close()


# ── IDS Structure Import ────────────────────────────────────────

_STRUCTURE_MAP = {
    "⿰": "左右", "⿱": "上下", "⿲": "左中右", "⿳": "上中下",
    "⿴": "全包围", "⿵": "上三包围", "⿶": "下三包围", "⿷": "左三包围",
    "⿸": "左上包围", "⿹": "右上包围", "⿺": "左下包围", "⿻": "嵌套",
}


def _extract_structure(ids: str) -> str:
    for op, struct in _STRUCTURE_MAP.items():
        if op in ids:
            return struct
    return "独体"


def _clean_ids(ids: str) -> str:
    import re
    return re.sub(r"\[[A-Z]+\]", "", ids)


def import_ids_structure(db_path: str = None, ids_path: str = "/tmp/ids.txt") -> dict:
    """Import IDS data: fill structure and decomposition fields in kangxi_chars."""
    from name_data.storage import update_char_structure

    conn = init_db(db_path=db_path)
    stats = {"total_ids_lines": 0, "with_operator": 0, "updated": 0, "not_found": 0, "by_structure": {}}

    try:
        log.info("Loading existing chars from database...")
        existing = set()
        for row in conn.execute("SELECT char_t FROM kangxi_chars").fetchall():
            existing.add(row[0])
        log.info("Loaded %d chars from DB", len(existing))

        with open(ids_path, "r", encoding="utf-8") as f:
            batch = []
            for line in f:
                if line.startswith("#") or not line.strip():
                    continue
                parts = line.strip().split("\t")
                if len(parts) < 3:
                    continue
                cp, ch, ids_raw = parts[0], parts[1], parts[2]
                stats["total_ids_lines"] += 1

                if ch not in existing:
                    stats["not_found"] += 1
                    continue

                ids_clean = _clean_ids(ids_raw)
                structure = _extract_structure(ids_clean)
                if structure != "独体":
                    stats["with_operator"] += 1

                batch.append((structure, ids_clean, ch))
                stats["by_structure"][structure] = stats["by_structure"].get(structure, 0) + 1

                if len(batch) >= 5000:
                    for s, d, c in batch:
                        update_char_structure(conn, c, s, d)
                    stats["updated"] += len(batch)
                    batch = []
                    log.info("IDS: updated %d chars so far...", stats["updated"])

            if batch:
                for s, d, c in batch:
                    update_char_structure(conn, c, s, d)
                stats["updated"] += len(batch)

        log.info("IDS import complete: %d lines → %d updated, %d not found in DB",
                 stats["total_ids_lines"], stats["updated"], stats["not_found"])
        top = sorted(stats["by_structure"].items(), key=lambda x: -x[1])[:10]
        log.info("Structure distribution: %s", {k: v for k, v in top})
        return stats
    finally:
        conn.close()


def init_pipeline(db_path: str = None, unihan_dir: str = None) -> dict:
    """Full initialization: radicals + character import."""
    result = {"radicals": 0, "chars": {}}

    log.info("Step 1/2: Importing 214 Kangxi radicals...")
    result["radicals"] = import_radicals(db_path=db_path)

    log.info("Step 2/2: Importing characters from Unihan...")
    result["chars"] = import_chars(db_path=db_path, unihan_dir=unihan_dir)

    log.info("Pipeline init complete: %d radicals, char_stats=%s",
             result["radicals"], result["chars"])
    return result


def import_all_seed_data(db_path: str = None) -> dict:
    """Import all seed data: naming tables + I Ching hexagrams + Tuibei Tu."""
    from name_data.storage import _insert_seed
    from name_data.seed_data.wuge_81 import WUGE_81
    from name_data.seed_data.sancai_config import SANCAI_CONFIG
    from name_data.seed_data.shishen import SHISHEN
    from name_data.seed_data.shier_changsheng import SHIER_CHANGSHENG
    from name_data.seed_data.component_element import COMPONENT_ELEMENTS
    from name_data.seed_data.zodiac_component import ZODIAC_COMPONENTS
    from name_data.seed_data.compound_surname import COMPOUND_SURNAMES
    from name_data.seed_data.stroke_correction import STROKE_CORRECTIONS
    from name_data.seed_data.bagua import BAGUA
    from name_data.seed_data.hexagram_64 import HEXAGRAM_64
    from name_data.seed_data.hexagram_lines import HEXAGRAM_LINES
    from name_data.seed_data.tuibei_tu import TUIBEI_TU
    from name_data.seed_data.wuge_pattern import WUGE_PATTERNS
    from name_data.seed_data.wuge_health import WUGE_HEALTH
    from name_data.seed_data.taiyi_number import TAIYI_NUMBERS
    from name_data.seed_data.tianyun import TIANYUN
    from name_data.seed_data.jiugong import JIUGONG
    from name_data.seed_data.shensha import SHENSHA
    from name_data.seed_data.surname_pattern import SURNAME_PATTERNS

    conn = init_db(db_path=db_path)
    results = {}

    try:
        results["wuge_81"] = _insert_seed(conn, "wuge_81",
            ["id", "number", "ji_xiong", "description", "summary", "five_element", "base_score"], WUGE_81)
        log.info("wuge_81: %d rows", results["wuge_81"])

        results["sancai_config"] = _insert_seed(conn, "sancai_config",
            ["id", "heaven_element", "man_element", "earth_element", "ji_xiong", "description", "base_score"], SANCAI_CONFIG)
        log.info("sancai_config: %d rows", results["sancai_config"])

        results["shishen"] = _insert_seed(conn, "shishen",
            ["id", "name", "relation_type", "yin_yang_match", "description", "traits"], SHISHEN)
        log.info("shishen: %d rows", results["shishen"])

        results["shier_changsheng"] = _insert_seed(conn, "shier_changsheng",
            ["id", "name", "stage", "ji_xiong", "description"], SHIER_CHANGSHENG)
        log.info("shier_changsheng: %d rows", results["shier_changsheng"])

        results["component_element"] = _insert_seed(conn, "component_element",
            ["component", "five_element", "category", "description"], COMPONENT_ELEMENTS)
        log.info("component_element: %d rows", results["component_element"])

        results["zodiac_component"] = _insert_seed(conn, "zodiac_component",
            ["zodiac", "component", "favorability", "reason", "score_mod"], ZODIAC_COMPONENTS)
        log.info("zodiac_component: %d rows", results["zodiac_component"])

        results["compound_surname"] = _insert_seed(conn, "compound_surname",
            ["id", "surname", "pinyin", "strokes", "char_breakdown"], COMPOUND_SURNAMES)
        log.info("compound_surname: %d rows", results["compound_surname"])

        results["stroke_correction"] = _insert_seed(conn, "stroke_correction",
            ["char_t", "correct_strokes", "common_error", "reason", "source"], STROKE_CORRECTIONS)
        log.info("stroke_correction: %d rows", results["stroke_correction"])

        # ── I Ching (周易) & Tuibei Tu (推背图) ──
        results["bagua"] = _insert_seed(conn, "bagua",
            ["id", "name_zh", "symbol", "unicode", "wuxing", "direction", "nature", "family_member", "body_part", "description"], BAGUA)
        log.info("bagua: %d rows", results["bagua"])

        results["hexagram_64"] = _insert_seed(conn, "hexagram_64",
            ["id", "name_zh", "name_pinyin", "upper_trigram", "lower_trigram", "binary_code", "judgment", "image_text", "tuan_zhuan", "ji_xiong", "description"], HEXAGRAM_64)
        log.info("hexagram_64: %d rows", results["hexagram_64"])

        results["hexagram_lines"] = _insert_seed(conn, "hexagram_lines",
            ["hexagram_id", "line_number", "line_name", "yin_yang", "is_proper", "line_text", "line_meaning"], HEXAGRAM_LINES)
        log.info("hexagram_lines: %d rows", results["hexagram_lines"])

        results["tuibei_tu"] = _insert_seed(conn, "tuibei_tu",
            ["id", "image_name", "image_desc", "poem_chen", "poem_song", "commentary", "hexagram_id", "historical_era"], TUIBEI_TU)
        log.info("tuibei_tu: %d rows", results["tuibei_tu"])

        # ── Sparse reference tables ──
        results["wuge_pattern"] = _insert_seed(conn, "wuge_pattern",
            ["pattern_name", "condition_desc", "ji_xiong", "description"], WUGE_PATTERNS)
        log.info("wuge_pattern: %d rows", results["wuge_pattern"])

        results["wuge_health"] = _insert_seed(conn, "wuge_health",
            ["pattern_desc", "health_issue", "body_part", "severity"], WUGE_HEALTH)
        log.info("wuge_health: %d rows", results["wuge_health"])

        results["taiyi_number"] = _insert_seed(conn, "taiyi_number",
            ["id", "number", "ji_xiong", "description"], TAIYI_NUMBERS)
        log.info("taiyi_number: %d rows", results["taiyi_number"])

        results["tianyun"] = _insert_seed(conn, "tianyun",
            ["id", "name", "five_element", "description", "ji_xiong"], TIANYUN)
        log.info("tianyun: %d rows", results["tianyun"])

        results["jiugong"] = _insert_seed(conn, "jiugong",
            ["id", "name", "number", "direction", "five_element", "ji_xiong", "description"], JIUGONG)
        log.info("jiugong: %d rows", results["jiugong"])

        results["shensha"] = _insert_seed(conn, "shensha",
            ["id", "name", "type", "ji_xiong", "description", "rule_hint"], SHENSHA)
        log.info("shensha: %d rows", results["shensha"])

        results["surname_pattern"] = _insert_seed(conn, "surname_pattern",
            ["surname_strokes", "pattern_desc", "ji_xiong", "description"], SURNAME_PATTERNS)
        log.info("surname_pattern: %d rows", results["surname_pattern"])

        log.info("All seed data imported: %s", {k: v for k, v in results.items()})
        return results
    finally:
        conn.close()


# ── I Ching (周易) Divination Algorithms ──────────────────────────

def divine_by_coins(db_path: str = None) -> dict:
    """金钱卦 — simulate 3 coins tossed 6 times. Returns primary + mutual + changed hexagrams."""
    import random

    def toss_coins() -> tuple:
        """Return (line_type, is_changing). 3 heads=old yang(9,changing), 2 heads=young yang(7),
           2 tails=young yin(8), 3 tails=old yin(6,changing)."""
        coins = [random.choice([2, 3]) for _ in range(3)]  # 2=tail/yin, 3=head/yang
        total = sum(coins)
        if total == 6:
            return 1, True   # old yin → yang (changing)
        elif total == 7:
            return 1, False  # young yang
        elif total == 8:
            return 0, False  # young yin
        else:  # total == 9
            return 0, True   # old yang → yin (changing)

    lines = []
    changing = []
    for i in range(6):
        line, is_changing = toss_coins()
        lines.append(line)
        if is_changing:
            changing.append(i + 1)

    binary = "".join(str(b) for b in lines)
    primary_id = _binary_to_hexagram_id(binary, db_path)

    # Changed hexagram: flip changing lines
    changed_lines = lines[:]
    for pos in changing:
        changed_lines[pos - 1] = 1 - changed_lines[pos - 1]
    changed_binary = "".join(str(b) for b in changed_lines)
    changed_id = _binary_to_hexagram_id(changed_binary, db_path) if changing else None

    # Mutual hexagram: lines 2-4 as lower, 3-5 as upper
    mutual_lower = lines[1:4]
    mutual_upper = lines[2:5]
    mutual_binary = "".join(str(b) for b in mutual_lower + mutual_upper)
    mutual_id = _binary_to_hexagram_id(mutual_binary, db_path)

    conn = init_db(db_path=db_path, read_only=True)
    try:
        primary = _get_hexagram_full(conn, primary_id)
        mutual = _get_hexagram_full(conn, mutual_id) if mutual_id else None
        changed = _get_hexagram_full(conn, changed_id) if changed_id else None
    finally:
        conn.close()

    return {
        "method": "金钱卦",
        "primary": primary,
        "changing_lines": changing,
        "mutual": mutual,
        "changed": changed,
    }


def divine_by_numbers(a: int, b: int, c: int, db_path: str = None) -> dict:
    """数字卦 — 3 numbers generate upper trigram, lower trigram, and changing line.

    a = upper trigram index (1-8), b = lower trigram index (1-8), c = changing line (1-6)
    """
    upper = ((a - 1) % 8) + 1
    lower = ((b - 1) % 8) + 1
    change = ((c - 1) % 6) + 1

    upper_bin = _trigram_to_binary(upper)
    lower_bin = _trigram_to_binary(lower)
    binary = lower_bin + upper_bin
    primary_id = _binary_to_hexagram_id(binary, db_path)

    # Changed hexagram
    lines = [int(bit) for bit in binary]
    lines[change - 1] = 1 - lines[change - 1]
    changed_binary = "".join(str(b) for b in lines)
    changed_id = _binary_to_hexagram_id(changed_binary, db_path)

    # Mutual hexagram
    mutual_lower = binary[1:4]
    mutual_upper = binary[2:5]
    mutual_binary = mutual_lower + mutual_upper
    mutual_id = _binary_to_hexagram_id(mutual_binary, db_path)

    conn = init_db(db_path=db_path, read_only=True)
    try:
        primary = _get_hexagram_full(conn, primary_id)
        mutual = _get_hexagram_full(conn, mutual_id) if mutual_id else None
        changed = _get_hexagram_full(conn, changed_id) if changed_id else None
    finally:
        conn.close()

    return {
        "method": "数字卦",
        "primary": primary,
        "changing_lines": [change],
        "mutual": mutual,
        "changed": changed,
    }


def get_wrong_hexagram(hexagram_id: int, db_path: str = None) -> dict:
    """错卦 — flip all 6 lines (all yang→yin, all yin→yang)."""
    conn = init_db(db_path=db_path, read_only=True)
    try:
        row = conn.execute("SELECT binary_code FROM hexagram_64 WHERE id = ?", [hexagram_id]).fetchone()
        if not row:
            return None
        flipped = "".join("1" if b == "0" else "0" for b in row[0])
        wrong_id = _binary_to_hexagram_id(flipped, db_path)
        return _get_hexagram_full(conn, wrong_id)
    finally:
        conn.close()


def get_reverse_hexagram(hexagram_id: int, db_path: str = None) -> dict:
    """综卦 — reverse the hexagram upside down (reverse 6-line order)."""
    conn = init_db(db_path=db_path, read_only=True)
    try:
        row = conn.execute("SELECT binary_code FROM hexagram_64 WHERE id = ?", [hexagram_id]).fetchone()
        if not row:
            return None
        reversed_bin = row[0][::-1]
        rev_id = _binary_to_hexagram_id(reversed_bin, db_path)
        return _get_hexagram_full(conn, rev_id)
    finally:
        conn.close()


def interpret_hexagram(hexagram_id: int, changing_lines: list = None, db_path: str = None) -> dict:
    """Full interpretation of a hexagram reading: primary + mutual + changed + active line texts."""
    changing_lines = changing_lines or []
    conn = init_db(db_path=db_path, read_only=True)
    try:
        primary = _get_hexagram_full(conn, hexagram_id)
        if not primary:
            return {"error": f"Hexagram {hexagram_id} not found"}

        # Get changing line texts
        active_lines = []
        if changing_lines:
            for ln in changing_lines:
                line = conn.execute("""
                    SELECT line_name, line_text, line_meaning FROM hexagram_lines
                    WHERE hexagram_id = ? AND line_number = ?
                """, [hexagram_id, ln]).fetchone()
                if line:
                    active_lines.append({
                        "number": ln,
                        "name": line[0],
                        "text": line[1],
                        "meaning": line[2],
                    })

        # Compute changed hexagram
        binary = primary["binary_code"]
        changed = None
        if changing_lines:
            lines = [int(b) for b in binary]
            for ln in changing_lines:
                lines[ln - 1] = 1 - lines[ln - 1]
            changed_bin = "".join(str(b) for b in lines)
            changed_id = _binary_to_hexagram_id(changed_bin, db_path)
            changed = _get_hexagram_full(conn, changed_id)

        # Mutual hexagram
        mutual_lower = binary[1:4]
        mutual_upper = binary[2:5]
        mutual_bin = mutual_lower + mutual_upper
        mutual_id = _binary_to_hexagram_id(mutual_bin, db_path)
        mutual = _get_hexagram_full(conn, mutual_id)

        # Ti-Yong analysis (体用分析)
        if changing_lines:
            ti_yong = _analyze_ti_yong(binary, changing_lines, db_path)
        else:
            ti_yong = None

        return {
            "primary": primary,
            "changing_lines": active_lines,
            "mutual": mutual,
            "changed": changed,
            "ti_yong": ti_yong,
        }
    finally:
        conn.close()


# ── Tui Bei Tu (推背图) ────────────────────────────────────────────

def list_tuibei(era: str = None, db_path: str = None) -> list:
    """List all 60 Tui Bei Tu prophecies, optionally filtered by historical era."""
    conn = init_db(db_path=db_path, read_only=True)
    try:
        if era:
            rows = conn.execute("""
                SELECT t.id, t.image_name, t.image_desc, t.poem_chen, t.poem_song,
                       t.historical_era, t.hexagram_id, h.name_zh as hexagram_name,
                       h.ji_xiong as hexagram_ji_xiong
                FROM tuibei_tu t
                LEFT JOIN hexagram_64 h ON t.hexagram_id = h.id
                WHERE t.historical_era = ?
                ORDER BY t.id
            """, [era]).fetchall()
        else:
            rows = conn.execute("""
                SELECT t.id, t.image_name, t.image_desc, t.poem_chen, t.poem_song,
                       t.historical_era, t.hexagram_id, h.name_zh as hexagram_name,
                       h.ji_xiong as hexagram_ji_xiong
                FROM tuibei_tu t
                LEFT JOIN hexagram_64 h ON t.hexagram_id = h.id
                ORDER BY t.id
            """).fetchall()
        return [
            {"id": r[0], "image_name": r[1], "image_desc": r[2],
             "poem_chen": r[3], "poem_song": r[4], "historical_era": r[5],
             "hexagram_id": r[6], "hexagram_name": r[7], "hexagram_ji_xiong": r[8]}
            for r in rows
        ]
    finally:
        conn.close()


def get_tuibei(tuibei_id: int, db_path: str = None) -> dict:
    """Get full detail for a single Tui Bei Tu prophecy, with linked hexagram."""
    conn = init_db(db_path=db_path, read_only=True)
    try:
        row = conn.execute("""
            SELECT t.id, t.image_name, t.image_desc, t.poem_chen, t.poem_song,
                   t.commentary, t.historical_era, t.hexagram_id, t.ji_xiong
            FROM tuibei_tu t WHERE t.id = ?
        """, [tuibei_id]).fetchone()
        if not row:
            return {"error": f"Prophecy #{tuibei_id} not found"}

        result = {
            "id": row[0], "image_name": row[1], "image_desc": row[2],
            "poem_chen": row[3], "poem_song": row[4], "commentary": row[5],
            "historical_era": row[6], "hexagram_id": row[7], "ji_xiong": row[8],
        }

        # Enrich with full hexagram data
        if row[7]:
            hex_detail = interpret_hexagram(row[7], db_path=db_path)
            result["hexagram"] = {
                "id": row[7],
                "name": hex_detail["primary"]["name"],
                "judgment": hex_detail["primary"]["judgment"],
                "ji_xiong": hex_detail["primary"]["ji_xiong"],
                "binary_code": hex_detail["primary"]["binary_code"],
                "description": hex_detail["primary"]["description"],
            }

        return result
    finally:
        conn.close()


def _find_tuibei_for_hexagram(conn, hexagram_id: int) -> dict:
    """Find Tui Bei Tu prophecy for a hexagram, with fallback logic.

    Fallback chain: direct → wrong hexagram (错卦) → reverse hexagram (综卦).
    """
    # Direct match
    row = conn.execute(
        "SELECT id FROM tuibei_tu WHERE hexagram_id = ?", [hexagram_id]
    ).fetchone()
    if row:
        return {"match_type": "direct", "tuibei": get_tuibei(row[0])}

    # Get hexagram binary for computing wrong/reverse
    h = conn.execute(
        "SELECT id, binary_code FROM hexagram_64 WHERE id = ?", [hexagram_id]
    ).fetchone()
    if not h:
        return {"match_type": "none", "tuibei": None, "reason": "Hexagram not found"}
    binary = h[1]

    # Wrong hexagram (错卦): flip all 6 lines
    wrong_bin = "".join("1" if b == "0" else "0" for b in binary)
    wrong_row = conn.execute(
        "SELECT id FROM hexagram_64 WHERE binary_code = ?", [wrong_bin]
    ).fetchone()
    if wrong_row:
        tb = conn.execute(
            "SELECT id FROM tuibei_tu WHERE hexagram_id = ?", [wrong_row[0]]
        ).fetchone()
        if tb:
            hex_name = conn.execute(
                "SELECT name_zh FROM hexagram_64 WHERE id = ?", [wrong_row[0]]
            ).fetchone()[0]
            return {
                "match_type": "wrong_hexagram",
                "via_hexagram_id": wrong_row[0],
                "via_hexagram_name": hex_name,
                "tuibei": get_tuibei(tb[0]),
            }

    # Reverse hexagram (综卦): reverse binary string
    rev_bin = binary[::-1]
    rev_row = conn.execute(
        "SELECT id FROM hexagram_64 WHERE binary_code = ?", [rev_bin]
    ).fetchone()
    if rev_row:
        tb = conn.execute(
            "SELECT id FROM tuibei_tu WHERE hexagram_id = ?", [rev_row[0]]
        ).fetchone()
        if tb:
            hex_name = conn.execute(
                "SELECT name_zh FROM hexagram_64 WHERE id = ?", [rev_row[0]]
            ).fetchone()[0]
            return {
                "match_type": "reverse_hexagram",
                "via_hexagram_id": rev_row[0],
                "via_hexagram_name": hex_name,
                "tuibei": get_tuibei(tb[0]),
            }

    return {"match_type": "none", "tuibei": None,
            "reason": f"Hexagram #{hexagram_id} and its variants have no Tui Bei Tu mapping"}


def consult_tuibei(method: str = "random", hexagram_id: int = None,
                   db_path: str = None) -> dict:
    """Consult Tui Bei Tu (推背图) for a prophecy.

    Methods:
      - 'random': randomly draws one of 60 prophecies (抽签式)
      - 'hexagram': looks up prophecy by hexagram ID with fallback chain
        (direct → wrong hexagram → reverse hexagram)
      - 'index': get a specific prophecy by 象 number (1-60)

    Returns prophecy with linked hexagram details and interpretation.
    """
    import random

    conn = init_db(db_path=db_path, read_only=True)
    try:
        if method == "hexagram" and hexagram_id:
            result = _find_tuibei_for_hexagram(conn, hexagram_id)
            return {
                "consult_method": "hexagram",
                "query_hexagram_id": hexagram_id,
                **result,
            }

        elif method == "index" and hexagram_id:  # hexagram_id param reused as index
            return {
                "consult_method": "index",
                "tuibei": get_tuibei(hexagram_id),
            }

        else:  # random
            count = conn.execute("SELECT COUNT(*) FROM tuibei_tu").fetchone()[0]
            random_id = random.randint(1, count)
            return {
                "consult_method": "random",
                "tuibei": get_tuibei(random_id),
            }
    finally:
        conn.close()


def list_tuibei_eras(db_path: str = None) -> list:
    """List all historical eras covered by Tui Bei Tu with prophecy counts."""
    conn = init_db(db_path=db_path, read_only=True)
    try:
        rows = conn.execute("""
            SELECT historical_era, COUNT(*) as cnt
            FROM tuibei_tu
            GROUP BY historical_era
            ORDER BY MIN(id)
        """).fetchall()
        return [{"era": r[0], "count": r[1]} for r in rows]
    finally:
        conn.close()


# ── Internal helpers ──────────────────────────────────────────────

def _trigram_to_binary(trigram_id: int) -> str:
    """Convert trigram id (1-8) to 3-bit binary (bottom→top)."""
    mapping = {
        1: "111",  # 乾 ☰
        2: "011",  # 兑 ☱
        3: "101",  # 离 ☲
        4: "001",  # 震 ☳
        5: "110",  # 巽 ☴
        6: "010",  # 坎 ☵
        7: "100",  # 艮 ☶
        8: "000",  # 坤 ☷
    }
    return mapping.get(trigram_id, "000")


def _binary_to_hexagram_id(binary: str, db_path: str = None) -> int:
    """Look up hexagram id from 6-bit binary code."""
    conn = init_db(db_path=db_path, read_only=True)
    try:
        row = conn.execute(
            "SELECT id FROM hexagram_64 WHERE binary_code = ?", [binary]
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _get_hexagram_full(conn, hexagram_id: int) -> dict:
    """Get full hexagram data with lines."""
    row = conn.execute("""
        SELECT h.id, h.name_zh, h.name_pinyin, h.judgment, h.image_text,
               h.tuan_zhuan, h.ji_xiong, h.description, h.binary_code,
               u.name_zh as upper_name, l.name_zh as lower_name
        FROM hexagram_64 h
        JOIN bagua u ON h.upper_trigram = u.id
        JOIN bagua l ON h.lower_trigram = l.id
        WHERE h.id = ?
    """, [hexagram_id]).fetchone()
    if not row:
        return None

    lines = conn.execute("""
        SELECT line_number, line_name, yin_yang, is_proper, line_text, line_meaning
        FROM hexagram_lines WHERE hexagram_id = ? ORDER BY line_number
    """, [hexagram_id]).df().to_dict("records")

    return {
        "id": row[0],
        "name": row[1],
        "pinyin": row[2],
        "judgment": row[3],
        "image_text": row[4],
        "tuan_zhuan": row[5],
        "ji_xiong": row[6],
        "description": row[7],
        "binary_code": row[8],
        "upper_trigram": row[9],
        "lower_trigram": row[10],
        "lines": lines,
    }


def _analyze_ti_yong(binary: str, changing_lines: list, db_path: str = None) -> dict:
    """Analyze ti-yong (体用) relationship: which trigram is ti (subject) and which is yong (object)."""
    lower_trigram = binary[:3]
    upper_trigram = binary[3:]

    # Determine which trigram has the changing line
    has_change_lower = any(ln <= 3 for ln in changing_lines)
    has_change_upper = any(ln >= 4 for ln in changing_lines)

    if has_change_lower and not has_change_upper:
        ti = upper_trigram
        yong = lower_trigram
        ti_pos = "外卦(上)"
    elif has_change_upper and not has_change_lower:
        ti = lower_trigram
        yong = upper_trigram
        ti_pos = "内卦(下)"
    else:
        ti = lower_trigram
        yong = upper_trigram
        ti_pos = "体用同变"

    conn = init_db(db_path=db_path, read_only=True)
    try:
        ti_elem = conn.execute("SELECT wuxing FROM bagua WHERE id = ?",
                               [_binary_3bit_to_trigram_id(ti)]).fetchone()
        yong_elem = conn.execute("SELECT wuxing FROM bagua WHERE id = ?",
                                 [_binary_3bit_to_trigram_id(yong)]).fetchone()
    finally:
        conn.close()

    ti_element = ti_elem[0] if ti_elem else "未知"
    yong_element = yong_elem[0] if yong_elem else "未知"

    relation = _wuxing_relation(ti_element, yong_element)

    return {
        "ti_trigram": _binary_3bit_to_trigram_id(ti),
        "ti_element": ti_element,
        "ti_position": ti_pos,
        "yong_trigram": _binary_3bit_to_trigram_id(yong),
        "yong_element": yong_element,
        "relation": relation,
    }


def _binary_3bit_to_trigram_id(binary: str) -> int:
    """Convert 3-bit binary to bagua id."""
    mapping = {
        "111": 1, "011": 2, "101": 3, "001": 4,
        "110": 5, "010": 6, "100": 7, "000": 8,
    }
    return mapping.get(binary, 0)


def _wuxing_relation(ti: str, yong: str) -> str:
    """Return five element relationship description."""
    generation = {"木": "火", "火": "土", "土": "金", "金": "水", "水": "木"}
    control = {"木": "土", "土": "水", "水": "火", "火": "金", "金": "木"}

    if ti == yong:
        return "比和(吉)"
    if generation.get(ti) == yong:
        return "体生用(耗泄)"
    if generation.get(yong) == ti:
        return "用生体(大吉)"
    if control.get(ti) == yong:
        return "体克用(小吉)"
    if control.get(yong) == ti:
        return "用克体(凶)"

    return "未知"


# ── Name Scoring Engine (姓名评分引擎) ─────────────────────────────

# Gan-Zhi tables
HEAVENLY_STEMS = ["甲", "乙", "丙", "丁", "戊", "己", "庚", "辛", "壬", "癸"]
EARTHLY_BRANCHES = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]
GAN_WUXING = {"甲": "木", "乙": "木", "丙": "火", "丁": "火", "戊": "土",
              "己": "土", "庚": "金", "辛": "金", "壬": "水", "癸": "水"}
ZHI_WUXING = {"子": "水", "丑": "土", "寅": "木", "卯": "木", "辰": "土", "巳": "火",
              "午": "火", "未": "土", "申": "金", "酉": "金", "戌": "土", "亥": "水"}
ZODIAC_MAP = ["鼠", "牛", "虎", "兔", "龙", "蛇", "马", "羊", "猴", "鸡", "狗", "猪"]
ZODIAC_BY_BRANCH = dict(zip(EARTHLY_BRANCHES, ZODIAC_MAP))

# Month Gan-Zhi: based on year stem and solar month
MONTH_GAN_BY_YEAR = {
    "甲": ["丙", "丁", "戊", "己", "庚", "辛", "壬", "癸", "甲", "乙", "丙", "丁"],
    "乙": ["戊", "己", "庚", "辛", "壬", "癸", "甲", "乙", "丙", "丁", "戊", "己"],
    "丙": ["庚", "辛", "壬", "癸", "甲", "乙", "丙", "丁", "戊", "己", "庚", "辛"],
    "丁": ["壬", "癸", "甲", "乙", "丙", "丁", "戊", "己", "庚", "辛", "壬", "癸"],
    "戊": ["甲", "乙", "丙", "丁", "戊", "己", "庚", "辛", "壬", "癸", "甲", "乙"],
    "己": ["丙", "丁", "戊", "己", "庚", "辛", "壬", "癸", "甲", "乙", "丙", "丁"],
    "庚": ["戊", "己", "庚", "辛", "壬", "癸", "甲", "乙", "丙", "丁", "戊", "己"],
    "辛": ["庚", "辛", "壬", "癸", "甲", "乙", "丙", "丁", "戊", "己", "庚", "辛"],
    "壬": ["壬", "癸", "甲", "乙", "丙", "丁", "戊", "己", "庚", "辛", "壬", "癸"],
    "癸": ["甲", "乙", "丙", "丁", "戊", "己", "庚", "辛", "壬", "癸", "甲", "乙"],
}

# Hour branch by time (solar adjusted)
HOUR_BRANCH = {
    0: "子", 1: "丑", 2: "丑", 3: "寅", 4: "寅", 5: "卯", 6: "卯", 7: "辰", 8: "辰",
    9: "巳", 10: "巳", 11: "午", 12: "午", 13: "未", 14: "未", 15: "申", 16: "申",
    17: "酉", 18: "酉", 19: "戌", 20: "戌", 21: "亥", 22: "亥", 23: "子",
}

# Day stem to hour stem mapping (五鼠遁)
HOUR_GAN_BY_DAY = {
    "甲": ["甲", "乙", "丙", "丁", "戊", "己", "庚", "辛", "壬", "癸", "甲", "乙"],
    "乙": ["丙", "丁", "戊", "己", "庚", "辛", "壬", "癸", "甲", "乙", "丙", "丁"],
    "丙": ["戊", "己", "庚", "辛", "壬", "癸", "甲", "乙", "丙", "丁", "戊", "己"],
    "丁": ["庚", "辛", "壬", "癸", "甲", "乙", "丙", "丁", "戊", "己", "庚", "辛"],
    "戊": ["壬", "癸", "甲", "乙", "丙", "丁", "戊", "己", "庚", "辛", "壬", "癸"],
    "己": ["甲", "乙", "丙", "丁", "戊", "己", "庚", "辛", "壬", "癸", "甲", "乙"],
    "庚": ["丙", "丁", "戊", "己", "庚", "辛", "壬", "癸", "甲", "乙", "丙", "丁"],
    "辛": ["戊", "己", "庚", "辛", "壬", "癸", "甲", "乙", "丙", "丁", "戊", "己"],
    "壬": ["庚", "辛", "壬", "癸", "甲", "乙", "丙", "丁", "戊", "己", "庚", "辛"],
    "癸": ["壬", "癸", "甲", "乙", "丙", "丁", "戊", "己", "庚", "辛", "壬", "癸"],
}


def _stem_branch_index(stem: str, branch: str) -> int:
    """Get sexagenary cycle index (0-59) from stem+branch pair."""
    si = HEAVENLY_STEMS.index(stem)
    bi = EARTHLY_BRANCHES.index(branch)
    # Find index where stem and branch align
    for i in range(60):
        if i % 10 == si and i % 12 == bi:
            return i
    return 0


def _index_to_ganzhi(idx: int) -> str:
    """Convert sexagenary index (0-59) to stem+branch string."""
    return HEAVENLY_STEMS[idx % 10] + EARTHLY_BRANCHES[idx % 12]


# ── Five Element Assignment ─────────────────────────────────────────
# Maps Kangxi radical index → five element (金/木/水/火/土)
# Based on traditional naming (姓名学) principles:
#   1. Direct element radicals
#   2. Semantic associations (nature, color, season, direction)
#   3. Heavenly stems and earthly branches
_RADICAL_ELEMENT = {
    # ── 金 (Metal) ──
    167: "金",  # 金
    18: "金",   # 刀/刂
    19: "金",   # 力
    21: "金",   # 匕 (dagger)
    57: "金",   # 弓
    62: "金",   # 戈
    79: "金",   # 殳 (weapon)
    110: "金",  # 矛
    112: "金",  # 石 → actually 土 in many systems, but 金 for metal ores
    123: "金",  # 网/罒 → neutral, but net tools are metal-related
    150: "金",  # 谷 → neutral... skip
    154: "金",  # 貝/贝 (currency → gold/metal in wuxing)
    159: "金",  # 車/车
    160: "金",  # 辛 (heavenly stem, metal)
    164: "金",  # 酉 (earthly branch, metal)
    169: "金",  # 門/门 → neutral, skip... actually 金 for metal gates
    # ── 木 (Wood) ──
    75: "木",   # 木
    64: "木",   # 手/扌 → neutral... skip (hand has no element in most systems)
    94: "木",   # 犬/犭 → neutral... skip
    100: "木",  # 生 (life → wood)
    115: "木",  # 禾 (grain → wood)
    118: "木",  # 竹 (bamboo → wood)
    119: "木",  # 米 (rice → wood)
    120: "木",  # 糸/纟 (silk → wood)
    140: "木",  # 艸/艹 (grass → wood)
    145: "木",  # 衣/衤 (clothing → wood)
    174: "木",  # 青 (green → wood)
    199: "木",  # 麥/麦 (wheat → wood)
    200: "木",  # 麻 (hemp → wood)
    # ── 水 (Water) ──
    15: "水",   # 冫 (ice → water)
    47: "水",   # 巛/川 (river → water)
    85: "水",   # 水/氵
    137: "水",  # 舟 (boat → water)
    173: "水",  # 雨 (rain → water)
    182: "水",  # 風/风 (wind → associated with water/movement)
    195: "水",  # 魚/鱼 (fish → water)
    203: "水",  # 黑 (black → water color)
    # ── 火 (Fire) ──
    61: "火",   # 心/忄 (heart → fire)
    72: "火",   # 日 (sun → fire)
    86: "火",   # 火/灬
    147: "火",  # 見/见 (sight/light → fire)
    155: "火",  # 赤 (red → fire)
    207: "火",  # 鼓 → neutral... skip
    # ── 土 (Earth) ──
    27: "土",   # 厂 (cliff → earth)
    32: "土",   # 土
    33: "土",   # 士 (scholar → earth, 土 related)
    38: "土",   # 女 → neutral... skip
    46: "土",   # 山 (mountain → earth)
    53: "土",   # 广 → neutral... skip
    96: "土",   # 玉/王 (jade → earth)
    97: "土",   # 瓜 → neutral... skip
    98: "土",   # 瓦 (clay/ceramic → earth)
    102: "土",  # 田 (field → earth)
    113: "土",  # 示/礻 → neutral... skip
    157: "土",  # 足 → neutral... skip
    170: "土",  # 阜/阝 (mound → earth)
    198: "土",  # 鹿 → neutral... skip
}


def _get_element_from_ids(decomposition: str) -> Optional[str]:
    """Try to determine five element from IDS decomposition by matching known components."""
    if not decomposition:
        return None
    # Known element-bearing components (from component_element + extensions)
    _IDS_ELEMENTS = {
        "金": "金", "钅": "金", "釒": "金", "刀": "金", "刂": "金", "戈": "金",
        "辛": "金", "酉": "金", "申": "金", "庚": "金", "貝": "金", "贝": "金",
        "木": "木", "禾": "木", "竹": "木", "艹": "木", "艸": "木", "糸": "木",
        "纟": "木", "衤": "木", "生": "木", "青": "木", "林": "木", "森": "木",
        "水": "水", "氵": "水", "冫": "水", "雨": "水", "魚": "水", "鱼": "水",
        "川": "水", "泉": "水", "冬": "水", "北": "水", "子": "水", "亥": "水",
        "火": "火", "灬": "火", "日": "火", "心": "火", "忄": "火", "赤": "火",
        "光": "火", "夏": "火", "丙": "火", "丁": "火", "午": "火", "巳": "火",
        "土": "土", "山": "土", "石": "土", "玉": "土", "王": "土", "田": "土",
        "阝": "土", "戊": "土", "己": "土", "辰": "土", "戌": "土", "丑": "土",
        "未": "土", "黄": "土", "中": "土",
    }
    # Check each character in the decomposition
    found = {}
    for ch in decomposition:
        if ch in _IDS_ELEMENTS:
            elem = _IDS_ELEMENTS[ch]
            found[elem] = found.get(elem, 0) + 1
    if found:
        return max(found, key=found.get)
    return None


def assign_char_elements(db_path: str = None) -> dict:
    """Populate five_element for all kangxi_chars using radical + IDS analysis.

    Strategy (priority order):
      1. Radical-based: map radical_index → five_element via _RADICAL_ELEMENT
      2. IDS decomposition: match known element-bearing components
      3. Direct element radical always takes priority over IDS match
    """
    conn = init_db(db_path=db_path, read_only=False)
    try:
        # Ensure element_source column exists
        try:
            conn.execute("ALTER TABLE kangxi_chars ADD COLUMN element_source VARCHAR(16)")
        except Exception:
            pass  # column already exists

        # Get all chars with null/no five_element
        rows = conn.execute("""
            SELECT id, char_t, radical_index, decomposition
            FROM kangxi_chars
            WHERE five_element IS NULL OR five_element = ''
        """).fetchall()

        updates = []
        stats = {"radical": 0, "ids": 0, "unmatched": 0}

        for row_id, char, rad_idx, decomp in rows:
            element = None
            source = None

            # Strategy 1: Radical-based
            if rad_idx and rad_idx in _RADICAL_ELEMENT:
                element = _RADICAL_ELEMENT[rad_idx]
                source = "radical"

            # Strategy 2: IDS decomposition (if radical didn't match)
            if not element and decomp:
                element = _get_element_from_ids(decomp)
                if element:
                    source = "ids"

            if element:
                updates.append((element, source, row_id))
                if source == "radical":
                    stats["radical"] += 1
                else:
                    stats["ids"] += 1
            else:
                stats["unmatched"] += 1

        # Batch update
        if updates:
            conn.executemany(
                "UPDATE kangxi_chars SET five_element = ?, element_source = ? WHERE id = ?",
                updates,
            )
        conn.commit()
        return {
            "total_processed": len(rows),
            "assigned_radical": stats["radical"],
            "assigned_ids": stats["ids"],
            "unmatched": stats["unmatched"],
        }
    finally:
        conn.close()


def calculate_bazi(year: int, month: int, day: int, hour: int = 12) -> dict:
    """Calculate BaZi (八字) four pillars using astronomical solar terms.

    Uses the Chinese calendar module for accurate solar term dates
    (computed via Sun's ecliptic longitude, not hardcoded approximations).

    Returns dict with pillars, wuxing and shishen analysis.
    """
    import datetime as dt
    from name_data.calendar import year_ganzhi, month_ganzhi, day_ganzhi

    target_date = dt.date(year, month, day)

    # ── Year & Month Pillars (using astronomical solar terms) ──
    year_gz = year_ganzhi(year, month, day)
    year_gan = year_gz[0]
    year_zhi = year_gz[1]

    month_gz = month_ganzhi(year, month, day)
    month_gan = month_gz[0]
    month_zhi = month_gz[1]

    # ── Day Pillar (60-day cycle, independent of lunar calendar) ──
    day_gz = day_ganzhi(target_date)
    day_gan = day_gz[0]
    day_zhi = day_gz[1]

    # ── Hour Pillar ──
    hour_zhi = HOUR_BRANCH.get(hour, "子")
    hz_idx = EARTHLY_BRANCHES.index(hour_zhi)
    hour_gan = HOUR_GAN_BY_DAY[day_gan][hz_idx]
    hour_gz = hour_gan + hour_zhi

    pillars = {
        "year": year_gz, "month": month_gz, "day": day_gz, "hour": hour_gz,
    }

    # Wuxing analysis
    elements = []
    for p in pillars.values():
        elements.append(GAN_WUXING[p[0]])
        elements.append(ZHI_WUXING[p[1]])

    wuxing_count = {"木": 0, "火": 0, "土": 0, "金": 0, "水": 0}
    for e in elements:
        wuxing_count[e] = wuxing_count.get(e, 0) + 1

    # Day master's element
    day_master = GAN_WUXING[day_gan]

    # Find favorable element (simplified: balance approach)
    sorted_elements = sorted(wuxing_count.items(), key=lambda x: x[1])
    favorable = sorted_elements[0][0] if sorted_elements[0][1] < 2 else day_master

    # Zodiac
    zodiac = ZODIAC_BY_BRANCH[year_zhi]

    return {
        "pillars": pillars,
        "day_master": day_master,
        "day_master_gan": day_gan,
        "wuxing_count": wuxing_count,
        "favorable_element": favorable,
        "zodiac": zodiac,
        "zodiac_branch": year_zhi,
    }


def _char_strokes(conn, char: str) -> int:
    """Get Kangxi stroke count for a character, with correction lookup."""
    # Check stroke correction table first
    corr = conn.execute(
        "SELECT correct_strokes FROM stroke_correction WHERE char_t = ?", [char]
    ).fetchone()
    if corr:
        return corr[0]

    row = conn.execute(
        "SELECT total_strokes FROM kangxi_chars WHERE char_t = ?", [char]
    ).fetchone()
    return row[0] if row else 0


def _grid_to_element(number: int) -> str:
    """Convert wuge number to five element (个位数定五行)."""
    digit = (number - 1) % 10 + 1
    if digit in (1, 2):
        return "木"
    elif digit in (3, 4):
        return "火"
    elif digit in (5, 6):
        return "土"
    elif digit in (7, 8):
        return "金"
    else:
        return "水"


def calculate_wuge(surname: str, given_name: str, db_path: str = None) -> dict:
    """Calculate Wu Ge (五格) from surname and given name using Kangxi strokes."""
    conn = init_db(db_path=db_path, read_only=True)
    try:
        # Get corrected stroke counts
        s_strokes = [_char_strokes(conn, c) for c in surname]
        g_strokes = [_char_strokes(conn, c) for c in given_name]

        total_s = sum(s_strokes)
        total_g = sum(g_strokes)

        # 天格: surname strokes + 1 (or compound surname total)
        tiange = total_s + 1
        if len(surname) == 2:  # compound surname
            tiange = total_s

        # 人格: surname last char + given name first char
        renge = (s_strokes[-1] if s_strokes else 1) + (g_strokes[0] if g_strokes else 1)

        # 地格: given name strokes (single=+1, double=sum)
        dige = total_g if len(given_name) >= 2 else total_g + 1

        # 外格: given name last char + 1 (single) or surname first char + given name last char
        if len(given_name) == 1:
            waige = g_strokes[0] + 1 if g_strokes else 2
        else:
            waige = (s_strokes[0] if s_strokes else 1) + (g_strokes[-1] if len(g_strokes) > 1 else g_strokes[0])

        # 总格: all strokes
        zongge = total_s + total_g

        # Lookup 81 number meanings
        def lookup_81(num):
            row = conn.execute(
                "SELECT ji_xiong, description, summary, five_element, base_score FROM wuge_81 WHERE number = ?",
                [num]
            ).fetchone()
            if row:
                return {"ji_xiong": row[0], "description": row[1], "summary": row[2],
                        "five_element": row[3], "base_score": row[4]}
            return {"ji_xiong": "未知", "description": "", "summary": "", "five_element": "", "base_score": 50}

        grids = {
            "天格": {"number": tiange, "element": _grid_to_element(tiange), **lookup_81(tiange)},
            "人格": {"number": renge, "element": _grid_to_element(renge), **lookup_81(renge)},
            "地格": {"number": dige, "element": _grid_to_element(dige), **lookup_81(dige)},
            "外格": {"number": waige, "element": _grid_to_element(waige), **lookup_81(waige)},
            "总格": {"number": zongge, "element": _grid_to_element(zongge), **lookup_81(zongge)},
        }

        return {
            "grids": grids,
            "surname_strokes": dict(zip(list(surname), s_strokes)),
            "given_strokes": dict(zip(list(given_name), g_strokes)),
        }
    finally:
        conn.close()


def evaluate_sancai(tiange_element: str, renge_element: str, dige_element: str,
                    db_path: str = None) -> dict:
    """Evaluate Sancai (三才) configuration."""
    conn = init_db(db_path=db_path, read_only=True)
    try:
        row = conn.execute("""
            SELECT ji_xiong, description, base_score FROM sancai_config
            WHERE heaven_element = ? AND man_element = ? AND earth_element = ?
        """, [tiange_element, renge_element, dige_element]).fetchone()
        if row:
            return {"ji_xiong": row[0], "description": row[1], "base_score": row[2]}
        # Fallback: deduce from wuxing relationships
        return _deduce_sancai(tiange_element, renge_element, dige_element)
    finally:
        conn.close()


def _deduce_sancai(tian: str, ren: str, di: str) -> dict:
    """Deduce sancai score from five element relationships."""
    gen = {"木": "火", "火": "土", "土": "金", "金": "水", "水": "木"}
    ctrl = {"木": "土", "土": "水", "水": "火", "火": "金", "金": "木"}

    score = 60
    desc = ""

    if gen.get(tian) == ren:
        score += 20
        desc += "天格生人格，长辈助力。"
    elif gen.get(ren) == tian:
        score += 10
        desc += "人格生天格，奉养长辈。"
    elif ctrl.get(tian) == ren:
        score -= 20
        desc += "天格克人格，上压下受。"
    elif ctrl.get(ren) == tian:
        score += 5
        desc += "人格克天格，独立自主。"
    else:
        score += 15
        desc += "天格人格比和，协调。"

    if gen.get(ren) == di:
        score += 20
        desc += "人格生地格，下属得力。"
    elif gen.get(di) == ren:
        score += 10
        desc += "地格生人格，家庭和谐。"
    elif ctrl.get(ren) == di:
        score += 5
        desc += "人格克地格，管教有方。"
    elif ctrl.get(di) == ren:
        score -= 20
        desc += "地格克人格，家庭拖累。"
    else:
        score += 15
        desc += "人格地格比和，稳定。"

    ji = "大吉" if score >= 80 else "吉" if score >= 60 else "中吉" if score >= 40 else "凶"
    return {"ji_xiong": ji, "description": desc, "base_score": max(0, min(100, score))}


def evaluate_zodiac(zodiac: str, given_name: str, db_path: str = None) -> dict:
    """Evaluate zodiac compatibility of name characters."""
    conn = init_db(db_path=db_path, read_only=True)
    try:
        score = 50
        details = []
        for char in given_name:
            # Check zodiac components
            rows = conn.execute("""
                SELECT component, favorability, reason, score_mod
                FROM zodiac_component WHERE zodiac = ?
            """, [zodiac]).fetchall()

            for comp, fav, reason, mod in rows:
                if comp in char:
                    if fav == "喜用":
                        score += mod
                        details.append(f"「{char}」含「{comp}」：{fav}({reason})")
                    elif fav == "忌用":
                        score += mod
                        details.append(f"「{char}」含「{comp}」：{fav}({reason})")

        return {
            "zodiac": zodiac,
            "score": max(0, min(100, score)),
            "details": details[:10],
        }
    finally:
        conn.close()


def evaluate_phonetic(surname: str, given_name: str, db_path: str = None) -> dict:
    """Evaluate phonetic qualities of a name."""
    conn = init_db(db_path=db_path, read_only=True)
    try:
        def get_tones(chars):
            tones = []
            for c in chars:
                row = conn.execute(
                    "SELECT tone, pinyin FROM kangxi_chars WHERE char_t = ?", [c]
                ).fetchone()
                if row and row[0]:
                    tones.append(row[0])
                else:
                    tones.append(0)
            return tones

        st = get_tones(surname)
        gt = get_tones(given_name)
        all_tones = st + gt

        score = 60
        notes = []

        # Check for all same tone
        if len(set(all_tones)) == 1 and len(all_tones) >= 2:
            score -= 20
            notes.append("全名同声调，缺乏音韵变化。")

        # Check for good tonal variation
        unique = len(set(all_tones))
        if unique >= 3:
            score += 15
            notes.append("声调变化丰富，悦耳动听。")
        elif unique >= 2:
            score += 5
            notes.append("声调有一定变化。")

        # Check for rising-falling balance (平仄)
        ping = sum(1 for t in all_tones if t in (1, 2))
        ze = len(all_tones) - ping
        if ping > 0 and ze > 0:
            score += 10
            notes.append("平仄相间，音律协调。")
        else:
            score -= 10
            notes.append("全平或全仄，缺少韵律感。")

        return {
            "tones": all_tones,
            "score": max(0, min(100, score)),
            "notes": notes,
        }
    finally:
        conn.close()


def evaluate_meaning(surname: str, given_name: str, db_path: str = None) -> dict:
    """Evaluate meaning/auspiciousness of name characters."""
    conn = init_db(db_path=db_path, read_only=True)
    try:
        score = 50
        details = []
        for char in given_name:
            row = conn.execute(
                "SELECT ji_xiong, meaning FROM kangxi_chars WHERE char_t = ?", [char]
            ).fetchone()
            if row:
                jx = row[0]
                meaning = row[1]
                if jx == "吉" or jx == "大吉":
                    score += 15
                elif jx == "凶" or jx == "大凶":
                    score -= 20
                details.append({"char": char, "ji_xiong": jx or "未知", "meaning": (meaning or "")[:100]})
            else:
                details.append({"char": char, "ji_xiong": "未知", "meaning": ""})

        return {
            "score": max(0, min(100, score)),
            "chars": details,
        }
    finally:
        conn.close()


def score_name(
    surname: str,
    given_name: str,
    birth_year: int = None,
    birth_month: int = None,
    birth_day: int = None,
    birth_hour: int = 12,
    gender: str = "男",
    db_path: str = None,
) -> dict:
    """Comprehensive name scoring with all factors weighted.

    Weights: wuge(30%) + sancai(20%) + bazi(20%) + zodiac(10%) + phonetic(10%) + meaning(10%)
    """
    conn = init_db(db_path=db_path, read_only=True)

    results = {"surname": surname, "given_name": given_name, "gender": gender}

    try:
        # ── 1. Bazi (八字) ──
        if birth_year and birth_month and birth_day:
            bazi = calculate_bazi(birth_year, birth_month, birth_day, birth_hour)
            results["bazi"] = bazi
            zodiac = bazi["zodiac"]
            favorable = bazi["favorable_element"]
        else:
            bazi = None
            zodiac = "鼠"
            favorable = None

        # ── 2. Wu Ge (五格) ──
        wuge = calculate_wuge(surname, given_name, db_path)
        results["wuge"] = wuge

        # Calculate wuge score
        wuge_scores = [g["base_score"] for g in wuge["grids"].values()]
        wuge_score = sum(wuge_scores) / len(wuge_scores)

        # ── 3. Sancai (三才) ──
        sancai = evaluate_sancai(
            wuge["grids"]["天格"]["element"],
            wuge["grids"]["人格"]["element"],
            wuge["grids"]["地格"]["element"],
            db_path,
        )
        results["sancai"] = sancai
        sancai_score = sancai["base_score"]

        # ── 4. Zodiac (生肖) ──
        zodiac_result = evaluate_zodiac(zodiac, given_name, db_path)
        results["zodiac"] = zodiac_result
        zodiac_score = zodiac_result["score"]

        # ── 5. Phonetic (音调) ──
        phonetic = evaluate_phonetic(surname, given_name, db_path)
        results["phonetic"] = phonetic
        phonetic_score = phonetic["score"]

        # ── 6. Meaning (字义) ──
        meaning = evaluate_meaning(surname, given_name, db_path)
        results["meaning"] = meaning
        meaning_score = meaning["score"]

        # ── Weighted total ──
        total = (
            wuge_score * 0.30 +
            sancai_score * 0.20 +
            (70 if favorable else 50) * 0.20 +  # bazi placeholder
            zodiac_score * 0.10 +
            phonetic_score * 0.10 +
            meaning_score * 0.10
        )

        results["scores"] = {
            "total": round(total, 1),
            "wuge": round(wuge_score, 1),
            "sancai": round(sancai_score, 1),
            "bazi": round(70 if favorable else 50, 1),
            "zodiac": round(zodiac_score, 1),
            "phonetic": round(phonetic_score, 1),
            "meaning": round(meaning_score, 1),
        }

        # ── Overall verdict ──
        if total >= 85:
            results["verdict"] = "大吉 — 非常优秀的名字，各方面都非常协调。"
        elif total >= 75:
            results["verdict"] = "吉 — 整体较好的名字，有少数可改进之处。"
        elif total >= 65:
            results["verdict"] = "中吉 — 中等偏上的名字，可考虑优化。"
        elif total >= 55:
            results["verdict"] = "平 — 名字一般，建议优化。"
        else:
            results["verdict"] = "凶 — 名字不理想，建议重新取名。"

        return results
    finally:
        conn.close()


def score_name_batch(names: list, **kwargs) -> list:
    """Score multiple names and return sorted by total score."""
    results = [score_name(**{**kwargs, "surname": s, "given_name": g}) for s, g in names]
    results.sort(key=lambda r: r["scores"]["total"], reverse=True)
    return results


# ── Name Generation ────────────────────────────────────────────────

# Five-element generation cycle: each element generates the next
_WUXING_GENERATES = {"木": "火", "火": "土", "土": "金", "金": "水", "水": "木"}
# Reverse: which element generates me
_WUXING_GENERATED_BY = {v: k for k, v in _WUXING_GENERATES.items()}


def _get_auspicious_numbers(conn) -> set:
    """Return set of numbers (1-81) rated 大吉 or 吉."""
    rows = conn.execute(
        "SELECT number FROM wuge_81 WHERE ji_xiong IN ('大吉', '吉')"
    ).fetchall()
    return {r[0] for r in rows}


def _get_acceptable_numbers(conn) -> set:
    """Return set of numbers rated 大吉, 吉, or 中吉."""
    rows = conn.execute(
        "SELECT number FROM wuge_81 WHERE ji_xiong IN ('大吉', '吉', '中吉')"
    ).fetchall()
    return {r[0] for r in rows}


# BMP CJK range: U+4E00 – U+9FFF (CJK Unified Ideographs)
# Plus CJK Extension A: U+3400 – U+4DBF
_BMP_FILTER = "unicode IS NOT NULL AND unicode >= 'U+3400' AND unicode <= 'U+9FFF'"
# Only characters with defined pronunciation and meaning (eliminates components/rare variants)
_CHAR_QUALITY_FILTER = "pinyin IS NOT NULL AND pinyin != '' AND meaning IS NOT NULL AND meaning != ''"
# Exclude stroke components: given-name characters need at least 3 strokes
_NAME_STROKE_MIN = "total_strokes >= 3"
# Combined filter for name-worthy characters
_NAME_CHAR_FILTER = f"{_BMP_FILTER} AND {_CHAR_QUALITY_FILTER} AND {_NAME_STROKE_MIN}"


def _rank_stroke_pairs(pairs: list, conn, s_total: int, s_first: int, s_last: int) -> list:
    """Rank stroke pairs by overall WuGe quality (sum of base_scores for all 5 grids)."""
    scored = []
    for g1, g2 in pairs:
        tiange = s_total + 1
        renge = s_last + g1
        dige = g1 + g2
        waige = s_first + g2
        zongge = s_total + g1 + g2

        total_score = 0
        for num in [tiange, renge, dige, waige, zongge]:
            row = conn.execute(
                "SELECT base_score FROM wuge_81 WHERE number = ?", [num]
            ).fetchone()
            if row:
                total_score += row[0]
        scored.append(((g1, g2), total_score))

    scored.sort(key=lambda x: x[1], reverse=True)
    return [p for p, _ in scored]


def _compute_stroke_pairs(s_total: int, s_first: int, s_last: int,
                          auspicious: set, acceptable: set) -> list:
    """Compute compatible (G1, G2) stroke pairs for a 2-char given name."""
    compatible = []
    for g1 in range(1, 32):
        renge = s_last + g1
        if renge not in auspicious:
            continue
        for g2 in range(1, 32):
            dige = g1 + g2
            if dige not in auspicious:
                continue
            zongge = s_total + g1 + g2
            if zongge not in acceptable:
                continue
            waige = s_first + g2
            if waige not in acceptable:
                continue
            compatible.append((g1, g2))
    return compatible


def _compute_stroke_singles(s_total: int, s_last: int,
                            auspicious: set, acceptable: set) -> list:
    """Compute compatible single-char given name strokes."""
    compatible = []
    for g1 in range(1, 32):
        renge = s_last + g1
        if renge not in auspicious:
            continue
        dige = g1 + 1
        if dige not in acceptable:
            continue
        zongge = s_total + g1
        if zongge not in acceptable:
            continue
        compatible.append(g1)
    return compatible


def _get_chars_by_strokes_and_element(conn, strokes: int, primary_element: str,
                                       limit: int = 40) -> list:
    """Get name-suitable BMP CJK characters with given strokes, preferring primary element."""
    generator = _WUXING_GENERATED_BY.get(primary_element, primary_element)
    rows = conn.execute(f"""
        SELECT char_t, total_strokes, five_element, ji_xiong, pinyin, tone, meaning, radical
        FROM kangxi_chars
        WHERE total_strokes = ?
          AND is_name_char = true
          AND {_NAME_CHAR_FILTER}
          AND (ji_xiong IS NULL OR ji_xiong NOT IN ('凶', '大凶'))
          AND (five_element = ? OR five_element = ?)
        ORDER BY
            CASE five_element WHEN ? THEN 0 ELSE 1 END,
            CASE ji_xiong WHEN '大吉' THEN 0 WHEN '吉' THEN 1 WHEN '中吉' THEN 2 ELSE 3 END,
            frequency DESC NULLS LAST
        LIMIT ?
    """, [strokes, primary_element, generator, primary_element, limit]).fetchall()
    return [
        {"char": r[0], "strokes": r[1], "element": r[2], "ji_xiong": r[3],
         "pinyin": r[4], "tone": r[5], "meaning": r[6], "radical": r[7]}
        for r in rows
    ]


def _get_chars_by_strokes_broad(conn, strokes: int, limit: int = 30) -> list:
    """Get name-suitable BMP CJK characters with given strokes, any element."""
    rows = conn.execute(f"""
        SELECT char_t, total_strokes, five_element, ji_xiong, pinyin, tone, meaning, radical
        FROM kangxi_chars
        WHERE total_strokes = ?
          AND is_name_char = true
          AND {_NAME_CHAR_FILTER}
          AND (ji_xiong IS NULL OR ji_xiong NOT IN ('凶', '大凶'))
        ORDER BY
            CASE ji_xiong WHEN '大吉' THEN 0 WHEN '吉' THEN 1 WHEN '中吉' THEN 2 ELSE 3 END,
            frequency DESC NULLS LAST
        LIMIT ?
    """, [strokes, limit]).fetchall()
    return [
        {"char": r[0], "strokes": r[1], "element": r[2], "ji_xiong": r[3],
         "pinyin": r[4], "tone": r[5], "meaning": r[6], "radical": r[7]}
        for r in rows
    ]


def generate_names(
    surname: str,
    birth_year: int,
    birth_month: int,
    birth_day: int,
    birth_hour: int = 12,
    gender: str = "男",
    num_names: int = 30,
    db_path: str = None,
) -> dict:
    """Generate auspicious name candidates based on BaZi and WuGe compatibility.

    Algorithm:
      1. Calculate BaZi → favorable element (喜用神) + zodiac
      2. Get surname Kangxi strokes
      3. Compute compatible stroke pairs/singles for good WuGe grids
      4. Rank stroke pairs by total WuGe quality, take top 20 for diversity
      5. Query BMP CJK characters (U+3400-U+9FFF) by element + stroke
      6. Generate 2-char + 1-char given name candidates (limited per pair)
      7. Score all candidates with full scoring engine
      8. Return top N ranked by total score
    """
    conn = init_db(db_path=db_path, read_only=True)

    try:
        # ── 1. BaZi analysis ──
        bazi = calculate_bazi(birth_year, birth_month, birth_day, birth_hour)
        favorable = bazi["favorable_element"]
        zodiac = bazi["zodiac"]

        # ── 2. Surname strokes ──
        s_strokes = [_char_strokes(conn, c) for c in surname]
        s_total = sum(s_strokes)
        s_first = s_strokes[0]
        s_last = s_strokes[-1]

        # ── 3. Auspicious numbers ──
        auspicious = _get_auspicious_numbers(conn)
        acceptable = _get_acceptable_numbers(conn)

        # ── 4. Compatible stroke combinations ──
        all_pairs = _compute_stroke_pairs(s_total, s_first, s_last, auspicious, acceptable)
        all_singles = _compute_stroke_singles(s_total, s_last, auspicious, acceptable)

        # Rank pairs by quality, take top 25 for diversity
        ranked_pairs = _rank_stroke_pairs(all_pairs, conn, s_total, s_first, s_last)
        top_pairs = ranked_pairs[:25]

        # ── 5. Query characters by stroke ──
        g1_strokes = set()
        g2_strokes = set()
        for g1, g2 in top_pairs:
            g1_strokes.add(g1)
            g2_strokes.add(g2)
        for g1 in all_singles:
            g1_strokes.add(g1)

        char_by_stroke = {}
        for st in g1_strokes | g2_strokes:
            chars = _get_chars_by_strokes_and_element(conn, st, favorable, limit=25)
            if len(chars) < 8:
                extra = _get_chars_by_strokes_broad(conn, st, limit=20)
                seen = {c["char"] for c in chars}
                for c in extra:
                    if c["char"] not in seen:
                        chars.append(c)
            char_by_stroke[st] = chars

        # ── 6. Generate candidates ──
        candidates = []

        # 6a. Two-character given names (limit 6 first × 5 second per pair)
        for g1_st, g2_st in top_pairs:
            chars1 = char_by_stroke.get(g1_st, [])[:6]
            chars2 = char_by_stroke.get(g2_st, [])[:5]
            for c1 in chars1:
                for c2 in chars2:
                    if c1["char"] == c2["char"]:
                        continue
                    given = c1["char"] + c2["char"]
                    candidates.append({
                        "surname": surname,
                        "given_name": given,
                        "name_type": "2字名",
                        "stroke_pair": (g1_st, g2_st),
                        "preview": f"{surname}{given}",
                    })

        # 6b. One-character given names (limit 8 chars per stroke)
        for g1_st in all_singles:
            for c1 in char_by_stroke.get(g1_st, [])[:8]:
                candidates.append({
                    "surname": surname,
                    "given_name": c1["char"],
                    "name_type": "1字名",
                    "stroke_pair": (g1_st,),
                    "preview": f"{surname}{c1['char']}",
                })

        # Deduplicate by given_name
        seen_names = set()
        unique_candidates = []
        for c in candidates:
            if c["given_name"] not in seen_names:
                seen_names.add(c["given_name"])
                unique_candidates.append(c)

        # ── 7. Score candidates ──
        kwargs = dict(
            birth_year=birth_year, birth_month=birth_month,
            birth_day=birth_day, birth_hour=birth_hour, gender=gender,
        )
        scored = []
        for c in unique_candidates:
            result = score_name(surname=c["surname"], given_name=c["given_name"],
                               db_path=db_path, **kwargs)
            scored.append({
                "surname": c["surname"],
                "given_name": c["given_name"],
                "full_name": c["preview"],
                "name_type": c["name_type"],
                "stroke_pair": c["stroke_pair"],
                "total_score": result["scores"]["total"],
                "verdict": result["verdict"],
                "scores": result["scores"],
                "wuge_grids": {
                    k: {"number": v["number"], "element": v["element"],
                        "ji_xiong": v["ji_xiong"], "summary": v["summary"]}
                    for k, v in result["wuge"]["grids"].items()
                },
                "sancai": result.get("sancai", {}),
            })

        scored.sort(key=lambda r: r["total_score"], reverse=True)

        # Build diverse result: max 4 candidates per stroke pair
        target = max(num_names, 30)
        diverse = []
        pair_counts = {}
        for c in scored:
            pair_key = c["stroke_pair"]
            count = pair_counts.get(pair_key, 0)
            if count < 4:
                diverse.append(c)
                pair_counts[pair_key] = count + 1
            if len(diverse) >= target:
                break

        return {
            "surname": surname,
            "gender": gender,
            "bazi_summary": {
                "pillars": bazi["pillars"],
                "day_master": bazi["day_master"],
                "wuxing_count": bazi["wuxing_count"],
                "favorable_element": favorable,
                "zodiac": zodiac,
            },
            "stroke_analysis": {
                "surname_chars": dict(zip(list(surname), s_strokes)),
                "surname_total": s_total,
                "surname_first": s_first,
                "surname_last": s_last,
                "compatible_double_pairs": len(all_pairs),
                "selected_pairs": len(top_pairs),
                "compatible_single_strokes": len(all_singles),
            },
            "total_candidates": len(scored),
            "candidates": diverse,
        }
    finally:
        conn.close()


# ── Daily Fortune (每日运势) ──────────────────────────────────────────

def _ensure_daily_fortune_table(conn) -> None:
    """Create daily_fortune table if not exists."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_fortune (
            date            DATE PRIMARY KEY,
            year_ganzhi     VARCHAR(4) NOT NULL,
            month_ganzhi    VARCHAR(4) NOT NULL,
            day_ganzhi      VARCHAR(4) NOT NULL,
            current_term    VARCHAR(4) NOT NULL,
            zodiac          VARCHAR(2) NOT NULL,
            day_element     VARCHAR(2) NOT NULL,
            day_stem        VARCHAR(2) NOT NULL,
            day_branch      VARCHAR(2) NOT NULL,
            hexagram_id     INTEGER NOT NULL,
            hexagram_name   VARCHAR(32) NOT NULL,
            hexagram_judgment TEXT,
            fortune_level   VARCHAR(8) NOT NULL,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("CREATE SEQUENCE IF NOT EXISTS seq_daily_fortune")


def compute_daily_fortune(d: "date | None" = None, db_path: str = None) -> dict:
    """Pre-compute daily fortune: calendar info + daily I Ching hexagram.

    Args:
        d: target date (default: today)
        db_path: database path

    Returns dict with full daily fortune data.
    """
    import datetime as dt
    from name_data.calendar import gregorian_to_ganzhi, day_ganzhi_index

    if d is None:
        d = dt.date.today()

    conn = init_db(db_path=db_path, read_only=False)
    try:
        _ensure_daily_fortune_table(conn)

        # ── Calendar info ──
        cal = gregorian_to_ganzhi(d)
        day_stem = cal["day_ganzhi"][0]
        day_branch = cal["day_ganzhi"][1]
        day_element = GAN_WUXING.get(day_stem, "土")

        # ── Daily hexagram via coin divination ──
        # Use date as seed for deterministic daily hexagram
        import random
        rng = random.Random(d.isoformat())
        hexagram = _divine_by_coins_seeded(rng, conn)

        hx_id = hexagram["primary"]["id"]
        hx_name = hexagram["primary"]["name"]
        hx_judgment = hexagram["primary"].get("judgment", "")

        # ── Fortune level ──
        fortune = _assess_day_fortune(day_stem, day_branch, cal.get("current_term", ""))

        # ── Store ──
        conn.execute("""
            INSERT OR REPLACE INTO daily_fortune
                (date, year_ganzhi, month_ganzhi, day_ganzhi, current_term,
                 zodiac, day_element, day_stem, day_branch,
                 hexagram_id, hexagram_name, hexagram_judgment, fortune_level)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            d.isoformat(),
            cal["year_ganzhi"], cal["month_ganzhi"], cal["day_ganzhi"],
            cal["current_term"], cal["zodiac"],
            day_element, day_stem, day_branch,
            hx_id, hx_name, (hx_judgment or "")[:500], fortune,
        ])
        conn.commit()

        # ── Almanac (黄历) ──
        from name_data.huangli import compute_daily_almanac as _huangli_compute
        almanac = _huangli_compute(d=d, db_path=db_path)

        return {
            "date": d.isoformat(),
            "year_ganzhi": cal["year_ganzhi"],
            "month_ganzhi": cal["month_ganzhi"],
            "day_ganzhi": cal["day_ganzhi"],
            "current_term": cal["current_term"],
            "zodiac": cal["zodiac"],
            "day_element": day_element,
            "day_stem": day_stem,
            "day_branch": day_branch,
            "daily_hexagram": {
                "id": hx_id,
                "name": hx_name,
                "judgment": (hx_judgment or "")[:200],
            },
            "fortune_level": fortune,
            "almanac": {
                "jianchu": almanac["jianchu"],
                "yellow_black": almanac["yellow_black"],
                "mansion_28": almanac["mansion_28"],
                "pengzu_taboos": almanac["pengzu_taboos"],
                "yi": almanac["yi"],
                "ji": almanac["ji"],
                "almanac_summary": almanac["almanac_summary"],
                "almanac_score": almanac["almanac_score"],
            },
        }
    finally:
        conn.close()


def _divine_by_coins_seeded(rng, conn) -> dict:
    """Coin divination with seeded RNG for deterministic daily hexagram."""
    import random as _random
    lines = []
    for _ in range(6):
        # Each coin: 2 (tails, value 2) or 3 (heads, value 3). Sum of 3 coins: 6-9.
        toss = rng.randint(2, 3) + rng.randint(2, 3) + rng.randint(2, 3)
        if toss == 6:
            lines.append(("0", True))   # old yin (changing)
        elif toss == 7:
            lines.append(("1", False))  # young yang
        elif toss == 8:
            lines.append(("0", False))  # young yin
        else:
            lines.append(("1", True))   # old yang (changing)

    # Build primary and changed hexagrams
    primary_binary = "".join(l[0] for l in lines)
    changed_binary = "".join(("1" if l[0] == "0" else "0") if l[1] else l[0] for l in lines)

    upper_bin = primary_binary[:3]
    lower_bin = primary_binary[3:]

    # Get bagua names
    bagua_map = {
        "111": ("乾", "天", "金"), "000": ("坤", "地", "土"),
        "001": ("震", "雷", "木"), "010": ("坎", "水", "水"),
        "100": ("艮", "山", "土"), "011": ("巽", "风", "木"),
        "101": ("离", "火", "火"), "110": ("兑", "泽", "金"),
    }

    upper = bagua_map.get(upper_bin, ("?", "?", "?"))
    lower = bagua_map.get(lower_bin, ("?", "?", "?"))

    # Find hexagram
    # Get bagua numeric IDs
    upper_id = _binary_3bit_to_trigram_id(upper_bin)
    lower_id = _binary_3bit_to_trigram_id(lower_bin)

    row = conn.execute("""
        SELECT id, name_zh, judgment FROM hexagram_64
        WHERE upper_trigram = ? AND lower_trigram = ?
    """, [upper_id, lower_id]).fetchone()

    if row:
        return {
            "primary": {"id": row[0], "name": row[1], "judgment": row[2] or ""},
        }

    return {"primary": {"id": 1, "name": "乾为天", "judgment": "元亨利贞"}}


def _assess_day_fortune(day_stem: str, day_branch: str, current_term: str) -> str:
    """Assess overall fortune level for a day based on stem-branch relationships."""
    # Day stem-element interactions
    stem_element = GAN_WUXING.get(day_stem, "土")
    branch_element = ZHI_WUXING.get(day_branch, "土")

    # Element harmony: same element is best, generating is good
    generation = {"木": "火", "火": "土", "土": "金", "金": "水", "水": "木"}
    control = {"木": "土", "土": "水", "水": "火", "火": "金", "金": "木"}

    if stem_element == branch_element:
        element_score = 3  # harmony
    elif generation.get(stem_element) == branch_element:
        element_score = 2  # stem generates branch (outgoing)
    elif generation.get(branch_element) == stem_element:
        element_score = 3  # branch generates stem (supported)
    elif control.get(stem_element) == branch_element:
        element_score = 1  # stem controls branch (effort)
    elif control.get(branch_element) == stem_element:
        element_score = 0  # stem controlled by branch (difficult)
    else:
        element_score = 1

    if element_score >= 3:
        return "大吉"
    elif element_score >= 2:
        return "吉"
    elif element_score >= 1:
        return "平"
    else:
        return "凶"


def get_daily_fortune(d: "date | None" = None, db_path: str = None) -> dict:
    """Retrieve daily fortune for a date (computes and stores if not cached)."""
    import datetime as dt
    if d is None:
        d = dt.date.today()

    conn = init_db(db_path=db_path, read_only=True)
    try:
        row = conn.execute(
            "SELECT * FROM daily_fortune WHERE date = ?", [d.isoformat()]
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return compute_daily_fortune(d=d, db_path=db_path)

    # Try to get almanac data
    almanac_data = None
    conn2 = init_db(db_path=db_path, read_only=True)
    try:
        tbl = conn2.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'daily_almanac'"
        ).fetchone()
        if tbl and tbl[0] > 0:
            arow = conn2.execute(
                "SELECT * FROM daily_almanac WHERE date = ?", [d.isoformat()]
            ).fetchone()
            if arow:
                import json
                almanac_data = {
                    "jianchu": {
                        "god": arow[1], "index": arow[2],
                        "yi": [], "ji": [], "score": 0,
                    },
                    "yellow_black": {
                        "officer": arow[4], "type": arow[3],
                        "ji_xiong": "吉" if arow[3] == "黄道" else "凶",
                        "is_auspicious": arow[3] == "黄道",
                    },
                    "mansion_28": {
                        "name": arow[5], "group": arow[6], "luminary": arow[7],
                    },
                    "pengzu_taboos": {
                        "stem_taboo": arow[8] or "", "branch_taboo": arow[9] or "",
                    },
                    "yi": json.loads(arow[10]) if arow[10] else [],
                    "ji": json.loads(arow[11]) if arow[11] else [],
                    "almanac_summary": arow[12] or "",
                    "almanac_score": arow[13] or 0,
                }
    finally:
        conn2.close()

    result = {
        "date": str(row[0]),
        "year_ganzhi": row[1],
        "month_ganzhi": row[2],
        "day_ganzhi": row[3],
        "current_term": row[4],
        "zodiac": row[5],
        "day_element": row[6],
        "day_stem": row[7],
        "day_branch": row[8],
        "daily_hexagram": {
            "id": row[9],
            "name": row[10],
            "judgment": (row[11] or "")[:200],
        },
        "fortune_level": row[12],
    }
    if almanac_data:
        result["almanac"] = almanac_data
    return result


def show_stats(db_path: str = None):
    """Print database statistics."""
    conn = init_db(db_path=db_path, read_only=True)
    try:
        total = conn.execute("SELECT COUNT(*) FROM kangxi_chars").fetchone()[0]
        radicals = conn.execute("SELECT COUNT(*) FROM kangxi_radicals").fetchone()[0]
        with_pinyin = conn.execute(
            "SELECT COUNT(*) FROM kangxi_chars WHERE pinyin != ''"
        ).fetchone()[0]
        with_meaning = conn.execute(
            "SELECT COUNT(*) FROM kangxi_chars WHERE meaning != ''"
        ).fetchone()[0]
        with_element = conn.execute(
            "SELECT COUNT(*) FROM kangxi_chars WHERE five_element IS NOT NULL"
        ).fetchone()[0]
        stroke_range = conn.execute(
            "SELECT MIN(total_strokes), MAX(total_strokes) FROM kangxi_chars"
        ).fetchone()

        # Naming tables
        wuge = conn.execute("SELECT COUNT(*) FROM wuge_81").fetchone()[0]
        sancai = conn.execute("SELECT COUNT(*) FROM sancai_config").fetchone()[0]
        zodiac = conn.execute("SELECT COUNT(*) FROM zodiac_component").fetchone()[0]
        components = conn.execute("SELECT COUNT(*) FROM component_element").fetchone()[0]
        corrections = conn.execute("SELECT COUNT(*) FROM stroke_correction").fetchone()[0]
        compounds = conn.execute("SELECT COUNT(*) FROM compound_surname").fetchone()[0]

        # I Ching tables
        hexagrams = conn.execute("SELECT COUNT(*) FROM hexagram_64").fetchone()[0]
        lines = conn.execute("SELECT COUNT(*) FROM hexagram_lines").fetchone()[0]
        tuibei = conn.execute("SELECT COUNT(*) FROM tuibei_tu").fetchone()[0]

        print(f"""Database: {DB_PATH}

  ── Kangxi Dictionary ──
  Total characters: {total:,}
  Kangxi radicals:  {radicals}
  With pinyin:      {with_pinyin:,}
  With meaning:     {with_meaning:,}
  With 5-element:   {with_element:,}
  Stroke range:     {stroke_range[0]} - {stroke_range[1]}

  ── Naming Reference ──
  Wuge 81:          {wuge}
  Sancai configs:   {sancai}
  Zodiac components:{zodiac}
  Component elements:{components}
  Stroke corrections:{corrections}
  Compound surnames:{compounds}

  ── I Ching (周易) ──
  Hexagrams (64):   {hexagrams}
  Line texts (384): {lines}
  Tuibei Tu:        {tuibei}
""")
    finally:
        conn.close()


# ── CLI ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if "--init" in sys.argv:
        unihan_dir = None
        if "--unihan-dir" in sys.argv:
            idx = sys.argv.index("--unihan-dir")
            if idx + 1 < len(sys.argv):
                unihan_dir = sys.argv[idx + 1]
        result = init_pipeline(unihan_dir=unihan_dir)
        print(result)

    elif "--import-chars" in sys.argv:
        unihan_dir = None
        if "--unihan-dir" in sys.argv:
            idx = sys.argv.index("--unihan-dir")
            if idx + 1 < len(sys.argv):
                unihan_dir = sys.argv[idx + 1]
        result = import_chars(unihan_dir=unihan_dir)
        print(result)

    elif "--import-radicals" in sys.argv:
        n = import_radicals()
        print(f"Inserted {n} radicals")

    elif "--import-ids" in sys.argv:
        ids_path = "/tmp/ids.txt"
        if "--ids-path" in sys.argv:
            idx = sys.argv.index("--ids-path")
            if idx + 1 < len(sys.argv):
                ids_path = sys.argv[idx + 1]
        result = import_ids_structure(ids_path=ids_path)
        print(result)

    elif "--import-seed" in sys.argv:
        result = import_all_seed_data()
        print(result)

    elif "--assign-elements" in sys.argv:
        result = assign_char_elements()
        print(result)

    elif "--score-name" in sys.argv:
        import json
        idx = sys.argv.index("--score-name")
        surname = sys.argv[idx + 1]
        given = sys.argv[idx + 2]
        kwargs = {"surname": surname, "given_name": given}
        if len(sys.argv) > idx + 5:
            kwargs["birth_year"] = int(sys.argv[idx + 3])
            kwargs["birth_month"] = int(sys.argv[idx + 4])
            kwargs["birth_day"] = int(sys.argv[idx + 5])
        result = score_name(**kwargs)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))

    elif "--stats" in sys.argv:
        show_stats()

    elif "--divine" in sys.argv:
        import json
        result = divine_by_coins()
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))

    elif "--divine-numbers" in sys.argv:
        import json
        try:
            idx = sys.argv.index("--divine-numbers")
            a, b, c = int(sys.argv[idx + 1]), int(sys.argv[idx + 2]), int(sys.argv[idx + 3])
            result = divine_by_numbers(a, b, c)
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        except (IndexError, ValueError):
            print("Usage: python -m name_data.pipeline --divine-numbers <a> <b> <c>")
            print("  a=upper trigram(1-8), b=lower trigram(1-8), c=changing line(1-6)")

    elif "--generate-name" in sys.argv:
        try:
            idx = sys.argv.index("--generate-name")
            surname = sys.argv[idx + 1]
            year, month, day = int(sys.argv[idx + 2]), int(sys.argv[idx + 3]), int(sys.argv[idx + 4])
            hour = int(sys.argv[idx + 5]) if len(sys.argv) > idx + 5 else 12
            gender = sys.argv[idx + 6] if len(sys.argv) > idx + 6 else "男"
            result = generate_names(surname, year, month, day, hour, gender)
            # Print summary
            print(f"Surname: {result['surname']}")
            print(f"BaZi: {result['bazi_summary']['pillars']}")
            print(f"Favorable: {result['bazi_summary']['favorable_element']}, Zodiac: {result['bazi_summary']['zodiac']}")
            print(f"Compatible stroke pairs: {result['stroke_analysis']['selected_pairs']}")
            print(f"Total candidates: {result['total_candidates']}")
            print()
            for i, c in enumerate(result['candidates'][:10]):
                wg = c['wuge_grids']
                print(f"{i+1:2d}. {c['full_name']:6s}  Score:{c['total_score']:5.1f}  "
                      f"人{wg['人格']['number']}({wg['人格']['ji_xiong']}) "
                      f"地{wg['地格']['number']}({wg['地格']['ji_xiong']}) "
                      f"总{wg['总格']['number']}({wg['总格']['ji_xiong']}) "
                      f"— {c['verdict'][:20]}")
        except (IndexError, ValueError):
            print("Usage: python -m name_data.pipeline --generate-name <surname> <year> <month> <day> [hour] [gender]")

    else:
        print("Usage: python -m name_data.pipeline [--init|--import-chars|--import-radicals|--import-seed|--assign-elements|--stats|--divine|--score-name|--generate-name] [--unihan-dir DIR]")
        print("  --divine              Coin divination (金钱卦)")
        print("  --divine-numbers a b c  Number divination (数字卦)")
        print("  --score-name surname given_name [year month day]  Score a name")
        print("  --generate-name surname year month day [hour] [gender]  Generate name candidates")
        print("  --assign-elements     Populate five_element for all characters")
