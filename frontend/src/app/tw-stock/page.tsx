import {
  fetchTwDailyReview,
  fetchTwListings,
  fetchTwIndices,
  fetchTwAvailableDates,
} from "@/lib/api";
import DateNav from "@/components/DateNav";
import ThemeToggle from "@/components/ThemeToggle";
import NavBar from "@/components/NavBar";

async function getData(date: string) {
  const [review, twseStocks, tpexStocks, indices, dates] =
    await Promise.all([
      fetchTwDailyReview(date).catch(() => null),
      fetchTwListings({ market: "TWSE", limit: 500 }),
      fetchTwListings({ market: "TPEx", limit: 500 }),
      fetchTwIndices({ limit: 60 }),
      fetchTwAvailableDates(),
    ]);

  return { review, twseStocks, tpexStocks, indices, dates };
}

function MoverRow({ m }: { m: Record<string, unknown> }) {
  const change = Number(m.change_pct ?? 0);
  const up = change > 0;
  const neutral = change === 0;

  return (
    <div
      className={`flex items-center gap-3 px-4 py-2.5 rounded-lg border transition-colors ${
        up
          ? "border-red-a10 bg-red-a5"
          : neutral
          ? "border-border bg-surface"
          : "border-green-a10 bg-green-a5"
      }`}
    >
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-sm font-mono font-medium text-ink">
            {String(m.code ?? "")}
          </span>
          <span className="text-sm text-ink truncate">{String(m.name ?? "")}</span>
          <span className="text-xs text-muted">{String(m.market ?? "")}</span>
        </div>
        {m.reasons ? (
          <div className="text-xs text-muted mt-0.5 truncate">
            {String(m.reasons)}
          </div>
        ) : null}
      </div>
      <div className="text-right">
        <div
          className={`text-sm font-semibold tabular-nums ${
            up ? "text-red" : neutral ? "text-muted" : "text-green"
          }`}
        >
          {up ? "+" : ""}
          {change.toFixed(1)}%
        </div>
        {m.volume ? (
          <div className="text-xs text-muted tabular-nums">
            {Number(m.volume).toLocaleString()}
          </div>
        ) : null}
      </div>
    </div>
  );
}

