"""
KR Stock Client — wraps the Korean Stock REST API (port 8006).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests


class KrStockClient:
    """Synchronous HTTP client for the Korean Stock Market API (KOSPI/KOSDAQ/KONEX)."""

    def __init__(self, base_url: str = "http://localhost:8006", timeout: int = 30):
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
        """Full daily review: movers, narratives, summary, industry breakdown."""
        return self._get(f"/api/v1/daily/{date}")

    # ── Movers ──

    def stocks_by_date(self, date: str, industry: str = None) -> dict:
        """Significant movers for a date. Optional industry filter."""
        params = {}
        if industry:
            params["industry"] = industry
        return self._get(f"/api/v1/stocks/{date}", params)

    # ── Stock Detail ──

    def stock_history(self, code: str, limit: int = 60) -> dict:
        """Price history for a stock."""
        return self._get(f"/api/v1/stock/{code}", {"limit": limit})

    def stock_detail(self, code: str) -> dict:
        """Stock listing info + mover history."""
        return self._get(f"/api/v1/stock/{code}/detail")

    # ── Listings ──

    def list_listings(
        self,
        market: str = None,
        sector: str = None,
        search: str = None,
        limit: int = 100,
    ) -> dict:
        """List stocks with optional filters (market, sector, search)."""
        params: dict = {"limit": limit}
        if market:
            params["market"] = market
        if sector:
            params["sector"] = sector
        if search:
            params["search"] = search
        return self._get("/api/v1/listings", params)

    # ── Indices ──

    def list_indices(
        self,
        index_code: str = None,
        start: str = None,
        end: str = None,
        limit: int = 200,
    ) -> dict:
        """Get market index data (KS11 for KOSPI, KQ11 for KOSDAQ)."""
        params: dict = {"limit": limit}
        if index_code:
            params["index_code"] = index_code
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        return self._get("/api/v1/indices", params)

    # ── Narratives ──

    def narratives(self, date: str) -> dict:
        """Get daily market narratives."""
        return self._get(f"/api/v1/narratives/{date}")

    def narratives_range(self, start: str, end: str) -> dict:
        """Get narratives for a date range."""
        return self._get("/api/v1/narratives/range", {"start": start, "end": end})

    # ── Industry ──

    def industry_breakdown(self, date: str) -> dict:
        """Get industry distribution for a date."""
        return self._get(f"/api/v1/industry/{date}")

    # ── Dates ──

    def available_dates(self, limit: int = 30) -> dict:
        """Get list of available trading dates."""
        return self._get("/api/v1/dates", {"limit": limit})

    # ── Trend ──

    def trend(self, start: str, end: str) -> dict:
        """Get daily aggregate stats for a date range (backtesting)."""
        return self._get("/api/v1/trend", {"start": start, "end": end})

    # ── Sectors ──

    def sector_rotation(self, start: str, end: str, top_n: int = 15) -> dict:
        """Get sector rotation heatmap data."""
        return self._get("/api/v1/sectors", {"start": start, "end": end, "top_n": top_n})

    def sector_detail(self, start: str, end: str, sector: str) -> dict:
        """Get daily stats for a single sector over time."""
        return self._get("/api/v1/sectors/macro", {"start": start, "end": end, "sector": sector})

    # ── Foreign Flows ──

    def foreign_flows(
        self,
        market: str = None,
        start: str = None,
        end: str = None,
        limit: int = 60,
    ) -> dict:
        """Get foreign/institutional flow data."""
        params: dict = {"limit": limit}
        if market:
            params["market"] = market
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        return self._get("/api/v1/flows", params)

    # ── DART Filings ──

    def list_filings(
        self,
        corp_name: str = None,
        report_type: str = None,
        start: str = None,
        end: str = None,
        limit: int = 100,
    ) -> dict:
        """Search DART corporate filings."""
        params: dict = {"limit": limit}
        if corp_name:
            params["corp_name"] = corp_name
        if report_type:
            params["report_type"] = report_type
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        return self._get("/api/v1/filings", params)

    def filing_detail(self, rcept_no: str) -> dict:
        """Get a single DART filing."""
        return self._get(f"/api/v1/filings/{rcept_no}")

    # ── Fetch ──

    def fetch(self, date: str = None, llm: bool = True) -> dict:
        """Trigger daily fetch (defaults to latest trading day)."""
        params: dict = {"llm": llm}
        if date:
            params["date"] = date
        return self._post("/api/v1/fetch", params=params)

    def fetch_filings(self, date: str = None) -> dict:
        """Trigger DART filings fetch."""
        params = {}
        if date:
            params["date"] = date
        return self._post("/api/v1/fetch-filings", params=params)

    def init(self) -> dict:
        """Full init: listings + index history + recent prices."""
        return self._post("/api/v1/init")

    def fetch_status(self, days: int = 7) -> dict:
        """Get recent fetch log entries."""
        return self._get("/api/v1/fetch/status", {"days": days})

    # ── Valuation & Fundamentals ──

    def stock_metrics(self, code: str) -> dict:
        """Get latest valuation metrics (P/E, P/B, dividend yield, beta, growth, etc.)."""
        return self._get(f"/api/v1/stock/{code}/metrics")

    def metrics_batch(self, codes: List[str]) -> dict:
        """Get latest metrics for multiple stocks."""
        resp = self._session.post(
            f"{self.base_url}/api/v1/metrics/batch",
            json={"codes": codes},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def stock_financials(self, code: str, statement_type: str = None) -> dict:
        """Get quarterly financials (Balance Sheet, Income Statement, Cash Flow)."""
        params = {}
        if statement_type:
            params["type"] = statement_type
        return self._get(f"/api/v1/stock/{code}/financials", params)

    def stock_analyst(self, code: str) -> dict:
        """Get analyst consensus (price targets, recommendations, estimates)."""
        return self._get(f"/api/v1/stock/{code}/analyst")

    # ── Lifecycle ──

    def close(self) -> None:
        self._session.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
