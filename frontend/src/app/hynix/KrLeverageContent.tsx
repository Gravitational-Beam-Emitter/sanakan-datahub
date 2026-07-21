"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import * as echarts from "echarts";

const API = process.env.NEXT_PUBLIC_HYNIX_API_URL || "http://127.0.0.1:8008";

const MONO = "SF Mono, ui-monospace, SFMono-Regular, Menlo, Consolas, monospace";
const LIQ_BLOWUP = 10;

/* ── helpers ── */

function toEchartsData(ts: number[], arr: (number | null)[]): [number, number | null][] {
  return arr.map((v, i) => [ts[i], v] as [number, number | null]);
}

function fnum(v: number | null | undefined, dec = 0): string {
  if (v == null) return "—";
  return v.toLocaleString("en-US", {
    minimumFractionDigits: dec,
    maximumFractionDigits: dec,
  });
}

function fmtDate(yyyymmdd: string): string {
  return yyyymmdd.slice(0, 4) + "-" + yyyymmdd.slice(4, 6) + "-" + yyyymmdd.slice(6, 8);
}

/* ── Dark mode detection ── */

function useIsDark(): boolean {
  const [dark, setDark] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    setDark(mq.matches);
    const h = (e: MediaQueryListEvent) => setDark(e.matches);
    mq.addEventListener("change", h);
    return () => mq.removeEventListener("change", h);
  }, []);
  return dark;
}

/* ── Types ── */

interface DumpData {
  dates: string[];
  series: Record<string, (number | null)[]>;
  etf_series: Record<string, (number | null)[]>;
}

interface KPI {
  r2?: number; r2Pct?: number; fin?: number; finKospi?: number; finKosdaq?: number;
  dep?: number; col?: number; liq5d?: number; liqPct?: number; misu?: number;
  r1?: number; r1p?: number; r1q?: number; kospi?: number; spx?: number;
  mcap?: number; mg?: number; mgPct?: number; util?: number; capEq?: number; capQ?: string;
}

interface ETF_KPI {
  thermo?: number; thermoW?: number; aum?: number; aumInv?: number;
  n?: number; nInv?: number; cum?: number; cumW?: number;
  os?: { usdBn?: number; n?: number; items?: unknown[] };
}

interface SummaryData {
  generated: string; asof: string;
  range: { start: string; end: string; rows: number };
  latest_daily_date: string | null; latest_etf_date: string | null;
  kpi: KPI; etf_kpi: ETF_KPI;
  latest_daily: Record<string, number | null> | null;
}

/* ── Shared chart colors (light/dark) ── */

function colors(dark: boolean) {
  return dark ? {
    ink: "#f5f5f7", sub: "#a1a1a6", grey: "#98989d",
    blue: "#0a84ff", red: "#ff453a", wred: "#ffb3ab", green: "#30d158", orange: "#ff9f0a",
    soft: "rgba(255,255,255,.16)", faint: "rgba(255,255,255,.07)", card: "#1c1c1e",
    fillRed: "rgba(255,69,58,.20)", fillBlue: "rgba(10,132,255,.18)",
    areaBlue: "rgba(10,132,255,.32)", areaRed: "rgba(255,69,58,.30)",
    zoomFill: "rgba(10,132,255,.10)",
    bg: "#000000",
  } : {
    ink: "#1d1d1f", sub: "#6e6e73", grey: "#8e8e93",
    blue: "#007aff", red: "#ff3b30", wred: "#c40018", green: "#248a3d", orange: "#b25000",
    soft: "rgba(0,0,0,.14)", faint: "rgba(0,0,0,.06)", card: "#ffffff",
    fillRed: "rgba(255,59,48,.14)", fillBlue: "rgba(0,122,255,.12)",
    areaBlue: "rgba(0,122,255,.30)", areaRed: "rgba(255,59,48,.28)",
    zoomFill: "rgba(0,122,255,.08)",
    bg: "#f5f5f7",
  };
}

/* ── === MAIN COMPONENT === ── */

export default function KrLeverageContent() {
  const dark = useIsDark();
  const [dump, setDump] = useState<DumpData | null>(null);
  const [summary, setSummary] = useState<SummaryData | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    Promise.all([
      fetch(`${API}/api/v1/kr-leverage/dump`).then(r => r.ok ? r.json() : null),
      fetch(`${API}/api/v1/kr-leverage/summary`).then(r => r.ok ? r.json() : null),
    ])
      .then(([d, s]) => {
        setDump(d);
        setSummary(s);
        setReady(true);
      })
      .catch(() => setReady(true));
  }, []);

  if (!ready) {
    return <div className="flex items-center justify-center py-20"><div className="h-4 w-48 bg-surface-hover rounded animate-pulse" /></div>;
  }
  if (!dump || !summary) {
    return (
      <div className="glass rounded-xl p-8 text-center">
        <p className="text-sm text-muted">无法加载数据。请确认 <code className="font-mono bg-surface px-1 py-0.5 rounded">python -m hynix.kimpremium</code> 已运行。</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-8">
      <KpiAlerts kpi={summary.kpi} daily={summary.latest_daily} dark={dark} />
      <KpiCards summary={summary} dark={dark} />
      <ChartThermo dump={dump} dark={dark} />
      <ChartLiquidation dump={dump} dark={dark} />
      <ChartR2Panorama dump={dump} dark={dark} />
      <ChartMicroDecomposition dump={dump} dark={dark} />
      <ChartDeposits dump={dump} dark={dark} />
      <SourceNote summary={summary} />
    </div>
  );
}

/* ── Alerts ── */

function KpiAlerts({ kpi, daily, dark }: { kpi: KPI; daily: Record<string, number | null> | null; dark: boolean }) {
  const items: { tag: string; text: string }[] = [];
  if ((kpi.mgPct ?? 0) >= 95) items.push({ tag: "估值", text: `KOSPI市值/GDP = ${fnum(kpi.mg, 0)}%，处于近10年 ${Math.round(kpi.mgPct ?? 0)} 分位（≥95 昂贵）` });
  if ((kpi.liqPct ?? 0) >= 95) items.push({ tag: "强平", text: `强平金额 5日均 ${fnum(kpi.liq5d, 0)} 亿，近10年 ${Math.round(kpi.liqPct ?? 0)} 分位` });
  if (daily?.liqR != null && daily.liqR >= LIQ_BLOWUP) items.push({ tag: "爆仓", text: `最新交易日强平/未收比 ${fnum(daily.liqR, 1)}% ≥10%，为爆仓日` });
  if (!items.length) return null;
  const C = colors(dark);
  return (
    <div className="flex flex-col gap-2">
      {items.map((item, i) => (
        <div key={i} className="flex gap-3 items-start rounded-xl py-3 px-4 text-xs"
          style={{ background: C.fillRed, border: `1px solid color-mix(in srgb, ${C.red} 26%, transparent)` }}>
          <span className="font-mono text-[10px] font-bold tracking-widest shrink-0 mt-0.5" style={{ color: C.red }}>{item.tag}</span>
          <span style={{ color: C.ink }} dangerouslySetInnerHTML={{ __html: item.text.replace(/<b>/g, `<b style="font-family:${MONO};font-variant-numeric:tabular-nums">`) }} />
        </div>
      ))}
    </div>
  );
}

/* ── KPI Cards ── */

