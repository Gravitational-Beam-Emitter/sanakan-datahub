# SK Hynix 跨市场套利追踪 — 部署与接入指南

> 给另一个项目的开发者：本文档说明如何部署海力士数据服务、以及如何用这些数据构建前端页面。

## 概述

SK Hynix（000660.KS）同时在多个市场交易。本模块追踪 **4 个标的**，折算为等效 1 股韩国普通股后进行折溢价对比：

| 标的 | 市场 | 类型 | 杠杆 | 折算规则 |
|---|---|---|---|---|
| 000660.KS | KR | 正股（基准） | 1x | 1 股 = 1 股 |
| SKHY | US (Nasdaq) | ADR | 1x | 10 ADR = 1 股，需 × USDKRW × 10 |
| 7709.HK | HK | 2x 杠杆 ETP | 2x | 掉期合成，等效 = 价格 ÷ (2 × 跟踪比率) |
| 0193T0.KS | KR | 2x 杠杆 ETF | 2x | 期货复制，等效 = 价格 ÷ (2 × 跟踪比率) |

核心指标：`equivalent_krw_per_share`（买 1 股 SK Hynix 等效权益需要多少 KRW）和 `premium_pct_vs_base`（相对韩国正股的溢价百分比）。

---

## 1. 部署

### 1.1 文件清单

```
hynix/
├── __init__.py
├── config.py          # 标的信息 + FX 配置
├── storage.py         # DuckDB 读写（5 张表）
├── pipeline.py        # yfinance 拉取 + 套利计算
├── api.py             # FastAPI REST API（端口 8008）
└── hynix.duckdb       # 数据库文件（运行后生成）

eco_data_sdk/
└── hynix_client.py    # Python SDK 客户端（可选）

mcp/
└── eco_data_server.py # MCP Server（含 3 个 hynix 工具）

frontend/src/app/hynix/
├── page.tsx           # SSR 页面入口
├── HynixContent.tsx   # 客户端交互组件（4 个 tab）
├── HynixArbTable.tsx  # 折溢价对比表
└── HynixPremiumChart.tsx  # 溢价时序 SVG 图
```

### 1.2 依赖

```bash
pip install duckdb pandas fastapi uvicorn yfinance
```

无需 API Key，全部数据通过 yfinance 免费获取。

### 1.3 初始化数据

```bash
cd /path/to/project

# 回填 60 天历史数据（跳过周末，约 2-3 分钟）
python -m hynix.pipeline --init

# 或指定回填天数
python -m hynix.pipeline --init --lookback 90
```

### 1.4 启动 API

```bash
python -m uvicorn hynix.api:app --host 127.0.0.1 --port 8008
```

验证：
```bash
curl http://127.0.0.1:8008/api/v1/health
# {"status":"ok","instruments":4,"dates":45,"arbitrage":148,"prices":180}
```

### 1.5 Systemd 保活（生产环境）

```bash
cat > /etc/systemd/system/hynix-api.service << 'EOF'
[Unit]
Description=SK Hynix Arbitrage API
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/eco-data
ExecStart=/usr/bin/python3 -m uvicorn hynix.api:app --host 127.0.0.1 --port 8008
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now hynix-api
```

### 1.6 每日定时拉取

韩国市场约 15:30 KST 收盘，美股约 05:30 KST（次日）收盘。建议在香港时间 **09:00** 拉取（美股已收盘、汇率可用）：

```bash
# crontab
0 9 * * 1-5 cd /opt/eco-data && python -m hynix.pipeline
```

或 systemd timer：
```bash
cat > /etc/systemd/system/hynix-fetch.service << 'EOF'
[Unit]
Description=Hynix Daily Fetch
[Service]
Type=oneshot
WorkingDirectory=/opt/eco-data
ExecStart=/usr/bin/python3 -m hynix.pipeline
EOF

cat > /etc/systemd/system/hynix-fetch.timer << 'EOF'
[Unit]
Description=Hynix Daily Fetch Timer
[Timer]
OnCalendar=*-*-* 09:00:00
[Install]
WantedBy=timers.target
EOF

systemctl enable --now hynix-fetch.timer
```

---

## 2. API 参考

Base URL: `http://YOUR_SERVER:8008`

