"""
Name Data Client — wraps the Chinese Naming & I Ching API (port 8008).
"""

from __future__ import annotations

from typing import Any, Optional

import requests


class NameDataClient:
    """Synchronous HTTP client for Chinese naming, BaZi, and I Ching divination."""

    def __init__(self, base_url: str = "http://localhost:8008", timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()

    def _get(self, path: str, params: Optional[dict] = None) -> Any:
        resp = self._session.get(f"{self.base_url}{path}", params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, body: Optional[dict] = None) -> Any:
        resp = self._session.post(f"{self.base_url}{path}", json=body, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    # ── Health ──

    def health(self) -> dict:
        return self._get("/api/v1/health")

    # ── Characters ──

    def search_characters(
        self,
        q: str = None,
        radical: str = None,
        strokes_min: int = None,
        strokes_max: int = None,
        element: str = None,
        tone: int = None,
        name_only: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> dict:
        params = {"limit": limit, "offset": offset, "name_only": name_only}
        if q: params["q"] = q
        if radical: params["radical"] = radical
        if strokes_min is not None: params["strokes_min"] = strokes_min
        if strokes_max is not None: params["strokes_max"] = strokes_max
        if element: params["element"] = element
        if tone is not None: params["tone"] = tone
        return self._get("/api/v1/characters/search", params)

    def character_detail(self, char: str) -> dict:
        return self._get(f"/api/v1/characters/{char}")

    def radicals(self) -> dict:
        return self._get("/api/v1/radicals")

    def stats(self) -> dict:
        return self._get("/api/v1/stats")

    # ── Name Scoring ──

    def score_name(
        self,
        surname: str,
        given_name: str,
        birth_year: int = None,
        birth_month: int = None,
        birth_day: int = None,
        birth_hour: int = 12,
        gender: str = "男",
    ) -> dict:
        return self._post("/api/v1/name/score", {
            "surname": surname,
            "given_name": given_name,
            "birth_year": birth_year,
            "birth_month": birth_month,
            "birth_day": birth_day,
            "birth_hour": birth_hour,
            "gender": gender,
        })

    def generate_names(
        self,
        surname: str,
        birth_year: int,
        birth_month: int,
        birth_day: int,
        birth_hour: int = 12,
        gender: str = "男",
        num_names: int = 30,
    ) -> dict:
        return self._post("/api/v1/name/generate", {
            "surname": surname,
            "birth_year": birth_year,
            "birth_month": birth_month,
            "birth_day": birth_day,
            "birth_hour": birth_hour,
            "gender": gender,
            "num_names": num_names,
        })

    def score_name_batch(
        self,
        names: list,
        birth_year: int = None,
        birth_month: int = None,
        birth_day: int = None,
        birth_hour: int = 12,
        gender: str = "男",
    ) -> dict:
        return self._post("/api/v1/name/score-batch", {
            "names": names,
            "birth_year": birth_year,
            "birth_month": birth_month,
            "birth_day": birth_day,
            "birth_hour": birth_hour,
            "gender": gender,
        })

    def calculate_bazi(
        self, year: int, month: int, day: int, hour: int = 12
    ) -> dict:
        return self._post("/api/v1/bazi/calculate", {
            "year": year, "month": month, "day": day, "hour": hour,
        })

    def calculate_wuge(self, surname: str, given_name: str) -> dict:
        return self._get("/api/v1/wuge/calculate", {
            "surname": surname, "given_name": given_name,
        })

    # ── I Ching Divination ──

    def divine_by_coins(self) -> dict:
        return self._get("/api/v1/divine/coins")

    def divine_by_numbers(self, a: int, b: int, c: int) -> dict:
        return self._get("/api/v1/divine/numbers", {"a": a, "b": b, "c": c})

    def hexagram(self, hexagram_id: int) -> dict:
        return self._get(f"/api/v1/hexagram/{hexagram_id}")

    def hexagram_wrong(self, hexagram_id: int) -> dict:
        return self._get(f"/api/v1/hexagram/{hexagram_id}/wrong")

    def hexagram_reverse(self, hexagram_id: int) -> dict:
        return self._get(f"/api/v1/hexagram/{hexagram_id}/reverse")

    # ── Tui Bei Tu (推背图) ──

    def list_tuibei(self, era: str = None) -> dict:
        params = {}
        if era: params["era"] = era
        return self._get("/api/v1/tuibei-tu", params)

    def tuibei_eras(self) -> dict:
        return self._get("/api/v1/tuibei-tu/eras")

    def get_tuibei(self, tuibei_id: int) -> dict:
        return self._get(f"/api/v1/tuibei-tu/{tuibei_id}")

    def consult_tuibei(self, method: str = "random", hexagram_id: int = None) -> dict:
        params = {"method": method}
        if hexagram_id: params["hexagram_id"] = hexagram_id
        return self._get("/api/v1/tuibei-tu/divine", params)

    # ── Chinese Calendar (农历) ──

    def calendar_today(self) -> dict:
        return self._get("/api/v1/calendar/today")

    def calendar_date(self, date_str: str) -> dict:
        return self._get(f"/api/v1/calendar/date/{date_str}")

    def day_ganzhi(self, date_str: str) -> dict:
        return self._get(f"/api/v1/calendar/day-ganzhi/{date_str}")

    def solar_terms(self, year: int) -> dict:
        return self._get(f"/api/v1/calendar/solar-terms/{year}")

    def current_term(self, year: int, month: int, day: int) -> dict:
        return self._get("/api/v1/calendar/current-term", {
            "year": year, "month": month, "day": day,
        })

    # ── Daily Fortune (每日运势) ──

    def daily_fortune(self, date_str: str = None) -> dict:
        """Get pre-computed daily fortune: calendar info + daily I Ching hexagram.

        Args:
            date_str: target date (YYYY-MM-DD), defaults to today
        """
        if date_str:
            return self._get(f"/api/v1/daily-fortune/{date_str}")
        return self._get("/api/v1/daily-fortune/today")

    # ── Chinese Almanac (黄历) ──

    def huangli(self, date_str: str = None) -> dict:
        """Get Chinese Almanac (黄历): jianchu gods, yellow/black path,
        28 lunar mansions, Peng Zu taboos, and daily suitable/avoid activities.
        Based on 《协纪辨方书》.

        Args:
            date_str: target date (YYYY-MM-DD), defaults to today
        """
        if date_str:
            return self._get(f"/api/v1/huangli/{date_str}")
        return self._get("/api/v1/huangli/today")

    # ── Lifecycle ──

    def close(self) -> None:
        self._session.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
