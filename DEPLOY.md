# 涨停复盘 — 完整部署手册

> 给部署者的 Claude Code：按以下步骤操作即可完成三服务部署。

## 项目概览

```
/opt/eco-data/
├── .env                          # 全局 API keys（所有服务共用）
├── eco_data.duckdb               # 宏观数据库（8MB，41 个指标）
├── cn_stock.duckdb               # 涨停数据库（4MB）
├── app/                          # ① Eco Data API — 宏观数据服务 :8000
│   ├── api.py                    #   FastAPI app
│   ├── config.py                 #   读 .env
│   ├── pipeline.py               #   数据拉取 + 定时器
│   ├── storage.py                #   DuckDB 读写
│   ├── indicators_registry.py    #   41 个指标注册表
│   └── eco_harness/              #   各数据源适配器（FRED/AKShare/WB/BoJ/EIA）
├── cn_stock/                     # ② cn_stock API — 涨停数据服务 :8001
│   ├── api.py                    #   FastAPI app
│   ├── config.py                 #   读 .env（LLM keys）
│   ├── pipeline.py               #   AKShare 抓取 + LLM 打标
│   ├── storage.py                #   DuckDB 读写
│   ├── tagging.py                #   LLM 原因分析 / 主线归纳
│   └── scheduler.py              #   定时器（每个交易日 15:37）
├── us_corp_actions/              # ③ US Corp Actions API — 美国公司行动 :8002
│   ├── api.py                    #   FastAPI app
│   ├── config.py                 #   配置（SEC URLs, DB_PATH）
│   ├── pipeline.py               #   SEC EDGAR 抓取 + 8-K 分类
│   ├── storage.py                #   DuckDB 读写
│   └── scheduler.py              #   定时器（每交易日 06:07 HKT）
├── us_corp_actions.duckdb        # 美国公司行动数据库
├── frontend/                     # ④ Next.js 前端 — Web UI :3000
│   ├── src/app/                  #   App Router 页面
│   ├── src/components/           #   React 组件
│   ├── src/lib/api.ts            #   API 客户端（读 :8000 / :8001 / :8002）
│   ├── package.json              #   Next.js 16 + Tailwind v4
│   └── .env.local                #   前端环境变量
├── eco_data_sdk/                 # Python SDK（可选，方便脚本调用）
└── PRODUCT.md                    # 产品文档
```

---

## Step 1 — 上传项目到服务器

```bash
# 方式 A：直接 scp
scp -r "/Users/a80460/Desktop/cibo eco data" user@your-server:/opt/
mv /opt/cibo\ eco\ data /opt/eco-data

# 方式 B：GitHub
# cd "/Users/a80460/Desktop/cibo eco data"
# git init && git add -A && git commit -m "init"
# git remote add origin <your-repo-url> && git push -u origin main
# 然后在服务器上 git clone
```

---

## Step 2 — 环境变量

### 2.1 全局 .env（项目根目录）

```bash
cd /opt/eco-data
cat > .env << 'EOF'
FRED_API_KEY=654f4b4eed93d8d3844e2386fa80b6ea
EIA_API_KEY=hVjuA155lYU1S6HkSU0ewwLcPxUeIf0JaBJYdQTB
DEEPSEEK_API_KEY=sk-e62bc33301744b6f9bc6fae9a98c1962
EOF
```

各 key 的用途：
- `FRED_API_KEY` — 美国宏观数据（美联储利率、国债收益率等），由 app/ 服务使用
- `EIA_API_KEY` — 能源数据（WTI 原油等），由 app/ 服务使用
- `DEEPSEEK_API_KEY` — LLM 打标（涨停原因分析 + 市场主线总结），由 cn_stock/ 服务使用

### 2.2 前端 .env.local

```bash
cat > /opt/eco-data/frontend/.env.local << 'EOF'
NEXT_PUBLIC_API_URL=http://127.0.0.1:8001
NEXT_PUBLIC_ECO_API_URL=http://127.0.0.1:8000
NEXT_PUBLIC_US_CORP_API_URL=http://127.0.0.1:8002
EOF
```

> 如果前端和 API 在不同服务器，把 `127.0.0.1` 改成 API 服务器的内网 IP。

---

## Step 3 — 安装依赖

### 3.1 Python（两个 API 服务共享同一套包）

