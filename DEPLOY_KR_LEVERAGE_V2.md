# 部署指南：韩国散户杠杆 v2（echarts 全交互版）

最新 commit: `93bdca5`

---

## 0. 拉取最新代码

代码仓库地址：`https://github.com/Gravitational-Beam-Emitter/cibo-eco-data.git`

分支：`main`

如果服务器上还没有代码，先 clone：
```bash
git clone https://github.com/Gravitational-Beam-Emitter/cibo-eco-data.git "/Users/a80460/Desktop/Sanakan datahub"
```

如果已经 clone 过，拉取最新：
```bash
cd "/Users/a80460/Desktop/Sanakan datahub" && git pull origin main
```

---

## 1. 安装 Python 依赖

```bash
python3 -m pip install fastapi uvicorn duckdb pandas requests finance-datareader
```

- `fastapi uvicorn` — API 服务
- `duckdb` — 数据库
- `pandas` — 数据处理
- `requests` — HTTP 请求（访问 KOFIA Freesis API）
- `finance-datareader` — 指数数据（KOSPI/KOSDAQ/S&P 500，不依赖 yfinance）

## 2. 初始化数据

### 数据源

| 数据 | 来源 | 方式 |
|------|------|------|
| 信用余额（fin, finKospi, finKosdaq） | KOFIA Freesis API | 直接 HTTP POST 请求 |
| 市场资金（dep, derivDep, rp, misu） | KOFIA Freesis API | 直接 HTTP POST 请求 |
| KOSPI/KOSDAQ 指数 + 市值 | FinanceDataReader → KRX | Python 库 |
| S&P 500 指数 | FinanceDataReader | Python 库 |
| 韩国名义GDP（mg 指标） | World Bank API | 免费公开 API，无需认证 |
| 券商自有资本合计（util 指标） | FSS 季度报告 | 硬编码年度数据点 + 日度插值 |

不再依赖 kimpremium.com 的聚合 JSON 文件，所有数据从原始数据源直接获取。

存入 `/Users/a80460/Desktop/Sanakan datahub/hynix/hynix.duckdb`：
- `kr_leverage_daily` — 22列日线数据，1998-01-30 至今约7159行
- `kr_leverage_etf_daily` — ETF资金流（暂空，需要额外数据源）
- `kr_leverage_meta` — 单行KPI快照

```bash
cd "/Users/a80460/Desktop/Sanakan datahub" && python3 -m hynix.kimpremium --init
```

验证数据：
```bash
python3 -c "
import duckdb
conn = duckdb.connect('/Users/a80460/Desktop/Sanakan datahub/hynix/hynix.duckdb', read_only=True)
print('kr_leverage_daily:', conn.execute('SELECT COUNT(*) FROM kr_leverage_daily').fetchone()[0], 'rows')
print('kr_leverage_etf_daily:', conn.execute('SELECT COUNT(*) FROM kr_leverage_etf_daily').fetchone()[0], 'rows')
print('Latest date:', conn.execute('SELECT MAX(date) FROM kr_leverage_daily').fetchone()[0])
conn.close()
"
```

---

## 3. 启动 API（端口 8008）

```bash
cd "/Users/a80460/Desktop/Sanakan datahub" && python3 -m uvicorn hynix.api:app --host 0.0.0.0 --port 8008
```

### 新增的 6 个 API 端点

| 端点 | 完整 URL 示例 |
|------|------|
| `GET /api/v1/kr-leverage/summary` | `http://localhost:8008/api/v1/kr-leverage/summary` |
| `GET /api/v1/kr-leverage/snapshot` | `http://localhost:8008/api/v1/kr-leverage/snapshot?date=2026-07-18` |
| `GET /api/v1/kr-leverage/series` | `http://localhost:8008/api/v1/kr-leverage/series?indicator=r2&start=2026-01-01&end=2026-07-21&limit=500` |
| `GET /api/v1/kr-leverage/etf` | `http://localhost:8008/api/v1/kr-leverage/etf?indicator=thermo&start=2026-01-01&end=2026-07-21&limit=500` |
| `GET /api/v1/kr-leverage/dump` | `http://localhost:8008/api/v1/kr-leverage/dump?start=2024-01-01&end=2026-07-21` |
| `POST /api/v1/kr-leverage/fetch` | `curl -X POST http://localhost:8008/api/v1/kr-leverage/fetch` |

**日度指标（/series）可用值**：`r2`, `p10`, `kospi`, `spx`, `fin`, `dep`, `liq`, `mg`, `util`, `creb`, `marcap`, `short`, `deltapct`, `exhaust`, `bkprc`, `exhsig`, `trend`, `sentiment`, `caputil`, `mgutil`, `fdr`, `off`

**ETF指标（/etf）可用值**：`thermo`, `thermoW`, `flow`, `flowW`, `cumFlow`, `cumFlowW`

curl 验证：
```bash
curl -s http://localhost:8008/api/v1/health | python3 -m json.tool
curl -s http://localhost:8008/api/v1/kr-leverage/summary | python3 -m json.tool
curl -s "http://localhost:8008/api/v1/kr-leverage/series?indicator=r2&limit=30" | python3 -m json.tool
```

