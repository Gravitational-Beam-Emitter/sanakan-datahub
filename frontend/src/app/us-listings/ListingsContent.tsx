"use client";

import { useState, useEffect } from "react";
import type { NewListing, CryptoProduct, ListingSummary, CryptoStats } from "@/lib/api";
import {
  fetchInsiderTrades,
  fetchEarnings,
  fetchUpcomingEarnings,
  fetchHoldings,
  fetchShortInterest,
  fetchFtd,
  fetchEtfFlows,
  fetchDividends,
  fetchSplits,
  fetchSuspensions,
  fetchEnforcement,
  fetchThresholdSecurities,
  fetchAtsFilings,
  fetchShortActivity,
  fetchLockupExpiry,
  fetchOptionsFlow,
} from "@/lib/api";
import type {
  InsiderTrade,
  EarningsEntry,
  InstitutionalHolding,
  ShortInterestEntry,
  FtdEntry,
  EtfFlowEntry,
  Dividend,
  StockSplit,
  Suspension,
  EnforcementAction,
  ThresholdSecurity,
  AtsFiling,
  ShortActivity,
  LockupExpiry,
  OptionsFlowEntry,
} from "@/lib/api";

const TYPE_LABELS: Record<string, string> = {
  IPO: "IPO",
  "Direct Listing": "直接上市",
  SPAC: "SPAC",
  Upcoming: "即将上市",
};

const CRYPTO_TYPE_LABELS: Record<string, string> = {
  spot_etf: "现货ETF",
  futures_etf: "期货ETF",
  etp: "ETP",
  crypto_stock: "Crypto股票",
  blockchain: "区块链",
};

const CRYPTO_TYPE_COLORS: Record<string, string> = {
  spot_etf: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-400",
  futures_etf: "bg-teal-100 text-teal-800 dark:bg-teal-900/30 dark:text-teal-400",
  etp: "bg-sky-100 text-sky-800 dark:bg-sky-900/30 dark:text-sky-400",
  crypto_stock: "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400",
  blockchain: "bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-400",
};

const EXCHANGE_COLORS: Record<string, string> = {
  NYSE: "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400",
  NASDAQ: "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400",
};

const TRANSACTION_COLORS: Record<string, string> = {
  "P-Purchase": "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-400",
  "S-Sale": "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400",
};

function formatAmount(n: number | null): string {
  if (n == null) return "-";
  if (n >= 1e9) return `$${(n / 1e9).toFixed(1)}B`;
  if (n >= 1e6) return `$${(n / 1e6).toFixed(0)}M`;
  return `$${n.toLocaleString()}`;
}

function formatPrice(p: number | null): string {
  if (p == null) return "-";
  return `$${p.toFixed(2)}`;
}

