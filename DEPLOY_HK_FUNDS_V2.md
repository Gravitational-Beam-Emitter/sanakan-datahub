# HK Fund KYP V2 部署指南 — SFC 双层分类引擎

> 本地路径 base: `/Users/a80460/Desktop/cibo datahub/`
> 服务器路径 base: `/opt/eco-data/`
> 端口: 8004 (不变)

## 改动概述

把原来单一的 `classification` 字段（ordinary/complex/derivatives/structured 互斥）拆成 SFC 双层独立分类：

- **§5.1A 衍生产品** (`is_derivative_product` boolean)
- **§5.5 复杂产品** (`is_complex_product` boolean)  
- **产品类型** (`complex_product_type`: derivative_fund / synthetic_etf / futures_etf / L&I / hedge_fund / structured / complex_bond / security_token / non_complex)

涉及文件（全部覆盖式部署）：

| 本地路径 | 服务器路径 | 改动 |
|----------|-----------|------|
| `hk_funds/config.py` | `/opt/eco-data/hk_funds/config.py` | 关键词常量拆成双层 |
| `hk_funds/storage.py` | `/opt/eco-data/hk_funds/storage.py` | DDL迁移+CRUD |
| `hk_funds/pipeline_funds.py` | `/opt/eco-data/hk_funds/pipeline_funds.py` | 三层分类引擎重写 |
| `hk_funds/api.py` | `/opt/eco-data/hk_funds/api.py` | 新增derivatives端点+参数 |
| `frontend/src/lib/api.ts` | `/opt/eco-data/frontend/src/lib/api.ts` | TypeScript接口更新 |
| `frontend/src/app/hk-funds/HkFundsContent.tsx` | `/opt/eco-data/frontend/src/app/hk-funds/HkFundsContent.tsx` | UI双层展示 |

---

## Step 1 — 替换后端文件

```bash
cd /opt/eco-data

# 覆盖 hk_funds/ 下 4 个文件
cp /path/to/local/hk_funds/config.py hk_funds/config.py
cp /path/to/local/hk_funds/storage.py hk_funds/storage.py
cp /path/to/local/hk_funds/pipeline_funds.py hk_funds/pipeline_funds.py
cp /path/to/local/hk_funds/api.py hk_funds/api.py
```

## Step 2 — 替换前端文件

```bash
cd /opt/eco-data

cp /path/to/local/frontend/src/lib/api.ts frontend/src/lib/api.ts
cp /path/to/local/frontend/src/app/hk-funds/HkFundsContent.tsx frontend/src/app/hk-funds/HkFundsContent.tsx
```

## Step 3 — 重启后端（自动跑迁移）

```bash
systemctl restart hk-funds-api
```

启动时会自动执行 `migrate_schema_v2()`：加新列 → 迁移旧数据 → 删旧列 → 加六因素列 → 重建索引。无需手动干预。

验证迁移成功：
```bash
curl http://127.0.0.1:8004/api/v1/health
# {"status":"ok","funds":{"total":...,"complex_product":...,"derivative_product":...},...}

curl http://127.0.0.1:8004/api/v1/funds/stats | python3 -m json.tool
# 应该看到 derivative_count, by_complex_type 等新字段
```

## Step 4 — 重跑分类引擎

数据迁移只做了旧值映射（精度有限），需要重跑分类引擎获得准确的六因素判定：

```bash
curl -X POST http://127.0.0.1:8004/api/v1/classify
```

## Step 5 — 重新构建前端

```bash
cd /opt/eco-data/frontend
npm run build
systemctl restart zt-frontend
```

---

## 新增API端点 & 参数

### 新增端点

```
GET /api/v1/funds/derivatives          # §5.1A 衍生产品列表
```

### 已有端点的新参数

```
GET /api/v1/funds?is_derivative_product=true    # 只查衍生产品
GET /api/v1/funds?is_complex_product=true        # 只查复杂产品
GET /api/v1/funds?complex_product_type=L&I       # 按产品类型过滤
GET /api/v1/funds/complex?complex_product_type=structured  # 复杂产品按类型过滤
POST /api/v1/funds/{id}/classify                 # 参数改为 is_derivative_product / is_complex_product / complex_product_type
```

### 旧参数仍然兼容

`classification=xxx` 和 `is_complex=true` 仍然能用，会自动映射到新字段，但响应中已不再返回这两个旧字段。

---

## 如何使用（cibo claude 调用示例）

```python
import requests
API = "http://127.0.0.1:8004"

# 1. 查所有衍生产品 (§5.1A)
r = requests.get(f"{API}/api/v1/funds/derivatives")
for f in r.json()["funds"]:
    print(f["fund_name_en"], f["complex_product_type"])

# 2. 按产品类型过滤 — 只看杠杆/反向产品
r = requests.get(f"{API}/api/v1/funds", params={"complex_product_type": "L&I"})
print(r.json()["count"], "个 L&I 产品")

# 3. 查某基金的双层分类
r = requests.get(f"{API}/api/v1/funds/search", params={"q": "leveraged"})
fund = r.json()["funds"][0]
print(f"衍生品: {fund['is_derivative_product']}, 复杂产品: {fund['is_complex_product']}")
print(f"产品类型: {fund['complex_product_type']}, 原因: {fund['classification_reason']}")

# 4. 看分类详情（六因素）
r = requests.get(f"{API}/api/v1/funds/{fund['id']}")
detail = r.json()
cls = detail.get("classification_detail", {})
print(f"衍生敞口: {cls.get('derivative_exposure_pct')}%")
print(f"六因素: 二级市场={cls.get('has_secondary_market')}, 信息透明={cls.get('has_transparent_info')}, 损失超本金={cls.get('loss_exceeds_principal')}, 复杂支付={cls.get('has_complex_payoff')}, 难估值={cls.get('illiquid_or_hard_to_value')}")

# 5. 统计概览
r = requests.get(f"{API}/api/v1/funds/stats")
stats = r.json()
print(f"总计: {stats['total']}, 复杂: {stats['complex_count']}, 衍生: {stats['derivative_count']}")
print("按类型分布:", stats["by_complex_type"])
```

---

## UI 变化

`/hk-funds` 页面更新：

- **基金清单 tab**：分类下拉框 → 两个切换按钮（§5.1A衍生产品 / §5.5复杂产品）；表格新增"产品类型""§5.1A衍生品""§5.5复杂"三列
- **复杂产品 tab**：新增衍生产品/复杂产品视图切换；新增SFC监管框架说明卡片；按产品类型分组代替原来的按来源分组
