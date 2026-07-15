# Deploy: SIP + Classification Fixes + NDE Extractor Fix

## What changed

### 新功能
1. **新文件 `hk_funds/sip_pipeline.py`** — 抓取 SFC 结构性投资产品登记册，341只产品标记为 `structured`
2. **修改 `hk_funds/scheduler.py`** — 新增 SIP 每周一定时任务（Mon 11:27 HKT）

### 分类引擎修复
3. **修改 `hk_funds/pipeline_funds.py`** — 
   - source label 不会串了（sfc_sip 不会再被合并到 sfc_utmf）
   - 权威分类（SFC/HKEX）不会被启发式覆盖
   - 脏 label 自动清理

### NDE extractor 修复（重要）
4. **修改 `hk_funds/nde_extractor.py`** — 
   - `download_document()` 现在正确处理 SFC 的两步文档下载：
     - Step 1: 请求 HTML 文档列表页 → 解析出真正的 PDF 链接
     - Step 2: 下载 KFS PDF → 提取文本
   - 之前：把 HTML 当 PDF 解析 → PdfReader 报错 → 100% fallback 到 name-only
   - 之后：正确下载 KFS PDF → 提取真实文本 → LLM 能看到 KFS 内容

## 部署步骤

```bash
cd ~/datahub
git pull

# 1. 一次性跑 SIP pipeline
python3 -m hk_funds.sip_pipeline

# 2. 重跑全量分类
python3 -c "
from hk_funds.storage import init_db
from hk_funds.pipeline_funds import classify_all_funds
conn = init_db()
print(classify_all_funds(conn))
conn.close()
"

# 3. 验证分类结果
python3 -c "
import duckdb
conn = duckdb.connect('hk_funds.duckdb')
total = conn.execute('SELECT count(*) FROM hk_funds WHERE is_active=true').fetchone()[0]
c = conn.execute('SELECT count(*) FROM hk_funds WHERE is_active=true AND is_complex_product=true').fetchone()[0]
d = conn.execute('SELECT count(*) FROM hk_funds WHERE is_active=true AND is_derivative_product=true').fetchone()[0]
print(f'Total: {total}, Complex: {c} ({100*c/total:.1f}%), Derivative: {d} ({100*d/total:.1f}%)')
conn.close()
"
# 应该看到: Complex ~462 (16.6%), Derivative ~461 (16.5%)

# 4. 测试 NDE PDF 下载是否修复（用一只基金测试）
python3 -c "
from hk_funds.nde_extractor import download_document, extract_pdf_text
url = 'https://apps.sfc.hk/productlistWeb/searchProduct/getDocListNoDate.do?lang=EN&ceref=BRV468&docType=OD'
pdf = download_document(url)
if pdf and pdf[:4] == b'%PDF':
    text = extract_pdf_text(pdf)
    print(f'OK: {len(pdf)} bytes PDF, {len(text)} chars text extracted')
    print(text[:200])
else:
    print('FAIL: could not download PDF')
"

# 5. 可选：跑 NDE batch（需要 LLM API key）
# python3 -m hk_funds.nde_extractor --batch --limit 20

# 6. 可选：启动 scheduler
# python3 -m hk_funds.scheduler
```

## 预期效果

| | 之前 | 之后 |
|---|---|---|
| 衍生品识别率 | 71/2023 (3.5%) | 461/2786 (16.5%) |
| 复杂产品识别率 | 15/2023 (0.7%) | 462/2786 (16.6%) |
| 结构性产品 | 0 | 342 |
| NDE PDF下载 | 0% 成功 | 100% 成功 |

数据来源分布：
- `sfc_sip`: 341（SFC 结构性产品登记册）
- `sfc_utmf`: 81（SFC UTMF 衍生品标记）
- `hkex_list`: 422（港交所 L&I/合成ETF/期货ETF）
- `heuristic`: 1942（关键词）

## 注意事项

- SIP + 分类修复不需要 API key
- NDE extractor 需要 LLM API key（DEEPSEEK_API_KEY 或 ANTHROPIC_API_KEY）
- pypdf 已安装就行（`pip install pypdf`），代码里已是 `from pypdf import PdfReader`
