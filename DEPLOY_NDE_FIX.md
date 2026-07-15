# Deploy: NDE Boolean Flag Fix

## 修的什么

cibo 发现的 bug：LLM（DeepSeek）把 5 只加密现货 ETF 错误标记为 `is_leveraged=True`，分类引擎不加甄别地信任这些标志位，导致 `_determine_complex_type()` 直接返回 `L&I`。

## 改动（2 个文件）

1. **`hk_funds/pipeline_funds.py`** — `_classify_fund()` 增加 NDE 信任阈值
   - NDE = 0% → 忽略 LLM 的 `is_leveraged`/`is_inverse`/`is_synthetic_replication`/`uses_derivatives_for_non_hedging` 布尔标志位
   - NDE > 0% → 正常使用（有真实衍生品敞口支撑）

2. **`hk_funds/config.py`** — 因子⑥新增虚拟资产关键词
   - `bitcoin`, `ether`, `solana`, `virtual asset`, `crypto`
   - `比特币`, `以太币`, `虚拟资产`, `加密货币`

## 部署

```bash
cd ~/datahub
git pull

# 重跑分类即可
python3 -c "
from hk_funds.storage import init_db
from hk_funds.pipeline_funds import classify_all_funds
conn = init_db()
print(classify_all_funds(conn))
conn.close()
"

# 验证 6 只受影响基金
python3 -c "
import duckdb
conn = duckdb.connect('hk_funds.duckdb')
for auth in ['BUU104','BUU105','BUU163','BUU164','BWV760','BJB721']:
    r = conn.execute('SELECT fund_name_en, complex_product_type, is_derivative_product, is_complex_product FROM hk_funds WHERE sfc_authorization_no=?', [auth]).fetchone()
    if r:
        print(f'{auth}: cpt={r[1]}, deriv={r[2]}, complex={r[3]}  ({r[0][:60]})')
conn.close()
"
# 加密现货 ETF → complex_bond, deriv=False, complex=True
# i Capital China Fund → 取决于 NDE 数据，但不会再被错误标记为 L&I
```

## 不需要重新跑 NDE extraction

已有的 NDE 数据不用重跑。分类引擎层面的修复就够了——即使 `hk_fund_classifications` 表里 `is_leveraged=True`, `derivative_exposure_pct=0`，分类引擎也会忽略。
