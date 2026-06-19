"use client";

import { CorpActionBreakdown } from "@/lib/api";

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

const TYPE_COLORS_BAR: Record<string, string> = {
  merger_acquisition: "bg-amber-500",
  equity_change: "bg-blue-500",
  securities_issuance: "bg-purple-500",
  delisting: "bg-red-500",
  bankruptcy: "bg-red-700",
  dividend: "bg-green-500",
  stock_split: "bg-cyan-500",
  buyback: "bg-emerald-500",
  earnings: "bg-yellow-500",
  other: "bg-gray-500",
};

export default function CorpBreakdown({
  breakdown,
}: {
  breakdown: CorpActionBreakdown[];
}) {
  if (!breakdown || breakdown.length === 0) return null;

  const maxCount = Math.max(...breakdown.map((b) => b.cnt));

  return (
    <section>
      <h2 className="text-sm font-medium text-muted mb-3">分类分布</h2>
      <div className="glass rounded-xl px-4 py-4 space-y-2.5">
        {breakdown.map((b) => (
          <div key={b.action_type} className="flex items-center gap-3">
            <span className="text-xs text-muted w-20 shrink-0 text-right">
              {TYPE_LABELS[b.action_type] || b.action_type}
            </span>
            <div className="flex-1 h-5 bg-surface rounded-full overflow-hidden">
              <div
                className={`h-full rounded-full transition-all ${
                  TYPE_COLORS_BAR[b.action_type] || TYPE_COLORS_BAR.other
                }`}
                style={{ width: `${(b.cnt / maxCount) * 100}%` }}
              />
            </div>
            <span className="text-xs text-muted w-12 shrink-0 tabular-nums">
              {b.cnt}
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}
