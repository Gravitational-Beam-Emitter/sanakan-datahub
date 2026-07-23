"""
A-Share ETF Flow Client — wraps the A-share ETF REST API (port 8008).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests


class AShareEtfClient:
    """Synchronous HTTP client for the A-share ETF Flow API."""

    def __init__(self, base_url: str = "http://localhost:8009", timeout: int = 30):
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

    # ── ETF ──

    def etfs_by_date(self, date: str, sector: str = None) -> dict:
        """All ETFs for a date, optionally filtered by sector."""
        params = {}
        if sector:
            params["sector"] = sector
        return self._get(f"/api/v1/etf/{date}", params)

    def etf_history(self, code: str, limit: int = 60) -> dict:
        """Daily history for a specific ETF code."""
        return self._get(f"/api/v1/etf/{code}/history", {"limit": limit})

    # ── Sectors ──

    def sector_list(self) -> dict:
        """List all sector labels."""
        return self._get("/api/v1/sectors/list")

    def sectors_by_date(self, date: str) -> dict:
        """Sector flow breakdown for a date."""
        return self._get(f"/api/v1/sectors/{date}")

    def sector_history(self, sector: str, limit: int = 60) -> dict:
        """Daily flow history for a sector."""
        return self._get(f"/api/v1/sectors/{sector}/history", {"limit": limit})

    # ── Margin ──

    def margin_latest(self) -> dict:
        """Latest margin balance data."""
        return self._get("/api/v1/margin")

    def margin_history(self, start: str = None, end: str = None, limit: int = 60) -> dict:
        """Margin balance history."""
        params: dict = {"limit": limit}
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        return self._get("/api/v1/margin/history", params)

    # ── Overview ──

    def overview(self, date: str) -> dict:
        """Market overview (merged proxy) for a date."""
        return self._get(f"/api/v1/overview/{date}")

    def overview_history(self, start: str = None, end: str = None, limit: int = 60) -> dict:
        """Market overview time series."""
        params: dict = {"limit": limit}
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        return self._get("/api/v1/overview/history", params)

    # ── Dates ──

    def available_dates(self, limit: int = 30) -> dict:
        """Get list of available trading dates."""
        return self._get("/api/v1/dates", {"limit": limit})

    # ── Fetch ──

    def fetch(self, date: str = None) -> dict:
        """Trigger data fetch for a date (defaults to latest)."""
        params = {}
        if date:
            params["date"] = date
        return self._post("/api/v1/fetch", params=params)

    # ── Lifecycle ──

    def close(self) -> None:
        self._session.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