### 2.1 端点一览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/health` | 健康检查（含 instruments/dates/arbitrage 计数） |
| GET | `/api/v1/instruments` | 跟踪标的列表及属性（杠杆、跟踪比率等） |
| GET | `/api/v1/arbitrage/latest` | **← 首页用**：最新折溢价快照（全部标的） |
| GET | `/api/v1/arbitrage/{date}` | 指定日期的折溢价（date 格式 `YYYY-MM-DD`） |
| GET | `/api/v1/arbitrage/{ticker}/history` | 某标的折溢价时间序列（支持 `?start=&end=&limit=`） |
| GET | `/api/v1/prices/{ticker}` | 某标的价格历史（OHLCV） |
| GET | `/api/v1/prices?date={date}` | 某日所有标的价格 |
| GET | `/api/v1/fx/latest` | 最新汇率（USDKRW, HKDKRW） |
| GET | `/api/v1/fx/history` | 汇率历史 |
| GET | `/api/v1/dates` | 有数据的交易日列表 |
| POST | `/api/v1/fetch` | 触发当日数据拉取 |
| POST | `/api/v1/init` | 完整初始化 + 回填 |

### 2.2 核心响应格式

**GET `/api/v1/arbitrage/latest`** — 你前端页面的主数据源：

```json
{
  "date": "2026-07-15",
  "base_ticker": "000660.KS",
  "base_price_krw": 2082000,
  "fx_rates": {
    "USDKRW": 1490.0,
    "HKDKRW": 189.6
  },
  "count": 4,
  "instruments": [
    {
      "ticker": "000660.KS",
      "name": "SK hynix (KR)",
      "market": "KR",
      "currency": "KRW",
      "instrument_type": "stock",
      "leverage": 1.0,
      "price_local": 2082000.0,
      "price_krw": 2082000.0,
      "nav_local": null,
      "nav_krw": null,
      "tracking_ratio": 1.0,
      "equivalent_krw_per_share": 2082000.0,
      "premium_pct_vs_base": 0.0,
      "nav_premium_pct": null
    },
    {
      "ticker": "SKHY",
      "name": "SK hynix ADR (US)",
      "market": "US",
      "currency": "USD",
      "instrument_type": "adr",
      "leverage": 1.0,
      "price_local": 185.27,
      "price_krw": 276047.0,
      "nav_local": null,
      "nav_krw": null,
      "tracking_ratio": 0.1,
      "equivalent_krw_per_share": 2760467.0,
      "premium_pct_vs_base": 32.59,
      "nav_premium_pct": null
    },
    {
      "ticker": "7709.HK",
      "name": "CSOP SK Hynix 2x LEP (HK)",
      "market": "HK",
      "currency": "HKD",
      "instrument_type": "etp",
      "leverage": 2.0,
      "price_local": 85.50,
      "price_krw": 16210.0,
      "tracking_ratio": 0.00389,
      "equivalent_krw_per_share": 2083548.0,
      "premium_pct_vs_base": 0.07,
      "nav_premium_pct": null
    }
  ]
}
```

**关键字段含义：**

| 字段 | 含义 | 前端展示建议 |
|---|---|---|
| `price_local` | 本地货币计价 | "185.27 USD" |
| `price_krw` | 折算为 KRW | 辅助参考 |
| `equivalent_krw_per_share` | **等效购买 1 股 SK Hynix 需要的 KRW 成本** | 核心对比列 |
| `premium_pct_vs_base` | 相对韩国正股的溢价率 | **红涨绿跌**，排序用 |
| `nav_premium_pct` | ETF 市价相对 NAV 的溢价 | 仅 ETF/ETP 有值 |
| `tracking_ratio` | 1 单位该标 = 多少股 SK Hynix | 调试用，可不展示 |

**GET `/api/v1/arbitrage/{ticker}/history?limit=60`** — 画溢价时序图：

```json
[
  {
    "date": "2026-07-10",
    "price_local": 172.50,
    "price_krw": 255300.0,
    "base_price_krw": 1960000.0,
    "equivalent_krw_per_share": 2553000.0,
    "premium_pct": 30.26,
    "nav_premium_pct": null
  }
]
```

### 2.3 典型前端数据流

```
用户打开页面
  │
  ├─► GET /api/v1/arbitrage/latest     → 折溢价对比表
  ├─► GET /api/v1/instruments          → 标的信息卡片
  ├─► GET /api/v1/fx/latest            → 汇率展示条
  ├─► GET /api/v1/dates                → 日期选择器下拉框
  └─► GET /api/v1/arbitrage/SKHY/history?limit=60   → ADR 溢价时序图
       GET /api/v1/arbitrage/7709.HK/history?limit=60 → HK ETP 溢价时序图
```

页面加载时 5 个请求并行发出（`Promise.all`），其中 `arbitrage/latest` 是主数据源，其余是辅助。

---

## 3. 在前端页面中使用

### 3.1 直接 fetch（任何框架通用）

