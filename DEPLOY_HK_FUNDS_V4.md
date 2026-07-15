# HK Fund KYP V4 部署指南 — 评级引擎优化 + 因子区分度提升

> 本地路径 base: `/Users/a80460/Desktop/Sanakan Datahub/`
> 服务器路径 base: `/opt/eco-data/`
> 端口: 8004 (不变)

## 改动概述（相比 V3）

V3 部署了评级模板引擎 + 50+ 管理人爬虫。V4 核心改动：

1. **评级引擎全面优化** — 全部 18 个 Broker 18I 因子熵值均达到 0.5 以上
2. **AUM 估算** — 对无 AUM 数据的 3616 家管理人，基于牌照组合/维基/成立年限估算规模
3. **DD 维度扩展** — 新增 6 个尽调维度的自动评分（职责分离/合规/审计/托管人/估值/外包监控）
4. **NAV 回填管线** — 新脚本从 Yahoo Finance 拉取 ISIN 基金历史净值
5. **JPMorgan connector 增强** — 新增业绩数据抓取和存储
6. **评分目标收紧** — 仅对 2478 家第 9 类资产管理持牌法团进行评分

---

## 涉及文件

### 新增文件

| 本地路径 | 服务器路径 | 说明 |
|----------|-----------|------|
| `hk_funds/backfill_nav_yfinance.py` | `/opt/eco-data/hk_funds/backfill_nav_yfinance.py` | NAV 回填脚本 |
| `hk_funds/pipeline_enforcement.py` | `/opt/eco-data/hk_funds/pipeline_enforcement.py` | 执法记录管线 |
| `hk_funds/pipeline_fund_performance.py` | `/opt/eco-data/hk_funds/pipeline_fund_performance.py` | 业绩计算管线 |
| `hk_funds/pipeline_manager_aum.py` | `/opt/eco-data/hk_funds/pipeline_manager_aum.py` | AUM 管线 |
| `hk_funds/pipeline_manager_dd.py` | `/opt/eco-data/hk_funds/pipeline_manager_dd.py` | 管理人 DD 管线 |
| `hk_funds/pipeline_sfc_enforcement_api.py` | `/opt/eco-data/hk_funds/pipeline_sfc_enforcement_api.py` | SFC 执法 API |
| `hk_funds/pipeline_webb_site.py` | `/opt/eco-data/hk_funds/pipeline_webb_site.py` | Webb-Site 数据 |
| `hk_funds/pipeline_wikipedia.py` | `/opt/eco-data/hk_funds/pipeline_wikipedia.py` | Wikipedia 数据 |
| `hk_funds/pipeline_wings_probe.py` | `/opt/eco-data/hk_funds/pipeline_wings_probe.py` | WINGS 探测 |
| `hk_funds/fix_fund_manager_linking.py` | `/opt/eco-data/hk_funds/fix_fund_manager_linking.py` | 管理人关联修复 |

### 修改文件（必须覆盖）

| 本地路径 | 服务器路径 | 改动内容 |
|----------|-----------|---------|
| `hk_funds/rating_engine.py` | `/opt/eco-data/hk_funds/rating_engine.py` | **核心** — 全面重写因子评分逻辑 |
| `hk_funds/manager_scoring.py` | `/opt/eco-data/hk_funds/manager_scoring.py` | DD_PASS_SCORE 3→4，阈值调整 |
| `hk_funds/storage.py` | `/opt/eco-data/hk_funds/storage.py` | 新增 NAV/业绩/持仓/分红等 upsert 函数 |
| `hk_funds/api.py` | `/opt/eco-data/hk_funds/api.py` | 新增模板/评级/KYP/NAV/ISIN API 端点 |
| `hk_funds/manager_connectors/base.py` | `/opt/eco-data/hk_funds/manager_connectors/base.py` | 新增 NAV 历史自动收集 |
| `hk_funds/manager_connectors/jpmorgan.py` | `/opt/eco-data/hk_funds/manager_connectors/jpmorgan.py` | 新增业绩数据抓取 |
| `hk_funds/__init__.py` | `/opt/eco-data/hk_funds/__init__.py` | 模块导出更新 |
| `mcp/eco_data_server.py` | `/opt/eco-data/mcp/eco_data_server.py` | 新增 10+ MCP 工具 |
| `eco_data_sdk/__init__.py` | `/opt/eco-data/eco_data_sdk/__init__.py` | SDK 导出更新 |
| `eco_data_sdk/hk_funds_client.py` | `/opt/eco-data/eco_data_sdk/hk_funds_client.py` | 新增模板/评级 API 方法 |
| `frontend/src/lib/api.ts` | `/opt/eco-data/frontend/src/lib/api.ts` | 新增模板/评级 API 函数 |
| `frontend/src/components/NavBar.tsx` | `/opt/eco-data/frontend/src/components/NavBar.tsx` | 新增 HK 基金导航入口 |

