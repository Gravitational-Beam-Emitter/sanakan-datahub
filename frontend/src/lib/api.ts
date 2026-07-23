const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8001";
const ECO_API = process.env.NEXT_PUBLIC_ECO_API_URL || "http://127.0.0.1:8000";
const US_CORP_API = process.env.NEXT_PUBLIC_US_CORP_API_URL || "http://127.0.0.1:8002";
const US_LISTINGS_API = process.env.NEXT_PUBLIC_US_LISTINGS_API_URL || "http://127.0.0.1:8003";

/* ── Multi-date aggregation types ── */

export interface TrendPoint {
  date: string;
  zt_count: number;
  avg_pct: number;
  max_lbc: number;
  sector_count: number;
}

export interface SectorRotation {
  start: string;
  end: string;
  days: string[];
  sectors: string[];
  matrix: number[][];
}

export interface MacroObservation {
  date: string;
  value: number;
}

export interface MacroSeries {
  id: number;
  name: string;
  data: MacroObservation[];
}


export interface LimitUpStock {
  date: string;
  code: string;
  name: string;
  price: number;
  pct: number;
  amount: number;
  ltsz: number;
  zsz: number;
  hs: number;
  fund: number;
  fbt: string;
  lbt: string;
  zbc: number;
  zttj: string;
  lbc: number;
  hybk: string;
  reasons: string | null;
}

export interface DailySummary {
  date: string;
  zt_count: number;
  avg_pct: number;
  max_lbc: number;
  zb_count: number;
  sector_count: number;
}

export interface Narrative {
  date: string;
  tag: string;
  name: string;
  description: string;
  stocks: { code: string; name: string; lbc: number }[];
}

export interface IndustryStat {
  industry: string;
  count: number;
  avg_pct: number;
  max_lbc: number;
}

export interface DailyReview {
  date: string;
  summary: DailySummary;
  stocks: LimitUpStock[];
  narratives: Narrative[];
  industries: IndustryStat[];
}

export interface StockHistory {
  code: string;
  count: number;
  history: LimitUpStock[];
}

export async function fetchDailyReview(date: string): Promise<DailyReview> {
  const res = await fetch(`${API_BASE}/api/v1/daily/${date}`, {
    next: { revalidate: 300 },
  });
  if (!res.ok) throw new Error(`Failed to fetch daily review: ${res.status}`);
  return res.json();
}

export async function fetchStockHistory(code: string): Promise<StockHistory> {
  const res = await fetch(`${API_BASE}/api/v1/stock/${code}`, {
    next: { revalidate: 300 },
  });
  if (!res.ok) throw new Error(`Failed to fetch stock history: ${res.status}`);
  return res.json();
}

export async function fetchAvailableDates(): Promise<string[]> {
  const res = await fetch(`${API_BASE}/api/v1/dates`, {
    next: { revalidate: 60 },
  });
  if (!res.ok) return [];
  const data = await res.json();
  return data.dates || [];
}

export async function triggerFetch(date?: string): Promise<void> {
  const params = date ? `?date=${date}` : "";
  await fetch(`${API_BASE}/api/v1/fetch${params}`, { method: "POST" });
}

/* ── Macro indicators (eco data API :8000) ── */

export interface MacroIndicator {
  id: number;
  name: string;
  frequency: string;
  value: number | null;
  date: string | null;
  history: { date: string; value: number }[];
}

const MACRO_IDS = [
  5,   // Manufacturing PMI (CN)
  7,   // M2 Money Supply (CN)
  8,   // LPR (CN)
  14,  // New House Price (CN)
  53,  // WTI Crude Oil (Global)
  32,  // Fed Funds Rate (US)
  55,  // North Bound Flow (CN)
  56,  // Margin Balance (CN)
  59,  // 10Y Bond Yield (CN)
  60,  // CNY/USD (CN)
  61,  // Caixin PMI (CN)
  63,  // Shibor 3M (CN)
  64,  // Reserve Ratio (CN)
] as const;

/* ── Multi-date fetch functions ── */

export async function fetchTrendData(start: string, end: string): Promise<TrendPoint[]> {
  const res = await fetch(`${API_BASE}/api/v1/trend?start=${start}&end=${end}`, {
    next: { revalidate: 300 },
  });
  if (!res.ok) return [];
  const data = await res.json();
  return (data.data || []).map((d: Record<string, unknown>) => ({
    date: String(d.date || "").slice(0, 10),
    zt_count: Number(d.zt_count || 0),
    avg_pct: Number(d.avg_pct || 0),
    max_lbc: Number(d.max_lbc || 0),
    sector_count: Number(d.sector_count || 0),
  }));
}

export async function fetchSectorRotation(start: string, end: string, topN = 15): Promise<SectorRotation> {
  const res = await fetch(`${API_BASE}/api/v1/sectors?start=${start}&end=${end}&top_n=${topN}`, {
    next: { revalidate: 300 },
  });
  if (!res.ok) return { start, end, days: [], sectors: [], matrix: [] };
  const data = await res.json();
  return {
    start: data.start,
    end: data.end,
    days: (data.days || []).map((d: string) => String(d).slice(0, 10)),
    sectors: data.sectors || [],
    matrix: data.matrix || [],
  };
}

export async function fetchNarrativesRange(start: string, end: string): Promise<Narrative[]> {
  const res = await fetch(`${API_BASE}/api/v1/narratives/range?start=${start}&end=${end}`, {
    next: { revalidate: 300 },
  });
  if (!res.ok) return [];
  const data = await res.json();
  return (data.narratives || []).map((n: Record<string, unknown>) => ({
    date: String(n.date || "").slice(0, 10),
    tag: String(n.tag || ""),
    name: String(n.name || ""),
    description: String(n.description || ""),
    stocks: (n.stocks as Array<{ code: string; name: string; lbc: number }>) || [],
  }));
}

export async function fetchMacroDataRange(macroIds: number[], start: string, end: string): Promise<MacroSeries[]> {
  const results = await Promise.all(
    macroIds.map(async (id) => {
      try {
        const res = await fetch(
          `${ECO_API}/api/v1/data/${id}?start=${start}&end=${end}&limit=1000`,
          { next: { revalidate: 600 } }
        );
        if (!res.ok) return { id, name: "", data: [] };
        const json = await res.json();
        return {
          id,
          name: json.indicator?.name || "",
          data: (json.data || []).map((o: { date: string; value: number }) => ({
            date: o.date.slice(0, 10),
            value: o.value,
          })),
        };
      } catch {
        return { id, name: "", data: [] };
      }
    })
  );
  return results;
}

/* ── US Corporate Actions (:8002) ── */

export interface CorpAction {
  filing_date: string;
  ticker: string;
  company_name: string;
  action_type: string;
  action_subtype: string | null;
  item_numbers: string | null;
  description: string | null;
  source_url: string | null;
}

export interface CorpActionSummary {
  date: string;
  total: number;
  companies: number;
  type_count: number;
}

export interface CorpActionBreakdown {
  action_type: string;
  cnt: number;
  companies: number;
}

export interface CorpActionDailyReview {
  date: string;
  summary: CorpActionSummary;
  actions: CorpAction[];
  breakdown: CorpActionBreakdown[];
}

export interface CorpActionDateSummary {
  start: string;
  end: string;
  daily_totals: { date: string; total: number; companies: number }[];
  type_breakdown: { date: string; action_type: string; cnt: number }[];
}

export async function fetchCorpActionsByDate(date: string): Promise<CorpActionDailyReview> {
  const res = await fetch(`${US_CORP_API}/api/v1/actions/${date}`, {
    next: { revalidate: 300 },
  });
  if (!res.ok) throw new Error(`Failed to fetch corp actions: ${res.status}`);
  return res.json();
}

export async function fetchCorpActions(params?: {
  start?: string;
  end?: string;
  action_type?: string;
  ticker?: string;
  limit?: number;
}): Promise<CorpAction[]> {
  const sp = new URLSearchParams();
  if (params?.start) sp.set("start", params.start);
  if (params?.end) sp.set("end", params.end);
  if (params?.action_type) sp.set("action_type", params.action_type);
  if (params?.ticker) sp.set("ticker", params.ticker);
  if (params?.limit) sp.set("limit", String(params.limit));
  const qs = sp.toString();
  const res = await fetch(`${US_CORP_API}/api/v1/actions${qs ? `?${qs}` : ""}`, {
    next: { revalidate: 300 },
  });
  if (!res.ok) return [];
  const data = await res.json();
  return data.actions || [];
}

export async function fetchTickerActions(ticker: string): Promise<CorpAction[]> {
  const res = await fetch(`${US_CORP_API}/api/v1/actions/ticker/${ticker}`, {
    next: { revalidate: 300 },
  });
  if (!res.ok) return [];
  const data = await res.json();
  return data.actions || [];
}

export async function fetchCorpActionDates(): Promise<string[]> {
  const res = await fetch(`${US_CORP_API}/api/v1/dates`, {
    next: { revalidate: 60 },
  });
  if (!res.ok) return [];
  const data = await res.json();
  return data.dates || [];
}

export async function fetchCorpActionSummary(start: string, end: string): Promise<CorpActionDateSummary> {
  const res = await fetch(`${US_CORP_API}/api/v1/summary?start=${start}&end=${end}`, {
    next: { revalidate: 300 },
  });
  if (!res.ok) return { start, end, daily_totals: [], type_breakdown: [] };
  return res.json();
}

export async function triggerCorpFetch(date?: string): Promise<void> {
  const params = date ? `?date=${date}` : "";
  await fetch(`${US_CORP_API}/api/v1/fetch${params}`, { method: "POST" });
}

/* ── Macro indicators (eco data API :8000) ── */

