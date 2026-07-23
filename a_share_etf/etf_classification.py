"""
ETF → Sector classification via keyword matching on ETF name.

Priority: broad-based index first, then sector. First-match-wins.
"""

from __future__ import annotations

# ── Sector definitions: (sector_label, [keywords]) in priority order ──
# Broad-based indices first, then industry sectors
_CLASSIFICATION_RULES: list[tuple[str, list[str]]] = [
    ("沪深300", ["沪深300"]),
    ("创业板", ["创业板"]),
    ("科创板", ["科创50", "科创100", "科创创业50", "科创创业", "科创板"]),
    ("中证500", ["中证500"]),
    ("中证2000", ["中证2000"]),
    ("上证50", ["上证50"]),
    ("A500", ["中证A500", "A500ETF", "A500"]),  # A500 before A50 to avoid substring conflict
    ("A50", ["中证A50", "A50ETF", "A50指数"]),
    ("信息技术", [
        "信息技术", "科技", "半导体", "芯片", "人工智能", "AI", "TMT",
        "通信", "电子", "计算机", "软件", "大数据", "云计算", "5G",
        "物联网", "机器人", "数字经济", "信创", "算力", "工业母机",
    ]),
    ("消费", [
        "消费", "食品", "饮料", "酒ETF", "家电", "零售", "农业",
        "畜牧", "养殖", "旅游", "影视", "传媒", "游戏", "教育",
    ]),
    ("医药", [
        "医药", "医疗", "生物医药", "创新药", "中药", "疫苗",
        "器械", "医械", "精准医疗", "医疗服务",
    ]),
    ("券商", ["券商", "证券"]),
    ("金融地产", [
        "金融", "银行", "保险", "地产", "房地产", "非银",
        "港股通金融", "金融科技",
    ]),
    ("军工", ["军工", "国防", "军事", "航空航天"]),
    ("资源品", [
        "资源", "有色", "煤炭", "钢铁", "稀土", "材料",
        "黄金", "矿业", "化工", "石化", "油气", "能源ETF",
        "电力ETF", "公用事业", "基建", "建材", "运输", "物流",
    ]),
    ("新能源+汽车", [
        "新能源", "光伏", "风电", "锂电", "电池", "新能源车",
        "汽车", "碳中和", "绿色电力", "储能", "氢能",
    ]),
]


def classify_etf(name: str) -> str:
    """Classify an ETF by its name. Returns sector label (Chinese)."""
    for sector, keywords in _CLASSIFICATION_RULES:
        for kw in keywords:
            if kw in name:
                return sector
    return "其他"


def list_sectors() -> list[str]:
    """Return all sector labels in priority order."""
    return [s for s, _ in _CLASSIFICATION_RULES] + ["其他"]
