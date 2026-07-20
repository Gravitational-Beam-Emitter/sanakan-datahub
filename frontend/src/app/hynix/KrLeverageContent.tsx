"use client";

import { useState, useEffect } from "react";
import {
  fetchKrLeverageSummary,
  fetchKrLeverageSeries,
  fetchKrLeverageETF,
} from "@/lib/api";
import type {
  KrLeverageSummary,
  KrLeverageSeriesPoint,
} from "@/lib/api";

function chip(p: number | null | undefined, label = "10y"): string {
  if (p == null) return "";
  const cls = p >= 95 ? "text-down" : p >= 80 ? "text-amber" : p >= 50 ? "text-muted" : "text-up";
  return `${label} ${Math.round(p)}%`;
}

function fnum(v: number | null | undefined, dec = 0): string {
  if (v == null) return "—";
  return v.toLocaleString("en-US", {
    minimumFractionDigits: dec,
    maximumFractionDigits: dec,
  });
}

function pctChip(p: number | null | undefined) {
  if (p == null) return null;
  const cls =
    p >= 95
      ? "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400"
      : p >= 80
      ? "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400"
      : p >= 50
      ? "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400"
      : "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400";
  return (
    <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-mono ${cls}`}>
      {chip(p)}
    </span>
  );
}

export default function KrLeverageContent() {
  const [summary, setSummary] = useState<KrLeverageSummary | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    fetchKrLeverageSummary()
      .then((s) => {
        setSummary(s);
        setReady(true);
      })
      .catch(() => setReady(true));
  }, []);

  if (!ready) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="h-4 w-48 bg-surface-hover rounded animate-pulse" />
      </div>
    );
  }

  if (!summary) {
    return (
      <div className="glass rounded-xl p-8 text-center">
        <p className="text-sm text-muted">
          无法加载韩国散户杠杆数据。请确认 hynix API (8008) 已启动，并已运行{" "}
          <code className="font-mono bg-surface px-1 py-0.5 rounded text-ink">
            python -m hynix.kimpremium
          </code>
        </p>
      </div>
    );
  }

  const kpi = summary.kpi;
  const etfKpi = summary.etf_kpi as Record<string, unknown> | undefined;
  const daily = summary.latest_daily;

  const kpiCards = [
    {
      k: "杠杆温度计",
      v: etfKpi?.thermo != null ? fnum(etfKpi.thermo as number, 2) : "—",
      u: "%",
      s: `R2 +${fnum((etfKpi?.thermo as number || 0) - (kpi.r2 as number || 0), 1)}pp · 锚2024`,
      hot: true,
    },
    {
      k: "强平金额 5日均",
      v: fnum(kpi.liq5d as number, 0),
      u: " 亿",
      s: chip(kpi.liqPct as number),
      hot: (kpi.liqPct as number) >= 95,
    },
    {
      k: "强平/未收比（最新）",
      v: daily?.liq_ratio != null ? fnum(daily.liq_ratio, 1) : "—",
      u: "%",
      s: daily?.liq_ratio != null && daily.liq_ratio >= 10 ? (
        <span className="kr-chip top">≥10% 爆仓日</span>
      ) : (
        "爆仓阈 10%"
      ),
      hot: daily?.liq_ratio != null && daily.liq_ratio >= 10,
    },
    {
      k: "估值 KOSPI市值/GDP",
      v: fnum(kpi.mg as number, 0),
      u: "%",
      s: pctChip(kpi.mgPct as number),
      hot: (kpi.mgPct as number) >= 95,
    },
    {
      k: "R2 融资/存管金",
      v: daily?.r2 != null ? fnum(daily.r2, 2) : "—",
      u: "%",
      s: pctChip(daily?.r2_10y_pct),
    },
    {
      k: "信用融资余额",
      v: fnum(kpi.fin as number, 2),
      u: " 万亿",
      s: `KOSPI ${fnum(kpi.finKospi as number, 1)} · KOSDAQ ${fnum(kpi.finKosdaq as number, 1)}`,
    },
    {
      k: "投资者存管金",
      v: fnum(kpi.dep as number, 1),
      u: " 万亿",
      s: `KOSPI ${fnum(kpi.kospi as number, 0)} / SPX ${fnum(kpi.spx as number, 0)}`,
    },
    {
      k: "额度利用率",
      v: kpi.util != null ? fnum(kpi.util as number, 1) : "—",
      u: "%",
      s: `自本 ${fnum(kpi.capEq as number, 0)} 万亿 · 法定上限 100%`,
      hot: (kpi.util as number) >= 90,
    },
  ];

  if (etfKpi) {
    const domTotal = (etfKpi.aum as number || 0) + (etfKpi.aumInv as number || 0);
    const os = etfKpi.os as Record<string, unknown> | undefined;
    kpiCards.push({
      k: "杠杆ETF 嵌入杠杆规模",
      v: fnum(domTotal, 1),
      u: " 万亿",
      s: `${os?.usdBn != null ? `+ $${fnum(os.usdBn as number, 1)}bn 海外(top50内·下限)` : "海外持仓接入中"} · 多头${etfKpi.n}+反向${etfKpi.nInv || 0}只`,
    });
  }

  return (
    <div className="flex flex-col gap-6">
      {/* Alerts */}
      {daily?.liq_ratio != null && daily.liq_ratio >= 10 && (
        <div className="flex gap-3 items-start rounded-xl bg-red-50 dark:bg-red-900/10 border border-red-200 dark:border-red-900/30 text-xs py-3 px-4">
          <span className="font-mono text-[10px] font-bold tracking-widest text-down shrink-0 mt-0.5">
            风险
          </span>
          <span className="text-ink">
            最新交易日强平/未收比 <b className="font-mono">{fnum(daily.liq_ratio, 1)}%</b> ≥10%，为爆仓日
          </span>
        </div>
      )}
      {(kpi.mgPct as number) >= 95 && (
        <div className="flex gap-3 items-start rounded-xl bg-red-50 dark:bg-red-900/10 border border-red-200 dark:border-red-900/30 text-xs py-3 px-4">
          <span className="font-mono text-[10px] font-bold tracking-widest text-down shrink-0 mt-0.5">
            估值
          </span>
          <span className="text-ink">
            KOSPI市值/GDP = <b className="font-mono">{fnum(kpi.mg as number, 0)}%</b>，处于近10年{" "}
            <b>{Math.round(kpi.mgPct as number)}</b> 分位（≥95 昂贵）
          </span>
        </div>
      )}

      {/* KPI Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
        {kpiCards.map((card, i) => (
          <div
            key={i}
            className={`glass rounded-xl p-3.5 ${
              card.hot ? "border-red-200 dark:border-red-900/50" : ""
            }`}
          >
            <div className="text-[11px] text-muted font-medium flex items-center gap-1.5">
              {card.hot && (
                <span className="w-1.5 h-1.5 rounded-full bg-down shrink-0" />
              )}
              {card.k}
            </div>
            <div
              className={`text-2xl font-bold font-mono mt-1 ${
                card.hot ? "text-down" : "text-ink"
              }`}
            >
              {card.v}
              <span className="text-xs text-muted font-normal ml-0.5">{card.u}</span>
            </div>
            <div className="text-[10.5px] text-muted font-mono mt-1.5 leading-relaxed">
              {card.s}
            </div>
          </div>
        ))}
      </div>

      {/* Data source note */}
      <div className="glass rounded-xl p-4">
        <p className="text-xs text-muted leading-relaxed">
          <b className="text-ink">数据源</b>：KOFIA FreeSIS（信用/资金/市值）· KSD SEIBro（ETF申赎、海外托管TOP50）· Naver（标普500）— 日频官方数据。
          数据覆盖 {summary.range.start} 至 {summary.range.end}，共 {summary.range.rows.toLocaleString()} 个交易日。
          生成时间：{summary.generated}。
        </p>
      </div>

      {/* Key indicator reference */}
      <MiniChartSection />
    </div>
  );
}

/* ── Mini chart reference section ── */

function MiniChartSection() {
  const [r2Data, setR2Data] = useState<KrLeverageSeriesPoint[]>([]);
  const [liqData, setLiqData] = useState<KrLeverageSeriesPoint[]>([]);
  const [thermoData, setThermoData] = useState<KrLeverageSeriesPoint[]>([]);

  useEffect(() => {
    fetchKrLeverageSeries("r2", 200).then((r) => {
      if (r) setR2Data(r.data.reverse());
    });
    fetchKrLeverageSeries("liq", 200).then((r) => {
      if (r) setLiqData(r.data.reverse());
    });
    fetchKrLeverageETF("thermo", 200).then((r) => {
      if (r) setThermoData(r.data.reverse());
    });
  }, []);

  return (
    <div className="flex flex-col gap-6">
      {/* Indicator reference */}
      <div className="glass rounded-xl p-4 sm:p-6">
        <h3 className="text-sm font-medium text-ink mb-4">关键指标说明</h3>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs">
          <div className="space-y-2">
            <div>
              <span className="font-mono font-medium text-ink">R2</span>
              <span className="text-muted ml-2">= 信用融资余额 / 投资者存管金。反映散户杠杆率，值越高说明借钱炒股越狂热。</span>
            </div>
            <div>
              <span className="font-mono font-medium text-ink">杠杆温度计</span>
              <span className="text-muted ml-2">= R2 + 杠杆ETF累计净申赎/存管金。补上了通过ETF间接加杠杆的部分。</span>
            </div>
            <div>
              <span className="font-mono font-medium text-ink">强平金额</span>
              <span className="text-muted ml-2">券商强制平仓卖出金额（亿韩元）。单日 ≥10% 为爆仓日。</span>
            </div>
            <div>
              <span className="font-mono font-medium text-ink">10年分位</span>
              <span className="text-muted ml-2">2520 交易日滚动窗口中的百分位排名。≥95 为极端高位。</span>
            </div>
          </div>
          <div className="space-y-2">
            <div>
              <span className="font-mono font-medium text-ink">存管金</span>
              <span className="text-muted ml-2">散户放在券商的闲置现金（R2 分母），资金流入/出市场的先导指标。</span>
            </div>
            <div>
              <span className="font-mono font-medium text-ink">KOSPI市值/GDP</span>
              <span className="text-muted ml-2">巴菲特指标。当前 ~{r2Data.length ? "209%" : "—"}，远高于 100% 公允价值线。</span>
            </div>
            <div>
              <span className="font-mono font-medium text-ink">额度利用率</span>
              <span className="text-muted ml-2">=(融资+借券+质押贷款)/券商自有资本。法定上限 100%。接近上限说明券商弹药不足。</span>
            </div>
            <div>
              <span className="font-mono font-medium text-ink">ETF净申赎</span>
              <span className="text-muted ml-2">杠杆ETF当日申购-赎回（亿韩元）。红=散户加杠杆，蓝=去杠杆。</span>
            </div>
          </div>
        </div>
      </div>

      {/* Sparkline cards for key indicators */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <SparklineCard
          title="R2 融资率"
          data={r2Data}
          unit="%"
          color="var(--ink)"
          lastLabel="R2 融资/存管金"
        />
        <SparklineCard
          title="强平金额 5日均"
          data={liqData}
          unit=" 亿"
          color="var(--red)"
          computeMA={5}
          lastLabel="日均强平"
        />
        <SparklineCard
          title="杠杆温度计"
          data={thermoData}
          unit="%"
          color="var(--orange)"
          lastLabel="温度计 (R2+ETF)"
        />
      </div>
    </div>
  );
}

/* ── Tiny inline SVG sparkline ── */

function SparklineCard({
  title,
  data,
  unit,
  color,
  computeMA,
  lastLabel,
}: {
  title: string;
  data: KrLeverageSeriesPoint[];
  unit: string;
  color: string;
  computeMA?: number;
  lastLabel: string;
}) {
  if (!data.length) {
    return (
      <div className="glass rounded-xl p-4">
        <div className="text-xs text-muted">{title}</div>
        <div className="h-24 flex items-center justify-center">
          <span className="text-xs text-muted">加载中…</span>
        </div>
      </div>
    );
  }

  const W = 280;
  const H = 100;
  const padLeft = 42;
  const padRight = 12;
  const padTop = 8;
  const padBottom = 18;
  const plotW = W - padLeft - padRight;
  const plotH = H - padTop - padBottom;

  let values = data.map((d) => d.value).filter((v) => v != null) as number[];

  // Optional moving average
  let maValues: (number | null)[] = [];
  if (computeMA && values.length) {
    const buf: number[] = [];
    for (let i = 0; i < data.length; i++) {
      const v = data[i].value;
      if (v != null) {
        buf.push(v);
        if (buf.length > computeMA) buf.shift();
      }
      if (buf.length > 0) {
        maValues.push(buf.reduce((a, b) => a + b, 0) / buf.length);
      } else {
        maValues.push(null);
      }
    }
  }

  // If we have more data points than pixel width, sample
  const step = Math.max(1, Math.floor(values.length / plotW));
  const sampled: { x: number; y: number }[] = [];
  const maSampled: { x: number; y: number | null }[] = [];

  for (let i = 0; i < data.length; i++) {
    if (data[i].value == null) continue;
    sampled.push({ x: i, y: data[i].value! });
    if (maValues.length) {
      maSampled.push({ x: i, y: maValues[i] });
    }
  }

  if (sampled.length < 2) {
    return (
      <div className="glass rounded-xl p-4">
        <div className="text-xs text-muted">{title}</div>
        <div className="h-24 flex items-center justify-center">
          <span className="text-sm font-mono font-bold text-ink">
            {values[values.length - 1]?.toFixed(1)}{unit}
          </span>
        </div>
      </div>
    );
  }

  const yMin = Math.min(...sampled.map((s) => s.y));
  const yMax = Math.max(...sampled.map((s) => s.y));
  const yPad = Math.max((yMax - yMin) * 0.1, 1);

  const xScale = (x: number) => padLeft + (x / (data.length - 1)) * plotW;
  const yScale = (y: number) =>
    padTop + plotH - ((y - (yMin - yPad)) / (yMax - yMin + 2 * yPad)) * plotH;

  // Build polyline
  const buildPath = (pts: { x: number; y: number | null }[]) => {
    let d = "";
    let first = true;
    for (const p of pts) {
      if (p.y == null) {
        first = true;
        continue;
      }
      const sx = xScale(p.x);
      const sy = yScale(p.y);
      d += first ? `M${sx},${sy}` : `L${sx},${sy}`;
      first = false;
    }
    return d;
  };

  const lastVal = values[values.length - 1];
  const lastDate = data[data.length - 1]?.date;

  return (
    <div className="glass rounded-xl p-4">
      <div className="text-xs text-muted mb-1">{title}</div>
      <div className="flex items-baseline gap-1.5 mb-2">
        <span className="text-lg font-bold font-mono text-ink">
          {lastVal?.toFixed(1)}
        </span>
        <span className="text-xs text-muted">{unit}</span>
      </div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full h-24"
        preserveAspectRatio="xMidYMid meet"
      >
        {/* Y grid lines */}
        {[yMin - yPad, (yMin + yMax) / 2, yMax + yPad].map((tick, i) => (
          <g key={i}>
            <line
              x1={padLeft}
              x2={W - padRight}
              y1={yScale(tick)}
              y2={yScale(tick)}
              stroke="rgba(120,120,128,.10)"
              strokeWidth={0.5}
            />
            <text
              x={padLeft - 3}
              y={yScale(tick) + 3}
              textAnchor="end"
              fill="var(--muted)"
              fontSize={9}
              fontFamily="SF Mono, monospace"
            >
              {tick.toFixed(tick < 10 ? 1 : 0)}
            </text>
          </g>
        ))}

        {/* MA line */}
        {maSampled.length > 1 && (
          <path
            d={buildPath(maSampled)}
            fill="none"
            stroke={color}
            strokeWidth={1}
            strokeOpacity={0.3}
            strokeDasharray="3,2"
          />
        )}

        {/* Main line */}
        <path
          d={buildPath(sampled)}
          fill="none"
          stroke={color}
          strokeWidth={1.4}
        />

        {/* Last date label */}
        {lastDate && (
          <text
            x={W - padRight}
            y={H - 3}
            textAnchor="end"
            fill="var(--muted)"
            fontSize={8.5}
            fontFamily="SF Mono, monospace"
          >
            {lastDate}
          </text>
        )}

        {/* Legend */}
        <text
          x={padLeft}
          y={12}
          fill="var(--ink)"
          fontSize={9}
          fontFamily="SF Mono, monospace"
        >
          {lastLabel}
        </text>
      </svg>
    </div>
  );
}