export async function fetchMacroBackground(): Promise<MacroIndicator[]> {
  const results = await Promise.all(
    MACRO_IDS.map(async (id) => {
      try {
        const [latestRes, histRes] = await Promise.all([
          fetch(`${ECO_API}/api/v1/data/${id}/latest`, { next: { revalidate: 600 } }),
          fetch(`${ECO_API}/api/v1/data/${id}?limit=12`, { next: { revalidate: 600 } }),
        ]);
        if (!latestRes.ok) return { id, name: "", frequency: "", value: null, date: null, history: [] };
        const [latestData, histData] = await Promise.all([latestRes.json(), histRes.ok ? histRes.json() : null]);
        return {
          id,
          name: latestData.indicator?.name || "",
          frequency: latestData.indicator?.frequency || "",
          value: latestData.latest?.value ?? null,
          date: latestData.latest?.date ?? null,
          history: histData?.data?.reverse().map((o: { date: string; value: number }) => ({
            date: o.date.slice(0, 10), value: o.value,
          })) || [],
        };
      } catch {
        return { id, name: "", frequency: "", value: null, date: null, history: [] };
      }
    })
  );
  return results;
}

/* ── US Listings + Crypto Products (:8003) ── */

export interface NewListing {
  id: number;
  ticker: string;
  company_name: string;
  listing_date: string;
  listing_type: string;
  exchange: string | null;
  offer_price: number | null;
  shares_offered: number | null;
  description: string | null;
  source: string;
  source_url: string | null;
  is_crypto: boolean;
}

export interface CryptoProduct {
  id: number;
  ticker: string;
  company_name: string;
  product_type: string;
  underlying_asset: string | null;
  listing_date: string | null;
  expense_ratio: number | null;
  aum: number | null;
  market_cap: number | null;
  description: string | null;
  issuer: string | null;
  is_active: boolean;
  data_source: string;
}

export interface ListingSummary {
  start: string;
  end: string;
  total: number;
  tickers: number;
  ipos: number;
  direct_listings: number;
  spacs: number;
  upcoming: number;
  crypto_count: number;
  monthly: { month: string; total: number; crypto_count: number }[];
}

export interface CryptoStats {
  total: number;
  by_type: { product_type: string; cnt: number }[];
  by_asset: { underlying_asset: string; cnt: number }[];
}

export async function fetchListings(params?: {
  start?: string; end?: string; listing_type?: string; exchange?: string;
  is_crypto?: boolean; limit?: number;
}): Promise<NewListing[]> {
  const sp = new URLSearchParams();
  if (params?.start) sp.set("start", params.start);
  if (params?.end) sp.set("end", params.end);
  if (params?.listing_type) sp.set("listing_type", params.listing_type);
  if (params?.exchange) sp.set("exchange", params.exchange);
  if (params?.is_crypto !== undefined) sp.set("is_crypto", String(params.is_crypto));
  if (params?.limit) sp.set("limit", String(params.limit));
  const qs = sp.toString();
  const res = await fetch(`${US_LISTINGS_API}/api/v1/listings${qs ? `?${qs}` : ""}`, {
    next: { revalidate: 300 },
  });
  if (!res.ok) return [];
  const data = await res.json();
  return data.listings || [];
}

export async function fetchUpcomingListings(): Promise<NewListing[]> {
  const res = await fetch(`${US_LISTINGS_API}/api/v1/listings/upcoming`, {
    next: { revalidate: 300 },
  });
  if (!res.ok) return [];
  const data = await res.json();
  return data.listings || [];
}

export async function fetchListingDates(): Promise<string[]> {
  const res = await fetch(`${US_LISTINGS_API}/api/v1/dates`, {
    next: { revalidate: 60 },
  });
  if (!res.ok) return [];
  const data = await res.json();
  return data.dates || [];
}

export async function fetchListingSummary(start: string, end: string): Promise<ListingSummary> {
  const res = await fetch(`${US_LISTINGS_API}/api/v1/summary?start=${start}&end=${end}`, {
    next: { revalidate: 300 },
  });
  if (!res.ok) return { start, end, total: 0, tickers: 0, ipos: 0, direct_listings: 0, spacs: 0, upcoming: 0, crypto_count: 0, monthly: [] };
  return res.json();
}

export async function fetchCryptoProducts(params?: {
  product_type?: string; underlying_asset?: string;
}): Promise<CryptoProduct[]> {
  const sp = new URLSearchParams();
  if (params?.product_type) sp.set("product_type", params.product_type);
  if (params?.underlying_asset) sp.set("underlying_asset", params.underlying_asset);
  const qs = sp.toString();
  const res = await fetch(`${US_LISTINGS_API}/api/v1/crypto${qs ? `?${qs}` : ""}`, {
    next: { revalidate: 300 },
  });
  if (!res.ok) return [];
  const data = await res.json();
  return data.products || [];
}

export async function fetchCryptoStats(): Promise<CryptoStats> {
  const res = await fetch(`${US_LISTINGS_API}/api/v1/crypto/stats`, {
    next: { revalidate: 300 },
  });
  if (!res.ok) return { total: 0, by_type: [], by_asset: [] };
  return res.json();
}

export async function triggerListingsFetch(month?: string): Promise<void> {
  const params = month ? `?month=${month}` : "";
  await fetch(`${US_LISTINGS_API}/api/v1/fetch${params}`, { method: "POST" });
}

export async function triggerCryptoRefresh(action?: string): Promise<void> {
  const params = action ? `?action=${action}` : "";
  await fetch(`${US_LISTINGS_API}/api/v1/fetch-crypto${params}`, { method: "POST" });
}

/* ── Insider Trades (:8003) ── */

export interface InsiderTrade {
  id: number;
  ticker: string;
  company_name: string;
  insider_name: string | null;
  insider_title: string | null;
  transaction_type: string | null;
  shares: number | null;
  price_per_share: number | null;
  total_value: number | null;
  shares_owned_after: number | null;
  filing_date: string;
  transaction_date: string | null;
  is_10b5_1: boolean;
  source_url: string | null;
}

export async function fetchInsiderTrades(params?: {
  ticker?: string; start?: string; end?: string; limit?: number;
}): Promise<InsiderTrade[]> {
  const sp = new URLSearchParams();
  if (params?.ticker) sp.set("ticker", params.ticker);
  if (params?.start) sp.set("start", params.start);
  if (params?.end) sp.set("end", params.end);
  if (params?.limit) sp.set("limit", String(params.limit));
  const qs = sp.toString();
  const res = await fetch(`${US_LISTINGS_API}/api/v1/insider${qs ? `?${qs}` : ""}`, {
    next: { revalidate: 300 },
  });
  if (!res.ok) return [];
  const data = await res.json();
  return data.trades || [];
}

export async function fetchInsiderByTicker(ticker: string): Promise<InsiderTrade[]> {
  const res = await fetch(`${US_LISTINGS_API}/api/v1/insider/${ticker}`, {
    next: { revalidate: 300 },
  });
  if (!res.ok) return [];
  const data = await res.json();
  return data.trades || [];
}

/* ── Earnings Calendar (:8003) ── */

export interface EarningsEntry {
  id: number;
  ticker: string;
  company_name: string;
  report_type: string;
  fiscal_period_end: string | null;
  filing_date: string;
  source_url: string | null;
}

export async function fetchEarnings(params?: {
  ticker?: string; start?: string; end?: string; report_type?: string; limit?: number;
}): Promise<EarningsEntry[]> {
  const sp = new URLSearchParams();
  if (params?.ticker) sp.set("ticker", params.ticker);
  if (params?.start) sp.set("start", params.start);
  if (params?.end) sp.set("end", params.end);
  if (params?.report_type) sp.set("report_type", params.report_type);
  if (params?.limit) sp.set("limit", String(params.limit));
  const qs = sp.toString();
  const res = await fetch(`${US_LISTINGS_API}/api/v1/earnings${qs ? `?${qs}` : ""}`, {
    next: { revalidate: 300 },
  });
  if (!res.ok) return [];
  const data = await res.json();
  return data.earnings || [];
}

export async function fetchUpcomingEarnings(limit?: number): Promise<EarningsEntry[]> {
  const sp = new URLSearchParams();
  if (limit) sp.set("limit", String(limit));
  const qs = sp.toString();
  const res = await fetch(`${US_LISTINGS_API}/api/v1/earnings/upcoming${qs ? `?${qs}` : ""}`, {
    next: { revalidate: 300 },
  });
  if (!res.ok) return [];
  const data = await res.json();
  return data.earnings || [];
}

/* ── Institutional Holdings (:8003) ── */

export interface InstitutionalHolding {
  id: number;
  filer_cik: string;
  filer_name: string;
  ticker: string;
  cusip: string | null;
  security_name: string | null;
  shares: number | null;
  market_value: number | null;
  quarter_end: string;
  filing_date: string;
  source_url: string | null;
}

export async function fetchHoldings(params?: {
  ticker?: string; filer_cik?: string; quarter_end?: string; limit?: number;
}): Promise<InstitutionalHolding[]> {
  const sp = new URLSearchParams();
  if (params?.ticker) sp.set("ticker", params.ticker);
  if (params?.filer_cik) sp.set("filer_cik", params.filer_cik);
  if (params?.quarter_end) sp.set("quarter_end", params.quarter_end);
  if (params?.limit) sp.set("limit", String(params.limit));
  const qs = sp.toString();
  const res = await fetch(`${US_LISTINGS_API}/api/v1/holdings${qs ? `?${qs}` : ""}`, {
    next: { revalidate: 300 },
  });
  if (!res.ok) return [];
  const data = await res.json();
  return data.holdings || [];
}

/* ── Short Interest & FTD (:8003) ── */

export interface ShortInterestEntry {
  id: number;
  ticker: string;
  settlement_date: string;
  short_interest: number | null;
  avg_daily_volume: number | null;
  days_to_cover: number | null;
  short_pct_float: number | null;
  source: string;
}

export interface FtdEntry {
  id: number;
  ticker: string;
  date: string;
  quantity: number | null;
  price: number | null;
  source: string;
}

export async function fetchShortInterest(ticker?: string, limit?: number): Promise<ShortInterestEntry[]> {
  const sp = new URLSearchParams();
  if (ticker) sp.set("ticker", ticker);
  if (limit) sp.set("limit", String(limit));
  const qs = sp.toString();
  const res = await fetch(`${US_LISTINGS_API}/api/v1/short-interest${qs ? `?${qs}` : ""}`, {
    next: { revalidate: 300 },
  });
  if (!res.ok) return [];
  const data = await res.json();
  return data.short_interest || [];
}