export default async function TwStockPage({
  searchParams,
}: {
  searchParams: Promise<{ date?: string }>;
}) {
  const { date } = await searchParams;
  const today = new Date().toISOString().slice(0, 10);
  const targetDate = date || today;

  const { review, twseStocks, tpexStocks, indices, dates } =
    await getData(targetDate);

  return (
    <div className="flex flex-col flex-1 max-w-5xl mx-auto w-full px-4 py-6 sm:px-6 sm:py-8 gap-6">
      {/* Header */}
      <header className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <DateNav date={targetDate} availableDates={dates} />
        <div className="flex items-center gap-3">
          <div className="text-xs text-muted">
            数据来源 TWSE · TPEx · 每日更新
          </div>
          <ThemeToggle />
        </div>
      </header>

      <NavBar />

      {/* Indices */}
      {indices.length > 0 && (
        <section className="glass rounded-xl p-4">
          <h2 className="text-sm font-medium text-muted mb-3">大盘指数</h2>
          <div className="flex gap-4 flex-wrap">
            {indices.slice(0, 2).map((idx) => {
              const change = Number(idx.change_pct ?? 0);
              return (
                <div key={idx.index_code} className="flex items-center gap-3">
                  <span className="text-sm font-medium text-ink">
                    {idx.index_name}
                  </span>
                  <span className="text-lg font-semibold tabular-nums text-ink">
                    {Number(idx.close).toLocaleString()}
                  </span>
                  <span
                    className={`text-sm font-medium tabular-nums ${
                      change > 0 ? "text-red" : change < 0 ? "text-green" : "text-muted"
                    }`}
                  >
                    {change > 0 ? "+" : ""}
                    {change.toFixed(2)}%
                  </span>
                </div>
              );
            })}
          </div>
        </section>
      )}

      {/* Market overview */}
      <section className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div className="glass rounded-xl p-3 text-center">
          <div className="text-xs text-muted">上市公司</div>
          <div className="text-xl font-bold text-ink">{twseStocks.length}</div>
        </div>
        <div className="glass rounded-xl p-3 text-center">
          <div className="text-xs text-muted">上柜公司</div>
          <div className="text-xl font-bold text-ink">{tpexStocks.length}</div>
        </div>
        <div className="glass rounded-xl p-3 text-center">
          <div className="text-xs text-muted">今日波动股</div>
          <div className="text-xl font-bold text-ink">
            {review?.summary?.mover_count ?? 0}
          </div>
        </div>
        <div className="glass rounded-xl p-3 text-center">
          <div className="text-xs text-muted">活跃题材</div>
          <div className="text-xl font-bold text-ink">
            {review?.summary?.sector_count ?? 0}
          </div>
        </div>
      </section>

      {/* Summary */}
      {review?.summary && review.summary.mover_count > 0 && (
        <section className="glass rounded-xl p-4">
          <h2 className="text-sm font-medium text-muted mb-3">
            {targetDate} 市场概览
          </h2>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-sm">
            <div>
              <span className="text-muted">上涨</span>{" "}
              <span className="text-red font-semibold">{review.summary.up_count}</span>
            </div>
            <div>
              <span className="text-muted">下跌</span>{" "}
              <span className="text-green font-semibold">{review.summary.down_count}</span>
            </div>
            <div>
              <span className="text-muted">平均涨跌</span>{" "}
              <span className="font-semibold">
                {review.summary.avg_change > 0 ? "+" : ""}
                {review.summary.avg_change}%
              </span>
            </div>
            <div>
              <span className="text-muted">最大波动</span>{" "}
              <span className="font-semibold">{review.summary.max_change}%</span>
            </div>
          </div>
        </section>
      )}

      {/* Movers */}
      {review && review.movers.length > 0 && (
        <section>
          <h2 className="text-sm font-medium text-muted mb-3">
            显著波动股 ({review.movers.length})
          </h2>
          <div className="flex flex-col gap-2">
            {review.movers.map((m, i) => (
              <MoverRow key={`${m.code}-${i}`} m={m} />
            ))}
          </div>
        </section>
      )}

      {/* Narratives */}
      {review && review.narratives.length > 0 && (
        <section>
          <h2 className="text-sm font-medium text-muted mb-3">市场主题</h2>
          <div className="grid sm:grid-cols-2 gap-3">
            {review.narratives.map((n, i) => (
              <div key={i} className="glass rounded-xl p-4">
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-xs bg-primary-a15 text-primary px-2 py-0.5 rounded-full">
                    {n.tag}
                  </span>
                  <span className="text-sm font-medium text-ink">{n.name}</span>
                </div>
                <p className="text-xs text-muted">{n.description}</p>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Industries */}
      {review && review.industries.length > 0 && (
        <section>
          <h2 className="text-sm font-medium text-muted mb-3">题材分布</h2>
          <div className="flex flex-wrap gap-2">
            {review.industries.map((ind, i) => (
              <div
                key={i}
                className="glass rounded-lg px-3 py-1.5 flex items-center gap-2"
              >
                <span className="text-sm text-ink">{ind.industry}</span>
                <span className="text-xs text-muted">{ind.count}</span>
                <span
                  className={`text-xs font-medium tabular-nums ${
                    Number(ind.avg_change) > 0 ? "text-red" : "text-green"
                  }`}
                >
                  {Number(ind.avg_change) > 0 ? "+" : ""}
                  {Number(ind.avg_change).toFixed(1)}%
                </span>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Empty state */}
      {(!review || review.movers.length === 0) && (
        <div className="flex flex-col items-center gap-3 py-12 text-muted">
          <div className="text-lg">暂无{targetDate}的台湾股市数据</div>
          <div className="text-sm">
            请先执行 <code className="bg-surface px-2 py-0.5 rounded">python -m tw_stock.pipeline --init</code> 初始化数据
          </div>
        </div>
      )}

      {/* Footer */}
      <footer className="text-center text-xs text-muted py-4 border-t border-border space-y-1">
        <p>台湾股市追踪 · 数据来源 TWSE 台湾证券交易所 + TPEx 柜买中心</p>
        <p>仅供内部研究用途，不构成投资建议。</p>
      </footer>
    </div>
  );
}
