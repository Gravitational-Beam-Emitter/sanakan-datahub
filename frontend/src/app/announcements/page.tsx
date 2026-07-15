import {
  fetchAnnouncements,
  fetchTrackedCompanies,
} from "@/lib/api";
import NavBar from "@/components/NavBar";
import ThemeToggle from "@/components/ThemeToggle";
import AnnouncementsContent from "./AnnouncementsContent";

export default async function AnnouncementsPage({
  searchParams,
}: {
  searchParams: Promise<{ market?: string; ticker?: string }>;
}) {
  const { market, ticker } = await searchParams;

  const [data, companies] = await Promise.all([
    fetchAnnouncements({ market, ticker, limit: 100 }),
    fetchTrackedCompanies(),
  ]);

  return (
    <div className="flex flex-col flex-1 max-w-5xl mx-auto w-full px-4 py-6 sm:px-6 sm:py-8 gap-6">
      {/* Header */}
      <header className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h1 className="text-lg font-semibold text-ink">公司公告</h1>
          <span className="text-xs text-muted">
            SEC · HKEX · CNINFO
          </span>
        </div>
        <div className="flex items-center gap-3">
          <div className="text-xs text-muted">
            {data.count} 条公告
          </div>
          <ThemeToggle />
        </div>
      </header>

      <NavBar />

      <AnnouncementsContent
        announcements={data.announcements}
        companies={companies}
        activeMarket={market}
        activeTicker={ticker}
      />

      {/* Footer */}
      <footer className="text-center text-xs text-muted py-4 border-t border-border space-y-1">
        <p>
          美股 (SEC EDGAR) · 港股 (HKEXnews) · A股 (巨潮资讯网) · 每日自动抓取
        </p>
      </footer>
    </div>
  );
}