```typescript
const API = "http://YOUR_SERVER:8008";

// 主数据
const res = await fetch(`${API}/api/v1/arbitrage/latest`);
const snapshot = await res.json();

// 时间序列（给图表用）
const [adrRes, hkRes, datesRes] = await Promise.all([
  fetch(`${API}/api/v1/arbitrage/SKHY/history?limit=60`),
  fetch(`${API}/api/v1/arbitrage/7709.HK/history?limit=60`),
  fetch(`${API}/api/v1/dates`),
]);
const adrHistory = await adrRes.json();
const hkHistory = await hkRes.json();
const availableDates = await datesRes.json();
```

### 3.2 Next.js Server Component 示例

```typescript
// app/hynix/page.tsx
export default async function HynixPage() {
  const API = process.env.HYNIX_API_URL || "http://127.0.0.1:8008";

  const res = await fetch(`${API}/api/v1/arbitrage/latest`, {
    next: { revalidate: 300 }, // ISR: 5 分钟刷新
  });
  const snapshot = await res.json();

  return (
    <div>
      <h1>SK Hynix 跨市场套利 — {snapshot.date}</h1>
      <ArbitrageTable instruments={snapshot.instruments} />
    </div>
  );
}
```

### 3.3 折溢价表格渲染

```tsx
function ArbitrageTable({ instruments }) {
  return (
    <table>
      <thead>
        <tr>
          <th>标的</th><th>市场</th><th>本地价格</th>
          <th>等效 1 股 KRW</th><th>溢价</th>
        </tr>
      </thead>
      <tbody>
        {instruments.map(inst => (
          <tr key={inst.ticker}>
            <td>{inst.name}</td>
            <td>{inst.market}</td>
            <td>{inst.price_local.toLocaleString()} {inst.currency}</td>
            <td>{inst.equivalent_krw_per_share.toLocaleString()} KRW</td>
            <td className={inst.premium_pct_vs_base > 0 ? "text-red" : "text-green"}>
              {inst.premium_pct_vs_base > 0 ? "+" : ""}
              {inst.premium_pct_vs_base.toFixed(2)}%
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
```

### 3.4 溢价时序图渲染

用 `arbitrage/{ticker}/history` 的数据，X 轴 = `date`，Y 轴 = `premium_pct`。

```
关键视觉元素：
- Y=0% 处画一条虚线基准线
- ADR 溢价用实线，7709.HK 溢价用虚线（区分两者）
- 颜色：正值红/橙，负值绿
```

### 3.5 日期选择器

```tsx
// GET /api/v1/dates 返回 ["2026-07-15", "2026-07-14", ...]
// 点击某日期时：
const res = await fetch(`${API}/api/v1/arbitrage/${selectedDate}`);
const historicalSnapshot = await res.json();
// 用返回的数据替换当前页面显示
```

---

## 4. 展示建议

以下是本项目前端实际采用的设计，供参考：

### 4.1 页面结构

```
┌─────────────────────────────────────────┐
│  ← 日期导航 →   SK Hynix 跨市场套利追踪  │
│  数据来源: yfinance / KRX / HKEX / Nasdaq │
├─────────────────────────────────────────┤
│  [折溢价对比] [ADR机制分析] [杠杆产品分析] [接入指南] │  ← Tab 切换
├─────────────────────────────────────────┤
│  交易日: [2026-07-15 ▾]                  │  ← 日期下拉框
│  USD/KRW=1490  HKD/KRW=189.6  Base=2.08M │  ← FX 信息条
├─────────────────────────────────────────┤
│  ┌─────────────────────────────────────┐ │
│  │         折溢价对比表                 │ │
│  │ Ticker │ 市场 │ 价格 │ 等效KRW │ 溢价% │ │
│  │ 000660  │ KR  │ 2.08M │ —     │  —   │ │
│  │ SKHY    │ US  │ $185  │ 2.76M  │ +32% │ │
│  │ 7709.HK │ HK  │ HKD85 │ 2.08M  │ +0%  │ │
│  └─────────────────────────────────────┘ │
├─────────────────────────────────────────┤
│  ┌─────────────────────────────────────┐ │
│  │       溢价时间序列图（SVG）          │ │
│  │  50% ┤          ╭──╮                │ │
│  │  25% ┤    ╭─────╯  ╰──              │ │
│  │   0% ┤───── ─ ─ ─ ─ ─ ─ ─ ─ ─      │ │
│  │      07-10  07-12  07-14            │ │
│  │   ── ADR溢价  --- 7709.HK溢价       │ │
│  └─────────────────────────────────────┘ │
├─────────────────────────────────────────┤
│  数据仅供参考，不构成投资建议            │
└─────────────────────────────────────────┘
```

### 4.2 关键 UX 规则

