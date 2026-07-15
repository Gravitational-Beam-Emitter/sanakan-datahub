# 公司公告模块部署指南 — for cibo claude

> 以下所有本地路径基于 `/Users/a80460/Desktop/cibo datahub/`，对应服务器 `/opt/eco-data/`。

## 新增内容概览

| 模块 | 端口 | DuckDB | 说明 |
|------|------|--------|------|
| `announcements/` | 8005 | `announcements.duckdb` | 公司公告/业绩会transcript下载（美股SEC + A股东方财富 + 港股HKEX） |

---

## Step 1 — 新增 pip 依赖

```bash
python3 -m pip install pdfplumber
```

`pdfplumber` 用于从 PDF 中提取文本内容（支持中英文）。

其他依赖已覆盖：`akshare`（已在 cn_stock 使用）、`fastapi`、`uvicorn`、`duckdb`、`pandas`、`requests`、`apscheduler`、`python-dotenv`、`beautifulsoup4`。

---

## Step 2 — 复制模块文件到服务器

需要复制以下整个模块目录到 `/opt/eco-data/announcements/`：

**本地路径**：`/Users/a80460/Desktop/cibo datahub/announcements/`

**服务器路径**：`/opt/eco-data/announcements/`

```
announcements/
├── __init__.py
├── config.py
├── storage.py
├── pipeline.py
├── api.py
├── scheduler.py
└── files/              # 空目录，首次运行后自动创建子目录
```

### 2.1 创建目录

```bash
mkdir -p /opt/eco-data/announcements
mkdir -p /opt/eco-data/announcements/files
```

### 2.2 复制文件（用你喜欢的工具 scp/rsync/cp）

需要复制的文件清单：

```
/Users/a80460/Desktop/cibo datahub/announcements/__init__.py
/Users/a80460/Desktop/cibo datahub/announcements/config.py
/Users/a80460/Desktop/cibo datahub/announcements/storage.py
/Users/a80460/Desktop/cibo datahub/announcements/pipeline.py
/Users/a80460/Desktop/cibo datahub/announcements/api.py
/Users/a80460/Desktop/cibo datahub/announcements/scheduler.py
```

---

## Step 3 — 初始化数据库

```bash
cd /opt/eco-data

# 首次运行：回填 3 只测试股票的历史数据
python3 -m announcements.pipeline --init
```

成功后 `/opt/eco-data/announcements.duckdb` 出现，内含 `announcements` 和 `fetch_log` 两张表。

验证：

```bash
python3 -c "
import duckdb
conn = duckdb.connect('/opt/eco-data/announcements.duckdb', read_only=True)
cnt = conn.execute('SELECT COUNT(*) FROM announcements').fetchone()[0]
print(f'Total announcements: {cnt}')
by_market = conn.execute('SELECT market, COUNT(*) FROM announcements GROUP BY market').fetchall()
print(f'By market: {by_market}')
conn.close()
"
```

预期输出类似：
```
Total announcements: 20
By market: [('cn', 17), ('us', 3)]
```

---

## Step 4 — 前端环境变量

更新 `/opt/eco-data/frontend/.env.local`，追加一行：

```bash
# 在现有文件中追加（不要覆盖）
echo 'NEXT_PUBLIC_ANN_API_URL=http://127.0.0.1:8005' >> /opt/eco-data/frontend/.env.local
```

完整文件内容应为：

```
NEXT_PUBLIC_API_URL=http://127.0.0.1:8001
NEXT_PUBLIC_ECO_API_URL=http://127.0.0.1:8000
NEXT_PUBLIC_US_CORP_API_URL=http://127.0.0.1:8002
NEXT_PUBLIC_US_LISTINGS_API_URL=http://127.0.0.1:8003
NEXT_PUBLIC_HK_FUNDS_API_URL=http://127.0.0.1:8004
NEXT_PUBLIC_ANN_API_URL=http://127.0.0.1:8005
```

---

## Step 5 — 前端文件更新

以下 3 个文件有修改，需要更新到服务器：

### 5.1 api.ts

