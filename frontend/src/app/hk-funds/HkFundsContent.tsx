"use client";

import { useState, useEffect } from "react";
import type { HKFund, HKFundManager, HKFundStats, HKManagerStats, HK_KypDimension, HK_FundRiskRating, HK_NonAuthorizedFund } from "@/lib/api";
import {
  fetchHKFunds,
  fetchHKFundStats,
  fetchHKComplexFunds,
  fetchHKDerivativeFunds,
  fetchHKManagers,
  fetchHKManagerStats,
  fetchHK_KypDimensions,
  fetchHK_AllRiskRatings,
  fetchHK_NonAuthorizedFunds,
  createHK_NonAuthorizedFund,
  fetchHKManagerScrapeStatus,
  type HKManagerScrapeStatus,
} from "@/lib/api";

const TYPE_COLORS: Record<string, string> = {
  derivative_fund: "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400",
  synthetic_etf: "bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-400",
  futures_etf: "bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-400",
  "L&I": "bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-400",
  hedge_fund: "bg-fuchsia-100 text-fuchsia-800 dark:bg-fuchsia-900/30 dark:text-fuchsia-400",
  structured: "bg-violet-100 text-violet-800 dark:bg-violet-900/30 dark:text-violet-400",
  complex_bond: "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400",
  security_token: "bg-cyan-100 text-cyan-800 dark:bg-cyan-900/30 dark:text-cyan-400",
  non_complex: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-400",
};

const TYPE_LABELS: Record<string, string> = {
  derivative_fund: "衍生基金",
  synthetic_etf: "合成ETF",
  futures_etf: "期货ETF",
  "L&I": "杠杆/反向",
  hedge_fund: "对冲基金",
  structured: "结构性产品",
  complex_bond: "复杂债券",
  security_token: "证券代币",
  non_complex: "非复杂",
};

const FLAG_COLORS = {
  yes: "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400",
  no: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-400",
} as const;

const LICENSE_LABELS: Record<string, string> = {
  active: "活跃",
  suspended: "暂停",
  revoked: "吊销",
};

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

function formatAmount(n: number | null): string {
  if (n == null) return "-";
  if (n >= 1e9) return `$${(n / 1e9).toFixed(1)}B`;
  if (n >= 1e6) return `$${(n / 1e6).toFixed(0)}M`;
  return `$${n.toLocaleString()}`;
}

// ═══════════════════════════════════════════════════════════
//  Tab 1: Fund List (基金清单)
// ═══════════════════════════════════════════════════════════

