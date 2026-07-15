"""
JPMorgan Asset Management connector.

Uses JPMorgan's FundsMarketingHandler API to extract fund ISINs, performance
data, and fund metadata for Hong Kong funds.

Strategy:
  1. Query getFMAFunds for fund list (~319 share classes for HK, retail + SAR + MPF)
  2. Match funds to hk_funds by name with JPMorgan manager constraint
  3. Store ISINs, performance data, currency, asset class

CE: AAA121 — JPMorgan Funds (Asia) Limited
    (also matches JPMorgan Asset Management (Europe) S.a.r.l.)
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

from hk_funds.manager_connectors.base import BaseManagerConnector, register_connector

logger = logging.getLogger("hk_funds.manager_connectors.jpmorgan")

API_BASE = "https://am.jpmorgan.com/FundsMarketingHandler"


@register_connector
class JPMorganConnector(BaseManagerConnector):
    """Extracts fund data from JPMorgan's FundsMarketingHandler API."""

    manager_ce_numbers = ["AAA121"]
    base_url = "https://am.jpmorgan.com"

    request_delay: float = 0.5
    request_timeout: int = 30

    _JPM_MANAGER_SQL = (
        "AND (LOWER(fund_manager_name_en) LIKE '%jpmorgan%'"
        " OR LOWER(fund_manager_name_en) LIKE '%j.p. morgan%'"
        " OR LOWER(fund_manager_name_en) LIKE '%jpm%')"
    )

    def __init__(self):
        super().__init__()
        # Access the session property to ensure it's created, then add headers
        self.session.headers.update({
            "Accept": "application/json",
            "Referer": "https://am.jpmorgan.com/hk/en/asset-management/per/funds/",
        })

    # ── Fund List ───────────────────────────────────────────────

    def get_fund_list(self) -> List[Dict[str, Any]]:
        """Get all funds from JPMorgan HK (retail, SAR, MPF)."""
        all_funds = []
        for fund_type in ["retail-funds", "sar-funds", "provident-funds", "mainland-hk-mrf-funds"]:
            params = f"country=hk&role=per&language=en&fundType={fund_type}"
            try:
                resp = self._get(f"{API_BASE}/getFMAFunds?{params}")
                data = resp.json()
                funds = data.get("funds", [])
                for f in funds:
                    if isinstance(f, dict):
                        f["_fund_type"] = fund_type
                all_funds.extend(funds)
                logger.debug(f"JPMorgan {fund_type}: {len(funds)} funds")
            except Exception as e:
                logger.warning(f"JPMorgan {fund_type} fetch failed: {e}")

        logger.info(f"JPMorgan: {len(all_funds)} total funds across all types")
        return all_funds

    # ── Name Matching ──────────────────────────────────────────

    def _match_fund_name(self, conn, jpm_name: str) -> Optional[int]:
        """Match JPMorgan fund/share class name to hk_funds.id.

        JPMorgan names follow patterns like:
          - "JPMorgan SAR American - Class A (acc) - USD"
          - "JPMorgan Multi Income (acc) - HKD"
          - "JPMorgan China A-Share Opportunities (acc) - RMB"

        We extract the base fund name (before share class) and match it.
        """
        if not jpm_name:
            return None

        # Extract base fund name: everything before " - Class" or " (acc)" or " (dist)"
        base_name = jpm_name.strip()
        # Remove share class suffix patterns
        base_name = re.sub(r"\s*-\s*Class\s+[A-Z].*$", "", base_name, flags=re.IGNORECASE)
        base_name = re.sub(r"\s*\(acc\).*$", "", base_name, flags=re.IGNORECASE)
        base_name = re.sub(r"\s*\(dist\).*$", "", base_name, flags=re.IGNORECASE)
        base_name = re.sub(r"\s*\(inc\).*$", "", base_name, flags=re.IGNORECASE)

        candidates = [base_name]
        # Also try stripping "JPMorgan " prefix
        for prefix in ["JPMorgan ", "JPM ", "J.P. Morgan "]:
            if base_name.startswith(prefix):
                candidates.append(base_name[len(prefix):])

        for candidate in candidates:
            c = re.sub(r"\s+", " ", candidate.lower().strip())
            c = re.sub(r"\s+(fund|class\s+\w+|etf)\s*$", "", c, flags=re.IGNORECASE)

            for query, params in [
                ("LOWER(fund_name_en) = ?", [c]),
                ("LOWER(fund_name_en) LIKE ?", [f"%{c}%"]),
            ]:
                row = conn.execute(
                    f"""SELECT id, fund_name_en FROM hk_funds
                       WHERE {query} AND is_active = true
                       {self._JPM_MANAGER_SQL}
                       LIMIT 1""",
                    params,
                ).fetchone()
                if row:
                    return row[0]

            # Word-level matching
            keywords = [w for w in c.split() if len(w) > 3 and w not in ("fund", "class", "etf", "class")]
            if len(keywords) >= 2:
                conditions = " AND ".join(["LOWER(fund_name_en) LIKE ?" for _ in keywords])
                params = [f"%{kw}%" for kw in keywords]
                row = conn.execute(
                    f"""SELECT id, fund_name_en FROM hk_funds
                       WHERE {conditions} AND is_active = true
                       {self._JPM_MANAGER_SQL}
                       LIMIT 1""",
                    params,
                ).fetchone()
                if row:
                    return row[0]

        return None

    # ── Main Interface ─────────────────────────────────────────

    def get_fund_details(self, isin: str) -> Optional[Dict[str, Any]]:
        """Not implemented — JPMorgan doesn't expose a public fund detail API."""
        return None

    def scrape_and_store(self, conn, date_str: str = None) -> Dict[str, int]:
        from hk_funds.storage import update_fund_from_manager, upsert_fund_performance

        today = date_str or datetime.now().strftime("%Y-%m-%d")
        stats = {
            "funds_found": 0, "matched": 0, "isins_updated": 0,
            "navs_stored": 0, "dividends_stored": 0, "details_updated": 0,
            "perf_stored": 0, "errors": 0,
        }

        try:
            funds = self.get_fund_list()
            stats["funds_found"] = len(funds)

            # Deduplicate by base fund name to avoid matching multiple share classes
            processed_names: set = set()

            for idx, fund in enumerate(funds):
                if not isinstance(fund, dict):
                    continue

                isin = fund.get("isin", "")
                name = fund.get("name", "")
                currency = fund.get("currencyCode", "")
                marketing_cat = fund.get("marketingCategory", "")
                fund_type = fund.get("_fund_type", "")
                perf = fund.get("atNavPerformanceReturn", {})

                if not isin or not name:
                    continue

                # Extract base fund name for dedup matching
                base_name = re.sub(r"\s*-\s*Class\s+[A-Z].*$", "", name, flags=re.IGNORECASE)
                base_name = re.sub(r"\s*\(acc\).*$", "", base_name, flags=re.IGNORECASE)
                base_name = re.sub(r"\s*\(dist\).*$", "", base_name, flags=re.IGNORECASE)
                base_name = re.sub(r"\s*\(inc\).*$", "", base_name, flags=re.IGNORECASE)
                base_name = base_name.strip()

                if base_name in processed_names:
                    continue
                processed_names.add(base_name)

                # Match to SFC register
                hk_fund_id = self._match_fund_name(conn, name)

                if not hk_fund_id:
                    if (idx + 1) % 50 == 0:
                        logger.info(
                            f"  [{idx + 1}/{len(funds)}] "
                            f"Matched={stats['matched']} (no match: {name[:60]})"
                        )
                    continue

                stats["matched"] += 1

                # Build data dict
                data = {
                    "isin": isin,
                    "asset_class": marketing_cat,
                    "nav_currency": currency,
                    "product_url": f"https://am.jpmorgan.com/hk/en/asset-management/per/products/fund-explorer?isin={isin}",
                    "source_type": "manager_website",
                }

                # Add performance data
                if isinstance(perf, dict):
                    for k, v in perf.items():
                        if v is not None:
                            data[f"perf_{k}"] = v

                # Store fund details
                if update_fund_from_manager(conn, hk_fund_id, data):
                    stats["details_updated"] += 1

                # Set ISIN
                if isin:
                    update_fund_from_manager(conn, hk_fund_id, {"isin": isin})
                    stats["isins_updated"] += 1

                # Store performance data from JPMorgan API
                if isinstance(perf, dict) and perf:
                    try:
                        perf_metrics = {
                            "ytd_return_pct": perf.get("ytd"),
                            "return_1m_pct": perf.get("mt1"),
                            "return_3m_pct": perf.get("mt3"),
                            "return_6m_pct": perf.get("mt6"),
                            "return_1y_pct": perf.get("yr1"),
                        }
                        # Annualize multi-year cumulative returns
                        yr3 = perf.get("yr3")
                        if yr3 is not None and yr3 > -1:
                            perf_metrics["return_3y_annualized_pct"] = (
                                (1 + yr3) ** (1 / 3) - 1
                            )
                        yr5 = perf.get("yr5")
                        if yr5 is not None and yr5 > -1:
                            perf_metrics["return_5y_annualized_pct"] = (
                                (1 + yr5) ** (1 / 5) - 1
                            )
                        # Convert decimals to percentages
                        for k in list(perf_metrics.keys()):
                            if perf_metrics[k] is not None:
                                perf_metrics[k] = round(perf_metrics[k] * 100, 4)
                        perf_metrics["data_points_used"] = 0
                        perf_metrics["calculation_date"] = perf.get(
                            "performanceReturnEffectiveDate", today
                        )
                        upsert_fund_performance(conn, hk_fund_id, perf_metrics)
                        stats["perf_stored"] += 1
                    except Exception as e:
                        logger.debug(f"Performance upsert failed for {name[:40]}: {e}")

                if (idx + 1) % 50 == 0:
                    logger.info(
                        f"  [{idx + 1}/{len(funds)}] "
                        f"Matched={stats['matched']} "
                        f"ISINs={stats['isins_updated']} "
                        f"Perf={stats['perf_stored']}"
                    )

            logger.info(
                f"JPMorgan scrape complete: "
                f"Matched={stats['matched']}/{stats['funds_found']}, "
                f"ISINs={stats['isins_updated']}, "
                f"Perf={stats['perf_stored']}, "
                f"Details={stats['details_updated']}, "
                f"Errors={stats['errors']}"
            )

        except Exception as e:
            logger.error(f"JPMorgan scrape failed: {e}")
            stats["errors"] += 1

        return stats
