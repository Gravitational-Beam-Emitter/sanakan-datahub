"""
Company Announcements Client — wraps the Announcements REST API (port 8005).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests


class AnnouncementsClient:
    """Synchronous HTTP client for the Multi-Market Company Announcements API (US SEC, HK HKEXnews, CN CNINFO)."""

    def __init__(self, base_url: str = "http://localhost:8005", timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()

    def _get(self, path: str, params: Optional[dict] = None) -> Any:
        resp = self._session.get(f"{self.base_url}{path}", params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, params: Optional[dict] = None) -> Any:
        resp = self._session.post(f"{self.base_url}{path}", params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    # ── Health ──

    def health(self) -> dict:
        return self._get("/api/v1/health")

    # ── Announcements ──

    def list_announcements(
        self,
        ticker: str = None,
        market: str = None,
        source: str = None,
        start: str = None,
        end: str = None,
        limit: int = 100,
    ) -> dict:
        """List announcements with optional filters. Excludes text_content for performance."""
        params: dict = {"limit": limit}
        if ticker:
            params["ticker"] = ticker
        if market:
            params["market"] = market
        if source:
            params["source"] = source
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        return self._get("/api/v1/announcements", params)

    def get_announcement(self, ann_id: int) -> dict:
        """Get single announcement with full text_content."""
        return self._get(f"/api/v1/announcements/{ann_id}")

    # ── Companies ──

    def companies(self) -> dict:
        """List tracked companies with announcement counts."""
        return self._get("/api/v1/companies")

    # ── Dates ──

    def available_dates(self, limit: int = 30) -> dict:
        """Get list of available announcement dates."""
        return self._get("/api/v1/dates", {"limit": limit})

    # ── Fetch ──

    def fetch(self) -> dict:
        """Trigger a daily fetch for all tracked companies."""
        return self._post("/api/v1/fetch")

    def fetch_status(self, days: int = 7) -> dict:
        """Get recent fetch log entries."""
        return self._get("/api/v1/fetch/status", {"days": days})

    # ── Lifecycle ──

    def close(self) -> None:
        self._session.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