function FundListTab() {
  const [funds, setFunds] = useState<HKFund[]>([]);
  const [stats, setStats] = useState<HKFundStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [derivativeOnly, setDerivativeOnly] = useState(false);
  const [complexOnly, setComplexOnly] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");

  const fetchData = () => {
    setLoading(true);
    Promise.all([
      fetchHKFunds({ limit: 500 }),
      fetchHKFundStats(),
    ])
      .then(([funds, stats]) => {
        setFunds(funds);
        setStats(stats);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => { fetchData(); }, []);

  if (error) return <div className="text-center py-12 text-red-500 text-sm">加载失败: {error}</div>;

  const filtered = funds.filter((f) => {
    if (derivativeOnly && !f.is_derivative_product) return false;
    if (complexOnly && !f.is_complex_product) return false;
    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      return (
        (f.fund_name_en || "").toLowerCase().includes(q) ||
        (f.fund_name_cn || "").includes(q) ||
        (f.isin || "").toLowerCase().includes(q) ||
        (f.sfc_authorization_no || "").toLowerCase().includes(q)
      );
    }
    return true;
  });

  return (
    <section>
      {/* Stats bar */}
      {stats && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
          {[
            { label: "总计", value: stats.total },
            { label: "§5.5 复杂产品", value: stats.complex_count, highlight: "amber" },
            { label: "§5.1A 衍生产品", value: stats.derivative_count, highlight: "red" },
            ...stats.by_complex_type.slice(0, 1).map((c) => ({
              label: TYPE_LABELS[c.complex_product_type] || c.complex_product_type, value: c.cnt,
            })),
          ].map((s) => {
            const hl = (s as { highlight?: string }).highlight;
            return (
              <div
                key={s.label}
                className={`glass rounded-xl px-4 py-3 text-center ${
                  hl === "amber" ? "ring-1 ring-amber-400/50" : hl === "red" ? "ring-1 ring-red-400/50" : ""
                }`}
              >
                <div className="text-2xl font-bold text-ink">{s.value}</div>
                <div className="text-xs text-muted mt-0.5">{s.label}</div>
              </div>
            );
          })}
        </div>
      )}

      {/* Search + filter toggles */}
      <div className="flex flex-wrap gap-3 mb-3">
        <input
          type="text"
          placeholder="搜索基金名称/ISIN/编号..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          className="glass rounded-lg px-3 py-1.5 text-sm text-ink placeholder:text-muted outline-none flex-1 min-w-[200px]"
        />
        <button
          onClick={() => { setDerivativeOnly(!derivativeOnly); setComplexOnly(false); }}
          className={`text-xs px-3 py-1.5 rounded-lg transition-all ${
            derivativeOnly ? "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400 font-medium" : "glass text-muted hover:text-ink"
          }`}
        >
          §5.1A 衍生产品
        </button>
        <button
          onClick={() => { setComplexOnly(!complexOnly); setDerivativeOnly(false); }}
          className={`text-xs px-3 py-1.5 rounded-lg transition-all ${
            complexOnly ? "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400 font-medium" : "glass text-muted hover:text-ink"
          }`}
        >
          §5.5 复杂产品
        </button>
      </div>

      {/* Table */}
      <div className="glass rounded-xl overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-muted text-xs">
              <th className="text-left px-3 py-2.5 font-medium">SFC编号</th>
              <th className="text-left px-3 py-2.5 font-medium">基金名称(EN)</th>
              <th className="text-left px-3 py-2.5 font-medium">ISIN</th>
              <th className="text-center px-3 py-2.5 font-medium">费用率</th>
              <th className="text-center px-3 py-2.5 font-medium">产品类型</th>
              <th className="text-center px-3 py-2.5 font-medium">§5.1A衍生品</th>
              <th className="text-center px-3 py-2.5 font-medium">§5.5复杂</th>
              <th className="text-left px-3 py-2.5 font-medium">注册地</th>
              <th className="text-left px-3 py-2.5 font-medium">管理人</th>
            </tr>
          </thead>
          <tbody>
            {loading
              ? Array.from({ length: 8 }).map((_, i) => <LoadingRow key={i} cols={9} />)
              : filtered.map((f) => (
                  <tr key={f.id} className="border-b border-border/50 hover:bg-surface-hover">
                    <td className="px-3 py-2.5 font-mono text-xs text-muted">{f.sfc_authorization_no}</td>
                    <td className="px-3 py-2.5 font-medium text-ink max-w-[200px] truncate" title={f.fund_name_en}>
                      {f.fund_name_en}
                    </td>
                    <td className="px-3 py-2.5 font-mono text-xs max-w-[120px] truncate" title={f.isin || ""}>
                      {f.isin ? (
                        <a href={`https://www.isin.org/isin-database/?isin=${f.isin}`} target="_blank" rel="noreferrer"
                           className="text-primary hover:underline">
                          {f.isin}
                        </a>
                      ) : <span className="text-muted">-</span>}
                    </td>
                    <td className="px-3 py-2.5 text-center text-xs">
                      {f.expense_ratio_pct != null ? (
                        <span className="text-ink">{f.expense_ratio_pct.toFixed(2)}%</span>
                      ) : f.management_fee_pct != null ? (
                        <span className="text-muted">{f.management_fee_pct.toFixed(2)}%</span>
                      ) : (
                        <span className="text-muted">-</span>
                      )}
                    </td>
                    <td className="px-3 py-2.5 text-center">
                      {f.complex_product_type && f.complex_product_type !== "non_complex" ? (
                        <span className={`text-xs px-2 py-0.5 rounded-full ${TYPE_COLORS[f.complex_product_type] || ""}`}>
                          {TYPE_LABELS[f.complex_product_type] || f.complex_product_type}
                        </span>
                      ) : (
                        <span className="text-xs text-muted">普通</span>
                      )}
                    </td>
                    <td className="px-3 py-2.5 text-center">
                      <span className={`text-xs px-2 py-0.5 rounded-full ${f.is_derivative_product ? FLAG_COLORS.yes : FLAG_COLORS.no}`}>
                        {f.is_derivative_product ? "是" : "否"}
                      </span>
                    </td>
                    <td className="px-3 py-2.5 text-center">
                      <span className={`text-xs px-2 py-0.5 rounded-full ${f.is_complex_product ? FLAG_COLORS.yes : FLAG_COLORS.no}`}>
                        {f.is_complex_product ? "是" : "否"}
                      </span>
                    </td>
                    <td className="px-3 py-2.5 text-muted text-xs">{f.domicile || "-"}</td>
                    <td className="px-3 py-2.5 max-w-[160px] truncate text-muted text-xs" title={f.fund_manager_name_en || ""}>
                      {f.fund_manager_name_en || "-"}
                    </td>
                  </tr>
                ))}
          </tbody>
        </table>
      </div>
      {!loading && filtered.length === 0 && (
        <div className="text-center py-12 text-muted text-sm">暂无基金数据 (可通过 import CSV 导入)</div>
      )}
    </section>
  );
}

// ═══════════════════════════════════════════════════════════
//  Tab 2: Complex Products (复杂产品)
// ═══════════════════════════════════════════════════════════

