"""
Name matching engine — Chinese + English fuzzy name matching.

Supports:
  - Traditional → Simplified Chinese conversion
  - Chinese → Pinyin (full + initials)
  - Cross-script matching (search Chinese name against English records and vice versa)
  - Fuzzy matching via rapidfuzz
"""

from __future__ import annotations

import re
from typing import List, Optional

import pandas as pd

try:
    from pypinyin import lazy_pinyin, Style
    HAS_PYPINYIN = True
except ImportError:
    HAS_PYPINYIN = False

try:
    from opencc import OpenCC
    _cc = OpenCC("t2s")  # Traditional → Simplified
    HAS_OPENCC = True
except ImportError:
    _cc = None
    HAS_OPENCC = False

try:
    from rapidfuzz import fuzz, process
    HAS_FUZZ = True
except ImportError:
    HAS_FUZZ = False

_CN_CHAR = re.compile(r"[\u4e00-\u9fff]")
_FULLWIDTH_NUM = re.compile(r"[\uff10-\uff19]")  # ０-９
_FULLWIDTH_ALPHA = re.compile(r"[\uff21-\uff3a\uff41-\uff5a]")  # Ａ-Ｚ, ａ-ｚ
_PUNCT = re.compile(r"[，。、；：？！（）《》【】「」『』\"'.,;:!?()\[\]{}<>]")


def _is_word_match(query: str, target: str) -> bool:
    """Check if query appears as a whole word in target (surrounded by spaces or boundaries)."""
    import re
    pattern = r"(?<![A-Za-z0-9])" + re.escape(query) + r"(?![A-Za-z0-9])"
    return bool(re.search(pattern, target))


