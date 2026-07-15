# KR Stock Fundamentals 部署指南

## 一、文件清单

以下文件需要同步到服务器（完整本地路径）：

### 修改的文件（6个）

```
/Users/a80460/Desktop/cibo datahub/kr_stock/storage.py        # +3张表 +upsert/query函数
/Users/a80460/Desktop/cibo datahub/kr_stock/pipeline.py       # +3个yfinance抓取函数 +fetch_daily集成
/Users/a80460/Desktop/cibo datahub/kr_stock/api.py            # +4个API端点
/Users/a80460/Desktop/cibo datahub/eco_data_sdk/kr_stock_client.py  # +4个SDK方法
/Users/a80460/Desktop/cibo datahub/mcp/eco_data_server.py     # +3个MCP工具
/Users/a80460/Desktop/cibo datahub/frontend/src/lib/api.ts    # +KR types +7个fetch函数
/Users/a80460/Desktop/cibo datahub/frontend/src/components/NavBar.tsx  # +KR韩股tab
```

### 无须变动的文件（之前已有）

```
/Users/a80460/Desktop/cibo datahub/kr_stock/__init__.py
/Users/a80460/Desktop/cibo datahub/kr_stock/config.py
/Users/a80460/Desktop/cibo datahub/kr_stock/tagging.py
/Users/a80460/Desktop/cibo datahub/kr_stock/scheduler.py
/Users/a80460/Desktop/cibo datahub/kr_stock/kr_stock.duckdb   # 包含已有数据的数据库文件
```

---

## 二、数据库

### 数据库文件路径
```
/Users/a80460/Desktop/cibo datahub/kr_stock/kr_stock.duckdb
```

### 新增的3张表（init_db() 自动创建，首次连接时生成）

| 表名 | 主键 | 内容 |
|------|------|------|
| `kr_stock_metrics` | (code, date) | 估值指标：PE/PB/ROE/Beta/市值/增长/利润率/股东结构/均线等28个字段 |
| `kr_stock_financials` | (code, date, statement_type, metric_name) | 季度财报（BS资产负债表/IS利润表/CF现金流量表），EAV模式 |
| `kr_analyst_data` | (code, date) | 分析师共识：目标价/评级/分析师人数/盈利预测/EPS趋势 |

### SQL 查询示例

```sql
-- 查三星估值指标（最新）
SELECT * FROM kr_stock_metrics WHERE code='005930' ORDER BY date DESC LIMIT 1;

-- 查三星最新季度利润表
SELECT date, metric_name, value FROM kr_stock_financials
WHERE code='005930' AND statement_type='IS' ORDER BY date DESC, metric_name;

-- 查分析师覆盖最多的股票
SELECT code, num_analysts, target_mean FROM kr_analyst_data
ORDER BY num_analysts DESC LIMIT 20;

-- 查高ROE低PE的股票
SELECT code, pe_forward, roe, dividend_yield FROM kr_stock_metrics
WHERE pe_forward > 0 AND pe_forward < 15 AND roe > 0.15
ORDER BY date DESC;
```

---

## 三、启动服务

### 1. KR Stock API（端口 8006）

```bash
cd "/Users/a80460/Desktop/cibo datahub"
python3 -m uvicorn kr_stock.api:app --host 0.0.0.0 --port 8006
```

验证：
```bash
curl http://localhost:8006/api/v1/health
```

### 2. MCP Server（stdio模式，由Claude Code/MCP客户端自动管理）

MCP配置文件示例（`~/.claude/claude_desktop_config.json` 或 `settings.json`）：
```json
{
  "mcpServers": {
    "eco-data": {
      "command": "python3",
      "args": ["/Users/a80460/Desktop/cibo datahub/mcp/eco_data_server.py"]
    }
  }
}
```

现有 `eco-data` MCP server 已包含所有工具（原有13个eco工具 + 原有7个KR工具 + 新增3个KR基本面工具）。

### 3. 首次初始化——抓取基本面数据

```bash
cd "/Users/a80460/Desktop/cibo datahub"

# 抓取单只股票（测试）
python3 -c "
from kr_stock.pipeline import fetch_stock_metrics, fetch_stock_financials, fetch_analyst_data
# 三星电子
fetch_stock_metrics(['005930'])
fetch_stock_financials(['005930'])
fetch_analyst_data(['005930'])
print('Done')
"

# 抓取前200只（按市值排序）
python3 -c "
from kr_stock.storage import init_db, get_listed_stocks
from kr_stock.pipeline import fetch_stock_metrics, fetch_stock_financials, fetch_analyst_data

conn = init_db(read_only=True)
top200 = get_listed_stocks(conn, active_only=True, limit=200)
codes = top200['code'].tolist()
conn.close()

print(f'Fetching fundamentals for {len(codes)} stocks...')
fetch_stock_metrics(codes)
fetch_stock_financials(codes)
fetch_analyst_data(codes)
print('Done')
"
```

### 4. 每日自动更新

`fetch_daily()` 已自动集成基本面抓取——每天抓取异动股票（涨跌幅>=10%）的基本面数据。