### 前端页面（必须覆盖）

| 本地路径 | 服务器路径 |
|----------|-----------|
| `frontend/src/app/hk-funds/page.tsx` | `/opt/eco-data/frontend/src/app/hk-funds/page.tsx` |
| `frontend/src/app/hk-funds/HkFundsContent.tsx` | `/opt/eco-data/frontend/src/app/hk-funds/HkFundsContent.tsx` |
| `frontend/src/app/hk-funds/TemplateEditor.tsx` | `/opt/eco-data/frontend/src/app/hk-funds/TemplateEditor.tsx` |
| `frontend/src/app/hk-funds/RatingResults.tsx` | `/opt/eco-data/frontend/src/app/hk-funds/RatingResults.tsx` |

---

## Step 1 — 批量复制全部文件

```bash
cd "/opt/eco-data"

# ── 创建目录结构 ──
mkdir -p hk_funds/manager_connectors
mkdir -p frontend/src/app/hk-funds
mkdir -p eco_data_sdk
mkdir -p mcp
mkdir -p frontend/src/lib
mkdir -p frontend/src/components

# ═══════════════════════════════════════════
# 后端核心（覆盖式部署）
# ═══════════════════════════════════════════

cp "/Users/a80460/Desktop/Sanakan Datahub/hk_funds/__init__.py"              hk_funds/__init__.py
cp "/Users/a80460/Desktop/Sanakan Datahub/hk_funds/config.py"                hk_funds/config.py
cp "/Users/a80460/Desktop/Sanakan Datahub/hk_funds/storage.py"               hk_funds/storage.py
cp "/Users/a80460/Desktop/Sanakan Datahub/hk_funds/api.py"                   hk_funds/api.py
cp "/Users/a80460/Desktop/Sanakan Datahub/hk_funds/rating_engine.py"         hk_funds/rating_engine.py
cp "/Users/a80460/Desktop/Sanakan Datahub/hk_funds/manager_scoring.py"       hk_funds/manager_scoring.py
cp "/Users/a80460/Desktop/Sanakan Datahub/hk_funds/standard_templates.py"    hk_funds/standard_templates.py
cp "/Users/a80460/Desktop/Sanakan Datahub/hk_funds/risk_rating.py"           hk_funds/risk_rating.py
cp "/Users/a80460/Desktop/Sanakan Datahub/hk_funds/kfs_parser.py"            hk_funds/kfs_parser.py
cp "/Users/a80460/Desktop/Sanakan Datahub/hk_funds/isin_sources.py"          hk_funds/isin_sources.py
cp "/Users/a80460/Desktop/Sanakan Datahub/hk_funds/scheduler.py"             hk_funds/scheduler.py

# 新增管线文件
cp "/Users/a80460/Desktop/Sanakan Datahub/hk_funds/pipeline_funds.py"        hk_funds/pipeline_funds.py
cp "/Users/a80460/Desktop/Sanakan Datahub/hk_funds/pipeline_managers.py"     hk_funds/pipeline_managers.py
cp "/Users/a80460/Desktop/Sanakan Datahub/hk_funds/pipeline_ofc.py"          hk_funds/pipeline_ofc.py
cp "/Users/a80460/Desktop/Sanakan Datahub/hk_funds/pipeline_manager_scrape.py" hk_funds/pipeline_manager_scrape.py
cp "/Users/a80460/Desktop/Sanakan Datahub/hk_funds/pipeline_hkex_import.py"  hk_funds/pipeline_hkex_import.py
cp "/Users/a80460/Desktop/Sanakan Datahub/hk_funds/hkex_pipeline.py"         hk_funds/hkex_pipeline.py
cp "/Users/a80460/Desktop/Sanakan Datahub/hk_funds/pipeline_webb_scraper.py" hk_funds/pipeline_webb_scraper.py
cp "/Users/a80460/Desktop/Sanakan Datahub/hk_funds/pipeline_webb_site.py"    hk_funds/pipeline_webb_site.py
cp "/Users/a80460/Desktop/Sanakan Datahub/hk_funds/pipeline_wikipedia.py"    hk_funds/pipeline_wikipedia.py
cp "/Users/a80460/Desktop/Sanakan Datahub/hk_funds/pipeline_wings.py"        hk_funds/pipeline_wings.py
cp "/Users/a80460/Desktop/Sanakan Datahub/hk_funds/pipeline_wings_probe.py"  hk_funds/pipeline_wings_probe.py
cp "/Users/a80460/Desktop/Sanakan Datahub/hk_funds/pipeline_manager_dd.py"   hk_funds/pipeline_manager_dd.py
cp "/Users/a80460/Desktop/Sanakan Datahub/hk_funds/pipeline_manager_aum.py"  hk_funds/pipeline_manager_aum.py
cp "/Users/a80460/Desktop/Sanakan Datahub/hk_funds/pipeline_enforcement.py"  hk_funds/pipeline_enforcement.py
cp "/Users/a80460/Desktop/Sanakan Datahub/hk_funds/pipeline_sfc_enforcement_api.py" hk_funds/pipeline_sfc_enforcement_api.py
cp "/Users/a80460/Desktop/Sanakan Datahub/hk_funds/pipeline_fund_performance.py" hk_funds/pipeline_fund_performance.py
cp "/Users/a80460/Desktop/Sanakan Datahub/hk_funds/backfill_nav_yfinance.py" hk_funds/backfill_nav_yfinance.py
cp "/Users/a80460/Desktop/Sanakan Datahub/hk_funds/fix_fund_manager_linking.py" hk_funds/fix_fund_manager_linking.py

# ═══════════════════════════════════════════
# 管理人爬虫（50+ connector，全部覆盖）
# ═══════════════════════════════════════════

cp "/Users/a80460/Desktop/Sanakan Datahub/hk_funds/manager_connectors/"*.py hk_funds/manager_connectors/

# ═══════════════════════════════════════════
# SDK + MCP + 前端
# ═══════════════════════════════════════════

cp "/Users/a80460/Desktop/Sanakan Datahub/eco_data_sdk/hk_funds_client.py"   eco_data_sdk/hk_funds_client.py
cp "/Users/a80460/Desktop/Sanakan Datahub/eco_data_sdk/__init__.py"          eco_data_sdk/__init__.py
cp "/Users/a80460/Desktop/Sanakan Datahub/mcp/eco_data_server.py"            mcp/eco_data_server.py
cp "/Users/a80460/Desktop/Sanakan Datahub/frontend/src/lib/api.ts"           frontend/src/lib/api.ts
cp "/Users/a80460/Desktop/Sanakan Datahub/frontend/src/components/NavBar.tsx" frontend/src/components/NavBar.tsx
cp "/Users/a80460/Desktop/Sanakan Datahub/frontend/src/app/hk-funds/"*.tsx   frontend/src/app/hk-funds/
```

