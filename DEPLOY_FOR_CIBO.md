# HK Fund 自定义评级系统 — 部署与使用 for Cibo

其他服务 Cibo 已经部署过了，这里只说本次新增内容和怎么用。

---

## 新增/修改的文件清单

```
/Users/a80460/Desktop/Sanakan Datahub/
│
├── hk_funds/
│   ├── rating_engine.py          [NEW]  通用模板评分引擎
│   ├── standard_templates.py     [NEW]  4套内置评分模板
│   ├── storage.py                [MOD]  新增3张表 + 12个CRUD函数
│   └── api.py                    [MOD]  新增7个REST端点
│
├── mcp/
│   └── eco_data_server.py        [MOD]  新增6个MCP tool + _hk_conn_rw()写连接
│
├── eco_data_sdk/
│   └── hk_funds_client.py        [MOD]  新增8个SDK方法
│
├── frontend/src/
│   ├── lib/api.ts                [MOD]  新增TypeScript类型 + 8个fetch函数
│   └── app/hk-funds/
│       ├── TemplateEditor.tsx     [NEW]  模板编辑器组件
│       ├── RatingResults.tsx      [NEW]  评级结果组件
│       └── HkFundsContent.tsx     [MOD]  新增"模板编辑"/"我的评级"tab
│
└── hk_funds.duckdb               [REQ]  数据库文件，必须可读写
```

---

## 一、启动方式

```bash
cd "/Users/a80460/Desktop/Sanakan Datahub"
python3 -m uvicorn hk_funds.api:app --host 127.0.0.1 --port 8004
```

首次启动会自动调用 `init_db()` → 建表 + 创建4套内置模板。数据库文件路径：

```
/Users/a80460/Desktop/Sanakan Datahub/hk_funds.duckdb
```

**⚠️ 这个文件必须可读写。** 评级模板的克隆、权重修改、评分结果存储都需要写权限。MCP server 中新增了 `_hk_conn_rw()` 函数（在 `/Users/a80460/Desktop/Sanakan Datahub/mcp/eco_data_server.py` 第309行），用于需要写操作的MCP tool。

---

## 二、4套内置模板

| 模板 ID | 名称 | 类型 | 因子 | 评级档位 |
|---------|------|------|------|---------|
| 2 | SFC 6-Factor (v1.0) | fund_risk | 6 | Low / Medium-Low / Medium / Medium-High / High |
| 3 | Broker 13-Factor Scorecard (v1.0) | fund_risk | 13 | R1 / R2 / R3 / R4 / R5 |
| 4 | 10-Dimension DD (v1.0) | manager_dd | 10 | Strong / Sufficient / Average / Limited / Lacking |
| 6 | Broker 18-Indicator DD (v1.0) | manager_dd | 18 | F1 / F2 / F3 / F4 / F5 |

### SFC 6-Factor (id=2) — 6因子加权评分

| 因子 | 权重 | 评分逻辑 |
|------|------|---------|
| complexity | 25% | lookup: non_complex→1, L&I→5, hedge_fund→5... |
| underlying_risk | 25% | lookup+keyword: money_market→1, equity→4, commodity→5... |
| leverage | 15% | lookup+keyword: L&I→5, leveraged→5, inverse→5... |
| liquidity | 15% | range: lockup_period_days bands + type_boost |
| credit_quality | 10% | keyword: AAA→1, BB→4, high yield→4... |
| currency_country | 10% | lookup: Hong Kong→1, China→3, emerging→4... |

阈值: ≤1.5→Low, ≤2.5→Medium-Low, ≤3.5→Medium, ≤4.0→Medium-High, >4.0→High

### Broker 13-Factor (id=3) — 三大部分13因子

**Fund House (30%)**
| 因子 | 权重 | 评分 |
|------|------|------|
| manager_size | 16% | manager_aum_range: >$1T→1, <$1B→5 |
| internal_control | 14% | manager_dd_score: Strong→1, Lacking→5 |

