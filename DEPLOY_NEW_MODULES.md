# 新增模块部署指南 — for cibo claude

> 以下所有本地路径基于 `/Users/a80460/Desktop/cibo datahub/`，对应服务器 `/opt/eco-data/`。

## 新增内容概览

这次新增了 2 个完整数据模块：

| 模块 | 端口 | 路由数 | 数据库 | 说明 |
|------|------|--------|--------|------|
| `us_listings/` | 8003 | 48 | `us_listings.duckdb` | 美股 IPO/内幕/财报/持仓/做空/期权等 15 类数据 |
| `hk_funds/` | 8004 | 25 | `hk_funds.duckdb` | HK 基金尽调 KYP + SFC 分类 + 管理人尽调 |

---

## Step 1 — 新增 pip 依赖

在现有 `python3 -m pip install` 基础上，追加以下包：

```bash
python3 -m pip install yfinance
```

`yfinance` 用于 us_listings 模块的以下 pipeline：
- `risk_pipeline.py` — 做空利息、隐含波动率
- `flow_pipeline.py` — ETF 资金流
- `corporate_events_pipeline.py` — 股息、拆分历史
- `options_flow_pipeline.py` — 期权流
- `short_sale_pipeline.py` — 做空活动增强数据
- `crypto_pipeline.py` — yfinance 辅助 finnhub 数据

其他依赖（fastapi, uvicorn, duckdb, pandas, requests, apscheduler, python-dotenv, beautifulsoup4）已在现有 DEPLOY.md 覆盖。

---

## Step 2 — 初始化新数据库

### 2.1 US Listings 数据库

```bash
cd /opt/eco-data

# 初始化当月 IPO 数据（会自动建表 us_listings.duckdb）
python3 -m us_listings.pipeline --init

# 如果只想拉取当前月份（更快）：
python3 -c "
from us_listings.pipeline import fetch_listings_for_month
print(fetch_listings_for_month(2026, 6))
"
```

成功后 `/opt/eco-data/us_listings.duckdb` 出现（约 15 张表）。

### 2.2 HK Funds 数据库

```bash
cd /opt/eco-data

# 拉取 SFC 认可基金清单（会自动建表 hk_funds.duckdb）
python3 -m hk_funds.pipeline_funds --init

# 拉取 SFC 持牌法团 + 链接 + 执法交叉
python3 -m hk_funds.pipeline_managers --init
```

> 注意：SFC 基金清单 API 可能不稳定，如果 `--init` 失败，可通过 CSV 导入：
> ```bash
> python3 -m hk_funds.pipeline_funds --csv /path/to/sfc_fund_list.csv
> ```

成功后 `/opt/eco-data/hk_funds.duckdb` 出现（8 张表）。

---

## Step 3 — 前端环境变量

更新 `/opt/eco-data/frontend/.env.local`，添加两个新 API 地址：

```bash
cat > /opt/eco-data/frontend/.env.local << 'EOF'
NEXT_PUBLIC_API_URL=http://127.0.0.1:8001
NEXT_PUBLIC_ECO_API_URL=http://127.0.0.1:8000
NEXT_PUBLIC_US_CORP_API_URL=http://127.0.0.1:8002
NEXT_PUBLIC_US_LISTINGS_API_URL=http://127.0.0.1:8003
NEXT_PUBLIC_HK_FUNDS_API_URL=http://127.0.0.1:8004
EOF
```

> 新增的是最后两行：`:8003` 和 `:8004`。

---

## Step 4 — 启动新服务

### 4.1 US Listings API（端口 8003）

```bash
cd /opt/eco-data
python3 -m uvicorn us_listings.api:app --host 127.0.0.1 --port 8003
```

验证：
```bash
curl http://127.0.0.1:8003/api/v1/health
# {"status":"ok","listings_count":...,"crypto_count":...,...}
```

### 4.2 HK Funds API（端口 8004）

```bash
cd /opt/eco-data
python3 -m uvicorn hk_funds.api:app --host 127.0.0.1 --port 8004
```

验证：
```bash
curl http://127.0.0.1:8004/api/v1/health
# {"status":"ok","funds_count":...,"managers_count":...,...}
```

---

## Step 5 — Systemd 保活（4 个新服务）

