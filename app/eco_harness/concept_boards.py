"""
A-Share Concept Board Indices — AKShare / East Money.

Covers niche hardware/AI supply chain sectors not covered by macro sources:
  - 光纤/光芯片 → 光通信模块, CPO概念
  - AI算力 → 算力概念, 数据中心, 液冷概念
  - 芯片 → AI芯片, 存储芯片, 国产芯片, 汽车芯片, 半导体概念
  - 小金属磷化铟 → 小金属概念, 磷化工
  - 覆铜板 → PCB (无独立概念板块, PCB覆盖生益科技等CCL厂商)

Data source: East Money (via AKShare stock_board_concept_hist_em).
Each concept board is a market-cap-weighted index of constituent A-shares.
"""

from __future__ import annotations

import logging
import time

import pandas as pd

logger = logging.getLogger("eco_data.concept_boards")

# ── Concept board registry: method_name → (code, label, tags) ──

BOARDS = {
    # ── 光通信 / 光芯片 ──
    "optical_comm":     ("BK1136", "光通信模块", "光通信,光纤,光芯片,光模块"),
    "cpo":              ("BK1128", "CPO概念", "光芯片,CPO,光模块,硅光"),
    # ── AI算力 ──
    "computing_power":  ("BK1134", "算力概念", "AI算力,数据中心,服务器"),
    "data_center":      ("BK0922", "数据中心", "AI算力,数据中心,液冷"),
    "liquid_cooling":   ("BK1138", "液冷概念", "AI算力,数据中心,液冷,散热"),
    # ── 芯片 / 半导体 ──
    "ai_chip":          ("BK1127", "AI芯片", "芯片,AI芯片,半导体"),
    "storage_chip":     ("BK1137", "存储芯片", "芯片,存储,半导体,HBM"),
    "domestic_chip":    ("BK0891", "国产芯片", "芯片,国产替代,半导体"),
    "auto_chip":        ("BK0969", "汽车芯片", "芯片,汽车,半导体,IGBT"),
    "semiconductor":    ("BK0917", "半导体概念", "芯片,半导体,集成电路"),
    # ── 小金属 / 磷化铟 ──
    "minor_metals":     ("BK0695", "小金属概念", "小金属,磷化铟,镓,锗,稀土"),
    "phosphate_chem":   ("BK1010", "磷化工", "磷化铟,磷化工,化工"),
    # ── 覆铜板 (CCL) ──
    "pcb":              ("BK0877", "PCB", "覆铜板,PCB,印制电路,CCL"),
    # ── 光纤 / 光缆 ──
    "optical_fiber":    ("BK1660", "光纤概念", "光通信,光纤,光缆"),
    # ── 先进封装 ──
    "glass_substrate":  ("BK1175", "玻璃基板", "先进封装,玻璃载板,AI芯片"),
    "chiplet":          ("BK1101", "Chiplet概念", "先进封装,芯粒,chiplet"),
    # ── 数据中心互连 ──
    "copper_connect":   ("BK1168", "铜缆高速连接", "AI数据中心,连接器,铜缆"),
    "f5g":              ("BK1088", "F5G概念", "光通信,F5G,全光网络"),
    "east_west_compute": ("BK1064", "东数西算", "算力,数据中心,东数西算"),
    # ── 光刻 / 显示 / 被动元件 ──
    "lithography":      ("BK0884", "光刻机(胶)", "半导体,光刻,光刻胶"),
    "micro_led":        ("BK0948", "MicroLED", "显示,MicroLED"),
    "mlcc":             ("BK0890", "MLCC", "被动元件,MLCC,电容"),
}


def fetch_board(code: str, start: str = "20200101", end: str = "") -> pd.DataFrame:
    """Fetch concept board daily K-line via AKShare (East Money backend).

    Retries up to 5x with jittered backoff (5s → 10s → 20s → 30s → 45s).
    """
    import random
    end = end or pd.Timestamp.now().strftime("%Y%m%d")

    import akshare as ak
    last_err = None
    for attempt in range(5):
        try:
            raw = ak.stock_board_concept_hist_em(
                symbol=code, period="daily",
                start_date=f"{start[:4]}{start[4:6]}{start[6:]}",
                end_date=f"{end[:4]}{end[4:6]}{end[6:]}",
            )
            if raw is None or len(raw) == 0:
                return pd.DataFrame()

            # AKShare returns: 日期,开盘,收盘,最高,最低,涨跌幅,涨跌额,成交量,成交额,振幅,换手率
            df = pd.DataFrame({
                "date": pd.to_datetime(raw["日期"]),
                "open": raw["开盘"].astype(float),
                "close": raw["收盘"].astype(float),
                "high": raw["最高"].astype(float),
                "low": raw["最低"].astype(float),
                "volume": raw["成交量"].astype(float),
                "amount": raw["成交额"].astype(float),
                "change_pct": raw["涨跌幅"].astype(float),
            })
            return df
        except Exception as e:
            last_err = e
            if attempt < 3:
                delay = 5 * (2 ** attempt) + random.uniform(0, 3)
                logger.warning(
                    f"Board {code} attempt {attempt + 1}/4 failed: {e}. "
                    f"Retrying in {delay:.1f}s..."
                )
                time.sleep(delay)
    logger.error(f"Board {code} failed after 4 attempts: {last_err}")
    raise last_err


