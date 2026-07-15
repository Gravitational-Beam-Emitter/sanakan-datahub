# HK Funds KYP/DD 系统部署与使用指南

## 目录结构

```
/Users/a80460/Desktop/cibo datahub/
├── hk_funds/                          # 核心模块
│   ├── api.py                         # FastAPI REST API (port 8004)
│   ├── storage.py                     # DuckDB schema + CRUD
│   ├── config.py                      # 配置
│   ├── pipeline_funds.py              # SFC 基金列表抓取
│   ├── pipeline_managers.py           # SFC 管理人抓取 + 执法记录
│   ├── pipeline_ofc.py                # OFC 开放式基金公司
│   ├── pipeline_manager_scrape.py     # 管理人网站爬取编排
│   ├── scheduler.py                   # 定时任务
│   ├── risk_rating.py                 # 5级风险评级
│   ├── classification.py              # 复杂产品分类 (§5.1A / §5.5)
│   ├── tagging.py                     # 标签
│   ├── isin_sources.py                # ISIN 来源调查 + 校验
│   ├── hk_funds.duckdb                # 主数据库 ⭐
│   └── manager_connectors/            # 管理人爬虫 (15个已注册)
│       ├── __init__.py                # 注册中心 + @register_connector
│       ├── base.py                    # BaseManagerConnector 基类
│       ├── hkex_etf.py                # HKEX ETF ISIN 导入 (通用)
│       ├── csop.py                    # 南方东英 (CE: ARN075, 84 funds)
│       ├── chinaamc.py                # 华夏基金 (CE: ARS988, 51 funds)
│       ├── mirae.py                   # 未来资产 (CE: BJB333, 63 funds)
│       ├── blackrock.py               # 贝莱德 (CE: AFF275, 24 funds)
│       ├── hangseng.py                # 恒生投资 (CE: ABT330, 25 funds)
│       ├── efund.py                   # 易方达 (CE: ARO593, 18 funds)
│       ├── bosera.py                  # 博时 (CE: AVR135, 18 funds)
│       ├── harvest.py                 # 嘉实 (CE: ASE565, 9 funds)
│       ├── icbc.py                    # 工银 (CE: AAY077, 5 funds)
│       ├── invesco.py                 # 景顺 (CE: AAJ770, 3 funds)
│       ├── amundi.py                  # 东方汇理 (CE: AAB444, 4 funds)
│       ├── statestreet.py             # 道富 (CE: AEI343, 3 funds)
│       ├── valuepartners.py           # 惠理 (CE: AFJ002, 3 funds)
│       ├── samsungam.py               # 三星 (CE: AQG442, 9 funds)
│       └── premia.py                  # Premia (CE: BIN676, 14 funds)
├── eco_data_sdk/                      # Python SDK
│   └── hk_funds_client.py             # 同步 HTTP 客户端
├── mcp/                               # MCP Server
│   └── eco_data_server.py             # MCP JSON-RPC over stdio (17 HK tools)
└── frontend/                          # Next.js 前端
```

---

## 一、部署步骤

### 1. 环境要求

```bash
python3 --version  # >= 3.10
pip install fastapi uvicorn duckdb pandas requests openpyxl
```

### 2. 确认数据库存在

```bash
ls -la "/Users/a80460/Desktop/cibo datahub/hk_funds/hk_funds.duckdb"
```

如不存在，需要先初始化：

```bash
cd "/Users/a80460/Desktop/cibo datahub"
python3 -c "
from hk_funds.storage import init_db
conn = init_db()
print('Tables:', conn.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall())
conn.close()
"
```

### 3. 启动 REST API (必须)

```bash
cd "/Users/a80460/Desktop/cibo datahub"
python3 -m uvicorn hk_funds.api:app --host 127.0.0.1 --port 8004
```

验证：
```bash
curl http://127.0.0.1:8004/api/v1/health | python3 -m json.tool
```

### 4. 配置 MCP Server (Claude Code 集成)

编辑 Claude Code 配置文件：

```bash
# 项目级配置 (推荐)
cat >> "/Users/a80460/Desktop/cibo datahub/.mcp.json" << 'MCPEOF'
{
  "mcpServers": {
    "eco-data": {
      "command": "python3",
      "args": ["mcp/eco_data_server.py"],
      "cwd": "/Users/a80460/Desktop/cibo datahub"
    }
  }
}
MCPEOF
```

或者全局配置 `~/.claude.json`:

```json
{
  "mcpServers": {
    "eco-data": {
      "command": "python3",
      "args": ["mcp/eco_data_server.py"],
      "cwd": "/Users/a80460/Desktop/cibo datahub"
    }
  }
}
```

重启 Claude Code 后，MCP 工具自动可用。

### 5. 首次数据抓取 (按顺序执行)

```bash
API="http://127.0.0.1:8004"

# 1) 抓取 SFC 基金列表 (2,021 只)
curl -X POST "$API/api/v1/fetch-funds"

# 2) 抓取 SFC 管理人 + 执法记录
curl -X POST "$API/api/v1/fetch-managers"

# 3) 运行分类引擎 (§5.1A / §5.5)
curl -X POST "$API/api/v1/classify"

# 4) 基金管理人关联
curl -X POST "$API/api/v1/link-managers"

# 5) 导入 HKEX ETF ISINs
curl -X POST "$API/api/v1/import-isins"

# 6) 运行风险评级
curl -X POST "$API/api/v1/risk-ratings/calculate"

# 7) 抓取 OFC 开放式基金公司
curl -X POST "$API/api/v1/ofc/init"
```

---

## 二、SDK 使用 (Python)

