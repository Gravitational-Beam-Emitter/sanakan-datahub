import { fetchHynixLatestArbitrage, fetchHynixAvailableDates } from "@/lib/api";
import DateNav from "@/components/DateNav";
import ThemeToggle from "@/components/ThemeToggle";
import NavBar from "@/components/NavBar";
import HynixContent from "./HynixContent";

export default async function HynixPage({
  searchParams,
}: {
  searchParams: Promise<{ date?: string }>;
}) {
  const { date } = await searchParams;
  const targetDate = date || "";

  const [snapshot, dates] = await Promise.all([
    date
      ? (await import("@/lib/api")).fetchHynixArbitrageByDate(date).catch(() => null)
      : fetchHynixLatestArbitrage().catch(() => null),
    fetchHynixAvailableDates().catch(() => []),
  ]);

  return (
    <div className="flex flex-col flex-1 max-w-6xl mx-auto w-full px-4 py-6 sm:px-6 sm:py-8 gap-6">
      <header className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-4">
          <DateNav
            date={snapshot?.date || targetDate || new Date().toISOString().slice(0, 10)}
            availableDates={dates}
            basePath="/hynix"
          />
          <span className="text-xs text-muted">SK Hynix 跨市场套利追踪</span>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs text-muted">数据来源: yfinance / KRX / HKEX / Nasdaq</span>
          <ThemeToggle />
        </div>
      </header>

      <NavBar />

      {!snapshot ? (
        <div className="flex flex-col items-center justify-center py-20 gap-3">
          <p className="text-muted text-sm">暂无数据</p>
          <p className="text-muted text-xs">请先运行 python -m hynix.pipeline --init 初始化数据</p>
        </div>
      ) : (
        <HynixContent
          snapshot={snapshot}
          availableDates={dates}
          targetDate={snapshot.date}
        />
      )}

      <footer className="text-center text-xs text-muted py-4 border-t border-border space-y-1">
        <p>
          SK Hynix 跨市场套利追踪 — 实时比较 KR 股票、US ADR、HK ETP、KR ETF
          的折溢价
        </p>
        <p>
          数据仅供参考，不构成投资建议。不同市场价格存在时区差异，溢价可能反映异步定价。
        </p>
      </footer>
    </div>
  );
}
