# HK Fund KYP V3 部署指南 — 评级引擎 + 模板系统 + 管理人爬虫矩阵

> 本地路径 base: `/Users/a80460/Desktop/Sanakan Datahub/`
> 服务器路径 base: `/opt/eco-data/`
> 端口: 8004 (不变)

## 数据迁移说明

**无需重新抓取基金数据。** V3 只替换 Python 代码，不动数据库文件。服务器上 `hk_funds/hk_funds.duckdb` 里的现有基金数据全部保留。

**管理人数据建议重新抓取。** V2 的 SFC public register 已迁移到 WINGS，旧 API 返回空数据。V3 新增的 `pipeline_wings.py` 使用 WINGS 公开 API（无需登录），可以直接抓取 3,500+ 管理人。

---

## 改动概述（相比 V2）

V2 部署了 SFC 双层分类引擎。V3 新增：

1. **可配置评级模板引擎** — 支持 fund_risk / manager_dd 两类模板，任意配置因子权重和阈值
2. **4 套标准模板** — SFC 6-Factor、Broker 13-Factor Scorecard、10-Dimension DD、Broker 18-Indicator DD
3. **管理人评分引擎** — 10维度尽调自动映射到 5-tier Internal Control
4. **管理人爬虫矩阵** — 50+ 管理人网站 ISIN 爬虫 (从 15 个扩展到 50+)
5. **KFS PDF 解析器** — 从 KFS PDF 提取管理人/基金经理
6. **MCP 工具** — 从 17 个扩展到 27 个 HK 工具
7. **前端** — 新增评级模板编辑器 + 评级结果页面
8. **SDK** — hk_funds_client.py 完整 HTTP 客户端

---

## 涉及文件（全部覆盖式部署）

### 后端核心 —— 必须全部覆盖

| 本地路径 | 服务器路径 |
|----------|-----------|
| `hk_funds/__init__.py` | `/opt/eco-data/hk_funds/__init__.py` |
| `hk_funds/config.py` | `/opt/eco-data/hk_funds/config.py` |
| `hk_funds/storage.py` | `/opt/eco-data/hk_funds/storage.py` |
| `hk_funds/api.py` | `/opt/eco-data/hk_funds/api.py` |
| `hk_funds/pipeline_funds.py` | `/opt/eco-data/hk_funds/pipeline_funds.py` |
| `hk_funds/pipeline_managers.py` | `/opt/eco-data/hk_funds/pipeline_managers.py` |
| `hk_funds/pipeline_ofc.py` | `/opt/eco-data/hk_funds/pipeline_ofc.py` |
| `hk_funds/pipeline_manager_scrape.py` | `/opt/eco-data/hk_funds/pipeline_manager_scrape.py` |
| `hk_funds/pipeline_hkex_import.py` | `/opt/eco-data/hk_funds/pipeline_hkex_import.py` |
| `hk_funds/hkex_pipeline.py` | `/opt/eco-data/hk_funds/hkex_pipeline.py` |
| `hk_funds/pipeline_webb_scraper.py` | `/opt/eco-data/hk_funds/pipeline_webb_scraper.py` |
| `hk_funds/risk_rating.py` | `/opt/eco-data/hk_funds/risk_rating.py` |
| `hk_funds/rating_engine.py` | `/opt/eco-data/hk_funds/rating_engine.py` |
| `hk_funds/standard_templates.py` | `/opt/eco-data/hk_funds/standard_templates.py` |
| `hk_funds/manager_scoring.py` | `/opt/eco-data/hk_funds/manager_scoring.py` |
| `hk_funds/kfs_parser.py` | `/opt/eco-data/hk_funds/kfs_parser.py` |
| `hk_funds/isin_sources.py` | `/opt/eco-data/hk_funds/isin_sources.py` |
| `hk_funds/scheduler.py` | `/opt/eco-data/hk_funds/scheduler.py` |
| `hk_funds/pipeline_wings.py` | `/opt/eco-data/hk_funds/pipeline_wings.py` |

### 管理人爬虫 —— 50+ connector 文件