```bash
python3 -m pip install \
  fastapi uvicorn \
  duckdb pandas \
  akshare \
  fredapi wbgapi boj-api dbnomics requests \
  beautifulsoup4 lxml \
  apscheduler python-dotenv \
  openai anthropic
```

> Python ≥ 3.10 即可。`opensdmx` 需要 ≥ 3.12，如不可用 SDMX 模块会自动降级。

### 3.2 Node.js（前端）

```bash
cd /opt/eco-data/frontend
npm install
```

> 需要 Node.js ≥ 18。

---

## Step 4 — 初始化数据库

### 4.1 宏观数据库

```bash
cd /opt/eco-data
python3 -c "from app.pipeline import run_once; print(run_once())"
```

大约 1-2 分钟，拉取 41 个指标、约 49,000 条观测数据。成功后 `eco_data.duckdb` 会出现。

### 4.2 涨停数据库

`cn_stock.duckdb` 在首次启动 API 时会自动建表。手动拉取首日数据：

```bash
cd /opt/eco-data
python3 -c "from cn_stock.pipeline import fetch_latest; print(fetch_latest(use_llm=True))"
```

这会通过 AKShare 拉取最近一个交易日的数据，并通过 DeepSeek 做 LLM 打标。

### 4.3 美国公司行动数据库

```bash
cd /opt/eco-data
python3 -m us_corp_actions.pipeline --init
```

这会：
1. 从 SEC 下载 CIK↔Ticker 映射（约 10,000 家美国上市公司）
2. 从 2026-06-10 起回填所有 8-K 公司行动数据
3. 按 8-K Item 编号自动分类（并购重组/股权变更/证券发行/退市/破产/股利/股票拆分/股份回购/业绩公告/其他）

成功后 `us_corp_actions.duckdb` 会出现。

---

## Step 5 — 启动服务

### 5.1 Eco Data API（端口 8000）

```bash
cd /opt/eco-data
python3 -m uvicorn app.api:app --host 127.0.0.1 --port 8000
```

验证：
```bash
curl http://127.0.0.1:8000/api/v1/health
# {"status":"ok","indicators":41,"observations":49177}

curl http://127.0.0.1:8000/api/v1/data/5/latest
# {"indicator":{"id":5,"name":"制造业PMI",...},"latest":{"date":"2026-05-31","value":50.2}}
```

### 5.2 cn_stock API（端口 8001）

```bash
cd /opt/eco-data
python3 -m uvicorn cn_stock.api:app --host 127.0.0.1 --port 8001
```

验证：
```bash
curl http://127.0.0.1:8001/api/v1/health
# {"status":"ok","total_stocks":1234,"trading_days":30}

curl http://127.0.0.1:8001/api/v1/dates
# {"count":30,"dates":["2026-06-12","2026-06-11",...]}
```

### 5.3 前端（端口 3000）

**开发模式（调试用）：**
```bash
cd /opt/eco-data/frontend
npm run dev
# 监听 0.0.0.0:3000
```

**生产模式（推荐）：**
```bash
cd /opt/eco-data/frontend
npm run build
npm run start
# 监听 0.0.0.0:3000
```

> 生产模式需要先 `npm run build`，Next.js 会生成优化的静态 + SSR 产物。

验证：
```bash
curl http://127.0.0.1:3000
# 返回 HTML 页面
```

---

## Step 6 — Systemd 保活（推荐）

### 6.1 Eco Data API

```bash
cat > /etc/systemd/system/eco-data-api.service << 'EOF'
[Unit]
Description=Eco Data API
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/eco-data
ExecStart=/usr/bin/python3 -m uvicorn app.api:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
```

### 6.2 cn_stock API

```bash
cat > /etc/systemd/system/cn-stock-api.service << 'EOF'
[Unit]
Description=CN Stock Limit-Up API
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/eco-data
ExecStart=/usr/bin/python3 -m uvicorn cn_stock.api:app --host 127.0.0.1 --port 8001
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
```

### 6.3 US Corp Actions API（端口 8002）

```bash
cat > /etc/systemd/system/us-corp-api.service << 'EOF'
[Unit]
Description=US Corporate Actions API
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/eco-data
ExecStart=/usr/bin/python3 -m uvicorn us_corp_actions.api:app --host 127.0.0.1 --port 8002
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
```

### 6.4 前端（Next.js 生产模式）

