"""
LLM Tagger — Multi-provider LLM integration for stock extraction and sentiment analysis.

Reuses the multi-provider LLM pattern from cn_stock/tagging.py.
Extracts stock tickers/names from post text and classifies sentiment.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("kol_thermometer.llm_tagger")

# ── Provider registry ─────────────────────────────────────────

PROVIDERS = [
    {
        "name": "deepseek",
        "style": "openai",
        "model": "deepseek-chat",
        "base_url": "https://api.deepseek.com",
        "env_var": "DEEPSEEK_API_KEY",
    },
    {
        "name": "anthropic",
        "style": "anthropic",
        "model": "claude-sonnet-4-6",
        "base_url": None,
        "env_var": "ANTHROPIC_API_KEY",
    },
    {
        "name": "qwen",
        "style": "openai",
        "model": "qwen-plus",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "env_var": "QWEN_API_KEY",
    },
]


def _get_active_provider() -> Optional[dict]:
    """Return the first provider with a configured API key, or None."""
    from kol_thermometer import config
    for p in PROVIDERS:
        key = getattr(config, p["env_var"], "")
        if key:
            return {**p, "api_key": key}
    return None


def _call_llm_openai(provider: dict, system_prompt: str, user_prompt: str) -> str:
    """Call an OpenAI-compatible API (DeepSeek, Qwen)."""
    from openai import OpenAI
    client = OpenAI(api_key=provider["api_key"], base_url=provider["base_url"])
    resp = client.chat.completions.create(
        model=provider["model"],
        temperature=0.1,
        max_tokens=2048,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    return resp.choices[0].message.content or ""


def _call_llm_anthropic(provider: dict, system_prompt: str, user_prompt: str) -> str:
    """Call Anthropic Claude API."""
    from anthropic import Anthropic
    client = Anthropic(api_key=provider["api_key"])
    resp = client.messages.create(
        model=provider["model"],
        max_tokens=2048,
        temperature=0.1,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return resp.content[0].text


def _call_llm(system_prompt: str, user_prompt: str) -> Optional[str]:
    """Route to the first available LLM provider. Returns response text or None."""
    provider = _get_active_provider()
    if not provider:
        logger.warning("No LLM API key configured. Skipping.")
        return None
    try:
        if provider["style"] == "openai":
            text = _call_llm_openai(provider, system_prompt, user_prompt)
        else:
            text = _call_llm_anthropic(provider, system_prompt, user_prompt)
        logger.debug(f"LLM call OK via {provider['name']} ({provider['model']})")
        return text
    except Exception as e:
        logger.error(f"LLM call failed ({provider['name']}): {e}")
        return None


def _extract_json(text: str) -> str:
    """Strip markdown code fences from LLM response."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.startswith("```")]
        text = "\n".join(lines)
    return text


# ── Public API ────────────────────────────────────────────────

def extract_stock_mentions(posts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Extract stock mentions + sentiment from a batch of posts via LLM.

    Each post dict should have: id (post_id), title, content, platform, kol_id

    Returns list of mention dicts:
        [{post_id, kol_id, stock_code, stock_name, market, mention_context,
          sentiment_score, sentiment_label, confidence}]
    """
    if not posts:
        return []

    # Build batch prompt
    posts_text = []
    for i, p in enumerate(posts):
        title = (p.get("title") or "")[:200]
        content = (p.get("content") or "")
        if content:
            content = content[:500]
        text = f"{title} {content}".strip()[:600]
        posts_text.append(f"[{i}] platform={p.get('platform', '')} | {text}")

    combined = "\n\n".join(posts_text)

    system_prompt = (
        "You are a global financial analyst assistant. Extract ALL stock/ETF/crypto "
        "mentions from social media posts across worldwide markets and classify sentiment. "
        "Return strict JSON only."
    )

    user_prompt = f"""Analyze the following social media posts from global financial communities. For each post, extract ALL stock tickers, company names, ETFs, or crypto assets mentioned, and classify the sentiment toward each.

For each mention, provide:
- post_index: the [N] index of the post
- stock_code: the ticker symbol or asset code (e.g., AAPL, 005930, BTC, SPY)
- stock_name: full company/asset name if known
- market: US, CN, HK, JP, KR, EU, IN, AU, Crypto, or unknown
- mention_context: the sentence or phrase mentioning the stock (max 100 chars)
- sentiment_score: -1.0 (strongly bearish) to +1.0 (strongly bullish), 0 = neutral
- sentiment_label: "positive", "negative", or "neutral"
- confidence: 0.0 to 1.0

Ticker formats by market:
- US: 1-5 letter tickers (AAPL, TSLA, MSFT, AMZN, GOOGL)
- CN (A-share): 6-digit codes (000768, 600519)
- HK: 4-5 digit codes (0700, 09988, 0005)
- JP: 4-digit codes (7203=Toyota, 9984=SoftBank, 6758=Sony)
- KR: 6-digit codes (005930=Samsung, 000660=SK Hynix)
- EU: ticker with exchange suffix (MC.PA, SAP.DE, HSBA.L, VOW3.DE)
- IN: NSE/BSE symbols (RELIANCE, TCS, INFY, HDFC)
- AU: 3-letter ASX codes (BHP, CBA, FMG, WBC)
- Crypto: BTC, ETH, SOL, XRP, DOGE, etc.
- ETFs: SPY, QQQ, IWM, ARKK, EEM, VWO, FXI, EWJ, EWY, INDA

Multilingual sentiment patterns:
- Bullish/Positive: "buy", "long", "bullish", "moon", "rocket", "看涨", "买入", "買い", "매수", "longieren", "acheter"
- Bearish/Negative: "sell", "short", "bearish", "crash", "dump", "看跌", "卖出", "売り", "매도", "shorten", "vendre"
- Sarcasm detection: if the post is clearly sarcastic or ironic, flip the sentiment

Posts:
{combined}

Return a JSON array:
[{{"post_index": 0, "stock_code": "TSLA", "stock_name": "Tesla", "market": "US", "mention_context": "...", "sentiment_score": 0.8, "sentiment_label": "positive", "confidence": 0.95}}]

Return empty array [] if no specific stocks/assets are mentioned."""
    text = _call_llm(system_prompt, user_prompt)
    if not text:
        return []

    try:
        result = json.loads(_extract_json(text))
        mentions = []
        for m in result:
            idx = m.get("post_index", 0)
            if idx < len(posts):
                post = posts[idx]
                mentions.append({
                    "post_id": post.get("id"),
                    "kol_id": post.get("kol_id"),
                    "stock_code": str(m.get("stock_code", "")).upper(),
                    "stock_name": m.get("stock_name", ""),
                    "market": m.get("market", "unknown"),
                    "mention_context": m.get("mention_context", ""),
                    "sentiment_score": float(m.get("sentiment_score", 0)),
                    "sentiment_label": m.get("sentiment_label", "neutral"),
                    "confidence": float(m.get("confidence", 0.5)),
                })
        logger.info(f"LLM extracted {len(mentions)} stock mentions from {len(posts)} posts")
        return mentions
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        logger.error(f"Failed to parse LLM response: {e}\nResponse: {text[:500]}")
        return []


def needs_llm() -> bool:
    """Check if at least one LLM provider is configured."""
    return _get_active_provider() is not None


def active_provider() -> Optional[str]:
    """Return the name of the active LLM provider, or None."""
    p = _get_active_provider()
    return p["name"] if p else None
