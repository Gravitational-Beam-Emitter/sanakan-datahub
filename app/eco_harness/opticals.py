"""
Global Optical Communication Company Financials — yfinance.

Tracks key optical supply chain companies across 4 regions:
  - US: COHR, LITE, FN, ANET, GLW, CIEN, AAOI, CLS, CRDO
  - Taiwan: 2330.TW(TSMC), 3081.TWO, 3234.TWO, 3105.TWO, 3450.TWO, 3363.TWO, 4977.TW, 4979.TWO, 6442.TWO
  - Japan: 5801.T, 5802.T, 5803.T
  - Korea: 005930.KS, 000660.KS

Data: quarterly revenue and net income via yfinance (free, no API key).
"""

from __future__ import annotations

import pandas as pd


class OpticalsHarness:
    """Global optical communication supply chain financials via yfinance."""

    _YF_CACHE: dict = {}

    def _get_ticker(self, symbol: str):
        if symbol not in self._YF_CACHE:
            import yfinance as yf
            self._YF_CACHE[symbol] = yf.Ticker(symbol)
        return self._YF_CACHE[symbol]

    def _quarterly_revenue(self, symbol: str) -> pd.DataFrame:
        tk = self._get_ticker(symbol)
        qf = tk.quarterly_financials
        if qf is None or qf.empty:
            return pd.DataFrame(columns=["date", "value"])
        for idx in qf.index:
            if "Total Revenue" in str(idx):
                return self._series_to_df(qf.loc[idx])
        return pd.DataFrame(columns=["date", "value"])

    def _quarterly_net_income(self, symbol: str) -> pd.DataFrame:
        tk = self._get_ticker(symbol)
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

    def _quarterly_capex(self, symbol: str) -> pd.DataFrame:
        tk = self._get_ticker(symbol)
        cf = tk.cashflow
        if cf is None or cf.empty:
            return pd.DataFrame(columns=["date", "value"])
        for idx in cf.index:
            if "Capital Expenditure" in str(idx) or "Capital Expenditures" in str(idx):
                df = self._series_to_df(cf.loc[idx])
                df["value"] = df["value"].abs()
                return df
        return pd.DataFrame(columns=["date", "value"])

    @staticmethod
    def _series_to_df(s: pd.Series) -> pd.DataFrame:
        df = s.reset_index()
        df.columns = ["date", "value"]
        df["date"] = pd.to_datetime(df["date"])
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        return df.dropna(subset=["value"]).sort_values("date").reset_index(drop=True)

    def _annual_revenue(self, symbol: str) -> pd.DataFrame:
        """Extract annual total revenue (for markets where quarterly is unavailable, e.g. Japan)."""
        tk = self._get_ticker(symbol)
        af = tk.financials  # annual
        if af is None or af.empty:
            return pd.DataFrame(columns=["date", "value"])
        for idx in af.index:
            if str(idx).strip() in ("Total Revenue", "Operating Revenue"):
                return self._series_to_df(af.loc[idx])
        return pd.DataFrame(columns=["date", "value"])

    def _annual_net_income(self, symbol: str) -> pd.DataFrame:
        """Extract annual net income (for markets where quarterly is unavailable, e.g. Japan)."""
        tk = self._get_ticker(symbol)
        af = tk.financials  # annual
        if af is None or af.empty:
            return pd.DataFrame(columns=["date", "value"])
        for idx in af.index:
            if str(idx).strip() == "Net Income":
                return self._series_to_df(af.loc[idx])
        for idx in af.index:
            if "Net Income" in str(idx) and "Common" in str(idx):
                return self._series_to_df(af.loc[idx])
        return pd.DataFrame(columns=["date", "value"])

    # ═══════════════════════════════════════════════════════════════
    # US — Optical components, modules, and networking
    # ═══════════════════════════════════════════════════════════════

    def cohr_revenue(self) -> pd.DataFrame:
        """Coherent quarterly revenue (USD). Optical transceivers, CPO, lasers — Nvidia optical partner."""
        return self._quarterly_revenue("COHR")

    def cohr_net_income(self) -> pd.DataFrame:
        """Coherent quarterly net income (USD)."""
        return self._quarterly_net_income("COHR")

    def lite_revenue(self) -> pd.DataFrame:
        """Lumentum quarterly revenue (USD). EML lasers, silicon photonics — Nvidia optical partner."""
        return self._quarterly_revenue("LITE")

    def lite_net_income(self) -> pd.DataFrame:
        """Lumentum quarterly net income (USD)."""
        return self._quarterly_net_income("LITE")

    def fn_revenue(self) -> pd.DataFrame:
        """Fabrinet quarterly revenue (USD). Optical packaging and contract manufacturing."""
        return self._quarterly_revenue("FN")

    def anet_revenue(self) -> pd.DataFrame:
        """Arista Networks quarterly revenue (USD). High-speed data center switches for AI clusters."""
        return self._quarterly_revenue("ANET")

    def anet_net_income(self) -> pd.DataFrame:
        """Arista Networks quarterly net income (USD)."""
        return self._quarterly_net_income("ANET")

    def glw_revenue(self) -> pd.DataFrame:
        """Corning quarterly revenue (USD). Fiber optics, optical connectivity — Nvidia $5B partner."""
        return self._quarterly_revenue("GLW")

    def glw_net_income(self) -> pd.DataFrame:
        """Corning quarterly net income (USD)."""
        return self._quarterly_net_income("GLW")

    def cien_revenue(self) -> pd.DataFrame:
        """Ciena quarterly revenue (USD). Long-haul optical networking equipment."""
        return self._quarterly_revenue("CIEN")

    def aaoi_revenue(self) -> pd.DataFrame:
        """Applied Optoelectronics quarterly revenue (USD). Optical transceivers for data centers."""
        return self._quarterly_revenue("AAOI")

    def cls_revenue(self) -> pd.DataFrame:
        """Celestica quarterly revenue (USD). Optical component manufacturing and assembly."""
        return self._quarterly_revenue("CLS")

    def crdo_revenue(self) -> pd.DataFrame:
        """Credo Technology quarterly revenue (USD). High-speed optical connectivity chips."""
        return self._quarterly_revenue("CRDO")

    # ═══════════════════════════════════════════════════════════════
    # Taiwan — SiPh foundry, epi-wafers, optical components
    # ═══════════════════════════════════════════════════════════════

    def tsmc_tw_revenue(self) -> pd.DataFrame:
        """TSMC (2330.TW) quarterly revenue (TWD). Silicon photonics manufacturing, COUPE platform."""
        return self._quarterly_revenue("2330.TW")

    def tsmc_tw_net_income(self) -> pd.DataFrame:
        """TSMC (2330.TW) quarterly net income (TWD)."""
        return self._quarterly_net_income("2330.TW")

    def landmark_3081_revenue(self) -> pd.DataFrame:
        """联亚 (3081.TWO) quarterly revenue (TWD). Epi-wafer for SiPh/PIC lasers."""
        return self._quarterly_revenue("3081.TWO")

    def truelight_3234_revenue(self) -> pd.DataFrame:
        """光环 (3234.TWO) quarterly revenue (TWD). Fiber optic components & optical engines."""
        return self._quarterly_revenue("3234.TWO")

    def win_semi_3105_revenue(self) -> pd.DataFrame:
        """稳懋 (3105.TWO) quarterly revenue (TWD). Compound semiconductor foundry — InP, GaAs, VCSEL."""
        return self._quarterly_revenue("3105.TWO")

    def foci_3363_revenue(self) -> pd.DataFrame:
        """上诠 (3363.TWO) quarterly revenue (TWD). FAU for CPO, 1.6T/6.4T optical engines."""
        return self._quarterly_revenue("3363.TWO")

    def prime_4977_revenue(self) -> pd.DataFrame:
        """众达-KY (4977.TW) quarterly revenue (TWD). Broadcom CPO partner, ELSFP modules."""
        return self._quarterly_revenue("4977.TW")

    def luxnet_4979_revenue(self) -> pd.DataFrame:
        """华星光 (4979.TWO) quarterly revenue (TWD). High-speed optical transceivers."""
        return self._quarterly_revenue("4979.TWO")

    # ═══════════════════════════════════════════════════════════════
    # Japan — Fiber preforms, specialty fiber, optical cables
    # ═══════════════════════════════════════════════════════════════

    def furukawa_5801_revenue(self) -> pd.DataFrame:
        """古河电工 (5801.T) annual revenue (JPY). 10.3% global fiber share, preforms, submarine cables."""
        return self._annual_revenue("5801.T")

    def sumitomo_5802_revenue(self) -> pd.DataFrame:
        """住友电工 (5802.T) annual revenue (JPY). Ultra-low-loss fiber, laser chips, CPO components."""
        return self._annual_revenue("5802.T")

    def fujikura_5803_revenue(self) -> pd.DataFrame:
        """藤仓 (5803.T) annual revenue (JPY). Special fiber, high-power lasers — supplies all US hyperscalers."""
        return self._annual_revenue("5803.T")

    # ═══════════════════════════════════════════════════════════════
    # Korea — Memory/storage for AI, optical components
    # ═══════════════════════════════════════════════════════════════

    def samsung_005930_revenue(self) -> pd.DataFrame:
        """Samsung Electronics (005930.KS) quarterly revenue (KRW). Memory, HBM, optical components."""
        return self._quarterly_revenue("005930.KS")

    def samsung_005930_net_income(self) -> pd.DataFrame:
        """Samsung Electronics (005930.KS) quarterly net income (KRW)."""
        return self._quarterly_net_income("005930.KS")

    def sk_hynix_000660_revenue(self) -> pd.DataFrame:
        """SK Hynix (000660.KS) quarterly revenue (KRW). HBM memory for AI GPUs."""
        return self._quarterly_revenue("000660.KS")

    def sk_hynix_000660_net_income(self) -> pd.DataFrame:
        """SK Hynix (000660.KS) quarterly net income (KRW)."""
        return self._quarterly_net_income("000660.KS")