---

## Step 2 — 安装依赖

```bash
python3 -m pip install yfinance pypdf pdfplumber apscheduler
```

---

## Step 3 — 重启 API（自动建表）

```bash
systemctl restart hk-funds-api
```

`init_db()` 会自动创建/更新 V4 新增表：
- `hk_fund_nav_history` — NAV 时间序列
- `hk_fund_performance` — 业绩指标
- `hk_fund_holdings` — 持仓
- `hk_fund_dividends` — 分红
- `hk_fund_share_classes` — 份额类别
- `hk_fund_portfolio_managers` — 基金经理
- `hk_manager_dd` — 管理人10维度尽调
- `hk_rating_templates` — 评级模板
- `hk_template_factors` — 模板因子
- `hk_user_ratings` — 评级结果

验证：
```bash
curl http://127.0.0.1:8004/api/v1/health | python3 -m json.tool
```

---

## Step 4 — 初始化标准模板 + 计算评级

```bash
cd /opt/eco-data

# 插入 4 套标准模板
python3 -c "
from hk_funds.standard_templates import seed_standard_templates
from hk_funds.storage import init_db
conn = init_db()
seed_standard_templates(conn)
conn.close()
print('4 standard templates seeded.')
"

# 初始化所有管理人的 10 维度 DD
python3 -m hk_funds.pipeline_manager_dd

# 用 Broker 18I 模板对全部第 9 类管理人计算评级
curl -X POST http://127.0.0.1:8004/api/v1/templates/6/compute \
  -H 'Content-Type: application/json' \
  -d '{"user_id": "broker_18i", "target_type": "manager"}'
```