class NameMatcher:
    """Chinese + English name fuzzy matching."""

    def __init__(self):
        self._cc = _cc if HAS_OPENCC else None

    # ── Normalization ──────────────────────────────────────────

    @staticmethod
    def has_cn(text: str) -> bool:
        """Check if text contains Chinese characters."""
        return bool(_CN_CHAR.search(text))

    @staticmethod
    def normalize_cn(name: str) -> str:
        """Normalize Chinese name: strip punctuation + fullwidth→halfwidth + traditional→simplified."""
        if not name:
            return ""

        # Remove punctuation
        name = _PUNCT.sub("", name)

        # Fullwidth → halfwidth
        name = _FULLWIDTH_NUM.sub(lambda m: chr(ord(m.group()) - 0xFEE0), name)
        name = _FULLWIDTH_ALPHA.sub(lambda m: chr(ord(m.group()) - 0xFEE0), name)

        # Traditional → Simplified
        if HAS_OPENCC and _cc:
            name = _cc.convert(name)

        # Normalize whitespace
        name = re.sub(r"\s+", " ", name).strip()

        return name

    @staticmethod
    def normalize_en(name: str) -> str:
        """Normalize English name: uppercase, strip punctuation/titles."""
        if not name:
            return ""
        # Strip common titles
        for title in ("MR ", "MRS ", "MS ", "DR ", "PROF ", "H.E. ", "H.E ", "HE "):
            if name.upper().startswith(title):
                name = name[len(title):]
        name = re.sub(r"[^a-zA-Z0-9\s]", "", name)
        name = re.sub(r"\s+", " ", name).strip().upper()
        return name

    # ── Pinyin ─────────────────────────────────────────────────

    # Cache for Jyutping import check
    _HAS_JYUTPING: bool | None = None

    @staticmethod
    def to_pinyin(name: str) -> str:
        """Convert Chinese name to pinyin (space-separated, no tones)."""
        if not name or not HAS_PYPINYIN:
            return ""
        return " ".join(lazy_pinyin(name, style=Style.NORMAL))

    @staticmethod
    def to_pinyin_initials(name: str) -> str:
        """Convert Chinese name to pinyin initials (e.g. 'XJP' for '习近平')."""
        if not name or not HAS_PYPINYIN:
            return ""
        return "".join(lazy_pinyin(name, style=Style.FIRST_LETTER))

    @staticmethod
    def to_jyutping(name: str) -> str:
        """Convert Chinese name to Cantonese Jyutping romanization.

        Complements Mandarin pinyin for HK users who search with
        Cantonese romanization (e.g. 'can mau bo' for 陳茂波).
        Tone numbers are stripped for fuzzy search compatibility.
        """
        if not name:
            return ""
        if NameMatcher._HAS_JYUTPING is None:
            try:
                from pyjyutping import jyutping as jp_module
                NameMatcher._jyutping_module = jp_module
                NameMatcher._HAS_JYUTPING = True
            except ImportError:
                NameMatcher._HAS_JYUTPING = False
        if not NameMatcher._HAS_JYUTPING:
            return ""
        result = NameMatcher._jyutping_module.convert(name)
        # Strip tone numbers (e.g. "can4 mau6 bo1" → "can mau bo")
        import re as _re
        return _re.sub(r"\d", "", result)

    @staticmethod
    def to_romanization(name: str) -> str:
        """Combined romanization: Mandarin pinyin + Cantonese Jyutping.

        Used for the name_pinyin DB field to support both Mandarin and
        Cantonese searches against stored Chinese names.
        """
        parts = []
        py = NameMatcher.to_pinyin(name)
        if py:
            parts.append(py)
        jp = NameMatcher.to_jyutping(name)
        if jp and jp != py:
            parts.append(jp)
        return " | ".join(parts)

    # ── Search ─────────────────────────────────────────────────

    def search_candidates(
        self,
        query: str,
        df: pd.DataFrame,
        *,
        threshold: int = 75,
        limit: int = 20,
    ) -> pd.DataFrame:
        """Search candidates DataFrame (must have name_en, name_cn columns). Returns scored matches."""
        query_norm = self.normalize_cn(query)
        query_en = self.normalize_en(query)
        is_cn_query = self.has_cn(query_norm)
        query_pinyin = self.to_pinyin(query_norm) if is_cn_query else ""

        results = []
        for _, row in df.iterrows():
            name_en = str(row.get("name_en", "") or "")
            name_cn = str(row.get("name_cn", "") or "")
            name_cn_norm = str(row.get("name_cn_norm", "") or "")
            name_pinyin = str(row.get("name_pinyin", "") or "")

            best_score = 0

            if is_cn_query:
                # Chinese query: match against Chinese fields + pinyin
                # Use partial_ratio for substring matching (e.g. "大疆" in "深圳市大疆创新科技有限公司")
                if name_cn_norm:
                    if HAS_FUZZ:
                        best_score = max(best_score, fuzz.partial_ratio(query_norm, name_cn_norm))
                        best_score = max(best_score, fuzz.ratio(query_norm, name_cn_norm))
                    elif query_norm in name_cn_norm:
                        best_score = max(best_score, 90)
                if name_cn:
                    cn_norm = self.normalize_cn(name_cn)
                    if HAS_FUZZ:
                        best_score = max(best_score, fuzz.partial_ratio(query_norm, cn_norm))
                        best_score = max(best_score, fuzz.ratio(query_norm, cn_norm))
                    elif query_norm in cn_norm:
                        best_score = max(best_score, 90)
                # Direct substring bonus — query appears inside the full name
                if name_cn_norm and query_norm in name_cn_norm:
                    best_score = max(best_score, 100)
                if name_cn and query_norm in self.normalize_cn(name_cn):
                    best_score = max(best_score, 100)

                if query_pinyin and name_pinyin:
                    if query_pinyin.lower() == name_pinyin.lower():
                        best_score = max(best_score, 95)
                    elif HAS_FUZZ:
                        best_score = max(best_score, fuzz.partial_ratio(query_pinyin.lower(), name_pinyin.lower()))
                if query_pinyin and name_en:
                    qp = query_pinyin.replace(" ", "").lower()
                    ne = name_en.lower().replace(" ", "")
                    if qp == ne:
                        best_score = max(best_score, 90)
                    elif HAS_FUZZ and qp and ne:
                        best_score = max(best_score, fuzz.partial_ratio(qp, ne))
            else:
                # English query: match against English + pinyin fields
                if name_en:
                    ne_upper = name_en.upper()
                    if HAS_FUZZ:
                        # Use weighted ratio (WRatio) as primary scorer — handles length differences better
                        wr = fuzz.WRatio(query_en, ne_upper)
                        best_score = max(best_score, wr)
                        best_score = max(best_score, fuzz.partial_token_sort_ratio(query_en, ne_upper))
                        # partial_ratio is too aggressive for short queries, apply length penalty
                        pr = fuzz.partial_ratio(query_en, ne_upper)
                        if len(query_en) < 4 and pr >= 90:
                            pr = pr - 15  # penalty for short queries to reduce false positives
                        best_score = max(best_score, pr, fuzz.ratio(query_en, ne_upper))
                    elif query_en in ne_upper:
                        best_score = max(best_score, 90)
                    # Direct substring bonus: require word-boundary match for short queries
                    if query_en in ne_upper:
                        if len(query_en) >= 4 or _is_word_match(query_en, ne_upper):
                            best_score = max(best_score, 100)
                if name_pinyin:
                    q_norm = query_en.lower().replace(" ", "")
                    np_norm = name_pinyin.lower().replace(" ", "")
                    if HAS_FUZZ:
                        best_score = max(best_score, fuzz.partial_ratio(q_norm, np_norm))
                        best_score = max(best_score, fuzz.ratio(q_norm, np_norm))
                    elif q_norm in np_norm:
                        best_score = max(best_score, 90)
                if name_cn_norm:
                    # Try: is query_en a pinyin representation of name_cn?
                    pinyin = self.to_pinyin(name_cn_norm).replace(" ", "").lower()
                    qn = query_en.lower().replace(" ", "")
                    if qn == pinyin:
                        best_score = max(best_score, 90)
                    elif HAS_FUZZ and qn and pinyin:
                        best_score = max(best_score, fuzz.partial_ratio(qn, pinyin))

            if best_score >= threshold:
                results.append({**row.to_dict(), "match_score": best_score})

        if not results:
            return pd.DataFrame()

        result_df = pd.DataFrame(results)
        result_df = result_df.sort_values("match_score", ascending=False).head(limit)
        return result_df

    def cross_search(
        self,
        query: str,
        screening_df: pd.DataFrame,
        *,
        threshold: int = 75,
        limit: int = 20,
    ) -> dict:
        """Comprehensive cross-script search. Returns structured results."""
        matches = self.search_candidates(query, screening_df, threshold=threshold, limit=limit)

        sanctions = []
        peps = []
        others = []

        for _, row in matches.iterrows():
            entry = {
                "id": row.get("id"),
                "name_en": row.get("name_en"),
                "name_cn": row.get("name_cn"),
                "source": row.get("source"),
                "risk_category": row.get("risk_category"),
                "pep_level": row.get("pep_level"),
                "countries": row.get("countries"),
                "match_score": row.get("match_score"),
                "notes": row.get("notes"),
                "aliases": row.get("aliases"),
                "addresses": row.get("addresses"),
                "programs": row.get("programs"),
                "source_date": row.get("source_date"),
                "name_type": row.get("name_type"),
            }
            risk = str(row.get("risk_category", "") or "").lower()
            if "sanction" in risk:
                sanctions.append(entry)
            elif "pep" in risk:
                peps.append(entry)
            else:
                others.append(entry)

        return {
            "query": query,
            "total_matches": len(matches),
            "sanctions_hits": len(sanctions),
            "pep_hits": len(peps),
            "sanctions": sanctions,
            "peps": peps,
            "other": others,
        }
