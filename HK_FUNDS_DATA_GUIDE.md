# HK Funds KYP/DD — 数据使用说明

## 数据库：`hk_funds.duckdb`（~17MB，10张表，~29,000行）

| 表 | 行数 | 说明 |
|----|------|------|
| `hk_funds` | 2,021 | SFC认可基金主表（1,867 UTMF + 154 OFC，含衍生品标记、复杂产品分类） |
| `hk_fund_classifications` | 2,021 | §5.1A/§5.5 双维分类详情 |
| `hk_kyp_dimensions` | 20,210 | 产品尽调 10 维度 × 2,021 只基金 |
| `hk_kyp_assessment_log` | 4,039 | KYP 审计日志 |
| `hk_fund_risk_ratings` | 2,021 | 5 档风险评级 |
| `hk_fund_managers` | 0 | 管理人（待导入） |
| `hk_manager_dd` | 0 | 管理人尽调（待导入） |
| `hk_non_authorized_funds` | 2,231 | 非认可基金（2,231 只 Private OFC） |
| `hkex_listed_funds` | 0 | HKEX上市产品（服务器负责） |

---

## 一、基金分类（双维）

### 维度1：§5.1A 衍生品

查询 `hk_funds.is_derivative_product`：
- `true`：71只，来自SFC官方标记（`classification_source = "sfc_utmf"`）
- `false`：1,913只

```sql
SELECT fund_name_en, sfc_authorization_no
FROM hk_funds
WHERE is_active = true AND is_derivative_product = true;
```

### 维度2：§5.5 复杂产品

查询 `hk_funds.is_complex_product`：
- `true`：14只，6因子启发式引擎判定
- `false`：1,970只

14只复杂产品明细：
| complex_product_type | 数量 | 含义 |
|---------------------|------|------|
| `structured` | 8 | 结构性产品 |
| `derivative_fund` | 3 | 衍生品基金（NDE > 50%） |
| `L&I` | 2 | 杠杆/反向产品 |
| `synthetic_etf` | 1 | 合成ETF |

```sql
SELECT fund_name_en, complex_product_type, classification_reason
FROM hk_funds
WHERE is_active = true AND is_complex_product = true;
```

### 分类来源

| source | 数量 | 含义 |
|--------|------|------|
| `heuristic` | 1,913 | 启发式引擎判定（非衍生品/非复杂） |
| `sfc_utmf` | 65 | SFC官方衍生品标记 |
| `sfc_utmf+heuristic` | 6 | 双源一致 |

---

## 二、风险评级（5档）

API：`GET /api/v1/risk-ratings?risk_category=Medium`

```sql
SELECT f.fund_name_en, r.risk_category, r.overall_risk_score
FROM hk_fund_risk_ratings r
JOIN hk_funds f ON f.id = r.fund_id
ORDER BY r.overall_risk_score DESC;
```

**评级分布**：
| 级别 | 分数范围 | 数量 |
|------|---------|------|
| Low | ≤1.5 | 0 |
| Medium-Low | 1.5–2.5 | 1,951 |
| Medium | 2.5–3.5 | 33 |
| Medium-High | 3.5–4.0 | 0 |
| High | >4.0 | 0 |

**评级因子权重**：复杂度25% + 底层资产风险25% + 杠杆15% + 流动性15% + 信用质量10% + 货币/国家风险10%

评级引擎代码：`hk_funds/risk_rating.py`

重新计算（全量）：
```bash
python -m hk_funds.risk_rating --rate-all
```

单只基金：
```bash
python -m hk_funds.risk_rating --fund-id 123
```

人工覆盖 API：`PUT /api/v1/funds/{id}/risk-rating/override?new_score=3.5&new_category=Medium-High&reason=...`

---

## 三、KYP 产品尽调（10维度）

API：`GET /api/v1/funds/{id}/kyp`

10维度（SFC Code of Conduct para 5.1–5.5）：

| 维度代码 | 中文 | 已复核 |
|---------|------|--------|
| `product_structure` | 产品结构与机制 | 1,984 |
| `risk_profile` | 风险概况 | 1,984 |
| `complexity` | 复杂性分类 | 0 |
| `derivative_class` | 衍生品分类（§5.1A） | 71 |
| `issuer_assessment` | 发行人/对手方评估 | 0 |
| `fees_charges` | 费用与佣金 | 0 |
| `liquidity_lockup` | 流动性/锁定期 | 0 |
| `valuation_pricing` | 估值与定价 | 0 |
| `credit_quality` | 信用质量 | 0 |
| `key_terms` | 关键条款 | 0 |

前端 `/hk-funds` → "产品尽调" tab：
- 下拉选择基金
- 显示 10 维度完成进度条
- 2×5 卡片网格，颜色标记状态（已复核=绿、待评估=灰）

获取KYP缺口：
```bash
curl http://127.0.0.1:8004/api/v1/kyp/gaps?limit=20
```

---

## 四、管理人尽调（10维度）

API：`GET /api/v1/managers/{id}/dd`

10维度（SFC FMCC）：

| 维度 | 含义 |
|------|------|
| `financial_resources` | 财务资源与资本充足 |
| `human_resources` | 人力资源 |
| `internal_controls` | 内部控制 |
| `risk_governance` | 风险管理治理 |
| `segregation_duties` | 职责分离 |
| `compliance_function` | 合规职能 |
| `audit_function` | 审计职能 |
| `custodian_dd` | 托管人尽调 |
| `valuer_dd` | 估值人尽调 |
| `delegates_monitoring` | 转授权监控 |

