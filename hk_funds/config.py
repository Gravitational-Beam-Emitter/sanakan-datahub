"""
HK Fund KYP configuration — URLs, classification rules, rate limits.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path)

# Database
DB_PATH = str(Path(__file__).resolve().parent.parent / "hk_funds.duckdb")

# ═══════════════════════════════════════════════════════════════
#  SFC URLs
# ═══════════════════════════════════════════════════════════════

# SFC authorized UTMF list — productlistWeb search form (working as of 2026-06)
# POST searchBy=COMPANY to get all authorized Unit Trusts and Mutual Funds
SFC_UTMF_SEARCH_URL = "https://apps.sfc.hk/productlistWeb/searchProduct/UTMF.do?lang=EN"

# Monthly list of newly authorized funds (XLS, for incremental updates)
SFC_MONTHLY_LIST_URL = "https://www.sfc.hk/-/media/files/PCIP/Monthly-List"

# SFC authorized fund list — public register landing page (legacy)
SFC_FUND_LIST_URL = "https://www.sfc.hk/en/Regulatory-functions/Products/List-of-publicly-offered-investment-products/"

# SFC licensed corporations public register (old ExtJS app — may be mothballed)
SFC_PUBLICREG_SEARCH_URL = "https://apps.sfc.hk/publicregWeb/searchByRa?locale=en"
SFC_PUBLICREG_JSON_URL = "https://apps.sfc.hk/publicregWeb/searchByRaJson"

# SFC OFC register — all registered OFCs (Public + Private), parsed from HTML
SFC_OFC_REGISTER_URL = "https://apps.sfc.hk/productlistWeb/searchProduct/OFC.do?lang=EN"

# SFC WINGS platform (new) — public register migrated here, API requires auth
SFC_WINGS_URL = "https://wings.sfc.hk/main/"

# SFC individual/public register page on main website
SFC_LC_REGISTER_URL = "https://www.sfc.hk/en/Regulatory-functions/Intermediaries/Licensing/Register-of-licensed-persons-and-registered-institutions"

# SFC complex product list / classification guidance
SFC_COMPLEX_PRODUCTS_URL = "https://www.sfc.hk/en/Rules-and-standards/Suitability-requirement/Non-complex-and-complex-products"

# SFC circulars related to derivatives / complex products
SFC_CIRCULARS_URL = "https://www.sfc.hk/en/News-and-announcements/Circulars"

# HKEX listed securities (ETFs, L&I products)
HKEX_SECURITIES_URL = "https://www.hkex.com.hk/eng/services/trading/securities/securitieslists/ListOfSecurities.xlsx"

# ═══════════════════════════════════════════════════════════════
#  SFC Classification — Dual-Dimension Framework
# ═══════════════════════════════════════════════════════════════
#
#  SFC has TWO independent regulatory classifications:
#
#  §5.1A Derivative Products:
#    Defined by financial nature — value derives from underlying assets.
#    For funds: net derivative exposure > 50% NAV → "derivative fund".
#    Requires: derivative knowledge assessment (§5.1A) + financial
#    capacity check (§5.3).
#
#  §5.5 Complex Products:
#    Defined by retail investor understandability — six-factor test.
#    Factor ① = is it a derivative product?
#    Factors ②–⑥ = secondary market, info transparency, loss > principal,
#    complex payoff, illiquidity/valuation difficulty.
#    Requires: suitability assessment + minimum info + warning statement.
#    Exchange-traded complex products: suitability assessment exempted
#    if unsolicited (no recommendation).
#
#  A fund can be:
#    - derivative + complex (e.g. derivative fund NDE > 50%)
#    - complex only, not derivative (e.g. complex bond, structured note)
#    - derivative only, not complex (rare: plain exchange-traded options)
#    - neither (ordinary fund)
#
# ═══════════════════════════════════════════════════════════════

# ── §5.1A Derivative Product Indicators ──
# Keywords that suggest a fund IS a derivative product
DERIVATIVE_NAME_KEYWORDS_EN = [
    # SFC core indicators
    "derivative", "derivatives",
    "synthetic", "swap-based",
    "leveraged", "inverse",
    "absolute return",
    # Hedge fund / alternative strategies — multi-word to avoid ambiguity
    # "total return" removed: SFC only flags 2/18 total return funds as derivative;
    # the rest are traditional bond funds with NDE << 50%. The 2 real ones
    # are caught by the SFC known list. NDE extractor can re-evaluate.
    "covered call", "buy-write", "buy write",
    "long/short", "long short",
    "managed futures", "arbitrage",
    "multi-strategy", "macro", "quantitative",
    "market neutral", "equity long short", "event driven",
    "global macro", "cta",
    "relative value", "distressed",
    "credit arbitrage", "convertible arbitrage", "statistical arbitrage",
    "fixed income arbitrage", "risk arbitrage", "merger arbitrage",
    "tactical trading", "discretionary", "dynamic long short",
    "capital structure",
]

DERIVATIVE_NAME_KEYWORDS_CN = [
    # SFC core indicators
    "衍生工具", "衍生品",
    "合成",
    "杠杆", "反向",
    "绝对回报",
    # Hedge fund / alternative strategies
    "管理期货", "对冲", "套利", "多策略",
    "宏观", "量化", "市场中性", "股票多空", "事件驱动",
    "期货", "期权", "掉期", "相对价值",
    "绝对收益", "另类投资", "全权委托",
    "股票对冲", "固定收益套利", "可转换套利", "并购套利",
    "资本结构", "风险套利", "信贷套利",
]

# Fund types that are inherently derivative products (§5.1A)
DERIVATIVE_FUND_TYPES = [
    "synthetic_etf",
    "futures_etf",
    "leveraged_inverse_product",
    "leveraged_product",
    "inverse_product",
    "hedge_fund",
    "derivative_fund",
]

# ── §5.5 Complex Product Additional Indicators (beyond factor ①) ──
# Keywords pointing to complex structure (may be complex even if not derivative)
COMPLEX_PRODUCT_NAME_KEYWORDS_EN = [
    # Structured products
    "structured product", "structured note",
    "accumulator", "decumulator",
    "equity linked", "equity-linked",
    "credit linked", "credit-linked",
    "fx linked", "fx-linked",
    "currency linked", "currency-linked",
    "knock-out", "knock-in", "barrier",
    "digital option", "binary option", "binary",
    "autocallable", "range accrual",
    "snowball", "twin-win",
    "bonus enhanced",
    # Additional complex product types
    "total return swap", "credit default swap",
    "contract for difference", "cfd",
    "participation note", "p-note",
    "constant proportion", "cppi",
    "dynamic allocation", "target redemption",
    "reverse convertible", "worst-of", "best-of",
    "lookback", "asian option",
    "callable bull bear", "cbbc",
    "daily leveraged", "daily inverse",
    "volatility index", "vix",
]

COMPLEX_PRODUCT_NAME_KEYWORDS_CN = [
    # Structured products
    "结构性产品", "结构性票据",
    "累计", "累股",
    "股票挂钩", "外汇挂钩", "信贷挂钩", "货币挂钩",
    "敲出", "敲入", "鲨鱼鳍",
    "雪球", "二元", "双赢",
    "区间累积", "可赎回",
    # Additional complex product types
    "总收益互换", "信用违约互换", "差价合约",
    "参与票据", "固定比例", "目标赎回",
    "反向可转换", "最差表现", "最佳表现",
    "亚式期权", "牛熊证",
    "每日杠杆", "每日反向",
    "波动率指数",
]

# ── Complex Product Type Enum (§5.5) ──
COMPLEX_PRODUCT_TYPES = [
    "derivative_fund",   # NDE > 50% NAV, uses derivatives for investment purpose
    "synthetic_etf",     # swap-based replication
    "futures_etf",       # futures-based replication
    "L&I",               # leveraged and/or inverse product
    "hedge_fund",        # UT Code Chapter 8.7
    "structured",        # structured product / ELN / accumulator / decumulator
    "complex_bond",      # bond with special features (perpetual, subordinated, convertible, etc.)
    "security_token",    # tokenised securities
    "non_complex",       # not complex per §5.5 six-factor test
]

# ── Six-Factor Test Keyword Hints (§5.5 + Guidelines Chapter 6) ──
# Factor ① = is_derivative_product (computed, not keyword-based)

# Factor ④: Loss exceeds principal
FACTOR_4_KEYWORDS_EN = [
    "accumulator", "decumulator", "leveraged", "inverse",
    "knock-out", "knock-in", "daily leveraged", "daily inverse",
    "contract for difference", "cfd", "cbbc",
    "callable bull bear", "short bias", "bear market",
]
FACTOR_4_KEYWORDS_CN = ["杠杆", "反向", "累计", "敲出", "敲入",
    "每日杠杆", "每日反向", "牛熊证", "卖空", "熊市"]

# Factor ⑤: Complex payoff structure (multiple variables, complicated formulas)
FACTOR_5_KEYWORDS_EN = [
    "structured", "autocallable", "range accrual",
    "snowball", "binary", "barrier",
    "asian option", "lookback", "worst-of", "best-of",
    "total return swap", "credit default swap",
    "participation note", "p-note", "cppi",
    "reverse convertible", "target redemption",
    "digital option", "knock-out", "knock-in",
    "bonus enhanced", "twin-win",
]
FACTOR_5_KEYWORDS_CN = ["结构性", "雪球", "二元", "区间累积", "鲨鱼鳍",
    "亚式期权", "最差表现", "最佳表现", "总收益互换", "信用违约互换",
    "参与票据", "目标赎回", "反向可转换", "双赢"]

# Factor ⑥: Illiquid or hard to value underlying
FACTOR_6_KEYWORDS_EN = [
    "snowball", "barrier", "knock-out", "knock-in",
    "asian option", "lookback", "participation note", "p-note",
    "reverse convertible", "private credit", "private equity",
    "infrastructure debt", "distressed", "illiquid",
    # Virtual asset / crypto spot ETFs — SFC explicitly classifies
    # these as complex products per §5.5 factor ⑥ (hard-to-value underlying)
    "bitcoin", "ether", "virtual asset", "crypto", "solana",
]
FACTOR_6_KEYWORDS_CN = ["雪球", "敲出", "敲入", "鲨鱼鳍",
    "亚式期权", "参与票据", "反向可转换", "私募债", "私募股权",
    "基建债", "不良资产", "非流通",
    "比特币", "以太币", "虚拟资产", "加密货币"]

# ── Legacy Constants (kept for schema migration reference, not used in new engine) ──

# OLD: Single-dimension keywords (flattened derivative + complex together)
COMPLEX_NAME_KEYWORDS_EN = DERIVATIVE_NAME_KEYWORDS_EN + COMPLEX_PRODUCT_NAME_KEYWORDS_EN
COMPLEX_NAME_KEYWORDS_CN = DERIVATIVE_NAME_KEYWORDS_CN + COMPLEX_PRODUCT_NAME_KEYWORDS_CN

# OLD: Single-dimension fund types (flattened)
COMPLEX_FUND_TYPES = DERIVATIVE_FUND_TYPES + [
    "structured_fund",
    "structured_product",
    "accumulator",
    "decumulator",
    "equity_linked_note",
]

# Conventional fund types (not complex, not derivative)
ORDINARY_FUND_TYPES = [
    "unit_trust",
    "mutual_fund",
    "etf",
    "physical_etf",
    "index_fund",
    "bond_fund",
    "money_market_fund",
    "feeder_fund",
    "fund_of_funds",
    "reit",
]

# ═══════════════════════════════════════════════════════════════
#  Rate Limits
# ═══════════════════════════════════════════════════════════════

SFC_REQUEST_DELAY = 1.0       # seconds between SFC requests
SFC_MAX_RETRIES = 3
SFC_TIMEOUT = 30

# ═══════════════════════════════════════════════════════════════
#  Known Classified Products (Manually Maintained List)
# ═══════════════════════════════════════════════════════════════

# Map SFC authorization number or ISIN → complex_product_type.
# Entries here bypass the heuristic engine (source = "sfc_list").
# Values must be one of COMPLEX_PRODUCT_TYPES.
# Populated from SFC circulars and official complex/derivative product lists.

KNOWN_CLASSIFIED_FUNDS: dict[str, str] = {
    # "sfc_auth_no_or_isin": "complex_product_type"
    # e.g. "ABC123": "derivative_fund"
}

# Alias for backward compatibility
KNOWN_COMPLEX_FUNDS = KNOWN_CLASSIFIED_FUNDS