function KpiCards({ summary, dark }: { summary: SummaryData; dark: boolean }) {
  const C = colors(dark);
  const kpi = summary.kpi;
  const ekpi = summary.etf_kpi;
  const daily = summary.latest_daily;

  function pctChip(p: number | null | undefined) {
    if (p == null) return null;
    const bg = p >= 95 ? C.fillRed : p >= 80 ? "rgba(178,80,0,.13)" : p >= 50 ? C.faint : "rgba(36,138,61,.13)";
    const fg = p >= 95 ? C.red : p >= 80 ? C.orange : p >= 50 ? C.sub : C.green;
    return <span className="text-[10px] px-1.5 py-0.5 rounded-full font-mono" style={{ background: bg, color: fg }}>10y {Math.round(p)}%</span>;
  }

  const cards: { k: string; v: string; u: string; s: React.ReactNode; hot?: boolean }[] = [];

  if (ekpi?.thermo != null) {
    cards.push({ k: "杠杆温度计", v: fnum(ekpi.thermo, 2), u: "%", s: `R2 +${fnum((ekpi.thermo ?? 0) - (kpi.r2 ?? 0), 1)}pp · 锚2024`, hot: true });
  }
  cards.push({ k: "强平金额 5日均", v: fnum(kpi.liq5d, 0), u: " 亿", s: pctChip(kpi.liqPct), hot: (kpi.liqPct ?? 0) >= 95 });
  const liqR = daily?.liqR;
  cards.push({
    k: "强平/未收比（最新）", v: liqR != null ? fnum(liqR, 1) : "—", u: "%",
    s: liqR != null && liqR >= LIQ_BLOWUP
      ? <span className="text-[10px] px-1.5 py-0.5 rounded-full font-mono" style={{ background: C.fillRed, color: C.red }}>≥10% 爆仓日</span>
      : "爆仓阈 10%",
    hot: liqR != null && liqR >= LIQ_BLOWUP,
  });
  cards.push({ k: "估值 KOSPI市值/GDP", v: fnum(kpi.mg, 0), u: "%", s: pctChip(kpi.mgPct), hot: (kpi.mgPct ?? 0) >= 95 });
  cards.push({ k: "R2 融资/存管金", v: fnum(daily?.r2, 2), u: "%", s: pctChip(daily?.r2_10y_pct) });
  if (kpi.util != null) {
    cards.push({ k: "信用供与/券商自本", v: fnum(kpi.util, 1), u: "%", s: `自本 ${fnum(kpi.capEq, 0)} 万亿 · 法定上限 100%`, hot: kpi.util >= 90 });
  }
  cards.push({ k: "信用融资余额", v: fnum(kpi.fin, 2), u: " 万亿", s: `KOSPI ${fnum(kpi.finKospi, 1)} · KOSDAQ ${fnum(kpi.finKosdaq, 1)}` });
  cards.push({ k: "投资者存管金", v: fnum(kpi.dep, 1), u: " 万亿", s: `KOSPI ${fnum(kpi.kospi, 0)} / SPX ${fnum(kpi.spx, 0)}` });
  if (ekpi?.aum != null) {
    const dom = (ekpi.aum ?? 0) + (ekpi.aumInv ?? 0);
    const osB = ekpi.os?.usdBn;
    cards.push({ k: "杠杆ETF规模", v: fnum(dom, 1), u: " 万亿", s: `${osB != null ? `+ $${fnum(osB, 1)}bn 海外(top50·下限)` : "海外接入中"} · ${ekpi.n}+反向${ekpi.nInv}只` });
  }

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
      {cards.map((card, i) => (
        <div key={i} className="rounded-xl p-3.5" style={{
          background: C.card, border: card.hot ? `1px solid color-mix(in srgb, ${C.red} 40%, transparent)` : "1px solid var(--border-color, rgba(0,0,0,.10))",
        }}>
          <div className="text-[11px] font-medium flex items-center gap-1.5" style={{ color: C.sub }}>
            {card.hot && <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: C.red }} />}
            {card.k}
          </div>
          <div className="text-xl font-bold font-mono mt-1" style={{ color: card.hot ? C.red : C.ink }}>
            {card.v}<span className="text-[11px] font-normal ml-0.5" style={{ color: C.grey }}>{card.u}</span>
          </div>
          <div className="text-[10px] font-mono mt-1.5 leading-relaxed" style={{ color: C.grey }}>{card.s}</div>
        </div>
      ))}
    </div>
  );
}

/* ── Source note ── */

function SourceNote({ summary }: { summary: SummaryData }) {
  return (
    <div className="glass rounded-xl p-4 text-xs text-muted leading-relaxed">
      <b className="text-ink">数据源</b>：KOFIA FreeSIS（信用/资金/市值）· KSD SEIBro（ETF申赎、海外托管TOP50）· Naver（标普500）— 日频官方数据。
      覆盖 {summary.range.start}–{summary.range.end}，{summary.range.rows.toLocaleString()} 个交易日。
      生成 {summary.generated}。研究性可视化，非投资建议。
    </div>
  );
}

/* ══════════════════════════════════════════════════════════
   ① 杠杆温度计
   ══════════════════════════════════════════════════════════ */

