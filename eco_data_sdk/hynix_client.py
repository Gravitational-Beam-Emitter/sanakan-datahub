"""
Hynix Client — wraps the SK Hynix Cross-Market REST API (port 8008).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests


class HynixClient:
    """Synchronous HTTP client for the SK Hynix Cross-Market Arbitrage API."""

    def __init__(self, base_url: str = "http://localhost:8008", timeout: int = 30):
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

    # ── Instruments ──

    def list_instruments(self, market: str = None) -> dict:
        """List tracked instruments. Optional market filter (KR, US, HK)."""
        params = {}
        if market:
            params["market"] = market
        return self._get("/api/v1/instruments", params)

    # ── Arbitrage ──

    def latest_arbitrage(self) -> dict:
        """Get the latest cross-market arbitrage snapshot with premium/discount."""
        return self._get("/api/v1/arbitrage/latest")

    def arbitrage_by_date(self, date: str) -> dict:
        """Get arbitrage comparison for a specific date."""
        return self._get(f"/api/v1/arbitrage/{date}")

    def arbitrage_history(
        self,
        ticker: str,
        start: str = None,
        end: str = None,
        limit: int = 60,
    ) -> dict:
        """Get premium/discount time series for an instrument."""
        params: dict = {"limit": limit}
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        return self._get(f"/api/v1/arbitrage/{ticker}/history", params)

    # ── Prices ──

    def price_history(
        self,
        ticker: str,
        start: str = None,
        end: str = None,
        limit: int = 60,
    ) -> dict:
        """Get price history for an instrument."""
        params: dict = {"limit": limit}
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        return self._get(f"/api/v1/prices/{ticker}", params)

    def prices_by_date(self, date: str) -> dict:
        """Get all instrument prices for a given date."""
        return self._get("/api/v1/prices", {"date": date})

    # ── FX Rates ──

    def latest_fx(self) -> dict:
        """Get the latest FX rates."""
        return self._get("/api/v1/fx/latest")

    def fx_history(
        self,
        from_ccy: str = "USD",
        to_ccy: str = "KRW",
        limit: int = 60,
    ) -> dict:
        """Get FX rate history."""
        return self._get("/api/v1/fx/history", {"from_ccy": from_ccy, "to_ccy": to_ccy, "limit": limit})

    # ── Dates ──

    def available_dates(self, limit: int = 30) -> dict:
        """Get list of available trading dates."""
        return self._get("/api/v1/dates", {"limit": limit})

    # ── Fetch ──

    def fetch(self, date: str = None) -> dict:
        """Trigger daily fetch (defaults to latest trading day)."""
        params = {}
        if date:
            params["date"] = date
        return self._post("/api/v1/fetch", params=params)

    def init(self, lookback: int = 90) -> dict:
        """Full init: seed instruments + backfill."""
        return self._post("/api/v1/init", {"lookback": lookback})

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
