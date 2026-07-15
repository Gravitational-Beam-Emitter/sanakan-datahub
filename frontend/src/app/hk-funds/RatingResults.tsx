"use client";

import { useState, useEffect, useCallback } from "react";
import type { RatingTemplate, RatingResults as RatingResultsType } from "@/lib/api";
import { fetchRatingTemplates, fetchRatingResults, computeRatings } from "@/lib/api";

/* ── Helpers ── */

function getUserId(): string {
  if (typeof window === "undefined") return "anonymous";
  const stored = window.localStorage.getItem("rating_user_id");
  if (stored) return stored;
  const uid = "user_" + Math.random().toString(36).slice(2, 8);
  window.localStorage.setItem("rating_user_id", uid);
  return uid;
}

const TYPE_LABELS: Record<string, string> = {
  fund_risk: "基金风险",
  manager_dd: "管理人尽调",
};

/* ── Rating Results Tab ── */

export default function RatingResultsTab() {
  const [userId, setUserId] = useState("");
  const [templates, setTemplates] = useState<RatingTemplate[]>([]);
  const [selectedTemplateId, setSelectedTemplateId] = useState<number | null>(null);
  const [results, setResults] = useState<RatingResultsType | null>(null);
  const [loading, setLoading] = useState(true);
  const [computing, setComputing] = useState(false);
  const [sortKey, setSortKey] = useState<string>("overall_score");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  useEffect(() => {
    setUserId(getUserId());
  }, []);

  const loadTemplates = useCallback(async () => {
    setLoading(true);
    const sys = await fetchRatingTemplates("system");
    const mine = await fetchRatingTemplates(userId || getUserId());
    setTemplates([...sys, ...mine]);
    setLoading(false);
  }, [userId]);

  useEffect(() => {
    if (userId) loadTemplates();
  }, [userId, loadTemplates]);

  const loadResults = useCallback(async () => {
    if (!selectedTemplateId) return;
    setLoading(true);
    const template = templates.find((t) => t.id === selectedTemplateId);
    const targetType = template?.template_type === "manager_dd" ? "manager" : "fund";
    const r = await fetchRatingResults(selectedTemplateId, userId, targetType, 200);
    setResults(r);
    setLoading(false);
  }, [selectedTemplateId, userId, templates]);

  useEffect(() => {
    if (selectedTemplateId) loadResults();
  }, [selectedTemplateId, loadResults]);

  const handleCompute = async () => {
    if (!selectedTemplateId) return;
    setComputing(true);
    const template = templates.find((t) => t.id === selectedTemplateId);
    const targetType = template?.template_type === "manager_dd" ? "manager" : "fund";
    const r = await computeRatings(selectedTemplateId, userId, targetType);
    setComputing(false);
    if (r && "total_rated" in r && r.total_rated) loadResults();
  };

  const sortedResults = results?.results
    ? [...results.results].sort((a, b) => {
        const aVal = a[sortKey as keyof typeof a] ?? 0;
        const bVal = b[sortKey as keyof typeof b] ?? 0;
        if (typeof aVal === "number" && typeof bVal === "number") {
          return sortDir === "desc" ? bVal - aVal : aVal - bVal;
        }
        const aStr = String(aVal);
        const bStr = String(bVal);
        return sortDir === "desc" ? bStr.localeCompare(aStr) : aStr.localeCompare(bStr);
      })
    : [];

  const toggleSort = (key: string) => {
    if (sortKey === key) {
      setSortDir((d) => (d === "desc" ? "asc" : "desc"));
    } else {
      setSortKey(key);
      setSortDir("desc");
    }
  };

  const selectedTemplate = templates.find((t) => t.id === selectedTemplateId);

  return (
    <section className="max-w-4xl mx-auto mt-6 px-4">
      {/* Header */}
      <div className="mb-4">
        <h2 className="text-lg font-semibold">我的评级结果</h2>
        <p className="text-xs text-muted mt-1">
          用户: <code className="text-primary">{userId || "..."}</code>
          {" — "}查看按模板计算的基金/管理人评级
        </p>
      </div>

      {/* Template selector */}
      <div className="flex gap-3 mb-4">
        <select
          className="flex-1 px-4 py-2 rounded-lg border border-border bg-surface text-sm"
          value={selectedTemplateId ?? ""}
          onChange={(e) => setSelectedTemplateId(Number(e.target.value) || null)}
        >
          <option value="">-- 选择模板 --</option>
          {templates.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name} ({TYPE_LABELS[t.template_type] || t.template_type})
            </option>
          ))}
        </select>
        <button
          className="px-4 py-2 rounded-lg bg-amber-500 text-white text-sm hover:bg-amber-600 disabled:opacity-50"
          onClick={handleCompute}
          disabled={!selectedTemplateId || computing}
        >
          {computing ? "计算中..." : "🔄 重新计算"}
        </button>
        <button
          className="px-4 py-2 rounded-lg bg-surface border border-border text-sm hover:bg-surface-hover"
          onClick={() => {
            if (!results) return;
            const csv =
              "target_name,category,overall_score\n" +
              sortedResults
                .map((r) => `"${r.target_name}",${r.category},${r.overall_score}`)
                .join("\n");
            const blob = new Blob([csv], { type: "text/csv" });
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = `ratings_${selectedTemplate?.name || "export"}.csv`;
            a.click();
            URL.revokeObjectURL(url);
          }}
        >
          📥 CSV导出
        </button>
      </div>

      {results && (
        <>
          {/* Distribution cards */}
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-4">
            {(results.distribution || []).map((d) => (
              <div key={d.category} className="glass rounded-xl p-3 text-center">
                <div className="text-xs text-muted">{d.category}</div>
                <div className="text-2xl font-bold">{d.count}</div>
                <div className="text-[10px] text-muted">
                  {results.total_rated > 0
                    ? ((d.count / results.total_rated) * 100).toFixed(0) + "%"
                    : "-"}
                </div>
              </div>
            ))}
          </div>

          {/* Summary */}
          <div className="glass rounded-xl p-3 mb-4 text-xs text-muted">
            共 {results.total_rated} 条{" "}
            {selectedTemplate?.template_type === "manager_dd" ? "管理人" : "基金"} 评级
            {selectedTemplate && <> · 模板: {selectedTemplate.name}</>}
          </div>

          {/* Results table */}
          <div className="glass rounded-xl overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="border-b border-border">
                <tr>
                  <th
                    className="text-left px-4 py-2 text-xs text-muted cursor-pointer hover:text-ink"
                    onClick={() => toggleSort("target_name")}
                  >
                    名称 {sortKey === "target_name" && (sortDir === "desc" ? "↓" : "↑")}
                  </th>
                  <th
                    className="text-center px-4 py-2 text-xs text-muted cursor-pointer hover:text-ink"
                    onClick={() => toggleSort("category")}
                  >
                    评级 {sortKey === "category" && (sortDir === "desc" ? "↓" : "↑")}
                  </th>
                  <th
                    className="text-center px-4 py-2 text-xs text-muted cursor-pointer hover:text-ink"
                    onClick={() => toggleSort("overall_score")}
                  >
                    分数 {sortKey === "overall_score" && (sortDir === "desc" ? "↓" : "↑")}
                  </th>
                  <th className="text-center px-4 py-2 text-xs text-muted">因子数</th>
                </tr>
              </thead>
              <tbody>
                {loading &&
                  Array.from({ length: 5 }).map((_, i) => (
                    <tr key={i}>
                      {Array.from({ length: 4 }).map((__, j) => (
                        <td key={j} className="px-4 py-3">
                          <div className="h-4 bg-surface-hover rounded animate-pulse" />
                        </td>
                      ))}
                    </tr>
                  ))}
                {!loading &&
                  sortedResults.map((r, i) => (
                    <tr
                      key={i}
                      className="border-b border-border/40 hover:bg-surface-hover"
                    >
                      <td className="px-4 py-2 max-w-xs truncate" title={r.target_name}>
                        {r.target_name}
                      </td>
                      <td className="px-4 py-2 text-center">
                        <span className="text-xs px-2 py-0.5 rounded-full bg-primary-a15 text-primary">
                          {r.category}
                        </span>
                      </td>
                      <td className="px-4 py-2 text-center font-mono">
                        {r.overall_score.toFixed(2)}
                      </td>
                      <td className="px-4 py-2 text-center text-xs text-muted">
                        {r.factor_count}
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {!results && !loading && !selectedTemplateId && (
        <div className="text-center py-12 text-muted text-sm">
          选择模板查看评级结果
        </div>
      )}
    </section>
  );
}