| 本地路径 | 服务器路径 |
|----------|-----------|
| `hk_funds/manager_connectors/__init__.py` | `/opt/eco-data/hk_funds/manager_connectors/__init__.py` |
| `hk_funds/manager_connectors/base.py` | `/opt/eco-data/hk_funds/manager_connectors/base.py` |
| `hk_funds/manager_connectors/blackrock.py` | `/opt/eco-data/hk_funds/manager_connectors/blackrock.py` |
| `hk_funds/manager_connectors/csop.py` | `/opt/eco-data/hk_funds/manager_connectors/csop.py` |
| `hk_funds/manager_connectors/chinaamc.py` | `/opt/eco-data/hk_funds/manager_connectors/chinaamc.py` |
| `hk_funds/manager_connectors/mirae.py` | `/opt/eco-data/hk_funds/manager_connectors/mirae.py` |
| `hk_funds/manager_connectors/hangseng.py` | `/opt/eco-data/hk_funds/manager_connectors/hangseng.py` |
| `hk_funds/manager_connectors/efund.py` | `/opt/eco-data/hk_funds/manager_connectors/efund.py` |
| `hk_funds/manager_connectors/bosera.py` | `/opt/eco-data/hk_funds/manager_connectors/bosera.py` |
| `hk_funds/manager_connectors/harvest.py` | `/opt/eco-data/hk_funds/manager_connectors/harvest.py` |
| `hk_funds/manager_connectors/icbc.py` | `/opt/eco-data/hk_funds/manager_connectors/icbc.py` |
| `hk_funds/manager_connectors/invesco.py` | `/opt/eco-data/hk_funds/manager_connectors/invesco.py` |
| `hk_funds/manager_connectors/amundi.py` | `/opt/eco-data/hk_funds/manager_connectors/amundi.py` |
| `hk_funds/manager_connectors/statestreet.py` | `/opt/eco-data/hk_funds/manager_connectors/statestreet.py` |
| `hk_funds/manager_connectors/value_partners.py` | `/opt/eco-data/hk_funds/manager_connectors/value_partners.py` |
| `hk_funds/manager_connectors/samsung.py` | `/opt/eco-data/hk_funds/manager_connectors/samsung.py` |
| `hk_funds/manager_connectors/premia.py` | `/opt/eco-data/hk_funds/manager_connectors/premia.py` |
| `hk_funds/manager_connectors/hkex_etf.py` | `/opt/eco-data/hk_funds/manager_connectors/hkex_etf.py` |
| `hk_funds/manager_connectors/hsbc.py` | `/opt/eco-data/hk_funds/manager_connectors/hsbc.py` |
| `hk_funds/manager_connectors/jpmorgan.py` | `/opt/eco-data/hk_funds/manager_connectors/jpmorgan.py` |
| `hk_funds/manager_connectors/fidelity.py` | `/opt/eco-data/hk_funds/manager_connectors/fidelity.py` |
| `hk_funds/manager_connectors/schroders.py` | `/opt/eco-data/hk_funds/manager_connectors/schroders.py` |
| `hk_funds/manager_connectors/bnp_paribas.py` | `/opt/eco-data/hk_funds/manager_connectors/bnp_paribas.py` |
| `hk_funds/manager_connectors/ubs.py` | `/opt/eco-data/hk_funds/manager_connectors/ubs.py` |
| `hk_funds/manager_connectors/eastspring.py` | `/opt/eco-data/hk_funds/manager_connectors/eastspring.py` |
| `hk_funds/manager_connectors/pinebridge.py` | `/opt/eco-data/hk_funds/manager_connectors/pinebridge.py` |
| `hk_funds/manager_connectors/principal.py` | `/opt/eco-data/hk_funds/manager_connectors/principal.py` |
| `hk_funds/manager_connectors/franklin_templeton.py` | `/opt/eco-data/hk_funds/manager_connectors/franklin_templeton.py` |
| `hk_funds/manager_connectors/allianz.py` | `/opt/eco-data/hk_funds/manager_connectors/allianz.py` |
| `hk_funds/manager_connectors/alliance_bernstein.py` | `/opt/eco-data/hk_funds/manager_connectors/alliance_bernstein.py` |
| `hk_funds/manager_connectors/abrdn.py` | `/opt/eco-data/hk_funds/manager_connectors/abrdn.py` |
| `hk_funds/manager_connectors/capital_group.py` | `/opt/eco-data/hk_funds/manager_connectors/capital_group.py` |
| `hk_funds/manager_connectors/fubon.py` | `/opt/eco-data/hk_funds/manager_connectors/fubon.py` |
| `hk_funds/manager_connectors/pingan.py` | `/opt/eco-data/hk_funds/manager_connectors/pingan.py` |
| `hk_funds/manager_connectors/taikang.py` | `/opt/eco-data/hk_funds/manager_connectors/taikang.py` |
| `hk_funds/manager_connectors/huaan.py` | `/opt/eco-data/hk_funds/manager_connectors/huaan.py` |
| `hk_funds/manager_connectors/fullgoal.py` | `/opt/eco-data/hk_funds/manager_connectors/fullgoal.py` |
| `hk_funds/manager_connectors/dacheng.py` | `/opt/eco-data/hk_funds/manager_connectors/dacheng.py` |
| `hk_funds/manager_connectors/cinda.py` | `/opt/eco-data/hk_funds/manager_connectors/cinda.py` |
| `hk_funds/manager_connectors/gf_international.py` | `/opt/eco-data/hk_funds/manager_connectors/gf_international.py` |
| `hk_funds/manager_connectors/china_universal.py` | `/opt/eco-data/hk_funds/manager_connectors/china_universal.py` |
| `hk_funds/manager_connectors/china_life_franklin.py` | `/opt/eco-data/hk_funds/manager_connectors/china_life_franklin.py` |
| `hk_funds/manager_connectors/cmbi.py` | `/opt/eco-data/hk_funds/manager_connectors/cmbi.py` |
| `hk_funds/manager_connectors/cms.py` | `/opt/eco-data/hk_funds/manager_connectors/cms.py` |
| `hk_funds/manager_connectors/boci_prudential.py` | `/opt/eco-data/hk_funds/manager_connectors/boci_prudential.py` |
| `hk_funds/manager_connectors/clsa.py` | `/opt/eco-data/hk_funds/manager_connectors/clsa.py` |
| `hk_funds/manager_connectors/kgi.py` | `/opt/eco-data/hk_funds/manager_connectors/kgi.py` |
| `hk_funds/manager_connectors/bea_union.py` | `/opt/eco-data/hk_funds/manager_connectors/bea_union.py` |
| `hk_funds/manager_connectors/income_partners.py` | `/opt/eco-data/hk_funds/manager_connectors/income_partners.py` |
| `hk_funds/manager_connectors/ninety_one.py` | `/opt/eco-data/hk_funds/manager_connectors/ninety_one.py` |
| `hk_funds/manager_connectors/pickers.py` | `/opt/eco-data/hk_funds/manager_connectors/pickers.py` |
| `hk_funds/manager_connectors/asset_mg.py` | `/opt/eco-data/hk_funds/manager_connectors/asset_mg.py` |