**Fund Fundamental (40%)**
| fund_size | 5% | range: >$1B→1, <$1M→5 |
| expense_ratio | 3% | range: <0.5%→1, >2.5%→5 |
| portfolio_managers | 7% | has_portfolio_manager: yes→2, no→5 |
| fund_type | 15% | lookup: money_market→1, equity→4, sector_equity→5 |
| concentration | 10% | keyword: diversified→1, single country→5 |

**Performance (30%)**
| sharpe_ratio | 7% | range: ≥1.5→1, <0→5 |
| std_dev | 6% | range: ≤5→1, >25→5 |
| max_drawdown | 5% | range: ≤10%→1, >50%→5 |
| sharpe_vs_bm | 5% | benchmark_diff: ≥+0.5→1, <-1.0→5 |
| stddev_vs_bm | 4% | benchmark_diff: ≤-5→1, >+15→5 |
| maxdd_vs_bm | 3% | benchmark_diff: ≤-5→1, >+20→5 |

阈值: <1.8→R1, 1.8-2.3→R2, 2.3-2.9→R3, 2.9-3.3→R4, >3.3→R5

### 10-Dimension DD (id=4) — 10维度 pass/fail 计数

10个DD维度各评分1-5，≥3分且状态为 reviewed/approved 视为 pass。按通过数映射：

9-10 pass → Strong(1) / 7-8 → Sufficient(2) / 5-6 → Average(3) / 3-4 → Limited(4) / 0-2 → Lacking(5)

### Broker 18-Indicator DD (id=6) — 18指标加权

| 指标 | 权重 | 数据源 |
|------|------|--------|
| 成立年限 | 5% | hk_fund_managers.establishment_date |
| 股东实力 | 5% | hk_manager_dd.financial_resources |
| 实收资本 | 2% | hk_fund_managers.shareholder_equity_hkd |
| 监管处罚 | 8% | hk_manager_regulatory_history |
| 员工人数 | 3% | hk_fund_managers.employee_count |
| 基金经理年限 | 5% | hk_funds.portfolio_manager_name |
| 内部治理 | 2% | DD: internal_controls |
| 风控体系 | 8% | DD: risk_governance |
| 投研实力 | 5% | DD: human_resources |
| 管理规模 | 12% | hk_fund_manager_aum |
| 规模增长率 | 5% | hk_fund_manager_aum |
| 盈利产品比例 | 10% | manual |
| 管理层变动 | 5% | manual |
| 累计基金数 | 5% | hk_funds per manager count |
| 机构声誉 | 8% | manual |
| 合作渠道 | 7% | manual |
| 投资者构成 | 3% | manual |
| 投资者服务 | 2% | manual |

阈值: ≤1.5→F1, ≤2.5→F2, ≤3.5→F3, ≤4.0→F4, >4.0→F5

---

## 三、REST API

所有端点 base URL: `http://127.0.0.1:8004`

### 列出模板

```bash
# 系统模板
curl "http://127.0.0.1:8004/api/v1/templates?user_id=system"

# 某用户的模板
curl "http://127.0.0.1:8004/api/v1/templates?user_id=cibo_demo"

# 只列出基金风险类型
curl "http://127.0.0.1:8004/api/v1/templates?user_id=system&template_type=fund_risk"

# 只列出管理人尽调类型
curl "http://127.0.0.1:8004/api/v1/templates?user_id=system&template_type=manager_dd"
```

返回示例:
```json
{
  "count": 4,
  "templates": [
    {
      "id": 2,
      "user_id": "system",
      "name": "SFC 6-Factor (v1.0)",
      "description": "Default SFC-aligned 6-factor weighted scoring...",
      "template_type": "fund_risk",
      "is_system": true,
      "methodology_version": "1.0",
      "factor_count": 6,
      "created_at": "2026-06-22 14:30:00",
      "updated_at": "2026-06-22 14:30:00"
    }
  ]
}
```

### 获取模板详情（含所有因子配置和阈值）

```bash
curl "http://127.0.0.1:8004/api/v1/templates/2"
```

