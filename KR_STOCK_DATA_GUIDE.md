# KR Stock 韩国股票 — 数据使用说明

## 数据库：`kr_stock/kr_stock.duckdb`（9张表）

| 表 | 说明 | 主键 |
|----|------|------|
| `kr_listed_stocks` | KOSPI/KOSDAQ/KONEX 全部上市股票清单 | `(code)` |
| `kr_daily_prices` | 每日 OHLCV 价格数据 | `(date, code)` |
| `kr_market_indices` | KOSPI(KS11) / KOSDAQ(KQ11) 指数 | `(date, index_code)` |
| `kr_significant_movers` | 显著涨跌股（≥±10%） | `(date, code)` |
| `kr_stock_reasons` | LLM 生成的涨跌原因标签 | `(date, code)` |
| `kr_daily_narratives` | LLM 生成的每日市场主线叙事 | `(date, name)` |
| `kr_dart_filings` | DART 公司披露文件 | `(rcept_no)` |
| `kr_foreign_flows` | 外资/机构/个人日净买卖 | `(date, market)` |
| `kr_fetch_log` | 数据抓取审计日志 | `(id)` |

---

## 一、股票清单查询

### API

```bash
# 查看所有KOSPI股票
curl "http://127.0.0.1:8006/api/v1/listings?market=KOSPI&limit=20"

# 按行业搜索
curl "http://127.0.0.1:8006/api/v1/listings?sector=전기전자&limit=20"

# 按名称搜索
curl "http://127.0.0.1:8006/api/v1/listings?search=삼성&limit=10"

# 单只股票详情（含近期价格和涨跌历史）
curl "http://127.0.0.1:8006/api/v1/stock/005930/detail"
```

### Python SDK

```python
from eco_data_sdk import KrStockClient

kr = KrStockClient()

# 按市场过滤
stocks = kr.list_listings(market="KOSPI", limit=50)

# 按名称搜索
results = kr.list_listings(search="Samsung")

# 单只股票详情
detail = kr.stock_detail("005930")
print(detail["name"], detail["market_cap"], detail["market"])
```

### SQL

```sql
-- KOSPI 前20大市值
SELECT code, name, market_cap
FROM kr_listed_stocks
WHERE market = 'KOSPI' AND is_active = true
ORDER BY market_cap DESC NULLS LAST
LIMIT 20;

-- 各市场上市数量
SELECT market, COUNT(*) FROM kr_listed_stocks WHERE is_active = true GROUP BY market;
```

---

## 二、每日价格数据

### API

```bash
# 单只股票价格历史
curl "http://127.0.0.1:8006/api/v1/stock/005930?limit=30"
```

### Python SDK

```python
# 获取价格历史
prices = kr.stock_history("005930", limit=60)
for p in prices["prices"]:
    print(p["date"], p["close"], f"{p['change_pct']:+.1f}%")
```

### SQL

```sql
-- 某日所有股票涨跌幅排名
SELECT p.code, s.name, p.close, p.change_pct
FROM kr_daily_prices p
JOIN kr_listed_stocks s ON p.code = s.code
WHERE p.date = '2026-06-19'
ORDER BY p.change_pct DESC
LIMIT 20;
```

---

## 三、显著涨跌股（±10%+）

### API

```bash
# 最新交易日
curl "http://127.0.0.1:8006/api/v1/stocks/2026-06-19"

# 按行业过滤
curl "http://127.0.0.1:8006/api/v1/stocks/2026-06-19?industry=반도체"
```

### MCP tools

```
kr_daily_movers  → 获取某日显著涨跌股（含LLM原因）
kr_stock_detail  → 单只股票详情（价格+涨跌历史+原因）
```

### SQL

```sql
-- 某日最大涨幅股
SELECT m.code, m.name, m.change_pct, m.industry, r.reasons
FROM kr_significant_movers m
LEFT JOIN kr_stock_reasons r ON m.date = r.date AND m.code = r.code
WHERE m.date = '2026-06-19'
ORDER BY m.change_pct DESC
LIMIT 10;
```

---

## 四、KOSPI/KOSDAQ 指数

### API

```bash
# KOSPI 指数
curl "http://127.0.0.1:8006/api/v1/indices?index_code=KS11&limit=30"

# 日期范围
curl "http://127.0.0.1:8006/api/v1/indices?index_code=KS11&start=2026-06-01&end=2026-06-19"
```

### MCP

```
kr_market_indices  → 获取指数OHLCV数据
```

### SQL

```sql
SELECT date, close, change_pct
FROM kr_market_indices
WHERE index_code = 'KS11'
ORDER BY date DESC LIMIT 20;
```

---

## 五、LLM 市场叙事

每天收盘后，LLM 自动分析显著涨跌股并生成 5-8 个市场主线叙事。

### API

```bash
# 某日叙事
curl "http://127.0.0.1:8006/api/v1/narratives/2026-06-19"

# 日期范围
curl "http://127.0.0.1:8006/api/v1/narratives/range?start=2026-06-15&end=2026-06-19"
```

