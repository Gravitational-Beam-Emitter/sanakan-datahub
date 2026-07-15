"""
US Listings & Crypto Products Client — wraps the US Listings REST API (port 8003).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests


class UsListingsClient:
    """Synchronous HTTP client for the US Listings & Crypto Products API."""

    def __init__(self, base_url: str = "http://localhost:8003", timeout: int = 30):
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

    # ── Listings ──

    def list_listings(
        self,
        start: str = None,
        end: str = None,
        listing_type: str = None,
        exchange: str = None,
        is_crypto: bool = None,
        limit: int = 100,
    ) -> dict:
        """List new listings with optional filters."""
        params: dict = {"limit": limit}
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        if listing_type:
            params["listing_type"] = listing_type
        if exchange:
            params["exchange"] = exchange
        if is_crypto is not None:
            params["is_crypto"] = is_crypto
        return self._get("/api/v1/listings", params)

    def upcoming_listings(self) -> dict:
        """Get upcoming IPOs (listing_date >= today)."""
        return self._get("/api/v1/listings/upcoming")

    def get_listing(self, ticker: str) -> dict:
        """Get listing details for a specific ticker."""
        return self._get(f"/api/v1/listings/{ticker}")

    # ── Summary & Dates ──

    def summary(self, start: str, end: str) -> dict:
        """Get listing summary statistics for a date range (includes monthly breakdown)."""
        return self._get("/api/v1/summary", {"start": start, "end": end})

    def available_dates(self, limit: int = 60) -> dict:
        """Get list of available listing dates."""
        return self._get("/api/v1/dates", {"limit": limit})

    # ── Crypto Products ──

    def list_crypto(
        self,
        product_type: str = None,
        underlying_asset: str = None,
        active_only: bool = True,
    ) -> dict:
        """List all crypto products with optional filters."""
        params: dict = {"active_only": active_only}
        if product_type:
            params["product_type"] = product_type
        if underlying_asset:
            params["underlying_asset"] = underlying_asset
        return self._get("/api/v1/crypto", params)

    def crypto_stats(self) -> dict:
        """Get crypto product statistics (by type, asset)."""
        return self._get("/api/v1/crypto/stats")

    def crypto_recent(self, days: int = 30) -> dict:
        """Get recently added crypto products."""
        return self._get("/api/v1/crypto/recent", {"days": days})

    def get_crypto(self, ticker: str) -> dict:
        """Get detailed info for a single crypto product."""
        return self._get(f"/api/v1/crypto/{ticker}")

    # ── Insider Trades ──

    def list_insider(
        self,
        ticker: str = None,
        start: str = None,
        end: str = None,
        limit: int = 100,
    ) -> dict:
        """List insider trades (Form 4 filings)."""
        params: dict = {"limit": limit}
        if ticker:
            params["ticker"] = ticker
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        return self._get("/api/v1/insider", params)

    def insider_ticker(self, ticker: str) -> dict:
        """Get insider trading history for a specific ticker."""
        return self._get(f"/api/v1/insider/{ticker}")

    # ── Earnings Calendar ──

    def list_earnings(
        self,
        ticker: str = None,
        start: str = None,
        end: str = None,
        report_type: str = None,
        limit: int = 100,
    ) -> dict:
        """List earnings calendar (10-K/10-Q filings)."""
        params: dict = {"limit": limit}
        if ticker:
            params["ticker"] = ticker
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        if report_type:
            params["report_type"] = report_type
        return self._get("/api/v1/earnings", params)

    def upcoming_earnings(self, limit: int = 50) -> dict:
        """Get upcoming earnings (filing_date >= today)."""
        return self._get("/api/v1/earnings/upcoming", {"limit": limit})

    # ── Institutional Holdings (13F) ──

    def list_holdings(
        self,
        ticker: str = None,
        filer_cik: str = None,
        quarter_end: str = None,
        limit: int = 100,
    ) -> dict:
        """List institutional holdings (13F filings)."""
        params: dict = {"limit": limit}
        if ticker:
            params["ticker"] = ticker
        if filer_cik:
            params["filer_cik"] = filer_cik
        if quarter_end:
            params["quarter_end"] = quarter_end
        return self._get("/api/v1/holdings", params)

    def holdings_ticker(self, ticker: str) -> dict:
        """Get institutional holdings history for a specific ticker."""
        return self._get(f"/api/v1/holdings/{ticker}")

    # ── Short Interest & FTD ──

    def list_short_interest(self, ticker: str = None, limit: int = 50) -> dict:
        """List short interest data."""
        params: dict = {"limit": limit}
        if ticker:
            params["ticker"] = ticker
        return self._get("/api/v1/short-interest", params)

    def list_ftd(
        self,
        ticker: str = None,
        start: str = None,
        end: str = None,
        limit: int = 100,
    ) -> dict:
        """List fails-to-deliver data (SEC Reg SHO)."""
        params: dict = {"limit": limit}
        if ticker:
            params["ticker"] = ticker
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        return self._get("/api/v1/ftd", params)

    # ── ETF Flows ──

    def list_flows(
        self,
        ticker: str = None,
        start: str = None,
        end: str = None,
        limit: int = 30,
    ) -> dict:
        """List crypto ETF daily flows."""
        params: dict = {"limit": limit}
        if ticker:
            params["ticker"] = ticker
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        return self._get("/api/v1/flows", params)

    # ── Dividends & Splits ──

    def list_dividends(
        self,
        ticker: str = None,
        start: str = None,
        end: str = None,
        limit: int = 100,
    ) -> dict:
        """List dividend calendar."""
        params: dict = {"limit": limit}
        if ticker:
            params["ticker"] = ticker
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        return self._get("/api/v1/dividends", params)

    def list_splits(self, ticker: str = None, limit: int = 50) -> dict:
        """List stock split history."""
        params: dict = {"limit": limit}
        if ticker:
            params["ticker"] = ticker
        return self._get("/api/v1/splits", params)

    # ── Trading Suspensions ──

    def list_suspensions(
        self,
        ticker: str = None,
        start: str = None,
        end: str = None,
        limit: int = 100,
    ) -> dict:
        """List trading suspensions (SEC Form 34)."""
        params: dict = {"limit": limit}
        if ticker:
            params["ticker"] = ticker
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        return self._get("/api/v1/suspensions", params)

    # ── SEC Enforcement ──

    def list_enforcement(
        self,
        enforcement_type: str = None,
        start: str = None,
        end: str = None,
        limit: int = 100,
    ) -> dict:
        """List SEC enforcement actions (AAER, Litigation Releases, Admin Proceedings)."""
        params: dict = {"limit": limit}
        if enforcement_type:
            params["enforcement_type"] = enforcement_type
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        return self._get("/api/v1/enforcement", params)

    # ── Threshold Securities ──

    def list_threshold(self, ticker: str = None, date: str = None, limit: int = 100) -> dict:
        """List Reg SHO threshold securities."""
        params: dict = {"limit": limit}
        if ticker:
            params["ticker"] = ticker
        if date:
            params["date"] = date
        return self._get("/api/v1/threshold", params)

    # ── ATS / Dark Pool ──

    def list_ats(
        self,
        filer_cik: str = None,
        start: str = None,
        end: str = None,
        limit: int = 50,
    ) -> dict:
        """List ATS / dark pool filings (SEC Form ATS-N)."""
        params: dict = {"limit": limit}
        if filer_cik:
            params["filer_cik"] = filer_cik
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        return self._get("/api/v1/ats", params)

    # ── Short Sale Activity ──

    def list_short_activity(
        self,
        ticker: str = None,
        risk_level: str = None,
        limit: int = 50,
    ) -> dict:
        """List enhanced short sale activity with risk signals."""
        params: dict = {"limit": limit}
        if ticker:
            params["ticker"] = ticker
        if risk_level:
            params["risk_level"] = risk_level
        return self._get("/api/v1/short-activity", params)

    # ── IPO Lockup ──

    def list_lockups(
        self,
        ticker: str = None,
        status: str = "active",
        limit: int = 50,
    ) -> dict:
        """List IPO lockup expiry dates."""
        params: dict = {"status": status, "limit": limit}
        if ticker:
            params["ticker"] = ticker
        return self._get("/api/v1/lockup", params)

    # ── Options Flow ──

    def list_options_flow(
        self,
        ticker: str = None,
        unusual_only: bool = False,
        limit: int = 50,
    ) -> dict:
        """List options flow data with unusual activity detection."""
        params: dict = {"unusual_only": unusual_only, "limit": limit}
        if ticker:
            params["ticker"] = ticker
        return self._get("/api/v1/options-flow", params)

    # ── Fetch Triggers ──

    def fetch(self, month: str = None) -> dict:
        """Trigger new listings fetch (defaults to current month)."""
        params = {}
        if month:
            params["month"] = month
        return self._post("/api/v1/fetch", params=params)

    def fetch_crypto(self, action: str = "refresh") -> dict:
        """Trigger crypto products update. action: refresh, enrich, scan."""
        return self._post("/api/v1/fetch-crypto", params={"action": action})

    def fetch_insider(self, date: str = None) -> dict:
        """Trigger insider trading (Form 4) fetch."""
        params = {}
        if date:
            params["date"] = date
        return self._post("/api/v1/fetch-insider", params=params)

    def fetch_earnings(self, date: str = None) -> dict:
        """Trigger earnings calendar fetch."""
        params = {}
        if date:
            params["date"] = date
        return self._post("/api/v1/fetch-earnings", params=params)

    def fetch_holdings(self) -> dict:
        """Trigger institutional holdings (13F) fetch."""
        return self._post("/api/v1/fetch-holdings")

    def fetch_risk(self) -> dict:
        """Trigger risk data (short interest + FTD) fetch."""
        return self._post("/api/v1/fetch-risk")

    def fetch_flows(self) -> dict:
        """Trigger ETF flows fetch."""
        return self._post("/api/v1/fetch-flows")

    def fetch_corporate_events(self) -> dict:
        """Trigger dividends + stock splits fetch."""
        return self._post("/api/v1/fetch-corporate-events")

    def fetch_suspensions(self, date: str = None) -> dict:
        """Trigger trading suspensions fetch."""
        params = {}
        if date:
            params["date"] = date
        return self._post("/api/v1/fetch-suspensions", params=params)

    def fetch_enforcement(self, date: str = None) -> dict:
        """Trigger SEC enforcement actions fetch."""
        params = {}
        if date:
            params["date"] = date
        return self._post("/api/v1/fetch-enforcement", params=params)

    def fetch_threshold(self, date: str = None) -> dict:
        """Trigger threshold securities fetch."""
        params = {}
        if date:
            params["date"] = date
        return self._post("/api/v1/fetch-threshold", params=params)

    def fetch_ats(self, date: str = None) -> dict:
        """Trigger ATS/dark pool filings fetch."""
        params = {}
        if date:
            params["date"] = date
        return self._post("/api/v1/fetch-ats", params=params)

    def fetch_short_activity(self) -> dict:
        """Trigger enhanced short sale activity fetch."""
        return self._post("/api/v1/fetch-short-activity")

    def fetch_lockup(self) -> dict:
        """Trigger IPO lockup expiry computation."""
        return self._post("/api/v1/fetch-lockup")

    def fetch_options(self) -> dict:
        """Trigger options flow fetch."""
        return self._post("/api/v1/fetch-options")

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