当前状态：管理人表为空，需先导入管理人数据。

导入管理人 CSV：
```bash
python -m hk_funds.pipeline_managers --import-csv /path/to/managers.csv
# 或
curl -X POST http://127.0.0.1:8004/api/v1/import/managers-csv -F "file=@managers.csv"
```

CSV 列：`ce_number, company_name_en, company_name_cn, license_type, regulated_activity_1, regulated_activity_4, regulated_activity_9, license_status`

---

## 五、非认可基金（仅限PI）

API：`GET /api/v1/non-authorized-funds`

当前状态：**2,231 只 Private OFC 已入库**（来自 SFC OFC 登记册自动抓取）。另可通过前端 `/hk-funds` → "非认可基金" tab 手动录入。

查询 Private OFC：
```sql
SELECT fund_name_en, fund_manager_name_en, notes
FROM hk_non_authorized_funds
WHERE data_source = 'sfc_ofc_register' AND is_active = true;
```

分销限制类型：
- `pi_only` — 仅专业投资者（HK$8M+ portfolio）
- `pi_800k` — PI（800万港币组合）
- `institutional_only` — 仅机构
- `offshore_only` — 仅离岸

---

## 六、OFC（开放式基金公司）

### 数据来源

SFC OFC 登记册：`https://apps.sfc.hk/productlistWeb/searchProduct/OFC.do?lang=EN`

- **154 只 Public OFC**（SFC 认可，可零售） → `hk_funds` 表（`fund_type = 'open_ended_fund_company'`）
- **2,231 只 Private OFC**（非认可，仅 PI） → `hk_non_authorized_funds` 表（`data_source = 'sfc_ofc_register'`）

### OFC 结构

| 类型 | 数量 | 说明 |
|------|------|------|
| Umbrella OFC | 736 | 伞形 OFC（其中 37 只 Public，699 只 Private） |
| Sub-fund | 1,649 | 子基金（117 Public，1,532 Private） |
| 管理人 | 586 | 唯一投资管理人 |

### 数据查询

```sql
-- Public OFC（可零售分销）
SELECT fund_name_en, sfc_authorization_no, umbrella_fund_name, fund_manager_name_en
FROM hk_funds
WHERE fund_type = 'open_ended_fund_company' AND is_active = true;

-- Private OFC（仅 PI）
SELECT fund_name_en, fund_manager_name_en, notes
FROM hk_non_authorized_funds
WHERE data_source = 'sfc_ofc_register';

-- OFC 统计
curl http://127.0.0.1:8004/api/v1/ofc/stats
```

### 数据管道

```bash
# 抓取 OFC 登记册
python -m hk_funds.pipeline_ofc --fetch

# 完整管道（抓取 + 分类 + 风险评级 + KYP）
python -m hk_funds.pipeline_ofc --init

# 仅风险评级
python -m hk_funds.pipeline_ofc --rate-all

# API 触发
curl -X POST http://127.0.0.1:8004/api/v1/ofc/init
```

### API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/ofc/stats` | OFC 统计（Public/Private 数量、管理人） |
| POST | `/api/v1/ofc/fetch` | 抓取并存储 OFC 数据 |
| POST | `/api/v1/ofc/init` | 完整管道（fetch + classify + rate + KYP） |
| POST | `/api/v1/ofc/rate` | 对 OFC 基金运行风险评级 |
| POST | `/api/v1/ofc/classify` | 对 OFC 基金运行分类 |
| POST | `/api/v1/ofc/kyp` | 初始化 OFC 基金的 KYP 维度 |

---

## 七、前端展示

页面：`/hk-funds`（7个tab）

| Tab | 数据来源 | 功能 |
|-----|---------|------|
| 基金清单 | `GET /api/v1/funds` | 筛选、搜索、查看详情 |
| 产品尽调 | `GET /api/v1/funds/{id}/kyp` | 10维度矩阵、进度条 |
| 复杂产品 | `GET /api/v1/funds/complex` | 14只复杂产品明细 |
| 风险评级 | `GET /api/v1/risk-ratings` | 5档分布卡片、评级表 |
| 管理人尽调 | `GET /api/v1/managers` | 管理人列表、DD矩阵 |
| 监管追踪 | `GET /api/v1/managers/{id}/regulatory` | 执法记录 |
| 非认可基金 | `GET /api/v1/non-authorized-funds` | 录入、列表 |

---

## 八、Python模块路径

```python
# 基金数据
from hk_funds.storage import init_db, get_funds, get_fund_by_id
# KYP
from hk_funds.storage import get_kyp_dimensions, get_funds_with_kyp_gaps
# 风险评级
from hk_funds.risk_rating import calculate_fund_risk_rating, rate_all_funds
from hk_funds.storage import get_all_risk_ratings, get_fund_risk_rating
# 管理人
from hk_funds.storage import get_managers, get_manager_dd, get_managers_with_dd_gaps
# 非认可基金
from hk_funds.storage import get_non_authorized_funds, upsert_non_authorized_funds
# 数据采集
from hk_funds.pipeline_funds import fetch_funds_daily
from hk_funds.pipeline_managers import import_managers_csv, link_funds_to_managers
# OFC 管道
from hk_funds.pipeline_ofc import fetch_ofc_daily, init_ofc_pipeline, get_ofc_stats
```
