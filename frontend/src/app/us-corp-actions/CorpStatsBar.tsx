"use client";

import { CorpActionSummary } from "@/lib/api";

export default function CorpStatsBar({ summary }: { summary: CorpActionSummary }) {
  const stats = [
    { label: "公司行动", value: summary.total, unit: "条" },
    { label: "涉及公司", value: summary.companies, unit: "家" },
    { label: "行动分类", value: summary.type_count, unit: "类" },
  ];

  return (
    <div className="flex flex-wrap gap-3 w-full">
      {stats.map((s) => (
        <div
          key={s.label}
          className="flex items-baseline gap-1.5 glass rounded-xl px-4 py-3 min-w-0"
        >
          <span className="text-muted text-sm shrink-0">{s.label}</span>
          <span className="text-ink font-semibold text-2xl tabular-nums leading-none">
            {s.value}
          </span>
          <span className="text-muted text-sm">{s.unit}</span>
        </div>
      ))}
    </div>
  );
}