```python
from eco_data_sdk.hk_funds_client import HkFundsClient

client = HkFundsClient(base_url="http://localhost:8004")

# ── 健康检查 ──
print(client.health())
# {"status":"ok", "funds":{"total":2021,"with_isin":54,...}, "nav_records":0, ...}

# ── 查询基金 ──
funds = client.list_funds(search="BlackRock", limit=10)
print(funds["count"])  # 89

# ── 按 ISIN 查找 ──
fund = client.get_fund_by_isin("HK0000123456")

# ── 列出有 ISIN 的基金 ──
isins = client.list_funds_with_isins(limit=50)

# ── 基金详情 (含 NAV + 业绩) ──
detail = client.get_fund(fund_id=1)

# ── NAV 历史 ──
nav = client.get_nav_history(fund_id=1, start="2025-01-01", end="2026-06-22")

# ── 最新 NAV ──
latest = client.get_latest_nav(fund_id=1)

# ── 业绩指标 ──
perf = client.get_fund_performance(fund_id=1)

# ── 风险评级 ──
ratings = client.all_risk_ratings(risk_category="High")

# ── 复杂产品 ──
complex_funds = client.complex_products(complex_product_type="L&I")

# ── KYP 尽调维度 ──
kyp = client.get_kyp(fund_id=1)

# ── 管理人 ──
mgrs = client.list_managers(search="CSOP")
mgr = client.get_manager(manager_id=1)
mgr_funds = client.manager_funds(manager_id=1)

# ── 管理人 DD ──
dd = client.get_manager_dd(manager_id=1)

# ── OFC ──
print(client.ofc_stats())

# ── 触发 ISIN 导入 ──
result = client.import_isins()

# ── 管理人网站爬取 ──
status = client.scrape_status()
client.scrape_manager(ce_number="ARN075")  # 抓取 CSOP
```

---

## 三、MCP 工具 (Claude Code 中使用)

MCP server 启动后，以下 17 个 HK 工具可直接使用：

### 查询与搜索
| 工具 | 说明 |
|------|------|
| `hk_fund_stats` | 总体统计：基金数、ISIN覆盖、NAV记录、风险分布 |
| `hk_fund_search` | 按名称/ISIN/授权号/管理人搜索基金 |
| `hk_fund_isin_lookup` | 按ISIN查基金，含NAV+业绩 |
| `hk_fund_isins` | 列出有ISIN的基金 (分页) |
| `hk_fund_managers` | 管理人搜索 (按公司名/CE号) |
| `hk_fund_risk_ratings` | 风险评级列表 (5级，可筛选) |

### 基金详情
| 工具 | 说明 |
|------|------|
| `hk_fund_nav_history` | NAV 时间序列 (支持日期范围) |
| `hk_fund_latest_nav` | 最新净值 |
| `hk_fund_performance` | 业绩指标：YTD/1M/3M/1Y/3Y/5Y, Sharpe, Alpha, Beta 等 |
| `hk_kyp_dimensions` | 10维度 KYP 尽调评估 |
| `hk_kyp_gaps` | KYP 尽调缺口的基金 |

### 分类与合规
| 工具 | 说明 |
|------|------|
| `hk_complex_products` | §5.5 复杂产品 (结构化/衍生/L&I) |
| `hk_derivative_products` | §5.1A 衍生工具产品 |
| `hk_ofc_stats` | OFC 开放式基金公司统计 |

### 管理人与运营
| 工具 | 说明 |
|------|------|
| `hk_manager_dd` | 管理人 10维度尽调 |
| `hk_manager_scrape_status` | 爬虫注册状态 (15个connector) |
| `hk_non_authorized_funds` | 非认可基金 (PI only / OFC) |

### 使用示例 (在 Claude Code 中直接调用)

```
查询贝莱德的基金 → hk_fund_search query: "BlackRock"
查某个 ISIN → hk_fund_isin_lookup isin: "HK0000123456"
有多少基金有ISIN → hk_fund_isins limit: 10
某基金的NAV走势 → hk_fund_nav_history fund_id: 1 start: "2025-01-01"
某基金业绩 → hk_fund_performance fund_id: 1
管理人爬虫状态 → hk_manager_scrape_status
高风险基金列表 → hk_fund_risk_ratings risk_category: "High"
```

---

## 四、定时任务 (Scheduler)

```bash
# 查看 scheduler 配置
cat "/Users/a80460/Desktop/cibo datahub/hk_funds/scheduler.py"

# 手动运行每日定时任务
cd "/Users/a80460/Desktop/cibo datahub"
python3 -c "from hk_funds.scheduler import run_daily; run_daily()"

# 设置 crontab (按需)
# 0 9 * * 1-5 cd "/Users/a80460/Desktop/cibo datahub" && python3 -c "from hk_funds.scheduler import run_daily; run_daily()"
```

---

## 五、数据库状态 (当前 2026-06-22)

| 指标 | 数值 |
|------|------|
| 活跃 SFC 认可基金 | 2,021 |
| 拥有 ISIN | 54 (ETF only, via HKEX) |
| NAV 记录 | 0 (待管理人爬虫填充) |
| 业绩记录 | 0 (待管理人爬虫填充) |
| §5.5 复杂产品 | 14 |
| §5.1A 衍生工具产品 | 71 |
| 管理人 | 3,543 (21 有网站) |
| 基金-管理人关联 | 1,068 |
| OFC 公募 | 已入库 |
| 非认可私募 OFC | 2,231 |
| 已注册爬虫 Connector | 15 |

### 待完成：非 ETF 基金的 ISIN

ETF 基金的 ISIN 已通过 HKEX 导入 (54个)。其余 1,900+ 传统基金的 ISIN 需要逐家爬取管理人网站。
15 个 connector 已注册，已覆盖主要中资 + 外资管理人。

查看哪些管理人还需要 connector：
```
hk_manager_scrape_status
```