function ChartThermo({ dump, dark }: { dump: DumpData; dark: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const C = colors(dark);

  useEffect(() => {
    if (!ref.current) return;
    const etf = dump.etf_series;
    const etfDates = (dump as any).etf_dates || dump.dates.slice(-620);
    const d = dump.dates;
    // ETF data may be shorter; align by matching dates from daily series
    const n = dump.dates.length;
    const etfN = (etfDates?.length) || (etf.thermo?.length || 0);

    const toTs = (ds: string) => new Date(ds).getTime();
    const etfTS = (etfDates?.length ? etfDates : dump.dates.slice(-etfN)).map(toTs);

    const black = etf.r2 || [];
    const red = etf.thermo || [];
    const wgt = etf.thermoW || [];
    const flow = etf.flow || [];

    // Base area between r2 and thermo
    const base: (number | null)[] = [], pos: (number | null)[] = [], neg: (number | null)[] = [];
    const flowP: (number | null)[] = [], flowN: (number | null)[] = [];
    for (let i = 0; i < etfTS.length; i++) {
      const b = black[i], r = red[i];
      if (b == null || r == null) { base.push(null); pos.push(null); neg.push(null); }
      else {
        base.push(Math.min(b, r));
        pos.push(r > b ? +(r - b).toFixed(3) : 0);
        neg.push(b > r ? +(b - r).toFixed(3) : 0);
      }
      const f = flow[i];
      flowP.push(f != null && f >= 0 ? f : null);
      flowN.push(f != null && f < 0 ? f : null);
    }

    // Check if thermoW diverges from thermo
    let wShow = false;
    for (let w = 0; w < etfTS.length; w++) {
      if (wgt[w] != null && red[w] != null && Math.abs((wgt[w] ?? 0) - (red[w] ?? 0)) > 0.3) { wShow = true; break; }
    }

    const chart = echarts.init(ref.current, null, { renderer: "canvas" });
    const opt: any = {
      animation: false,
      textStyle: { fontFamily: MONO },
      axisPointer: { link: [{ xAxisIndex: "all" }] },
      tooltip: {
        trigger: "axis",
        backgroundColor: C.card,
        borderColor: C.soft,
        textStyle: { color: C.ink, fontFamily: MONO, fontSize: 11 },
        formatter: (ps: any) => {
          if (!ps?.length) return "";
          const i = ps[0].dataIndex;
          const dStr = etfDates?.[i] ? fmtDate(etfDates[i]) : "";
          let out = [`<b>${dStr}</b>`];
          if (black[i] != null) out.push(`<span style="color:${C.ink}">■</span> R2 显性融资 <b>${fnum(black[i], 2)}%</b>`);
          if (red[i] != null) out.push(`<span style="color:${C.red}">■</span> 杠杆温度计 <b>${fnum(red[i], 2)}%</b> (Δ${fnum((red[i] ?? 0) - (black[i] ?? 0), 2)}pp)`);
          if (wShow && wgt[i] != null) out.push(`<span style="color:${C.wred}">■</span> 借款加权 <b>${fnum(wgt[i], 2)}%</b>`);
          if (flow[i] != null) out.push(`<span style="color:${C.sub}">当日净申赎 ${fnum(flow[i], 0)} 亿</span>`);
          return out.join("<br>");
        },
      },
      legend: {
        top: 0, right: 8, icon: "rect", itemWidth: 12, itemHeight: 3,
        textStyle: { color: C.sub, fontFamily: MONO, fontSize: 11 },
        data: ["R2 显性融资", "杠杆温度计"].concat(wShow ? ["借款加权(倍数−1)"] : []).concat(["每日净申赎"]),
      },
      grid: [
        { left: 58, right: 52, top: 28, height: "48%" },
        { left: 58, right: 52, top: "66%", height: "19%" },
      ],
      xAxis: [
        { type: "time", gridIndex: 0, min: etfTS[0], max: etfTS[etfTS.length - 1], axisLine: { lineStyle: { color: C.soft } }, axisTick: { show: false }, axisLabel: { show: false }, splitLine: { show: false } },
        { type: "time", gridIndex: 1, min: etfTS[0], max: etfTS[etfTS.length - 1], axisLine: { lineStyle: { color: C.soft } }, axisTick: { show: false }, axisLabel: { color: C.sub, fontFamily: MONO, fontSize: 10 }, splitLine: { show: false } },
      ],
      yAxis: [
        { type: "value", gridIndex: 0, name: "%", nameTextStyle: { color: C.sub }, axisLine: { lineStyle: { color: C.soft } }, axisLabel: { color: C.sub, fontFamily: MONO, fontSize: 10 }, splitLine: { lineStyle: { color: C.faint } } },
        { type: "value", gridIndex: 1, name: "亿/日", nameTextStyle: { color: C.sub }, axisLine: { lineStyle: { color: C.soft } }, axisLabel: { color: C.sub, fontFamily: MONO, fontSize: 10 }, splitLine: { lineStyle: { color: C.faint } } },
      ],
      dataZoom: [
        { type: "inside", xAxisIndex: [0, 1] },
        { type: "slider", xAxisIndex: [0, 1], bottom: 6, height: 20, borderColor: C.soft, fillerColor: C.zoomFill, handleStyle: { color: C.card, borderColor: C.sub }, dataBackground: { lineStyle: { color: C.soft }, areaStyle: { color: C.faint } }, textStyle: { color: C.sub, fontFamily: MONO, fontSize: 10 } },
      ],
      series: [
        { name: "_base", type: "line", xAxisIndex: 0, yAxisIndex: 0, data: toEchartsData(etfTS, base), stack: "dv", showSymbol: false, lineStyle: { width: 0, opacity: 0 }, itemStyle: { opacity: 0 }, silent: true },
        { name: "_pos", type: "line", xAxisIndex: 0, yAxisIndex: 0, data: toEchartsData(etfTS, pos), stack: "dv", showSymbol: false, lineStyle: { width: 0, opacity: 0 }, areaStyle: { color: C.fillRed }, silent: true },
        { name: "_neg", type: "line", xAxisIndex: 0, yAxisIndex: 0, data: toEchartsData(etfTS, neg), stack: "dv", showSymbol: false, lineStyle: { width: 0, opacity: 0 }, areaStyle: { color: C.fillBlue }, silent: true },
        {
          name: "R2 显性融资", type: "line", xAxisIndex: 0, yAxisIndex: 0, data: toEchartsData(etfTS, black),
          showSymbol: false, lineStyle: { color: C.ink, width: 1.6 }, itemStyle: { color: C.ink },
          endLabel: { show: true, formatter: "R2", color: C.ink, fontFamily: MONO, fontSize: 10, offset: [4, 10] },
        },
        {
          name: "杠杆温度计", type: "line", xAxisIndex: 0, yAxisIndex: 0, data: toEchartsData(etfTS, red),
          showSymbol: false, lineStyle: { color: C.red, width: 1.7 }, itemStyle: { color: C.red },
          endLabel: { show: true, formatter: (p: any) => p.value?.[1] != null ? `${fnum(p.value[1], 1)}%` : "", color: C.red, fontFamily: MONO, fontSize: 10.5, offset: [4, 0] },
        },
        { name: "每日净申赎", type: "bar", xAxisIndex: 1, yAxisIndex: 1, data: toEchartsData(etfTS, flowP), large: true, itemStyle: { color: C.red } },
        { name: "_flowN", type: "bar", xAxisIndex: 1, yAxisIndex: 1, data: toEchartsData(etfTS, flowN), large: true, barGap: "-100%", itemStyle: { color: C.blue } },
      ],
    };
    if (wShow) {
      opt.series.push({
        name: "借款加权(倍数−1)", type: "line", xAxisIndex: 0, yAxisIndex: 0, data: toEchartsData(etfTS, wgt),
        showSymbol: false, lineStyle: { color: C.wred, width: 1.5, type: [6, 3] as const }, itemStyle: { color: C.wred },
        endLabel: { show: true, formatter: (p: any) => p.value?.[1] != null ? `${fnum(p.value[1], 1)}%` : "", color: C.wred, fontFamily: MONO, fontSize: 10.5, offset: [4, -12] },
      });
    }
    chart.setOption(opt);

    const handleResize = () => chart.resize();
    window.addEventListener("resize", handleResize);
    return () => { window.removeEventListener("resize", handleResize); chart.dispose(); };
  }, [dump, dark]);

  return (
    <section>
      <h2 className="text-xl font-bold tracking-tight mb-1" style={{ color: colors(dark).ink }}>① 杠杆温度计（锚定 2024）</h2>
      <p className="text-[13px] mb-3" style={{ color: colors(dark).sub }}>杠杆温度计 = (融资余额 + 杠杆ETF累计净申赎) / 存管金 · 对照线为显性融资 R2 · 下格 = 每日净申赎（红申购 / 蓝赎回）</p>
      <div ref={ref} style={{ height: 560, background: colors(dark).card, border: `1px solid ${colors(dark).soft}`, borderRadius: 18, padding: "14px 12px 8px" }} />
    </section>
  );
}

/* ══════════════════════════════════════════════════════════
   ② 强平·爆仓监控
   ══════════════════════════════════════════════════════════ */

function ChartLiquidation({ dump, dark }: { dump: DumpData; dark: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const C = colors(dark);
  const [years, setYears] = useState("all");
  const [dailyView, setDailyView] = useState<"recent" | "blow">("recent");

  const TS = dump.dates.map(d => new Date(d).getTime());
  const { liq, liqR, misu, fin, dep, r2, p10 } = dump.series;
  const n = TS.length;

  // 5-day MA of liq
  const ma: (number | null)[] = new Array(n).fill(null);
  const buf: number[] = [];
  for (let i = 0; i < n; i++) {
    if (liq[i] != null) { buf.push(liq[i]!); if (buf.length > 5) buf.shift(); }
    ma[i] = buf.length ? +(buf.reduce((a, b) => a + b, 0) / buf.length).toFixed(1) : null;
  }

  // Split into blowup / normal
  const liqN: (number | null)[] = new Array(n).fill(null);
  const liqB: (number | null)[] = new Array(n).fill(null);
  const blowScatter: [number, number][] = [];
  for (let i = 0; i < n; i++) {
    if (liq[i] == null) continue;
    if (liqR[i] != null && liqR[i]! >= LIQ_BLOWUP) {
      liqB[i] = liq[i];
      blowScatter.push([TS[i], liqR[i]!]);
    } else {
      liqN[i] = liq[i];
    }
  }

  // Window
  const setRange = useCallback((y: string) => {
    setYears(y);
    if (!ref.current) return;
    const chart = echarts.getInstanceByDom(ref.current);
    if (!chart) return;
    let startIdx = 0;
    if (y !== "all") {
      const end = dump.dates[dump.dates.length - 1];
      const target = (parseInt(end.slice(0, 4)) - parseInt(y)) + end.slice(4);
      for (let i = 0; i < dump.dates.length; i++) {
        if (dump.dates[i] >= target) { startIdx = i; break; }
      }
    }
    chart.dispatchAction({ type: "dataZoom", startValue: TS[startIdx], endValue: TS[TS.length - 1] });
  }, [TS, dump.dates]);

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current, null, { renderer: "canvas" });
    chart.setOption({
      animation: false,
      textStyle: { fontFamily: MONO },
      axisPointer: { link: [{ xAxisIndex: "all" }] },
      tooltip: {
        trigger: "axis",
        backgroundColor: C.card, borderColor: C.soft,
        textStyle: { color: C.ink, fontFamily: MONO, fontSize: 11 },
        formatter: (ps: any) => {
          if (!ps?.length) return "";
          const i = ps[0].dataIndex;
          const isBlow = liqR[i] != null && liqR[i]! >= LIQ_BLOWUP;
          let out = [`<b>${fmtDate(dump.dates[i])}</b>${isBlow ? ` <span style="color:${C.red};font-weight:700">爆仓日</span>` : ""}`];
          if (liq[i] != null) out.push(`<span style="color:${isBlow ? C.red : C.grey}">■</span> 强平金额 <b>${fnum(liq[i], 0)} 亿</b>（5日均 ${fnum(ma[i], 0)}）`);
          if (liqR[i] != null) out.push(`<span style="color:${C.ink}">■</span> 强平/未收 <b>${fnum(liqR[i], 1)}%</b>`);
          if (misu[i] != null) out.push(`<span style="color:${C.sub}">委托未收金 ${fnum(misu[i], 3)} 万亿</span>`);
          return out.join("<br>");
        },
      },
      legend: {
        top: 0, right: 8, icon: "rect", itemWidth: 12, itemHeight: 3,
        textStyle: { color: C.sub, fontFamily: MONO, fontSize: 11 },
        data: ["强平(日)", "爆仓日 ≥10%", "5日均", "强平/未收 %"],
      },
      grid: [
        { left: 58, right: 30, top: 28, height: "42%" },
        { left: 58, right: 30, top: "60%", height: "24%" },
      ],
      xAxis: [
        { type: "time", gridIndex: 0, min: TS[0], max: TS[TS.length - 1], axisLine: { lineStyle: { color: C.soft } }, axisTick: { show: false }, axisLabel: { show: false }, splitLine: { show: false } },
        { type: "time", gridIndex: 1, min: TS[0], max: TS[TS.length - 1], axisLine: { lineStyle: { color: C.soft } }, axisTick: { show: false }, axisLabel: { color: C.sub, fontFamily: MONO, fontSize: 10 }, splitLine: { show: false } },
      ],
      yAxis: [
        { type: "value", gridIndex: 0, name: "亿", nameTextStyle: { color: C.sub }, axisLine: { lineStyle: { color: C.soft } }, axisLabel: { color: C.sub, fontFamily: MONO, fontSize: 10 }, splitLine: { lineStyle: { color: C.faint } } },
        { type: "value", gridIndex: 1, name: "%", max: (v: any) => Math.max(12, Math.ceil((v.max || 0) + 1)), nameTextStyle: { color: C.sub }, axisLine: { lineStyle: { color: C.soft } }, axisLabel: { color: C.sub, fontFamily: MONO, fontSize: 10 }, splitLine: { lineStyle: { color: C.faint } } },
      ],
      dataZoom: [
        { type: "inside", xAxisIndex: [0, 1] },
        { type: "slider", xAxisIndex: [0, 1], bottom: 6, height: 20, borderColor: C.soft, fillerColor: C.zoomFill, handleStyle: { color: C.card, borderColor: C.sub }, dataBackground: { lineStyle: { color: C.soft }, areaStyle: { color: C.faint } }, textStyle: { color: C.sub, fontFamily: MONO, fontSize: 10 } },
      ],
      series: [
        { name: "强平(日)", type: "bar", xAxisIndex: 0, yAxisIndex: 0, data: toEchartsData(TS, liqN), large: true, itemStyle: { color: C.grey } },
        { name: "爆仓日 ≥10%", type: "bar", xAxisIndex: 0, yAxisIndex: 0, data: toEchartsData(TS, liqB), large: true, barGap: "-100%", itemStyle: { color: C.red } },
        { name: "5日均", type: "line", xAxisIndex: 0, yAxisIndex: 0, data: toEchartsData(TS, ma), showSymbol: false, lineStyle: { color: C.ink, width: 1.2 }, itemStyle: { color: C.ink } },
        {
          name: "强平/未收 %", type: "line", xAxisIndex: 1, yAxisIndex: 1, data: toEchartsData(TS, liqR), showSymbol: false, lineStyle: { color: C.ink, width: 1.2 }, itemStyle: { color: C.ink },
          markLine: { silent: true, symbol: "none", label: { show: true, position: "insideEndTop", color: C.red, fontFamily: MONO, fontSize: 10, formatter: "10% 爆仓阈" }, lineStyle: { color: C.red, type: "dashed", width: 1 }, data: [{ yAxis: LIQ_BLOWUP }] },
        },
        { name: "_blow", type: "scatter", xAxisIndex: 1, yAxisIndex: 1, data: blowScatter, symbolSize: 5, itemStyle: { color: C.red }, silent: true, tooltip: { show: false } },
      ],
    });

    const handleResize = () => chart.resize();
    window.addEventListener("resize", handleResize);
    return () => { window.removeEventListener("resize", handleResize); chart.dispose(); };
  }, [dump, dark]);

  // Blowup days for note
  const blowRecent: string[] = [];
  for (let k = n - 1; k >= 0 && blowRecent.length < 6; k--) {
    if (liqR[k] != null && liqR[k]! >= LIQ_BLOWUP) blowRecent.push(fmtDate(dump.dates[k]) + ` (${fnum(liqR[k], 1)}% · ${fnum(liq[k], 0)}亿)`);
  }

  // Daily detail table
  let blowCount = 0;
  for (let i = 0; i < n; i++) if (liqR[i] != null && liqR[i]! >= LIQ_BLOWUP) blowCount++;
  const detailIdx: number[] = [];
  if (dailyView === "recent") {
    for (let a = n - 1; a >= Math.max(0, n - 250); a--) detailIdx.push(a);
  } else {
    for (let b = n - 1; b >= 0; b--) if (liqR[b] != null && liqR[b]! >= LIQ_BLOWUP) detailIdx.push(b);
  }

  return (
    <section>
      <h2 className="text-xl font-bold tracking-tight mb-1" style={{ color: C.ink }}>② 强平·爆仓监控（Forced Liquidation）</h2>
      <p className="text-[13px] mb-3" style={{ color: C.sub }}>每日强平金额（<span style={{ color: C.red }}>红柱=爆仓日</span>，强平/未收 ≥10%）· 下格 = 强平/未收比</p>

      {/* Window buttons */}
      <div className="flex flex-wrap gap-0.5 items-center mb-2 px-3 py-1 rounded-xl" style={{ background: "var(--fill, rgba(120,120,128,.14))" }}>
        <span className="text-[11px] font-mono mr-1" style={{ color: C.grey, padding: "0 8px 0 2px" }}>窗口</span>
        {["all", "10", "5", "3", "1"].map(y => (
          <button key={y} onClick={() => setRange(y)}
            className={`text-xs font-mono px-3 py-1 rounded-lg transition ${years === y ? "font-semibold" : ""}`}
            style={{ color: years === y ? C.ink : C.sub, background: years === y ? C.card : "transparent", boxShadow: years === y ? "0 1px 4px rgba(0,0,0,.14)" : "none" }}>
            {y === "all" ? "全部" : y + "年"}
          </button>
        ))}
      </div>

      <div ref={ref} style={{ height: 440, background: C.card, border: `1px solid ${C.soft}`, borderRadius: 18, padding: "14px 12px 8px" }} />

      <p className="text-[12px] mt-3 px-4 py-3 rounded-xl leading-relaxed" style={{ background: C.faint, color: C.sub }}>
        <b style={{ color: C.ink }}>口径</b>：强平 = 券商强制平仓卖出金额（亿韩元），2006.04 起；比值 ≥{LIQ_BLOWUP}% 记为<b style={{ color: C.red }}>爆仓日</b>。近期：{blowRecent.length ? blowRecent.join(" · ") : "无"}。
      </p>

      {/* Daily detail table (collapsible) */}
      <details className="mt-3 rounded-xl" style={{ background: C.card, border: `1px solid ${C.soft}`, padding: "0 16px" }}>
        <summary className="cursor-pointer py-3.5 text-[13px] font-semibold flex items-center gap-2" style={{ color: C.sub }}>
          <span className="text-[11px]" style={{ color: C.grey }}>▶</span>
          逐日明细 — 近一年逐日 + 爆仓日全录（{blowCount} 天）· 点击展开
        </summary>
        <div className="pb-4">
          <div className="flex gap-0.5 items-center mb-3 px-3 py-1 rounded-xl" style={{ background: "var(--fill, rgba(120,120,128,.14))" }}>
            {(["recent", "blow"] as const).map(v => (
              <button key={v} onClick={() => setDailyView(v)}
                className={`text-xs font-mono px-3 py-1 rounded-lg transition ${dailyView === v ? "font-semibold" : ""}`}
                style={{ color: dailyView === v ? C.ink : C.sub, background: dailyView === v ? C.card : "transparent", boxShadow: dailyView === v ? "0 1px 4px rgba(0,0,0,.14)" : "none" }}>
                {v === "recent" ? "近一年逐日" : "爆仓日全录"}
              </button>
            ))}
          </div>
          <div className="overflow-auto max-h-96 rounded-xl" style={{ border: `1px solid ${C.soft}` }}>
            <table className="w-full text-[11.5px] font-mono whitespace-nowrap" style={{ fontVariantNumeric: "tabular-nums" }}>
              <thead>
                <tr>
                  {["日期", "强平 亿", "强平/未收 %", "未收金 万亿", "融资合计 万亿", "存管金 万亿", "R2 %", "10y分位"].map(h => (
                    <th key={h} className="text-right py-2 px-3 font-medium sticky top-0 z-10" style={{ background: colors(dark).bg, color: C.sub, borderBottom: `1px solid ${C.soft}` }}>
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {detailIdx.map(i => {
                  const isBlow = liqR[i] != null && liqR[i]! >= LIQ_BLOWUP;
                  return (
                    <tr key={i} className="hover:brightness-95" style={{ background: isBlow ? C.fillRed : "transparent", borderBottom: `1px solid ${C.faint}` }}>
                      <td className="py-1.5 px-3 text-left" style={{ color: C.ink }}>
                        {fmtDate(dump.dates[i])}
                        {isBlow && <span className="ml-1.5 text-[10px] px-1.5 py-0.5 rounded-full font-mono font-semibold" style={{ background: C.fillRed, color: C.red }}>爆仓</span>}
                      </td>
                      <td className="py-1.5 px-3 text-right" style={{ color: C.ink }}>{fnum(liq[i], 0)}</td>
                      <td className="py-1.5 px-3 text-right" style={{ color: isBlow ? C.red : C.ink, fontWeight: isBlow ? 700 : 400 }}>{fnum(liqR[i], 1)}</td>
                      <td className="py-1.5 px-3 text-right" style={{ color: C.ink }}>{fnum(misu[i], 3)}</td>
                      <td className="py-1.5 px-3 text-right" style={{ color: C.ink }}>{fnum(fin[i], 2)}</td>
                      <td className="py-1.5 px-3 text-right" style={{ color: C.ink }}>{fnum(dep[i], 1)}</td>
                      <td className="py-1.5 px-3 text-right" style={{ color: C.ink }}>{fnum(r2[i], 2)}</td>
                      <td className="py-1.5 px-3 text-right" style={{ color: C.ink }}>{fnum(p10[i], 1)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </details>
    </section>
  );
}

/* ══════════════════════════════════════════════════════════
   ③ 融资率全景 — R2 · 10年分位 · 指数
   ══════════════════════════════════════════════════════════ */

function ChartR2Panorama({ dump, dark }: { dump: DumpData; dark: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const C = colors(dark);
  const [years, setYears] = useState("10");

  const TS = dump.dates.map(d => new Date(d).getTime());
  const { r2, p10, kospi, kosdaq, spx, fin, dep } = dump.series;

  const setRange = useCallback((y: string) => {
    setYears(y);
    if (!ref.current) return;
    const chart = echarts.getInstanceByDom(ref.current);
    if (!chart) return;
    let startIdx = 0;
    if (y !== "all") {
      const end = dump.dates[dump.dates.length - 1];
      const target = (parseInt(end.slice(0, 4)) - parseInt(y)) + end.slice(4);
      for (let i = 0; i < dump.dates.length; i++) {
        if (dump.dates[i] >= target) { startIdx = i; break; }
      }
    }
    chart.dispatchAction({ type: "dataZoom", startValue: TS[startIdx], endValue: TS[TS.length - 1] });
  }, [TS, dump.dates]);

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current, null, { renderer: "canvas" });
    chart.setOption({
      animation: false,
      textStyle: { fontFamily: MONO },
      axisPointer: { link: [{ xAxisIndex: "all" }] },
      tooltip: {
        trigger: "axis",
        backgroundColor: C.card, borderColor: C.soft,
        textStyle: { color: C.ink, fontFamily: MONO, fontSize: 11 },
        formatter: (ps: any) => {
          if (!ps?.length) return "";
          const i = ps[0].dataIndex;
          let out = [`<b>${fmtDate(dump.dates[i])}</b>`];
          const seen: Record<string, boolean> = {};
          ps.forEach((p: any) => {
            if (seen[p.seriesName] || p.value?.[1] == null || p.seriesName.startsWith("_")) return;
            seen[p.seriesName] = true;
            const dec = p.seriesName === "R2" ? 2 : 1;
            const u = p.seriesName === "R2" ? "%" : "";
            out.push(`${p.marker} ${p.seriesName} <b>${fnum(p.value[1], dec)}${u}</b>`);
          });
          out.push(`<span style="color:${C.sub}">融资 ${fnum(fin[i], 2)} / 存管 ${fnum(dep[i], 1)} 万亿</span>`);
          return out.join("<br>");
        },
      },
      legend: {
        top: 0, right: 8, icon: "rect", itemWidth: 12, itemHeight: 3,
        textStyle: { color: C.sub, fontFamily: MONO, fontSize: 11 },
        data: ["R2", "10y分位", "KOSPI", "KOSDAQ", "S&P500"],
        selected: { KOSDAQ: false },
      },
      grid: [
        { left: 58, right: 78, top: 26, height: "33%" },
        { left: 58, right: 78, top: "44%", height: "17%" },
        { left: 58, right: 78, top: "67%", height: "19%" },
      ],
      xAxis: [
        { type: "time", gridIndex: 0, min: TS[0], max: TS[TS.length - 1], axisLine: { lineStyle: { color: C.soft } }, axisTick: { show: false }, axisLabel: { show: false }, splitLine: { show: false } },
        { type: "time", gridIndex: 1, min: TS[0], max: TS[TS.length - 1], axisLine: { lineStyle: { color: C.soft } }, axisTick: { show: false }, axisLabel: { show: false }, splitLine: { show: false } },
        { type: "time", gridIndex: 2, min: TS[0], max: TS[TS.length - 1], axisLine: { lineStyle: { color: C.soft } }, axisTick: { show: false }, axisLabel: { color: C.sub, fontFamily: MONO, fontSize: 10 }, splitLine: { show: false } },
      ],
      yAxis: [
        { type: "value", gridIndex: 0, name: "R2 %", nameTextStyle: { color: C.sub }, axisLine: { lineStyle: { color: C.soft } }, axisLabel: { color: C.sub, fontFamily: MONO, fontSize: 10 }, splitLine: { lineStyle: { color: C.faint } } },
        { type: "value", gridIndex: 1, min: 0, max: 100, interval: 25, name: "10y %ile", nameTextStyle: { color: C.sub }, axisLine: { lineStyle: { color: C.soft } }, axisLabel: { color: C.sub, fontFamily: MONO, fontSize: 10 }, splitLine: { lineStyle: { color: C.faint } } },
        { type: "log", gridIndex: 2, name: "指数(对数)", nameTextStyle: { color: C.sub }, axisLine: { lineStyle: { color: C.soft } }, axisLabel: { color: C.sub, fontFamily: MONO, fontSize: 10, formatter: (v: number) => v >= 1000 ? Math.round(v / 100) / 10 + "k" : String(Math.round(v)) }, splitLine: { show: false } },
      ],
      dataZoom: [
        { type: "inside", xAxisIndex: [0, 1, 2] },
        { type: "slider", xAxisIndex: [0, 1, 2], bottom: 6, height: 22, borderColor: C.soft, fillerColor: C.zoomFill, handleStyle: { color: C.card, borderColor: C.sub }, dataBackground: { lineStyle: { color: C.soft }, areaStyle: { color: C.faint } }, textStyle: { color: C.sub, fontFamily: MONO, fontSize: 10 } },
      ],
      series: [
        {
          name: "R2", type: "line", xAxisIndex: 0, yAxisIndex: 0, data: toEchartsData(TS, r2),
          showSymbol: false, lineStyle: { color: C.ink, width: 1.6 }, itemStyle: { color: C.ink },
          endLabel: { show: true, formatter: (p: any) => p.value?.[1] != null ? `R2 ${fnum(p.value[1], 1)}%` : "", color: C.ink, fontFamily: MONO, fontSize: 10.5, offset: [4, 0] },
        },
        {
          name: "10y分位", type: "line", xAxisIndex: 1, yAxisIndex: 1, data: toEchartsData(TS, p10),
          showSymbol: false, lineStyle: { color: C.red, width: 1.4 }, itemStyle: { color: C.red },
          markLine: { silent: true, symbol: "none", label: { show: true, position: "insideEndTop", color: C.red, fontFamily: MONO, fontSize: 10, formatter: "95" }, lineStyle: { color: C.red, type: "dashed", width: 1, opacity: 0.7 }, data: [{ yAxis: 95 }] },
          markArea: {
            silent: true,
            data: [
              [{ yAxis: 80, itemStyle: { color: dark ? "rgba(255,159,10,.10)" : "rgba(178,80,0,.07)" } }, { yAxis: 95 }],
              [{ yAxis: 95, itemStyle: { color: dark ? "rgba(255,69,58,.14)" : "rgba(255,59,48,.09)" } }, { yAxis: 100 }],
            ],
          },
        },
        { name: "KOSPI", type: "line", xAxisIndex: 2, yAxisIndex: 2, data: toEchartsData(TS, kospi), showSymbol: false, lineStyle: { color: C.grey, width: 1.3 }, itemStyle: { color: C.grey }, endLabel: { show: true, formatter: "KOSPI", color: C.grey, fontFamily: MONO, fontSize: 10, offset: [4, -6] } },
        { name: "KOSDAQ", type: "line", xAxisIndex: 2, yAxisIndex: 2, data: toEchartsData(TS, kosdaq), showSymbol: false, lineStyle: { color: C.grey, width: 1.1, type: "dashed" }, itemStyle: { color: C.grey }, endLabel: { show: true, formatter: "KOSDAQ", color: C.grey, fontFamily: MONO, fontSize: 10, offset: [4, 8] } },
        { name: "S&P500", type: "line", xAxisIndex: 2, yAxisIndex: 2, data: toEchartsData(TS, spx), showSymbol: false, connectNulls: false, lineStyle: { color: C.blue, width: 1.3 }, itemStyle: { color: C.blue }, endLabel: { show: true, formatter: "SPX", color: C.blue, fontFamily: MONO, fontSize: 10, offset: [4, 6] } },
      ],
    });

    // Apply initial range
    setTimeout(() => setRange("10"), 50);

    const handleResize = () => chart.resize();
    window.addEventListener("resize", handleResize);
    return () => { window.removeEventListener("resize", handleResize); chart.dispose(); };
  }, [dump, dark]);

  return (
    <section>
      <h2 className="text-xl font-bold tracking-tight mb-1" style={{ color: C.ink }}>③ 融资率全景 — R2 · 10年分位 · 指数（附图）</h2>
      <p className="text-[13px] mb-3" style={{ color: C.sub }}>R2 · 10年滚动分位 · KOSPI/标普500（对数）— 1998 至今，三格联动缩放</p>

      <div className="flex flex-wrap gap-0.5 items-center mb-2 px-3 py-1 rounded-xl" style={{ background: "var(--fill, rgba(120,120,128,.14))" }}>
        <span className="text-[11px] font-mono mr-1" style={{ color: C.grey, padding: "0 8px 0 2px" }}>窗口</span>
        {[
          { y: "all", label: "全部" },
          { y: "20", label: "20年" },
          { y: "10", label: "10年" },
          { y: "5", label: "5年" },
          { y: "3", label: "3年" },
          { y: "1", label: "1年" },
        ].map(({ y, label }) => (
          <button key={y} onClick={() => setRange(y)}
            className={`text-xs font-mono px-3 py-1 rounded-lg transition ${years === y ? "font-semibold" : ""}`}
            style={{ color: years === y ? C.ink : C.sub, background: years === y ? C.card : "transparent", boxShadow: years === y ? "0 1px 4px rgba(0,0,0,.14)" : "none" }}>
            {label}
          </button>
        ))}
      </div>

      <div ref={ref} style={{ height: 620, background: C.card, border: `1px solid ${C.soft}`, borderRadius: 18, padding: "14px 12px 8px" }} />
    </section>
  );
}

/* ══════════════════════════════════════════════════════════
   ④ 微观分解（附图）
   ══════════════════════════════════════════════════════════ */

function ChartMicroDecomposition({ dump, dark }: { dump: DumpData; dark: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const C = colors(dark);
  const [tab, setTab] = useState("comp");

  const TS = dump.dates.map(d => new Date(d).getTime());
  const { finKospi, finKosdaq, col, dep, derivDep, rp, r1q, r1p, r1, util, mg } = dump.series;

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current, null, { renderer: "canvas" });

    type OptFn = (tab: string) => any;
    const makeOption: OptFn = (t) => {
      const base: any = {
        animation: false,
        textStyle: { fontFamily: MONO },
        tooltip: {
          trigger: "axis",
          backgroundColor: C.card, borderColor: C.soft,
          textStyle: { color: C.ink, fontFamily: MONO, fontSize: 11 },
          formatter: (ps: any) => {
            if (!ps?.length) return "";
            let out = [`<b>${fmtDate(dump.dates[ps[0].dataIndex])}</b>`];
            ps.forEach((p: any) => {
              if (p.value?.[1] == null) return;
              const u = (t === "r1" || t === "val" || t === "util") ? "%" : " 万亿";
              const decV = t === "r1" ? 3 : ((t === "val" || t === "util") ? 1 : 2);
              out.push(`${p.marker} ${p.seriesName} <b>${fnum(p.value[1], decV)}${u}</b>`);
            });
            return out.join("<br>");
          },
        },
        legend: { top: 0, right: 8, icon: "rect", itemWidth: 12, itemHeight: 3, textStyle: { color: C.sub, fontFamily: MONO, fontSize: 11 } },
        grid: [{ left: 58, right: 30, top: 30, bottom: 58 }],
        xAxis: [{ type: "time", gridIndex: 0, min: TS[0], max: TS[TS.length - 1], axisLine: { lineStyle: { color: C.soft } }, axisTick: { show: false }, axisLabel: { color: C.sub, fontFamily: MONO, fontSize: 10 }, splitLine: { show: false } }],
        yAxis: [{ type: "value", axisLine: { lineStyle: { color: C.soft } }, axisLabel: { color: C.sub, fontFamily: MONO, fontSize: 10 }, splitLine: { lineStyle: { color: C.faint } } }],
        dataZoom: [
          { type: "inside", xAxisIndex: [0] },
          { type: "slider", xAxisIndex: [0], bottom: 4, height: 18, borderColor: C.soft, fillerColor: C.zoomFill, handleStyle: { color: C.card, borderColor: C.sub }, dataBackground: { lineStyle: { color: C.soft }, areaStyle: { color: C.faint } }, textStyle: { color: C.sub, fontFamily: MONO, fontSize: 10 } },
        ],
        series: [] as any[],
      };

      if (t === "comp") {
        base.yAxis[0].name = "万亿";
        base.yAxis[0].nameTextStyle = { color: C.sub };
        base.series = [
          { name: "KOSPI 融资", type: "line", stack: "fin", areaStyle: { color: C.areaBlue }, lineStyle: { color: C.blue, width: 1 }, itemStyle: { color: C.blue }, showSymbol: false, data: toEchartsData(TS, finKospi) },
          { name: "KOSDAQ 融资", type: "line", stack: "fin", areaStyle: { color: C.areaRed }, lineStyle: { color: C.red, width: 1 }, itemStyle: { color: C.red }, showSymbol: false, data: toEchartsData(TS, finKosdaq) },
          { name: "质押贷款(非融资)", type: "line", showSymbol: false, lineStyle: { color: C.grey, width: 1.3, type: "dashed" }, itemStyle: { color: C.grey }, data: toEchartsData(TS, col) },
        ];
      } else if (t === "denom") {
        base.yAxis[0].name = "万亿";
        base.yAxis[0].nameTextStyle = { color: C.sub };
        base.series = [
          { name: "投资者存管金", type: "line", showSymbol: false, lineStyle: { color: C.green, width: 1.6 }, itemStyle: { color: C.green }, data: toEchartsData(TS, dep) },
          { name: "衍生品交易保证金", type: "line", showSymbol: false, lineStyle: { color: C.grey, width: 1.2, type: "dashed" }, itemStyle: { color: C.grey }, data: toEchartsData(TS, derivDep) },
          { name: "RP卖出余额", type: "line", showSymbol: false, lineStyle: { color: C.orange, width: 1.2, type: "dotted" }, itemStyle: { color: C.orange }, data: toEchartsData(TS, rp) },
        ];
      } else if (t === "r1") {
        base.yAxis[0].name = "%";
        base.yAxis[0].nameTextStyle = { color: C.sub };
        base.series = [
          { name: "KOSDAQ 融资/市值", type: "line", showSymbol: false, lineStyle: { color: C.red, width: 1.5 }, itemStyle: { color: C.red }, data: toEchartsData(TS, r1q) },
          { name: "KOSPI 融资/市值", type: "line", showSymbol: false, lineStyle: { color: C.blue, width: 1.5 }, itemStyle: { color: C.blue }, data: toEchartsData(TS, r1p) },
          { name: "两市合计", type: "line", showSymbol: false, lineStyle: { color: C.grey, width: 1, type: "dashed" }, itemStyle: { color: C.grey }, data: toEchartsData(TS, r1) },
        ];
      } else if (t === "util") {
        base.yAxis[0].name = "%";
        base.yAxis[0].max = 110;
        base.yAxis[0].nameTextStyle = { color: C.sub };
        base.series = [{
          name: "信用供与/券商自本", type: "line", showSymbol: false, lineStyle: { color: C.ink, width: 1.6 }, itemStyle: { color: C.ink },
          data: toEchartsData(TS, util),
          endLabel: { show: true, formatter: (p: any) => p.value?.[1] != null ? `${fnum(p.value[1], 1)}%` : "", color: C.ink, fontFamily: MONO, fontSize: 10.5, offset: [4, 0] },
          markLine: { silent: true, symbol: "none", label: { show: true, position: "insideEndTop", color: C.red, fontFamily: MONO, fontSize: 10, formatter: "100% 法定上限" }, lineStyle: { color: C.red, type: "dashed", width: 1 }, data: [{ yAxis: 100 }] },
        }];
      } else if (t === "val") {
        base.yAxis[0].name = "%";
        base.yAxis[0].nameTextStyle = { color: C.sub };
        base.series = [{
          name: "KOSPI市值/名义GDP", type: "line", showSymbol: false, lineStyle: { color: C.ink, width: 1.6 }, itemStyle: { color: C.ink },
          data: toEchartsData(TS, mg),
          endLabel: { show: true, formatter: (p: any) => p.value?.[1] != null ? `${fnum(p.value[1], 0)}%` : "", color: C.ink, fontFamily: MONO, fontSize: 10.5, offset: [4, 0] },
          markLine: { silent: true, symbol: "none", label: { show: true, position: "insideEndTop", color: C.red, fontFamily: MONO, fontSize: 10, formatter: "100%" }, lineStyle: { color: C.red, type: "dashed", width: 1, opacity: 0.6 }, data: [{ yAxis: 100 }] },
        }];
      }
      return base;
    };

    chart.setOption(makeOption(tab));

    const handleResize = () => chart.resize();
    window.addEventListener("resize", handleResize);
    return () => { window.removeEventListener("resize", handleResize); chart.dispose(); };
  }, [dump, dark, tab]);

  const tabLabels = [
    { key: "comp", label: "融资构成" },
    { key: "denom", label: "资金分母" },
    { key: "r1", label: "融资/市值强度" },
    { key: "util", label: "额度利用率" },
    { key: "val", label: "估值 市值/GDP" },
  ];

  return (
    <section>
      <h2 className="text-lg font-bold tracking-tight mb-1" style={{ color: C.ink }}>④ 微观分解</h2>
      <p className="text-[13px] mb-3" style={{ color: C.sub }}>融资构成、资金分母、融资/市值强度、额度利用率、估值 市值/GDP — 1998 至今</p>

      <div className="flex flex-wrap gap-0.5 items-center mb-2 px-3 py-1 rounded-xl" style={{ background: "var(--fill, rgba(120,120,128,.14))" }}>
        <span className="text-[11px] font-mono mr-1" style={{ color: C.grey, padding: "0 8px 0 2px" }}>微观分解</span>
        {tabLabels.map(({ key, label }) => (
          <button key={key} onClick={() => setTab(key)}
            className={`text-xs font-mono px-3 py-1 rounded-lg transition ${tab === key ? "font-semibold" : ""}`}
            style={{ color: tab === key ? C.ink : C.sub, background: tab === key ? C.card : "transparent", boxShadow: tab === key ? "0 1px 4px rgba(0,0,0,.14)" : "none" }}>
            {label}
          </button>
        ))}
      </div>

      <div ref={ref} style={{ height: 340, background: C.card, border: `1px solid ${C.soft}`, borderRadius: 18, padding: "14px 12px 8px" }} />
    </section>
  );
}

/* ══════════════════════════════════════════════════════════
   ⑤ 存管金
   ══════════════════════════════════════════════════════════ */

function ChartDeposits({ dump, dark }: { dump: DumpData; dark: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const C = colors(dark);
  const [years, setYears] = useState("all");

  const TS = dump.dates.map(d => new Date(d).getTime());
  const dep = dump.series.dep || [];
  const r2 = dump.series.r2 || [];
  const n = TS.length;

  // Daily change
  const chg: (number | null)[] = new Array(n).fill(null);
  for (let i = 1; i < n; i++) {
    if (dep[i] != null && dep[i - 1] != null) chg[i] = +((dep[i]! - dep[i - 1]!).toFixed(3));
  }
  const up = chg.map(v => v != null && v >= 0 ? v : null);
  const dn = chg.map(v => v != null && v < 0 ? v : null);

  const setRange = useCallback((y: string) => {
    setYears(y);
    if (!ref.current) return;
    const chart = echarts.getInstanceByDom(ref.current);
    if (!chart) return;
    let startIdx = 0;
    if (y !== "all") {
      const end = dump.dates[dump.dates.length - 1];
      const target = (parseInt(end.slice(0, 4)) - parseInt(y)) + end.slice(4);
      for (let i = 0; i < dump.dates.length; i++) {
        if (dump.dates[i] >= target) { startIdx = i; break; }
      }
    }
    chart.dispatchAction({ type: "dataZoom", startValue: TS[startIdx], endValue: TS[TS.length - 1] });
  }, [TS, dump.dates]);

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current, null, { renderer: "canvas" });
    chart.setOption({
      animation: false,
      textStyle: { fontFamily: MONO },
      axisPointer: { link: [{ xAxisIndex: "all" }] },
      tooltip: {
        trigger: "axis",
        backgroundColor: C.card, borderColor: C.soft,
        textStyle: { color: C.ink, fontFamily: MONO, fontSize: 11 },
        formatter: (ps: any) => {
          if (!ps?.length) return "";
          const i = ps[0].dataIndex;
          let out = [`<b>${fmtDate(dump.dates[i])}</b>`];
          if (dep[i] != null) out.push(`<span style="color:${C.green}">■</span> 存管金 <b>${fnum(dep[i], 2)} 万亿</b>`);
          if (chg[i] != null) {
            const pct = dep[i - 1] ? ((chg[i]! / dep[i - 1]!) * 100).toFixed(1) : "—";
            out.push(`<span style="color:${chg[i]! >= 0 ? C.red : C.blue}">■</span> 日变动 <b>${chg[i]! >= 0 ? "+" : ""}${fnum(chg[i], 3)} 万亿</b> (${pct}%)`);
          }
          if (r2[i] != null) out.push(`<span style="color:${C.sub}">R2 ${fnum(r2[i], 2)}%</span>`);
          return out.join("<br>");
        },
      },
      legend: {
        top: 0, right: 8, icon: "rect", itemWidth: 12, itemHeight: 3,
        textStyle: { color: C.sub, fontFamily: MONO, fontSize: 11 },
        data: ["投资者存管金", "每日净变动"],
      },
      grid: [
        { left: 58, right: 46, top: 28, height: "44%" },
        { left: 58, right: 46, top: "62%", height: "22%" },
      ],
      xAxis: [
        { type: "time", gridIndex: 0, min: TS[0], max: TS[TS.length - 1], axisLine: { lineStyle: { color: C.soft } }, axisTick: { show: false }, axisLabel: { show: false }, splitLine: { show: false } },
        { type: "time", gridIndex: 1, min: TS[0], max: TS[TS.length - 1], axisLine: { lineStyle: { color: C.soft } }, axisTick: { show: false }, axisLabel: { color: C.sub, fontFamily: MONO, fontSize: 10 }, splitLine: { show: false } },
      ],
      yAxis: [
        { type: "value", gridIndex: 0, name: "万亿", nameTextStyle: { color: C.sub }, axisLine: { lineStyle: { color: C.soft } }, axisLabel: { color: C.sub, fontFamily: MONO, fontSize: 10 }, splitLine: { lineStyle: { color: C.faint } } },
        { type: "value", gridIndex: 1, name: "万亿/日", nameTextStyle: { color: C.sub }, axisLine: { lineStyle: { color: C.soft } }, axisLabel: { color: C.sub, fontFamily: MONO, fontSize: 10 }, splitLine: { lineStyle: { color: C.faint } } },
      ],
      dataZoom: [
        { type: "inside", xAxisIndex: [0, 1] },
        { type: "slider", xAxisIndex: [0, 1], bottom: 6, height: 20, borderColor: C.soft, fillerColor: C.zoomFill, handleStyle: { color: C.card, borderColor: C.sub }, dataBackground: { lineStyle: { color: C.soft }, areaStyle: { color: C.faint } }, textStyle: { color: C.sub, fontFamily: MONO, fontSize: 10 } },
      ],
      series: [
        {
          name: "投资者存管金", type: "line", xAxisIndex: 0, yAxisIndex: 0, data: toEchartsData(TS, dep),
          showSymbol: false, lineStyle: { color: C.green, width: 1.6 }, itemStyle: { color: C.green },
          areaStyle: { color: "rgba(36,138,61,.08)" },
          endLabel: { show: true, formatter: (p: any) => p.value?.[1] != null ? `${fnum(p.value[1], 0)}` : "", color: C.green, fontFamily: MONO, fontSize: 10.5, offset: [4, 0] },
        },
        { name: "每日净变动", type: "bar", xAxisIndex: 1, yAxisIndex: 1, data: toEchartsData(TS, up), large: true, itemStyle: { color: C.red } },
        { name: "_dn", type: "bar", xAxisIndex: 1, yAxisIndex: 1, data: toEchartsData(TS, dn), large: true, barGap: "-100%", itemStyle: { color: C.blue } },
      ],
    });

    const handleResize = () => chart.resize();
    window.addEventListener("resize", handleResize);
    return () => { window.removeEventListener("resize", handleResize); chart.dispose(); };
  }, [dump, dark]);

  return (
    <section>
      <h2 className="text-xl font-bold tracking-tight mb-1" style={{ color: C.ink }}>⑤ 投资者存管金（R2 分母）</h2>
      <p className="text-[13px] mb-3" style={{ color: C.sub }}>투자자예탁금 = 散户闲置现金（R2 分母，不含衍生品保证金）· 下格 = 每日净变动（红进蓝出）</p>

      <div className="flex flex-wrap gap-0.5 items-center mb-2 px-3 py-1 rounded-xl" style={{ background: "var(--fill, rgba(120,120,128,.14))" }}>
        <span className="text-[11px] font-mono mr-1" style={{ color: C.grey, padding: "0 8px 0 2px" }}>窗口</span>
        {["all", "10", "5", "3", "1"].map(y => (
          <button key={y} onClick={() => setRange(y)}
            className={`text-xs font-mono px-3 py-1 rounded-lg transition ${years === y ? "font-semibold" : ""}`}
            style={{ color: years === y ? C.ink : C.sub, background: years === y ? C.card : "transparent", boxShadow: years === y ? "0 1px 4px rgba(0,0,0,.14)" : "none" }}>
            {y === "all" ? "全部" : y + "年"}
          </button>
        ))}
      </div>

      <div ref={ref} style={{ height: 440, background: C.card, border: `1px solid ${C.soft}`, borderRadius: 18, padding: "14px 12px 8px" }} />
    </section>
  );
}