### SDK + MCP + 前端

| 本地路径 | 服务器路径 |
|----------|-----------|
| `eco_data_sdk/hk_funds_client.py` | `/opt/eco-data/eco_data_sdk/hk_funds_client.py` |
| `eco_data_sdk/__init__.py` | `/opt/eco-data/eco_data_sdk/__init__.py` |
| `mcp/eco_data_server.py` | `/opt/eco-data/mcp/eco_data_server.py` |
| `frontend/src/lib/api.ts` | `/opt/eco-data/frontend/src/lib/api.ts` |
| `frontend/src/components/NavBar.tsx` | `/opt/eco-data/frontend/src/components/NavBar.tsx` |
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
# 后端核心 (18个文件)
# ═══════════════════════════════════════════

cp "/Users/a80460/Desktop/Sanakan Datahub/hk_funds/__init__.py" hk_funds/__init__.py
cp "/Users/a80460/Desktop/Sanakan Datahub/hk_funds/config.py" hk_funds/config.py
cp "/Users/a80460/Desktop/Sanakan Datahub/hk_funds/storage.py" hk_funds/storage.py
cp "/Users/a80460/Desktop/Sanakan Datahub/hk_funds/api.py" hk_funds/api.py
cp "/Users/a80460/Desktop/Sanakan Datahub/hk_funds/pipeline_funds.py" hk_funds/pipeline_funds.py
cp "/Users/a80460/Desktop/Sanakan Datahub/hk_funds/pipeline_managers.py" hk_funds/pipeline_managers.py
cp "/Users/a80460/Desktop/Sanakan Datahub/hk_funds/pipeline_ofc.py" hk_funds/pipeline_ofc.py
cp "/Users/a80460/Desktop/Sanakan Datahub/hk_funds/pipeline_manager_scrape.py" hk_funds/pipeline_manager_scrape.py
cp "/Users/a80460/Desktop/Sanakan Datahub/hk_funds/pipeline_hkex_import.py" hk_funds/pipeline_hkex_import.py
cp "/Users/a80460/Desktop/Sanakan Datahub/hk_funds/hkex_pipeline.py" hk_funds/hkex_pipeline.py
cp "/Users/a80460/Desktop/Sanakan Datahub/hk_funds/pipeline_webb_scraper.py" hk_funds/pipeline_webb_scraper.py
cp "/Users/a80460/Desktop/Sanakan Datahub/hk_funds/risk_rating.py" hk_funds/risk_rating.py
cp "/Users/a80460/Desktop/Sanakan Datahub/hk_funds/rating_engine.py" hk_funds/rating_engine.py
cp "/Users/a80460/Desktop/Sanakan Datahub/hk_funds/standard_templates.py" hk_funds/standard_templates.py
cp "/Users/a80460/Desktop/Sanakan Datahub/hk_funds/manager_scoring.py" hk_funds/manager_scoring.py
cp "/Users/a80460/Desktop/Sanakan Datahub/hk_funds/kfs_parser.py" hk_funds/kfs_parser.py
cp "/Users/a80460/Desktop/Sanakan Datahub/hk_funds/isin_sources.py" hk_funds/isin_sources.py
cp "/Users/a80460/Desktop/Sanakan Datahub/hk_funds/scheduler.py" hk_funds/scheduler.py
cp "/Users/a80460/Desktop/Sanakan Datahub/hk_funds/pipeline_wings.py" hk_funds/pipeline_wings.py

