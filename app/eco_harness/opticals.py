"""
Global Optical Communication Company Financials — yfinance.

Tracks key optical supply chain companies across 4 regions:
  - US: COHR, LITE, FN, ANET, GLW, CIEN, AAOI, CLS, CRDO
  - Taiwan: 2330.TW(TSMC), 3081.TWO, 3234.TWO, 3105.TWO, 3363.TWO, 4977.TW, 4979.TWO
  - Japan: 5801.T, 5802.T, 5803.T
  - Korea: 005930.KS, 000660.KS

Data: quarterly/annual revenue and net income via yfinance (free, no API key).

Rate limiting: min 3-second gap between ticker fetches to avoid Yahoo IP throttling
on server deployments. Retries up to 3x with exponential backoff.
"""

from __future__ import annotations

import logging
import time

import pandas as pd

logger = logging.getLogger("eco_data.opticals")

# Minimum seconds between calls to different yfinance tickers.
# Servers get throttled more aggressively than residential IPs.
MIN_FETCH_INTERVAL = 3.0
MAX_RETRIES = 3
RETRY_BASE_DELAY = 5.0


class OpticalsHarness:
    """Global optical communication supply chain financials via yfinance.

    Rate-limited: enforces a minimum interval between unique ticker fetches
    and retries with exponential backoff on failure.
    """

    _YF_CACHE: dict = {}
    _last_fetch: float = 0.0

    @classmethod
    def _rate_limit(cls):
        """Enforce minimum interval between ticker fetches."""
        now = time.monotonic()
        wait = cls._last_fetch + MIN_FETCH_INTERVAL - now
        if wait > 0:
            time.sleep(wait)
        cls._last_fetch = time.monotonic()

    def _get_ticker(self, symbol: str):
        """Get cached yfinance Ticker, respecting rate limit for new tickers."""
        if symbol not in self._YF_CACHE:
            self._rate_limit()
            import yfinance as yf
            logger.debug(f"Creating yfinance Ticker: {symbol}")
            self._YF_CACHE[symbol] = yf.Ticker(symbol)
        return self._YF_CACHE[symbol]

    def _retry_fetch(self, fetch_fn, symbol: str, metric: str) -> pd.DataFrame:
        """Call fetch_fn with retry + exponential backoff."""
        last_err = None
        for attempt in range(MAX_RETRIES):
            try:
                return fetch_fn(symbol)
            except Exception as e:
                last_err = e
                if attempt < MAX_RETRIES - 1:
                    delay = RETRY_BASE_DELAY * (2 ** attempt)
                    logger.warning(
                        f"yfinance {symbol} {metric} attempt {attempt + 1} failed: {e}. "
                        f"Retrying in {delay}s..."
                    )
                    time.sleep(delay)
        logger.error(f"yfinance {symbol} {metric} failed after {MAX_RETRIES} attempts: {last_err}")
        return pd.DataFrame(columns=["date", "value"])

    def _quarterly_revenue(self, symbol: str) -> pd.DataFrame:
        def _fetch(s: str) -> pd.DataFrame:
            self._rate_limit()
            tk = self._get_ticker(s)
            qf = tk.quarterly_financials
            if qf is None or qf.empty:
                return pd.DataFrame(columns=["date", "value"])
            for idx in qf.index:
                if "Total Revenue" in str(idx):
                    return self._series_to_df(qf.loc[idx])
            return pd.DataFrame(columns=["date", "value"])
        return self._retry_fetch(_fetch, symbol, "revenue")

    def _quarterly_net_income(self, symbol: str) -> pd.DataFrame:
        def _fetch(s: str) -> pd.DataFrame:
            self._rate_limit()
            tk = self._get_ticker(s)
            qf = tk.quarterly_financials
            if qf is None or qf.empty:
                return pd.DataFrame(columns=["date", "value"])
            for idx in qf.index:
                if str(idx).strip() == "Net Income":
                    return self._series_to_df(qf.loc[idx])
            for idx in qf.index:
                if "Net Income" in str(idx) and "Common" in str(idx):
                    return self._series_to_df(qf.loc[idx])
            return pd.DataFrame(columns=["date", "value"])
        return self._retry_fetch(_fetch, symbol, "net_income")

    def _annual_revenue(self, symbol: str) -> pd.DataFrame:
        def _fetch(s: str) -> pd.DataFrame:
            self._rate_limit()
            tk = self._get_ticker(s)
            af = tk.financials
            if af is None or af.empty:
                return pd.DataFrame(columns=["date", "value"])
            for idx in af.index:
                if str(idx).strip() in ("Total Revenue", "Operating Revenue"):
                    return self._series_to_df(af.loc[idx])
            return pd.DataFrame(columns=["date", "value"])
        return self._retry_fetch(_fetch, symbol, "annual_revenue")

    @staticmethod
    def _series_to_df(s: pd.Series) -> pd.DataFrame:
        df = s.reset_index()
        df.columns = ["date", "value"]
        df["date"] = pd.to_datetime(df["date"])
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        return df.dropna(subset=["value"]).sort_values("date").reset_index(drop=True)

    # ═══════════════════════════════════════════════════════════════
    # US — Optical components, modules, and networking
    # ═══════════════════════════════════════════════════════════════

    def cohr_revenue(self) -> pd.DataFrame:
        """Coherent quarterly revenue (USD)."""
        return self._quarterly_revenue("COHR")

    def cohr_net_income(self) -> pd.DataFrame:
        """Coherent quarterly net income (USD)."""
        return self._quarterly_net_income("COHR")

    def lite_revenue(self) -> pd.DataFrame:
        """Lumentum quarterly revenue (USD)."""
        return self._quarterly_revenue("LITE")

    def lite_net_income(self) -> pd.DataFrame:
        """Lumentum quarterly net income (USD)."""
        return self._quarterly_net_income("LITE")

    def fn_revenue(self) -> pd.DataFrame:
        """Fabrinet quarterly revenue (USD)."""
        return self._quarterly_revenue("FN")

    def anet_revenue(self) -> pd.DataFrame:
        """Arista Networks quarterly revenue (USD)."""
        return self._quarterly_revenue("ANET")

    def anet_net_income(self) -> pd.DataFrame:
        """Arista Networks quarterly net income (USD)."""
        return self._quarterly_net_income("ANET")

    def glw_revenue(self) -> pd.DataFrame:
        """Corning quarterly revenue (USD)."""
        return self._quarterly_revenue("GLW")

    def glw_net_income(self) -> pd.DataFrame:
        """Corning quarterly net income (USD)."""
        return self._quarterly_net_income("GLW")

    def cien_revenue(self) -> pd.DataFrame:
        """Ciena quarterly revenue (USD)."""
        return self._quarterly_revenue("CIEN")

    def aaoi_revenue(self) -> pd.DataFrame:
        """Applied Optoelectronics quarterly revenue (USD)."""
        return self._quarterly_revenue("AAOI")

    def cls_revenue(self) -> pd.DataFrame:
        """Celestica quarterly revenue (USD)."""
        return self._quarterly_revenue("CLS")

    def crdo_revenue(self) -> pd.DataFrame:
        """Credo Technology quarterly revenue (USD)."""
        return self._quarterly_revenue("CRDO")

    # ═══════════════════════════════════════════════════════════════
    # Taiwan — SiPh foundry, epi-wafers, optical components
    # ═══════════════════════════════════════════════════════════════

    def tsmc_tw_revenue(self) -> pd.DataFrame:
        """TSMC (2330.TW) quarterly revenue (TWD)."""
        return self._quarterly_revenue("2330.TW")

    def tsmc_tw_net_income(self) -> pd.DataFrame:
        """TSMC (2330.TW) quarterly net income (TWD)."""
        return self._quarterly_net_income("2330.TW")

    def landmark_3081_revenue(self) -> pd.DataFrame:
        """联亚 (3081.TWO) quarterly revenue (TWD)."""
        return self._quarterly_revenue("3081.TWO")

    def truelight_3234_revenue(self) -> pd.DataFrame:
        """光环 (3234.TWO) quarterly revenue (TWD)."""
        return self._quarterly_revenue("3234.TWO")

    def win_semi_3105_revenue(self) -> pd.DataFrame:
        """稳懋 (3105.TWO) quarterly revenue (TWD)."""
        return self._quarterly_revenue("3105.TWO")

    def foci_3363_revenue(self) -> pd.DataFrame:
        """上诠 (3363.TWO) quarterly revenue (TWD)."""
        return self._quarterly_revenue("3363.TWO")

    def prime_4977_revenue(self) -> pd.DataFrame:
        """众达-KY (4977.TW) quarterly revenue (TWD)."""
        return self._quarterly_revenue("4977.TW")

    def luxnet_4979_revenue(self) -> pd.DataFrame:
        """华星光 (4979.TWO) quarterly revenue (TWD)."""
        return self._quarterly_revenue("4979.TWO")

    # ═══════════════════════════════════════════════════════════════
    # Japan — Fiber preforms, specialty fiber, optical cables
    # (annual: yfinance only has annual financials for JP tickers)
    # ═══════════════════════════════════════════════════════════════

    def furukawa_5801_revenue(self) -> pd.DataFrame:
        """古河电工 (5801.T) annual revenue (JPY)."""
        return self._annual_revenue("5801.T")

    def sumitomo_5802_revenue(self) -> pd.DataFrame:
        """住友电工 (5802.T) annual revenue (JPY)."""
        return self._annual_revenue("5802.T")

    def fujikura_5803_revenue(self) -> pd.DataFrame:
        """藤仓 (5803.T) annual revenue (JPY)."""
        return self._annual_revenue("5803.T")

    # ═══════════════════════════════════════════════════════════════
    # Korea — Memory/storage for AI, optical components
    # ═══════════════════════════════════════════════════════════════

    def samsung_005930_revenue(self) -> pd.DataFrame:
        """Samsung Electronics (005930.KS) quarterly revenue (KRW)."""
        return self._quarterly_revenue("005930.KS")

    def samsung_005930_net_income(self) -> pd.DataFrame:
        """Samsung Electronics (005930.KS) quarterly net income (KRW)."""
        return self._quarterly_net_income("005930.KS")

    def sk_hynix_000660_revenue(self) -> pd.DataFrame:
        """SK Hynix (000660.KS) quarterly revenue (KRW)."""
        return self._quarterly_revenue("000660.KS")

    def sk_hynix_000660_net_income(self) -> pd.DataFrame:
        """SK Hynix (000660.KS) quarterly net income (KRW)."""
        return self._quarterly_net_income("000660.KS")