### 5.1 US Listings API

```bash
cat > /etc/systemd/system/us-listings-api.service << 'EOF'
[Unit]
Description=US Listings & Equities API
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/eco-data
ExecStart=/usr/bin/python3 -m uvicorn us_listings.api:app --host 127.0.0.1 --port 8003
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
```

### 5.2 US Listings Scheduler

```bash
cat > /etc/systemd/system/us-listings-scheduler.service << 'EOF'
[Unit]
Description=US Listings & Equities Scheduler
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/eco-data
ExecStart=/usr/bin/python3 -m us_listings.scheduler
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
```

### 5.3 HK Funds API

```bash
cat > /etc/systemd/system/hk-funds-api.service << 'EOF'
[Unit]
Description=HK Fund KYP API
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/eco-data
ExecStart=/usr/bin/python3 -m uvicorn hk_funds.api:app --host 127.0.0.1 --port 8004
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
```

### 5.4 HK Funds Scheduler

```bash
cat > /etc/systemd/system/hk-funds-scheduler.service << 'EOF'
[Unit]
Description=HK Fund KYP Scheduler
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/eco-data
ExecStart=/usr/bin/python3 -m hk_funds.scheduler
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
```

### 5.5 启用所有新服务

```bash
systemctl daemon-reload
systemctl enable --now us-listings-api
systemctl enable --now us-listings-scheduler
systemctl enable --now hk-funds-api
systemctl enable --now hk-funds-scheduler

# 检查全部 8 个服务（4 个旧的 + 4 个新的）
systemctl status eco-data-api cn-stock-api us-corp-api zt-frontend \
                 us-listings-api us-listings-scheduler \
                 hk-funds-api hk-funds-scheduler
```

---

## Step 6 — 调度器说明

### US Listings Scheduler（8 个 cron job，HKT 时区）

| 时间 | 内容 |
|------|------|
| 每日 07:07 | IPO 日历 |
| 每日 07:37 | Crypto 产品刷新 |
| 每日 08:07 | 内幕交易 + 财报日历 |
| 每日 08:27 | 做空/风控数据 + ETF 资金流 |
| 每日 08:47 | 公司事件（股息/拆分） |
| 每日 09:07 | 交易暂停、执法行动、阈值证券、ATS 暗池、做空活动、锁仓到期、期权流 |
| 每周一 09:27 | 机构持仓 13F + ATS-N 备案 |
| 每周一 09:57 | 机构持仓（纳斯达克+纽交所） |

### HK Funds Scheduler（4 个 cron job，HKT 时区）

| 时间 | 内容 |
|------|------|
| 每周一 11:07 | SFC 基金清单刷新 + 分类 |
| 每周一 11:37 | SFC 持牌法团刷新 + 关联 + 执法 |
| 每周一 12:07 | 重新分类 + 重新关联 |
| 每日 09:37 | 管理人执法交叉检查 |

---

## Step 7 — 前端重新构建

新增了 `/hk-funds` 页面和 NavBar 中的 "HK基金" 入口，需要重新构建前端：

```bash
cd /opt/eco-data/frontend
npm run build
systemctl restart zt-frontend
```

---

## Step 8 — US Listings API 接口速查（48 routes on :8003）

### 健康检查
```
GET  /api/v1/health
```

### IPO & Crypto
```
GET  /api/v1/listings?year=2026&month=6
GET  /api/v1/listings/{symbol}
GET  /api/v1/crypto
GET  /api/v1/crypto/stats
POST /api/v1/fetch-listings?year=2026&month=6
POST /api/v1/fetch-crypto
POST /api/v1/scan-sec-crypto
```

### 内幕交易 & 财报
```
GET  /api/v1/insider?date=2026-06-19
GET  /api/v1/insider/{ticker}
GET  /api/v1/earnings?start=2026-06-01&end=2026-06-20
GET  /api/v1/earnings/{ticker}
POST /api/v1/fetch-insider
POST /api/v1/fetch-earnings
```

### 机构持仓 & 做空
```
GET  /api/v1/holdings?ticker=AAPL
GET  /api/v1/holdings/stats
GET  /api/v1/short-interest?ticker=AAPL
GET  /api/v1/fails-to-deliver?ticker=AAPL
POST /api/v1/fetch-holdings
POST /api/v1/fetch-risk
```