# ═══════════════════════════════════════════
# 管理人爬虫 (50+ connector)
# ═══════════════════════════════════════════

cp "/Users/a80460/Desktop/Sanakan Datahub/hk_funds/manager_connectors/"*.py hk_funds/manager_connectors/

# ═══════════════════════════════════════════
# SDK + MCP + 前端
# ═══════════════════════════════════════════

cp "/Users/a80460/Desktop/Sanakan Datahub/eco_data_sdk/hk_funds_client.py" eco_data_sdk/hk_funds_client.py
cp "/Users/a80460/Desktop/Sanakan Datahub/eco_data_sdk/__init__.py" eco_data_sdk/__init__.py
cp "/Users/a80460/Desktop/Sanakan Datahub/mcp/eco_data_server.py" mcp/eco_data_server.py
cp "/Users/a80460/Desktop/Sanakan Datahub/frontend/src/lib/api.ts" frontend/src/lib/api.ts
cp "/Users/a80460/Desktop/Sanakan Datahub/frontend/src/components/NavBar.tsx" frontend/src/components/NavBar.tsx
cp "/Users/a80460/Desktop/Sanakan Datahub/frontend/src/app/hk-funds/"*.tsx frontend/src/app/hk-funds/
```

---

## Step 2 — 安装新增依赖

```bash
python3 -m pip install pypdf pdfplumber apscheduler
```

---

## Step 3 — 重启 API（自动跑 V3 迁移）

```bash
systemctl restart hk-funds-api
```

启动时 `init_db()` 会自动建 V3 新增的表：
- `hk_rating_templates` — 评级模板
- `hk_rating_template_factors` — 模板因子
- `hk_rating_factor_configs` — 因子配置
- `hk_user_ratings` — 用户评级结果
- `hk_manager_dd` — 管理人10维度尽调
- `hk_kyp_dimensions` — 基金10维度KYP
- `hk_kyp_assessment_log` — KYP评估日志
- `hk_fund_risk_ratings` — 风险评级
- `hk_non_authorized_funds` — 非认可基金
- `hk_fund_nav_history` — NAV历史
- `hk_fund_performance` — 业绩指标
- `hk_fund_holdings` — 持仓
- `hk_fund_dividends` — 分红
- `hk_fund_share_classes` — 份额类别
- `hk_fund_portfolio_managers` — 基金经理

验证：
```bash
curl http://127.0.0.1:8004/api/v1/health | python3 -m json.tool
```

---

## Step 4 — 从 WINGS 抓取管理人数据（无需登录）

WINGS 公开 API 无需任何认证，直接抓取全部持牌法团：

```bash
cd /opt/eco-data
python3 -m hk_funds.pipeline_wings --link
```

> `--link` 参数会同时做基金-管理人名称匹配关联。

预计耗时 2-3 分钟，抓取约 3,500+ 管理人。验证：

```bash
curl http://127.0.0.1:8004/api/v1/managers/stats | python3 -m json.tool
# {"total": 3593, "type9_count": 2478, ...}
```

---

## Step 5 — 初始化标准模板

重启后需要手动插入 4 套标准评级模板：

```bash
cd /opt/eco-data

