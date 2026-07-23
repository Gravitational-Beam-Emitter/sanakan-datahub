import { fetchAuctionStocks, fetchFundFlowStocks, fetchFundFlowSectors, fetchMoneyFlowStats } from "@/lib/api";
import NavBar from "@/components/NavBar";
import MoneyFlowContent from "./Content";

export default async function MoneyFlowPage() {
  const [auctionData, inflowStocks, outflowStocks, inflowSectors, stats] = await Promise.all([
    fetchAuctionStocks({ limit: 50 }).catch(() => ({ count: 0, stocks: [] })),
    fetchFundFlowStocks({ direction: "inflow", limit: 30 }).catch(() => ({ count: 0, stocks: [] })),
    fetchFundFlowStocks({ direction: "outflow", limit: 30 }).catch(() => ({ count: 0, stocks: [] })),
    fetchFundFlowSectors({ sector_type: "行业资金流", direction: "inflow", limit: 20 }).catch(() => ({ count: 0, sectors: [] })),
    fetchMoneyFlowStats().catch(() => null),
  ]);

  return (
    <div className="flex flex-col flex-1 max-w-6xl mx-auto w-full px-4 py-6 sm:px-6 sm:py-8 gap-6">
      <header className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-xl font-semibold">资金流向 & 竞价抢筹</h1>
          <p className="text-xs text-muted mt-1">
            盘前竞价抢筹排行 + 主力资金流入流出板块/个股 · 数据源: 东方财富 via AKShare
          </p>
        </div>
        {stats && (
          <div className="flex gap-4 text-xs text-muted">
            <span>竞价 {stats.auction_days} 天</span>
            <span>资金流 {stats.fund_flow_days} 天</span>
          </div>
        )}
      </header>

      <NavBar />

      <MoneyFlowContent
        initialAuction={auctionData.stocks}
        initialInflowStocks={inflowStocks.stocks}
        initialOutflowStocks={outflowStocks.stocks}
        initialSectors={inflowSectors.sectors}
      />
    </div>
  );
}
