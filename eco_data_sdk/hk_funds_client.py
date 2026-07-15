"""
HK Funds KYP/DD Client — wraps the HK Funds REST API (port 8004).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests


class HkFundsClient:
    """Synchronous HTTP client for the HK Fund KYP/DD API."""

    def __init__(self, base_url: str = "http://localhost:8004", timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()

    def _get(self, path: str, params: Optional[dict] = None) -> Any:
        resp = self._session.get(f"{self.base_url}{path}", params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, params: Optional[dict] = None, json: Optional[dict] = None) -> Any:
        resp = self._session.post(f"{self.base_url}{path}", params=params, json=json, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def _put(self, path: str, params: Optional[dict] = None, json: Optional[dict] = None) -> Any:
        resp = self._session.put(f"{self.base_url}{path}", params=params, json=json, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def _delete(self, path: str) -> Any:
        resp = self._session.delete(f"{self.base_url}{path}", timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    # ── Health ──

    def health(self) -> dict:
        return self._get("/api/v1/health")

    # ── Funds ──

    def list_funds(
        self,
        is_derivative_product: bool = None,
        is_complex_product: bool = None,
        complex_product_type: str = None,
        fund_type: str = None,
        domicile: str = None,
        search: str = None,
        limit: int = 100,
    ) -> dict:
        """List SFC-authorized funds with filters."""
        params: dict = {"limit": limit}
        if is_derivative_product is not None:
            params["is_derivative_product"] = is_derivative_product
        if is_complex_product is not None:
            params["is_complex_product"] = is_complex_product
        if complex_product_type:
            params["complex_product_type"] = complex_product_type
        if fund_type:
            params["fund_type"] = fund_type
        if domicile:
            params["domicile"] = domicile
        if search:
            params["search"] = search
        return self._get("/api/v1/funds", params)

    def get_fund(self, fund_id: int) -> dict:
        """Get single fund detail with classification, manager, documents."""
        return self._get(f"/api/v1/funds/{fund_id}")

    def fund_stats(self) -> dict:
        """Aggregate fund statistics."""
        return self._get("/api/v1/funds/stats")

    def complex_products(self, complex_product_type: str = None, limit: int = 100) -> dict:
        """List §5.5 complex products."""
        params = {"limit": limit}
        if complex_product_type:
            params["complex_product_type"] = complex_product_type
        return self._get("/api/v1/funds/complex", params)

    def derivative_products(self, limit: int = 100) -> dict:
        """List §5.1A derivative products."""
        return self._get("/api/v1/funds/derivatives", {"limit": limit})

    def search_funds(self, q: str, limit: int = 50) -> dict:
        """Full-text search across fund names, ISIN, authorization number."""
        return self._get("/api/v1/funds/search", {"q": q, "limit": limit})

    def classify_fund(self, fund_id: int, is_derivative_product: bool,
                      is_complex_product: bool, complex_product_type: str,
                      reason: str = "") -> dict:
        """Manually set fund classification."""
        return self._post(f"/api/v1/funds/{fund_id}/classify", params={
            "is_derivative_product": is_derivative_product,
            "is_complex_product": is_complex_product,
            "complex_product_type": complex_product_type,
            "reason": reason,
        })

    # ── KYP Dimensions ──

    def get_kyp(self, fund_id: int) -> list:
        """Get all 10 KYP dimensions for a fund."""
        return self._get(f"/api/v1/funds/{fund_id}/kyp")

    def update_kyp(self, fund_id: int, dimension: str, updates: dict) -> dict:
        """Update a single KYP dimension."""
        return self._put(f"/api/v1/funds/{fund_id}/kyp/{dimension}", json=updates)

    def kyp_history(self, fund_id: int, limit: int = 50) -> list:
        """Get KYP audit trail for a fund."""
        return self._get(f"/api/v1/funds/{fund_id}/kyp/history", {"limit": limit})

    def kyp_gaps(self, limit: int = 50) -> list:
        """Get funds with incomplete KYP assessments."""
        return self._get("/api/v1/kyp/gaps", {"limit": limit})

    def init_kyp(self, fund_id: int) -> dict:
        """Initialize all 10 KYP dimensions for a fund."""
        return self._post(f"/api/v1/funds/{fund_id}/kyp/init")

    # ── Risk Ratings ──

    def get_risk_rating(self, fund_id: int) -> dict:
        """Get risk rating for a single fund."""
        return self._get(f"/api/v1/funds/{fund_id}/risk-rating")

    def all_risk_ratings(self, risk_category: str = None) -> list:
        """Get all fund risk ratings, optionally filtered by category."""
        params = {}
        if risk_category:
            params["risk_category"] = risk_category
        return self._get("/api/v1/risk-ratings", params)

    def override_risk_rating(self, fund_id: int, new_score: float,
                             new_category: str, reason: str,
                             overridden_by: str = "api") -> dict:
        """Manually override a fund's risk rating."""
        return self._put(f"/api/v1/funds/{fund_id}/risk-rating/override", params={
            "new_score": new_score, "new_category": new_category,
            "reason": reason, "overridden_by": overridden_by,
        })

    def calculate_risk_ratings(self, fund_id: int = None) -> dict:
        """Calculate risk ratings. If fund_id given, rate single fund; else all."""
        params = {}
        if fund_id:
            params["fund_id"] = fund_id
        return self._post("/api/v1/risk-ratings/calculate", params=params)

    # ── Managers ──

    def list_managers(self, license_type: str = None, license_status: str = "active",
                      has_enforcement: bool = None, search: str = None,
                      limit: int = 100) -> dict:
        """List fund managers (SFC licensed corporations)."""
        params: dict = {"license_status": license_status, "limit": limit}
        if license_type:
            params["license_type"] = license_type
        if has_enforcement is not None:
            params["has_enforcement"] = has_enforcement
        if search:
            params["search"] = search
        return self._get("/api/v1/managers", params)

    def manager_stats(self) -> dict:
        """Manager statistics."""
        return self._get("/api/v1/managers/stats")

    def get_manager(self, manager_id: int) -> dict:
        """Get manager detail with funds and regulatory history."""
        return self._get(f"/api/v1/managers/{manager_id}")

    def manager_funds(self, manager_id: int, limit: int = 200) -> dict:
        """Get funds managed by a specific manager."""
        return self._get(f"/api/v1/managers/{manager_id}/funds", {"limit": limit})

    def manager_regulatory(self, manager_id: int, limit: int = 100) -> dict:
        """Get regulatory/enforcement history for a manager."""
        return self._get(f"/api/v1/managers/{manager_id}/regulatory", {"limit": limit})

    # ── Manager DD ──

    def get_manager_dd(self, manager_id: int) -> list:
        """Get 10-dimension DD assessment for a manager."""
        return self._get(f"/api/v1/managers/{manager_id}/dd")

    def update_manager_dd(self, manager_id: int, dimension: str, updates: dict) -> dict:
        """Update a single manager DD dimension."""
        return self._put(f"/api/v1/managers/{manager_id}/dd/{dimension}", json=updates)

    def manager_dd_gaps(self, limit: int = 50) -> list:
        """Get managers with incomplete DD assessments."""
        return self._get("/api/v1/managers/dd/gaps", {"limit": limit})

    def init_manager_dd(self, manager_id: int) -> dict:
        """Initialize all 10 DD dimensions for a manager."""
        return self._post(f"/api/v1/managers/{manager_id}/dd/init")

    # ── Non-Authorized Funds ──

    def list_non_authorized_funds(self, distribution_restriction: str = None,
                                   is_active: bool = True, limit: int = 100) -> list:
        """List non-SFC-authorized funds (PI-only)."""
        params = {"is_active": is_active, "limit": limit}
        if distribution_restriction:
            params["distribution_restriction"] = distribution_restriction
        return self._get("/api/v1/non-authorized-funds", params)

    def get_non_authorized_fund(self, fund_id: int) -> dict:
        """Get a single non-authorized fund."""
        return self._get(f"/api/v1/non-authorized-funds/{fund_id}")

    def create_non_authorized_fund(self, records: list) -> dict:
        """Create non-authorized fund records."""
        return self._post("/api/v1/non-authorized-funds", json=records)

    # ── OFC ──

    def ofc_stats(self) -> dict:
        """Get OFC statistics."""
        return self._get("/api/v1/ofc/stats")

    def ofc_fetch(self) -> dict:
        """Fetch OFC register from SFC."""
        return self._post("/api/v1/ofc/fetch")

    def ofc_init(self) -> dict:
        """Full OFC pipeline (fetch + classify + rate + KYP)."""
        return self._post("/api/v1/ofc/init")

    # ── v4: NAV History & Performance ──

    def get_nav_history(self, fund_id: int, start: str = None,
                        end: str = None, limit: int = 500) -> dict:
        """Get NAV time series for a fund."""
        params = {"limit": limit}
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        return self._get(f"/api/v1/funds/{fund_id}/nav-history", params)

    def get_latest_nav(self, fund_id: int) -> dict:
        """Get latest NAV for a fund."""
        return self._get(f"/api/v1/funds/{fund_id}/nav-latest")

    def get_fund_performance(self, fund_id: int) -> dict:
        """Get performance metrics for a fund."""
        return self._get(f"/api/v1/funds/{fund_id}/performance")

    # ── v4: ISIN Lookup ──

    def list_funds_with_isins(self, limit: int = 200, offset: int = 0) -> dict:
        """List funds that have ISIN codes."""
        return self._get("/api/v1/funds/isins", {"limit": limit, "offset": offset})

    def get_fund_by_isin(self, isin: str) -> dict:
        """Look up a fund by its ISIN code."""
        return self._get(f"/api/v1/funds/by-isin/{isin}")

    def import_isins(self) -> dict:
        """Import ISINs from HKEX ListOfSecurities into hk_funds."""
        return self._post("/api/v1/import-isins")

    # ── v4: Holdings, Dividends, Share Classes ──

    def get_fund_holdings(self, fund_id: int, limit: int = 50) -> dict:
        """Get top holdings for a fund."""
        return self._get(f"/api/v1/funds/{fund_id}/holdings", {"limit": limit})

    def get_fund_dividends(self, fund_id: int, limit: int = 50) -> dict:
        """Get dividend history for a fund."""
        return self._get(f"/api/v1/funds/{fund_id}/dividends", {"limit": limit})

    def get_fund_share_classes(self, fund_id: int) -> dict:
        """Get share classes for a fund (ISINs, currencies, hedging)."""
        return self._get(f"/api/v1/funds/{fund_id}/share-classes")

    # ── v4: Manager Website Scraping ──

    def scrape_manager(self, ce_number: str = None) -> dict:
        """Trigger manager website scraping."""
        params = {}
        if ce_number:
            params["ce_number"] = ce_number
        return self._post("/api/v1/managers/scrape", params=params)

    def scrape_status(self) -> dict:
        """Get connector registry status."""
        return self._get("/api/v1/managers/scrape/status")

    # ── Data Operations ──

    def fetch_funds(self, date: str = None) -> dict:
        """Trigger SFC fund list fetch."""
        params = {}
        if date:
            params["date"] = date
        return self._post("/api/v1/fetch-funds", params=params)

    def fetch_managers(self, date: str = None) -> dict:
        """Trigger manager data fetch."""
        params = {}
        if date:
            params["date"] = date
        return self._post("/api/v1/fetch-managers", params=params)

    def classify_all(self) -> dict:
        """Re-run classification engine on all funds."""
        return self._post("/api/v1/classify")

    def link_managers(self) -> dict:
        """Re-run fund-manager linking."""
        return self._post("/api/v1/link-managers")

    def import_funds_csv(self, file_path: str) -> dict:
        """Import fund records from CSV file."""
        with open(file_path, "rb") as f:
            resp = self._session.post(
                f"{self.base_url}/api/v1/import/csv",
                files={"file": f},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return resp.json()

    def import_managers_csv(self, file_path: str) -> dict:
        """Import manager records from CSV/Excel file."""
        with open(file_path, "rb") as f:
            resp = self._session.post(
                f"{self.base_url}/api/v1/import/managers-csv",
                files={"file": f},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return resp.json()

    # ── Rating Templates ──

    def list_templates(self, user_id: str = "system", template_type: str = None) -> dict:
        """List rating templates for a user (system for built-in)."""
        params = {"user_id": user_id}
        if template_type:
            params["template_type"] = template_type
        return self._get("/api/v1/templates", params)

    def get_template(self, template_id: int) -> dict:
        """Get full template with factors and thresholds."""
        return self._get(f"/api/v1/templates/{template_id}")

    def clone_template(self, source_template_id: int, user_id: str,
                       new_name: str = "") -> dict:
        """Clone a template for a user. Returns the new template."""
        return self._post("/api/v1/templates/clone", params={
            "source_template_id": source_template_id,
            "user_id": user_id,
            "new_name": new_name,
        })

    def update_template(self, template_id: int, body: dict) -> dict:
        """Update a user-owned template (weights, thresholds, name, description).
        body: {user_id, name?, description?, factor_weights?, category_thresholds?}"""
        return self._put(f"/api/v1/templates/{template_id}", json=body)

    def delete_template(self, template_id: int) -> dict:
        """Delete a user-owned template."""
        return self._delete(f"/api/v1/templates/{template_id}")

    def compute_ratings(self, template_id: int, user_id: str,
                        target_type: str = "fund", target_id: int = 0) -> dict:
        """Compute ratings using a template. Omit target_id for batch."""
        return self._post(f"/api/v1/templates/{template_id}/compute", json={
            "user_id": user_id,
            "target_type": target_type,
            "target_id": target_id,
        })

    def get_rating_results(self, template_id: int, user_id: str,
                           target_type: str = "fund", limit: int = 100) -> dict:
        """Get rating results for a template+user combination."""
        return self._get(f"/api/v1/templates/{template_id}/results", params={
            "user_id": user_id,
            "target_type": target_type,
            "limit": limit,
        })

    def close(self) -> None:
        self._session.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