```bash
cd "/Users/a80460/Desktop/cibo datahub"

# 手动触发每日抓取
python3 -m kr_stock.pipeline

# 指定日期
python3 -m kr_stock.pipeline --date 2026-06-20

# 带LLM标签
python3 -m kr_stock.pipeline --date 2026-06-20  # 默认启用LLM
python3 -m kr_stock.pipeline --date 2026-06-20 --no-llm  # 跳过LLM
```

定时任务（scheduler.py，周一至周五韩国收盘后15:37 KST）：
```bash
python3 -m kr_stock.scheduler
```

---

## 四、数据使用方式

### 方式1：REST API（端口8006）

```bash
# 股票估值指标
curl http://localhost:8006/api/v1/stock/005930/metrics

# 季度财报
curl "http://localhost:8006/api/v1/stock/005930/financials?type=IS"   # 利润表
curl "http://localhost:8006/api/v1/stock/005930/financials?type=BS"   # 资产负债表
curl "http://localhost:8006/api/v1/stock/005930/financials?type=CF"   # 现金流量表

# 分析师数据
curl http://localhost:8006/api/v1/stock/005930/analyst

# 批量指标
curl -X POST http://localhost:8006/api/v1/metrics/batch \
  -H "Content-Type: application/json" \
  -d '{"codes": ["005930", "000660", "035420"]}'

# 每日复盘（含异动股+估值）
curl http://localhost:8006/api/v1/daily/2026-06-20
```

### 方式2：Python SDK

```python
from eco_data_sdk import KrStockClient

with KrStockClient() as kr:
    # 估值指标
    m = kr.stock_metrics("005930")
    print(f"PE: {m['pe_forward']}, ROE: {m['roe']}, Beta: {m['beta']}")
    print(f"市值: {m['market_cap']:,}, 机构持股: {m['inst_holding_pct']:.1%}")

    # 季度财报
    fs = kr.stock_financials("005930", statement_type="IS")
    for f in fs[:5]:
        print(f"  {f['date']} | {f['metric_name']}: {f['value']:,.0f}")

    # 分析师
    a = kr.stock_analyst("005930")
    print(f"目标价: {a['target_mean']:,.0f}, 分析师: {a['num_analysts']}位")

    # 批量查询
    batch = kr.metrics_batch(["005930", "000660", "035420"])
    for code, metrics in batch["metrics"].items():
        if metrics:
            print(f"{code}: PE={metrics['pe_forward']}, ROE={metrics['roe']}")
```

### 方式3：MCP 工具（Claude Code 可直接调用）

```
工具: kr_stock_metrics
参数: {"code": "005930"}
返回: PE/PB/ROE/Beta/市值/增长率/利润率/机构持股/均线/52周高低 等28个估值指标

工具: kr_stock_financials
参数: {"code": "005930", "statement_type": "IS"}
返回: 季度财报（BS资产负债表/IS利润表/CF现金流量表）

工具: kr_stock_analyst
参数: {"code": "005930"}
返回: 目标均价/最高/最低/中位数，评级，分析师人数，盈利预测，EPS趋势
```

### 方式4：直连 DuckDB（最快，适合批量分析）

```python
import duckdb

db = "/Users/a80460/Desktop/cibo datahub/kr_stock/kr_stock.duckdb"
conn = duckdb.connect(db, read_only=True)

# 筛选：低PE + 高ROE + 高增长
df = conn.execute("""
    SELECT m.code, s.name, m.pe_forward, m.roe, m.revenue_growth, m.dividend_yield
    FROM kr_stock_metrics m
    JOIN kr_listed_stocks s ON m.code = s.code
    WHERE m.date = (SELECT MAX(date) FROM kr_stock_metrics)
      AND m.pe_forward > 0 AND m.pe_forward < 15
      AND m.roe > 0.15
    ORDER BY m.roe DESC
""").df()
print(df.head(20))
```

---

## 五、前端

前端已添加 KR 相关 TypeScript 类型和 fetch 函数（`frontend/src/lib/api.ts`），以及导航栏 `KR韩股` tab（`NavBar.tsx`）。

如需启用 KR 股票前端页面，创建 `frontend/src/app/kr-stock/page.tsx`（可参考 `tw-stock/page.tsx` 模板）。

```bash
cd "/Users/a80460/Desktop/cibo datahub/frontend"
npm run dev
# 访问 http://localhost:3000/kr-stock
```

---

## 六、依赖

```bash
pip install yfinance duckdb pandas fastapi uvicorn requests
```

`yfinance` 是核心依赖——用于抓取估值指标、季度财报、分析师数据。无需 API key。

---

## 七、注意事项

1. **yfinance 有速率限制**：每只股票约0.5-1秒，200只约2-3分钟。`fetch_daily()` 仅对异动股（~20-50只/天）抓取，不会触发全量。
2. **首次批量抓取**：如需覆盖全部2875只股票，建议分批次、加 sleep。
3. **`.KQ` vs `.KS` 后缀**：代码自动判断——以1或2开头的代码（KOSDAQ）用`.KQ`，其余用`.KS`。
4. **数据时效**：yfinance 财报数据延迟1-2个季度，估值指标为实时。
5. **端口**：KR API 端口 8006（与 TW 8007、US listings 8003 等互不冲突）。
