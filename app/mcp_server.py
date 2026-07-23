"""
MCP (Model Context Protocol) Server — lightweight JSON-RPC over stdio.

Exposes eco-data tools to AI agents (Claude Code etc.).
Run: python app/mcp_server.py

No external dependencies — pure stdlib JSON-RPC.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, List, Optional

from app.storage import init_db, get_indicators, get_indicator, get_data, search_indicators, get_all_tags
from app.pipeline import run_once

SERVER_NAME = "eco-data-mcp"
SERVER_VERSION = "1.2.0"

SOURCE_META = {
    "us":   {"label": "US / FRED",              "provider": "Federal Reserve Economic Data", "key_required": True,  "description": "US GDP, CPI, unemployment, Fed funds, Treasury yields, credit spreads, housing, labor, PCE inflation, financial conditions, sovereign yields (8 countries), exchange rates (9 pairs)", "category": "macro"},
    "cn":   {"label": "China / AKShare",        "provider": "AKShare (东方财富/新浪)",        "key_required": False, "description": "中国 GDP, CPI, PPI, PMI, M2, LPR, 社融, 外汇储备, 房地产, 消费, 贸易, 北向资金, 融资融券, 国债收益率, 汇率", "category": "macro"},
    "global_": {"label": "Global / World Bank",  "provider": "World Bank WDI API",            "key_required": False, "description": "GDP, CPI, GDP growth, population for 8+ countries (1960-full)", "category": "macro"},
    "hk":   {"label": "Hong Kong / AKShare",    "provider": "AKShare",                       "key_required": False, "description": "香港 CPI, PPI, GDP, 失业率, 贸易, 建造, HIBOR", "category": "macro"},
    "jp":   {"label": "Japan / BoJ+AKShare",    "provider": "Bank of Japan + AKShare",       "key_required": False, "description": "日本 CPI, 失业率, 政策利率, 领先指标, Tankan调查", "category": "macro"},
    "euro": {"label": "Eurozone / AKShare",     "provider": "AKShare (Jin10财经日历)",       "key_required": False, "description": "欧元区 GDP, CPI, PPI, PMI, 失业率, 工业产出, 零售, 贸易, ZEW/Sentix情绪", "category": "macro"},
    "uk":   {"label": "UK / AKShare",           "provider": "AKShare (Jin10财经日历)",       "key_required": False, "description": "英国 GDP, CPI, 失业率, 零售, 贸易, Halifax/Rightmove房价, 央行利率", "category": "macro"},
    "de":   {"label": "Germany / AKShare",      "provider": "AKShare (Jin10财经日历)",       "key_required": False, "description": "德国 CPI, GDP, Ifo商业景气, ZEW情绪, 贸易", "category": "macro"},
    "au":   {"label": "Australia / AKShare",    "provider": "AKShare (Jin10财经日历)",       "key_required": False, "description": "澳大利亚 CPI, 失业率, 零售, 贸易, RBA利率", "category": "macro"},
    "ca":   {"label": "Canada / AKShare",       "provider": "AKShare (Jin10财经日历)",       "key_required": False, "description": "加拿大 CPI, GDP, 失业率, 贸易, BoC利率", "category": "macro"},
    "ch":   {"label": "Switzerland / AKShare",  "provider": "AKShare (Jin10财经日历)",       "key_required": False, "description": "瑞士 CPI, GDP, 贸易, SVME PMI, SNB利率", "category": "macro"},
    "bond": {"label": "Bond Market / AKShare",   "provider": "AKShare",                       "key_required": False, "description": "中美各期限国债收益率 (2Y/5Y/10Y/30Y), 利差, 可转债指数", "category": "macro"},
    "futures": {"label": "Futures / AKShare",    "provider": "AKShare (新浪财经)",            "key_required": False, "description": "沪金/沪银/沪铜/螺纹钢/铁矿石/原油主力合约", "category": "macro"},
    "shipping": {"label": "Shipping / AKShare",  "provider": "AKShare (新浪财经)",            "key_required": False, "description": "波罗的海干散货/油轮指数 BDI/BCI/BPI/BCTI", "category": "macro"},
    "banks": {"label": "Central Bank Rates",     "provider": "AKShare (Jin10财经日历)",       "key_required": False, "description": "全球央行政策利率: ECB, BOE, BOJ, RBA, SNB, Fed, RBI, BCB, RBNZ", "category": "macro"},
    "alt":  {"label": "Alternative / Leading",   "provider": "AKShare",                       "key_required": False, "description": "SOX半导体, 原油油轮, 大宗商品/能源/农业/建材指数, 金银ETF持仓, 消费者信心, OPEC产量", "category": "macro"},
    "llm":  {"label": "LLM Ecosystem",           "provider": "GitHub + HuggingFace + PyPI",   "key_required": False, "description": "LLM生态代理指标: GitHub Stars (9 repos), HuggingFace下载量 (5 models), PyPI月下载量 (5 SDKs)", "category": "macro"},
    "defi": {"label": "DeFi & Prediction Markets","provider": "Polymarket + DeFi Llama + CoinGecko", "key_required": False, "description": "链上金融: Polymarket预测市场交易量, DeFi DEX/衍生品TVL, RWA代币化规模, CEX交易量", "category": "macro"},
    "ai":   {"label": "AI Infrastructure",           "provider": "FRED (Federal Reserve Economic Data)", "key_required": True,  "description": "AI全供应链: SOX半导体指数, Kelly数据中心指数, 云计算指数, 半导体/PCB/存储/网络设备/变压器PPI, 制造业建设(芯片fab), 铀/铜/锂价格, 核电发电, 电价, AI机器人指数", "category": "macro"},
    "ai_co": {"label": "AI Company Financials",       "provider": "Yahoo Finance (yfinance)",         "key_required": False, "description": "AI供应链企业财报: NVIDIA/TSMC/ASML/Broadcom营收利润, 微软/亚马逊/谷歌/Meta营收及CapEx, 四大云厂商合计AI基础设施投资", "category": "macro"},
    "cb":  {"label": "A-Share Concept Boards",        "provider": "AKShare (东方财富概念板块)",       "key_required": False, "description": "A股概念板块指数: 光通信/CPO/算力/数据中心/液冷/AI芯片/存储芯片/国产芯片/汽车芯片/半导体/小金属/磷化工/PCB (13个板块)", "category": "macro"},
    "aml":  {"label": "AML/CFT Country Risk Ratings", "provider": "FATF + US State Dept + Basel Institute", "key_required": False, "description": "反洗钱国家风险评级: FATF黑/灰名单(26国), 美国INCSR洗钱关注国(81国), Basel AML指数综合评分(65国)", "category": "country_risk"},
    "sanctions": {"label": "Sanctions & Corruption", "provider": "OFAC + EU FSF + UN SC + Transparency International", "key_required": False, "description": "制裁与腐败: OFAC SDN制裁名单(美国), EU FSF欧盟金融制裁(5,892条), UN SC联合国安理会制裁(1,010条), 按国家聚合制裁数量, TI腐败感知指数CPI(180国评分排名)", "category": "country_risk"},
    "name_screening": {"label": "Name Screening (中英文)", "provider": "OpenSanctions + GDELT + 阿里云法院", "key_required": False, "description": "名称筛查: OpenSanctions制裁+PEP数据库(440K+实体,含中文名), GDELT全球负面新闻, 阿里云信数科技中国法院涉诉(失信/被执行/裁判文书), 中英文模糊匹配+拼音跨文字搜索", "category": "name_screening"},
    "energy": {"label": "Energy / EIA",          "provider": "U.S. Energy Information Admin", "key_required": True,  "description": "WTI原油价格, Henry Hub天然气价格", "category": "macro"},
}

TOOLS = [
    {
        "name": "data_sources",
        "description": "List all 25 data sources with metadata: provider, whether an API key is required, and description of what data each source provides. Start here to understand the full scope of available data.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_indicators",
        "description": "List all available macroeconomic indicators. Returns id, source, name, description, frequency, and last_updated for each indicator.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "Filter by source: us, cn, global_, hk, jp, euro, uk, de, au, ca, ch, bond, futures, shipping, banks, alt, llm, defi, energy, ai, ai_co, cb, aml, sanctions, name_screening (optional)",
                },
            },
        },
    },
    {
        "name": "get_indicator",
        "description": "Get detailed metadata for a single indicator by its ID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer", "description": "Indicator ID from list_indicators"},
            },
            "required": ["id"],
        },
    },
    {
        "name": "query_data",
        "description": "Query time-series observations for an indicator. Returns date and value pairs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer", "description": "Indicator ID"},
                "start": {"type": "string", "description": "Start date (YYYY-MM-DD, optional)"},
                "end": {"type": "string", "description": "End date (YYYY-MM-DD, optional)"},
                "limit": {"type": "integer", "description": "Max rows to return (default 100)", "default": 100},
            },
            "required": ["id"],
        },
    },
    {
        "name": "search_indicators",
        "description": "Search indicators by keyword in name, description, or source.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keyword (e.g. 'GDP', 'CPI', 'China')"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "trigger_fetch",
        "description": "Trigger a data refresh from upstream sources. Can filter by source (us, cn, global_, hk, jp, euro, uk, de, au, ca, ch, bond, futures, shipping, banks, alt, llm, defi, energy, ai, ai_co, cb, aml, sanctions, name_screening).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "Source to fetch (optional, all if omitted)"},
            },
        },
    },
    {
        "name": "get_health",
        "description": "Get database health status — total indicators, observations, and breakdown by source with descriptions.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "search_name",
        "description": "Comprehensive name screening against sanctions lists, PEP database, negative news, and Chinese court records. Supports both Chinese (中文) and English names with fuzzy matching and cross-script search (Chinese→Pinyin→English and vice versa). Returns matches categorized by risk: sanctions, PEP, and other. Optionally includes negative news from GDELT and Chinese court records.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Name to screen (Chinese or English)"},
                "include_news": {"type": "boolean", "description": "Also search GDELT for negative news (default false)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "name_screening_stats",
        "description": "Get name screening database statistics: total records, breakdown by source and risk category, PEP count, Chinese name coverage.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_tags",
        "description": "List all tags with indicator counts. Browse data by topic (通胀, 就业, AI算力, 数据中心, DeFi...) without knowing exact keywords. Use this to discover available data categories.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "data_sources_by_category",
        "description": "List all data sources grouped by three categories: macro (22 sources — US, China, Eurozone, Japan, A-share concept boards, etc.), country_risk (AML ratings, sanctions, CPI), and name_screening (OpenSanctions PEP/sanctions database with Chinese+English fuzzy search). Use this for a structured overview of the entire data platform.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_risk_indicators",
        "description": "List all country risk indicators — AML/CFT ratings (FATF, INCSR, Basel), sanctions (OFAC SDN by country), and corruption perception (TI CPI). These are time-series indicators in the AML and sanctions sources.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "Filter by risk source: aml, sanctions (optional, returns all if omitted)",
                },
            },
        },
    },
]


def _log(msg: str) -> None:
    """Log to stderr (stdout is reserved for JSON-RPC)."""
    print(f"[eco-data-mcp] {msg}", file=sys.stderr, flush=True)


# ── Tool handlers ─────────────────────────────────────────────


def handle_list_indicators(args: dict) -> list:
    conn = init_db()
    try:
        source = args.get("source")
        df = get_indicators(conn, source=source)
        result = df.to_dict(orient="records")
        for r in result:
            for k, v in r.items():
                if hasattr(v, "isoformat"):
                    r[k] = v.isoformat()
                elif str(type(v)) == "<class 'numpy.int64'>":
                    r[k] = int(v)
        return result
    finally:
        conn.close()


def handle_get_indicator(args: dict) -> dict:
    conn = init_db()
    try:
        row = get_indicator(conn, args["id"])
        if row is None:
            return {"error": f"Indicator {args['id']} not found"}
        for k, v in row.items():
            if hasattr(v, "isoformat"):
                row[k] = v.isoformat()
            elif str(type(v)) == "<class 'numpy.int64'>":
                row[k] = int(v)
        return row
    finally:
        conn.close()


def handle_query_data(args: dict) -> dict:
    conn = init_db()
    try:
        ind_id = args["id"]
        meta = get_indicator(conn, ind_id)
        if meta is None:
            return {"error": f"Indicator {ind_id} not found"}
        df = get_data(conn, ind_id, start=args.get("start"), end=args.get("end"), limit=args.get("limit", 100))
        records = []
        for _, row in df.iterrows():
            records.append({"date": str(row["date"]), "value": row["value"]})
        return {"indicator": meta["name"], "source": meta["source"], "count": len(records), "data": records}
    finally:
        conn.close()


def handle_search_indicators(args: dict) -> list:
    conn = init_db()
    try:
        df = search_indicators(conn, args["query"])
        result = df.to_dict(orient="records")
        for r in result:
            for k, v in r.items():
                if hasattr(v, "isoformat"):
                    r[k] = v.isoformat()
                elif str(type(v)) == "<class 'numpy.int64'>":
                    r[k] = int(v)
        return result
    finally:
        conn.close()


def handle_trigger_fetch(args: dict) -> dict:
    import os
    source = args.get("source")
    summary = run_once(
        fred_api_key=os.environ.get("FRED_API_KEY", ""),
        eia_api_key=os.environ.get("EIA_API_KEY", ""),
        sources=[source] if source else None,
    )
    return summary


def handle_data_sources(_args: dict) -> list:
    return [
        {
            "id": key,
            "label": meta["label"],
            "provider": meta["provider"],
            "key_required": meta["key_required"],
            "category": meta.get("category", "macro"),
            "description": meta["description"],
        }
        for key, meta in SOURCE_META.items()
    ]


def handle_get_health(_args: dict) -> dict:
    conn = init_db()
    try:
        count = conn.execute("SELECT COUNT(*) FROM indicators").fetchone()[0]
        obs_count = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
        by_source = conn.execute(
            "SELECT source, COUNT(*) as cnt FROM indicators GROUP BY source ORDER BY cnt DESC"
        ).fetchall()
        return {
            "status": "ok",
            "indicators": count,
            "observations": obs_count,
            "sources": [
                {"id": r[0], "label": SOURCE_META.get(r[0], {}).get("label", r[0]),
                 "count": r[1], "description": SOURCE_META.get(r[0], {}).get("description", "")}
                for r in by_source
            ],
        }
    finally:
        conn.close()


def handle_list_tags(_args: dict) -> list:
    conn = init_db()
    try:
        return get_all_tags(conn)
    finally:
        conn.close()


def handle_data_sources_by_category(_args: dict) -> dict:
    """List all data sources grouped by three categories."""
    from app.categories import DataCategory, sources_by_category, category_label
    result = {}
    for cat in DataCategory:
        srcs = sources_by_category(cat)
        result[cat.value] = {
            "label": category_label(cat),
            "label_en": category_label(cat, en=True),
            "sources": [
                {
                    "id": s,
                    "label": SOURCE_META.get(s, {}).get("label", s),
                    "description": SOURCE_META.get(s, {}).get("description", ""),
                }
                for s in srcs
            ],
        }
    return result


def handle_list_risk_indicators(args: dict) -> list:
    """List country risk indicators (AML, sanctions, CPI)."""
    from app.categories import DataCategory, sources_by_category
    conn = init_db()
    try:
        risk_sources = sources_by_category(DataCategory.COUNTRY_RISK)
        source = args.get("source")
        if source:
            if source not in risk_sources:
                return [{"error": f"Unknown risk source: {source}. Available: {risk_sources}"}]
            df = get_indicators(conn, source=source)
        else:
            df = get_indicators(conn, sources=risk_sources)
        result = df.to_dict(orient="records")
        for r in result:
            for k, v in r.items():
                if hasattr(v, "isoformat"):
                    r[k] = v.isoformat()
                elif str(type(v)) == "<class 'numpy.int64'>":
                    r[k] = int(v)
        return result
    finally:
        conn.close()


def handle_search_name(args: dict) -> dict:
    """Comprehensive name screening."""
    from app.eco_harness.name_screening import NameScreeningHarness
    nsh = NameScreeningHarness()
    return nsh.screen(args["query"], include_news=args.get("include_news", False))


def handle_name_screening_stats(_args: dict) -> dict:
    """Get name screening database statistics."""
    from app.eco_harness.name_screening import NameScreeningHarness
    nsh = NameScreeningHarness()
    return nsh.get_stats()


HANDLERS = {
    "data_sources": handle_data_sources,
    "list_indicators": handle_list_indicators,
    "get_indicator": handle_get_indicator,
    "query_data": handle_query_data,
    "search_indicators": handle_search_indicators,
    "trigger_fetch": handle_trigger_fetch,
    "get_health": handle_get_health,
    "search_name": handle_search_name,
    "name_screening_stats": handle_name_screening_stats,
    "list_tags": handle_list_tags,
    "data_sources_by_category": handle_data_sources_by_category,
    "list_risk_indicators": handle_list_risk_indicators,
}


# ── JSON-RPC ──────────────────────────────────────────────────


def _rpc_response(req_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _rpc_error(req_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def handle_request(req: dict) -> Optional[dict]:
    method = req.get("method", "")
    req_id = req.get("id")

    if method == "initialize":
        return _rpc_response(req_id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {
                "name": SERVER_NAME,
                "version": SERVER_VERSION,
                "description": "Global economic intelligence platform — three data tiers: "
                               "MACRO (22 sources: US/FRED, China/AKShare, Eurozone, UK, Germany, Japan, "
                               "Australia, Canada, Switzerland, Hong Kong, World Bank, bond & futures, "
                               "shipping, central bank rates, alternative, LLM ecosystem, DeFi, energy/EIA, "
                               "AI infrastructure, AI company financials, A-share concept boards), "
                               "COUNTRY RISK (FATF/INCSR/Basel AML ratings, OFAC sanctions, TI CPI), "
                               "NAME SCREENING (OpenSanctions 383K entities, Chinese+English fuzzy search). "
                               "Use data_sources_by_category for structured overview, data_sources or "
                               "get_health for details, then list_indicators to browse.",
            },
        })

    if method == "notifications/initialized":
        return None  # no response for notifications

    if method == "tools/list":
        return _rpc_response(req_id, {"tools": TOOLS})

    if method == "tools/call":
        params = req.get("params", {})
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})
        handler = HANDLERS.get(tool_name)
        if handler is None:
            return _rpc_error(req_id, -32601, f"Unknown tool: {tool_name}")
        try:
            result = handler(tool_args)
            # Format as MCP tool result
            return _rpc_response(req_id, {
                "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}],
            })
        except Exception as e:
            return _rpc_error(req_id, -32603, str(e))

    if method == "ping":
        return _rpc_response(req_id, {})

    return _rpc_error(req_id, -32601, f"Unknown method: {method}")


# ── Main loop ─────────────────────────────────────────────────


def main() -> None:
    """Run the MCP server — reads JSON-RPC from stdin, writes to stdout."""
    _log(f"{SERVER_NAME} v{SERVER_VERSION} starting...")
    _log(f"Available tools: {list(HANDLERS.keys())}")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            _log(f"Invalid JSON: {e}")
            continue

        _log(f"<- {req.get('method', '?')}")
        resp = handle_request(req)
        if resp is not None:
            _log(f"-> response (id={resp.get('id')})")
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