---

## Step 5 — 重新构建前端

```bash
cd /opt/eco-data/frontend
npm run build
systemctl restart zt-frontend
```

---

## Step 6 — 回填 NAV（可选，耗时较长）

```bash
cd /opt/eco-data

# 先从管理人网站抓取 ISIN + NAV（约 10 分钟）
python3 -m hk_funds.pipeline_manager_scrape

# 再用 Yahoo Finance 回填 NAV 历史（约 30 分钟，548 只有 ISIN 的基金）
python3 -m hk_funds.backfill_nav_yfinance --skip-existing

# 重新计算业绩指标
python3 -c "
from hk_funds.storage import init_db
from hk_funds.pipeline_fund_performance import compute_all_performance
conn = init_db()
compute_all_performance(conn)
conn.close()
"
```

---

## 前端如何使用 V4 数据

### 1. 评级模板编辑器页面
**URL**: `/hk-funds` → TemplateEditor tab

调用 API：
- `GET /api/v1/templates` — 列出 4 套标准模板
- `GET /api/v1/templates/{id}` — 查看模板详情（18 个因子 + 权重 + 配置）
- `POST /api/v1/templates/clone` — 克隆模板并自定义权重/阈值
- `PUT /api/v1/templates/{id}` — 修改因子权重或评分阈值

### 2. 评级结果页面
**URL**: `/hk-funds` → RatingResults tab

调用 API：
- `POST /api/v1/templates/{id}/compute` — 触发评级计算（2478 家第 9 类管理人）
- `GET /api/v1/templates/{id}/results?user_id=broker_18i` — 查看评级分布和结果列表

展示内容：
- F1~F5 五档分布（当前：F1:0.2%, F2:6.9%, F3:54.5%, F4:28.3%, F5:10.1%）
- 每家管理人的综合得分 + 18 个因子明细得分
- 支持按管理人名称搜索、按评级档位筛选

### 3. 管理人详情页
**URL**: `/hk-funds` → 点击管理人名称

调用 API：
- `GET /api/v1/managers/{id}` — 管理人基本信息 + 旗下基金
- `GET /api/v1/managers/{id}/dd` — 10 维度尽调评估得分
- `GET /api/v1/managers/{id}/regulatory` — 执法/处罚记录

### 4. 基金详情页
**URL**: `/hk-funds` → 点击基金名称

调用 API：
- `GET /api/v1/funds/{id}` — 基金详情 + 最新 NAV
- `GET /api/v1/funds/{id}/nav-history` — NAV 时间序列图
- `GET /api/v1/funds/{id}/performance` — YTD/1M/3M/1Y 业绩
- `GET /api/v1/funds/{id}/risk-rating` — 风险评级

