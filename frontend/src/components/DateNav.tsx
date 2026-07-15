"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

function toDate(ymd: string): Date {
  const clean = ymd.replace(/-/g, "");
  return new Date(
    parseInt(clean.slice(0, 4)),
    parseInt(clean.slice(4, 6)) - 1,
    parseInt(clean.slice(6, 8))
  );
}

function formatDisplayDate(ymd: string): string {
  const d = toDate(ymd);
  const week = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];
  return `${d.getMonth() + 1}月${d.getDate()}日 ${week[d.getDay()]}`;
}

function addDays(ymd: string, n: number): string {
  const d = toDate(ymd);
  d.setDate(d.getDate() + n);
  return d.toISOString().slice(0, 10);
}

export default function DateNav({
  date,
  availableDates,
  basePath = "/",
}: {
  date: string;
  availableDates: string[];
  basePath?: string;
}) {
  const router = useRouter();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  const navigate = useCallback(
    (d: string) => {
      router.push(`${basePath}?date=${d}`);
    },
    [router, basePath]
  );

  const prev = addDays(date, -1);
  const next = addDays(date, 1);
  const hasNext = mounted && next <= new Date().toISOString().slice(0, 10);

  return (
    <div className="flex items-center gap-2">
      <button
        onClick={() => navigate(prev)}
        className="flex items-center justify-center w-8 h-8 rounded-xl glass text-muted hover:text-ink hover:brightness-105 transition-all cursor-pointer"
        aria-label="前一天"
      >
        ←
      </button>
      <h1 className="text-base font-semibold text-ink tabular-nums">
        {formatDisplayDate(date)}
      </h1>
      {hasNext && (
        <button
          onClick={() => navigate(next)}
          className="flex items-center justify-center w-8 h-8 rounded-xl glass text-muted hover:text-ink hover:brightness-105 transition-all cursor-pointer"
          aria-label="后一天"
        >
          →
        </button>
      )}
      {mounted && date !== availableDates[0] && (
        <button
          onClick={() => navigate(availableDates[0])}
          className="text-xs px-2.5 py-1.5 rounded-lg bg-primary-a15 text-primary hover:bg-primary-a10 transition-colors cursor-pointer font-medium"
        >
          最新
        </button>
      )}
    </div>
  );
}