export async function fetchFtd(params?: {
  ticker?: string; start?: string; end?: string; limit?: number;
}): Promise<FtdEntry[]> {
  const sp = new URLSearchParams();
  if (params?.ticker) sp.set("ticker", params.ticker);
  if (params?.start) sp.set("start", params.start);
  if (params?.end) sp.set("end", params.end);
  if (params?.limit) sp.set("limit", String(params.limit));
  const qs = sp.toString();
  const res = await fetch(`${US_LISTINGS_API}/api/v1/ftd${qs ? `?${qs}` : ""}`, {
    next: { revalidate: 300 },
  });
  if (!res.ok) return [];
  const data = await res.json();
  return data.ftd || [];
}

/* ── ETF Flows (:8003) ── */

export interface EtfFlowEntry {
  id: number;
  ticker: string;
  date: string;
  close_price: number | null;
  volume: number | null;
  aum: number | null;
  estimated_flow: number | null;
  flow_pct: number | null;
  source: string;
}

export async function fetchEtfFlows(params?: {
  ticker?: string; start?: string; end?: string; limit?: number;
}): Promise<EtfFlowEntry[]> {
  const sp = new URLSearchParams();
  if (params?.ticker) sp.set("ticker", params.ticker);
  if (params?.start) sp.set("start", params.start);
  if (params?.end) sp.set("end", params.end);
  if (params?.limit) sp.set("limit", String(params.limit));
  const qs = sp.toString();
  const res = await fetch(`${US_LISTINGS_API}/api/v1/flows${qs ? `?${qs}` : ""}`, {
    next: { revalidate: 300 },
  });
  if (!res.ok) return [];
  const data = await res.json();
  return data.flows || [];
}

/* ── Fetch Triggers for new pipelines (:8003) ── */

export async function triggerInsiderFetch(date?: string): Promise<void> {
  const params = date ? `?date=${date}` : "";
  await fetch(`${US_LISTINGS_API}/api/v1/fetch-insider${params}`, { method: "POST" });
}

export async function triggerEarningsFetch(date?: string): Promise<void> {
  const params = date ? `?date=${date}` : "";
  await fetch(`${US_LISTINGS_API}/api/v1/fetch-earnings${params}`, { method: "POST" });
}

export async function triggerHoldingsFetch(): Promise<void> {
  await fetch(`${US_LISTINGS_API}/api/v1/fetch-holdings`, { method: "POST" });
}

export async function triggerRiskFetch(): Promise<void> {
  await fetch(`${US_LISTINGS_API}/api/v1/fetch-risk`, { method: "POST" });
}

export async function triggerFlowsFetch(): Promise<void> {
  await fetch(`${US_LISTINGS_API}/api/v1/fetch-flows`, { method: "POST" });
}

/* ── Dividends & Stock Splits (:8003) ── */

export interface Dividend {
  id: number;
  ticker: string;
  announcement_date: string | null;
  ex_dividend_date: string;
  pay_date: string | null;
  dividend_rate: number | null;
  dividend_yield: number | null;
  last_dividend_value: number | null;
  payout_ratio: number | null;
  five_year_avg_yield: number | null;
  source: string;
}

export interface StockSplit {
  id: number;
  ticker: string;
  split_date: string;
  split_ratio: string;
  source: string;
}

export async function fetchDividends(params?: {
  ticker?: string; start?: string; end?: string; limit?: number;
}): Promise<Dividend[]> {
  const sp = new URLSearchParams();
  if (params?.ticker) sp.set("ticker", params.ticker);
  if (params?.start) sp.set("start", params.start);
  if (params?.end) sp.set("end", params.end);
  if (params?.limit) sp.set("limit", String(params.limit));
  const qs = sp.toString();
  const res = await fetch(`${US_LISTINGS_API}/api/v1/dividends${qs ? `?${qs}` : ""}`, {
    next: { revalidate: 300 },
  });
  if (!res.ok) return [];
  const data = await res.json();
  return data.dividends || [];
}

export async function fetchSplits(ticker?: string, limit?: number): Promise<StockSplit[]> {
  const sp = new URLSearchParams();
  if (ticker) sp.set("ticker", ticker);
  if (limit) sp.set("limit", String(limit));
  const qs = sp.toString();
  const res = await fetch(`${US_LISTINGS_API}/api/v1/splits${qs ? `?${qs}` : ""}`, {
    next: { revalidate: 300 },
  });
  if (!res.ok) return [];
  const data = await res.json();
  return data.splits || [];
}

/* ── Trading Suspensions (:8003) ── */

export interface Suspension {
  id: number;
  ticker: string;
  company_name: string;
  suspension_type: string | null;
  reason: string | null;
  effective_date: string | null;
  filing_date: string;
  source_url: string | null;
}

export async function fetchSuspensions(params?: {
  ticker?: string; start?: string; end?: string; limit?: number;
}): Promise<Suspension[]> {
  const sp = new URLSearchParams();
  if (params?.ticker) sp.set("ticker", params.ticker);
  if (params?.start) sp.set("start", params.start);
  if (params?.end) sp.set("end", params.end);
  if (params?.limit) sp.set("limit", String(params.limit));
  const qs = sp.toString();
  const res = await fetch(`${US_LISTINGS_API}/api/v1/suspensions${qs ? `?${qs}` : ""}`, {
    next: { revalidate: 300 },
  });
  if (!res.ok) return [];
  const data = await res.json();
  return data.suspensions || [];
}

/* ── SEC Enforcement Actions (:8003) ── */

export interface EnforcementAction {
  id: number;
  enforcement_type: string;
  entity_name: string;
  ticker: string | null;
  penalty_amount: number | null;
  description: string | null;
  filing_date: string;
  source_url: string | null;
}

export async function fetchEnforcement(params?: {
  enforcement_type?: string; start?: string; end?: string; limit?: number;
}): Promise<EnforcementAction[]> {
  const sp = new URLSearchParams();
  if (params?.enforcement_type) sp.set("enforcement_type", params.enforcement_type);
  if (params?.start) sp.set("start", params.start);
  if (params?.end) sp.set("end", params.end);
  if (params?.limit) sp.set("limit", String(params.limit));
  const qs = sp.toString();
  const res = await fetch(`${US_LISTINGS_API}/api/v1/enforcement${qs ? `?${qs}` : ""}`, {
    next: { revalidate: 300 },
  });
  if (!res.ok) return [];
  const data = await res.json();
  return data.enforcement || [];
}

/* ── Threshold Securities (:8003) ── */

export interface ThresholdSecurity {
  id: number;
  ticker: string;
  security_name: string | null;
  market_category: string | null;
  is_threshold: boolean;
  date: string;
  source: string;
}

export async function fetchThresholdSecurities(params?: {
  ticker?: string; date?: string; limit?: number;
}): Promise<ThresholdSecurity[]> {
  const sp = new URLSearchParams();
  if (params?.ticker) sp.set("ticker", params.ticker);
  if (params?.date) sp.set("date", params.date);
  if (params?.limit) sp.set("limit", String(params.limit));
  const qs = sp.toString();
  const res = await fetch(`${US_LISTINGS_API}/api/v1/threshold${qs ? `?${qs}` : ""}`, {
    next: { revalidate: 300 },
  });
  if (!res.ok) return [];
  const data = await res.json();
  return data.threshold || [];
}

/* ── ATS / Dark Pool (:8003) ── */

export interface AtsFiling {
  id: number;
  ats_name: string;
  filer_cik: string;
  filer_name: string;
  filing_type: string | null;
  volume_estimate: string | null;
  securities_traded: string | null;
  description: string | null;
  filing_date: string;
  source_url: string | null;
}

export async function fetchAtsFilings(params?: {
  filer_cik?: string; start?: string; end?: string; limit?: number;
}): Promise<AtsFiling[]> {
  const sp = new URLSearchParams();
  if (params?.filer_cik) sp.set("filer_cik", params.filer_cik);
  if (params?.start) sp.set("start", params.start);
  if (params?.end) sp.set("end", params.end);
  if (params?.limit) sp.set("limit", String(params.limit));
  const qs = sp.toString();
  const res = await fetch(`${US_LISTINGS_API}/api/v1/ats${qs ? `?${qs}` : ""}`, {
    next: { revalidate: 300 },
  });
  if (!res.ok) return [];
  const data = await res.json();
  return data.ats || [];
}

/* ── Short Sale Activity (:8003) ── */

export interface ShortActivity {
  id: number;
  ticker: string;
  date: string;
  short_interest: number | null;
  short_pct_float: number | null;
  days_to_cover: number | null;
  avg_volume: number | null;
  float_shares: number | null;
  short_change_pct: number | null;
  insider_ownership_pct: number | null;
  institutional_ownership_pct: number | null;
  risk_level: string | null;
  squeeze_score: number | null;
  source: string;
}

export async function fetchShortActivity(params?: {
  ticker?: string; risk_level?: string; limit?: number;
}): Promise<ShortActivity[]> {
  const sp = new URLSearchParams();
  if (params?.ticker) sp.set("ticker", params.ticker);
  if (params?.risk_level) sp.set("risk_level", params.risk_level);
  if (params?.limit) sp.set("limit", String(params.limit));
  const qs = sp.toString();
  const res = await fetch(`${US_LISTINGS_API}/api/v1/short-activity${qs ? `?${qs}` : ""}`, {
    next: { revalidate: 300 },
  });
  if (!res.ok) return [];
  const data = await res.json();
  return data.short_activity || [];
}

/* ── IPO Lockup Expiry (:8003) ── */

export interface LockupExpiry {
  id: number;
  ticker: string;
  company_name: string;
  listing_date: string;
  listing_type: string;
  lockup_end_date: string;
  lockup_period_days: number;
  days_remaining: number;
  estimated_shares_unlocking: number | null;
  estimated_value: number | null;
  status: string;
}