### 5. MCP 工具（Claude Code 中直接调用）

```
查评级模板列表     → hk_rating_template_list
查 Broker 18I 模板 → hk_rating_template_get template_id: 6
查看评级结果       → hk_rating_results template_id: 6 user_id: "broker_18i"
重新计算评级       → hk_rating_compute template_id: 6 user_id: "broker_18i" target_type: "manager"
查管理人 DD        → hk_manager_dd manager_id: 1
查基金 NAV 历史    → hk_fund_nav_history fund_id: 1
查基金业绩         → hk_fund_performance fund_id: 1
```

---

## V4 核心改进：因子区分度对比

| 因子 | 权重 | V3 熵值 | V4 熵值 | 改进 |
|------|------|---------|---------|------|
| scale_growth | 0.05 | 0.424 | 0.525 | 复合增长代理（牌照年限+AUM+名称变更+多牌照） |
| management_scale | 0.12 | 0.407 | 0.670 | 无 AUM 数据管理人自动估算规模 |
| regulatory_penalties | 0.08 | 0.117 | 0.555 | 执法次数分段 + 牌照年限代理 |
| risk_control_system | 0.08 | 0.452 | 0.721 | 多牌照层级 + 维基/网站信号 |
| distribution_channels | 0.07 | 0.562 | 0.825 | AUM 估算改进级联效应 |
| investor_composition | 0.03 | 0.584 | 0.824 | 同上 |
| institutional_reputation | 0.08 | 0.602 | 0.845 | 同上 |

**V3**: 7 个低熵因子（< 0.5） | **V4**: 0 个低熵因子（全部 ≥ 0.513）

---

## 数据库：不需要传输，不需要覆盖

**本地 DuckDB 不上传服务器。** 服务器上已有的 `hk_funds.duckdb` 保持不动，所有存量数据（基金、管理人、分类、执法记录等）原封不动。

V4 新增的数据全部通过以下方式在服务器上**重新生成**，无需从本地迁移：

| 新增数据 | 如何生成 | 命令 |
|---------|---------|------|
| 管理人 10 维 DD 评分 | 本地计算后写入 | `python3 -m hk_funds.pipeline_manager_dd` |
| 2478 家管理人评级结果 | 用模板计算 | `POST /api/v1/templates/6/compute` |
| NAV 历史数据 | Yahoo Finance 拉取 | `python3 -m hk_funds.backfill_nav_yfinance` |
| 业绩指标 | 从 NAV 计算 | `python3 -m hk_funds.pipeline_fund_performance` |
| 管理人 ISIN/网站数据 | 爬虫抓取 | `python3 -m hk_funds.pipeline_manager_scrape` |

**一句话：部署代码 → 跑脚本 → 数据就有了。服务器的旧数据完全不受影响。**

### 数据管线执行顺序

如果服务器之前没有跑过以下管线，按顺序执行（每个都是增量 upsert，不覆盖已有数据）：

```bash
cd /opt/eco-data

# 1. Webb-Site 数据（公司成立日期、名称变更、网站等）—— 评级引擎依赖
python3 -m hk_funds.pipeline_webb_site

# 2. Wikipedia 数据（中英文维基条目）—— 机构声誉因子依赖
python3 -m hk_funds.pipeline_wikipedia

# 3. 管理人 AUM 数据
python3 -m hk_funds.pipeline_manager_aum

# 4. 执法记录（SFC enforcement API）
python3 -m hk_funds.pipeline_sfc_enforcement_api

# 5. 管理人 10 维 DD 评分（必须在评级计算之前）
python3 -m hk_funds.pipeline_manager_dd

# 6. 用 Broker 18I 模板计算全部第 9 类管理人评级
curl -X POST http://127.0.0.1:8004/api/v1/templates/6/compute \
  -H 'Content-Type: application/json' \
  -d '{"user_id": "broker_18i", "target_type": "manager"}'
```