### 风控数据（yfinance enhanced）
```
GET  /api/v1/risk/short-signals?ticker=AAPL
GET  /api/v1/risk/options-skew?ticker=AAPL
```

### ETF 资金流
```
GET  /api/v1/etf-flows?ticker=SPY
GET  /api/v1/etf-flows/top
POST /api/v1/fetch-flows
```

### 公司事件（股息/拆分）
```
GET  /api/v1/dividends?ticker=AAPL
GET  /api/v1/splits?ticker=AAPL
POST /api/v1/fetch-corporate-events
```

### 交易暂停 & 执法
```
GET  /api/v1/suspensions?date=2026-06-19
GET  /api/v1/suspensions/{ticker}
GET  /api/v1/enforcement
GET  /api/v1/enforcement/{ticker}
POST /api/v1/fetch-suspensions
POST /api/v1/fetch-enforcement
```

### 阈值证券 & ATS 暗池
```
GET  /api/v1/threshold
GET  /api/v1/threshold/{ticker}
GET  /api/v1/ats
GET  /api/v1/ats/{file_number}
POST /api/v1/fetch-threshold
POST /api/v1/fetch-ats
```

### 做空活动 & 锁仓 & 期权流
```
GET  /api/v1/short-activity?ticker=AAPL
GET  /api/v1/lockup?ticker=AAPL
GET  /api/v1/lockup/upcoming
GET  /api/v1/options-flow?ticker=AAPL
GET  /api/v1/options-flow/unusual
POST /api/v1/fetch-short-activity
POST /api/v1/fetch-lockup
POST /api/v1/fetch-options-flow
```

---

## Step 9 — HK Funds API 接口速查（25 routes on :8004）

### 健康检查 & 统计
```
GET  /api/v1/health
GET  /api/v1/funds/stats
GET  /api/v1/managers/stats
```

### 基金产品
```
GET  /api/v1/funds                          # 基金列表 ?limit=50&offset=0
GET  /api/v1/funds?classification=complex   # 筛选：ordinary/complex/derivatives/structured
GET  /api/v1/funds?type=etf                 # 按基金类型筛选
GET  /api/v1/funds?domicile=HK              # 按注册地筛选
GET  /api/v1/funds/search?q=科技            # 中英文关键词搜索
GET  /api/v1/funds/complex                  # 全部复杂/衍生产品
GET  /api/v1/funds/{id}                     # 基金详情 + 分类信息
GET  /api/v1/funds/{id}/documents           # 基金文件清单
POST /api/v1/funds/{id}/classify            # 手动修改分类
```

### 管理人尽调
```
GET  /api/v1/managers                       # 管理人列表 ?limit=50&offset=0
GET  /api/v1/managers?has_enforcement=true  # 只展示有执法记录的
GET  /api/v1/managers/{id}                  # 管理人详情
GET  /api/v1/managers/{id}/funds            # 该管理人管理的基金
GET  /api/v1/managers/{id}/regulatory       # 该管理人监管处罚历史
```

### HKEX 上市基金
```
GET  /api/v1/hkex-funds                     # HKEX 上市基金列表
GET  /api/v1/hkex-funds/{stock_code}        # 按股票代码查看
```

### 触发刷新
```
POST /api/v1/fetch-funds                    # 触发 SFC 基金清单刷新
POST /api/v1/fetch-managers                 # 触发 SFC 持牌法团刷新
POST /api/v1/classify                       # 触发全量重分类
POST /api/v1/link-managers                  # 触发管理人-基金重关联
POST /api/v1/import/csv                     # CSV 批量导入基金数据
```

---

## cibo claude 如何使用这些新增数据

### 方式 1：直接调 REST API（推荐）

你的项目只需 HTTP 请求即可拿到所有数据：

