# KR Stock 韩国股票管道 — 部署指南

> 本地路径 base: `/Users/a80460/Desktop/cibo datahub/`
> 服务器路径 base: `/opt/eco-data/`
> 端口: 8006（新增）

## 改动概述

新增 `kr_stock/` 模块 — 韩国股票（KOSPI/KOSDAQ/KONEX）数据管道。包含：
- 股票清单 + 每日OHLCV价格
- KOSPI/KOSDAQ指数数据
- 显著涨跌股追踪（±10%阈值）
- LLM自动标记（题材标签 + 市场主线叙事）
- DART公司披露文件（韩国版EDGAR）
- 外资/机构资金流向

涉及文件（全部新增部署）：

| 本地路径 | 服务器路径 | 说明 |
|----------|-----------|------|
| `kr_stock/` (整个目录) | `/opt/eco-data/kr_stock/` | 6个Python模块 |
| `eco_data_sdk/kr_stock_client.py` | `/opt/eco-data/eco_data_sdk/kr_stock_client.py` | SDK客户端 |
| `eco_data_sdk/__init__.py` | `/opt/eco-data/eco_data_sdk/__init__.py` | SDK导出更新 |
| `mcp/eco_data_server.py` | `/opt/eco-data/mcp/eco_data_server.py` | MCP新增7个工具 |

---

## Step 1 — 安装依赖

```bash
pip install finance-datareader
```

FinanceDataReader 是唯一必须新增的依赖。LLM 标记用到的 openai/anthropic SDK 项目已有。DART 披露是可选的（需要 `opendartreader`，只用 `pip install opendartreader` 且配了 DART_API_KEY 才抓取）。

## Step 2 — 部署后端文件

```bash
cd /opt/eco-data

# 整个 kr_stock 目录拷贝
cp -r /path/to/local/kr_stock/ kr_stock/

# SDK 客户端
cp /path/to/local/eco_data_sdk/kr_stock_client.py eco_data_sdk/kr_stock_client.py
cp /path/to/local/eco_data_sdk/__init__.py eco_data_sdk/__init__.py

# MCP server（覆盖）
cp /path/to/local/mcp/eco_data_server.py mcp/eco_data_server.py
```

## Step 3 — 启动后端

```bash
# 方式1：直接启动
python3 -m uvicorn kr_stock.api:app --host 127.0.0.1 --port 8006 &

# 方式2：systemd（推荐）
cat > /etc/systemd/system/kr-stock-api.service << 'EOF'
[Unit]
Description=Korean Stock API
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/eco-data
ExecStart=/usr/bin/python3 -m uvicorn kr_stock.api:app --host 127.0.0.1 --port 8006
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable kr-stock-api
systemctl start kr-stock-api
```

验证启动成功：
```bash
curl http://127.0.0.1:8006/api/v1/health
# {"status":"ok","total_stocks":0,"trading_days_with_prices":0,...}
```

## Step 4 — 初始化数据（重要！）

新数据库是空的，必须运行初始化：

```bash
cd /opt/eco-data

# 全量初始化：清单 + 指数历史 + 最近几天价格
python3 -m kr_stock.pipeline --init
```

预计耗时：首批500只股票（按市值排序）的近期价格数据需要 3-5 分钟。全量 2,875 只股票首次抓取会更久。建议先用 --init（只抓前500只），后续通过每日定时任务逐步覆盖全量。

初始化后验证：
```bash
curl http://127.0.0.1:8006/api/v1/health
# 应该有 total_stocks > 2000, trading_days_with_prices > 0

curl http://127.0.0.1:8006/api/v1/listings?market=KOSPI&limit=5
# 应返回 KOSPI 股票清单
```

## Step 5 — 启动定时任务

韩国股市收盘时间 15:30 KST（首尔时间 = 北京时间）。定时任务在 16:07 跑。

```bash
# 后台启动
python3 -m kr_stock.scheduler &

# 或加入 crontab
# cron: 7 16 * * 1-5 cd /opt/eco-data && python3 -m kr_stock.pipeline >> /var/log/kr_stock.log 2>&1
```

## Step 6 — 重启 MCP server

```bash
# 如果 MCP server 是 systemd 管理的：
systemctl restart eco-data-mcp

# 或直接杀进程重启
pkill -f eco_data_server.py
python3 mcp/eco_data_server.py &
```

---

## 新增文件清单（全部在本地）

```
kr_stock/
├── __init__.py
├── config.py
├── storage.py
├── pipeline.py
├── tagging.py
├── api.py
└── scheduler.py

eco_data_sdk/
├── kr_stock_client.py    (新增)
└── __init__.py           (修改：新增导入和导出)

mcp/
└── eco_data_server.py    (修改：+7个工具)
```
