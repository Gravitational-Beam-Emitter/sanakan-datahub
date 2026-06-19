const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8001";
const ECO_API = process.env.NEXT_PUBLIC_ECO_API_URL || "http://127.0.0.1:8000";
const US_CORP_API = process.env.NEXT_PUBLIC_US_CORP_API_URL || "http://127.0.0.1:8002";

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
