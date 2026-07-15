"""
US Corporate Actions Client — wraps the US Corp Actions REST API (port 8002).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests


class UsCorpActionsClient:
    """Synchronous HTTP client for the US Corporate Actions API (SEC 8-K filings)."""

    def __init__(self, base_url: str = "http://localhost:8002", timeout: int = 30):
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

    # ── Actions ──

    def daily_actions(self, date: str) -> dict:
        """Get all corporate actions for a filing date with summary and breakdown."""
        return self._get(f"/api/v1/actions/{date}")

    def list_actions(
        self,
        start: str = None,
        end: str = None,
        action_type: str = None,
        ticker: str = None,
        limit: int = 100,
    ) -> dict:
        """List actions with optional date range, type, and ticker filters."""
        params: dict = {"limit": limit}
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        if action_type:
            params["action_type"] = action_type
        if ticker:
            params["ticker"] = ticker
        return self._get("/api/v1/actions", params)

    def ticker_history(self, ticker: str, limit: int = 50) -> dict:
        """Get corporate action history for a specific ticker."""
        return self._get(f"/api/v1/actions/ticker/{ticker}", {"limit": limit})

    # ── Dates ──

    def available_dates(self, limit: int = 30) -> dict:
        """Get list of available filing dates."""
        return self._get("/api/v1/dates", {"limit": limit})

    # ── Summary ──

    def summary(self, start: str, end: str) -> dict:
        """Get daily action type breakdown for a date range."""
        return self._get("/api/v1/summary", {"start": start, "end": end})

    def breakdown(self, date: str) -> dict:
        """Get action type breakdown for a specific date."""
        return self._get(f"/api/v1/breakdown/{date}")

    # ── Fetch ──

    def fetch_status(self, days: int = 7) -> dict:
        """Get recent fetch log entries."""
        return self._get("/api/v1/fetch/status", {"days": days})

    def fetch(self, date: str = None) -> dict:
        """Manually trigger data fetch for a date (defaults to latest)."""
        params = {}
        if date:
            params["date"] = date
        return self._post("/api/v1/fetch", params=params)

    def init(self) -> dict:
        """Re-initialize: download CIK map and backfill historical data."""
        return self._post("/api/v1/init")

    # ── Lifecycle ──

    def close(self) -> None:
        self._session.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
