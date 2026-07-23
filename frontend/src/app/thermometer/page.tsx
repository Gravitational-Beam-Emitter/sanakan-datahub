import { fetchThermometer, fetchKols, fetchKolStats } from "@/lib/api";
import NavBar from "@/components/NavBar";
import ThermometerContent from "./Content";

export default async function ThermometerPage() {
  const [thermoData, kolsData, stats] = await Promise.all([
    fetchThermometer({ limit: 50 }).catch(() => ({ count: 0, stocks: [] })),
    fetchKols({ limit: 50 }).catch(() => ({ count: 0, kols: [] })),
    fetchKolStats().catch(() => null),
  ]);

  return (
    <div className="flex flex-col flex-1 max-w-6xl mx-auto w-full px-4 py-6 sm:px-6 sm:py-8 gap-6">
      <header className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-xl font-semibold">市场温度计</h1>
          <p className="text-xs text-muted mt-1">
            追踪 Reddit / YouTube 大V 吹票热度 · 自动发现 · 自动评级
          </p>
        </div>
        {stats && (
          <div className="flex gap-4 text-xs text-muted">
            <span>{stats.active_kols} 大V</span>
            <span>{stats.total_posts} 帖</span>
            <span>{stats.total_mentions} 提及</span>
          </div>
        )}
      </header>

      <NavBar />

      <ThermometerContent
        initialStocks={thermoData.stocks}
        initialKols={kolsData.kols}
        stats={stats}
      />
    </div>
  );
}