function ComplexProductsTab() {
  const [funds, setFunds] = useState<HKFund[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [view, setView] = useState<"complex" | "derivative">("complex");

  useEffect(() => {
    const fetcher = view === "complex" ? fetchHKComplexFunds(200) : fetchHKDerivativeFunds(200);
    fetcher
      .then(setFunds)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [view]);

  if (error) return <div className="text-center py-12 text-red-500 text-sm">加载失败: {error}</div>;

  const byType: Record<string, HKFund[]> = {};
  funds.forEach((f) => {
    const type = f.complex_product_type || "non_complex";
    if (!byType[type]) byType[type] = [];
    byType[type].push(f);
  });

  const sortedTypes = Object.keys(byType).sort((a, b) => byType[b].length - byType[a].length);

  return (
    <section>
      <h2 className="text-sm font-medium text-muted mb-3">
        {view === "complex" ? "§5.5 复杂产品 — 需适应性评估" : "§5.1A 衍生产品 — 需衍生品知识评估 + 财务能力检查"} ({funds.length})
      </h2>

      {/* Regulatory info card */}
      <div className="glass rounded-xl p-3 mb-3 text-xs text-muted leading-relaxed">
        <p className="font-medium text-ink mb-1">SFC 监管框架 — 双层独立分类</p>
        <p><span className="text-red-500 font-medium">§5.1A 衍生产品</span>: 按金融性质定义（价值衍生自相关资产）。净衍生敞口 &gt; 50% NAV → 衍生基金。要求客户衍生知识评估 + 财务能力检查。</p>
        <p className="mt-1"><span className="text-amber-500 font-medium">§5.5 复杂产品</span>: 按零售投资者可理解性定义（六因素测试）。因素① = 是否为衍生产品。要求适配性评估 + 最低产品资料 + 警告声明。交易所买卖复杂产品在无招揽时部分豁免。</p>
      </div>

      {/* View toggle */}
      <div className="flex gap-3 mb-4">
        <button
          onClick={() => setView("complex")}
          className={`text-xs px-3 py-1.5 rounded-lg transition-all ${
            view === "complex" ? "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400 font-medium" : "glass text-muted hover:text-ink"
          }`}
        >
          §5.5 复杂产品
        </button>
        <button
          onClick={() => setView("derivative")}
          className={`text-xs px-3 py-1.5 rounded-lg transition-all ${
            view === "derivative" ? "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400 font-medium" : "glass text-muted hover:text-ink"
          }`}
        >
          §5.1A 衍生产品
        </button>
      </div>

      {sortedTypes.map((type) => {
        const items = byType[type];
        return (
          <div key={type} className="mb-4">
            <h3 className="text-xs font-medium text-muted mb-2">
              {TYPE_LABELS[type] || type} ({items.length})
            </h3>
            <div className="glass rounded-xl overflow-hidden">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-muted text-xs">
                    <th className="text-left px-3 py-2.5 font-medium">基金名称</th>
                    <th className="text-center px-3 py-2.5 font-medium">§5.1A衍生品</th>
                    <th className="text-center px-3 py-2.5 font-medium">§5.5复杂</th>
                    <th className="text-left px-3 py-2.5 font-medium">判定原因</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((f) => (
                    <tr key={f.id} className="border-b border-border/50 hover:bg-surface-hover">
                      <td className="px-3 py-2.5">
                        <div className="font-medium text-ink text-sm">{f.fund_name_en}</div>
                        {f.fund_name_cn && <div className="text-xs text-muted">{f.fund_name_cn}</div>}
                      </td>
                      <td className="px-3 py-2.5 text-center">
                        <span className={`text-xs px-2 py-0.5 rounded-full ${f.is_derivative_product ? FLAG_COLORS.yes : FLAG_COLORS.no}`}>
                          {f.is_derivative_product ? "是" : "否"}
                        </span>
                      </td>
                      <td className="px-3 py-2.5 text-center">
                        <span className={`text-xs px-2 py-0.5 rounded-full ${f.is_complex_product ? FLAG_COLORS.yes : FLAG_COLORS.no}`}>
                          {f.is_complex_product ? "是" : "否"}
                        </span>
                      </td>
                      <td className="px-3 py-2.5 text-xs text-muted max-w-[300px] truncate" title={f.classification_reason || ""}>
                        {f.classification_reason || "-"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        );
      })}
      {!loading && funds.length === 0 && (
        <div className="text-center py-12 text-muted text-sm">暂无数据</div>
      )}
    </section>
  );
}

// ═══════════════════════════════════════════════════════════
//  Tab 3: Manager KYP (管理人尽调)
// ═══════════════════════════════════════════════════════════

function ManagerKypTab() {
  const [managers, setManagers] = useState<HKFundManager[]>([]);
  const [stats, setStats] = useState<HKManagerStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [enforcementOnly, setEnforcementOnly] = useState(false);

  useEffect(() => {
    Promise.all([
      fetchHKManagers({ limit: 500 }),
      fetchHKManagerStats(),
    ])
      .then(([mgrs, stats]) => {
        setManagers(mgrs);
        setStats(stats);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (error) return <div className="text-center py-12 text-red-500 text-sm">加载失败: {error}</div>;

  const filtered = enforcementOnly
    ? managers.filter((m) => m.has_sfc_enforcement_history)
    : managers;

  return (
    <section>
      {stats && (
        <div className="grid grid-cols-3 gap-3 mb-4">
          {[
            { label: "活跃管理人", value: stats.total },
            { label: "Type 9 资管", value: stats.type9_count },
            { label: "有执法记录", value: stats.with_enforcement, highlight: true },
          ].map((s) => (
            <div
              key={s.label}
              className={`glass rounded-xl px-4 py-3 text-center ${
                (s as { highlight?: boolean }).highlight ? "ring-1 ring-red-400/50" : ""
              }`}
            >
              <div className="text-2xl font-bold text-ink">{s.value}</div>
              <div className="text-xs text-muted mt-0.5">{s.label}</div>
            </div>
          ))}
        </div>
      )}

      <div className="flex gap-3 mb-3">
        <button
          onClick={() => setEnforcementOnly(false)}
          className={`text-xs px-3 py-1.5 rounded-lg transition-all ${
            !enforcementOnly ? "bg-primary-a15 text-primary font-medium" : "glass text-muted hover:text-ink"
          }`}
        >
          全部 ({managers.length})
        </button>
        <button
          onClick={() => setEnforcementOnly(true)}
          className={`text-xs px-3 py-1.5 rounded-lg transition-all ${
            enforcementOnly ? "bg-primary-a15 text-primary font-medium" : "glass text-muted hover:text-ink"
          }`}
        >
          有执法记录 ({managers.filter((m) => m.has_sfc_enforcement_history).length})
        </button>
      </div>

      <div className="glass rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-muted text-xs">
              <th className="text-left px-3 py-2.5 font-medium">CE编号</th>
              <th className="text-left px-3 py-2.5 font-medium">公司名称(EN)</th>
              <th className="text-left px-3 py-2.5 font-medium">公司名称(CN)</th>
              <th className="text-center px-3 py-2.5 font-medium">牌照类型</th>
              <th className="text-center px-3 py-2.5 font-medium">状态</th>
              <th className="text-center px-3 py-2.5 font-medium">RA 9</th>
              <th className="text-center px-3 py-2.5 font-medium">执法记录</th>
            </tr>
          </thead>
          <tbody>
            {loading
              ? Array.from({ length: 5 }).map((_, i) => <LoadingRow key={i} cols={7} />)
              : filtered.map((m) => (
                  <tr key={m.id} className="border-b border-border/50 hover:bg-surface-hover">
                    <td className="px-3 py-2.5 font-mono text-xs text-ink">{m.ce_number}</td>
                    <td className="px-3 py-2.5 font-medium text-ink max-w-[200px] truncate" title={m.company_name_en}>
                      {m.company_name_en}
                    </td>
                    <td className="px-3 py-2.5 max-w-[150px] truncate text-muted text-xs" title={m.company_name_cn || ""}>
                      {m.company_name_cn || "-"}
                    </td>
                    <td className="px-3 py-2.5 text-center text-xs text-muted">{m.license_type}</td>
                    <td className="px-3 py-2.5 text-center">
                      <span className={`text-xs px-2 py-0.5 rounded-full ${
                        m.license_status === "active"
                          ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-400"
                          : "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400"
                      }`}>
                        {LICENSE_LABELS[m.license_status] || m.license_status}
                      </span>
                    </td>
                    <td className="px-3 py-2.5 text-center">
                      {m.regulated_activity_9 ? (
                        <span className="text-xs px-2 py-0.5 rounded-full bg-sky-100 text-sky-800 dark:bg-sky-900/30 dark:text-sky-400">Type 9</span>
                      ) : "-"}
                    </td>
                    <td className="px-3 py-2.5 text-center">
                      {m.has_sfc_enforcement_history ? (
                        <span className="text-xs px-2 py-0.5 rounded-full bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400 font-medium">
                          {m.enforcement_count}项
                        </span>
                      ) : (
                        <span className="text-xs text-muted">无</span>
                      )}
                    </td>
                  </tr>
                ))}
          </tbody>
        </table>
      </div>
      {!loading && filtered.length === 0 && (
        <div className="text-center py-12 text-muted text-sm">暂无管理人数据</div>
      )}
    </section>
  );
}

// ═══════════════════════════════════════════════════════════
//  Tab 4: Regulatory Tracking (监管追踪)
// ═══════════════════════════════════════════════════════════

function RegulatoryTab() {
  const [managers, setManagers] = useState<HKFundManager[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    fetchHKManagers({ has_enforcement: true, limit: 200 })
      .then(setManagers)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (error) return <div className="text-center py-12 text-red-500 text-sm">加载失败: {error}</div>;

  const byActivity: Record<string, number> = {};
  managers.forEach((m) => {
    const key = m.regulated_activity_9 ? "Type 9 资管" :
                m.regulated_activity_1 ? "Type 1 证券交易" :
                m.regulated_activity_4 ? "Type 4 顾问" : "其他";
    byActivity[key] = (byActivity[key] || 0) + 1;
  });

  return (
    <section>
      <h2 className="text-sm font-medium text-muted mb-3">
        管理人监管追踪 ({managers.length}家有记录)
      </h2>

      {/* Breakdown */}
      <div className="flex flex-wrap gap-3 mb-4">
        {Object.entries(byActivity).map(([key, cnt]) => (
          <div key={key} className="glass rounded-xl px-4 py-2 text-center">
            <div className="text-lg font-bold text-ink">{cnt}</div>
            <div className="text-xs text-muted">{key}</div>
          </div>
        ))}
      </div>

      <div className="glass rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-muted text-xs">
              <th className="text-left px-3 py-2.5 font-medium">CE编号</th>
              <th className="text-left px-3 py-2.5 font-medium">公司名称</th>
              <th className="text-center px-3 py-2.5 font-medium">牌照</th>
              <th className="text-center px-3 py-2.5 font-medium">执法次数</th>
              <th className="text-left px-3 py-2.5 font-medium">最近更新</th>
            </tr>
          </thead>
          <tbody>
            {loading
              ? Array.from({ length: 5 }).map((_, i) => <LoadingRow key={i} cols={5} />)
              : managers.map((m) => (
                  <tr key={m.id} className="border-b border-border/50 hover:bg-surface-hover">
                    <td className="px-3 py-2.5 font-mono text-xs text-ink">{m.ce_number}</td>
                    <td className="px-3 py-2.5">
                      <div className="font-medium text-ink text-sm">{m.company_name_en}</div>
                      {m.company_name_cn && <div className="text-xs text-muted">{m.company_name_cn}</div>}
                    </td>
                    <td className="px-3 py-2.5 text-center text-xs text-muted">{m.license_type}</td>
                    <td className="px-3 py-2.5 text-center">
                      <span className="text-xs px-2 py-0.5 rounded-full bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400 font-medium">
                        {m.enforcement_count}
                      </span>
                    </td>
                    <td className="px-3 py-2.5 text-xs text-muted">
                      {m.license_effective_date?.slice(0, 10) || "-"}
                    </td>
                  </tr>
                ))}
          </tbody>
        </table>
      </div>
      {!loading && managers.length === 0 && (
        <div className="text-center py-12 text-muted text-sm">暂无监管记录</div>
      )}
    </section>
  );
}

// ═══════════════════════════════════════════════════════════
//  Tab 5: KYP Dashboard (产品尽调)
// ═══════════════════════════════════════════════════════════

const KYP_DIM_LABELS: Record<string, string> = {
  product_structure: "产品结构",
  risk_profile: "风险概况",
  complexity: "复杂性分类",
  derivative_class: "衍生品分类",
  issuer_assessment: "发行人评估",
  fees_charges: "费用与佣金",
  liquidity_lockup: "流动性/锁定期",
  valuation_pricing: "估值与定价",
  credit_quality: "信用质量",
  key_terms: "关键条款",
};

const STATUS_COLORS: Record<string, string> = {
  reviewed: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-400",
  approved: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-400",
  in_progress: "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400",
  pending: "bg-slate-100 text-slate-600 dark:bg-slate-900/30 dark:text-slate-400",
};

const STATUS_LABELS: Record<string, string> = {
  reviewed: "已复核",
  approved: "已批准",
  in_progress: "进行中",
  pending: "待评估",
};

const RISK_CAT_COLORS: Record<string, string> = {
  Low: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-400",
  "Medium-Low": "bg-cyan-100 text-cyan-800 dark:bg-cyan-900/30 dark:text-cyan-400",
  Medium: "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400",
  "Medium-High": "bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-400",
  High: "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400",
};

function KypDashboardTab() {
  const [funds, setFunds] = useState<HKFund[]>([]);
  const [selectedFund, setSelectedFund] = useState<HKFund | null>(null);
  const [dimensions, setDimensions] = useState<HK_KypDimension[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchHKFunds({ limit: 500 }).then((f) => { setFunds(f); setLoading(false); });
  }, []);

  useEffect(() => {
    if (!selectedFund) return;
    setLoading(true);
    fetchHK_KypDimensions(selectedFund.id).then((d) => {
      setDimensions(d);
      setLoading(false);
    });
  }, [selectedFund]);

  const reviewedCount = dimensions.filter(d => d.assessment_status === "reviewed" || d.assessment_status === "approved").length;
  const pct = dimensions.length > 0 ? Math.round((reviewedCount / 10) * 100) : 0;

  return (
    <section className="max-w-4xl mx-auto mt-6 px-4">
      {/* Fund selector */}
      <div className="mb-4">
        <select
          className="w-full px-4 py-2 rounded-lg border border-border bg-surface text-sm"
          value={selectedFund?.id ?? ""}
          onChange={(e) => {
            const f = funds.find(x => x.id === Number(e.target.value));
            setSelectedFund(f || null);
          }}
        >
          <option value="">-- 选择基金 --</option>
          {funds.map((f) => (
            <option key={f.id} value={f.id}>{f.fund_name_en}</option>
          ))}
        </select>
      </div>

      {selectedFund && (
        <>
          {/* Progress bar */}
          <div className="glass rounded-xl p-4 mb-4">
            <div className="flex items-center justify-between mb-2">
              <span className="text-sm font-medium">KYP 完成度</span>
              <span className="text-sm text-muted">{reviewedCount}/10 ({pct}%)</span>
            </div>
            <div className="w-full h-2 bg-surface-hover rounded-full overflow-hidden">
              <div
                className={`h-full rounded-full transition-all ${pct === 100 ? "bg-emerald-500" : pct >= 50 ? "bg-amber-500" : "bg-slate-400"}`}
                style={{ width: `${pct}%` }}
              />
            </div>
          </div>

          {/* Dimension cards */}
          <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
            {dimensions.map((d) => (
              <div
                key={d.dimension}
                className="glass rounded-lg p-3 text-center cursor-pointer hover:shadow-md transition-shadow"
                title={d.findings || ""}
              >
                <div className="text-xs text-muted mb-1">{KYP_DIM_LABELS[d.dimension] || d.dimension}</div>
                <span className={`inline-block text-xs px-2 py-0.5 rounded-full ${STATUS_COLORS[d.assessment_status] || "bg-slate-100 text-slate-600"}`}>
                  {STATUS_LABELS[d.assessment_status] || d.assessment_status}
                </span>
                {d.score != null && (
                  <div className="text-xl font-semibold mt-1">{d.score}/5</div>
                )}
              </div>
            ))}
          </div>
        </>
      )}

      {!selectedFund && !loading && (
        <div className="text-center py-12 text-muted text-sm">选择一只基金查看 KYP 尽调维度</div>
      )}
    </section>
  );
}

// ═══════════════════════════════════════════════════════════
//  Tab 6: Risk Rating (风险评级)
// ═══════════════════════════════════════════════════════════

function RiskRatingTab() {
  const [ratings, setRatings] = useState<HK_FundRiskRating[]>([]);
  const [filter, setFilter] = useState<string>("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetchHK_AllRiskRatings(filter || undefined).then((r) => {
      setRatings(r);
      setLoading(false);
    });
  }, [filter]);

  const dist: Record<string, number> = {};
  ratings.forEach(r => { dist[r.risk_category] = (dist[r.risk_category] || 0) + 1; });

  return (
    <section className="max-w-4xl mx-auto mt-6 px-4">
      {/* Summary cards */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-4">
        {["Low", "Medium-Low", "Medium", "Medium-High", "High"].map(cat => (
          <div key={cat} className={`glass rounded-xl p-3 text-center ${filter === cat ? "ring-2 ring-primary" : ""}`}
               onClick={() => setFilter(filter === cat ? "" : cat)} style={{ cursor: "pointer" }}>
            <div className="text-xs text-muted">{cat}</div>
            <div className={`text-2xl font-bold ${RISK_CAT_COLORS[cat]?.split(" ")[0]?.replace("bg-", "text-") ?? "text-ink"}`}>
              {dist[cat] || 0}
            </div>
          </div>
        ))}
      </div>

      {/* Methodology note */}
      <div className="glass rounded-xl p-3 mb-4 text-xs text-muted">
        <strong>评级方法论 v1.0:</strong> 六因子加权评分 — 复杂度(25%) + 底层资产风险(25%) + 杠杆/衍生品(15%) + 流动性(15%) + 信用质量(10%) + 货币/国家风险(10%)。共五档: Low / Medium-Low / Medium / Medium-High / High。
      </div>

      {/* Rating table */}
      <div className="glass rounded-xl overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="border-b border-border">
            <tr>
              <th className="text-left px-4 py-2 text-xs text-muted">基金名称</th>
              <th className="text-left px-4 py-2 text-xs text-muted">管理人</th>
              <th className="text-center px-4 py-2 text-xs text-muted">评级</th>
              <th className="text-center px-4 py-2 text-xs text-muted">分数</th>
              <th className="text-center px-4 py-2 text-xs text-muted">衍生品</th>
              <th className="text-center px-4 py-2 text-xs text-muted">来源</th>
            </tr>
          </thead>
          <tbody>
            {loading && Array.from({ length: 5 }).map((_, i) => <LoadingRow key={i} cols={6} />)}
            {!loading && ratings.slice(0, 100).map((r) => (
              <tr key={r.fund_id} className="border-b border-border/40 hover:bg-surface-hover">
                <td className="px-4 py-2 max-w-xs truncate" title={r.fund_name_en}>{r.fund_name_en}</td>
                <td className="px-4 py-2 text-muted text-xs">{r.sfc_authorization_no}</td>
                <td className="px-4 py-2 text-center">
                  <span className={`text-xs px-2 py-0.5 rounded-full ${RISK_CAT_COLORS[r.risk_category] || ""}`}>{r.risk_category}</span>
                </td>
                <td className="px-4 py-2 text-center font-mono">{r.overall_risk_score}</td>
                <td className="px-4 py-2 text-center text-xs text-muted">{r.is_derivative_product ? "是" : "否"}</td>
                <td className="px-4 py-2 text-center text-xs text-muted">{r.is_automated ? "自动" : "人工"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

// ═══════════════════════════════════════════════════════════
//  Tab 7: Non-Authorized Funds (非认可基金)
// ═══════════════════════════════════════════════════════════

function NonAuthorizedFundsTab() {
  const [funds, setFunds] = useState<HK_NonAuthorizedFund[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<Partial<HK_NonAuthorizedFund>>({
    fund_name_en: "",
    fund_name_cn: "",
    isin: "",
    fund_type: "",
    domicile: "",
    currency: "",
    fund_manager_name_en: "",
    fund_manager_name_cn: "",
    distribution_restriction: "pi_only",
    min_investment_hkd: undefined,
    notes: "",
  });
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    fetchHK_NonAuthorizedFunds({ limit: 200 }).then((f) => {
      setFunds(f);
      setLoading(false);
    });
  }, []);

  async function handleCreate() {
    if (!form.fund_name_en?.trim()) return;
    setSubmitting(true);
    const stored = await createHK_NonAuthorizedFund([{ ...form, is_active: true, data_source: "manual" }]);
    if (stored > 0) {
      setShowForm(false);
      setForm({ fund_name_en: "", fund_name_cn: "", isin: "", fund_type: "", domicile: "",
        currency: "", fund_manager_name_en: "", fund_manager_name_cn: "",
        distribution_restriction: "pi_only", min_investment_hkd: undefined, notes: "" });
      const updated = await fetchHK_NonAuthorizedFunds({ limit: 200 });
      setFunds(updated);
    }
    setSubmitting(false);
  }

  return (
    <section className="max-w-4xl mx-auto mt-6 px-4">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-lg font-semibold">非认可基金</h2>
          <p className="text-xs text-muted mt-1">
            非 SFC 认可基金，仅限专业投资者 (PI: HK$8M+ portfolio) 分销。需更严格的产品尽调和适当性评估。
          </p>
        </div>
        <button
          onClick={() => setShowForm(!showForm)}
          className="text-sm px-4 py-2 rounded-lg bg-primary-a15 text-primary font-medium hover:bg-primary-a20 transition-all"
        >
          {showForm ? "取消" : "+ 新增"}
        </button>
      </div>

      {/* Create form */}
      {showForm && (
        <div className="glass rounded-xl p-4 mb-4">
          <h3 className="text-sm font-medium mb-3">新增非认可基金</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div>
              <label className="text-xs text-muted block mb-1">基金名称 (EN) *</label>
              <input className="w-full rounded-lg bg-surface px-3 py-1.5 text-sm border border-border focus:outline-none focus:border-primary"
                value={form.fund_name_en || ""} onChange={e => setForm({ ...form, fund_name_en: e.target.value })} />
            </div>
            <div>
              <label className="text-xs text-muted block mb-1">基金名称 (CN)</label>
              <input className="w-full rounded-lg bg-surface px-3 py-1.5 text-sm border border-border focus:outline-none focus:border-primary"
                value={form.fund_name_cn || ""} onChange={e => setForm({ ...form, fund_name_cn: e.target.value })} />
            </div>
            <div>
              <label className="text-xs text-muted block mb-1">ISIN</label>
              <input className="w-full rounded-lg bg-surface px-3 py-1.5 text-sm border border-border focus:outline-none focus:border-primary"
                value={form.isin || ""} onChange={e => setForm({ ...form, isin: e.target.value })} />
            </div>
            <div>
              <label className="text-xs text-muted block mb-1">基金类型</label>
              <select className="w-full rounded-lg bg-surface px-3 py-1.5 text-sm border border-border focus:outline-none focus:border-primary"
                value={form.fund_type || ""} onChange={e => setForm({ ...form, fund_type: e.target.value })}>
                <option value="">--</option>
                <option value="hedge_fund">Hedge Fund</option>
                <option value="private_equity">Private Equity</option>
                <option value="venture_capital">Venture Capital</option>
                <option value="real_estate">Real Estate</option>
                <option value="private_credit">Private Credit</option>
                <option value="crypto">Crypto / Digital Assets</option>
                <option value="other">Other</option>
              </select>
            </div>
            <div>
              <label className="text-xs text-muted block mb-1">注册地</label>
              <input className="w-full rounded-lg bg-surface px-3 py-1.5 text-sm border border-border focus:outline-none focus:border-primary"
                value={form.domicile || ""} onChange={e => setForm({ ...form, domicile: e.target.value })} />
            </div>
            <div>
              <label className="text-xs text-muted block mb-1">币种</label>
              <select className="w-full rounded-lg bg-surface px-3 py-1.5 text-sm border border-border focus:outline-none focus:border-primary"
                value={form.currency || ""} onChange={e => setForm({ ...form, currency: e.target.value })}>
                <option value="">--</option>
                {["USD", "HKD", "CNY", "EUR", "JPY", "GBP", "SGD", "AUD"].map(c => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>
            <div>
              <label className="text-xs text-muted block mb-1">管理人名 (EN)</label>
              <input className="w-full rounded-lg bg-surface px-3 py-1.5 text-sm border border-border focus:outline-none focus:border-primary"
                value={form.fund_manager_name_en || ""} onChange={e => setForm({ ...form, fund_manager_name_en: e.target.value })} />
            </div>
            <div>
              <label className="text-xs text-muted block mb-1">分销限制</label>
              <select className="w-full rounded-lg bg-surface px-3 py-1.5 text-sm border border-border focus:outline-none focus:border-primary"
                value={form.distribution_restriction || "pi_only"} onChange={e => setForm({ ...form, distribution_restriction: e.target.value })}>
                <option value="pi_only">PI Only (专业投资者)</option>
                <option value="pi_800k">PI (HK$8M portfolio)</option>
                <option value="institutional_only">仅机构</option>
                <option value="offshore_only">仅离岸</option>
              </select>
            </div>
            <div>
              <label className="text-xs text-muted block mb-1">最低投资额 (HKD)</label>
              <input type="number" className="w-full rounded-lg bg-surface px-3 py-1.5 text-sm border border-border focus:outline-none focus:border-primary"
                value={form.min_investment_hkd ?? ""} onChange={e => setForm({ ...form, min_investment_hkd: e.target.value ? Number(e.target.value) : undefined })} />
            </div>
          </div>
          <div className="mt-3">
            <label className="text-xs text-muted block mb-1">备注</label>
            <textarea className="w-full rounded-lg bg-surface px-3 py-1.5 text-sm border border-border focus:outline-none focus:border-primary" rows={2}
              value={form.notes || ""} onChange={e => setForm({ ...form, notes: e.target.value })} />
          </div>
          <div className="mt-4 flex gap-2">
            <button className="text-sm px-4 py-2 rounded-lg bg-primary text-white font-medium hover:bg-primary/90 disabled:opacity-50"
              onClick={handleCreate} disabled={submitting || !form.fund_name_en?.trim()}>
              {submitting ? "提交中..." : "确认新增"}
            </button>
            <button className="text-sm px-4 py-2 rounded-lg bg-surface text-muted hover:text-ink"
              onClick={() => setShowForm(false)}>取消</button>
          </div>
        </div>
      )}

      {/* Fund list */}
      <div className="glass rounded-xl overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="border-b border-border">
            <tr>
              <th className="text-left px-4 py-2 text-xs text-muted">基金名称</th>
              <th className="text-left px-4 py-2 text-xs text-muted">类型</th>
              <th className="text-left px-4 py-2 text-xs text-muted">注册地</th>
              <th className="text-center px-4 py-2 text-xs text-muted">分销限制</th>
              <th className="text-right px-4 py-2 text-xs text-muted">最低投资(HKD)</th>
              <th className="text-center px-4 py-2 text-xs text-muted">状态</th>
            </tr>
          </thead>
          <tbody>
            {loading && Array.from({ length: 5 }).map((_, i) => <LoadingRow key={i} cols={6} />)}
            {!loading && funds.length === 0 && (
              <tr><td colSpan={6} className="text-center py-8 text-muted text-sm">暂无数据。点击"+ 新增"添加非认可基金。</td></tr>
            )}
            {!loading && funds.map((f) => (
              <tr key={f.id} className="border-b border-border/40 hover:bg-surface-hover">
                <td className="px-4 py-2 max-w-xs">
                  <div className="truncate font-medium" title={f.fund_name_en}>{f.fund_name_en}</div>
                  {f.fund_name_cn && <div className="text-xs text-muted truncate">{f.fund_name_cn}</div>}
                </td>
                <td className="px-4 py-2 text-xs text-muted">{f.fund_type || "—"}</td>
                <td className="px-4 py-2 text-xs text-muted">{f.domicile || "—"}</td>
                <td className="px-4 py-2 text-center">
                  <span className={`text-xs px-2 py-0.5 rounded-full ${
                    f.distribution_restriction === "pi_only" ? "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400" :
                    f.distribution_restriction === "institutional_only" ? "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400" :
                    "bg-slate-100 text-slate-600 dark:bg-slate-900/30 dark:text-slate-400"
                  }`}>
                    {f.distribution_restriction === "pi_only" ? "PI Only" :
                     f.distribution_restriction === "pi_800k" ? "PI HK$8M" :
                     f.distribution_restriction === "institutional_only" ? "仅机构" :
                     f.distribution_restriction === "offshore_only" ? "仅离岸" :
                     f.distribution_restriction || "—"}
                  </span>
                </td>
                <td className="px-4 py-2 text-right font-mono text-xs">{f.min_investment_hkd != null ? f.min_investment_hkd.toLocaleString() : "—"}</td>
                <td className="px-4 py-2 text-center">
                  <span className={`text-xs px-2 py-0.5 rounded-full ${f.is_active ? "bg-emerald-100 text-emerald-800" : "bg-slate-100 text-slate-700"}`}>
                    {f.is_active ? "活跃" : "停用"}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

// ═══════════════════════════════════════════════════════════
//  Tab 8: ETF Connectors (数据连接器)
// ═══════════════════════════════════════════════════════════

function ConnectorsTab() {
  const [status, setStatus] = useState<HKManagerScrapeStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    fetchHKManagerScrapeStatus()
      .then((s) => { setStatus(s); setLoading(false); })
      .catch((e) => { setError(e.message); setLoading(false); });
  }, []);

  if (error) return <div className="text-center py-12 text-red-500 text-sm">加载失败: {error}</div>;
  if (loading) return <div className="text-center py-12 text-muted text-sm">加载中...</div>;
  if (!status) return null;

  return (
    <section>
      <h2 className="text-sm font-medium text-muted mb-3">
        ETF 数据连接器 — {status.registered_connectors} 个已注册管理器
      </h2>

      {/* Connected managers */}
      <h3 className="text-xs font-medium text-ink mb-2">已连接的基金管理人</h3>
      <div className="glass rounded-xl overflow-hidden mb-4">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-muted text-xs">
              <th className="text-left px-3 py-2.5 font-medium">CE编号</th>
              <th className="text-left px-3 py-2.5 font-medium">管理人</th>
              <th className="text-left px-3 py-2.5 font-medium">网站</th>
            </tr>
          </thead>
          <tbody>
            {status.connectors.map((c) => (
              <tr key={c.ce_number} className="border-b border-border/50 hover:bg-surface-hover">
                <td className="px-3 py-2.5 font-mono text-xs text-ink">{c.ce_number}</td>
                <td className="px-3 py-2.5 text-xs text-ink">{c.company_name_en}</td>
                <td className="px-3 py-2.5 text-xs">
                  {c.website ? (
                    <a href={c.website} target="_blank" rel="noreferrer" className="text-primary hover:underline">
                      {c.website}
                    </a>
                  ) : "-"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Managers needing connectors */}
      {status.top_managers_without_connectors.length > 0 && (
        <>
          <h3 className="text-xs font-medium text-ink mb-2">
            待优先构建连接器 ({status.managers_needing_connectors} 个管理器未覆盖)
          </h3>
          <div className="glass rounded-xl overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-muted text-xs">
                  <th className="text-left px-3 py-2.5 font-medium">CE编号</th>
                  <th className="text-left px-3 py-2.5 font-medium">管理人</th>
                  <th className="text-center px-3 py-2.5 font-medium">基金数</th>
                </tr>
              </thead>
              <tbody>
                {status.top_managers_without_connectors.map((m) => (
                  <tr key={m.ce_number} className="border-b border-border/50">
                    <td className="px-3 py-2.5 font-mono text-xs text-muted">{m.ce_number}</td>
                    <td className="px-3 py-2.5 text-xs">{m.company_name_en}</td>
                    <td className="px-3 py-2.5 text-center text-xs">
                      <span className="px-2 py-0.5 rounded-full bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400">
                        {m.fund_count}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {/* Data flow info */}
      <div className="mt-4 glass rounded-xl p-3 text-xs text-muted">
        <p className="font-medium text-ink mb-1">数据来源说明</p>
        <p>ETF ISIN 数据来自 <strong>HKEX ListOfSecurities.xlsx</strong>（HKEX 官方证券列表，每日更新）。</p>
        <p className="mt-1">UTMF（非交易所交易）基金的 ISIN 需要从基金管理人网站爬取。目前已覆盖 15 家 ETF 管理人（共 266 个 ETF）。</p>
      </div>
    </section>
  );
}

// ═══════════════════════════════════════════════════════════
//  Main component
// ═══════════════════════════════════════════════════════════

import TemplateEditor from "./TemplateEditor";
import RatingResultsTab from "./RatingResults";

const TABS = [
  { key: "funds", label: "基金清单" },
  { key: "kyp", label: "产品尽调" },
  { key: "complex", label: "复杂产品" },
  { key: "risk", label: "风险评级" },
  { key: "managers", label: "管理人尽调" },
  { key: "regulatory", label: "监管追踪" },
  { key: "non_auth", label: "非认可基金" },
  { key: "connectors", label: "数据连接器" },
  { key: "templates", label: "模板编辑" },
  { key: "my_ratings", label: "我的评级" },
] as const;

type TabKey = (typeof TABS)[number]["key"];

export default function HkFundsContent() {
  const [tab, setTab] = useState<TabKey>("funds");

  return (
    <>
      {tab === "funds" && <FundListTab />}
      {tab === "kyp" && <KypDashboardTab />}
      {tab === "complex" && <ComplexProductsTab />}
      {tab === "risk" && <RiskRatingTab />}
      {tab === "managers" && <ManagerKypTab />}
      {tab === "regulatory" && <RegulatoryTab />}
      {tab === "non_auth" && <NonAuthorizedFundsTab />}
      {tab === "connectors" && <ConnectorsTab />}
      {tab === "templates" && <TemplateEditor />}
      {tab === "my_ratings" && <RatingResultsTab />}

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
