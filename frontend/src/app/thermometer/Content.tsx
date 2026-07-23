"use client";

import { useState, useEffect } from "react";
import type { ThermometerStock, KolItem, KolThermometerStats, StockMention } from "@/lib/api";
import { fetchThermometerStock, fetchThermometer } from "@/lib/api";

type Tab = "hot" | "kols" | "momentum" | "detail";

export default function ThermometerContent({
  initialStocks,
  initialKols,
  stats,
}: {
  initialStocks: ThermometerStock[];
  initialKols: KolItem[];
  stats: KolThermometerStats | null;
}) {
  const [tab, setTab] = useState<Tab>("hot");
  const [stocks, setStocks] = useState(initialStocks);
  const [market, setMarket] = useState("");

  // Detail view state
  const [selectedStock, setSelectedStock] = useState<string>("");
  const [stockDetail, setStockDetail] = useState<{
    thermometer_history: ThermometerStock[];
    recent_mentions: StockMention[];
  } | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  useEffect(() => {
    if (market) {
      fetchThermometer({ market, limit: 50 }).then((d) => {
        if (d.stocks.length > 0) setStocks(d.stocks);
      });
    } else {
      setStocks(initialStocks);
    }
  }, [market, initialStocks]);

  const handleStockClick = async (code: string) => {
    setSelectedStock(code);
    setTab("detail");
    setDetailLoading(true);
    const d = await fetchThermometerStock(code, 14);
    setStockDetail(d);
    setDetailLoading(false);
  };

  const heatColor = (score: number) => {
    if (score >= 70) return "text-red-500";
    if (score >= 50) return "text-orange-400";
    if (score >= 30) return "text-yellow-400";
    return "text-muted";
  };

  const heatBg = (score: number) => {
    if (score >= 70) return "bg-red-500/10";
    if (score >= 50) return "bg-orange-400/10";
    if (score >= 30) return "bg-yellow-400/10";
    return "bg-surface-hover";
  };

  const sentimentEmoji = (bias: number) => {
    if (bias > 0.3) return "🔥";
    if (bias > 0.1) return "☀️";
    if (bias < -0.3) return "🧊";
    if (bias < -0.1) return "☁️";
    return "➖";
  };

  const tierBadge = (tier: string) => {
    const colors: Record<string, string> = {
      S: "bg-yellow-400/20 text-yellow-400",
      A: "bg-green-400/20 text-green-400",
      B: "bg-blue-400/20 text-blue-400",
      C: "bg-gray-400/20 text-gray-400",
      D: "bg-muted/20 text-muted",
    };
    return (
      <span className={`text-xs px-1.5 py-0.5 rounded font-medium ${colors[tier] || colors.D}`}>
        {tier}
      </span>
    );
  };

  return (
    <div className="flex flex-col gap-4">
      {/* Tab bar */}
      <div className="flex gap-1 bg-surface rounded-lg p-1 w-fit">
        {([
          ["hot", "热门股票"],
          ["momentum", "动量变化"],
          ["kols", "大V排行"],
        ] as [Tab, string][]).map(([t, label]) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-4 py-1.5 rounded-md text-sm transition-all ${
              tab === t
                ? "bg-primary-a15 text-primary font-medium"
                : "text-muted hover:text-ink"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Market filter */}
      {tab !== "detail" && (
        <div className="flex gap-2">
          {["", "US", "CN", "HK", "EU", "JP", "KR", "IN", "Crypto", "Global"].map((m) => (
            <button
              key={m}
              onClick={() => setMarket(m)}
              className={`px-3 py-1 rounded text-xs transition-all ${
                market === m ? "bg-primary-a15 text-primary" : "text-muted hover:text-ink"
              }`}
            >
              {m || "全部"}
            </button>
          ))}
        </div>
      )}

      {/* Tab content */}
      {tab === "hot" && (
        <div className="grid gap-2">
          {stocks.length === 0 && (
            <p className="text-sm text-muted py-10 text-center">
              暂无数据 · 请先配置 API keys 并运行 pipeline
            </p>
          )}
          {stocks.map((s, i) => (
            <button
              key={s.stock_code}
              onClick={() => handleStockClick(s.stock_code)}
              className={`flex items-center gap-3 p-3 rounded-lg text-left transition-all hover:ring-1 hover:ring-border ${heatBg(s.heat_score)}`}
            >
              <span className="text-xs text-muted w-5">{i + 1}</span>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-medium text-sm truncate">
                    {s.stock_name || s.stock_code}
                  </span>
                  <span className="text-xs text-muted">{s.stock_code}</span>
                  <span className="text-xs text-muted">{s.market}</span>
                </div>
                <div className="flex gap-3 text-xs text-muted mt-0.5">
                  <span>{s.mention_count} 次提及</span>
                  <span>{s.unique_kols} 位大V</span>
                </div>
              </div>
              <div className="text-right">
                <div className={`text-lg font-bold ${heatColor(s.heat_score)}`}>
                  {s.heat_score.toFixed(0)}°
                </div>
                <div className="text-xs text-muted">
                  {sentimentEmoji(s.sentiment_bias)}{" "}
                  {s.momentum !== 0 && (
                    <span className={s.momentum > 0 ? "text-red-400" : "text-green-400"}>
                      {s.momentum > 0 ? "+" : ""}{s.momentum.toFixed(1)}
                    </span>
                  )}
                </div>
              </div>
            </button>
          ))}
        </div>
      )}

      {tab === "momentum" && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <h3 className="text-sm font-medium mb-2 text-red-400">🔥 热度上升</h3>
            <div className="grid gap-2">
              {stocks
                .filter((s) => s.momentum > 0)
                .sort((a, b) => b.momentum - a.momentum)
                .slice(0, 15)
                .map((s) => (
                  <button
                    key={s.stock_code}
                    onClick={() => handleStockClick(s.stock_code)}
                    className="flex items-center gap-3 p-2 rounded-lg text-left hover:bg-surface-hover transition-all"
                  >
                    <span className="text-sm truncate flex-1">
                      {s.stock_name || s.stock_code}
                    </span>
                    <span className="text-xs text-muted">{s.heat_score.toFixed(0)}°</span>
                    <span className="text-xs text-red-400 font-medium">
                      +{s.momentum.toFixed(1)}
                    </span>
                  </button>
                ))}
            </div>
          </div>
          <div>
            <h3 className="text-sm font-medium mb-2 text-green-400">❄️ 热度下降</h3>
            <div className="grid gap-2">
              {stocks
                .filter((s) => s.momentum < 0)
                .sort((a, b) => a.momentum - b.momentum)
                .slice(0, 15)
                .map((s) => (
                  <button
                    key={s.stock_code}
                    onClick={() => handleStockClick(s.stock_code)}
                    className="flex items-center gap-3 p-2 rounded-lg text-left hover:bg-surface-hover transition-all"
                  >
                    <span className="text-sm truncate flex-1">
                      {s.stock_name || s.stock_code}
                    </span>
                    <span className="text-xs text-muted">{s.heat_score.toFixed(0)}°</span>
                    <span className="text-xs text-green-400 font-medium">
                      {s.momentum.toFixed(1)}
                    </span>
                  </button>
                ))}
            </div>
          </div>
        </div>
      )}

      {tab === "kols" && (
        <div className="grid gap-2">
          {initialKols.map((k) => (
            <div
              key={k.id}
              className="flex items-center gap-3 p-3 rounded-lg bg-surface hover:ring-1 hover:ring-border transition-all"
            >
              <span className="text-sm text-muted w-8">
                {k.platform === "reddit" ? "🤖" : k.platform === "youtube" ? "▶️" : k.platform === "twitter" ? "🐦" : k.platform === "weibo" ? "📢" : k.platform === "seekingalpha" ? "📈" : k.platform === "moomoo" ? "🐮" : k.platform === "stocktwits" ? "🐂" : k.platform === "finnhub" ? "📰" : "📊"}
              </span>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-medium text-sm truncate">
                    {k.display_name || k.username}
                  </span>
                  {tierBadge(k.tier)}
                </div>
                <div className="flex gap-3 text-xs text-muted mt-0.5">
                  <span>{k.platform}</span>
                  <span>{(k.followers / 1000).toFixed(1)}k 粉丝</span>
                  <span>{k.posts_per_week.toFixed(1)}帖/周</span>
                </div>
              </div>
              <div className="text-right text-xs text-muted">
                <div>评分 {k.total_score.toFixed(0)}</div>
                <div>权重 {k.base_weight.toFixed(2)}</div>
              </div>
            </div>
          ))}
        </div>
      )}

      {tab === "detail" && (
        <div>
          <button
            onClick={() => setTab("hot")}
            className="text-sm text-primary hover:underline mb-3 inline-block"
          >
            ← 返回列表
          </button>

          {detailLoading && <p className="text-sm text-muted py-10">加载中...</p>}

          {stockDetail && (
            <div className="grid gap-6">
              {/* Heat trend */}
              <div className="bg-surface rounded-lg p-4">
                <h3 className="text-sm font-medium mb-3">
                  {selectedStock} 热度趋势 (14天)
                </h3>
                <div className="flex items-end gap-1 h-24">
                  {stockDetail.thermometer_history
                    .slice()
                    .reverse()
                    .map((d, i) => (
                      <div
                        key={i}
                        className="flex-1 bg-primary-a15 rounded-t min-h-[2px]"
                        style={{ height: `${d.heat_score}%` }}
                        title={`${d.date}: ${d.heat_score.toFixed(0)}°`}
                      />
                    ))}
                </div>
                <div className="flex justify-between text-xs text-muted mt-2">
                  {stockDetail.thermometer_history.length > 0 && (
                    <>
                      <span>{stockDetail.thermometer_history[stockDetail.thermometer_history.length - 1]?.date}</span>
                      <span>{stockDetail.thermometer_history[0]?.date}</span>
                    </>
                  )}
                </div>
              </div>

              {/* Recent mentions */}
              <div>
                <h3 className="text-sm font-medium mb-2">最近提及</h3>
                <div className="grid gap-2">
                  {stockDetail.recent_mentions.length === 0 && (
                    <p className="text-xs text-muted">暂未提及</p>
                  )}
                  {stockDetail.recent_mentions.map((m, i) => (
                    <div
                      key={i}
                      className="p-3 rounded-lg bg-surface text-sm"
                    >
                      <div className="flex items-center gap-2 mb-1">
                        <span className="text-xs text-muted">
                          {m.platform === "reddit" ? "Reddit" : m.platform === "youtube" ? "YouTube" : m.platform === "twitter" ? "Twitter" : m.platform === "weibo" ? "Weibo" : m.platform === "seekingalpha" ? "Seeking Alpha" : m.platform === "moomoo" ? "Moomoo" : m.platform === "stocktwits" ? "StockTwits" : m.platform === "finnhub" ? "Finnhub" : m.platform}
                        </span>
                        <span className="text-xs font-medium">
                          {m.display_name || m.username}
                        </span>
                        {tierBadge(m.tier)}
                        <span
                          className={`text-xs ml-auto ${
                            m.sentiment_label === "positive"
                              ? "text-red-400"
                              : m.sentiment_label === "negative"
                              ? "text-green-400"
                              : "text-muted"
                          }`}
                        >
                          {m.sentiment_label === "positive" ? "Bullish" : m.sentiment_label === "negative" ? "Bearish" : "Neutral"}
                        </span>
                      </div>
                      <p className="text-xs text-muted line-clamp-2">
                        {m.mention_context || m.post_title}
                      </p>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      <footer className="text-center text-xs text-muted py-4 border-t border-border">
        <p>
          全球市场温度计 — 自动追踪 Reddit / YouTube / Twitter / Weibo / Seeking Alpha / Moomoo / StockTwits / Finnhub 全球金融市场讨论热度 · 数据仅供参考
        </p>
      </footer>
    </div>
  );
}
