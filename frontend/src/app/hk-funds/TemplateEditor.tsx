"use client";

import { useState, useEffect, useCallback } from "react";
import type {
  RatingTemplate,
  TemplateDetail,
  TemplateFactor,
  CategoryThreshold,
} from "@/lib/api";
import {
  fetchRatingTemplates,
  fetchRatingTemplateDetail,
  cloneRatingTemplate,
  updateRatingTemplate,
  deleteRatingTemplate,
  computeRatings,
} from "@/lib/api";

/* ── Helpers ── */

const TYPE_LABELS: Record<string, string> = {
  fund_risk: "基金风险",
  manager_dd: "管理人尽调",
};

function getUserId(): string {
  if (typeof window === "undefined") return "anonymous";
  const stored = window.localStorage.getItem("rating_user_id");
  if (stored) return stored;
  const uid = "user_" + Math.random().toString(36).slice(2, 8);
  window.localStorage.setItem("rating_user_id", uid);
  return uid;
}

/* ── Template Editor Tab ── */

export default function TemplateEditor() {
  const [userId, setUserId] = useState("");
  const [templates, setTemplates] = useState<RatingTemplate[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<TemplateDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [computing, setComputing] = useState(false);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [computeResult, setComputeResult] = useState<any>(null);
  const [message, setMessage] = useState("");

  /* factor weights being edited; keyed by factor_key */
  const [editingWeights, setEditingWeights] = useState<Record<string, number>>({});
  const [editingThresholds, setEditingThresholds] = useState<CategoryThreshold[]>([]);

  useEffect(() => {
    setUserId(getUserId());
  }, []);

  /* Load template list */
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

  /* Load template detail */
  const loadDetail = useCallback(async (tid: number) => {
    const d = await fetchRatingTemplateDetail(tid);
    if (d) {
      setDetail(d);
      const w: Record<string, number> = {};
      d.factors.forEach((f: TemplateFactor) => {
        w[f.factor_key] = f.weight;
      });
      setEditingWeights(w);
      setEditingThresholds(d.category_thresholds || []);
    }
  }, []);

  useEffect(() => {
    if (selectedId) loadDetail(selectedId);
  }, [selectedId, loadDetail]);

  /* Clone */
  const handleClone = async () => {
    if (!selectedId || !detail) return;
    const r = await cloneRatingTemplate(selectedId, userId, detail.name + " (自定义)");
    if (r) {
      setMessage(`已克隆为「${r.template.name}」`);
      loadTemplates();
      setSelectedId(r.cloned_template_id);
    }
  };

  /* Save weights & thresholds */
  const handleSave = async () => {
    if (!selectedId || !detail || detail.is_system) return;
    setSaving(true);
    setMessage("");
    const r = await updateRatingTemplate(selectedId, {
      user_id: userId,
      factor_weights: editingWeights,
      category_thresholds: editingThresholds,
    });
    if (r) {
      setMessage(`已保存: ${r.updated.join(", ")}`);
      loadDetail(selectedId);
    }
    setSaving(false);
  };

  /* Compute ratings */
  const handleCompute = async () => {
    if (!selectedId) return;
    setComputing(true);
    setComputeResult(null);
    const r = await computeRatings(selectedId, userId, detail?.template_type === "manager_dd" ? "manager" : "fund");
    setComputing(false);
    if (r) {
      setComputeResult(r as any);
      if ("total_rated" in r && r.total_rated) {
        setMessage(`已计算 ${r.total_rated} 条评级`);
      }
    } else {
      setMessage("计算失败");
    }
  };

  /* Weight slider */
  const handleWeightChange = (key: string, val: number) => {
    setEditingWeights((prev) => {
      const next = { ...prev, [key]: val };
      return next;
    });
  };

  /* Normalize weights to 100% */
  const normalizeWeights = () => {
    const sum = Object.values(editingWeights).reduce((a, b) => a + b, 0);
    if (sum === 0 || Math.abs(sum - 1) < 0.001) return;
    const next: Record<string, number> = {};
    Object.entries(editingWeights).forEach(([k, v]) => {
      next[k] = Math.round((v / sum) * 1000) / 1000;
    });
    setEditingWeights(next);
  };

  const weightSum = Object.values(editingWeights).reduce((a, b) => a + b, 0);
  const isSystem = detail?.is_system ?? true;
  const isOwn = !isSystem;

  return (
    <section className="max-w-4xl mx-auto mt-6 px-4">
      {/* Header */}
      <div className="mb-4">
        <h2 className="text-lg font-semibold">评级模板编辑器</h2>
        <p className="text-xs text-muted mt-1">
          用户: <code className="text-primary">{userId || "..."}</code>
          {" — "}可克隆系统模板后自定义因子权重和评级阈值
        </p>
      </div>

      {/* Template selector */}
      <div className="flex gap-3 mb-4">
        <select
          className="flex-1 px-4 py-2 rounded-lg border border-border bg-surface text-sm"
          value={selectedId ?? ""}
          onChange={(e) => setSelectedId(Number(e.target.value) || null)}
        >
          <option value="">-- 选择模板 --</option>
          {templates.map((t) => (
            <option key={t.id} value={t.id}>
              {t.is_system ? "📋" : "✏️"} {t.name}
              {" "}({TYPE_LABELS[t.template_type] || t.template_type})
              {t.is_system ? " [系统]" : ` [${t.user_id}]`}
            </option>
          ))}
        </select>
        <button
          className="px-4 py-2 rounded-lg bg-surface border border-border text-sm hover:bg-surface-hover disabled:opacity-50"
          disabled={!selectedId}
          onClick={handleClone}
        >
          克隆为我的模板
        </button>
      </div>

      {detail && (
        <>
          {/* Template info */}
          <div className="glass rounded-xl p-4 mb-4">
            <div className="flex items-center justify-between mb-1">
              <h3 className="font-semibold">{detail.name}</h3>
              <span className={`text-xs px-2 py-0.5 rounded-full ${isSystem ? "bg-blue-100 text-blue-700" : "bg-green-100 text-green-700"}`}>
                {isSystem ? "系统模板" : "我的模板"}
              </span>
            </div>
            <p className="text-xs text-muted">{detail.description}</p>
            <div className="flex gap-4 mt-2 text-xs text-muted">
              <span>类型: {TYPE_LABELS[detail.template_type]}</span>
              <span>版本: {detail.methodology_version}</span>
              <span>因子数: {detail.factors.length}</span>
            </div>
          </div>

          {/* Factor weights */}
          <div className="glass rounded-xl p-4 mb-4">
            <div className="flex items-center justify-between mb-3">
              <h4 className="font-medium text-sm">因子权重</h4>
              <div className="flex items-center gap-2">
                <span className={`text-xs ${Math.abs(weightSum - 1) < 0.001 ? "text-emerald-600" : "text-amber-600"}`}>
                  合计: {(weightSum * 100).toFixed(1)}%
                </span>
                <button
                  className="text-xs px-2 py-1 rounded bg-surface border border-border hover:bg-surface-hover"
                  onClick={normalizeWeights}
                >
                  归一化到100%
                </button>
              </div>
            </div>

            {detail.factors.map((f) => (
              <div key={f.factor_key} className="mb-3">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs text-muted">
                    {f.factor_label}
                    <code className="ml-2 text-[10px] text-muted">{f.factor_key}</code>
                  </span>
                  <span className="text-xs font-mono">
                    {(editingWeights[f.factor_key] * 100).toFixed(1)}%
                  </span>
                </div>
                <input
                  type="range"
                  min="0"
                  max="100"
                  step="0.5"
                  value={editingWeights[f.factor_key] * 100}
                  onChange={(e) =>
                    handleWeightChange(f.factor_key, Number(e.target.value) / 100)
                  }
                  disabled={isSystem}
                  className="w-full h-1.5 rounded-full appearance-none bg-surface-hover cursor-pointer disabled:opacity-60"
                  style={{ accentColor: "var(--color-primary, #2563eb)" }}
                />
              </div>
            ))}
          </div>

          {/* Thresholds */}
          <div className="glass rounded-xl p-4 mb-4">
            <h4 className="font-medium text-sm mb-2">评级阈值</h4>
            <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
              {editingThresholds.map((t, i) => (
                <div key={i} className="flex items-center gap-2">
                  <input
                    type="number"
                    className="w-20 px-2 py-1 text-xs rounded border border-border bg-surface"
                    value={t.max}
                    onChange={(e) => {
                      if (isSystem) return;
                      const next = [...editingThresholds];
                      next[i] = { ...next[i], max: Number(e.target.value) };
                      setEditingThresholds(next);
                    }}
                    disabled={isSystem}
                  />
                  <span className="text-xs text-muted">→</span>
                  <span className="text-xs font-medium">{t.label}</span>
                </div>
              ))}
            </div>
          </div>

          {/* Actions */}
          <div className="flex gap-3 mb-4">
            {isOwn && (
              <button
                className="px-4 py-2 rounded-lg bg-primary text-white text-sm hover:bg-primary/90 disabled:opacity-50"
                onClick={handleSave}
                disabled={saving}
              >
                {saving ? "保存中..." : "💾 保存修改"}
              </button>
            )}
            <button
              className="px-4 py-2 rounded-lg bg-amber-500 text-white text-sm hover:bg-amber-600 disabled:opacity-50"
              onClick={handleCompute}
              disabled={computing}
            >
              {computing ? "计算中..." : "🔄 计算全部评级"}
            </button>
            {isOwn && (
              <button
                className="px-4 py-2 rounded-lg bg-red-500 text-white text-sm hover:bg-red-600"
                onClick={async () => {
                  if (!confirm("确认删除此模板？")) return;
                  const r = await deleteRatingTemplate(selectedId!);
                  if (r?.deleted) {
                    setMessage("已删除");
                    setDetail(null);
                    setSelectedId(null);
                    loadTemplates();
                  }
                }}
              >
                🗑️ 删除
              </button>
            )}
          </div>

          {/* Messages */}
          {message && (
            <div className="glass rounded-xl p-3 mb-4 text-sm text-emerald-700 bg-emerald-50 dark:bg-emerald-900/20 dark:text-emerald-400">
              {message}
            </div>
          )}

          {/* Compute results */}
          {computeResult && (
            <div className="glass rounded-xl p-4 mb-4">
              <h4 className="font-medium text-sm mb-2">计算结果</h4>
              {computeResult.total_rated != null && (
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
                  {(computeResult.distribution || []).map((d: { category: string; count: number }) => (
                    <div key={d.category} className="text-center p-2 rounded-lg bg-surface">
                      <div className="text-xs text-muted">{d.category}</div>
                      <div className="text-xl font-bold">{d.count}</div>
                    </div>
                  ))}
                </div>
              )}
              {computeResult.overall_score != null && (
                <div className="text-center p-2">
                  <span className="text-lg font-bold">{computeResult.category}</span>
                  <span className="text-sm text-muted ml-2">({computeResult.overall_score})</span>
                </div>
              )}
            </div>
          )}
        </>
      )}

      {!detail && !loading && (
        <div className="text-center py-12 text-muted text-sm">选择一个模板以编辑</div>
      )}
    </section>
  );
}