返回:
```json
{
  "id": 2,
  "user_id": "system",
  "name": "SFC 6-Factor (v1.0)",
  "description": "...",
  "template_type": "fund_risk",
  "methodology_version": "1.0",
  "is_system": true,
  "category_thresholds": [
    {"max": 1.5, "label": "Low"},
    {"max": 2.5, "label": "Medium-Low"},
    {"max": 3.5, "label": "Medium"},
    {"max": 4.0, "label": "Medium-High"},
    {"max": 99.0, "label": "High"}
  ],
  "factors": [
    {
      "factor_key": "complexity",
      "factor_label": "Product Complexity",
      "weight": 0.25,
      "ordinal": 1,
      "config": {
        "type": "lookup",
        "field": "complex_product_type",
        "score_map": {"non_complex": 1, "L&I": 5, "hedge_fund": 5, ...},
        "default_score": 1
      }
    }
  ]
}
```

### 克隆模板

```bash
curl -X POST "http://127.0.0.1:8004/api/v1/templates/clone?source_template_id=2&user_id=cibo_demo&new_name=Cibo%E4%B8%93%E5%B1%9E%E8%AF%84%E7%BA%A7"
```

返回:
```json
{
  "cloned_template_id": 7,
  "template": {
    "id": 7,
    "name": "Cibo专属评级",
    "template_type": "fund_risk",
    "factor_count": 6
  }
}
```

### 修改自定义模板

```bash
curl -X PUT "http://127.0.0.1:8004/api/v1/templates/7" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "cibo_demo",
    "name": "Cibo 高复杂度版",
    "factor_weights": {
      "complexity": 0.40,
      "underlying_risk": 0.20,
      "leverage": 0.15,
      "liquidity": 0.12,
      "credit_quality": 0.08,
      "currency_country": 0.05
    },
    "category_thresholds": [
      {"max": 1.5, "label": "Low"},
      {"max": 2.5, "label": "Medium-Low"},
      {"max": 3.5, "label": "Medium"},
      {"max": 4.5, "label": "Medium-High"},
      {"max": 99, "label": "High"}
    ]
  }'
```

- `factor_weights` 的 key 必须和模板的 factor_key 一致
- `category_thresholds` 的 max 必须从小到大排列，最后一个给一个很大的数兜底
- `name` 和 `description` 可选
- 系统模板 (is_system=true) 不能修改

### 删除自定义模板

```bash
curl -X DELETE "http://127.0.0.1:8004/api/v1/templates/7"
```

系统模板不可删除，返回 403。

### 计算评级

```bash
# 对全部2021只活跃基金批量计算
curl -X POST "http://127.0.0.1:8004/api/v1/templates/7/compute" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "cibo_demo", "target_type": "fund"}'

# 对单个基金计算
curl -X POST "http://127.0.0.1:8004/api/v1/templates/7/compute" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "cibo_demo", "target_type": "fund", "target_id": 4283}'

# 对全部3543家管理人计算尽调评级 (用18I模板 id=6)
curl -X POST "http://127.0.0.1:8004/api/v1/templates/6/compute" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "cibo_demo", "target_type": "manager"}'
```

批量计算返回值:
```json
{
  "template_id": 7,
  "template_name": "Cibo专属评级",
  "total_rated": 2021,
  "distribution": [
    {"category": "Medium-Low", "count": 2008},
    {"category": "Medium", "count": 13}
  ],
  "errors": []
}
```

### 查看评级结果

```bash
curl "http://127.0.0.1:8004/api/v1/templates/7/results?user_id=cibo_demo&target_type=fund&limit=50"
```

返回:
```json
{
  "template_id": 7,
  "user_id": "cibo_demo",
  "target_type": "fund",
  "total_rated": 2021,
  "distribution": [
    {"category": "Medium-Low", "count": 2008},
    {"category": "Medium", "count": 13}
  ],
  "results": [
    {
      "target_id": 4283,
      "target_name": "AI Innovation Fund",
      "overall_score": 2.05,
      "category": "Medium-Low",
      "factor_count": 6,
      "computed_at": "2026-06-22 15:00:00"
    }
  ]
}
```

