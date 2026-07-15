# Deploy: Manager Profile Extraction + DD Scoring Enhancement

## 修的什么

之前管理人 connector 只抓 NAV、ISIN、fund list 这些基金层面的数据。管理人尽调（10维度DD）全靠 proxy 信号凑合（Wikipedia有没有、成立多少年、牌照多久），没有真正从管理人网站抓过公司介绍、团队规模、基金经理、获奖记录。

现在新增了：

1. **管理人网站公司介绍抓取** — 自动访问每个管理人的 About Us / Team / Contact / News 页面，提取全部可见文字
2. **LLM 结构化提取** — 把网页文字发给 LLM，提取：公司介绍、成立年份、员工人数、投资团队人数、办公室分布、高管简介、获奖记录、AUM、投资理念、资产类别、机构客户、其他监管牌照
3. **DD 评分增强** — financial_resources 和 human_resources 两个维度现在会参考真实数据（有AUM用AUM，有员工人数用员工人数），不再全靠 proxy 瞎猜

## 改动的文件（7个）

| 文件 | 改动 |
|------|------|
| `hk_funds/manager_connectors/base.py` | BaseManagerConnector 新增 `get_about_page()` / `get_team_page()` / `get_contact_page()` / `get_news_page()` / `scrape_company_profile()` |
| `hk_funds/manager_profile_extractor.py` | **新文件** — LLM 提取模块，复用 `nde_extractor.py` 同款 LLM 客户端 |
| `hk_funds/storage.py` | 新增 `hk_manager_profiles` 表 + CRUD 函数 |
| `hk_funds/pipeline_manager_dd.py` | `score_financial_resources()` 新增 AUM/awards/institutional_clients 参数；`score_human_resources()` 新增 total_staff/investment_professionals 参数 |
| `hk_funds/manager_connectors/blackrock.py` | 新增 Playwright 版 About/Team/News 页面抓取（BlackRock是SPA，requests 拿不到内容） |
| `hk_funds/manager_connectors/hsbc.py` | 同上，HSBC 专用 Playwright 抓取 |
| `hk_funds/scheduler.py` | 新增 Job 7：每月第一个周一 13:07 HKT 自动跑 profile 提取 + DD 重算 |
| `mcp/eco_data_server.py` | `hk_manager_scrape_status` 返回 `profile_extractions` 统计 |

## 部署步骤

```bash
cd ~/datahub
git pull

# 1. 数据库迁移 — 新增 hk_manager_profiles 表
python3 -c "
from hk_funds.storage import init_db
conn = init_db()
conn.close()
print('OK: hk_manager_profiles table created')
"

# 2. 测试 profile 抓取（不需要 LLM）
python3 -c "
from hk_funds.manager_connectors.value_partners import ValuePartnersConnector
c = ValuePartnersConnector()
result = c.scrape_company_profile()
if result['combined_text']:
    print(f'OK: scraped {len(result[\"combined_text\"])} chars from Value Partners')
    print(f'Sources: {result[\"source_urls\"]}')
else:
    print('No text scraped (valuepartners.com.hk may require Playwright)')
"

# 3. 跑 profile 提取（先试 5 个管理人，确认 LLM 可用）
python3 -m hk_funds.manager_profile_extractor --all-connected --limit 5

# 4. 如果上面成功了，跑 DD 重算（把 profile 数据用上）
python3 -m hk_funds.pipeline_manager_dd

# 5. 验证结果
python3 -c "
import duckdb
conn = duckdb.connect('hk_funds.duckdb')

# 看 profile 表
print('=== Profiles extracted ===')
rows = conn.execute('''
    SELECT m.company_name_en, p.total_staff, p.aum_usd, p.founding_year,
           p.institutional_clients, p.extraction_date
    FROM hk_manager_profiles p
    JOIN hk_fund_managers m ON m.id = p.manager_id
    ORDER BY p.aum_usd DESC NULLS LAST
    LIMIT 10
''').fetchall()
for r in rows:
    aum_str = f'\${r[2]/1e9:.1f}B' if r[2] else 'N/A'
    staff_str = str(r[1]) if r[1] else 'N/A'
    print(f'  {r[0][:40]:40s} staff={staff_str:6s} AUM={aum_str:10s} year={r[3]}')

# 看 DD 评分有没有被 profile 数据影响
print()
print('=== DD scores with profile enrichment ===')
rows = conn.execute('''
    SELECT m.company_name_en, dd.dd_dimension, dd.score, dd.data_source, dd.findings
    FROM hk_manager_dd dd
    JOIN hk_fund_managers m ON m.id = dd.manager_id
    WHERE dd.data_source LIKE '%manager_website%'
    ORDER BY dd.score DESC
    LIMIT 10
''').fetchall()
for r in rows:
    print(f'  {r[0][:35]:35s} {r[1]:25s} score={r[2]} source={r[3][:30]}')

conn.close()
"
```