function formatShares(n: number | null): string {
  if (n == null) return "-";
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(0)}K`;
  return n.toLocaleString();
}

function formatPct(n: number | null): string {
  if (n == null) return "-";
  return `${n.toFixed(2)}%`;
}

function LoadingRow({ cols }: { cols: number }) {
  return (
    <tr>
      {Array.from({ length: cols }).map((_, i) => (
        <td key={i} className="px-4 py-3">
          <div className="h-4 bg-surface-hover rounded animate-pulse" />
        </td>
      ))}
    </tr>
  );
}

// ═══════════════════════════════════════════════════════════
//  Existing sub-components
// ═══════════════════════════════════════════════════════════

function StatsBar({ summary }: { summary: ListingSummary }) {
  const stats = [
    { label: "本月上市", value: summary.total, sub: `${summary.tickers} 只股票` },
    { label: "IPO", value: summary.ipos },
    { label: "SPAC", value: summary.spacs },
    { label: "即将上市", value: summary.upcoming },
    { label: "Crypto相关", value: summary.crypto_count, highlight: summary.crypto_count > 0 },
  ];

  return (
    <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
      {stats.map((s) => (
        <div
          key={s.label}
          className={`glass rounded-xl px-4 py-3 text-center ${
            s.highlight ? "ring-1 ring-amber-400/50" : ""
          }`}
        >
          <div className="text-2xl font-bold text-ink">{s.value}</div>
          <div className="text-xs text-muted mt-0.5">{s.label}</div>
          {s.sub && <div className="text-[10px] text-muted">{s.sub}</div>}
        </div>
      ))}
    </div>
  );
}

function UpcomingBar({ upcoming }: { upcoming: NewListing[] }) {
  if (upcoming.length === 0) return null;

  return (
    <section>
      <h2 className="text-sm font-medium text-muted mb-3">
        即将上市 ({upcoming.length})
      </h2>
      <div className="glass rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-muted text-xs">
              <th className="text-left px-4 py-2.5 font-medium">Ticker</th>
              <th className="text-left px-4 py-2.5 font-medium">公司名称</th>
              <th className="text-left px-4 py-2.5 font-medium">预计日期</th>
              <th className="text-left px-4 py-2.5 font-medium">类型</th>
              <th className="text-left px-4 py-2.5 font-medium">交易所</th>
              <th className="text-right px-4 py-2.5 font-medium">预计发行价</th>
            </tr>
          </thead>
          <tbody>
            {upcoming.slice(0, 20).map((l) => (
              <tr key={l.id} className="border-b border-border/50 hover:bg-surface-hover">
                <td className="px-4 py-2.5 font-medium text-ink">{l.ticker}</td>
                <td className="px-4 py-2.5">{l.company_name}</td>
                <td className="px-4 py-2.5 text-muted">{l.listing_date?.slice(0, 10) || "-"}</td>
                <td className="px-4 py-2.5">
                  <span className="text-xs px-2 py-0.5 rounded-full bg-surface-hover text-muted">
                    {TYPE_LABELS[l.listing_type] || l.listing_type}
                  </span>
                </td>
                <td className="px-4 py-2.5">
                  {l.exchange ? (
                    <span className={`text-xs px-2 py-0.5 rounded-full ${EXCHANGE_COLORS[l.exchange] || ""}`}>
                      {l.exchange}
                    </span>
                  ) : "-"}
                </td>
                <td className="px-4 py-2.5 text-right">{formatPrice(l.offer_price)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function ListingsTable({ listings }: { listings: NewListing[] }) {
  return (
    <section>
      <h2 className="text-sm font-medium text-muted mb-3">
        近期上市 ({listings.length})
      </h2>
      <div className="glass rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-muted text-xs">
              <th className="text-left px-4 py-2.5 font-medium">Ticker</th>
              <th className="text-left px-4 py-2.5 font-medium">公司名称</th>
              <th className="text-left px-4 py-2.5 font-medium">上市日期</th>
              <th className="text-left px-4 py-2.5 font-medium">类型</th>
              <th className="text-left px-4 py-2.5 font-medium">交易所</th>
              <th className="text-right px-4 py-2.5 font-medium">发行价</th>
              <th className="text-right px-4 py-2.5 font-medium">发行规模</th>
              <th className="text-center px-4 py-2.5 font-medium">Crypto</th>
            </tr>
          </thead>
          <tbody>
            {listings.map((l) => (
              <tr key={l.id} className="border-b border-border/50 hover:bg-surface-hover">
                <td className="px-4 py-2.5 font-medium text-ink">{l.ticker}</td>
                <td className="px-4 py-2.5 max-w-[200px] truncate" title={l.company_name}>
                  {l.company_name}
                </td>
                <td className="px-4 py-2.5 text-muted">{l.listing_date?.slice(0, 10) || "-"}</td>
                <td className="px-4 py-2.5">
                  <span className="text-xs px-2 py-0.5 rounded-full bg-surface-hover text-muted">
                    {TYPE_LABELS[l.listing_type] || l.listing_type}
                  </span>
                </td>
                <td className="px-4 py-2.5">
                  {l.exchange ? (
                    <span className={`text-xs px-2 py-0.5 rounded-full ${EXCHANGE_COLORS[l.exchange] || ""}`}>
                      {l.exchange}
                    </span>
                  ) : "-"}
                </td>
                <td className="px-4 py-2.5 text-right tabular-nums">
                  {formatPrice(l.offer_price)}
                </td>
                <td className="px-4 py-2.5 text-right tabular-nums text-muted">
                  {formatAmount(l.shares_offered)}
                </td>
                <td className="px-4 py-2.5 text-center">
                  {l.is_crypto ? (
                    <span className="text-xs px-2 py-0.5 rounded-full bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400">
                      Crypto
                    </span>
                  ) : (
                    <span className="text-muted">-</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function CryptoChips({
  products,
  cryptoStats,
}: {
  products: CryptoProduct[];
  cryptoStats: CryptoStats;
}) {
  const [filter, setFilter] = useState<string>("all");

  const filtered =
    filter === "all" ? products : products.filter((p) => p.product_type === filter);

  return (
    <section>
      {/* Stats bar */}
      <div className="flex flex-wrap gap-3 mb-4">
        <div className="glass rounded-xl px-4 py-2 text-center">
          <div className="text-xl font-bold text-ink">{cryptoStats.total}</div>
          <div className="text-xs text-muted">总计</div>
        </div>
        {cryptoStats.by_type.map((t) => (
          <button
            key={t.product_type}
            onClick={() => setFilter(filter === t.product_type ? "all" : t.product_type)}
            className={`glass rounded-xl px-4 py-2 text-center transition-all ${
              filter === t.product_type
                ? "ring-1 ring-primary/50"
                : "hover:bg-surface-hover"
            }`}
          >
            <div className="text-xl font-bold text-ink">{t.cnt}</div>
            <div className="text-xs text-muted">
              {CRYPTO_TYPE_LABELS[t.product_type] || t.product_type}
            </div>
          </button>
        ))}
      </div>

      {/* Products grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
        {filtered.map((p) => (
          <div key={p.id} className="glass rounded-xl p-4 hover:bg-surface-hover transition-colors">
            <div className="flex items-center justify-between mb-2">
              <span className="font-semibold text-ink">{p.ticker}</span>
              <span
                className={`text-xs px-2 py-0.5 rounded-full ${
                  CRYPTO_TYPE_COLORS[p.product_type] || ""
                }`}
              >
                {CRYPTO_TYPE_LABELS[p.product_type] || p.product_type}
              </span>
            </div>
            <div className="text-sm text-ink mb-1 line-clamp-2">{p.company_name}</div>
            <div className="flex flex-wrap gap-1.5 mt-2">
              {p.underlying_asset && (
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-surface-hover text-muted">
                  {p.underlying_asset}
                </span>
              )}
              {p.issuer && (
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-surface-hover text-muted">
                  {p.issuer}
                </span>
              )}
            </div>
            <div className="flex gap-4 mt-3 text-xs text-muted">
              {p.aum != null && <span>AUM: {formatAmount(p.aum)}</span>}
              {p.market_cap != null && <span>市值: {formatAmount(p.market_cap)}</span>}
              {p.expense_ratio != null && (
                <span>费率: {p.expense_ratio.toFixed(2)}%</span>
              )}
            </div>
            {p.listing_date && (
              <div className="text-[10px] text-muted mt-2">
                上市日期: {String(p.listing_date).slice(0, 10)}
              </div>
            )}
          </div>
        ))}
      </div>

      {filtered.length === 0 && (
        <div className="text-center py-12 text-muted text-sm">暂无数据</div>
      )}
    </section>
  );
}

// ═══════════════════════════════════════════════════════════
//  Insider Trades tab
// ═══════════════════════════════════════════════════════════

function InsiderTradesTab() {
  const [trades, setTrades] = useState<InsiderTrade[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    fetchInsiderTrades({ limit: 200 })
      .then(setTrades)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (error) {
    return <div className="text-center py-12 text-red-500 text-sm">加载失败: {error}</div>;
  }

  return (
    <section>
      <h2 className="text-sm font-medium text-muted mb-3">
        内幕交易 - Form 4 ({trades.length})
      </h2>
      <div className="glass rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-muted text-xs">
              <th className="text-left px-3 py-2.5 font-medium">Ticker</th>
              <th className="text-left px-3 py-2.5 font-medium">内部人</th>
              <th className="text-left px-3 py-2.5 font-medium">职位</th>
              <th className="text-center px-3 py-2.5 font-medium">交易类型</th>
              <th className="text-right px-3 py-2.5 font-medium">股数</th>
              <th className="text-right px-3 py-2.5 font-medium">价格</th>
              <th className="text-right px-3 py-2.5 font-medium">总价值</th>
              <th className="text-center px-3 py-2.5 font-medium">10b5-1</th>
              <th className="text-left px-3 py-2.5 font-medium">申报日期</th>
            </tr>
          </thead>
          <tbody>
            {loading
              ? Array.from({ length: 5 }).map((_, i) => <LoadingRow key={i} cols={9} />)
              : trades.map((t) => (
                  <tr key={t.id} className="border-b border-border/50 hover:bg-surface-hover">
                    <td className="px-3 py-2.5 font-medium text-ink">{t.ticker}</td>
                    <td className="px-3 py-2.5 max-w-[120px] truncate">{t.insider_name || "-"}</td>
                    <td className="px-3 py-2.5 max-w-[120px] truncate text-muted">{t.insider_title || "-"}</td>
                    <td className="px-3 py-2.5 text-center">
                      <span className={`text-xs px-2 py-0.5 rounded-full ${TRANSACTION_COLORS[t.transaction_type || ""] || "bg-surface-hover text-muted"}`}>
                        {t.transaction_type || "-"}
                      </span>
                    </td>
                    <td className="px-3 py-2.5 text-right tabular-nums">{formatShares(t.shares)}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums">{formatPrice(t.price_per_share)}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums">{formatAmount(t.total_value)}</td>
                    <td className="px-3 py-2.5 text-center">
                      {t.is_10b5_1 ? (
                        <span className="text-xs px-2 py-0.5 rounded-full bg-sky-100 text-sky-800 dark:bg-sky-900/30 dark:text-sky-400">计划</span>
                      ) : "-"}
                    </td>
                    <td className="px-3 py-2.5 text-muted">{t.filing_date?.slice(0, 10) || "-"}</td>
                  </tr>
                ))}
          </tbody>
        </table>
      </div>
      {!loading && trades.length === 0 && (
        <div className="text-center py-12 text-muted text-sm">暂无内幕交易数据</div>
      )}
    </section>
  );
}

// ═══════════════════════════════════════════════════════════
//  Earnings Calendar tab
// ═══════════════════════════════════════════════════════════

function EarningsTab() {
  const [earnings, setEarnings] = useState<EarningsEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState<string>("all");

  useEffect(() => {
    fetchEarnings({ limit: 200 })
      .then(setEarnings)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (error) {
    return <div className="text-center py-12 text-red-500 text-sm">加载失败: {error}</div>;
  }

  const filtered = filter === "all" ? earnings : earnings.filter((e) => e.report_type === filter);
  const kCount = earnings.filter((e) => e.report_type === "10-K").length;
  const qCount = earnings.filter((e) => e.report_type === "10-Q").length;

  return (
    <section>
      <div className="flex flex-wrap gap-3 mb-3">
        <button
          onClick={() => setFilter("all")}
          className={`text-xs px-3 py-1.5 rounded-lg transition-all ${
            filter === "all" ? "bg-primary-a15 text-primary font-medium" : "glass text-muted hover:text-ink"
          }`}
        >
          全部 ({earnings.length})
        </button>
        <button
          onClick={() => setFilter("10-K")}
          className={`text-xs px-3 py-1.5 rounded-lg transition-all ${
            filter === "10-K" ? "bg-primary-a15 text-primary font-medium" : "glass text-muted hover:text-ink"
          }`}
        >
          10-K 年报 ({kCount})
        </button>
        <button
          onClick={() => setFilter("10-Q")}
          className={`text-xs px-3 py-1.5 rounded-lg transition-all ${
            filter === "10-Q" ? "bg-primary-a15 text-primary font-medium" : "glass text-muted hover:text-ink"
          }`}
        >
          10-Q 季报 ({qCount})
        </button>
      </div>

      <div className="glass rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-muted text-xs">
              <th className="text-left px-4 py-2.5 font-medium">Ticker</th>
              <th className="text-left px-4 py-2.5 font-medium">公司名称</th>
              <th className="text-center px-4 py-2.5 font-medium">报告类型</th>
              <th className="text-left px-4 py-2.5 font-medium">财季截止</th>
              <th className="text-left px-4 py-2.5 font-medium">申报日期</th>
            </tr>
          </thead>
          <tbody>
            {loading
              ? Array.from({ length: 5 }).map((_, i) => <LoadingRow key={i} cols={5} />)
              : filtered.map((e) => (
                  <tr key={e.id} className="border-b border-border/50 hover:bg-surface-hover">
                    <td className="px-4 py-2.5 font-medium text-ink">{e.ticker || "-"}</td>
                    <td className="px-4 py-2.5 max-w-[200px] truncate">{e.company_name}</td>
                    <td className="px-4 py-2.5 text-center">
                      <span className={`text-xs px-2 py-0.5 rounded-full ${
                        e.report_type === "10-K"
                          ? "bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-400"
                          : "bg-sky-100 text-sky-800 dark:bg-sky-900/30 dark:text-sky-400"
                      }`}>
                        {e.report_type}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 text-muted">{e.fiscal_period_end?.slice(0, 10) || "-"}</td>
                    <td className="px-4 py-2.5 text-muted">{e.filing_date?.slice(0, 10) || "-"}</td>
                  </tr>
                ))}
          </tbody>
        </table>
      </div>
      {!loading && filtered.length === 0 && (
        <div className="text-center py-12 text-muted text-sm">暂无财报数据</div>
      )}
    </section>
  );
}

// ═══════════════════════════════════════════════════════════
//  Institutional Holdings tab
// ═══════════════════════════════════════════════════════════

function HoldingsTab() {
  const [holdings, setHoldings] = useState<InstitutionalHolding[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    fetchHoldings({ limit: 200 })
      .then(setHoldings)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (error) {
    return <div className="text-center py-12 text-red-500 text-sm">加载失败: {error}</div>;
  }

  return (
    <section>
      <h2 className="text-sm font-medium text-muted mb-3">
        机构持仓 - 13F ({holdings.length})
      </h2>
      <div className="glass rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-muted text-xs">
              <th className="text-left px-3 py-2.5 font-medium">Ticker</th>
              <th className="text-left px-3 py-2.5 font-medium">机构</th>
              <th className="text-left px-3 py-2.5 font-medium">证券名称</th>
              <th className="text-right px-3 py-2.5 font-medium">持仓股数</th>
              <th className="text-right px-3 py-2.5 font-medium">市值</th>
              <th className="text-left px-3 py-2.5 font-medium">季度截止</th>
              <th className="text-left px-3 py-2.5 font-medium">申报日期</th>
            </tr>
          </thead>
          <tbody>
            {loading
              ? Array.from({ length: 5 }).map((_, i) => <LoadingRow key={i} cols={7} />)
              : holdings.map((h) => (
                  <tr key={h.id} className="border-b border-border/50 hover:bg-surface-hover">
                    <td className="px-3 py-2.5 font-medium text-ink">{h.ticker}</td>
                    <td className="px-3 py-2.5 max-w-[150px] truncate" title={h.filer_name}>
                      {h.filer_name || h.filer_cik}
                    </td>
                    <td className="px-3 py-2.5 max-w-[180px] truncate text-muted" title={h.security_name || ""}>
                      {h.security_name || "-"}
                    </td>
                    <td className="px-3 py-2.5 text-right tabular-nums">{formatShares(h.shares)}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums">{formatAmount(h.market_value)}</td>
                    <td className="px-3 py-2.5 text-muted">{h.quarter_end?.slice(0, 10) || "-"}</td>
                    <td className="px-3 py-2.5 text-muted">{h.filing_date?.slice(0, 10) || "-"}</td>
                  </tr>
                ))}
          </tbody>
        </table>
      </div>
      {!loading && holdings.length === 0 && (
        <div className="text-center py-12 text-muted text-sm">暂无机构持仓数据</div>
      )}
    </section>
  );
}

// ═══════════════════════════════════════════════════════════
//  Risk Data tab (Short Interest + FTD)
// ═══════════════════════════════════════════════════════════

function RiskDataTab() {
  const [subtab, setSubtab] = useState<"si" | "ftd">("si");
  const [siData, setSiData] = useState<ShortInterestEntry[]>([]);
  const [ftdData, setFtdData] = useState<FtdEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([
      fetchShortInterest(undefined, 100),
      fetchFtd({ limit: 100 }),
    ])
      .then(([si, ftd]) => {
        setSiData(si);
        setFtdData(ftd);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (error) {
    return <div className="text-center py-12 text-red-500 text-sm">加载失败: {error}</div>;
  }

  return (
    <section>
      {/* Sub-tabs */}
      <div className="flex gap-3 mb-4">
        <button
          onClick={() => setSubtab("si")}
          className={`text-sm px-4 py-2 rounded-lg transition-all ${
            subtab === "si"
              ? "bg-primary-a15 text-primary font-medium"
              : "glass text-muted hover:text-ink"
          }`}
        >
          空头仓位 ({siData.length})
        </button>
        <button
          onClick={() => setSubtab("ftd")}
          className={`text-sm px-4 py-2 rounded-lg transition-all ${
            subtab === "ftd"
              ? "bg-primary-a15 text-primary font-medium"
              : "glass text-muted hover:text-ink"
          }`}
        >
          交收失败 FTD ({ftdData.length})
        </button>
      </div>

      {subtab === "si" ? (
        <div className="glass rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-muted text-xs">
                <th className="text-left px-4 py-2.5 font-medium">Ticker</th>
                <th className="text-right px-4 py-2.5 font-medium">空头仓位</th>
                <th className="text-right px-4 py-2.5 font-medium">日均成交量</th>
                <th className="text-right px-4 py-2.5 font-medium">回补天数</th>
                <th className="text-right px-4 py-2.5 font-medium">空头占比</th>
                <th className="text-left px-4 py-2.5 font-medium">结算日期</th>
              </tr>
            </thead>
            <tbody>
              {loading
                ? Array.from({ length: 5 }).map((_, i) => <LoadingRow key={i} cols={6} />)
                : siData.map((s) => (
                    <tr key={s.id} className="border-b border-border/50 hover:bg-surface-hover">
                      <td className="px-4 py-2.5 font-medium text-ink">{s.ticker}</td>
                      <td className="px-4 py-2.5 text-right tabular-nums">{s.short_interest?.toLocaleString() || "-"}</td>
                      <td className="px-4 py-2.5 text-right tabular-nums text-muted">{s.avg_daily_volume?.toLocaleString() || "-"}</td>
                      <td className="px-4 py-2.5 text-right tabular-nums">{s.days_to_cover?.toFixed(1) || "-"}</td>
                      <td className="px-4 py-2.5 text-right tabular-nums">
                        {s.short_pct_float != null ? (
                          <span className={s.short_pct_float > 20 ? "text-red-500 font-medium" : ""}>
                            {s.short_pct_float.toFixed(1)}%
                          </span>
                        ) : "-"}
                      </td>
                      <td className="px-4 py-2.5 text-muted">{s.settlement_date?.slice(0, 10) || "-"}</td>
                    </tr>
                  ))}
            </tbody>
          </table>
          {!loading && siData.length === 0 && (
            <div className="text-center py-12 text-muted text-sm">暂无空头仓位数据</div>
          )}
        </div>
      ) : (
        <div className="glass rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-muted text-xs">
                <th className="text-left px-4 py-2.5 font-medium">Ticker</th>
                <th className="text-right px-4 py-2.5 font-medium">失败数量</th>
                <th className="text-right px-4 py-2.5 font-medium">价格</th>
                <th className="text-left px-4 py-2.5 font-medium">日期</th>
              </tr>
            </thead>
            <tbody>
              {loading
                ? Array.from({ length: 5 }).map((_, i) => <LoadingRow key={i} cols={4} />)
                : ftdData.map((f) => (
                    <tr key={f.id} className="border-b border-border/50 hover:bg-surface-hover">
                      <td className="px-4 py-2.5 font-medium text-ink">{f.ticker}</td>
                      <td className="px-4 py-2.5 text-right tabular-nums">{f.quantity?.toLocaleString() || "-"}</td>
                      <td className="px-4 py-2.5 text-right tabular-nums">{formatPrice(f.price)}</td>
                      <td className="px-4 py-2.5 text-muted">{f.date?.slice(0, 10) || "-"}</td>
                    </tr>
                  ))}
            </tbody>
          </table>
          {!loading && ftdData.length === 0 && (
            <div className="text-center py-12 text-muted text-sm">暂无FTD数据 (SEC数据可能尚未发布)</div>
          )}
        </div>
      )}
    </section>
  );
}

// ═══════════════════════════════════════════════════════════
//  ETF Flows tab
// ═══════════════════════════════════════════════════════════

function EtfFlowsTab() {
  const [flows, setFlows] = useState<EtfFlowEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    fetchEtfFlows({ limit: 100 })
      .then(setFlows)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (error) {
    return <div className="text-center py-12 text-red-500 text-sm">加载失败: {error}</div>;
  }

  return (
    <section>
      <h2 className="text-sm font-medium text-muted mb-3">
        Crypto ETF 资金流 ({flows.length})
      </h2>
      <div className="glass rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-muted text-xs">
              <th className="text-left px-4 py-2.5 font-medium">Ticker</th>
              <th className="text-right px-4 py-2.5 font-medium">收盘价</th>
              <th className="text-right px-4 py-2.5 font-medium">成交量</th>
              <th className="text-right px-4 py-2.5 font-medium">AUM</th>
              <th className="text-right px-4 py-2.5 font-medium">估算资金流</th>
              <th className="text-right px-4 py-2.5 font-medium">资金流%</th>
              <th className="text-left px-4 py-2.5 font-medium">日期</th>
            </tr>
          </thead>
          <tbody>
            {loading
              ? Array.from({ length: 5 }).map((_, i) => <LoadingRow key={i} cols={7} />)
              : flows.map((f) => (
                  <tr key={f.id} className="border-b border-border/50 hover:bg-surface-hover">
                    <td className="px-4 py-2.5 font-medium text-ink">{f.ticker}</td>
                    <td className="px-4 py-2.5 text-right tabular-nums">{formatPrice(f.close_price)}</td>
                    <td className="px-4 py-2.5 text-right tabular-nums">{f.volume?.toLocaleString() || "-"}</td>
                    <td className="px-4 py-2.5 text-right tabular-nums">{formatAmount(f.aum)}</td>
                    <td className="px-4 py-2.5 text-right tabular-nums">
                      {f.estimated_flow != null ? (
                        <span className={f.estimated_flow > 0 ? "text-emerald-600" : f.estimated_flow < 0 ? "text-red-500" : ""}>
                          {formatAmount(f.estimated_flow)}
                        </span>
                      ) : "-"}
                    </td>
                    <td className="px-4 py-2.5 text-right tabular-nums">
                      {f.flow_pct != null ? (
                        <span className={f.flow_pct > 0 ? "text-emerald-600" : f.flow_pct < 0 ? "text-red-500" : ""}>
                          {f.flow_pct > 0 ? "+" : ""}{f.flow_pct.toFixed(2)}%
                        </span>
                      ) : "-"}
                    </td>
                    <td className="px-4 py-2.5 text-muted">{f.date?.slice(0, 10) || "-"}</td>
                  </tr>
                ))}
          </tbody>
        </table>
      </div>
      {!loading && flows.length === 0 && (
        <div className="text-center py-12 text-muted text-sm">暂无ETF资金流数据</div>
      )}
    </section>
  );
}

// ═══════════════════════════════════════════════════════════
//  Dividends tab
// ═══════════════════════════════════════════════════════════

function DividendsTab() {
  const [dividends, setDividends] = useState<Dividend[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    fetchDividends({ limit: 200 })
      .then(setDividends)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (error) {
    return <div className="text-center py-12 text-red-500 text-sm">加载失败: {error}</div>;
  }

  return (
    <section>
      <h2 className="text-sm font-medium text-muted mb-3">
        股息分红 ({dividends.length})
      </h2>
      <div className="glass rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-muted text-xs">
              <th className="text-left px-3 py-2.5 font-medium">Ticker</th>
              <th className="text-left px-3 py-2.5 font-medium">除息日</th>
              <th className="text-left px-3 py-2.5 font-medium">支付日</th>
              <th className="text-right px-3 py-2.5 font-medium">股息率</th>
              <th className="text-right px-3 py-2.5 font-medium">股息收益</th>
              <th className="text-right px-3 py-2.5 font-medium">派息比率</th>
              <th className="text-right px-3 py-2.5 font-medium">5年均收益</th>
            </tr>
          </thead>
          <tbody>
            {loading
              ? Array.from({ length: 5 }).map((_, i) => <LoadingRow key={i} cols={7} />)
              : dividends.map((d) => (
                  <tr key={d.id} className="border-b border-border/50 hover:bg-surface-hover">
                    <td className="px-3 py-2.5 font-medium text-ink">{d.ticker}</td>
                    <td className="px-3 py-2.5 text-muted">{d.ex_dividend_date?.slice(0, 10) || "-"}</td>
                    <td className="px-3 py-2.5 text-muted">{d.pay_date?.slice(0, 10) || "-"}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums">{d.dividend_rate?.toFixed(4) || "-"}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums">{d.dividend_yield != null ? `${d.dividend_yield.toFixed(2)}%` : "-"}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums">{d.payout_ratio != null ? `${d.payout_ratio.toFixed(1)}%` : "-"}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums">{d.five_year_avg_yield != null ? `${d.five_year_avg_yield.toFixed(2)}%` : "-"}</td>
                  </tr>
                ))}
          </tbody>
        </table>
      </div>
      {!loading && dividends.length === 0 && (
        <div className="text-center py-12 text-muted text-sm">暂无股息分红数据</div>
      )}
    </section>
  );
}

// ═══════════════════════════════════════════════════════════
//  Stock Splits tab
// ═══════════════════════════════════════════════════════════

function SplitsTab() {
  const [splits, setSplits] = useState<StockSplit[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    fetchSplits(undefined, 100)
      .then(setSplits)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (error) {
    return <div className="text-center py-12 text-red-500 text-sm">加载失败: {error}</div>;
  }

  return (
    <section>
      <h2 className="text-sm font-medium text-muted mb-3">
        股票拆分 ({splits.length})
      </h2>
      <div className="glass rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-muted text-xs">
              <th className="text-left px-4 py-2.5 font-medium">Ticker</th>
              <th className="text-left px-4 py-2.5 font-medium">拆分日期</th>
              <th className="text-center px-4 py-2.5 font-medium">拆分比例</th>
            </tr>
          </thead>
          <tbody>
            {loading
              ? Array.from({ length: 5 }).map((_, i) => <LoadingRow key={i} cols={3} />)
              : splits.map((s) => (
                  <tr key={s.id} className="border-b border-border/50 hover:bg-surface-hover">
                    <td className="px-4 py-2.5 font-medium text-ink">{s.ticker}</td>
                    <td className="px-4 py-2.5 text-muted">{s.split_date?.slice(0, 10) || "-"}</td>
                    <td className="px-4 py-2.5 text-center">
                      <span className="text-xs px-2 py-0.5 rounded-full bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-400">
                        {s.split_ratio}
                      </span>
                    </td>
                  </tr>
                ))}
          </tbody>
        </table>
      </div>
      {!loading && splits.length === 0 && (
        <div className="text-center py-12 text-muted text-sm">暂无股票拆分数据</div>
      )}
    </section>
  );
}

// ═══════════════════════════════════════════════════════════
//  Trading Suspensions tab
// ═══════════════════════════════════════════════════════════

function SuspensionsTab() {
  const [suspensions, setSuspensions] = useState<Suspension[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    fetchSuspensions({ limit: 100 })
      .then(setSuspensions)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (error) {
    return <div className="text-center py-12 text-red-500 text-sm">加载失败: {error}</div>;
  }

  return (
    <section>
      <h2 className="text-sm font-medium text-muted mb-3">
        交易暂停 - SEC Form 34 ({suspensions.length})
      </h2>
      <div className="glass rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-muted text-xs">
              <th className="text-left px-3 py-2.5 font-medium">Ticker</th>
              <th className="text-left px-3 py-2.5 font-medium">公司名称</th>
              <th className="text-center px-3 py-2.5 font-medium">暂停类型</th>
              <th className="text-left px-3 py-2.5 font-medium">原因</th>
              <th className="text-left px-3 py-2.5 font-medium">生效日期</th>
              <th className="text-left px-3 py-2.5 font-medium">申报日期</th>
            </tr>
          </thead>
          <tbody>
            {loading
              ? Array.from({ length: 5 }).map((_, i) => <LoadingRow key={i} cols={6} />)
              : suspensions.map((s) => (
                  <tr key={s.id} className="border-b border-border/50 hover:bg-surface-hover">
                    <td className="px-3 py-2.5 font-medium text-ink">{s.ticker || "-"}</td>
                    <td className="px-3 py-2.5 max-w-[150px] truncate">{s.company_name}</td>
                    <td className="px-3 py-2.5 text-center">
                      <span className="text-xs px-2 py-0.5 rounded-full bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400">
                        {s.suspension_type || "Suspension"}
                      </span>
                    </td>
                    <td className="px-3 py-2.5 max-w-[200px] truncate text-muted" title={s.reason || ""}>
                      {s.reason || "-"}
                    </td>
                    <td className="px-3 py-2.5 text-muted">{s.effective_date?.slice(0, 10) || "-"}</td>
                    <td className="px-3 py-2.5 text-muted">{s.filing_date?.slice(0, 10) || "-"}</td>
                  </tr>
                ))}
          </tbody>
        </table>
      </div>
      {!loading && suspensions.length === 0 && (
        <div className="text-center py-12 text-muted text-sm">暂无交易暂停数据</div>
      )}
    </section>
  );
}

// ═══════════════════════════════════════════════════════════
//  Enforcement Actions tab
// ═══════════════════════════════════════════════════════════

function EnforcementTab() {
  const [actions, setActions] = useState<EnforcementAction[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState<string>("all");

  useEffect(() => {
    fetchEnforcement({ limit: 100 })
      .then(setActions)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (error) {
    return <div className="text-center py-12 text-red-500 text-sm">加载失败: {error}</div>;
  }

  const filtered = filter === "all" ? actions : actions.filter((a) => a.enforcement_type === filter);
  const aaerCount = actions.filter((a) => a.enforcement_type === "AAER").length;
  const lrCount = actions.filter((a) => a.enforcement_type === "LR").length;
  const apCount = actions.filter((a) => a.enforcement_type === "AP").length;

  return (
    <section>
      <div className="flex flex-wrap gap-3 mb-3">
        <button
          onClick={() => setFilter("all")}
          className={`text-xs px-3 py-1.5 rounded-lg transition-all ${
            filter === "all" ? "bg-primary-a15 text-primary font-medium" : "glass text-muted hover:text-ink"
          }`}
        >
          全部 ({actions.length})
        </button>
        <button
          onClick={() => setFilter("AAER")}
          className={`text-xs px-3 py-1.5 rounded-lg transition-all ${
            filter === "AAER" ? "bg-primary-a15 text-primary font-medium" : "glass text-muted hover:text-ink"
          }`}
        >
          AAER 会计审计 ({aaerCount})
        </button>
        <button
          onClick={() => setFilter("LR")}
          className={`text-xs px-3 py-1.5 rounded-lg transition-all ${
            filter === "LR" ? "bg-primary-a15 text-primary font-medium" : "glass text-muted hover:text-ink"
          }`}
        >
          LR 诉讼 ({lrCount})
        </button>
        <button
          onClick={() => setFilter("AP")}
          className={`text-xs px-3 py-1.5 rounded-lg transition-all ${
            filter === "AP" ? "bg-primary-a15 text-primary font-medium" : "glass text-muted hover:text-ink"
          }`}
        >
          AP 行政程序 ({apCount})
        </button>
      </div>

      <div className="glass rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-muted text-xs">
              <th className="text-center px-3 py-2.5 font-medium">类型</th>
              <th className="text-left px-3 py-2.5 font-medium">实体名称</th>
              <th className="text-left px-3 py-2.5 font-medium">Ticker</th>
              <th className="text-right px-3 py-2.5 font-medium">罚款金额</th>
              <th className="text-left px-3 py-2.5 font-medium">描述</th>
              <th className="text-left px-3 py-2.5 font-medium">日期</th>
            </tr>
          </thead>
          <tbody>
            {loading
              ? Array.from({ length: 5 }).map((_, i) => <LoadingRow key={i} cols={6} />)
              : filtered.map((a) => (
                  <tr key={a.id} className="border-b border-border/50 hover:bg-surface-hover">
                    <td className="px-3 py-2.5 text-center">
                      <span className={`text-xs px-2 py-0.5 rounded-full ${
                        a.enforcement_type === "AAER"
                          ? "bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-400"
                          : a.enforcement_type === "LR"
                          ? "bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-400"
                          : "bg-sky-100 text-sky-800 dark:bg-sky-900/30 dark:text-sky-400"
                      }`}>
                        {a.enforcement_type}
                      </span>
                    </td>
                    <td className="px-3 py-2.5 max-w-[150px] truncate" title={a.entity_name}>
                      {a.entity_name}
                    </td>
                    <td className="px-3 py-2.5 font-medium text-ink">{a.ticker || "-"}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums">
                      {a.penalty_amount != null ? (
                        <span className="text-red-500 font-medium">{formatAmount(a.penalty_amount)}</span>
                      ) : "-"}
                    </td>
                    <td className="px-3 py-2.5 max-w-[200px] truncate text-muted" title={a.description || ""}>
                      {a.description || "-"}
                    </td>
                    <td className="px-3 py-2.5 text-muted">{a.filing_date?.slice(0, 10) || "-"}</td>
                  </tr>
                ))}
          </tbody>
        </table>
      </div>
      {!loading && filtered.length === 0 && (
        <div className="text-center py-12 text-muted text-sm">暂无执法行动数据</div>
      )}
    </section>
  );
}

// ═══════════════════════════════════════════════════════════
//  Threshold Securities tab
// ═══════════════════════════════════════════════════════════

function ThresholdTab() {
  const [securities, setSecurities] = useState<ThresholdSecurity[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    fetchThresholdSecurities({ limit: 100 })
      .then(setSecurities)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (error) {
    return <div className="text-center py-12 text-red-500 text-sm">加载失败: {error}</div>;
  }

  return (
    <section>
      <h2 className="text-sm font-medium text-muted mb-3">
        Reg SHO 阈值证券 ({securities.length})
      </h2>
      <div className="glass rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-muted text-xs">
              <th className="text-left px-4 py-2.5 font-medium">Ticker</th>
              <th className="text-left px-4 py-2.5 font-medium">证券名称</th>
              <th className="text-center px-4 py-2.5 font-medium">市场</th>
              <th className="text-center px-4 py-2.5 font-medium">阈值状态</th>
              <th className="text-left px-4 py-2.5 font-medium">日期</th>
            </tr>
          </thead>
          <tbody>
            {loading
              ? Array.from({ length: 5 }).map((_, i) => <LoadingRow key={i} cols={5} />)
              : securities.map((s) => (
                  <tr key={s.id} className="border-b border-border/50 hover:bg-surface-hover">
                    <td className="px-4 py-2.5 font-medium text-ink">{s.ticker}</td>
                    <td className="px-4 py-2.5 max-w-[200px] truncate text-muted" title={s.security_name || ""}>
                      {s.security_name || "-"}
                    </td>
                    <td className="px-4 py-2.5 text-center text-muted">{s.market_category || "-"}</td>
                    <td className="px-4 py-2.5 text-center">
                      {s.is_threshold ? (
                        <span className="text-xs px-2 py-0.5 rounded-full bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400">
                          阈值
                        </span>
                      ) : (
                        <span className="text-xs px-2 py-0.5 rounded-full bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-400">
                          正常
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-2.5 text-muted">{s.date?.slice(0, 10) || "-"}</td>
                  </tr>
                ))}
          </tbody>
        </table>
      </div>
      {!loading && securities.length === 0 && (
        <div className="text-center py-12 text-muted text-sm">暂无阈值证券数据</div>
      )}
    </section>
  );
}

// ═══════════════════════════════════════════════════════════
//  ATS / Dark Pool tab
// ═══════════════════════════════════════════════════════════

function AtsTab() {
  const [filings, setFilings] = useState<AtsFiling[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    fetchAtsFilings({ limit: 50 })
      .then(setFilings)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (error) {
    return <div className="text-center py-12 text-red-500 text-sm">加载失败: {error}</div>;
  }

  return (
    <section>
      <h2 className="text-sm font-medium text-muted mb-3">
        暗池 / ATS - SEC Form ATS-N ({filings.length})
      </h2>
      <div className="glass rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-muted text-xs">
              <th className="text-left px-3 py-2.5 font-medium">ATS 名称</th>
              <th className="text-left px-3 py-2.5 font-medium">运营机构</th>
              <th className="text-center px-3 py-2.5 font-medium">申报类型</th>
              <th className="text-right px-3 py-2.5 font-medium">预估成交量</th>
              <th className="text-left px-3 py-2.5 font-medium">交易品种</th>
              <th className="text-left px-3 py-2.5 font-medium">申报日期</th>
            </tr>
          </thead>
          <tbody>
            {loading
              ? Array.from({ length: 5 }).map((_, i) => <LoadingRow key={i} cols={6} />)
              : filings.map((f) => (
                  <tr key={f.id} className="border-b border-border/50 hover:bg-surface-hover">
                    <td className="px-3 py-2.5 font-medium text-ink max-w-[140px] truncate" title={f.ats_name}>
                      {f.ats_name}
                    </td>
                    <td className="px-3 py-2.5 max-w-[160px] truncate text-muted" title={f.filer_name}>
                      {f.filer_name}
                    </td>
                    <td className="px-3 py-2.5 text-center">
                      <span className="text-xs px-2 py-0.5 rounded-full bg-sky-100 text-sky-800 dark:bg-sky-900/30 dark:text-sky-400">
                        {f.filing_type || "ATS-N"}
                      </span>
                    </td>
                    <td className="px-3 py-2.5 text-right tabular-nums text-muted">{f.volume_estimate || "-"}</td>
                    <td className="px-3 py-2.5 max-w-[160px] truncate text-muted" title={f.securities_traded || ""}>
                      {f.securities_traded || "-"}
                    </td>
                    <td className="px-3 py-2.5 text-muted">{f.filing_date?.slice(0, 10) || "-"}</td>
                  </tr>
                ))}
          </tbody>
        </table>
      </div>
      {!loading && filings.length === 0 && (
        <div className="text-center py-12 text-muted text-sm">暂无ATS暗池数据 (SEC每季度更新)</div>
      )}
    </section>
  );
}

// ═══════════════════════════════════════════════════════════
//  Short Sale Activity tab (enhanced signals)
// ═══════════════════════════════════════════════════════════

const RISK_LEVEL_COLORS: Record<string, string> = {
  normal: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-400",
  elevated: "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400",
  high: "bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-400",
  extreme: "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400",
};

function ShortActivityTab() {
  const [activities, setActivities] = useState<ShortActivity[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [riskFilter, setRiskFilter] = useState<string>("all");

  useEffect(() => {
    fetchShortActivity({ limit: 100 })
      .then(setActivities)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (error) {
    return <div className="text-center py-12 text-red-500 text-sm">加载失败: {error}</div>;
  }

  const filtered = riskFilter === "all"
    ? activities
    : activities.filter((a) => a.risk_level === riskFilter);

  return (
    <section>
      <div className="flex flex-wrap gap-3 mb-3">
        <button
          onClick={() => setRiskFilter("all")}
          className={`text-xs px-3 py-1.5 rounded-lg transition-all ${
            riskFilter === "all" ? "bg-primary-a15 text-primary font-medium" : "glass text-muted hover:text-ink"
          }`}
        >
          全部 ({activities.length})
        </button>
        {["extreme", "high", "elevated", "normal"].map((level) => (
          <button
            key={level}
            onClick={() => setRiskFilter(riskFilter === level ? "all" : level)}
            className={`text-xs px-3 py-1.5 rounded-lg transition-all ${
              riskFilter === level ? "bg-primary-a15 text-primary font-medium" : "glass text-muted hover:text-ink"
            }`}
          >
            {level === "extreme" ? "极高" : level === "high" ? "高" : level === "elevated" ? "偏高" : "正常"}
          </button>
        ))}
      </div>

      <div className="glass rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-muted text-xs">
              <th className="text-left px-3 py-2.5 font-medium">Ticker</th>
              <th className="text-right px-3 py-2.5 font-medium">空头占比</th>
              <th className="text-right px-3 py-2.5 font-medium">回补天数</th>
              <th className="text-right px-3 py-2.5 font-medium">空头变化</th>
              <th className="text-center px-3 py-2.5 font-medium">风险等级</th>
              <th className="text-right px-3 py-2.5 font-medium">挤仓评分</th>
              <th className="text-left px-3 py-2.5 font-medium">日期</th>
            </tr>
          </thead>
          <tbody>
            {loading
              ? Array.from({ length: 5 }).map((_, i) => <LoadingRow key={i} cols={7} />)
              : filtered.map((a) => (
                  <tr key={a.id} className="border-b border-border/50 hover:bg-surface-hover">
                    <td className="px-3 py-2.5 font-medium text-ink">{a.ticker}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums">
                      {a.short_pct_float != null ? (
                        <span className={a.short_pct_float > 20 ? "text-red-500 font-medium" : ""}>
                          {a.short_pct_float.toFixed(1)}%
                        </span>
                      ) : "-"}
                    </td>
                    <td className="px-3 py-2.5 text-right tabular-nums">{a.days_to_cover?.toFixed(1) || "-"}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums">
                      {a.short_change_pct != null ? (
                        <span className={a.short_change_pct > 0 ? "text-red-500" : "text-emerald-600"}>
                          {a.short_change_pct > 0 ? "+" : ""}{a.short_change_pct.toFixed(1)}%
                        </span>
                      ) : "-"}
                    </td>
                    <td className="px-3 py-2.5 text-center">
                      <span className={`text-xs px-2 py-0.5 rounded-full ${RISK_LEVEL_COLORS[a.risk_level || "normal"]}`}>
                        {a.risk_level === "extreme" ? "极高" : a.risk_level === "high" ? "高" : a.risk_level === "elevated" ? "偏高" : "正常"}
                      </span>
                    </td>
                    <td className="px-3 py-2.5 text-right tabular-nums">
                      {a.squeeze_score != null ? (
                        <span className={a.squeeze_score > 70 ? "text-red-500 font-medium" : a.squeeze_score > 40 ? "text-amber-500" : ""}>
                          {a.squeeze_score.toFixed(0)}
                        </span>
                      ) : "-"}
                    </td>
                    <td className="px-3 py-2.5 text-muted">{a.date?.slice(0, 10) || "-"}</td>
                  </tr>
                ))}
          </tbody>
        </table>
      </div>
      {!loading && filtered.length === 0 && (
        <div className="text-center py-12 text-muted text-sm">暂无做空活动数据</div>
      )}
    </section>
  );
}

// ═══════════════════════════════════════════════════════════
//  IPO Lockup Expiry tab
// ═══════════════════════════════════════════════════════════

function LockupTab() {
  const [lockups, setLockups] = useState<LockupExpiry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("active");

  useEffect(() => {
    fetchLockupExpiry({ limit: 100 })
      .then(setLockups)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (error) {
    return <div className="text-center py-12 text-red-500 text-sm">加载失败: {error}</div>;
  }

  const filtered = lockups.filter((l) => statusFilter === "all" || l.status === statusFilter);
  const activeCount = lockups.filter((l) => l.status === "active").length;
  const expiredCount = lockups.filter((l) => l.status === "expired").length;

  return (
    <section>
      <div className="flex flex-wrap gap-3 mb-3">
        <button
          onClick={() => setStatusFilter("all")}
          className={`text-xs px-3 py-1.5 rounded-lg transition-all ${
            statusFilter === "all" ? "bg-primary-a15 text-primary font-medium" : "glass text-muted hover:text-ink"
          }`}
        >
          全部 ({lockups.length})
        </button>
        <button
          onClick={() => setStatusFilter("active")}
          className={`text-xs px-3 py-1.5 rounded-lg transition-all ${
            statusFilter === "active" ? "bg-primary-a15 text-primary font-medium" : "glass text-muted hover:text-ink"
          }`}
        >
          未到期 ({activeCount})
        </button>
        <button
          onClick={() => setStatusFilter("expired")}
          className={`text-xs px-3 py-1.5 rounded-lg transition-all ${
            statusFilter === "expired" ? "bg-primary-a15 text-primary font-medium" : "glass text-muted hover:text-ink"
          }`}
        >
          已到期 ({expiredCount})
        </button>
      </div>

      <div className="glass rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-muted text-xs">
              <th className="text-left px-3 py-2.5 font-medium">Ticker</th>
              <th className="text-left px-3 py-2.5 font-medium">公司名称</th>
              <th className="text-left px-3 py-2.5 font-medium">锁仓到期</th>
              <th className="text-right px-3 py-2.5 font-medium">剩余天数</th>
              <th className="text-right px-3 py-2.5 font-medium">预估解锁股数</th>
              <th className="text-right px-3 py-2.5 font-medium">预估价值</th>
              <th className="text-center px-3 py-2.5 font-medium">状态</th>
            </tr>
          </thead>
          <tbody>
            {loading
              ? Array.from({ length: 5 }).map((_, i) => <LoadingRow key={i} cols={7} />)
              : filtered.map((l) => (
                  <tr key={l.id} className="border-b border-border/50 hover:bg-surface-hover">
                    <td className="px-3 py-2.5 font-medium text-ink">{l.ticker}</td>
                    <td className="px-3 py-2.5 max-w-[140px] truncate" title={l.company_name}>
                      {l.company_name}
                    </td>
                    <td className="px-3 py-2.5 text-muted">{l.lockup_end_date?.slice(0, 10) || "-"}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums">
                      <span className={l.days_remaining <= 7 ? "text-red-500 font-medium" : l.days_remaining <= 30 ? "text-amber-500" : ""}>
                        {l.days_remaining}天
                      </span>
                    </td>
                    <td className="px-3 py-2.5 text-right tabular-nums">{formatShares(l.estimated_shares_unlocking)}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums">{formatAmount(l.estimated_value)}</td>
                    <td className="px-3 py-2.5 text-center">
                      <span className={`text-xs px-2 py-0.5 rounded-full ${
                        l.status === "active"
                          ? "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400"
                          : "bg-surface-hover text-muted"
                      }`}>
                        {l.status === "active" ? "未到期" : "已到期"}
                      </span>
                    </td>
                  </tr>
                ))}
          </tbody>
        </table>
      </div>
      {!loading && filtered.length === 0 && (
        <div className="text-center py-12 text-muted text-sm">暂无锁仓到期数据</div>
      )}
    </section>
  );
}

// ═══════════════════════════════════════════════════════════
//  Options Flow tab
// ═══════════════════════════════════════════════════════════

function OptionsFlowTab() {
  const [options, setOptions] = useState<OptionsFlowEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [unusualOnly, setUnusualOnly] = useState(false);

  useEffect(() => {
    fetchOptionsFlow({ limit: 100 })
      .then(setOptions)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (error) {
    return <div className="text-center py-12 text-red-500 text-sm">加载失败: {error}</div>;
  }

  const filtered = unusualOnly ? options.filter((o) => o.is_unusual) : options;
  const unusualCount = options.filter((o) => o.is_unusual).length;

  return (
    <section>
      <div className="flex flex-wrap gap-3 mb-3">
        <button
          onClick={() => setUnusualOnly(false)}
          className={`text-xs px-3 py-1.5 rounded-lg transition-all ${
            !unusualOnly ? "bg-primary-a15 text-primary font-medium" : "glass text-muted hover:text-ink"
          }`}
        >
          全部 ({options.length})
        </button>
        <button
          onClick={() => setUnusualOnly(true)}
          className={`text-xs px-3 py-1.5 rounded-lg transition-all ${
            unusualOnly ? "bg-primary-a15 text-primary font-medium" : "glass text-muted hover:text-ink"
          }`}
        >
          异常活动 ({unusualCount})
        </button>
      </div>

      <div className="glass rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-muted text-xs">
              <th className="text-left px-3 py-2.5 font-medium">Ticker</th>
              <th className="text-right px-3 py-2.5 font-medium">Call 成交量</th>
              <th className="text-right px-3 py-2.5 font-medium">Put 成交量</th>
              <th className="text-right px-3 py-2.5 font-medium">P/C 比率</th>
              <th className="text-right px-3 py-2.5 font-medium">Vol/OI</th>
              <th className="text-center px-3 py-2.5 font-medium">情绪</th>
              <th className="text-center px-3 py-2.5 font-medium">异常</th>
              <th className="text-left px-3 py-2.5 font-medium">日期</th>
            </tr>
          </thead>
          <tbody>
            {loading
              ? Array.from({ length: 5 }).map((_, i) => <LoadingRow key={i} cols={8} />)
              : filtered.map((o) => (
                  <tr key={o.id} className="border-b border-border/50 hover:bg-surface-hover">
                    <td className="px-3 py-2.5 font-medium text-ink">{o.ticker}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums">{o.total_call_volume?.toLocaleString()}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums">{o.total_put_volume?.toLocaleString()}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums">
                      {o.put_call_vol_ratio != null ? (
                        <span className={
                          o.put_call_vol_ratio > 1.5 ? "text-red-500 font-medium" :
                          o.put_call_vol_ratio < 0.5 ? "text-emerald-600 font-medium" : ""
                        }>
                          {o.put_call_vol_ratio.toFixed(2)}
                        </span>
                      ) : "-"}
                    </td>
                    <td className="px-3 py-2.5 text-right tabular-nums">
                      {o.vol_oi_ratio != null ? (
                        <span className={o.vol_oi_ratio > 2.0 ? "text-red-500 font-medium" : ""}>
                          {o.vol_oi_ratio.toFixed(1)}x
                        </span>
                      ) : "-"}
                    </td>
                    <td className="px-3 py-2.5 text-center">
                      <span className={`text-xs px-2 py-0.5 rounded-full ${
                        o.sentiment === "bullish"
                          ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-400"
                          : o.sentiment === "bearish"
                          ? "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400"
                          : "bg-surface-hover text-muted"
                      }`}>
                        {o.sentiment === "bullish" ? "看涨" : o.sentiment === "bearish" ? "看跌" : "中性"}
                      </span>
                    </td>
                    <td className="px-3 py-2.5 text-center">
                      {o.is_unusual ? (
                        <span className="text-xs px-2 py-0.5 rounded-full bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400">
                          异常
                        </span>
                      ) : "-"}
                    </td>
                    <td className="px-3 py-2.5 text-muted">{o.date?.slice(0, 10) || "-"}</td>
                  </tr>
                ))}
          </tbody>
        </table>
      </div>
      {!loading && filtered.length === 0 && (
        <div className="text-center py-12 text-muted text-sm">暂无期权流数据</div>
      )}
    </section>
  );
}

// ═══════════════════════════════════════════════════════════
//  Main component
// ═══════════════════════════════════════════════════════════

const TABS = [
  { key: "listings", label: "新上市" },
  { key: "crypto", label: "Crypto产品" },
  { key: "insider", label: "内幕交易" },
  { key: "earnings", label: "财报日历" },
  { key: "holdings", label: "机构持仓" },
  { key: "risk", label: "风控数据" },
  { key: "flows", label: "ETF资金流" },
  { key: "dividends", label: "股息分红" },
  { key: "splits", label: "股票拆分" },
  { key: "suspensions", label: "交易暂停" },
  { key: "enforcement", label: "执法行动" },
  { key: "threshold", label: "阈值证券" },
  { key: "ats", label: "暗池ATS" },
  { key: "short-activity", label: "做空活动" },
  { key: "lockup", label: "锁仓到期" },
  { key: "options", label: "期权流" },
] as const;

type TabKey = (typeof TABS)[number]["key"];

export default function ListingsContent({
  listings,
  summary,
  cryptoProducts,
  cryptoStats,
  upcoming,
}: {
  listings: NewListing[];
  summary: ListingSummary;
  cryptoProducts: CryptoProduct[];
  cryptoStats: CryptoStats;
  upcoming: NewListing[];
}) {
  const [tab, setTab] = useState<TabKey>("listings");

  return (
    <>
      {tab === "listings" && (
        <div className="flex flex-col gap-6">
          <StatsBar summary={summary} />
          <UpcomingBar upcoming={upcoming} />
          <ListingsTable listings={listings} />
        </div>
      )}
      {tab === "crypto" && (
        <CryptoChips products={cryptoProducts} cryptoStats={cryptoStats} />
      )}
      {tab === "insider" && <InsiderTradesTab />}
      {tab === "earnings" && <EarningsTab />}
      {tab === "holdings" && <HoldingsTab />}
      {tab === "risk" && <RiskDataTab />}
      {tab === "flows" && <EtfFlowsTab />}
      {tab === "dividends" && <DividendsTab />}
      {tab === "splits" && <SplitsTab />}
      {tab === "suspensions" && <SuspensionsTab />}
      {tab === "enforcement" && <EnforcementTab />}
      {tab === "threshold" && <ThresholdTab />}
      {tab === "ats" && <AtsTab />}
      {tab === "short-activity" && <ShortActivityTab />}
      {tab === "lockup" && <LockupTab />}
      {tab === "options" && <OptionsFlowTab />}

      {/* Tab switcher */}
      <div className="flex justify-center mt-6">
        <div className="glass rounded-xl px-1 py-1 flex gap-0.5 flex-wrap justify-center">
          {TABS.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`text-sm px-4 py-2 rounded-lg transition-all whitespace-nowrap ${
                tab === t.key
                  ? "bg-primary-a15 text-primary font-medium"
                  : "text-muted hover:text-ink hover:bg-surface-hover"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>
    </>
  );
}