python3 -c "
from hk_funds.standard_templates import seed_standard_templates
from hk_funds.storage import init_db
conn = init_db()
seed_standard_templates(conn)
conn.close()
print('4 standard templates seeded.')
"
```

验证模板已插入：
```bash
curl http://127.0.0.1:8004/api/v1/templates | python3 -m json.tool
# 应该看到 template_type: fund_risk 2个, manager_dd 2个
```

---

## Step 6 — 初始化风险评级 + KYP + 管理人DD

```bash
cd /opt/eco-data

# 初始化所有基金的风险评级 (使用默认 SFC 6-Factor 模板)
curl -X POST http://127.0.0.1:8004/api/v1/risk-ratings/calculate

# 初始化所有基金的 KYP 10维度评估
curl -X POST http://127.0.0.1:8004/api/v1/kyp/init-all 2>/dev/null || \
  echo "try individual init: curl -X POST http://127.0.0.1:8004/api/v1/funds/{id}/kyp/init"

# 初始化管理人 DD (对所有有基金的管理人)
python3 -c "
from hk_funds.storage import init_db, init_manager_dd
conn = init_db()
managers = conn.execute('SELECT DISTINCT fund_manager_id FROM hk_funds WHERE fund_manager_id IS NOT NULL').fetchall()
for (mid,) in managers:
    try:
        init_manager_dd(conn, mid)
    except Exception:
        pass
print(f'DD initialized for {len(managers)} managers.')
conn.close()
"
```

---

## Step 7 — 重新构建前端

```bash
cd /opt/eco-data/frontend
npm run build
systemctl restart zt-frontend
```

---

## Step 8 — 重启 MCP Server

如果你在 `.mcp.json` 或 `~/.claude.json` 中配置了 MCP，重启 Claude Code 即可加载新增的 10 个 HK MCP 工具。

---

## 新增 API 端点速查

### 评级模板系统 (6 endpoints)

```
GET    /api/v1/templates                          # 列出模板 (可选 ?template_type=fund_risk|manager_dd)
GET    /api/v1/templates/{template_id}            # 查看模板详情 (含所有因子)
POST   /api/v1/templates/clone                    # 从系统模板克隆为用户模板 (body: {source_template_id, user_id})
PUT    /api/v1/templates/{template_id}            # 修改用户模板的因子权重/阈值
DELETE /api/v1/templates/{template_id}            # 删除用户模板
POST   /api/v1/templates/{template_id}/compute    # 用模板计算评级 (body: {user_id, target_type})
GET    /api/v1/templates/{template_id}/results    # 查看评级结果 (params: user_id, target_type)
```

### 基金详情扩展 (4 endpoints)

```
GET    /api/v1/funds/{fund_id}/holdings           # 持仓
GET    /api/v1/funds/{fund_id}/dividends          # 分红历史
GET    /api/v1/funds/{fund_id}/share-classes      # 份额类别
GET    /api/v1/funds/{fund_id}/risk-rating        # 风险评级详情
```

### 管理人扩展 (2 endpoints)

```
GET    /api/v1/managers/scrape/status             # 爬虫注册状态
POST   /api/v1/managers/scrape                    # 触发管理人爬取 (body: {ce_number})
```

### 非认可基金 (2 endpoints)

```
GET    /api/v1/non-authorized-funds               # 非认可基金列表
POST   /api/v1/non-authorized-funds               # 添加非认可基金
```

### 基金管理人 (1 endpoint)

```
GET    /api/v1/funds/{fund_id}/portfolio-manager  # 查看/搜索基金经理
```

---

## MCP 工具速查（共 27 个 HK 工具，新增 10 个）

### 新增 V3 工具

| 工具 | 说明 |
|------|------|
| `hk_rating_template_list` | 列出评级模板（系统/用户） |
| `hk_rating_template_get` | 查看模板详情+因子 |
| `hk_rating_template_clone` | 克隆模板 |
| `hk_rating_template_update` | 更新用户模板权重/阈值 |
| `hk_rating_compute` | 用模板计算评级 |
| `hk_rating_results` | 查看评级结果分布 |
| `hk_fund_holdings` | 查看基金持仓 |
| `hk_fund_dividends` | 查看分红历史 |
| `hk_fund_share_classes` | 查看份额类别 |
| `hk_fund_portfolio_manager` | 查看/搜索基金经理 |
| `hk_fund_isins` | 列出有ISIN的基金(分页) |
| `hk_fund_latest_nav` | 最新NAV |
| `hk_fund_nav_history` | NAV时间序列 |
| `hk_fund_performance` | 业绩指标 |
| `hk_non_authorized_funds` | 非认可基金列表 |

### 已有 V2 工具（保持不变）

`hk_fund_stats`, `hk_fund_search`, `hk_fund_isin_lookup`, `hk_fund_risk_ratings`, `hk_kyp_dimensions`, `hk_kyp_gaps`, `hk_complex_products`, `hk_derivative_products`, `hk_ofc_stats`, `hk_fund_managers`, `hk_manager_dd`, `hk_manager_scrape_status`

---

## 使用示例

### 方式 1：直接调 REST API

```python
import requests
API = "http://127.0.0.1:8004"

