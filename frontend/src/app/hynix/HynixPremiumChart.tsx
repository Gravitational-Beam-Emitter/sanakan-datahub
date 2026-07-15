"use client";

import type { HynixArbitrageHistoryPoint } from "@/lib/api";

interface Props {
  adrHistory: HynixArbitrageHistoryPoint[];
  hkHistory: HynixArbitrageHistoryPoint[];
  basePrice: number;
}

function fmtKRW(n: number): string {
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e4) return `${(n / 1e4).toFixed(0)}万`;
  return n.toLocaleString();
}

export default function HynixPremiumChart({
  adrHistory,
  hkHistory,
  basePrice,
}: Props) {
  const hasData = adrHistory.length > 0 || hkHistory.length > 0;
  if (!hasData) {
    return (
      <div className="flex items-center justify-center h-48 text-xs text-muted">
        暂无历史数据
      </div>
    );
  }

  const width = 700;
  const height = 280;
  const pad = 40;
  const w = width - pad * 2;
  const h = height - pad * 2;

  // Build unified date axis from adrHistory dates
  const allDates = adrHistory.map((p) => p.date);
  if (!allDates.length) return null;

  // Collect all premium values for y-axis range
  const allVals: number[] = [];
  const adrPoints: { x: number; y: number }[] = [];
  const hkPoints: { x: number; y: number }[] = [];

  adrHistory.forEach((p, i) => {
    const x = pad + (allDates.length > 1 ? (i / (allDates.length - 1)) * w : w / 2);
    adrPoints.push({ x, y: p.premium_pct });
    allVals.push(p.premium_pct);
  });

  // Map hkHistory by date
  const hkDateMap = new Map<string, HynixArbitrageHistoryPoint>();
  hkHistory.forEach((p) => hkDateMap.set(p.date, p));

  allDates.forEach((d, i) => {
    const hp = hkDateMap.get(d);
    if (hp) {
      const x = pad + (allDates.length > 1 ? (i / (allDates.length - 1)) * w : w / 2);
      hkPoints.push({ x, y: hp.premium_pct });
      allVals.push(hp.premium_pct);
    }
  });

  const minVal = Math.min(0, ...allVals);
  const maxVal = Math.max(0, ...allVals);
  const range = maxVal - minVal || 1;
  const yMin = minVal - range * 0.1;
  const yMax = maxVal + range * 0.1;
  const yRange = yMax - yMin;

  const toY = (v: number) => h + pad - ((v - yMin) / yRange) * h;

  // Build polyline points strings
  const adrLine = adrPoints
    .map((p) => `${p.x.toFixed(1)},${toY(p.y).toFixed(1)}`)
    .join(" ");
  const hkLine = hkPoints
    .map((p) => `${p.x.toFixed(1)},${toY(p.y).toFixed(1)}`)
    .join(" ");

  const zeroY = toY(0);

  // Y-axis ticks
  const yTicks = 5;
  const yStep = yRange / yTicks;

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className="w-full h-auto"
      preserveAspectRatio="xMidYMid meet"
    >
      {/* Zero baseline */}
      <line
        x1={pad}
        y1={zeroY}
        x2={width - pad}
        y2={zeroY}
        stroke="var(--border)"
        strokeWidth="1"
        strokeDasharray="4 4"
      />
      <text
        x={pad - 4}
        y={zeroY + 4}
        textAnchor="end"
        fontSize="10"
        fill="var(--muted)"
      >
        0%
      </text>

      {/* Y-axis grid lines */}
      {Array.from({ length: yTicks + 1 }, (_, i) => {
        const val = yMin + yStep * i;
        const y = toY(val);
        if (Math.abs(val) < yRange * 0.02) return null; // skip near-zero (already drawn)
        return (
          <g key={i}>
            <line
              x1={pad}
              y1={y}
              x2={width - pad}
              y2={y}
              stroke="var(--border)"
              strokeWidth="0.5"
              opacity="0.5"
            />
            <text
              x={pad - 4}
              y={y + 4}
              textAnchor="end"
              fontSize="10"
              fill="var(--muted)"
            >
              {val.toFixed(1)}%
            </text>
          </g>
        );
      })}

      {/* X-axis labels */}
      {allDates.map((l, i) => {
        const step = Math.max(1, Math.floor(allDates.length / 8));
        if (i % step !== 0 && i !== allDates.length - 1) return null;
        const x = pad + (allDates.length > 1 ? (i / (allDates.length - 1)) * w : w / 2);
        return (
          <text
            key={i}
            x={x}
            y={height - 8}
            textAnchor="middle"
            fontSize="9"
            fill="var(--muted)"
          >
            {l.length > 5 ? l.slice(5) : l}
          </text>
        );
      })}

      {/* ADR premium line */}
      {adrLine && (
        <g>
          <polyline
            points={adrLine}
            fill="none"
            stroke="var(--primary)"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          {/* Dots */}
          {adrPoints.map((p, i) => (
            <circle
              key={`adr-${i}`}
              cx={p.x}
              cy={toY(p.y)}
              r="2"
              fill="var(--primary)"
            />
          ))}
        </g>
      )}

      {/* HK ETP premium line */}
      {hkLine && (
        <g>
          <polyline
            points={hkLine}
            fill="none"
            stroke="var(--amber, #f59e0b)"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeDasharray="6 3"
          />
          {hkPoints.map((p, i) => (
            <circle
              key={`hk-${i}`}
              cx={p.x}
              cy={toY(p.y)}
              r="2"
              fill="var(--amber, #f59e0b)"
            />
          ))}
        </g>
      )}

      {/* Legend */}
      <rect
        x={width - pad - 160}
        y={pad - 14}
        width={150}
        height={36}
        rx={8}
        fill="var(--surface)"
        stroke="var(--border)"
        strokeWidth="0.5"
      />
      <line
        x1={width - pad - 148}
        y1={pad + 2}
        x2={width - pad - 130}
        y2={pad + 2}
        stroke="var(--primary)"
        strokeWidth="2"
      />
      <text
        x={width - pad - 126}
        y={pad + 6}
        fontSize="10"
        fill="var(--muted)"
      >
        ADR 溢价
      </text>
      <line
        x1={width - pad - 148}
        y1={pad + 16}
        x2={width - pad - 130}
        y2={pad + 16}
        stroke="var(--amber, #f59e0b)"
        strokeWidth="2"
        strokeDasharray="6 3"
      />
      <text
        x={width - pad - 126}
        y={pad + 20}
        fontSize="10"
        fill="var(--muted)"
      >
        7709.HK 溢价
      </text>

      {/* Tooltip info: baseline reference */}
      <text
        x={width - pad}
        y={pad - 18}
        textAnchor="end"
        fontSize="9"
        fill="var(--muted)"
      >
        基准: {fmtKRW(basePrice)} KRW
      </text>
    </svg>
  );
}
