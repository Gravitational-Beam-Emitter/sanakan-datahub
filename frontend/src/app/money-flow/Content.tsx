"use client";

import { useState } from "react";
import type { AuctionStock, FundFlowStock, FundFlowSector } from "@/lib/api";
import { fetchAuctionStocks, fetchAuctionSectors, fetchFundFlowStocks, fetchFundFlowSectors } from "@/lib/api";

type Tab = "auction" | "fund-flow";

export default function MoneyFlowContent({
  initialAuction,
  initialInflowStocks,
  initialOutflowStocks,
  initialSectors,
}: {
  initialAuction: AuctionStock[];
  initialInflowStocks: FundFlowStock[];
  initialOutflowStocks: FundFlowStock[];
  initialSectors: FundFlowSector[];
}) {
  const [tab, setTab] = useState<Tab>("auction");

  // Auction state
  const [auctionStocks, setAuctionStocks] = useState(initialAuction);
  const [auctionSectors, setAuctionSectors] = useState<{ sector: string; avg_rush_score: number; rush_stocks_count: number }[]>([]);
  const [minGap, setMinGap] = useState(0);

  // Fund flow state
  const [inflowStocks, setInflowStocks] = useState(initialInflowStocks);
  const [outflowStocks, setOutflowStocks] = useState(initialOutflowStocks);
  const [sectors, setSectors] = useState(initialSectors);
  const [secType, setSecType] = useState("行业资金流");

  const loadAuctionSectors = async () => {
    const d = await fetchAuctionSectors({ limit: 30 });
    setAuctionSectors(d.sectors);
  };

  const handleMinGapChange = async (gap: number) => {
    setMinGap(gap);
    const d = await fetchAuctionStocks({ min_gap: gap, limit: 50 });
    setAuctionStocks(d.stocks);
  };

  const handleSecTypeChange = async (st: string) => {
    setSecType(st);
    const [sIn, sOut] = await Promise.all([
      fetchFundFlowSectors({ sector_type: st, direction: "inflow", limit: 20 }),
      fetchFundFlowSectors({ sector_type: st, direction: "outflow", limit: 20 }),
    ]);
    setSectors(sIn.sectors);
  };

  const formatVolume = (v: number) => {
    if (v >= 1e8) return `${(v / 1e8).toFixed(1)}亿`;
    if (v >= 1e4) return `${(v / 1e4).toFixed(0)}万`;
    return String(v);
  };

  const formatMoney = (v: number) => {
    const abs = Math.abs(v);
    const sign = v >= 0 ? "+" : "-";
    if (abs >= 1e8) return `${sign}${(abs / 1e8).toFixed(2)}亿`;
    if (abs >= 1e4) return `${sign}${(abs / 1e4).toFixed(0)}万`;
    return `${sign}${abs.toFixed(0)}`;
  };

  return (
    <div className="flex flex-col gap-4">
      {/* Tabs */}
      <div className="flex gap-1 bg-surface rounded-lg p-1 w-fit">
        {([
          ["auction", "竞价抢筹"],
          ["fund-flow", "资金流向"],
        ] as [Tab, string][]).map(([t, label]) => (
          <button
            key={t}
            onClick={() => {
              setTab(t);
              if (t === "auction") loadAuctionSectors();
            }}
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

      {/* ── Auction Tab ── */}
      {tab === "auction" && (
        <div className="grid gap-6">
          {/* Filter */}
          <div className="flex gap-2">
            {[0, 1, 2, 3, 5].map((gap) => (
              <button
                key={gap}
                onClick={() => handleMinGapChange(gap)}
                className={`px-3 py-1 rounded text-xs transition-all ${
                  minGap === gap ? "bg-primary-a15 text-primary" : "text-muted hover:text-ink"
                }`}
              >
                {gap === 0 ? "全部" : `竞价涨幅≥${gap}%`}
              </button>
            ))}
          </div>

          {/* Rush stocks leaderboard */}
          <div>
            <h3 className="text-sm font-medium mb-2">抢筹评分排行 🏃</h3>
            <div className="grid gap-1.5">
              {auctionStocks.length === 0 && (
                <p className="text-sm text-muted py-10 text-center">
                  暂无数据 · 请先运行 python3 -m a_share_money_flow.pipeline
                </p>
              )}
              {auctionStocks.slice(0, 30).map((s, i) => (
                <div
                  key={s.code}
                  className="flex items-center gap-3 p-2 rounded-lg bg-surface hover:ring-1 hover:ring-border transition-all text-sm"
                >
                  <span className="text-xs text-muted w-5">{i + 1}</span>
                  <div className="flex-1 min-w-0">
                    <span className="font-medium truncate">{s.name}</span>
                    <span className="text-xs text-muted ml-2">{s.code}</span>
                  </div>
                  <span className={`text-xs font-medium ${s.gap_pct >= 0 ? "text-red-400" : "text-green-400"}`}>
                    {s.gap_pct >= 0 ? "+" : ""}{s.gap_pct.toFixed(1)}%
                  </span>
                  <span className="text-xs text-muted w-16 text-right">
                    {formatVolume(s.amount)}
                  </span>
                  <span className={`text-sm font-bold w-12 text-right ${
                    s.rush_score >= 60 ? "text-red-400" : s.rush_score >= 40 ? "text-orange-400" : "text-muted"
                  }`}>
                    {s.rush_score.toFixed(0)}
                  </span>
                </div>
              ))}
            </div>
          </div>

          {/* Sector rush rankings */}
          {auctionSectors.length > 0 && (
            <div>
              <h3 className="text-sm font-medium mb-2">板块抢筹排行 📊</h3>
              <div className="grid gap-1.5">
                {auctionSectors.slice(0, 15).map((s) => (
                  <div
                    key={s.sector}
                    className="flex items-center gap-3 p-2 rounded-lg bg-surface text-sm"
                  >
                    <span className="font-medium flex-1">{s.sector}</span>
                    <span className="text-xs text-muted">{s.rush_stocks_count}只抢筹</span>
                    <span className={`text-sm font-bold ${
                      s.avg_rush_score >= 50 ? "text-red-400" : "text-orange-400"
                    }`}>
                      {s.avg_rush_score.toFixed(0)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── Fund Flow Tab ── */}
      {tab === "fund-flow" && (
        <div className="grid gap-6">
          {/* Sector type switch */}
          <div className="flex gap-2">
            {["行业资金流", "概念资金流"].map((st) => (
              <button
                key={st}
                onClick={() => handleSecTypeChange(st)}
                className={`px-3 py-1 rounded text-xs transition-all ${
                  secType === st ? "bg-primary-a15 text-primary" : "text-muted hover:text-ink"
                }`}
              >
                {st.replace("资金流", "")}
              </button>
            ))}
          </div>

          {/* Sector fund flow */}
          <div>
            <h3 className="text-sm font-medium mb-2 text-red-400">
              板块主力净流入 TOP {secType.replace("资金流", "")}
            </h3>
            <div className="grid gap-1.5">
              {sectors.length === 0 && (
                <p className="text-sm text-muted py-5 text-center">暂无数据</p>
              )}
              {sectors.slice(0, 15).map((s) => (
                <div
                  key={s.sector_name}
                  className="flex items-center gap-3 p-2 rounded-lg bg-surface text-sm"
                >
                  <div className="flex-1 min-w-0">
                    <span className="font-medium">{s.sector_name}</span>
                    <span className="text-xs text-muted ml-2">龙头: {s.top_stock}</span>
                  </div>
                  <span className={`text-xs ${s.change_pct >= 0 ? "text-red-400" : "text-green-400"}`}>
                    {s.change_pct >= 0 ? "+" : ""}{s.change_pct?.toFixed(1)}%
                  </span>
                  <span className={`text-xs font-medium ${s.main_inflow > 0 ? "text-red-400" : "text-green-400"}`}>
                    {formatMoney(s.main_inflow)}
                  </span>
                </div>
              ))}
            </div>
          </div>

          {/* Stock fund flow: Inflow + Outflow side by side */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <h3 className="text-sm font-medium mb-2 text-red-400">个股主力净流入 TOP</h3>
              <div className="grid gap-1.5">
                {inflowStocks.slice(0, 15).map((s) => (
                  <div
                    key={s.code}
                    className="flex items-center gap-2 p-2 rounded-lg bg-surface text-sm"
                  >
                    <span className="font-medium truncate flex-1">{s.name}</span>
                    <span className="text-xs text-muted">{s.code}</span>
                    <span className={`text-xs ${s.change_pct >= 0 ? "text-red-400" : "text-green-400"}`}>
                      {s.change_pct >= 0 ? "+" : ""}{s.change_pct?.toFixed(1)}%
                    </span>
                    <span className="text-xs font-medium text-red-400">
                      {formatMoney(s.main_inflow)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
            <div>
              <h3 className="text-sm font-medium mb-2 text-green-400">个股主力净流出 TOP</h3>
              <div className="grid gap-1.5">
                {outflowStocks.slice(0, 15).map((s) => (
                  <div
                    key={s.code}
                    className="flex items-center gap-2 p-2 rounded-lg bg-surface text-sm"
                  >
                    <span className="font-medium truncate flex-1">{s.name}</span>
                    <span className="text-xs text-muted">{s.code}</span>
                    <span className={`text-xs ${s.change_pct >= 0 ? "text-red-400" : "text-green-400"}`}>
                      {s.change_pct >= 0 ? "+" : ""}{s.change_pct?.toFixed(1)}%
                    </span>
                    <span className="text-xs font-medium text-green-400">
                      {formatMoney(s.main_inflow)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      <footer className="text-center text-xs text-muted py-4 border-t border-border">
        <p>
          A股资金流向 & 竞价抢筹 — 数据源: 东方财富 via AKShare · 竞价数据9:28采集 · 资金流数据14:57采集
        </p>
      </footer>
    </div>
  );
}