export async function fetchLockupExpiry(params?: {
  ticker?: string; status?: string; limit?: number;
}): Promise<LockupExpiry[]> {
  const sp = new URLSearchParams();
  if (params?.ticker) sp.set("ticker", params.ticker);
  if (params?.status) sp.set("status", params.status);
  if (params?.limit) sp.set("limit", String(params.limit));
  const qs = sp.toString();
  const res = await fetch(`${US_LISTINGS_API}/api/v1/lockup${qs ? `?${qs}` : ""}`, {
    next: { revalidate: 300 },
  });
  if (!res.ok) return [];
  const data = await res.json();
  return data.lockups || [];
}

/* ── Options Flow (:8003) ── */

export interface OptionsFlowEntry {
  id: number;
  ticker: string;
  date: string;
  expiration_date: string;
  total_call_volume: number;
  total_put_volume: number;
  total_call_oi: number;
  total_put_oi: number;
  put_call_vol_ratio: number | null;
  put_call_oi_ratio: number | null;
  vol_oi_ratio: number | null;
  max_call_strike: number | null;
  max_call_volume: number | null;
  max_put_strike: number | null;
  max_put_volume: number | null;
  is_unusual: boolean;
  sentiment: string | null;
  source: string;
}

export async function fetchOptionsFlow(params?: {
  ticker?: string; unusual_only?: boolean; limit?: number;
}): Promise<OptionsFlowEntry[]> {
  const sp = new URLSearchParams();
  if (params?.ticker) sp.set("ticker", params.ticker);
  if (params?.unusual_only) sp.set("unusual_only", "true");
  if (params?.limit) sp.set("limit", String(params.limit));
  const qs = sp.toString();
  const res = await fetch(`${US_LISTINGS_API}/api/v1/options-flow${qs ? `?${qs}` : ""}`, {
    next: { revalidate: 300 },
  });
  if (!res.ok) return [];
  const data = await res.json();
  return data.options_flow || [];
}

/* ── Fetch Triggers for Round 2 pipelines (:8003) ── */

export async function triggerCorporateEventsFetch(): Promise<void> {
  await fetch(`${US_LISTINGS_API}/api/v1/fetch-corporate-events`, { method: "POST" });
}

export async function triggerSuspensionsFetch(date?: string): Promise<void> {
  const params = date ? `?date=${date}` : "";
  await fetch(`${US_LISTINGS_API}/api/v1/fetch-suspensions${params}`, { method: "POST" });
}

export async function triggerEnforcementFetch(date?: string): Promise<void> {
  const params = date ? `?date=${date}` : "";
  await fetch(`${US_LISTINGS_API}/api/v1/fetch-enforcement${params}`, { method: "POST" });
}

export async function triggerThresholdFetch(date?: string): Promise<void> {
  const params = date ? `?date=${date}` : "";
  await fetch(`${US_LISTINGS_API}/api/v1/fetch-threshold${params}`, { method: "POST" });
}

export async function triggerAtsFetch(date?: string): Promise<void> {
  const params = date ? `?date=${date}` : "";
  await fetch(`${US_LISTINGS_API}/api/v1/fetch-ats${params}`, { method: "POST" });
}

export async function triggerShortActivityFetch(): Promise<void> {
  await fetch(`${US_LISTINGS_API}/api/v1/fetch-short-activity`, { method: "POST" });
}

export async function triggerLockupFetch(): Promise<void> {
  await fetch(`${US_LISTINGS_API}/api/v1/fetch-lockup`, { method: "POST" });
}

export async function triggerOptionsFetch(): Promise<void> {
  await fetch(`${US_LISTINGS_API}/api/v1/fetch-options`, { method: "POST" });
}

/* ── HK Fund KYP (:8004) ── */

const HK_FUNDS_API = process.env.NEXT_PUBLIC_HK_FUNDS_API_URL || "http://127.0.0.1:8004";

export interface HKFund {
  id: number;
  sfc_authorization_no: string;
  fund_name_en: string;
  fund_name_cn: string | null;
  fund_type: string;
  fund_structure: string | null;
  domicile: string | null;
  currency: string | null;
  isin: string | null;
  bloomberg_ticker: string | null;
  launch_date: string | null;
  authorization_date: string | null;
  fund_manager_name_en: string | null;
  fund_manager_name_cn: string | null;
  fund_manager_id: number | null;
  trustee_custodian: string | null;
  management_fee_pct: number | null;
  performance_fee_pct: number | null;
  nav: number | null;
  nav_date: string | null;
  subscription_mode: string | null;
  redemption_frequency: string | null;
  min_subscription_hkd: number | null;
  min_subscription_usd: number | null;
  is_derivative_product: boolean;
  is_complex_product: boolean;
  complex_product_type: string;
  classification_reason: string | null;
  classification_source: string | null;
  is_active: boolean;
  // v4 — enhanced fund data
  expense_ratio_pct: number | null;
  front_load_pct: number | null;
  back_load_pct: number | null;
  benchmark_name: string | null;
  fund_inception_date: string | null;
  aum: number | null;
  aum_date: string | null;
  distribution_frequency: string | null;
  dividend_yield_12m_pct: number | null;
  source_type: string | null;
}

export interface HKFundClassification {
  id: number;
  fund_id: number;
  sfc_complex_list_match: boolean;
  derivative_exposure_pct: number | null;
  is_synthetic_replication: boolean;
  is_leveraged: boolean;
  leverage_ratio: number | null;
  is_inverse: boolean;
  is_structured: boolean;
  has_nested_derivatives: boolean;
  uses_derivatives_for_non_hedging: boolean;
  has_secondary_market: boolean;
  has_transparent_info: boolean;
  loss_exceeds_principal: boolean;
  has_complex_payoff: boolean;
  illiquid_or_hard_to_value: boolean;
  classification_determination: string | null;
  last_reviewed_date: string | null;
}

export interface HKFundManager {
  id: number;
  ce_number: string;
  company_name_en: string;
  company_name_cn: string | null;
  license_type: string;
  regulated_activity_1: boolean;
  regulated_activity_4: boolean;
  regulated_activity_9: boolean;
  license_status: string;
  license_effective_date: string | null;
  business_address: string | null;
  website: string | null;
  key_ro_name_en: string | null;
  key_ro_name_cn: string | null;
  ro_count: number | null;
  total_licensed_staff: number | null;
  has_sfc_enforcement_history: boolean;
  enforcement_count: number;
}

export interface HKManagerRegulatory {
  id: number;
  manager_id: number;
  source: string;
  action_type: string;
  action_date: string;
  penalty_amount_hkd: number | null;
  description_en: string | null;
  description_cn: string | null;
}

export interface HKFundStats {
  total: number;
  complex_count: number;
  derivative_count: number;
  by_complex_type: { complex_product_type: string; cnt: number }[];
  by_domicile: { domicile: string; cnt: number }[];
}

export interface HKManagerStats {
  total: number;
  type9_count: number;
  with_enforcement: number;
}

// v4 — NAV history & performance
export interface HKFundNavRecord {
  id: number;
  fund_id: number;
  nav_date: string;
  nav: number;
  nav_currency: string;
  source: string;
}

export interface HKFundPerformance {
  fund_id: number;
  ytd_return_pct: number | null;
  return_1m_pct: number | null;
  return_3m_pct: number | null;
  return_6m_pct: number | null;
  return_1y_pct: number | null;
  return_3y_annualized_pct: number | null;
  return_5y_annualized_pct: number | null;
  std_dev_3y: number | null;
  sharpe_ratio_3y: number | null;
  max_drawdown_pct: number | null;
  max_drawdown_period: string | null;
  alpha_3y: number | null;
  beta_3y: number | null;
  r_squared_3y: number | null;
  data_points_used: number | null;
  calculation_date: string | null;
}

export interface HKManagerScrapeStatus {
  registered_connectors: number;
  connectors: Array<{
    ce_number: string;
    company_name_en: string;
    website: string;
  }>;
  managers_needing_connectors: number;
  top_managers_without_connectors: Array<{
    ce_number: string;
    company_name_en: string;
    fund_count: number;
  }>;
}

export interface HKFundDetail extends HKFund {
  classification_detail: HKFundClassification | null;
  documents: { id: number; document_type: string; document_date: string; source_url: string | null }[];
  manager: HKFundManager | null;
}

export interface HKManagerDetail extends HKFundManager {
  fund_count: number;
  funds: HKFund[];
  regulatory_count: number;
  regulatory_history: HKManagerRegulatory[];
}

export async function fetchHKFunds(params?: {
  is_derivative_product?: boolean; is_complex_product?: boolean;
  complex_product_type?: string; fund_type?: string; domicile?: string;
  search?: string; limit?: number;
}): Promise<HKFund[]> {
  const sp = new URLSearchParams();
  if (params?.is_derivative_product !== undefined) sp.set("is_derivative_product", String(params.is_derivative_product));
  if (params?.is_complex_product !== undefined) sp.set("is_complex_product", String(params.is_complex_product));
  if (params?.complex_product_type) sp.set("complex_product_type", params.complex_product_type);
  if (params?.fund_type) sp.set("fund_type", params.fund_type);
  if (params?.domicile) sp.set("domicile", params.domicile);
  if (params?.search) sp.set("search", params.search);
  if (params?.limit) sp.set("limit", String(params.limit));
  const qs = sp.toString();
  const res = await fetch(`${HK_FUNDS_API}/api/v1/funds${qs ? `?${qs}` : ""}`, {
    next: { revalidate: 300 },
  });
  if (!res.ok) return [];
  const data = await res.json();
  return data.funds || [];
}

export async function fetchHKFundStats(): Promise<HKFundStats> {
  const res = await fetch(`${HK_FUNDS_API}/api/v1/funds/stats`, { next: { revalidate: 300 } });
  if (!res.ok) return { total: 0, complex_count: 0, derivative_count: 0, by_complex_type: [], by_domicile: [] };
  return res.json();
}