---

## 4. MCP 工具（3 个新工具）

MCP 服务器代码在 `/Users/a80460/Desktop/Sanakan datahub/mcp/eco_data_server.py`。

Claude Code 的 `.mcp.json` 配置：

```json
{
  "mcpServers": {
    "eco-data": {
      "command": "python3",
      "args": ["mcp/eco_data_server.py"],
      "cwd": "/Users/a80460/Desktop/Sanakan datahub"
    }
  }
}
```

新增工具：

| 工具名 | 参数 | 说明 |
|------|------|------|
| `kr_leverage_summary` | 无 | 最新KPI摘要（信用余额、爆仓、R²） |
| `kr_leverage_series` | `indicator`（必填）, `start`, `end`, `limit` | 单指标时间序列 |
| `kr_leverage_etf` | `indicator`（必填）, `start`, `end`, `limit` | ETF资金流时间序列 |

---

## 5. 前端部署

### 5.1 安装 echarts 依赖

```bash
cd "/Users/a80460/Desktop/Sanakan datahub/frontend" && npm install
```

### 5.2 构建和启动

```bash
# 开发模式（端口 3000）
cd "/Users/a80460/Desktop/Sanakan datahub/frontend" && npm run dev

# 生产模式
cd "/Users/a80460/Desktop/Sanakan datahub/frontend" && npm run build && npm start
```

浏览器打开 `http://localhost:3000/hynix` → 点击 **「韩国散户杠杆」** tab。

### 5.3 变更的文件

新增：
- `/Users/a80460/Desktop/Sanakan datahub/frontend/src/app/hynix/KrLeverageContent.tsx` — 5 个 echarts 图表
- `/Users/a80460/Desktop/Sanakan datahub/hynix/kimpremium.py` — 数据管道

修改：
- `/Users/a80460/Desktop/Sanakan datahub/frontend/src/app/hynix/HynixContent.tsx` — 新增 tab
- `/Users/a80460/Desktop/Sanakan datahub/frontend/src/lib/api.ts` — 新增 API 函数
- `/Users/a80460/Desktop/Sanakan datahub/frontend/package.json` — 新增 echarts
- `/Users/a80460/Desktop/Sanakan datahub/hynix/storage.py` — 新增 3 个表 + 查询函数
- `/Users/a80460/Desktop/Sanakan datahub/hynix/api.py` — 新增 6 个端点
- `/Users/a80460/Desktop/Sanakan datahub/mcp/eco_data_server.py` — 新增 3 个 MCP 工具

### 5.4 5 个图表

1. **信用温度计** — 信用余额(柱状图) + 余额增速(折线)，时间窗口：全部/20年/10年/5年/3年/1年
2. **爆仓标注** — 信用余额叠加 KOSPI 暴跌日（跌>2.5%/4%/7%），含爆仓信号 markArea
3. **R² 全景图** — 6 指标叠加（信用余额/KOSPI/标普500/融资余额/存款余额/流动性），双 Y 轴
4. **微观分解** — 5 个子 tab：融资构成/资金分母/融资/市值强度/额度利用率/估值
5. **存款搬家** — 信用余额 vs 活期存款 vs 定期存款 vs 客户存款总额
6. **清算明细表** — 可折叠表格（近一年逐日 + 爆仓日全记录）

### 5.5 TypeScript 调用

```typescript
// 批量获取所有指标（前端用 /dump）
const res = await fetch('http://localhost:8008/api/v1/kr-leverage/dump?start=2024-01-01&end=2026-07-21');
const data = await res.json();
// data.dates: string[]
// data.series: { r2: number[], kospi: number[], fin: number[], dep: number[], ... }
// data.etf_series: { thermo: number[], flow: number[], cumFlow: number[], ... }

// 单个指标
const res2 = await fetch('http://localhost:8008/api/v1/kr-leverage/series?indicator=r2&start=2026-01-01&limit=200');
const { indicator, count, data: points } = await res2.json();
```

---

## 6. 定时任务（每日增量更新）

```bash
mkdir -p "/Users/a80460/Desktop/Sanakan datahub/logs"
```

```bash
crontab -e
```

```
0 15 * * 1-5 cd "/Users/a80460/Desktop/Sanakan datahub" && /usr/bin/python3 -m hynix.kimpremium >> "/Users/a80460/Desktop/Sanakan datahub/logs/kimpremium.log" 2>&1
```

手动执行验证：
```bash
cd "/Users/a80460/Desktop/Sanakan datahub" && python3 -m hynix.kimpremium
```

---

## 7. 数据说明

所有数据直接从原始数据源获取：