---

## 四、Python SDK

文件位置: `/Users/a80460/Desktop/Sanakan Datahub/eco_data_sdk/hk_funds_client.py`

```python
import sys
sys.path.insert(0, "/Users/a80460/Desktop/Sanakan Datahub")

from eco_data_sdk.hk_funds_client import HkFundsClient

client = HkFundsClient("http://127.0.0.1:8004")

# ── 第一步：列出系统模板 ──
templates = client.list_templates(user_id="system")
for t in templates["templates"]:
    print(f"  [{t['id']}] {t['name']}  ({t['template_type']})  {t['factor_count']}因子")

# ── 第二步：获取模板详情 ──
detail = client.get_template(template_id=2)
print(f"\n模板: {detail['name']}")
for f in detail["factors"]:
    print(f"  {f['factor_key']}: weight={f['weight']}  type={f['config'].get('type')}")

print(f"\n阈值: {detail['category_thresholds']}")

# ── 第三步：克隆模板 ──
clone = client.clone_template(
    source_template_id=2,
    user_id="cibo_demo",
    new_name="Cibo 自定义评分"
)
new_id = clone["cloned_template_id"]
print(f"\n克隆成功: 新模板 id={new_id}")

# ── 第四步：修改因子权重 ──
client.update_template(new_id, {
    "user_id": "cibo_demo",
    "factor_weights": {
        "complexity": 0.40,
        "underlying_risk": 0.20,
        "leverage": 0.15,
        "liquidity": 0.12,
        "credit_quality": 0.08,
        "currency_country": 0.05,
    },
})
print("权重已更新")

# ── 第五步：批量计算全部基金 ──
result = client.compute_ratings(new_id, "cibo_demo", target_type="fund")
print(f"\n评级完成: {result['total_rated']} 只基金")
print(f"分布: {result['distribution']}")

# ── 第六步：查看评级结果 ──
results = client.get_rating_results(new_id, "cibo_demo", target_type="fund", limit=10)
print(f"\n前10只:")
for r in results["results"]:
    print(f"  {r['target_name'][:50]:50s}  {r['overall_score']:5.2f}  {r['category']}")

# ── 清理 ──
client.close()
```

完整 SDK 方法列表：

| 方法 | 签名 |
|------|------|
| `list_templates` | `(user_id="system", template_type=None) -> dict` |
| `get_template` | `(template_id: int) -> dict` |
| `clone_template` | `(source_template_id, user_id, new_name="") -> dict` |
| `update_template` | `(template_id, body: dict) -> dict` |
| `delete_template` | `(template_id: int) -> dict` |
| `compute_ratings` | `(template_id, user_id, target_type="fund", target_id=0) -> dict` |
| `get_rating_results` | `(template_id, user_id, target_type="fund", limit=100) -> dict` |

---

## 五、MCP Tools

MCP server 文件: `/Users/a80460/Desktop/Sanakan Datahub/mcp/eco_data_server.py`

新增了6个 tool，AI agent 可以直接调用：

| Tool 名称 | 参数 | 功能 |
|-----------|------|------|
| `hk_rating_template_list` | `user_id` (str), `template_type`? (str) | 列出所有模板 |
| `hk_rating_template_get` | `template_id` (int) | 获取模板详情+因子+阈值 |
| `hk_rating_template_clone` | `source_template_id` (int), `user_id` (str), `new_name`? (str) | 克隆模板 |
| `hk_rating_template_update` | `template_id` (int), `user_id` (str), `factor_weights`? (JSON str), `category_thresholds`? (JSON str), `name`? (str), `description`? (str) | 修改自定义模板 |
| `hk_rating_compute` | `template_id` (int), `user_id` (str), `target_type`? (str), `target_id`? (int) | 计算评级 |
| `hk_rating_results` | `template_id` (int), `user_id` (str), `target_type`? (str), `limit`? (int) | 查看评级结果 |