export async function fetchHKComplexFunds(limit?: number): Promise<HKFund[]> {
  const sp = new URLSearchParams();
  if (limit) sp.set("limit", String(limit));
  const res = await fetch(`${HK_FUNDS_API}/api/v1/funds/complex${sp.toString() ? `?${sp.toString()}` : ""}`, {
    next: { revalidate: 300 },
  });
  if (!res.ok) return [];
  const data = await res.json();
  return data.funds || [];
}

export async function fetchHKDerivativeFunds(limit?: number): Promise<HKFund[]> {
  const sp = new URLSearchParams();
  if (limit) sp.set("limit", String(limit));
  const res = await fetch(`${HK_FUNDS_API}/api/v1/funds/derivatives${sp.toString() ? `?${sp.toString()}` : ""}`, {
    next: { revalidate: 300 },
  });
  if (!res.ok) return [];
  const data = await res.json();
  return data.funds || [];
}

export async function searchHKFunds(q: string, limit?: number): Promise<HKFund[]> {
  const sp = new URLSearchParams(); sp.set("q", q);
  if (limit) sp.set("limit", String(limit));
  const res = await fetch(`${HK_FUNDS_API}/api/v1/funds/search?${sp.toString()}`, {
    next: { revalidate: 300 },
  });
  if (!res.ok) return [];
  const data = await res.json();
  return data.funds || [];
}

export async function fetchHKFundDetail(fundId: number): Promise<HKFundDetail | null> {
  const res = await fetch(`${HK_FUNDS_API}/api/v1/funds/${fundId}`, { next: { revalidate: 300 } });
  if (!res.ok) return null;
  return res.json();
}

export async function fetchHKManagers(params?: {
  license_type?: string; license_status?: string; has_enforcement?: boolean;
  search?: string; limit?: number;
}): Promise<HKFundManager[]> {
  const sp = new URLSearchParams();
  if (params?.license_type) sp.set("license_type", params.license_type);
  if (params?.license_status) sp.set("license_status", params.license_status);
  if (params?.has_enforcement !== undefined) sp.set("has_enforcement", String(params.has_enforcement));
  if (params?.search) sp.set("search", params.search);
  if (params?.limit) sp.set("limit", String(params.limit));
  const qs = sp.toString();
  const res = await fetch(`${HK_FUNDS_API}/api/v1/managers${qs ? `?${qs}` : ""}`, {
    next: { revalidate: 300 },
  });
  if (!res.ok) return [];
  const data = await res.json();
  return data.managers || [];
}

export async function fetchHKManagerStats(): Promise<HKManagerStats> {
  const res = await fetch(`${HK_FUNDS_API}/api/v1/managers/stats`, { next: { revalidate: 300 } });
  if (!res.ok) return { total: 0, type9_count: 0, with_enforcement: 0 };
  return res.json();
}

export async function fetchHKManagerDetail(managerId: number): Promise<HKManagerDetail | null> {
  const res = await fetch(`${HK_FUNDS_API}/api/v1/managers/${managerId}`, { next: { revalidate: 300 } });
  if (!res.ok) return null;
  return res.json();
}

export async function triggerHKFundsFetch(): Promise<void> {
  await fetch(`${HK_FUNDS_API}/api/v1/fetch-funds`, { method: "POST" });
}

export async function triggerHKManagersFetch(): Promise<void> {
  await fetch(`${HK_FUNDS_API}/api/v1/fetch-managers`, { method: "POST" });
}

export async function triggerHKClassify(): Promise<void> {
  await fetch(`${HK_FUNDS_API}/api/v1/classify`, { method: "POST" });
}

/* ── HK Fund KYP Dimensions ── */

export interface HK_KypDimension {
  id: number;
  fund_id: number;
  dimension: string;
  assessment_status: string;
  data_source: string | null;
  assessed_by: string | null;
  assessment_date: string | null;
  next_review_date: string | null;
  score: number | null;
  findings: string | null;
  gaps: string | null;
}

export async function fetchHK_KypDimensions(fundId: number): Promise<HK_KypDimension[]> {
  const res = await fetch(`${HK_FUNDS_API}/api/v1/funds/${fundId}/kyp`, { next: { revalidate: 300 } });
  if (!res.ok) return [];
  return res.json();
}