1. **溢价排序**：表格默认按 `premium_pct_vs_base` 降序排列，最高的排最上面
2. **颜色规则**：正溢价（溢价）= 红色/涨色，负溢价（折价）= 绿色/跌色
3. **基准行高亮**：000660.KS（正股）行用浅色背景标记为基准
4. **杠杆标签**：杠杆产品（2x）用醒目标签标注，不要只写数字
5. **ADR 说明**：务必在 ADR 溢价旁附上简短说明——"ADR 溢价为结构性流动性溢价，非定价错误"。否则用户看到 +30% 会以为是 bug
6. **杠杆风险提示**：对 7709.HK / 0193T0.KS 展示风险提示——"每日重置杠杆产品不适合持有一日以上，存在波动率衰减"
7. **数据时效**：标注"不同市场价格存在时区差异（KR 15:30 / HK 16:00 / US 04:00 ET），溢价可能部分反映异步定价"
8. **移动响应**：表头在移动端超过 4 列时启用横向滚动（`overflow-x-auto`）

### 4.3 Tab 内容建议

| Tab | 内容 |
|---|---|
| **折溢价对比**（默认） | 对比表 + 时序图 + 标的详情 |
| **ADR 机制分析** | 关键时间线（F-6 注册→上市→KSD 登记→拆分）+ 单向转换机制说明 + 为什么溢价可以持续（参照 TSMC ADR 10-30% 结构性溢价） |
| **杠杆产品分析** | 产品对比 + 波动率衰减公式 `Drag ≈ (k²σ²)/2` + SK Hynix 实际衰减估算 + 复利效应 + 额外成本（掉期/费率/展期/对手方风险） |
| **接入指南** | API 端点表 + 响应格式 + 前端集成代码 + 部署步骤 |

前三个 tab 面向用户，最后一个 tab 面向开发者。

---

## 5. 数据背景（给开发者理解数据用）

### 5.1 ADR 为什么会有 30%+ 的溢价？

这不是定价错误，是**结构性溢价**：

- **单向转换**：ADR → 韩国股票 可随时赎回（但溢价时无人会赎回）；韩国股票 → ADR **被冻结**（需监管审批 + 托管行 Citi 创设额度）
- **供给稀缺**：初始 ADR 发行量仅占总股本 ~2.5%，Citi 严格控制创设
- **无法做空**：新上市 ADR 缺乏可借券源，套利者无法"卖高买低"
- **TSMC 先例**：台积电 ADR 长期维持 10-30% 溢价，证明这种结构可以永续

### 5.2 杠杆产品的衰减

7709.HK（2x 每日重置）持有风险：

```
年化衰减 ≈ (k² × σ²) / 2
k=2（2倍杠杆）, σ=60%（SK Hynix 典型年化波动率）
衰减 ≈ (4 × 0.36) / 2 = 72% 年化
```

意思是标的横盘一年，2x ETF 可能跌 72%——仅来自数学，不含费用。加上掉期成本（8-20%）和管理费（~2%），实际损耗更大。

### 5.3 时区差异

- 韩国（KOSPI）：09:00-15:30 KST（UTC+9）
- 香港（HKEX）：09:30-16:00 HKT（UTC+8）
- 美国（Nasdaq）：09:30-16:00 ET（UTC-4/-5）

同一日期（calendar date）的快照中，韩国和香港价格来自各自的盘中/收盘，美国价格来自约 12 小时后的收盘。这之间的信息差（如韩国盘后新闻）会导致溢价计算包含异步定价成分。这是跨市场套利数据的固有限制，不是 bug。

---

## 6. 环境变量

如果前端通过 Next.js 服务端 fetch 访问 API（推荐，避免 CORS）：

```bash
# frontend/.env.local
HYNIX_API_URL=http://127.0.0.1:8008
```

如果前端直接通过浏览器 fetch 访问 API（需 API 开启 CORS，已默认开启）：

```bash
NEXT_PUBLIC_HYNIX_API_URL=https://your-server:8008
```

---

## 7. 故障排查

**API 返回 404？**
```bash
curl http://127.0.0.1:8008/api/v1/health
# 确认 uvicorn 在运行
```

**`arbitrage/latest` 返回空？**
```bash
python -m hynix.pipeline --init
# 首次使用需初始化数据库
```

**某些标的 `premium_pct_vs_base` 恒为 0？**
这是首次拉取时的正常行为——跟踪比率（tracking_ratio）在第一次拉取时估算，第二次及以后才会显示真实的溢价变动。

**缺少某日数据？**
yfinance 在周末/节假日无数据。Pipeline 自动跳过周末。如果交易日缺数据，可能是 API 限流——手动跑一次：
```bash
python -m hynix.pipeline --date 2026-07-15
```