# ── 查看所有标准模板 ──
r = requests.get(f"{API}/api/v1/templates")
for t in r.json()["templates"]:
    print(f"{t['id']}: {t['name']} ({t['template_type']})")

# ── 获取 SFC 6-Factor 模板详情 ──
r = requests.get(f"{API}/api/v1/templates/1")
tmpl = r.json()
for f in tmpl["factors"]:
    print(f"  {f['factor_key']}: weight={f['weight']}")

# ── 克隆模板并修改权重 ──
r = requests.post(f"{API}/api/v1/templates/clone", json={
    "source_template_id": 1,
    "user_id": "my_team",
    "new_name": "My Custom 6-Factor"
})
my_tmpl = r.json()
print(f"Cloned template ID: {my_tmpl['id']}")

# 修改权重
requests.put(f"{API}/api/v1/templates/{my_tmpl['id']}", json={
    "user_id": "my_team",
    "factor_weights": {"complexity": 0.30, "leverage": 0.25, "liquidity": 0.20},
    "category_thresholds": [
        {"max": 1.5, "label": "Low"},
        {"max": 2.5, "label": "Medium-Low"},
        {"max": 3.5, "label": "Medium"},
        {"max": 4.5, "label": "Medium-High"},
        {"max": 99.0, "label": "High"},
    ]
})

# ── 用你的模板计算评级 ──
r = requests.post(f"{API}/api/v1/templates/{my_tmpl['id']}/compute", json={
    "user_id": "my_team",
    "target_type": "fund"
})
print(r.json())  # {computed_count: 2021, ...}

# ── 看评级结果分布 ──
r = requests.get(f"{API}/api/v1/templates/{my_tmpl['id']}/results", params={
    "user_id": "my_team",
    "target_type": "fund"
})
for cat, funds in r.json()["categories"].items():
    print(f"  {cat}: {len(funds)} funds")

# ── 查某基金的持仓 + 分红 ──
r = requests.get(f"{API}/api/v1/funds/1/holdings")
print(r.json())

r = requests.get(f"{API}/api/v1/funds/1/dividends")
print(r.json())

# ── 查管理人爬虫状态 ──
r = requests.get(f"{API}/api/v1/managers/scrape/status")
print(r.json())
# {registered_connectors: 52, top_uncovered: [...], ...}
```

### 方式 2：SDK (Python)

```python
from eco_data_sdk.hk_funds_client import HkFundsClient

client = HkFundsClient(base_url="http://localhost:8004")

# 模板操作
templates = client.list_templates(template_type="fund_risk")
tmpl = client.get_template(template_id=1)
cloned = client.clone_template(source_template_id=1, user_id="my_team")
client.update_template(template_id=cloned["id"], user_id="my_team",
                       factor_weights={"complexity": 0.30})