| 数据 | 来源 | API/方法 |
|------|------|------|
| 信用余额（신용거래융자/대주） | KOFIA Freesis | `POST /meta/getMetaDataList.do` (OBJ_NM=STATSCU0100000070BO) |
| 市场资金（예탁금/RP/미수금） | KOFIA Freesis | `POST /meta/getMetaDataList.do` (OBJ_NM=STATSCU0100000060BO) |
| 最新快照 | KOFIA Freesis | `POST /stockSubMain/STATSCUSUBMAIN01BO.do` |
| KOSPI 指数 + 市值 | KRX → FinanceDataReader | `fdr.DataReader('KS11', ...)` |
| KOSDAQ 指数 + 市值 | KRX → FinanceDataReader | `fdr.DataReader('KQ11', ...)` |
| S&P 500 指数 | FinanceDataReader | `fdr.DataReader('US500', ...)` |
| 韩国名义GDP（mg指标） | World Bank API | `GET /v2/country/KR/indicator/NY.GDP.MKTP.CN` (免费，无需认证) |
| 券商自有资本合计（util指标） | FSS 季度报告 | 硬编码数据点 + 日度前向填充 (2025年末: 106.9T) |

**不依赖 yfinance** — FinanceDataReader 直接访问 KRX 和 Yahoo 历史数据镜像，不会被 Yahoo 封 IP。

### 新增衍生指标（v2.1）

以下指标由 `build_combined_df()` 自动计算，无需额外数据源：

| 指标 | 计算方式 |
|------|------|
| `mg` | KOSPI 总市值 / 韩国名义GDP × 100（Buffett Indicator），GDP 由年频插值为日频 |
| `util` | (融资 + 大主 + 质押贷款) / 券商自有资本 × 100 |
| `p10` | r2 在近 2500 个交易日（~10年）中的百分位排名 |
| `r1p` | KOSPI 融资 / KOSPI 市值 × 100 |
| `r1q` | KOSDAQ 融资 / KOSDAQ 市值 × 100 |
| `col` | 预托证券担保融资（来自 KOFIA TMPV9） |
| `loan` | 信用交易大主合计 = loanKospi + loanKosdaq |

### ETF 数据

ETF 数据（thermo, flow, cumFlow 等）目前有两个来源：

1. **历史数据（2024-07 至 2026-07-16）**：来自原 kimpremium.com 的 620 条日频记录，基于真实杠杆 ETF 申赎数据
2. **代理指标（v2.1 新增）**：`_compute_etf_df()` 函数从日频信用/KOSPI 数据计算代理 ETF 温度指标（滚动 R² 相关系数），用于填补 2026-07-16 之后的数据

### 仍缺失的数据

| 指标 | 说明 | 原因 |
|------|------|------|
| 真实 ETF 申赎数据 | KOFIA 不提供 ETF 资金流 API（STATFND 系列返回空） | 需要 KRX ETF 市场数据或商业数据源 |
| 强平金额（真实） | KOFIA 提供 `forceLiqAmt`/`forceLiqPct`（반대매매）但未写入 DB | 列名与现有 schema 不兼容，需前端同步修改 |
| 券商个体数据 | 每间券商的自有资本、信用额度使用情况 | 需要 FSS 或 KOFIA 券商级别的数据 |

数据库文件：`/Users/a80460/Desktop/Sanakan datahub/hynix/hynix.duckdb`

### 核心指标含义

| indicator | 中文名称 | 说明 |
|------|------|------|
| `creb` | 信用贷款余额 | 韩国散户借钱买股票的总余额 |
| `fin` | 融资余额 | 信用融资余额（신용거래융자） |
| `dep` | 存款余额 | 客户存款总额（투자자예탁금） |
| `liq` | 资金流动性 | 信用余额 / 存款余额 |
| `r2` | 信用率 | 信用余额 / KOSPI 市值 |
| `r1` | 信用/存管金 | 信用余额 / (存管金 + 衍生品保证金) |
| `r1p` | KOSPI 信用率 | KOSPI 融资 / KOSPI 市值 |
| `r1q` | KOSDAQ 信用率 | KOSDAQ 融资 / KOSDAQ 市值 |
| `p10` | 10年分位 | r2 在近 10 年（~2500 交易日）中的百分位 |
| `mg` | KOSPI市值/GDP | 巴菲特指标（Buffett Indicator），GDP 来自 World Bank API |
| `util` | 额度利用率 | 总信用供与 / 券商自有资本合计（法定上限 100%） |
| `mcap` | KOSPI 总市值 | 来自 FinanceDataReader (KRX) |
| `col` | 质押贷款 | 预托证券担保融资（예탁증권담보융자） |
| `loan` | 融券余额 | 信用交易大主（대주）合计 |
| `misu` | 委托未收金 | 委托买卖未收金（위탁매매미수금） |
| `deltapct` | 信用变化率 | 日度信用余额变化百分比 |
| `exhaust` | 信用枯竭度 | 信用使用率指标 |
| `bkprc` | 爆仓量 | 强制平仓量 |
| `exhsig` | 爆仓信号 | 爆仓预警信号 |
| `sentiment` | 散户情绪 | 综合情绪指标 |
| `trend` | 信用趋势 | 信用趋势方向 |
| `short` | 融券余额 | 做空余额 |
| `thermo` | ETF资金温度 | 杠杆ETF资金流入热度（来自 kimpremium.com 历史数据） |
| `flow` | ETF资金流量 | 杠杆ETF日度资金流 |
| `cumFlow` | ETF累计流量 | 杠杆ETF累计资金流 |
