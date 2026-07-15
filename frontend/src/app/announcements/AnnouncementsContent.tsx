"use client";

import { useRouter } from "next/navigation";
import type { Announcement, TrackedCompany } from "@/lib/api";

const MARKET_LABELS: Record<string, string> = {
  us: "US",
  hk: "HK",
  cn: "A股",
};

const MARKET_BADGES: Record<string, string> = {
  us: "bg-blue-500/10 text-blue-400 border-blue-500/20",
  hk: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
  cn: "bg-amber-500/10 text-amber-400 border-amber-500/20",
};

const SOURCE_LABELS: Record<string, string> = {
  sec: "SEC",
  hkex: "HKEX",
  cninfo: "深交所",
};

export default function AnnouncementsContent({
  announcements,
  companies,
  activeMarket,
  activeTicker,
}: {
  announcements: Announcement[];
  companies: TrackedCompany[];
  activeMarket?: string;
  activeTicker?: string;
}) {
  const router = useRouter();

  const updateFilter = (key: string, value: string) => {
    const params = new URLSearchParams();
    if (key === "market") {
      if (value) params.set("market", value);
    } else if (key === "ticker") {
      const currentMarket = activeMarket || "";
      if (currentMarket) params.set("market", currentMarket);
      if (value) params.set("ticker", value);
    }
    const qs = params.toString();
    router.push(`/announcements${qs ? `?${qs}` : ""}`);
  };

  // Group companies by market for the dropdown
  const companiesByMarket: Record<string, TrackedCompany[]> = {};
  for (const c of companies) {
    if (!companiesByMarket[c.market]) companiesByMarket[c.market] = [];
    companiesByMarket[c.market].push(c);
  }

  return (
    <div className="flex flex-col gap-4">
      {/* Filter bar */}
      <div className="flex flex-wrap gap-2 items-center">
        <span className="text-xs text-muted mr-1">筛选:</span>

        {/* Market filter */}
        <select
          value={activeMarket || ""}
          onChange={(e) => updateFilter("market", e.target.value)}
          className="text-xs px-3 py-1.5 rounded-lg bg-surface border border-border text-ink"
        >
          <option value="">全部市场</option>
          <option value="us">US</option>
          <option value="hk">HK</option>
          <option value="cn">A股</option>
        </select>

        {/* Ticker filter — show tickers for active market */}
        {activeMarket && companiesByMarket[activeMarket] && (
          <select
            value={activeTicker || ""}
            onChange={(e) => updateFilter("ticker", e.target.value)}
            className="text-xs px-3 py-1.5 rounded-lg bg-surface border border-border text-ink"
          >
            <option value="">全部公司</option>
            {companiesByMarket[activeMarket].map((c) => (
              <option key={c.ticker} value={c.ticker}>
                {c.ticker} — {c.company_name} ({c.announcement_count})
              </option>
            ))}
          </select>
        )}

        {announcements.length > 0 && (
          <span className="text-xs text-muted ml-auto">
            共 {announcements.length} 条
          </span>
        )}
      </div>

      {/* Announcements table */}
      {announcements.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 gap-3">
          <p className="text-muted text-sm">暂无公告数据</p>
          <p className="text-muted text-xs">
            请先运行 pipeline 拉取数据
          </p>
        </div>
      ) : (
        <div className="glass rounded-xl overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-xs text-muted">
                  <th className="text-left px-4 py-2.5 font-medium">日期</th>
                  <th className="text-left px-4 py-2.5 font-medium">市场</th>
                  <th className="text-left px-4 py-2.5 font-medium">代码</th>
                  <th className="text-left px-4 py-2.5 font-medium">公司</th>
                  <th className="text-left px-4 py-2.5 font-medium">标题</th>
                  <th className="text-left px-4 py-2.5 font-medium">类型</th>
                  <th className="text-left px-4 py-2.5 font-medium">来源</th>
                </tr>
              </thead>
              <tbody>
                {announcements.map((ann) => (
                  <tr
                    key={ann.id}
                    className="border-b border-border/50 hover:bg-surface-hover transition-colors"
                  >
                    <td className="px-4 py-2.5 whitespace-nowrap text-muted">
                      {ann.announcement_date}
                    </td>
                    <td className="px-4 py-2.5">
                      <span
                        className={`text-xs px-1.5 py-0.5 rounded border ${MARKET_BADGES[ann.market] || "bg-surface border-border"}`}
                      >
                        {MARKET_LABELS[ann.market] || ann.market}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 font-mono text-xs">
                      {ann.ticker}
                    </td>
                    <td className="px-4 py-2.5 whitespace-nowrap">
                      {ann.company_name}
                    </td>
                    <td className="px-4 py-2.5 max-w-xs truncate">
                      {ann.source_url ? (
                        <a
                          href={ann.source_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-primary hover:underline"
                        >
                          {ann.title || "(无标题)"}
                        </a>
                      ) : (
                        ann.title || "(无标题)"
                      )}
                    </td>
                    <td className="px-4 py-2.5">
                      <span className="text-xs px-1.5 py-0.5 rounded bg-surface border border-border text-muted">
                        {ann.filing_type || "—"}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 text-xs text-muted">
                      {SOURCE_LABELS[ann.source] || ann.source}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
