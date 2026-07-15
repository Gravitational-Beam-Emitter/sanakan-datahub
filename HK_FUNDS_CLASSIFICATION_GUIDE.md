# HK Fund Classification — 衍生品与复杂产品判定逻辑

## 一个基金有两个独立的 SFC 监管标签

| 标签 | 法规 | 含义 | 触发条件 |
|------|------|------|----------|
| `is_derivative_product` | §5.1A | 衍生品 | NDE > 50% NAV，或其价值来源于底层资产 |
| `is_complex_product` | §5.5 | 复杂产品 | 六因子测试通过，散户买入前需做 suitability assessment |

两者独立判断。一个基金可以：
- 同时是衍生品+复杂产品（最常见）
- 仅是复杂产品而非衍生品（如 complex bond）
- 两者皆否（普通基金）

`complex_product_type` 记录具体是哪种复杂产品（9 类：derivative_fund, synthetic_etf, futures_etf, L&I, hedge_fund, structured, complex_bond, security_token, non_complex）。

---

## 判定分为四层，从权威到启发式

### Layer 0 — 权威数据源（直接判定，不走启发式）

三个自动抓取的 SFC/HKEX 公开登记册 + 一个人工维护列表：

| 数据源 | 脚本 | 覆盖产品 | complex_product_type | 判定依据 |
|--------|------|----------|---------------------|----------|
| SFC UTMF 列表 | `pipeline_funds.py` → `fetch_sfc_fund_list()` | SFC 授权的 unit trust / OFC | `derivative_fund` | SFC 官方「Derivative funds: Yes」列 |
| SFC SIP 登记册 | `sip_pipeline.py` → `fetch_sip_list()` | 非上市结构性产品（ELI, ELD） | `structured` | SFC 明确列为 §5.5 复杂产品 |
| HKEX 证券列表 | `hkex_pipeline.py` → `fetch_hkex_daily()` | L&I / 合成 ETF / 期货 ETF / REIT / covered call ETF | `L&I`, `synthetic_etf`, `futures_etf` 等 | 产品结构本身决定 |
| `KNOWN_CLASSIFIED_FUNDS` | `config.py` | 人工维护 | 手动指定 | 对已知的特殊产品做精确标记 |

这些数据源会被 `build_known_classified_from_sfc()` 加载到内存 dict，以 `sfc_authorization_no` 或 `isin` 为 key。命中即直接判定，**不经过任何启发式**。

当前权威数据源覆盖：
- 81 只 UTMF 衍生品基金
- 341 只 SIP 结构性产品
- 422 只 HKEX 产品（L&I 40 + ETF 371 + REIT 11）
- 合计 844 只（占总量 30%）

### Layer 0.5 — NDE 数据（LLM 提取 KFS）

`nde_extractor.py` 下载 SFC 的 KFS（产品关键事实陈述）PDF，用 LLM 提取：
- `derivative_exposure_pct`：净衍生品敞口占 NAV 百分比
- `uses_derivatives_for_non_hedging`：是否用衍生品做投资目的（非对冲）

如果 NDE > 50% → 直接标记为衍生品。

注意：Layer 0.5 不会覆盖 Layer 0 的权威分类。

### Step 1 — §5.1A 衍生品判定（启发式）

当基金不在权威数据源中时，通过以下方式判断：

**1a. 基金类型检查**

以下基金类型**本身就是衍生品**（`DERIVATIVE_FUND_TYPES`）：
`synthetic_etf`, `futures_etf`, `leveraged_inverse_product`, `leveraged_product`, `inverse_product`, `hedge_fund`, `derivative_fund`

**1b. 基金名称关键词匹配**

中英文各约 50 个关键词，分三类：
- 结构类：`synthetic`, `swap-based`, `leveraged`, `inverse`, `合成`, `杠杆`, `反向`
- 策略类：`absolute return`, `long/short`, `managed futures`, `arbitrage`, `market neutral`, `绝对回报`, `管理期货`, `套利`, `市场中性`
- 对冲基金类：`global macro`, `event driven`, `cta`, `distressed`, `事件驱动`, `另类投资`

注意：以下词已被排除（假阳性率太高）：`volatility`（低波动率基金）、`alpha`（Alpha 收益基金）、`total return`（总回报债基）、`systematic`（系统化股票基金）、`short`（短久期债基）。

### Step 2 — §5.5 复杂产品六因子测试（启发式）

SFC 的六因子测试（Guidelines on Online Distribution and Advisory Platforms, Chapter 6）：

| 因子 | 判断方式 |
|------|----------|
| ① 是否为衍生品 | Step 1 的结论 |
| ② 无二级市场 | 结构性产品默认无二级市场 |
| ③ 缺乏透明定价信息 | SFC 授权基金默认有 KFS → 通过 |
| ④ 损失可能超过本金 | 杠杆/反向产品、accumulator、CFD 等关键词 |
| ⑤ 复杂回报结构 | 结构性产品、autocallable、barrier、雪球、凤凰等关键词 |
| ⑥ 底层资产难以估值 | snowball、private credit、distressed、不良资产等关键词 |

**任意一个因子触发 → 复杂产品**。最常见的情况：
- 因子①触发（是衍生品） → 自动复杂
- 因子⑤触发（结构性产品关键词匹配）→ 复杂

### Step 3 — 确定 complex_product_type

```python
if 权威数据源命中:
    complex_product_type = 数据源指定的类型  # 不覆盖
elif 启发式判定为复杂:
    complex_product_type = 优先级匹配:
        1. structured    （有结构性产品关键词）
        2. synthetic_etf / futures_etf / L&I  （类型匹配）
        3. hedge_fund    （类型匹配）
        4. derivative_fund  （是衍生品）
        5. complex_bond     （含 bond/债券 关键词且有复杂特征）
        6. complex_bond     （兜底）
```

---

## 分类结果在当前数据库的分布

```
2786 只活跃基金
├── 2324 普通基金（非衍生、非复杂）
├── 461 衍生品（16.5%）
│   ├── 81  SFC UTMF 标记
│   ├── 341 SIP 结构性产品
│   └── 39  HKEX / 关键词
└── 462 复杂产品（16.6%）
    ├── 342 structured（结构性产品）
    ├── 76  derivative_fund
    ├── 42  L&I
    ├── 1   synthetic_etf
    └── 1   complex_bond
```

---

## 实际查询方式

在 `hk_funds.duckdb` 中：

```sql
-- 查某只基金的完整分类
SELECT fund_name_en, is_derivative_product, is_complex_product, 
       complex_product_type, classification_source, classification_reason
FROM hk_funds
WHERE sfc_authorization_no = 'BQK122';

-- 查所有 SFC 标记的衍生品基金
SELECT fund_name_en FROM hk_funds 
WHERE classification_source = 'sfc_utmf' AND is_derivative_product = true;

-- 查所有结构性产品
SELECT fund_name_en, fund_manager_name_en FROM hk_funds
WHERE complex_product_type = 'structured';

-- 看分类来源分布
SELECT classification_source, count(*) FROM hk_funds 
WHERE is_active = true GROUP BY 1 ORDER BY 2 DESC;
```

或通过 MCP API：
- `hk_derivative_products` — 查询衍生品
- `hk_complex_products` — 查询复杂产品（可按 type 过滤）
