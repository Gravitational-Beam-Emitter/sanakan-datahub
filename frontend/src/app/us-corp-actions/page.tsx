import {
  fetchCorpActionsByDate,
  fetchCorpActionDates,
} from "@/lib/api";
import DateNav from "@/components/DateNav";
import ThemeToggle from "@/components/ThemeToggle";
import NavBar from "@/components/NavBar";
import CorpActionsTable from "@/components/CorpActionsTable";
import CorpStatsBar from "./CorpStatsBar";
import CorpBreakdown from "./CorpBreakdown";

async function getReview(date: string) {
  try {
    return await fetchCorpActionsByDate(date);
  } catch {
    return null;
  }
}

export default async function UsCorpActionsPage({
  searchParams,
}: {
  searchParams: Promise<{ date?: string }>;
}) {
  const { date } = await searchParams;
  const today = new Date().toISOString().slice(0, 10);
  const targetDate = date || today;

  const [review, dates] = await Promise.all([
    getReview(targetDate),
    fetchCorpActionDates(),
  ]);

  return (
    <div className="flex flex-col flex-1 max-w-5xl mx-auto w-full px-4 py-6 sm:px-6 sm:py-8 gap-6">
      {/* Header */}
      <header className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <DateNav date={targetDate} availableDates={dates} />
        <div className="flex items-center gap-3">
          <div className="text-xs text-muted">
            数据来源 SEC EDGAR · 每日自动抓取
          </div>
          <ThemeToggle />
        </div>
      </header>

      <NavBar />

      {!review ? (
        <div className="flex flex-col items-center justify-center py-20 gap-3">
          <p className="text-muted text-sm">
            {targetDate} 暂无美国公司行动数据
          </p>
          <p className="text-muted text-xs">
            可能是非交易日，或数据尚未拉取
          </p>
        </div>
      ) : (
        <>
          {/* Stats */}
          <CorpStatsBar summary={review.summary} />

          {/* Type breakdown */}
          <CorpBreakdown breakdown={review.breakdown} />

          {/* Actions table */}
          <section>
            <h2 className="text-sm font-medium text-muted mb-3">
              公司行动明细 ({review.actions.length})
            </h2>
            <div className="glass rounded-xl overflow-hidden">
              <CorpActionsTable actions={review.actions} />
            </div>
          </section>
        </>
      )}

      {/* Footer */}
      <footer className="text-center text-xs text-muted py-4 border-t border-border space-y-1">
        <p>美国上市公司公司行动 · 数据来源 SEC EDGAR 8-K filings</p>
        <p>
          仅供内部研究用途，不构成投资建议。覆盖 NYSE + NASDAQ 全量上市公司。
        </p>
      </footer>
    </div>
  );
}