MCP 配置示例:
```json
{
  "mcpServers": {
    "eco-data": {
      "command": "python3",
      "args": ["/Users/a80460/Desktop/Sanakan Datahub/mcp/eco_data_server.py"]
    }
  }
}
```

其中 `hk_rating_template_update` 的 `factor_weights` 和 `category_thresholds` 传 JSON 字符串：

```
factor_weights: '{"complexity":0.40,"underlying_risk":0.20,"leverage":0.15,"liquidity":0.12,"credit_quality":0.08,"currency_country":0.05}'

category_thresholds: '[{"max":1.5,"label":"Low"},{"max":2.5,"label":"Medium-Low"},{"max":3.5,"label":"Medium"},{"max":4.5,"label":"Medium-High"},{"max":99,"label":"High"}]'
```

---

## 六、前端 Web UI

相关文件:
- `/Users/a80460/Desktop/Sanakan Datahub/frontend/src/app/hk-funds/TemplateEditor.tsx`
- `/Users/a80460/Desktop/Sanakan Datahub/frontend/src/app/hk-funds/RatingResults.tsx`
- `/Users/a80460/Desktop/Sanakan Datahub/frontend/src/app/hk-funds/HkFundsContent.tsx`
- `/Users/a80460/Desktop/Sanakan Datahub/frontend/src/lib/api.ts`

Cibo 网页的 "基金数据" 页面新增了两个 tab：

- **模板编辑** — 下拉选模板 → 拖动每个因子的权重滑块 → 点击"归一化到100%"自动调整 → 编辑阈值 → "克隆为我的模板" → "保存修改" → "计算全部评级"
- **我的评级** — 查看评级分布卡片 → 点表头排序 → "CSV导出"下载 → "重新计算"

前端 TypeScript 调用（已在 `api.ts` 中封装）:

```typescript
import {
  fetchRatingTemplates,       // GET  /api/v1/templates
  fetchRatingTemplateDetail,  // GET  /api/v1/templates/:id
  cloneRatingTemplate,        // POST /api/v1/templates/clone
  updateRatingTemplate,       // PUT  /api/v1/templates/:id
  deleteRatingTemplate,       // DELETE /api/v1/templates/:id
  computeRatings,             // POST /api/v1/templates/:id/compute
  fetchRatingResults,         // GET  /api/v1/templates/:id/results
} from "@/lib/api";
```

---

## 七、完整工作流（curl 从头走一遍）

```bash
# ═══════════════════════════════════════════════════════════════
# 1. 启动服务
# ═══════════════════════════════════════════════════════════════
cd "/Users/a80460/Desktop/Sanakan Datahub"
python3 -m uvicorn hk_funds.api:app --host 127.0.0.1 --port 8004 &
# 等待 "Application startup complete"

# ═══════════════════════════════════════════════════════════════
# 2. 看看有哪些系统模板
# ═══════════════════════════════════════════════════════════════
curl -s "http://127.0.0.1:8004/api/v1/templates?user_id=system" | python3 -m json.tool

# ═══════════════════════════════════════════════════════════════
# 3. 查看 SFC 6-Factor 模板详情
# ═══════════════════════════════════════════════════════════════
curl -s "http://127.0.0.1:8004/api/v1/templates/2" | python3 -m json.tool

# ═══════════════════════════════════════════════════════════════
# 4. 克隆一份给自己用
# ═══════════════════════════════════════════════════════════════
curl -s -X POST "http://127.0.0.1:8004/api/v1/templates/clone?source_template_id=2&user_id=cibo_demo&new_name=Cibo%E4%B8%93%E5%B1%9E%E8%AF%84%E7%BA%A7"
# 返回 {"cloned_template_id": 7, ...}

# ═══════════════════════════════════════════════════════════════
# 5. 修改权重：复杂度从25%提到40%
# ═══════════════════════════════════════════════════════════════
curl -s -X PUT "http://127.0.0.1:8004/api/v1/templates/7" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "cibo_demo",
    "factor_weights": {
      "complexity": 0.40,
      "underlying_risk": 0.20,
      "leverage": 0.15,
      "liquidity": 0.12,
      "credit_quality": 0.08,
      "currency_country": 0.05
    }
  }'

# ═══════════════════════════════════════════════════════════════
# 6. 对全部基金执行评分
# ═══════════════════════════════════════════════════════════════
curl -s -X POST "http://127.0.0.1:8004/api/v1/templates/7/compute" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "cibo_demo", "target_type": "fund"}'

# ═══════════════════════════════════════════════════════════════
# 7. 查看评级分布
# ═══════════════════════════════════════════════════════════════
curl -s "http://127.0.0.1:8004/api/v1/templates/7/results?user_id=cibo_demo&target_type=fund&limit=5"
# {
#   "total_rated": 2021,
#   "distribution": [
#     {"category": "Medium-Low", "count": 2008},
#     {"category": "Medium", "count": 13}
#   ],
#   "results": [
#     {"target_name":"Hang Seng HSCEI Daily (1.5x) Leveraged Fund","overall_score":3.75,"category":"Medium-High"},
#     ...
#   ]
# }
```