class ConceptBoardHarness:
    """Harness for A-share concept board indices (光通信/芯片/AI算力/小金属/覆铜板)."""

    _last_fetch: float = 0.0
    _FETCH_GAP = 2.0  # min seconds between board fetches (server IPs need more gap)

    def __init__(self):
        self._cache: dict[str, pd.DataFrame] = {}

    def _get_board(self, method: str) -> pd.DataFrame:
        if method in self._cache:
            return self._cache[method]

        # Rate limit: min gap between AKShare calls
        now = time.monotonic()
        wait = self._last_fetch + self._FETCH_GAP - now
        if wait > 0:
            time.sleep(wait)

        code, label, _ = BOARDS[method]
        logger.info(f"Fetching concept board: {label} ({code})")
        df = fetch_board(code)
        self._cache[method] = df
        self._last_fetch = time.monotonic()
        return df

    # ── 光通信 / 光芯片 ──

    def optical_comm(self):
        """光通信模块指数 — 光纤/光芯片/光模块 (daily)."""
        df = self._get_board("optical_comm")
        return df[["date", "close"]].rename(columns={"close": "value"})

    def cpo(self):
        """CPO概念指数 — 光芯片/共封装光学/硅光 (daily)."""
        df = self._get_board("cpo")
        return df[["date", "close"]].rename(columns={"close": "value"})

    # ── AI算力 ──

    def computing_power(self):
        """算力概念指数 — AI算力/服务器/IDC (daily)."""
        df = self._get_board("computing_power")
        return df[["date", "close"]].rename(columns={"close": "value"})

    def data_center(self):
        """数据中心指数 — IDC/算力基础设施 (daily)."""
        df = self._get_board("data_center")
        return df[["date", "close"]].rename(columns={"close": "value"})

    def liquid_cooling(self):
        """液冷概念指数 — 数据中心散热/AI服务器液冷 (daily)."""
        df = self._get_board("liquid_cooling")
        return df[["date", "close"]].rename(columns={"close": "value"})

    # ── 芯片 / 半导体 ──

    def ai_chip(self):
        """AI芯片指数 — 人工智能专用芯片 (daily)."""
        df = self._get_board("ai_chip")
        return df[["date", "close"]].rename(columns={"close": "value"})

    def storage_chip(self):
        """存储芯片指数 — NAND/DRAM/HBM (daily)."""
        df = self._get_board("storage_chip")
        return df[["date", "close"]].rename(columns={"close": "value"})

    def domestic_chip(self):
        """国产芯片指数 — 芯片国产替代 (daily)."""
        df = self._get_board("domestic_chip")
        return df[["date", "close"]].rename(columns={"close": "value"})

    def auto_chip(self):
        """汽车芯片指数 — IGBT/MCU/车规芯片 (daily)."""
        df = self._get_board("auto_chip")
        return df[["date", "close"]].rename(columns={"close": "value"})

    def semiconductor(self):
        """半导体概念指数 — 芯片/集成电路/晶圆 (daily)."""
        df = self._get_board("semiconductor")
        return df[["date", "close"]].rename(columns={"close": "value"})

    # ── 小金属 / 磷化铟 ──

    def minor_metals(self):
        """小金属概念指数 — 磷化铟/镓/锗/稀土 (daily)."""
        df = self._get_board("minor_metals")
        return df[["date", "close"]].rename(columns={"close": "value"})

    def phosphate_chem(self):
        """磷化工指数 — 磷化铟上游/磷矿/磷肥 (daily)."""
        df = self._get_board("phosphate_chem")
        return df[["date", "close"]].rename(columns={"close": "value"})

    # ── 光纤 / 光缆 ──

    def optical_fiber(self):
        """光纤概念指数 — 光纤/光缆 (daily)."""
        df = self._get_board("optical_fiber")
        return df[["date", "close"]].rename(columns={"close": "value"})

    # ── 先进封装 ──

    def glass_substrate(self):
        """玻璃基板指数 — 玻璃载板/先进封装 (daily)."""
        df = self._get_board("glass_substrate")
        return df[["date", "close"]].rename(columns={"close": "value"})

    def chiplet(self):
        """Chiplet概念指数 — 先进封装/芯粒 (daily)."""
        df = self._get_board("chiplet")
        return df[["date", "close"]].rename(columns={"close": "value"})

    # ── 数据中心互连 ──

    def copper_connect(self):
        """铜缆高速连接指数 — AI数据中心互连/连接器 (daily)."""
        df = self._get_board("copper_connect")
        return df[["date", "close"]].rename(columns={"close": "value"})

    def f5g(self):
        """F5G概念指数 — 第五代固定网络/全光网络 (daily)."""
        df = self._get_board("f5g")
        return df[["date", "close"]].rename(columns={"close": "value"})

    def east_west_compute(self):
        """东数西算指数 — 算力枢纽/数据中心 (daily)."""
        df = self._get_board("east_west_compute")
        return df[["date", "close"]].rename(columns={"close": "value"})

    # ── 光刻 / 显示 / 被动元件 ──

    def lithography(self):
        """光刻机(胶)指数 — 光刻机/光刻胶 (daily)."""
        df = self._get_board("lithography")
        return df[["date", "close"]].rename(columns={"close": "value"})

    def micro_led(self):
        """MicroLED指数 — MicroLED显示 (daily)."""
        df = self._get_board("micro_led")
        return df[["date", "close"]].rename(columns={"close": "value"})

    def mlcc(self):
        """MLCC指数 — 多层陶瓷电容/被动元件 (daily)."""
        df = self._get_board("mlcc")
        return df[["date", "close"]].rename(columns={"close": "value"})

    # ── 覆铜板 (CCL) ──

    def pcb(self):
        """PCB指数 — 覆铜板/印制电路板/CCL (daily)."""
        df = self._get_board("pcb")
        return df[["date", "close"]].rename(columns={"close": "value"})