client.compute_ratings(template_id=cloned["id"], user_id="my_team")
results = client.get_rating_results(template_id=cloned["id"], user_id="my_team")

# 基金数据
holdings = client.get_holdings(fund_id=1)
dividends = client.get_dividends(fund_id=1)
shares = client.get_share_classes(fund_id=1)
pm = client.get_portfolio_manager(fund_id=1)

# 管理人
status = client.scrape_status()
client.scrape_manager(ce_number="ARN075")  # 触发 CSOP 爬取
```

### 方式 3：MCP 工具（Claude Code 中直接调用）

```
查模板列表       → hk_rating_template_list
看 SFC 6-Factor  → hk_rating_template_get template_id: 1
克隆一个模板     → hk_rating_template_clone source_template_id: 1 user_id: "my_team"
查看评级结果     → hk_rating_results template_id: 1 user_id: "my_team"
查基金持仓       → hk_fund_holdings fund_id: 1
查基金经理       → hk_fund_portfolio_manager fund_id: 1
爬虫覆盖状态     → hk_manager_scrape_status
```

### 方式 4：直接读 DuckDB

```python
import duckdb
conn = duckdb.connect("/opt/eco-data/hk_funds/hk_funds.duckdb", read_only=True)

# 看所有评级模板
conn.execute("SELECT id, name, template_type FROM hk_rating_templates").fetchall()

# 看某用户的评级结果
conn.execute("""
    SELECT f.fund_name_en, r.factor_scores, r.overall_score, r.category_label
    FROM hk_user_ratings r
    JOIN hk_funds f ON r.target_id = f.id
    WHERE r.template_id = 1 AND r.user_id = 'my_team'
    ORDER BY r.overall_score DESC
    LIMIT 20
""").fetchdf()

# 看哪些管理人还没 ISIN 爬虫覆盖
conn.execute("""
    SELECT m.company_name_en, COUNT(f.id) as fund_count
    FROM hk_fund_managers m
    JOIN hk_funds f ON f.fund_manager_id = m.id
    WHERE f.isin IS NULL
    GROUP BY m.id, m.company_name_en
    ORDER BY fund_count DESC
    LIMIT 20
""").fetchdf()
```

---

## 数据库最终状态

| 表 | 说明 |
|----|------|
| `hk_funds` | SFC 认可基金 (2,021 只) |
| `hk_fund_classifications` | 双层分类详情 (六因素) |
| `hk_fund_managers` | SFC 持牌法团 (3,543) |
| `hk_fund_manager_funds` | 基金-管理人 M:N |
| `hk_manager_regulatory_history` | 管理人执法记录 |
| `hk_fund_documents` | 基金销售文件 |
| `hkex_listed_funds` | HKEX 上市基金 |
| `hk_fetch_log` | 数据抓取审计 |
| `hk_kyp_dimensions` | 10维度 KYP 评估 |
| `hk_kyp_assessment_log` | KYP 评估日志 |
| `hk_fund_risk_ratings` | 风险评级 |
| `hk_manager_dd` | 管理人10维度尽调 |
| `hk_non_authorized_funds` | 非认可基金 (2,231 OFC) |
| `hk_fund_nav_history` | NAV 时间序列 |
| `hk_fund_performance` | 业绩指标 |
| `hk_fund_holdings` | 持仓数据 |
| `hk_fund_dividends` | 分红历史 |
| `hk_fund_share_classes` | 份额类别 |
| `hk_fund_portfolio_managers` | 基金经理信息 |
| `hk_rating_templates` | 评级模板 (4系统) |
| `hk_rating_template_factors` | 模板因子 |
| `hk_rating_factor_configs` | 因子配置 |
| `hk_user_ratings` | 用户评级结果 |

---

## 定时任务 (Scheduler)

```bash
# 查看 scheduler 是否在跑
systemctl status hk-funds-scheduler

# 手动跑一次每日任务
cd /opt/eco-data && python3 -m hk_funds.scheduler
```

4 个 cron job (HKT 时区)：

| 时间 | 内容 |
|------|------|
| 周一 11:07 | SFC 基金清单刷新 + 分类 |
| 周一 11:37 | SFC 持牌法团刷新 + 关联 + 执法 |
| 周一 12:07 | 重新分类 + 重新关联 |
| 每日 09:37 | 管理人执法交叉检查 |