- **本地路径**：`/Users/a80460/Desktop/cibo datahub/frontend/src/lib/api.ts`
- **服务器路径**：`/opt/eco-data/frontend/src/lib/api.ts`
- **改动**：末尾新增了 `ANN_API` 常量、`Announcement` 等 TypeScript 接口、4 个 fetch 函数（`fetchAnnouncements`、`fetchAnnouncementDetail`、`fetchTrackedCompanies`、`triggerAnnFetch`）

### 5.2 NavBar.tsx

- **本地路径**：`/Users/a80460/Desktop/cibo datahub/frontend/src/components/NavBar.tsx`
- **服务器路径**：`/opt/eco-data/frontend/src/components/NavBar.tsx`
- **改动**：TABS 数组新增 `{ href: "/announcements", label: "公告" }`

### 5.3 新增页面

- **本地路径**：`/Users/a80460/Desktop/cibo datahub/frontend/src/app/announcements/`
- **服务器路径**：`/opt/eco-data/frontend/src/app/announcements/`
- **文件**：`page.tsx`、`AnnouncementsContent.tsx`

```bash
mkdir -p /opt/eco-data/frontend/src/app/announcements
# 复制：
#   page.tsx → /opt/eco-data/frontend/src/app/announcements/page.tsx
#   AnnouncementsContent.tsx → /opt/eco-data/frontend/src/app/announcements/AnnouncementsContent.tsx
```

---

## Step 6 — 前端重新构建

```bash
cd /opt/eco-data/frontend
npm run build
systemctl restart zt-frontend
```

---

## Step 7 — 启动新服务

### 7.1 Announcements API（端口 8005）

```bash
cd /opt/eco-data
python3 -m uvicorn announcements.api:app --host 127.0.0.1 --port 8005
```

验证：

```bash
curl http://127.0.0.1:8005/api/v1/health
# {"status":"ok","total_announcements":20,"by_market":{"cn":17,"us":3},"last_fetch":{...}}

curl http://127.0.0.1:8005/api/v1/companies
# {"companies":[{"ticker":"600519","market":"cn",...},{"ticker":"AAPL","market":"us",...}]}

curl "http://127.0.0.1:8005/api/v1/announcements?market=us&limit=3"
# {"count":3,"announcements":[...]}
```

---

## Step 8 — Systemd 保活（2 个新服务）

### 8.1 Announcements API

```bash
cat > /etc/systemd/system/announcements-api.service << 'EOF'
[Unit]
Description=Company Announcements API
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/eco-data
ExecStart=/usr/bin/python3 -m uvicorn announcements.api:app --host 127.0.0.1 --port 8005
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
```

### 8.2 Announcements Scheduler

```bash
cat > /etc/systemd/system/announcements-scheduler.service << 'EOF'
[Unit]
Description=Company Announcements Scheduler
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/eco-data
ExecStart=/usr/bin/python3 -m announcements.scheduler
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
```

### 8.3 启用服务

```bash
systemctl daemon-reload
systemctl enable --now announcements-api
systemctl enable --now announcements-scheduler

# 验证
systemctl status announcements-api announcements-scheduler
```

---

## Step 9 — 自定义公司列表

部署后如需调整要抓取的公司，编辑 `/opt/eco-data/announcements/config.py` 中的 `TRACKED_COMPANIES` 列表：

```python
TRACKED_COMPANIES: list[dict] = [
    # 美股 — 需要 10位 CIK 码（SEC EDGAR）
    {
        "ticker": "AAPL",
        "market": "us",
        "name": "Apple Inc.",
        "cik": "0000320193",
    },
    # 港股 — 需要 5位 股票代码（HKEX主板）
    {
        "ticker": "0700",
        "market": "hk",
        "name": "Tencent Holdings Ltd.",
        "hkex_code": "00700",
    },
    # A股 — 只需要 6位 ticker（AKShare自动识别）
    {
        "ticker": "600519",
        "market": "cn",
        "name": "Kweichow Moutai Co., Ltd.",
    },
    # 添加更多公司 ...
]
```

添加后重新初始化：

```bash
cd /opt/eco-data
python3 -m announcements.pipeline --init
```

---

## 调度器说明

| 时间 | 内容 |
|------|------|
| 每个交易日 08:37 HKT | 对所有 TRACKED_COMPANIES 拉取最近 30 天公告 |