export async function updateHK_KypDimension(fundId: number, dimension: string, data: Partial<HK_KypDimension>): Promise<boolean> {
  const res = await fetch(`${HK_FUNDS_API}/api/v1/funds/${fundId}/kyp/${dimension}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return res.ok;
}

export async function fetchHK_KypGaps(limit?: number): Promise<any[]> {
  const sp = new URLSearchParams();
  if (limit) sp.set("limit", String(limit));
  const res = await fetch(`${HK_FUNDS_API}/api/v1/kyp/gaps?${sp.toString()}`, { next: { revalidate: 300 } });
  if (!res.ok) return [];
  return res.json();
}

/* ── HK Fund Risk Ratings ── */

export interface HK_FundRiskRating {
  fund_id: number;
  fund_name_en?: string;
  sfc_authorization_no?: string;
  fund_manager_name_en?: string;
  is_derivative_product?: boolean;
  overall_risk_score: number;
  risk_category: string;
  is_automated: boolean;
  score_breakdown?: string;
  supporting_rationale?: string | null;
  last_calculated?: string;
}

export async function fetchHK_FundRiskRating(fundId: number): Promise<HK_FundRiskRating | null> {
  const res = await fetch(`${HK_FUNDS_API}/api/v1/funds/${fundId}/risk-rating`, { next: { revalidate: 300 } });
  if (!res.ok) return null;
  const data = await res.json();
  if (data.error) return null;
  return data;
}

export async function fetchHK_AllRiskRatings(riskCategory?: string): Promise<HK_FundRiskRating[]> {
  const sp = new URLSearchParams();
  if (riskCategory) sp.set("risk_category", riskCategory);
  const res = await fetch(`${HK_FUNDS_API}/api/v1/risk-ratings?${sp.toString()}`, { next: { revalidate: 300 } });
  if (!res.ok) return [];
  return res.json();
}

export async function overrideHK_RiskRating(fundId: number, newScore: number, newCategory: string, reason: string): Promise<boolean> {
  const sp = new URLSearchParams();
  sp.set("new_score", String(newScore));
  sp.set("new_category", newCategory);
  sp.set("reason", reason);
  const res = await fetch(`${HK_FUNDS_API}/api/v1/funds/${fundId}/risk-rating/override?${sp.toString()}`, { method: "PUT" });
  return res.ok;
}

/* ── HK Manager DD ── */

export interface HK_ManagerDd {
  id: number;
  manager_id: number;
  dd_dimension: string;
  assessment_status: string;
  score: number | null;
  findings: string | null;
  gaps: string | null;
  assessment_date: string | null;
}

export async function fetchHK_ManagerDd(managerId: number): Promise<HK_ManagerDd[]> {
  const res = await fetch(`${HK_FUNDS_API}/api/v1/managers/${managerId}/dd`, { next: { revalidate: 300 } });
  if (!res.ok) return [];
  return res.json();
}

/* ── HK Non-Authorized Funds ── */

export interface HK_NonAuthorizedFund {
  id: number;
  fund_name_en: string;
  fund_name_cn: string | null;
  isin: string | null;
  bloomberg_ticker: string | null;
  fund_type: string | null;
  domicile: string | null;
  currency: string | null;
  fund_manager_name_en: string | null;
  fund_manager_name_cn: string | null;
  distribution_restriction: string;
  min_investment_hkd: number | null;
  is_active: boolean;
  data_source: string | null;
  notes: string | null;
  created_at: string | null;
}

export async function fetchHK_NonAuthorizedFunds(params?: {
  distribution_restriction?: string; limit?: number;
}): Promise<HK_NonAuthorizedFund[]> {
  const sp = new URLSearchParams();
  if (params?.distribution_restriction) sp.set("distribution_restriction", params.distribution_restriction);
  if (params?.limit) sp.set("limit", String(params.limit));
  const res = await fetch(`${HK_FUNDS_API}/api/v1/non-authorized-funds?${sp.toString()}`, { next: { revalidate: 300 } });
  if (!res.ok) return [];
  return res.json();
}

export async function createHK_NonAuthorizedFund(records: Partial<HK_NonAuthorizedFund>[]): Promise<number> {
  const res = await fetch(`${HK_FUNDS_API}/api/v1/non-authorized-funds`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(records),
  });
  if (!res.ok) return 0;
  const data = await res.json();
  return data.stored || 0;
}

export async function fetchHK_NonAuthorizedFundDetail(fundId: number): Promise<HK_NonAuthorizedFund | null> {
  const res = await fetch(`${HK_FUNDS_API}/api/v1/non-authorized-funds/${fundId}`, { next: { revalidate: 300 } });
  if (!res.ok) return null;
  return res.json();
}

/* ── HK Fund v4: NAV History & Performance ── */

export async function fetchHKFundNavHistory(
  fundId: number, start?: string, end?: string
): Promise<{ fund_id: number; nav_history: HKFundNavRecord[] }> {
  const sp = new URLSearchParams();
  if (start) sp.set("start", start);
  if (end) sp.set("end", end);
  const res = await fetch(`${HK_FUNDS_API}/api/v1/funds/${fundId}/nav-history?${sp.toString()}`, { next: { revalidate: 60 } });
  if (!res.ok) return { fund_id: fundId, nav_history: [] };
  return res.json();
}

export async function fetchHKFundPerformance(fundId: number): Promise<{ fund_id: number; performance: HKFundPerformance | null }> {
  const res = await fetch(`${HK_FUNDS_API}/api/v1/funds/${fundId}/performance`, { next: { revalidate: 300 } });
  if (!res.ok) return { fund_id: fundId, performance: null };
  return res.json();
}

/* ── HK Fund v4: Manager Scrape ── */

export async function fetchHKManagerScrapeStatus(): Promise<HKManagerScrapeStatus> {
  const res = await fetch(`${HK_FUNDS_API}/api/v1/managers/scrape/status`, { next: { revalidate: 300 } });
  if (!res.ok) return { registered_connectors: 0, connectors: [], managers_needing_connectors: 0, top_managers_without_connectors: [] };
  return res.json();
}

export async function triggerHKManagerScrape(ceNumber?: string): Promise<any> {
  const sp = new URLSearchParams();
  if (ceNumber) sp.set("ce_number", ceNumber);
  const res = await fetch(`${HK_FUNDS_API}/api/v1/managers/scrape?${sp.toString()}`, { method: "POST" });
  return res.json();
}

/* ── Announcements ──────────────────────────────────── */

const ANN_API = process.env.NEXT_PUBLIC_ANN_API_URL || "http://127.0.0.1:8005";

export interface Announcement {
  id: number;
  ticker: string;
  market: string;
  company_name: string;
  title: string | null;
  announcement_date: string;
  source: string;
  filing_type: string | null;
  source_url: string | null;
  local_file_path: string | null;
  text_content?: string | null;
  file_type: string | null;
  created_at: string;
}

export interface AnnouncementListResponse {
  count: number;
  announcements: Announcement[];
}

export interface TrackedCompany {
  ticker: string;
  market: string;
  company_name: string;
  announcement_count: number;
}

export async function fetchAnnouncements(params?: {
  ticker?: string; market?: string; source?: string;
  start?: string; end?: string; limit?: number;
}): Promise<AnnouncementListResponse> {
  const sp = new URLSearchParams();
  if (params?.ticker) sp.set("ticker", params.ticker);
  if (params?.market) sp.set("market", params.market);
  if (params?.source) sp.set("source", params.source);
  if (params?.start) sp.set("start", params.start);
  if (params?.end) sp.set("end", params.end);
  if (params?.limit) sp.set("limit", String(params.limit ?? 100));
  const qs = sp.toString();
  const res = await fetch(`${ANN_API}/api/v1/announcements${qs ? `?${qs}` : ""}`, {
    next: { revalidate: 300 },
  });
  if (!res.ok) return { count: 0, announcements: [] };
  return res.json();
}

export async function fetchAnnouncementDetail(id: number): Promise<Announcement | null> {
  const res = await fetch(`${ANN_API}/api/v1/announcements/${id}`, {
    next: { revalidate: 300 },
  });
  if (!res.ok) return null;
  return res.json();
}

export async function fetchTrackedCompanies(): Promise<TrackedCompany[]> {
  const res = await fetch(`${ANN_API}/api/v1/companies`, {
    next: { revalidate: 300 },
  });
  if (!res.ok) return [];
  const data = await res.json();
  return data.companies || [];
}

export async function triggerAnnFetch(): Promise<void> {
  await fetch(`${ANN_API}/api/v1/fetch`, { method: "POST" });
}

/* ── TW Stock (:8007) ── */

const TW_STOCK_API = process.env.NEXT_PUBLIC_TW_STOCK_API_URL || "http://127.0.0.1:8007";

/* ── SK Hynix Cross-Market (:8008) ── */

const HYNIX_API = process.env.NEXT_PUBLIC_HYNIX_API_URL || "http://127.0.0.1:8008";

export interface HynixInstrument {
  ticker: string;
  name: string;
  market: string;
  currency: string;
  instrument_type: string;
  leverage: number;
  tracking_ratio: number | null;
  skh_weight: number;
  yf_ticker: string;
  note: string;
}

export interface HynixArbitrageInstrument {
  ticker: string;
  name: string;
  market: string;
  currency: string;
  instrument_type: string;
  leverage: number;
  price_local: number;
  price_krw: number;
  nav_local: number | null;
  nav_krw: number | null;
  tracking_ratio: number;
  equivalent_krw_per_share: number;
  premium_pct_vs_base: number;
  nav_premium_pct: number | null;
}

export interface HynixArbitrageSnapshot {
  date: string;
  base_ticker: string;
  base_price_krw: number;
  fx_rates: Record<string, number>;
  count: number;
  instruments: HynixArbitrageInstrument[];
}

export interface HynixPricePoint {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  nav: number | null;
  change_pct: number | null;
}

export interface HynixArbitrageHistoryPoint {
  date: string;
  price_local: number;
  price_krw: number;
  base_price_krw: number;
  equivalent_krw_per_share: number;
  premium_pct: number;
  nav_premium_pct: number | null;
}

export interface TwListedStock {
  code: string;
  name: string;
  name_en: string | null;
  market: string;
  sector: string | null;
  industry: string | null;
  listing_date: string | null;
  market_cap: number | null;
}

export interface TwDailyMover {
  date: string;
  code: string;
  name: string;
  change_pct: number;
  volume: number;
  close: number;
  market: string;
  sector: string | null;
  industry: string | null;
  reasons: string | null;
}

export interface TwIndexData {
  date: string;
  index_code: string;
  index_name: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number;
  change_pct: number | null;
}

export interface TwDailyReview {
  date: string;
  summary: {
    date: string;
    mover_count: number;
    avg_change: number;
    max_change: number;
    up_count: number;
    down_count: number;
    sector_count: number;
    indices: { index_code: string; close: number; change_pct: number | null }[];
  };
  movers: TwDailyMover[];
  narratives: { date: string; tag: string; name: string; description: string; stocks: string[] }[];
  industries: { industry: string; count: number; avg_change: number; max_change: number }[];
}

export async function fetchTwDailyReview(date: string): Promise<TwDailyReview> {
  const res = await fetch(`${TW_STOCK_API}/api/v1/daily/${date}`, {
    next: { revalidate: 300 },
  });
  if (!res.ok) throw new Error(`Failed to fetch TW daily review: ${res.status}`);
  return res.json();
}

export async function fetchTwListings(params?: {
  market?: string; sector?: string; search?: string; limit?: number;
}): Promise<TwListedStock[]> {
  const sp = new URLSearchParams();
  if (params?.market) sp.set("market", params.market);
  if (params?.sector) sp.set("sector", params.sector);
  if (params?.search) sp.set("search", params.search);
  if (params?.limit) sp.set("limit", String(params.limit ?? 100));
  const qs = sp.toString();
  const res = await fetch(`${TW_STOCK_API}/api/v1/listings${qs ? `?${qs}` : ""}`, {
    next: { revalidate: 300 },
  });
  if (!res.ok) return [];
  const data = await res.json();
  return data.stocks || [];
}

export async function fetchTwIndices(params?: {
  index_code?: string; start?: string; end?: string; limit?: number;
}): Promise<TwIndexData[]> {
  const sp = new URLSearchParams();
  if (params?.index_code) sp.set("index_code", params.index_code);
  if (params?.start) sp.set("start", params.start);
  if (params?.end) sp.set("end", params.end);
  if (params?.limit) sp.set("limit", String(params.limit ?? 200));
  const qs = sp.toString();
  const res = await fetch(`${TW_STOCK_API}/api/v1/indices${qs ? `?${qs}` : ""}`, {
    next: { revalidate: 300 },
  });
  if (!res.ok) return [];
  const data = await res.json();
  return data.indices || [];
}

export async function fetchTwAvailableDates(): Promise<string[]> {
  const res = await fetch(`${TW_STOCK_API}/api/v1/dates`, {
    next: { revalidate: 60 },
  });
  if (!res.ok) return [];
  const data = await res.json();
  return data.dates || [];
}

export async function triggerTwFetch(date?: string): Promise<void> {
  const params = date ? `?date=${date}` : "";
  await fetch(`${TW_STOCK_API}/api/v1/fetch${params}`, { method: "POST" });
}

/* ── KR Stock (:8006) ── */

const KR_STOCK_API = process.env.NEXT_PUBLIC_KR_STOCK_API_URL || "http://127.0.0.1:8006";

export interface KrListedStock {
  code: string;
  name: string;
  name_en: string | null;
  market: string;
  sector: string | null;
  industry: string | null;
  listing_date: string | null;
  market_cap: number | null;
}

export interface KrDailyMover {
  date: string;
  code: string;
  name: string;
  change_pct: number;
  volume: number;
  close: number;
  market: string;
  sector: string | null;
  industry: string | null;
  reasons: string | null;
}

export interface KrIndexData {
  date: string;
  index_code: string;
  index_name: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number;
  change_pct: number | null;
}

export interface KrDailyReview {
  date: string;
  summary: {
    date: string;
    mover_count: number;
    avg_change: number;
    max_change: number;
    up_count: number;
    down_count: number;
    sector_count: number;
    indices: { index_code: string; close: number; change_pct: number | null }[];
  };
  movers: KrDailyMover[];
  narratives: { date: string; tag: string; name: string; description: string; stocks: string[] }[];
  industries: { industry: string; count: number; avg_change: number; max_change: number }[];
}

export interface KrStockMetrics {
  code: string;
  date: string | null;
  market_cap: number | null;
  enterprise_value: number | null;
  pe_trailing: number | null;
  pe_forward: number | null;
  pb_ratio: number | null;
  ps_ratio: number | null;
  dividend_yield: number | null;
  payout_ratio: number | null;
  beta: number | null;
  roa: number | null;
  roe: number | null;
  gross_margin: number | null;
  ebitda_margin: number | null;
  operating_margin: number | null;
  revenue_growth: number | null;
  earnings_growth: number | null;
  free_cashflow: number | null;
  operating_cashflow: number | null;
  inst_holding_pct: number | null;
  insider_holding_pct: number | null;
  shares_outstanding: number | null;
  float_shares: number | null;
  ma_50: number | null;
  ma_200: number | null;
  high_52w: number | null;
  low_52w: number | null;
}

export interface KrAnalystData {
  code: string;
  date: string | null;
  target_mean: number | null;
  target_high: number | null;
  target_low: number | null;
  target_median: number | null;
  recommendation: string | null;
  num_analysts: number | null;
  earnings_estimate_avg: number | null;
  revenue_estimate_avg: number | null;
  eps_trend_current: number | null;
  eps_trend_7d_ago: number | null;
  eps_trend_30d_ago: number | null;
}

export async function fetchKrDailyReview(date: string): Promise<KrDailyReview> {
  const res = await fetch(`${KR_STOCK_API}/api/v1/daily/${date}`, {
    next: { revalidate: 300 },
  });
  if (!res.ok) throw new Error(`Failed to fetch KR daily review: ${res.status}`);
  return res.json();
}

export async function fetchKrListings(params?: {
  market?: string; sector?: string; search?: string; limit?: number;
}): Promise<KrListedStock[]> {
  const sp = new URLSearchParams();
  if (params?.market) sp.set("market", params.market);
  if (params?.sector) sp.set("sector", params.sector);
  if (params?.search) sp.set("search", params.search);
  if (params?.limit) sp.set("limit", String(params.limit ?? 100));
  const qs = sp.toString();
  const res = await fetch(`${KR_STOCK_API}/api/v1/listings${qs ? `?${qs}` : ""}`, {
    next: { revalidate: 300 },
  });
  if (!res.ok) return [];
  const data = await res.json();
  return data.stocks || [];
}

export async function fetchKrIndices(params?: {
  index_code?: string; start?: string; end?: string; limit?: number;
}): Promise<KrIndexData[]> {
  const sp = new URLSearchParams();
  if (params?.index_code) sp.set("index_code", params.index_code);
  if (params?.start) sp.set("start", params.start);
  if (params?.end) sp.set("end", params.end);
  if (params?.limit) sp.set("limit", String(params.limit ?? 200));
  const qs = sp.toString();
  const res = await fetch(`${KR_STOCK_API}/api/v1/indices${qs ? `?${qs}` : ""}`, {
    next: { revalidate: 300 },
  });
  if (!res.ok) return [];
  const data = await res.json();
  return data.indices || [];
}

export async function fetchKrAvailableDates(): Promise<string[]> {
  const res = await fetch(`${KR_STOCK_API}/api/v1/dates`, {
    next: { revalidate: 60 },
  });
  if (!res.ok) return [];
  const data = await res.json();
  return data.dates || [];
}

export async function fetchKrStockMetrics(code: string): Promise<KrStockMetrics | null> {
  const res = await fetch(`${KR_STOCK_API}/api/v1/stock/${code}/metrics`, {
    next: { revalidate: 300 },
  });
  if (!res.ok) return null;
  return res.json();
}

export async function fetchKrStockAnalyst(code: string): Promise<KrAnalystData | null> {
  const res = await fetch(`${KR_STOCK_API}/api/v1/stock/${code}/analyst`, {
    next: { revalidate: 300 },
  });
  if (!res.ok) return null;
  return res.json();
}

export async function triggerKrFetch(date?: string): Promise<void> {
  const params = date ? `?date=${date}` : "";
  await fetch(`${KR_STOCK_API}/api/v1/fetch${params}`, { method: "POST" });
}


/* ── HK Rating Templates ── */

export interface RatingTemplate {
  id: number;
  user_id: string;
  name: string;
  description: string;
  template_type: "fund_risk" | "manager_dd";
  is_system: boolean;
  methodology_version: string;
  factor_count: number;
  category_thresholds?: string;
  created_at?: string;
  updated_at?: string;
}

export interface TemplateDetail {
  id: number;
  user_id: string;
  name: string;
  description: string;
  template_type: "fund_risk" | "manager_dd";
  is_system: boolean;
  methodology_version: string;
  factors: TemplateFactor[];
  category_thresholds: CategoryThreshold[];
}

export interface TemplateFactor {
  id?: number;
  factor_key: string;
  factor_label: string;
  weight: number;
  ordinal: number;
  config: Record<string, unknown>;
}

export interface CategoryThreshold {
  max: number;
  label: string;
}

export interface UserRatingResult {
  target_id: number;
  target_name: string;
  overall_score: number;
  category: string;
  factor_count: number;
  computed_at?: string;
}

export interface RatingResults {
  template_id: number;
  user_id: string;
  target_type: string;
  total_rated: number;
  distribution: { category: string; count: number }[];
  results: UserRatingResult[];
}

export async function fetchRatingTemplates(
  user_id: string = "system",
  template_type?: string
): Promise<RatingTemplate[]> {
  const sp = new URLSearchParams({ user_id });
  if (template_type) sp.set("template_type", template_type);
  const res = await fetch(`${HK_FUNDS_API}/api/v1/templates?${sp}`, {
    next: { revalidate: 30 },
  });
  if (!res.ok) return [];
  const data = await res.json();
  return data.templates || [];
}

export async function fetchRatingTemplateDetail(
  template_id: number
): Promise<TemplateDetail | null> {
  const res = await fetch(`${HK_FUNDS_API}/api/v1/templates/${template_id}`, {
    next: { revalidate: 30 },
  });
  if (!res.ok) return null;
  return res.json();
}

export async function cloneRatingTemplate(
  source_template_id: number,
  user_id: string,
  new_name?: string
): Promise<{ cloned_template_id: number; template: RatingTemplate } | null> {
  const sp = new URLSearchParams({
    source_template_id: String(source_template_id),
    user_id,
  });
  if (new_name) sp.set("new_name", new_name);
  const res = await fetch(`${HK_FUNDS_API}/api/v1/templates/clone?${sp}`, {
    method: "POST",
  });
  if (!res.ok) return null;
  return res.json();
}

export async function updateRatingTemplate(
  template_id: number,
  body: {
    user_id: string;
    name?: string;
    description?: string;
    factor_weights?: Record<string, number>;
    category_thresholds?: CategoryThreshold[];
  }
): Promise<{ template_id: number; updated: string[] } | null> {
  const res = await fetch(`${HK_FUNDS_API}/api/v1/templates/${template_id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) return null;
  return res.json();
}

export async function deleteRatingTemplate(
  template_id: number
): Promise<{ deleted: boolean } | null> {
  const res = await fetch(`${HK_FUNDS_API}/api/v1/templates/${template_id}`, {
    method: "DELETE",
  });
  if (!res.ok) return null;
  return res.json();
}

export async function computeRatings(
  template_id: number,
  user_id: string,
  target_type: string = "fund",
  target_id?: number
): Promise<RatingResults | { overall_score: number; category: string; total_rated?: undefined } | null> {
  const res = await fetch(
    `${HK_FUNDS_API}/api/v1/templates/${template_id}/compute`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id, target_type, target_id: target_id || 0 }),
    }
  );
  if (!res.ok) return null;
  return res.json();
}