```bash
cat > /etc/systemd/system/zt-frontend.service << 'EOF'
[Unit]
Description=涨停复盘 Frontend (Next.js)
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/eco-data/frontend
ExecStart=/usr/bin/node node_modules/.bin/next start
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
```

### 6.5 启用所有服务

```bash
systemctl daemon-reload
systemctl enable --now eco-data-api
systemctl enable --now cn-stock-api
systemctl enable --now us-corp-api
systemctl enable --now zt-frontend

# 检查状态
systemctl status eco-data-api cn-stock-api us-corp-api zt-frontend
```

---

## Step 7 — 反向代理（对外暴露）

用 Nginx 或 Caddy。以下以 Nginx 为例：

```bash
cat > /etc/nginx/sites-available/zt-replay << 'EOF'
server {
    listen 80;
    server_name your-domain.com;

    # 前端
    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # API 不对外暴露（安全考虑），如需暴露则取消注释：
    # location /api/v1/eco/ {
    #     proxy_pass http://127.0.0.1:8000/api/v1/;
    # }
    # location /api/v1/stock/ {
    #     proxy_pass http://127.0.0.1:8001/api/v1/;
    # }
}
EOF

ln -s /etc/nginx/sites-available/zt-replay /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
```

> 前端通过服务端 fetch（`next: { revalidate: 300 }`）直接请求 `127.0.0.1:8000` / `127.0.0.1:8001`，不需要对外暴露 API 端口。这更安全。

---

## 定时任务

### 宏观数据刷新

```bash
cd /opt/eco-data
python3 -c "
from app.pipeline import start_scheduler
scheduler = start_scheduler()
# 保持进程运行：每天 8:07 拉日频 / 每周一 8:13 拉周频 / 每月 15 号 8:21 拉全量
import time
while True:
    time.sleep(60)
"
```

或者用 systemd 管理定时器进程：

```bash
cat > /etc/systemd/system/eco-data-scheduler.service << 'EOF'
[Unit]
Description=Eco Data Scheduler
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/eco-data
ExecStart=/usr/bin/python3 -c "from app.pipeline import start_scheduler; scheduler = start_scheduler(); import time; [time.sleep(60) for _ in iter(int, 1)]"
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now eco-data-scheduler
```

### 涨停数据每日抓取

```bash
cat > /etc/systemd/system/cn-stock-scheduler.service << 'EOF'
[Unit]
Description=CN Stock Daily Scheduler
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/eco-data
ExecStart=/usr/bin/python3 -m cn_stock.scheduler
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now cn-stock-scheduler
```

每个交易日 15:37 自动抓取当日涨停数据 + LLM 打标。

### 美国公司行动每日抓取

```bash
cat > /etc/systemd/system/us-corp-scheduler.service << 'EOF'
[Unit]
Description=US Corp Actions Daily Scheduler
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/eco-data
ExecStart=/usr/bin/python3 -m us_corp_actions.scheduler
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now us-corp-scheduler
```

每交易日 06:07 HKT（≈ 美东 18:07 T-1）自动抓取 SEC EDGAR 8-K 公司行动数据并分类。

---

## API 接口文档

### Eco Data API（端口 8000）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/health` | 健康检查 |
| GET | `/api/v1/indicators` | 所有指标列表，可选 `?source=cn` 过滤中国 |
| GET | `/api/v1/indicators/search?q=GDP` | 关键词搜索 |
| GET | `/api/v1/indicators/{id}` | 单个指标详情 |
| GET | `/api/v1/data/{id}/latest` | 指标最新值 |
| GET | `/api/v1/data/{id}?limit=12` | 历史数据（倒序），默认 1000 条 |
| POST | `/api/v1/fetch?source=cn` | 触发数据刷新 |

前端使用的 6 个指标 ID：

| ID | 指标 | 频率 | 来源 |
|----|------|------|------|
| 5 | 制造业PMI | monthly | cn |
| 7 | M2 货币供应量 | monthly | cn |
| 8 | LPR 5年期 | monthly | cn |
| 14 | 70城新建住宅价格指数 | monthly | cn |
| 53 | WTI 原油现货价 | daily | energy |
| 32 | 联邦基金利率 | daily | us |

