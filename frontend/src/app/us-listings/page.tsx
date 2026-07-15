import {
  fetchListings,
  fetchListingSummary,
  fetchCryptoProducts,
  fetchCryptoStats,
  fetchListingDates,
  fetchUpcomingListings,
} from "@/lib/api";
import DateNav from "@/components/DateNav";
import ThemeToggle from "@/components/ThemeToggle";
import NavBar from "@/components/NavBar";
import ListingsContent from "./ListingsContent";

async function getData(date: string) {
  const startOfMonth = date.slice(0, 7) + "-01";
  const endOfMonth = date.slice(0, 7) + "-31";

  const [listings, summary, cryptoProducts, cryptoStats, dates, upcoming] =
    await Promise.all([
      fetchListings({ start: startOfMonth, end: endOfMonth, limit: 200 }),
      fetchListingSummary(startOfMonth, endOfMonth),
      fetchCryptoProducts(),
      fetchCryptoStats(),
      fetchListingDates(),
      fetchUpcomingListings(),
    ]);

  return { listings, summary, cryptoProducts, cryptoStats, dates, upcoming };
}

export default async function UsListingsPage({
  searchParams,
}: {
  searchParams: Promise<{ date?: string }>;
}) {
  const { date } = await searchParams;
  const today = new Date().toISOString().slice(0, 10);
  const targetDate = date || today;

  const { listings, summary, cryptoProducts, cryptoStats, dates, upcoming } =
    await getData(targetDate);

  return (
    <div className="flex flex-col flex-1 max-w-5xl mx-auto w-full px-4 py-6 sm:px-6 sm:py-8 gap-6">
      {/* Header */}
      <header className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <DateNav date={targetDate} availableDates={dates} />
        <div className="flex items-center gap-3">
          <div className="text-xs text-muted">
            数据来源 NASDAQ · SEC EDGAR · 每日自动更新
          </div>
          <ThemeToggle />
        </div>
      </header>

      <NavBar />

      <ListingsContent
        listings={listings}
        summary={summary}
        cryptoProducts={cryptoProducts}
        cryptoStats={cryptoStats}
        upcoming={upcoming}
      />

      {/* Footer */}
      <footer className="text-center text-xs text-muted py-4 border-t border-border space-y-1">
        <p>美股新上市追踪 · 数据来源 NASDAQ IPO Calendar + SEC EDGAR</p>
        <p>仅供内部研究用途，不构成投资建议。</p>
      </footer>
    </div>
  );
}