export async function fetchRatingResults(
  template_id: number,
  user_id: string,
  target_type: string = "fund",
  limit: number = 100
): Promise<RatingResults | null> {
  const sp = new URLSearchParams({
    user_id,
    target_type,
    limit: String(limit),
  });
  const res = await fetch(
    `${HK_FUNDS_API}/api/v1/templates/${template_id}/results?${sp}`,
    { next: { revalidate: 30 } }
  );
  if (!res.ok) return null;
  return res.json();
}

/* ── SK Hynix Cross-Market (:8008) ── */

export async function fetchHynixInstruments(
  market?: string
): Promise<HynixInstrument[]> {
  const sp = new URLSearchParams();
  if (market) sp.set("market", market);
  const qs = sp.toString();
  const res = await fetch(`${HYNIX_API}/api/v1/instruments${qs ? `?${qs}` : ""}`, {
    next: { revalidate: 300 },
  });
  if (!res.ok) return [];
  const data = await res.json();
  return data.instruments || [];
}

export async function fetchHynixLatestArbitrage(): Promise<HynixArbitrageSnapshot | null> {
  const res = await fetch(`${HYNIX_API}/api/v1/arbitrage/latest`, {
    next: { revalidate: 60 },
  });
  if (!res.ok) return null;
  return res.json();
}

export async function fetchHynixArbitrageByDate(
  date: string
): Promise<HynixArbitrageSnapshot | null> {
  const res = await fetch(`${HYNIX_API}/api/v1/arbitrage/${date}`, {
    next: { revalidate: 300 },
  });
  if (!res.ok) return null;
  return res.json();
}

export async function fetchHynixArbitrageHistory(
  ticker: string,
  start?: string,
  end?: string,
  limit: number = 60
): Promise<HynixArbitrageHistoryPoint[]> {
  const sp = new URLSearchParams({ limit: String(limit) });
  if (start) sp.set("start", start);
  if (end) sp.set("end", end);
  const res = await fetch(
    `${HYNIX_API}/api/v1/arbitrage/${ticker}/history?${sp}`,
    { next: { revalidate: 60 } }
  );
  if (!res.ok) return [];
  const data = await res.json();
  return data.history || [];
}

export async function fetchHynixPrices(
  ticker: string,
  limit: number = 60
): Promise<HynixPricePoint[]> {
  const res = await fetch(
    `${HYNIX_API}/api/v1/prices/${ticker}?limit=${limit}`,
    { next: { revalidate: 60 } }
  );
  if (!res.ok) return [];
  const data = await res.json();
  return data.prices || [];
}

export async function fetchHynixAvailableDates(
  limit: number = 30
): Promise<string[]> {
  const res = await fetch(`${HYNIX_API}/api/v1/dates?limit=${limit}`, {
    next: { revalidate: 300 },
  });
  if (!res.ok) return [];
  const data = await res.json();
  return data.dates || [];
}

export async function fetchHynixFXLatest(): Promise<{
  date: string;
  rates: Record<string, number>;
} | null> {
  const res = await fetch(`${HYNIX_API}/api/v1/fx/latest`, {
    next: { revalidate: 60 },
  });
  if (!res.ok) return null;
  return res.json();
}

export async function triggerHynixFetch(date?: string): Promise<void> {
  const params = date ? `?date=${date}` : "";
  await fetch(`${HYNIX_API}/api/v1/fetch${params}`, { method: "POST" });
}

/* ── Korean Retail Leverage (kimpremium.com via :8008) ── */

export interface KrLeverageSummary {
  generated: string;
  asof: string;
  range: { start: string; end: string; rows: number };
  latest_daily_date: string | null;
  latest_etf_date: string | null;
  kpi: Record<string, number | string | null>;
  etf_kpi: Record<string, unknown>;
  latest_daily: {
    date: string;
    r2: number | null;
    r2_10y_pct: number | null;
    kospi: number | null;
    spx: number | null;
    fin_trillion: number | null;
    dep_trillion: number | null;
    liq_100m: number | null;
    liq_ratio: number | null;
    mcap_gdp_pct: number | null;
    credit_util_pct: number | null;
    misu_trillion: number | null;
  } | null;
}

