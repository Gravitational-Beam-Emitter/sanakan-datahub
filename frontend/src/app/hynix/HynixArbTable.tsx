"use client";

import type { HynixArbitrageInstrument } from "@/lib/api";

function fmtKRW(n: number): string {
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e4) return `${(n / 1e4).toFixed(1)}万`;
  return n.toLocaleString();
}

function typeBadge(type: string) {
  const map: Record<string, string> = {
    stock: "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400",
    adr: "bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-400",
    etp: "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400",
    etf: "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400",
  };
  return map[type] || map.stock;
}

export default function HynixArbTable({
  instruments,
}: {
  instruments: HynixArbitrageInstrument[];
}) {
  if (!instruments.length) {
    return (
      <div className="glass rounded-xl p-6 text-center text-xs text-muted">
        暂无折溢价数据
      </div>
    );
  }

  return (
    <div className="glass rounded-xl overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-border">
            <th className="text-left py-3 px-3 text-muted font-medium">标的</th>
            <th className="text-left py-3 px-3 text-muted font-medium">市场</th>
            <th className="text-left py-3 px-3 text-muted font-medium">类型</th>
            <th className="text-right py-3 px-3 text-muted font-medium">杠杆</th>
            <th className="text-right py-3 px-3 text-muted font-medium">本地价格</th>
            <th className="text-right py-3 px-3 text-muted font-medium">KRW价格</th>
            <th className="text-right py-3 px-3 text-muted font-medium">
              等效1股KRW
            </th>
            <th className="text-right py-3 px-3 text-muted font-medium">
              相对正股溢价
            </th>
            <th className="text-right py-3 px-3 text-muted font-medium">
              NAV溢价
            </th>
          </tr>
        </thead>
        <tbody>
          {instruments.map((inst) => {
            const isBase = inst.instrument_type === "stock";
            return (
              <tr
                key={inst.ticker}
                className={`border-b border-border/50 hover:bg-surface-hover ${
                  isBase ? "bg-primary-a05" : ""
                }`}
              >
                <td className="py-2.5 px-3">
                  <div className="flex flex-col">
                    <span className="font-mono font-medium text-ink">
                      {inst.ticker}
                    </span>
                    <span className="text-muted text-[10px]">{inst.name}</span>
                  </div>
                </td>
                <td className="py-2.5 px-3 text-muted">{inst.market}</td>
                <td className="py-2.5 px-3">
                  <span
                    className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${typeBadge(inst.instrument_type)}`}
                  >
                    {inst.instrument_type.toUpperCase()}
                  </span>
                </td>
                <td className="py-2.5 px-3 text-right font-mono">
                  {inst.leverage > 0 ? `${inst.leverage}x` : `${inst.leverage}x`}
                </td>
                <td className="py-2.5 px-3 text-right font-mono tabular-nums">
                  <span className="text-ink">
                    {inst.price_local.toLocaleString(undefined, {
                      minimumFractionDigits: 0,
                      maximumFractionDigits: 2,
                    })}
                  </span>
                  <span className="text-muted ml-0.5">{inst.currency}</span>
                </td>
                <td className="py-2.5 px-3 text-right font-mono tabular-nums text-muted">
                  {fmtKRW(inst.price_krw)} KRW
                </td>
                <td className="py-2.5 px-3 text-right font-mono tabular-nums">
                  {isBase ? (
                    <span className="text-muted">— (基准)</span>
                  ) : (
                    <span className="text-ink">
                      {fmtKRW(inst.equivalent_krw_per_share)} KRW
                    </span>
                  )}
                </td>
                <td className="py-2.5 px-3 text-right font-mono tabular-nums">
                  {isBase ? (
                    <span className="text-muted">—</span>
                  ) : (
                    <span
                      className={`font-medium ${
                        inst.premium_pct_vs_base >= 0
                          ? "text-up"
                          : "text-down"
                      }`}
                    >
                      {inst.premium_pct_vs_base >= 0 ? "+" : ""}
                      {inst.premium_pct_vs_base.toFixed(2)}%
                    </span>
                  )}
                </td>
                <td className="py-2.5 px-3 text-right font-mono tabular-nums">
                  {inst.nav_premium_pct != null ? (
                    <span
                      className={
                        inst.nav_premium_pct >= 0 ? "text-up" : "text-down"
                      }
                    >
                      {inst.nav_premium_pct >= 0 ? "+" : ""}
                      {inst.nav_premium_pct.toFixed(2)}%
                    </span>
                  ) : (
                    <span className="text-muted">—</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