### cn_stock API（端口 8001）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/health` | 健康检查 |
| GET | `/api/v1/daily/{date}` | 每日完整复盘（股票 + 主线 + 行业分布） |
| GET | `/api/v1/stocks/{date}?industry=半导体` | 某日涨停股，可选行业过滤 |
| GET | `/api/v1/stock/{code}` | 某只股票的历史涨停记录 |
| GET | `/api/v1/narratives/{date}` | 某日市场主线 |
| GET | `/api/v1/industry/{date}` | 某日行业分布 |
| GET | `/api/v1/dates` | 可用交易日列表 |
| POST | `/api/v1/fetch?date=20260612` | 触发数据抓取（不传 date 默认最新） |

### US Corp Actions API（端口 8002）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/health` | 健康检查 |
| GET | `/api/v1/actions/{date}` | 某日完整公司行动复盘（含 summary + actions + breakdown） |
| GET | `/api/v1/actions?start=YYYYMMDD&end=YYYYMMDD&action_type=dividend&ticker=AAPL` | 按条件筛选公司行动 |
| GET | `/api/v1/actions/ticker/{ticker}` | 某 ticker 的历史公司行动 |
| GET | `/api/v1/dates` | 可用交易日列表 |
| GET | `/api/v1/summary?start=YYYYMMDD&end=YYYYMMDD` | 日期范围内的每日汇总 |
| GET | `/api/v1/breakdown/{date}` | 某日行动类型分布 |
| GET | `/api/v1/fetch/status` | 最近抓取状态 |
| POST | `/api/v1/fetch?date=20260618` | 触发数据抓取（不传 date 默认上一交易日） |
| POST | `/api/v1/init` | 初始化：下载 CIK 映射 + 回填历史数据 |

行动类型（action_type）：

| 类型 | 英文 | 8-K Items |
|------|------|-----------|
| 并购重组 | merger_acquisition | 1.01, 2.01 |
| 股权变更 | equity_change | 5.01-5.08 |
| 证券发行 | securities_issuance | 3.02, 3.03 |
| 退市 | delisting | 3.01 |
| 破产 | bankruptcy | 1.03 |
| 股利 | dividend | 8.01 (keyword) |
| 股票拆分 | stock_split | 8.01 (keyword) |
| 股份回购 | buyback | 8.01 (keyword) |
| 业绩公告 | earnings | 2.02 |
| 其他 | other | 8.01, 9.01 |

---

## 前端数据流

```
                    浏览器
                      │
                      ▼
              Next.js (:3000)  ──服务端 fetch──►  cn_stock API (:8001)
              （Server Components）               Eco Data API (:8000)
                                                  US Corp API (:8002)
                      │
                      ▼
              HTML 页面（含 Glass UI + 暗色主题）
```

前端是 **Next.js App Router**，首页 `src/app/page.tsx` 是 Server Component，通过 `fetchDailyReview()` 和 `fetchMacroBackground()` 在服务端拉取数据后 SSR 渲染。客户端交互组件（MacroBar、StockTable）标记 `"use client"`，在浏览器中处理展开/折叠、搜索、筛选。

ISR 策略：`fetch` 带 `next: { revalidate: 300 }`（5分钟），`fetchAvailableDates` 带 `next: { revalidate: 60 }`（1分钟）。

---

## 故障排查

### 前端显示"暂无数据"

1. 确认 cn_stock API 在运行：`curl http://127.0.0.1:8001/api/v1/health`
2. 确认有数据：`curl http://127.0.0.1:8001/api/v1/dates`
3. 手动拉取当日数据：`curl -X POST http://127.0.0.1:8001/api/v1/fetch`

### 宏观卡片全是 "—"

1. 确认 Eco Data API 在运行：`curl http://127.0.0.1:8000/api/v1/health`
2. 确认前端 `.env.local` 中的 `NEXT_PUBLIC_ECO_API_URL` 正确
3. 检查指标是否有数据：`curl http://127.0.0.1:8000/api/v1/data/5/latest`

### Next.js build 失败

1. 确认 Node.js ≥ 18：`node --version`
2. 清除缓存重试：`rm -rf .next && npm run build`
3. 如 Tailwind v4 相关报错，确认 `@tailwindcss/postcss` 已安装

### 前端改了代码不生效

Turbopack 有时缓存问题：`rm -rf .next && npm run dev`

---

## 安全注意事项

- API 端口（8000/8001）只监听 `127.0.0.1`，不对外暴露
- `.env` 包含 API key，不要提交到 Git，权限设 `chmod 600 .env`
- 生产环境使用 `npm run build && npm run start`，不要用 dev 模式
- 如需对外暴露 API，务必加上认证层（API key / JWT）
