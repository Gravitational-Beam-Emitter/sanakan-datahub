"""
CN Stock Limit-Up Client — wraps the CN Stock REST API (port 8001).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests


class CnStockClient:
    """Synchronous HTTP client for the A-Share Limit-Up Review API."""

    def __init__(self, base_url: str = "http://localhost:8001", timeout: int = 30):
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

    # ── Daily Review ──

    def daily_review(self, date: str) -> dict:
        """Full daily review: stocks, narratives, summary, industry breakdown."""
        return self._get(f"/api/v1/daily/{date}")

    def stocks_by_date(self, date: str, industry: str = None) -> dict:
        """List limit-up stocks for a date. Optional industry filter."""
        params = {}
        if industry:
            params["industry"] = industry
        return self._get(f"/api/v1/stocks/{date}", params)

    def stock_history(self, code: str, limit: int = 60) -> dict:
        """Limit-up history for a specific stock code."""
        return self._get(f"/api/v1/stock/{code}", {"limit": limit})

    # ── Narratives ──

    def narratives(self, date: str) -> dict:
        """Get market narratives for a date."""
        return self._get(f"/api/v1/narratives/{date}")

    def narratives_range(self, start: str, end: str) -> dict:
        """Get LLM-generated narratives for a date range."""
        return self._get("/api/v1/narratives/range", {"start": start, "end": end})

    # ── Industry ──

    def industry_breakdown(self, date: str) -> dict:
        """Get industry distribution for a date."""
        return self._get(f"/api/v1/industry/{date}")

    # ── Dates ──

    def available_dates(self, limit: int = 30) -> dict:
        """Get list of available trading dates."""
        return self._get("/api/v1/dates", {"limit": limit})

    # ── Trend / Backtesting ──

    def trend(self, start: str, end: str) -> dict:
        """Get daily aggregate stats for a date range (backtesting charts)."""
        return self._get("/api/v1/trend", {"start": start, "end": end})

    def sector_rotation(self, start: str, end: str, top_n: int = 15) -> dict:
        """Get industry-date counts for sector rotation heatmap."""
        return self._get("/api/v1/sectors", {"start": start, "end": end, "top_n": top_n})

    def sector_detail(self, start: str, end: str, sector: str) -> dict:
        """Get daily stats for a single sector over time (macro cross-analysis)."""
        return self._get("/api/v1/sectors/macro", {"start": start, "end": end, "sector": sector})

    # ── Fetch ──

    def fetch(self, date: str = None, llm: bool = True) -> dict:
        """Trigger data fetch for a date (defaults to latest). Set llm=False to skip LLM tagging."""
        params: dict = {"llm": llm}
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