export interface KrLeverageSeriesPoint {
  date: string;
  value: number | null;
}

export async function fetchKrLeverageSummary(): Promise<KrLeverageSummary | null> {
  const res = await fetch(`${HYNIX_API}/api/v1/kr-leverage/summary`, {
    next: { revalidate: 300 },
    signal: AbortSignal.timeout(10000),
  });
  if (!res.ok) return null;
  return res.json();
}

export async function fetchKrLeverageSeries(
  indicator: string,
  limit = 500,
): Promise<{ indicator: string; count: number; data: KrLeverageSeriesPoint[] } | null> {
  const res = await fetch(
    `${HYNIX_API}/api/v1/kr-leverage/series?indicator=${indicator}&limit=${limit}`,
    { next: { revalidate: 300 }, signal: AbortSignal.timeout(10000) },
  );
  if (!res.ok) return null;
  return res.json();
}

export async function fetchKrLeverageETF(
  indicator: string,
  limit = 500,
): Promise<{ indicator: string; count: number; data: KrLeverageSeriesPoint[] } | null> {
  const res = await fetch(
    `${HYNIX_API}/api/v1/kr-leverage/etf?indicator=${indicator}&limit=${limit}`,
    { next: { revalidate: 300 }, signal: AbortSignal.timeout(10000) },
  );
  if (!res.ok) return null;
  return res.json();
}

/* ── KOL Thermometer (:8010) ── */

const KOL_API = process.env.NEXT_PUBLIC_KOL_API_URL || "http://127.0.0.1:8010";

export interface KolItem {
  id: number;
  platform: string;
  username: string;
  display_name: string;
  followers: number;
  tier: string;
  total_score: number;
  base_weight: number;
  posts_per_week: number;
  last_post_date: string;
}

export interface ThermometerStock {
  date: string;
  stock_code: string;
  stock_name: string;
  market: string;
  mention_count: number;
  unique_kols: number;
  heat_score: number;
  sentiment_bias: number;
  momentum: number;
}

export interface StockMention {
  stock_code: string;
  sentiment_score: number;
  sentiment_label: string;
  mention_context: string;
  post_title: string;
  posted_at: string;
  platform: string;
  username: string;
  display_name: string;
  tier: string;
}

export interface KolThermometerStats {
  active_kols: number;
  total_posts: number;
  total_mentions: number;
  thermometer_days: number;
  tier_distribution: { tier: string; cnt: number }[];
  platform_distribution: { platform: string; cnt: number }[];
}

export async function fetchKols(params?: {
  platform?: string; tier?: string; limit?: number;
}): Promise<{ count: number; kols: KolItem[] }> {
  const sp = new URLSearchParams();
  if (params?.platform) sp.set("platform", params.platform);
  if (params?.tier) sp.set("tier", params.tier);
  if (params?.limit) sp.set("limit", String(params.limit));
  const qs = sp.toString();
  const res = await fetch(`${KOL_API}/api/v1/kols${qs ? `?${qs}` : ""}`, {
    next: { revalidate: 300 },
    signal: AbortSignal.timeout(10000),
  });
  if (!res.ok) return { count: 0, kols: [] };
  return res.json();
}

export async function fetchThermometer(params?: {
  market?: string; min_heat?: number; limit?: number;
}): Promise<{ count: number; stocks: ThermometerStock[] }> {
  const sp = new URLSearchParams();
  if (params?.market) sp.set("market", params.market);
  if (params?.min_heat) sp.set("min_heat", String(params.min_heat ?? 0));
  if (params?.limit) sp.set("limit", String(params.limit ?? 50));
  const qs = sp.toString();
  const res = await fetch(`${KOL_API}/api/v1/thermometer${qs ? `?${qs}` : ""}`, {
    next: { revalidate: 120 },
    signal: AbortSignal.timeout(10000),
  });
  if (!res.ok) return { count: 0, stocks: [] };
  return res.json();
}

export async function fetchThermometerStock(stockCode: string, days = 14): Promise<{
  stock_code: string; thermometer_history: ThermometerStock[]; recent_mentions: StockMention[];
} | null> {
  const res = await fetch(`${KOL_API}/api/v1/thermometer/${stockCode}?days=${days}`, {
    next: { revalidate: 120 },
    signal: AbortSignal.timeout(10000),
  });
  if (!res.ok) return null;
  return res.json();
}

export async function fetchKolMentions(params?: {
  stock_code?: string; platform?: string; limit?: number;
}): Promise<{ count: number; mentions: any[] }> {
  const sp = new URLSearchParams();
  if (params?.stock_code) sp.set("stock_code", params.stock_code);
  if (params?.platform) sp.set("platform", params.platform);
  if (params?.limit) sp.set("limit", String(params.limit ?? 50));
  const qs = sp.toString();
  const res = await fetch(`${KOL_API}/api/v1/mentions${qs ? `?${qs}` : ""}`, {
    next: { revalidate: 120 },
    signal: AbortSignal.timeout(10000),
  });
  if (!res.ok) return { count: 0, mentions: [] };
  return res.json();
}

export async function fetchKolStats(): Promise<KolThermometerStats | null> {
  const res = await fetch(`${KOL_API}/api/v1/stats`, {
    next: { revalidate: 300 },
    signal: AbortSignal.timeout(10000),
  });
  if (!res.ok) return null;
  return res.json();
}

/* ── A-Share Money Flow (:8011) ── */

const MONEY_FLOW_API = process.env.NEXT_PUBLIC_MONEY_FLOW_API_URL || "http://127.0.0.1:8011";

export interface AuctionStock {
  code: string;
  name: string;
  gap_pct: number;
  volume: number;
  amount: number;
  turnover: number;
  rush_score: number;
  sector: string;
}

export interface AuctionSector {
  sector: string;
  stock_count: number;
  avg_rush_score: number;
  max_rush_score: number;
  rush_stocks_count: number;
  total_auction_amount: number;
  top_stocks: string;
}

export interface FundFlowStock {
  code: string;
  name: string;
  latest_price: number;
  change_pct: number;
  main_inflow: number;
  main_inflow_pct: number;
  super_large_inflow: number;
  large_inflow: number;
  medium_inflow: number;
  small_inflow: number;
}

export interface FundFlowSector {
  sector_name: string;
  change_pct: number;
  main_inflow: number;
  main_inflow_pct: number;
  super_large_inflow: number;
  large_inflow: number;
  medium_inflow: number;
  small_inflow: number;
  top_stock: string;
}

export interface MoneyFlowStats {
  auction_days: number;
  auction_stock_records: number;
  fund_flow_days: number;
  fund_flow_stock_records: number;
  fund_flow_sector_records: number;
}

export async function fetchAuctionStocks(params?: {
  min_gap?: number; min_score?: number; sector?: string; limit?: number;
}): Promise<{ count: number; stocks: AuctionStock[] }> {
  const sp = new URLSearchParams();
  if (params?.min_gap) sp.set("min_gap", String(params.min_gap));
  if (params?.min_score) sp.set("min_score", String(params.min_score));
  if (params?.sector) sp.set("sector", params.sector);
  if (params?.limit) sp.set("limit", String(params.limit ?? 50));
  const qs = sp.toString();
  const res = await fetch(`${MONEY_FLOW_API}/api/v1/auction/stocks${qs ? `?${qs}` : ""}`, {
    next: { revalidate: 120 },
    signal: AbortSignal.timeout(10000),
  });
  if (!res.ok) return { count: 0, stocks: [] };
  return res.json();
}

export async function fetchAuctionSectors(params?: {
  limit?: number;
}): Promise<{ count: number; sectors: AuctionSector[] }> {
  const sp = new URLSearchParams();
  if (params?.limit) sp.set("limit", String(params.limit ?? 30));
  const qs = sp.toString();
  const res = await fetch(`${MONEY_FLOW_API}/api/v1/auction/sectors${qs ? `?${qs}` : ""}`, {
    next: { revalidate: 120 },
    signal: AbortSignal.timeout(10000),
  });
  if (!res.ok) return { count: 0, sectors: [] };
  return res.json();
}

export async function fetchFundFlowStocks(params?: {
  direction?: string; sector?: string; min_amount?: number; limit?: number;
}): Promise<{ count: number; stocks: FundFlowStock[] }> {
  const sp = new URLSearchParams();
  if (params?.direction) sp.set("direction", params.direction);
  if (params?.sector) sp.set("sector", params.sector);
  if (params?.min_amount) sp.set("min_amount", String(params.min_amount));
  if (params?.limit) sp.set("limit", String(params.limit ?? 50));
  const qs = sp.toString();
  const res = await fetch(`${MONEY_FLOW_API}/api/v1/fund-flow/stocks${qs ? `?${qs}` : ""}`, {
    next: { revalidate: 120 },
    signal: AbortSignal.timeout(10000),
  });
  if (!res.ok) return { count: 0, stocks: [] };
  return res.json();
}

export async function fetchFundFlowSectors(params?: {
  sector_type?: string; direction?: string; limit?: number;
}): Promise<{ count: number; sectors: FundFlowSector[] }> {
  const sp = new URLSearchParams();
  if (params?.sector_type) sp.set("sector_type", params.sector_type);
  if (params?.direction) sp.set("direction", params.direction ?? "all");
  if (params?.limit) sp.set("limit", String(params.limit ?? 30));
  const qs = sp.toString();
  const res = await fetch(`${MONEY_FLOW_API}/api/v1/fund-flow/sectors${qs ? `?${qs}` : ""}`, {
    next: { revalidate: 120 },
    signal: AbortSignal.timeout(10000),
  });
  if (!res.ok) return { count: 0, sectors: [] };
  return res.json();
}

export async function fetchMoneyFlowStats(): Promise<MoneyFlowStats | null> {
  const res = await fetch(`${MONEY_FLOW_API}/api/v1/stats`, {
    next: { revalidate: 300 },
    signal: AbortSignal.timeout(10000),
  });
  if (!res.ok) return null;
  return res.json();
}