### SQL

```sql
SELECT date, tag, name, description
FROM kr_daily_narratives
WHERE date = '2026-06-19'
ORDER BY name;
```

每个叙事包含：
- `tag`: 短标签（如 "반도체", "2차전지", "K-방산"）
- `name`: 主线名称
- `description`: 2-3句分析
- `stocks`: 3-8只代表股票的 JSON（code, name, change_pct）

---

## 六、每日复盘（全量）

### API

```bash
curl "http://127.0.0.1:8006/api/v1/daily/2026-06-19"
```

返回：summary（汇总统计）+ movers（涨跌股列表）+ narratives（市场叙事）+ industries（行业分布）

---

## 七、DART 公司披露

需配置 `.env` 中的 `DART_API_KEY`（从 https://opendart.fss.or.kr 免费注册获取）。

### API

```bash
# 按公司名搜索
curl "http://127.0.0.1:8006/api/v1/filings?corp_name=삼성전자&limit=10"

# 按报告类型搜索（사업보고서=年报, 감사보고서=审计报告, 증권신고서=证券申报）
curl "http://127.0.0.1:8006/api/v1/filings?report_type=사업보고서&limit=10"

# 单件详情
curl "http://127.0.0.1:8006/api/v1/filings/20260619000001"
```

### MCP

```
kr_dart_filings  → 搜索DART披露文件
```

### SQL

```sql
SELECT receipt_date, corp_name, report_nm, url
FROM kr_dart_filings
WHERE corp_name LIKE '%삼성%'
ORDER BY receipt_date DESC
LIMIT 20;
```

---

## 八、资金流向

### API

```bash
curl "http://127.0.0.1:8006/api/v1/flows?market=KOSPI&limit=20"
```

### MCP

```
kr_foreign_flows  → 外资/机构/个人净买卖数据
```

---

## 九、回测数据

### API

```bash
# 趋势数据（每日聚合统计）
curl "http://127.0.0.1:8006/api/v1/trend?start=2026-01-01&end=2026-06-19"

# 板块轮动热力图数据
curl "http://127.0.0.1:8006/api/v1/sectors?start=2026-01-01&end=2026-06-19&top_n=15"

# 单行业时序
curl "http://127.0.0.1:8006/api/v1/sectors/macro?start=2026-01-01&end=2026-06-19&sector=반도체"
```

---

## 十、数据管道命令

```bash
# 初始化（首次部署）：清单 + 指数 + 近几天价格
python -m kr_stock.pipeline --init

# 抓取指定日期
python -m kr_stock.pipeline --date 20260619

# 抓取最新交易日
python -m kr_stock.pipeline

# 跳过LLM标记（省API费用）
python -m kr_stock.pipeline --no-llm

# 抓取最近5个交易日
python -m kr_stock.pipeline --all

# 触发API抓取
curl -X POST http://127.0.0.1:8006/api/v1/fetch?date=2026-06-19
curl -X POST "http://127.0.0.1:8006/api/v1/fetch?llm=false"   # 跳过LLM

# 触发DART披露抓取
curl -X POST http://127.0.0.1:8006/api/v1/fetch-filings

# 查看抓取日志
curl http://127.0.0.1:8006/api/v1/fetch/status?days=7
```

---

## 十一、MCP tools 一览（7个新增工具）

| Tool | 参数 | 说明 |
|------|------|------|
| `kr_stock_stats` | 无 | 数据库概览统计 |
| `kr_listed_stocks` | market, sector, search, limit | 搜索韩国股票 |
| `kr_daily_movers` | date, limit | 某日显著涨跌股（含LLM原因） |
| `kr_market_indices` | index_code, limit | KOSPI/KOSDAQ指数 |
| `kr_foreign_flows` | market, limit | 外资/机构资金流向 |
| `kr_dart_filings` | corp_name, report_type, limit | 搜索DART披露 |
| `kr_stock_detail` | code (必填) | 单只股票完整详情 |

---

## 十二、Python模块路径

```python
# 存储层
from kr_stock.storage import (
    init_db, get_counts, get_listed_stocks, get_stock_detail,
    get_daily_prices, get_daily_movers, get_market_indices,
    get_narratives, get_industry_summary, get_available_dates,
    get_trend_data, get_sector_rotation, get_daily_summary,
    get_foreign_flows, get_dart_filings, get_filing_by_rcept_no,
    get_fetch_status, log_fetch,
)

# 数据管道
from kr_stock.pipeline import (
    fetch_listings, fetch_daily_prices, fetch_indices,
    fetch_significant_movers, fetch_dart_filings,
    fetch_daily, fetch_latest, init_pipeline,
)

# LLM 标记
from kr_stock.tagging import (
    tag_significant_movers, generate_market_narratives,
    needs_llm, active_provider,
)

# SDK 客户端
from eco_data_sdk import KrStockClient
```
