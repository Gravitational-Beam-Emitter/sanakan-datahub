"use client";

import { CorpAction } from "@/lib/api";

const TYPE_LABELS: Record<string, string> = {
  merger_acquisition: "并购重组",
  equity_change: "股权变更",
  securities_issuance: "证券发行",
  delisting: "退市",
  bankruptcy: "破产",
  dividend: "股利",
  stock_split: "股票拆分",
  buyback: "股份回购",
  earnings: "业绩公告",
  other: "其他",
};

const TYPE_COLORS: Record<string, string> = {
  merger_acquisition: "bg-amber-500/15 text-amber-400",
  equity_change: "bg-blue-500/15 text-blue-400",
  securities_issuance: "bg-purple-500/15 text-purple-400",
  delisting: "bg-red-500/15 text-red-400",
  bankruptcy: "bg-red-700/15 text-red-500",
  dividend: "bg-green-500/15 text-green-400",
  stock_split: "bg-cyan-500/15 text-cyan-400",
  buyback: "bg-emerald-500/15 text-emerald-400",
  earnings: "bg-yellow-500/15 text-yellow-400",
  other: "bg-gray-500/15 text-gray-400",
};

export default function CorpActionsTable({ actions }: { actions: CorpAction[] }) {
  if (!actions || actions.length === 0) {
    return (
      <div className="text-center py-12 text-muted text-sm">
        暂无公司行动数据
      </div>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border text-muted text-xs">
            <th className="text-left py-2 px-3 font-medium">Ticker</th>
            <th className="text-left py-2 px-3 font-medium hidden sm:table-cell">公司</th>
            <th className="text-left py-2 px-3 font-medium">行动类型</th>
            <th className="text-left py-2 px-3 font-medium hidden md:table-cell">8-K Items</th>
            <th className="text-left py-2 px-3 font-medium hidden lg:table-cell">描述</th>
            <th className="text-left py-2 px-3 font-medium hidden sm:table-cell">链接</th>
          </tr>
        </thead>
        <tbody>
          {actions.map((a, i) => (
            <tr
              key={`${a.ticker}-${a.filing_date}-${i}`}
              className="border-b border-border/50 hover:bg-surface-hover transition-colors"
            >
              <td className="py-2 px-3 font-mono font-medium tabular-nums">{a.ticker}</td>
              <td className="py-2 px-3 text-muted hidden sm:table-cell max-w-[180px] truncate">
                {a.company_name}
              </td>
              <td className="py-2 px-3">
                <span
                  className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${
                    TYPE_COLORS[a.action_type] || TYPE_COLORS.other
                  }`}
                >
                  {TYPE_LABELS[a.action_type] || a.action_type}
                </span>
              </td>
              <td className="py-2 px-3 font-mono text-xs text-muted hidden md:table-cell">
                {a.item_numbers || "—"}
              </td>
              <td className="py-2 px-3 text-muted hidden lg:table-cell max-w-[300px] truncate">
                {a.description || "—"}
              </td>
              <td className="py-2 px-3 hidden sm:table-cell">
                {a.source_url ? (
                  <a
                    href={a.source_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-primary hover:underline text-xs"
                  >
                    SEC
                  </a>
                ) : (
                  <span className="text-muted text-xs">—</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