```python
import requests

# US Listings — 看最近的内幕交易
r = requests.get("http://127.0.0.1:8003/api/v1/insider?date=2026-06-19")
for trade in r.json()["trades"]:
    print(trade["ticker"], trade["transaction_type"], trade["shares"])

# US Listings — 看某只票的风控信号
r = requests.get("http://127.0.0.1:8003/api/v1/risk/short-signals?ticker=GME")
print(r.json())  # risk_level, squeeze_score, borrow_rate...

# HK Funds — 查某只基金的分类
r = requests.get("http://127.0.0.1:8004/api/v1/funds/search?q=leveraged")
for fund in r.json()["funds"]:
    print(fund["fund_name_en"], fund["classification"], fund["classification_reason"])

# HK Funds — 查某管理人有没有被 SFC 罚过
r = requests.get("http://127.0.0.1:8004/api/v1/managers?has_enforcement=true")
print(r.json())  # 只返回有执法记录的管理人
```

### 方式 2：更新 MCP 配置（可选）

如果需要 AI Agent 也能直接调用这些新 API，可以在 `.mcp.json` 中增加工具。不过现阶段 4 个 API 都是 RESTful 的，直接 HTTP 调用更简单。

### 方式 3：直接读 DuckDB（适合批量分析）

```python
import duckdb

# US Listings 数据库
conn = duckdb.connect("/opt/eco-data/us_listings.duckdb")
df = conn.execute("SELECT * FROM insider_trades ORDER BY filing_date DESC LIMIT 20").fetchdf()

# HK Funds 数据库
conn = duckdb.connect("/opt/eco-data/hk_funds.duckdb")
df = conn.execute("SELECT * FROM hk_funds WHERE is_complex = true").fetchdf()
```

---

## 完整文件夹结构（部署后）

```
/opt/eco-data/
├── .env                              # API keys
├── eco_data.duckdb
├── cn_stock.duckdb
├── us_corp_actions.duckdb
├── us_listings.duckdb                # NEW
├── hk_funds.duckdb                   # NEW
├── app/                              # Eco Data API :8000
├── cn_stock/                         # CN Stock API :8001
├── us_corp_actions/                  # US Corp Actions :8002
├── us_listings/                      # NEW — US IPO + Equities :8003
│   ├── __init__.py
│   ├── config.py
│   ├── storage.py
│   ├── pipeline.py
│   ├── crypto_pipeline.py
│   ├── insider_pipeline.py
│   ├── earnings_pipeline.py
│   ├── holdings_pipeline.py
│   ├── risk_pipeline.py
│   ├── flow_pipeline.py
│   ├── corporate_events_pipeline.py
│   ├── suspension_pipeline.py
│   ├── enforcement_pipeline.py
│   ├── threshold_pipeline.py
│   ├── ats_pipeline.py
│   ├── short_sale_pipeline.py
│   ├── lockup_pipeline.py
│   ├── options_flow_pipeline.py
│   ├── api.py
│   └── scheduler.py
├── hk_funds/                         # NEW — HK Fund KYP :8004
│   ├── __init__.py
│   ├── config.py
│   ├── storage.py
│   ├── pipeline_funds.py
│   ├── pipeline_managers.py
│   ├── api.py
│   └── scheduler.py
├── frontend/                         # Next.js :3000
│   ├── src/app/hk-funds/             # NEW — HK 基金页面
│   │   ├── page.tsx
│   │   └── HkFundsContent.tsx
│   ├── src/app/us-listings/          # UPDATED — 16 tabs
│   │   ├── page.tsx
│   │   └── ListingsContent.tsx
│   ├── src/components/NavBar.tsx     # UPDATED — 新增 HK基金 tab
│   ├── src/lib/api.ts               # UPDATED — 新增 HK fund 接口
│   └── .env.local                   # UPDATED — 新增 :8003 :8004
├── mcp/
└── eco_data_sdk/
```

---

## 服务端口总览

| 端口 | 服务 | Systemd unit |
|------|------|-------------|
| 8000 | Eco Data API | `eco-data-api` |
| 8001 | CN Stock API | `cn-stock-api` |
| 8002 | US Corp Actions API | `us-corp-api` |
| 8003 | US Listings API | `us-listings-api` |
| 8004 | HK Funds API | `hk-funds-api` |
| 3000 | Frontend (Next.js) | `zt-frontend` |

Scheduler 进程（无端口）：
- `eco-data-scheduler`
- `cn-stock-scheduler`
- `us-corp-scheduler`
- `us-listings-scheduler`
- `hk-funds-scheduler`