---

## 八、数据库表结构（本次新增3张表）

数据库文件: `/Users/a80460/Desktop/Sanakan Datahub/hk_funds.duckdb`

```sql
-- 模板定义表
hk_rating_templates (
    id          INTEGER PRIMARY KEY,
    user_id     VARCHAR NOT NULL DEFAULT 'system',   -- 'system' = 内置模板
    name        VARCHAR NOT NULL,
    description TEXT,
    template_type VARCHAR NOT NULL,                   -- 'fund_risk' | 'manager_dd'
    methodology_version VARCHAR DEFAULT '1.0',
    is_system   BOOLEAN DEFAULT false,
    category_thresholds_json TEXT,                   -- [{"max":1.5,"label":"Low"},...]
    created_at  TIMESTAMP DEFAULT now(),
    updated_at  TIMESTAMP DEFAULT now()
)

-- 模板因子表
hk_template_factors (
    id          INTEGER PRIMARY KEY,
    template_id INTEGER NOT NULL,
    factor_key  VARCHAR NOT NULL,                    -- e.g. 'complexity', 'underlying_risk'
    factor_label VARCHAR,                            -- e.g. 'Product Complexity'
    weight      DECIMAL(5,4),                        -- e.g. 0.25
    ordinal     INTEGER DEFAULT 0,
    config_json TEXT,                                -- scoring logic JSON
    created_at  TIMESTAMP DEFAULT now(),
    UNIQUE(template_id, factor_key)
)

-- 用户评级结果表
hk_user_ratings (
    id          INTEGER PRIMARY KEY,
    template_id INTEGER NOT NULL,
    user_id     VARCHAR NOT NULL,
    target_type VARCHAR NOT NULL,                    -- 'fund' | 'manager'
    target_id   INTEGER NOT NULL,
    overall_score DECIMAL(5,2),
    category    VARCHAR,
    factor_scores_json TEXT,                         -- [{factor_key, score, weight, weighted}]
    methodology_version VARCHAR DEFAULT '1.0',
    computed_at TIMESTAMP DEFAULT now(),
    is_latest   BOOLEAN DEFAULT true                 -- 查最新评级用 WHERE is_latest=true
)
```

---

## 九、当前数据现状

| 数据表 | 记录数 | 说明 |
|--------|--------|------|
| hk_funds (is_active=true) | 2,021 | 活跃基金 ✓ |
| hk_fund_managers | 3,543 | 持牌管理人 ✓ |
| hk_fund_performance | 0 | 业绩数据待采集 |
| hk_manager_dd | 0 | 尽调数据待采集 |
| hk_fund_manager_aum | 0 | 管理人AUM待采集 |
| hk_fund_documents | 部分 | KFS等文件，部分基金有 |

目前评分主要靠基金类型(lookup)、复杂度分类(lookup)、关键词匹配(keyword)、默认值(default_score)这些字段驱动。业绩因子(performance)、管理人因子(manager_aum/manager_dd)在数据补齐之前会使用默认分数。