调度器会自动存储新公告（DuckDB upsert 去重），PDF/HTML 文件保存到 `announcements/files/{market}/{ticker}/`。

---

## API 接口速查（5 routes on :8005）

### 健康检查
```
GET  /api/v1/health
```

### 公告列表
```
GET  /api/v1/announcements                           # 全部公告 ?limit=100
GET  /api/v1/announcements?market=us                 # 按市场筛选
GET  /api/v1/announcements?ticker=AAPL               # 按股票筛选
GET  /api/v1/announcements?market=cn&limit=50        # 组合筛选
GET  /api/v1/announcements?start=2026-06-01&end=2026-06-20  # 日期范围
```

### 公告详情（含全文 text_content）
```
GET  /api/v1/announcements/{id}
```

### 已追踪公司
```
GET  /api/v1/companies
```

### 可用日期
```
GET  /api/v1/dates
```

### 触发手动抓取
```
POST /api/v1/fetch
```

### 抓取日志
```
GET  /api/v1/fetch/status?days=7
```

---

## 文件存储结构

```
/opt/eco-data/
└── announcements/
    └── files/
        ├── us/AAPL/
        │   ├── 2026-05-01_000032019326000013_10-Q.html
        │   └── 2026-04-30_000032019326000011_8-K.html
        ├── hk/0700/
        │   └── 2026-06-18_2026061800001.pdf
        └── cn/600519/
            └── 2026-06-12_AN202606111823465368.pdf
```

---

## DuckDB 表结构

```sql
-- announcements 表
id               INTEGER PRIMARY KEY  -- 自增ID
ticker           VARCHAR              -- 股票代码
market           VARCHAR              -- us / hk / cn
company_name     VARCHAR              -- 公司名
title            VARCHAR              -- 公告标题
announcement_date DATE                -- 公告日期
source           VARCHAR              -- sec / hkex / cninfo
filing_type      VARCHAR              -- 8-K / 10-K / annual_report / etc.
source_url       VARCHAR              -- 原文链接
local_file_path  VARCHAR              -- 本地文件路径（相对于 announcements/files）
text_content     TEXT                 -- 提取的文本内容（最多 50KB）
file_type        VARCHAR              -- pdf / html
created_at       TIMESTAMP            -- 创建时间

-- 唯一约束：(ticker, market, source, filing_type, announcement_date, title)
-- 确保同一条公告不会重复存储
```

---

## 已知问题

1. **港股HKEX**：搜索页面使用 JavaServer Faces，GET 请求只返回 GEM 创业板结果。目前用 URL 枚举方式遍历主板PDF链接并匹配股票代码，对单只股票 30 天回看约需 600-900 次 HEAD 请求，耗时 5-8 分钟。如果公司数量增多，建议用 Playwright 无头浏览器或付费API（如 Gugudata）替代。
2. **Koyfin**：需要登录认证，无公开 API，不可行。用 SEC EDGAR（美股）、东方财富（A股）、HKEXnews（港股）替代。
3. **SEC 仅提供 HTML**：EDGAR 不提供原生 PDF，目前下载的是 HTML 文件。如需 PDF，需用 `wkhtmltopdf` 或 `weasyprint` 转换。
4. **A股 PDF 文本提取**：东方财富的 PDF 为扫描件时，pdfplumber 无法提取文本（text_content 为空），但 PDF 文件本身已保存。

---

## 服务端口总览（更新后）

| 端口 | 服务 | Systemd unit |
|------|------|-------------|
| 8000 | Eco Data API | `eco-data-api` |
| 8001 | CN Stock API | `cn-stock-api` |
| 8002 | US Corp Actions API | `us-corp-api` |
| 8003 | US Listings API | `us-listings-api` |
| 8004 | HK Funds API | `hk-funds-api` |
| 8005 | Announcements API | `announcements-api` |
| 3000 | Frontend (Next.js) | `zt-frontend` |

Scheduler 进程（无端口）：
- `eco-data-scheduler`
- `cn-stock-scheduler`
- `us-corp-scheduler`
- `us-listings-scheduler`
- `hk-funds-scheduler`
- `announcements-scheduler`