## 网页展示建议

`hk_manager_profiles` 表里有这些字段可以直接在网页上展示：

### 管理人详情页 — Company Profile 区块

```sql
SELECT company_description_en, company_description_cn,
       founding_year, total_staff, investment_professionals,
       offices, key_personnel, awards,
       aum_usd, aum_date, investment_philosophy,
       asset_classes, institutional_clients, regulatory_licenses,
       data_source, extraction_date
FROM hk_manager_profiles
WHERE manager_id = ?
```

网页上可以展示：

```
┌─ Company Profile ──────────────────────────────────┐
│ 公司简介: <company_description_en>                    │
│ 成立年份: <founding_year>                             │
│ 员工人数: <total_staff> | 投资团队: <investment_professionals> │
│ AUM: <aum_usd> (as of <aum_date>)                   │
│                                                     │
│ 投资理念: <investment_philosophy>                     │
│ 资产类别: <asset_classes>                             │
│ 机构客户: <institutional_clients>                     │
│ 监管牌照: <regulatory_licenses>                       │
│                                                     │
│ 📍 Offices: <offices列表>                             │
│ 👥 Key Personnel: <key_personnel列表，每人 name+title>  │
│ 🏆 Awards: <awards列表>                               │
│                                                     │
│ Source: <data_source> | Last updated: <extraction_date> │
└─────────────────────────────────────────────────────┘
```

### DD 评分页 — 展示真实数据来源

DD 评分中 `data_source` 字段现在会区分：
- `webb_site_wikipedia` — 纯 proxy 信号（旧方式）
- `manager_website+webb_site_wikipedia` — 有真实网站数据支撑

网页上可以标注数据来源，让用户知道哪些评分是基于真实数据、哪些是估算。

### MCP API 新增数据

`hk_manager_scrape_status` 返回结果新增了 `profile_extractions` 字段：

```json
{
  "profile_extractions": {
    "total": 12,
    "with_aum": 8,
    "with_staff": 6
  }
}
```

### 前端改动建议

1. **管理人详情页** (`frontend/src/app/hk-funds/managers/[id]/`) — 新建一个 `CompanyProfileCard` 组件，展示 profile 数据
2. **管理人列表页** — 加一列显示是否有 profile 数据（✅/❌）
3. **DD 评分页** — 在评分旁边显示 data_source 标签

MCP API 加一个 endpoint（在 `eco_data_server.py` 里已有点）：

```python
# 在 api.py 或直接 SQL 就可以查
GET /api/v1/managers/<id>/profile
→ SELECT * FROM hk_manager_profiles WHERE manager_id = ?
```

## 注意事项

1. **LLM API Key 必须配置** — profile 提取依赖 DeepSeek / Anthropic / Qwen API key。`DEEPSEEK_API_KEY` 在 `.env` 里
2. **Playwright 依赖** — BlackRock 和 HSBC 的 profile 抓取需要 Playwright（已经在服务器上装了，用于 fund 数据抓取）
3. **不是所有管理人都能抓到数据** — 有些管理人网站是纯 JS 渲染的 SPA（requests 拿不到），需要像 BlackRock/HSBC 一样单独写 Playwright 版 connector。目前约 50-55 个管理人有 connector，其中 BlackRock 和 HSBC 做了 Playwright 覆盖，其余用 requests 尝试通用 URL pattern
4. **首次跑完全量** — 建议先 `--limit 5` 测试，确认没问题后再去掉 limit 跑全量
5. **DD 重算** — profile 提取完后必须跑 `pipeline_manager_dd` 才能让 DD 评分用上新数据
