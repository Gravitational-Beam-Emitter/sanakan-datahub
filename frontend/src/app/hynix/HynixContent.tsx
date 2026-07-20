"use client";

import { useState, useEffect } from "react";
import type {
  HynixArbitrageSnapshot,
  HynixArbitrageHistoryPoint,
  HynixInstrument,
} from "@/lib/api";
import {
  fetchHynixInstruments,
  fetchHynixArbitrageHistory,
  fetchHynixFXLatest,
  fetchHynixArbitrageByDate,
  fetchHynixAvailableDates,
} from "@/lib/api";
import HynixArbTable from "./HynixArbTable";
import HynixPremiumChart from "./HynixPremiumChart";
import KrLeverageContent from "./KrLeverageContent";

export default function HynixContent({
  snapshot,
  availableDates: initialDates,
  targetDate,
}: {
  snapshot: HynixArbitrageSnapshot;
  availableDates: string[];
  targetDate: string;
}) {
  const [tab, setTab] = useState<
    "arbitrage" | "adr" | "leverage" | "kr-leverage" | "guide"
  >("arbitrage");
  const [selectedDate, setSelectedDate] = useState(targetDate);
  const [currentSnapshot, setCurrentSnapshot] = useState(snapshot);
  const [instruments, setInstruments] = useState<HynixInstrument[]>([]);
  const [availableDates, setAvailableDates] = useState(initialDates);
  const [loading, setLoading] = useState(false);

  // Premium history for chart
  const [adrHistory, setAdrHistory] = useState<HynixArbitrageHistoryPoint[]>([]);
  const [hkHistory, setHkHistory] = useState<HynixArbitrageHistoryPoint[]>([]);
  const [fxRates, setFxRates] = useState<Record<string, number>>({});

  useEffect(() => {
    fetchHynixInstruments().then(setInstruments).catch(() => {});
    fetchHynixFXLatest()
      .then((fx) => {
        if (fx) setFxRates(fx.rates);
      })
      .catch(() => {});
    fetchHynixArbitrageHistory("SKHY", undefined, undefined, 60)
      .then(setAdrHistory)
      .catch(() => {});
    fetchHynixArbitrageHistory("7709.HK", undefined, undefined, 60)
      .then(setHkHistory)
      .catch(() => {});
  }, []);

  // Reload data when date changes
  const handleDateChange = async (date: string) => {
    setSelectedDate(date);
    setLoading(true);
    const [newSnapshot, dates] = await Promise.all([
      fetchHynixArbitrageByDate(date).catch(() => null),
      fetchHynixAvailableDates().catch(() => []),
    ]);
    if (newSnapshot) setCurrentSnapshot(newSnapshot);
    if (dates.length) setAvailableDates(dates);
    setLoading(false);
  };

  const tabs = [
    { key: "arbitrage" as const, label: "折溢价对比" },
    { key: "adr" as const, label: "ADR 机制分析" },
    { key: "leverage" as const, label: "杠杆产品分析" },
    { key: "kr-leverage" as const, label: "韩国散户杠杆" },
    { key: "guide" as const, label: "接入指南" },
  ];

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="h-4 w-48 bg-surface-hover rounded animate-pulse" />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      {/* Tab navigation */}
      <div className="flex gap-1 p-1 glass rounded-xl self-start">
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`text-xs px-3 py-1.5 rounded-lg transition-all ${
              tab === t.key
                ? "bg-primary-a15 text-primary font-medium"
                : "text-muted hover:text-ink"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Date picker for arbitrage tab */}
      {tab === "arbitrage" && (
        <div className="flex items-center gap-2">
          <span className="text-xs text-muted">交易日:</span>
          <select
            value={selectedDate}
            onChange={(e) => handleDateChange(e.target.value)}
            className="text-xs glass rounded-lg px-3 py-1.5 text-ink focus:outline-none"
          >
            {availableDates.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        </div>
      )}

      {/* Tab content */}
      {tab === "arbitrage" && (
        <div className="flex flex-col gap-6">
          {/* FX rate bar */}
          <div className="flex flex-wrap gap-3 text-xs text-muted">
            <span className="glass rounded-lg px-3 py-1">
              USD/KRW = {fxRates["USDKRW"]?.toFixed(1) || currentSnapshot.fx_rates?.["USDKRW"]?.toFixed(1) || "—"}
            </span>
            <span className="glass rounded-lg px-3 py-1">
              HKD/KRW = {fxRates["HKDKRW"]?.toFixed(1) || currentSnapshot.fx_rates?.["HKDKRW"]?.toFixed(1) || "—"}
            </span>
            <span className="glass rounded-lg px-3 py-1">
              Base: {currentSnapshot.base_ticker} @ {currentSnapshot.base_price_krw.toLocaleString()} KRW
            </span>
          </div>

          {/* Arbitrage table */}
          <HynixArbTable instruments={currentSnapshot.instruments} />

          {/* Premium time series chart */}
          <div className="glass rounded-xl p-4 sm:p-6">
            <h3 className="text-sm font-medium text-ink mb-4">
              各市场折溢价时间序列（折算为等效1股SK Hynix KRW价格）
            </h3>
            <HynixPremiumChart
              adrHistory={adrHistory}
              hkHistory={hkHistory}
              basePrice={currentSnapshot.base_price_krw}
            />
          </div>

          {/* Instrument details */}
          <div className="glass rounded-xl p-4 sm:p-6">
            <h3 className="text-sm font-medium text-ink mb-4">跟踪标的详情</h3>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-border">
                    <th className="text-left py-2 px-3 text-muted font-medium">Ticker</th>
                    <th className="text-left py-2 px-3 text-muted font-medium">名称</th>
                    <th className="text-left py-2 px-3 text-muted font-medium">市场</th>
                    <th className="text-left py-2 px-3 text-muted font-medium">类型</th>
                    <th className="text-right py-2 px-3 text-muted font-medium">杠杆</th>
                    <th className="text-left py-2 px-3 text-muted font-medium">说明</th>
                  </tr>
                </thead>
                <tbody>
                  {instruments.map((inst) => (
                    <tr key={inst.ticker} className="border-b border-border/50 hover:bg-surface-hover">
                      <td className="py-2 px-3 font-mono text-ink">{inst.ticker}</td>
                      <td className="py-2 px-3 text-ink">{inst.name}</td>
                      <td className="py-2 px-3 text-muted">{inst.market}</td>
                      <td className="py-2 px-3">
                        <span className={`text-xs px-1.5 py-0.5 rounded-full ${
                          inst.instrument_type === "stock"
                            ? "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400"
                            : inst.instrument_type === "adr"
                            ? "bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-400"
                            : inst.instrument_type === "etp"
                            ? "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400"
                            : "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400"
                        }`}>
                          {inst.instrument_type.toUpperCase()}
                        </span>
                      </td>
                      <td className="py-2 px-3 text-right font-mono">
                        {inst.leverage > 0 ? `${inst.leverage}x` : `${inst.leverage}x`}
                      </td>
                      <td className="py-2 px-3 text-muted text-xs max-w-xs truncate">
                        {inst.note}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* Korean Retail Leverage tab */}
      {tab === "kr-leverage" && <KrLeverageContent />}

      {/* ADR Analysis tab */}
      {tab === "adr" && <AdrAnalysis />}

      {/* Leveraged Product Analysis tab */}
      {tab === "leverage" && <LeverageAnalysis />}

      {/* Integration Guide tab */}
      {tab === "guide" && <IntegrationGuide />}
    </div>
  );
}

/* ── ADR Analysis Tab ── */

function AdrAnalysis() {
  return (
    <div className="flex flex-col gap-6">
      {/* Key Timeline */}
      <div className="glass rounded-xl p-4 sm:p-6">
        <h3 className="text-sm font-medium text-ink mb-4">ADR 关键时间线</h3>
        <div className="space-y-3">
          {[
            {
              date: "2026-06-30",
              event: "SEC 提交 F-1/F-6 注册文件",
              detail:
                "F-6 注册了 ~17.8 亿份 ADR（占总股本 25%），远超实际发行量（2.5%），为后续转换预留空间。",
            },
            {
              date: "2026-07-07",
              event: "簿记建档开始",
              detail:
                "Baillie Gifford、Coatue、Situational Awareness Partners 等基石投资者意向认购 ~$7B。整体账簿 >7x 超额认购。",
            },
            {
              date: "2026-07-09",
              event: "ADR 最终定价 $149",
              detail:
                "溢价约 3.1%（相对于隐含韩国股价）。总募资 ~$26.5B，为史上最大非美国公司赴美上市。",
            },
            {
              date: "2026-07-10",
              event: "Nasdaq 上市交易（SKHYV 临时代码）",
              detail:
                "崔泰源会长敲钟。首日交易使用 when-issued 代码 SKHYV，10 ADR = 1 股韩国普通股。",
            },
            {
              date: "2026-07-13",
              event: "SKHY 正式代码启用",
              detail: "常规交易开始，ADR 溢价从首日 ~16% 一度飙升至 51%。",
            },
            {
              date: "2026-07-14",
              event: "结算日（pay-in）",
              detail:
                "T+2 结算完成，ADR 正式发行。Citi（托管行）可能限制 ADR 创设以维持稀缺性溢价。",
            },
            {
              date: "2026-07-29（预计）",
              event: "KSD 登记 + KOSPI 新股上市",
              detail:
                "新股发行底层的韩国股票将在韩国证券托管院登记，理论上开启双向转换窗口。但 Citi 可能继续限制创设量。",
            },
            {
              date: "2026 Q4（预计）",
              event: "ADR 创设第二阶段",
              detail:
                "Citi 可能逐步释放更多 ADR 创设额度，溢价有望收窄。完全开放需 3-6 个月。",
            },
            {
              date: "2026 年底（预期）",
              event: "股票拆分（10:1 → 1:1）",
              detail:
                "预计 11 月临时股东大会表决，12 月完成拆分。拆分后 1 ADR = 1 普通股，ADR 定价将更接近美国同业。",
            },
          ].map((item, i) => (
            <div key={i} className="flex gap-4">
              <div className="flex flex-col items-center">
                <div className="w-2 h-2 rounded-full bg-primary mt-1.5" />
                {i < 8 && <div className="w-px flex-1 bg-border mt-1" />}
              </div>
              <div className="flex-1 pb-4">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-mono text-primary">{item.date}</span>
                  <span className="text-sm font-medium text-ink">{item.event}</span>
                </div>
                <p className="text-xs text-muted mt-1">{item.detail}</p>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* ADR Mechanism */}
      <div className="glass rounded-xl p-4 sm:p-6">
        <h3 className="text-sm font-medium text-ink mb-4">ADR 折溢价机制：为什么溢价可以持续？</h3>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <div className="space-y-4">
            <div>
              <h4 className="text-xs font-medium text-ink">单向转换瓶颈</h4>
              <p className="text-xs text-muted mt-1">
                ADR → 韩国股票：<span className="text-up">可随时赎回</span>（但ADRs溢价时无人会赎回）
                <br />
                韩国股票 → ADR：<span className="text-down font-medium">被冻结/受限</span>（需韩国监管审批 + KSD 登记 + Citi 创设配额）
                <br />
                零售投资者<span className="text-down">完全被排除</span>在转换机制之外，仅机构可参与。
              </p>
            </div>
            <div>
              <h4 className="text-xs font-medium text-ink">供给受限</h4>
              <p className="text-xs text-muted mt-1">
                ADR 初始发行量仅占 SK Hynix 总股本的 <strong>~2.5%</strong>（177.9M ADR）。
                Citi 的 F-6 注册虽然覆盖了 25% 的总股本，但实际创设额度被
                <strong>严格管控</strong>，形成人为供给稀缺。
              </p>
            </div>
          </div>
          <div className="space-y-4">
            <div>
              <h4 className="text-xs font-medium text-ink">卖空约束</h4>
              <p className="text-xs text-muted mt-1">
                新上市 ADR 缺乏可借券 source，<strong>无法卖空</strong>。
                即使有做市商愿意融券，成本极高。这消除了传统套利中"卖高买低"的做空压力。
              </p>
            </div>
            <div>
              <h4 className="text-xs font-medium text-ink">TSMC 先例</h4>
              <p className="text-xs text-muted mt-1">
                TSMC ADR 长期维持 <strong>10-30% 溢价</strong>，证明了受限转换条件下溢价可以
                <strong>结构性永续存在</strong>，而非短期异常。SK Hynix
                的溢价格局可能类似。
              </p>
            </div>
          </div>
        </div>

        <div className="mt-6 p-4 rounded-lg bg-amber-50 dark:bg-amber-900/10 border border-amber-200 dark:border-amber-900/30">
          <p className="text-xs text-amber-800 dark:text-amber-300 font-medium">核心结论</p>
          <p className="text-xs text-amber-700 dark:text-amber-400 mt-1">
            ADR 溢价不是"定价错误"，而是<strong>结构性流动性溢价</strong>。
            美国投资者为获取 AI 存储龙头敞口支付便利性溢价 + 供给稀缺溢价 + 做空保护溢价。
            只有当 Citi 大量释放 ADR 创设额度（预计 3-6 个月）或韩国股票拆分完成（年底）后，溢价才可能系统性收窄。
          </p>
        </div>
      </div>

      {/* ADR Calculation Details */}
      <div className="glass rounded-xl p-4 sm:p-6">
        <h3 className="text-sm font-medium text-ink mb-4">ADR 折算公式</h3>
        <div className="space-y-3 text-xs">
          <div className="font-mono bg-surface rounded-lg p-3">
            <p className="text-muted"># ADR 价格 → 等效 1 股韩国股票价格</p>
            <p className="text-ink mt-1">
              <span className="text-primary">equivalent_krw</span> = ADR_price_usd × USDKRW × 10
            </p>
            <p className="text-muted mt-2"># 溢价率</p>
            <p className="text-ink mt-1">
              <span className="text-primary">premium_pct</span> = (equivalent_krw / 000660.KS_price - 1) × 100
            </p>
          </div>
          <p className="text-muted">
            其中 ×10 是因为 ADR 比率为 <strong>10:1</strong>（10 份 ADR = 1 股韩国普通股）。
            每股 ADR 代表 0.1 股 SK Hynix 的权益。
          </p>
          <p className="text-muted">
            ADR 与韩国正股享有完全相同的投票权和分红权。股息以美元派发，由托管行 Citi
            代扣韩国股息税后转付。
          </p>
        </div>
      </div>
    </div>
  );
}

/* ── Leveraged Product Analysis Tab ── */

function LeverageAnalysis() {
  return (
    <div className="flex flex-col gap-6">
      {/* Product overview */}
      <div className="glass rounded-xl p-4 sm:p-6">
        <h3 className="text-sm font-medium text-ink mb-4">追踪的杠杆产品</h3>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-border">
                <th className="text-left py-2 px-3 text-muted font-medium">产品</th>
                <th className="text-left py-2 px-3 text-muted font-medium">底层资产</th>
                <th className="text-right py-2 px-3 text-muted font-medium">杠杆</th>
                <th className="text-left py-2 px-3 text-muted font-medium">复制方式</th>
                <th className="text-right py-2 px-3 text-muted font-medium">费率</th>
                <th className="text-left py-2 px-3 text-muted font-medium">风险</th>
              </tr>
            </thead>
            <tbody>
              <tr className="border-b border-border/50">
                <td className="py-2 px-3 font-medium text-ink">7709.HK<br /><span className="text-muted">CSOP SK Hynix 2x LEP</span></td>
                <td className="py-2 px-3">SK Hynix (000660.KS)</td>
                <td className="py-2 px-3 text-right font-mono font-medium text-up">2x</td>
                <td className="py-2 px-3">掉期合成复制<br /><span className="text-muted">(swap-based synthetic)</span></td>
                <td className="py-2 px-3 text-right">~2.0% / 年</td>
                <td className="py-2 px-3 text-xs text-down">
                  波动率衰减<br />对手方风险<br />流动性风险
                </td>
              </tr>
              <tr className="border-b border-border/50">
                <td className="py-2 px-3 font-medium text-ink">0193T0.KS<br /><span className="text-muted">KODEX SK Hynix 2x</span></td>
                <td className="py-2 px-3">SK Hynix (000660.KS)</td>
                <td className="py-2 px-3 text-right font-mono font-medium text-up">2x</td>
                <td className="py-2 px-3">期货 + 掉期<br /><span className="text-muted">(futures-based)</span></td>
                <td className="py-2 px-3 text-right">~0.95% / 年</td>
                <td className="py-2 px-3 text-xs text-down">
                  波动率衰减<br />期货展期成本
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      {/* Volatility Decay Math */}
      <div className="glass rounded-xl p-4 sm:p-6">
        <h3 className="text-sm font-medium text-ink mb-4">杠杆 ETF 波动率衰减（Volatility Decay）</h3>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <div>
            <h4 className="text-xs font-medium text-ink mb-3">经典两日示例</h4>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-border">
                    <th className="text-left py-1.5 px-2 text-muted">日</th>
                    <th className="text-right py-1.5 px-2 text-muted">标的涨跌</th>
                    <th className="text-right py-1.5 px-2 text-muted">标的价格</th>
                    <th className="text-right py-1.5 px-2 text-muted">2x ETF 涨跌</th>
                    <th className="text-right py-1.5 px-2 text-muted">ETF 价格</th>
                  </tr>
                </thead>
                <tbody>
                  <tr className="border-b border-border/50">
                    <td className="py-1.5 px-2 text-muted">起点</td>
                    <td className="py-1.5 px-2 text-right">—</td>
                    <td className="py-1.5 px-2 text-right font-mono text-ink">$100.00</td>
                    <td className="py-1.5 px-2 text-right">—</td>
                    <td className="py-1.5 px-2 text-right font-mono text-ink">$100.00</td>
                  </tr>
                  <tr className="border-b border-border/50">
                    <td className="py-1.5 px-2 text-muted">D1</td>
                    <td className="py-1.5 px-2 text-right text-up">+10%</td>
                    <td className="py-1.5 px-2 text-right font-mono text-ink">$110.00</td>
                    <td className="py-1.5 px-2 text-right text-up font-medium">+20%</td>
                    <td className="py-1.5 px-2 text-right font-mono text-ink">$120.00</td>
                  </tr>
                  <tr>
                    <td className="py-1.5 px-2 text-muted">D2</td>
                    <td className="py-1.5 px-2 text-right text-down">−9.09%</td>
                    <td className="py-1.5 px-2 text-right font-mono text-ink">$100.00</td>
                    <td className="py-1.5 px-2 text-right text-down font-medium">−18.18%</td>
                    <td className="py-1.5 px-2 text-right font-mono text-down font-medium">$98.18</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p className="text-xs text-muted mt-2">
              <strong>标的归零</strong>（$100→$100），但 2x ETF 已亏损 <strong>−1.82%</strong>。这就是纯粹数学造成的衰减——不含任何费用。
            </p>
          </div>

          <div>
            <h4 className="text-xs font-medium text-ink mb-3">衰减公式</h4>
            <div className="font-mono bg-surface rounded-lg p-3 text-xs space-y-2">
              <p className="text-muted"># 年化波动率衰减近似公式</p>
              <p className="text-ink">
                <span className="text-primary">Drag</span> ≈ (k² × σ²) / 2
              </p>
              <p className="text-muted mt-2">其中：</p>
              <p className="text-ink">
                k = 杠杆倍数（2x → k=2）<br />
                σ = 标的年化波动率
              </p>
            </div>

            <h4 className="text-xs font-medium text-ink mt-4 mb-2">SK Hynix 的实际衰减估算</h4>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-border">
                    <th className="text-left py-1.5 px-2 text-muted">假设波动率</th>
                    <th className="text-right py-1.5 px-2 text-muted">预期年化衰减</th>
                    <th className="text-left py-1.5 px-2 text-muted">适用场景</th>
                  </tr>
                </thead>
                <tbody>
                  <tr className="border-b border-border/50">
                    <td className="py-1.5 px-2 font-mono">40%</td>
                    <td className="py-1.5 px-2 text-right font-mono text-down">~32%</td>
                    <td className="py-1.5 px-2 text-muted">平稳期（标的横盘）</td>
                  </tr>
                  <tr className="border-b border-border/50">
                    <td className="py-1.5 px-2 font-mono">60%</td>
                    <td className="py-1.5 px-2 text-right font-mono text-down">~72%</td>
                    <td className="py-1.5 px-2 text-muted">正常波动（标的年化波动 60%）</td>
                  </tr>
                  <tr>
                    <td className="py-1.5 px-2 font-mono">80%+</td>
                    <td className="py-1.5 px-2 text-right font-mono text-down">~128%</td>
                    <td className="py-1.5 px-2 text-muted">极端波动（如2026年7月暴跌）</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p className="text-xs text-muted mt-2">
              SK Hynix 作为高贝塔半导体股，年化波动率通常在 50-70%。这意味着持有 2x 杠杆产品
              一年，仅波动率衰减就可能侵蚀 <strong>50-100%</strong> 的收益（假设标的横盘）。
            </p>
          </div>
        </div>
      </div>

      {/* Compounding effect */}
      <div className="glass rounded-xl p-4 sm:p-6">
        <h3 className="text-sm font-medium text-ink mb-4">每日重置的复利效应</h3>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <div>
            <h4 className="text-xs font-medium text-ink mb-2">趋势市场 → 复利加成（&gt;2x）</h4>
            <div className="font-mono bg-surface rounded-lg p-3 text-xs">
              <p className="text-muted"># 标的连续10天每天 +2%</p>
              <p className="text-ink mt-1">标的 10 日回报：+21.9%</p>
              <p className="text-up font-medium">2x ETF 10 日回报：+48.0%（&gt;2×21.9%）</p>
            </div>
            <p className="text-xs text-muted mt-2">
              每天涨 → 每天放大仓位 → 仓位越来越大 → 收益滚雪球。单边上涨是杠杆 ETF 的最佳环境。
            </p>
          </div>
          <div>
            <h4 className="text-xs font-medium text-ink mb-2">震荡市场 → 复利损耗（&lt;2x）</h4>
            <div className="font-mono bg-surface rounded-lg p-3 text-xs">
              <p className="text-muted"># 标的交替 +2% / -2% 各5天</p>
              <p className="text-ink mt-1">标的 10 日回报：−0.2%</p>
              <p className="text-down font-medium">2x ETF 10 日回报：−0.8%（4× 标的亏损）</p>
            </div>
            <p className="text-xs text-muted mt-2">
              每天涨跌交替 → 持续"高买低卖" → 隐性损耗远超标的波动。震荡市是杠杆 ETF 的杀手。
            </p>
          </div>
        </div>
      </div>

      {/* Additional costs */}
      <div className="glass rounded-xl p-4 sm:p-6">
        <h3 className="text-sm font-medium text-ink mb-4">额外成本叠加</h3>
        <div className="space-y-3 text-xs">
          <div className="flex gap-3">
            <span className="text-down font-mono w-24 shrink-0">掉期/融资成本</span>
            <span className="text-muted">
              合成复制型产品（如 7709.HK）需支付掉期对手方融资利息。单一个股掉期成本可能高达
              <strong> 8-20%+ / 年</strong>，远超指数 ETF 的 1-3%。这是杠杆产品最大的隐性成本。
            </span>
          </div>
          <div className="flex gap-3">
            <span className="text-down font-mono w-24 shrink-0">管理费</span>
            <span className="text-muted">
              7709.HK 全年经常性开支比率 <strong>~2.00%</strong>；0193T0.KS 约 <strong>~0.95%</strong>。
            </span>
          </div>
          <div className="flex gap-3">
            <span className="text-down font-mono w-24 shrink-0">期货展期</span>
            <span className="text-muted">
              期货型产品（0193T0.KS）在 contango 市场需承担正展期成本；backwardation 时可获展期收益。
            </span>
          </div>
          <div className="flex gap-3">
            <span className="text-down font-mono w-24 shrink-0">对手方风险</span>
            <span className="text-muted">
              7709.HK 为合成掉期结构，若掉期对手方（通常是 CSOP 关联方或投行）违约，ETP 可能面临重大损失。无实物持仓保护。
            </span>
          </div>
        </div>

        <div className="mt-6 p-4 rounded-lg bg-red-50 dark:bg-red-900/10 border border-red-200 dark:border-red-900/30">
          <p className="text-xs text-red-800 dark:text-red-300 font-medium">风险提示</p>
          <p className="text-xs text-red-700 dark:text-red-400 mt-1">
            <strong>杠杆单股产品不适合持有一日以上。</strong>
            2026 年 7 月，7709.HK 在 12 个交易日内从 193 HKD 暴跌至 65 HKD（−66%），远超 2× 标的跌幅。
            每日重置 + 波动率衰减 + 掉期成本叠加 + 标的暴跌 = <strong>可能在一天内损失大部分或全部投资。</strong>
            韩国金融监管已启动对单股杠杆 ETF 的全面整治，可能出台降低杠杆倍数、限制日内波幅等措施。
          </p>
        </div>
      </div>
    </div>
  );
}

/* ── Integration Guide Tab ── */

function IntegrationGuide() {
  return (
    <div className="flex flex-col gap-6">
      <div className="glass rounded-xl p-4 sm:p-6">
        <h3 className="text-sm font-medium text-ink mb-4">数据接入指南</h3>
        <p className="text-xs text-muted mb-6">
          海力士跨市场套利数据通过 REST API（端口 8008）提供。以下是如何在前端页面或外部系统中使用该数据。
        </p>

        {/* API endpoints */}
        <h4 className="text-xs font-medium text-ink mb-3">API 端点</h4>
        <div className="overflow-x-auto mb-6">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-border">
                <th className="text-left py-2 px-3 text-muted font-medium">方法</th>
                <th className="text-left py-2 px-3 text-muted font-medium">路径</th>
                <th className="text-left py-2 px-3 text-muted font-medium">说明</th>
              </tr>
            </thead>
            <tbody>
              {[
                ["GET", "/api/v1/arbitrage/latest", "最新折溢价快照（所有标的）"],
                ["GET", "/api/v1/arbitrage/{date}", "指定日期的折溢价数据"],
                ["GET", "/api/v1/arbitrage/{ticker}/history", "某标的的折溢价时间序列"],
                ["GET", "/api/v1/instruments", "跟踪标的列表及属性"],
                ["GET", "/api/v1/prices/{ticker}", "某标的的价格历史"],
                ["GET", "/api/v1/prices?date={date}", "某日所有标的价格"],
                ["GET", "/api/v1/fx/latest", "最新汇率"],
                ["GET", "/api/v1/dates", "有数据的交易日列表"],
                ["POST", "/api/v1/fetch", "触发数据拉取"],
              ].map(([method, path, desc], i) => (
                <tr key={i} className="border-b border-border/50">
                  <td className="py-2 px-3">
                    <span className={`text-xs px-1.5 py-0.5 rounded-full font-mono ${
                      method === "GET" ? "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400" : "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400"
                    }`}>{method}</span>
                  </td>
                  <td className="py-2 px-3 font-mono text-ink">{path}</td>
                  <td className="py-2 px-3 text-muted">{desc}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Response format */}
        <h4 className="text-xs font-medium text-ink mb-3">关键响应格式</h4>
        <div className="font-mono bg-surface rounded-lg p-4 text-xs overflow-x-auto mb-6">
          <pre className="text-muted">{`// GET /api/v1/arbitrage/latest 响应
{
  "date": "2026-07-15",
  "base_ticker": "000660.KS",
  "base_price_krw": 2082000,
  "fx_rates": { "USDKRW": 1490.0, "HKDKRW": 189.6 },
  "instruments": [
    {
      "ticker": "SKHY",           // 标的代码
      "name": "SK hynix ADR (US)",// 名称
      "market": "US",             // 市场: KR/US/HK
      "currency": "USD",          // 计价货币
      "instrument_type": "adr",   // 类型: stock/adr/etp/etf
      "leverage": 1.0,            // 杠杆倍数 (1x/2x)
      "price_local": 185.27,      // 本地货币价格
      "price_krw": 276047,        // KRW 折算价
      "equivalent_krw_per_share": 2760467, // 等效1股SKH的KRW成本
      "premium_pct_vs_base": 32.59,        // 相对KR正股的溢价%
      "nav_premium_pct": null,    // ETF价格相对NAV的溢价%
      "tracking_ratio": 0.1       // 1单位标的=多少股SKH
    }
  ]
}`}</pre>
        </div>

        {/* Frontend integration */}
        <h4 className="text-xs font-medium text-ink mb-3">在前端页面中展示</h4>
        <div className="font-mono bg-surface rounded-lg p-4 text-xs overflow-x-auto mb-6">
          <pre className="text-muted">{`// 1. 在你的 lib/api.ts 中添加（或直接 fetch）
const HYNIX_API = "http://YOUR_SERVER:8008";

export async function getHynixArbitrage() {
  const res = await fetch(HYNIX_API + "/api/v1/arbitrage/latest");
  return res.json();
}

// 2. 在页面组件中使用
import { getHynixArbitrage } from "@/lib/api";

export default async function HynixPage() {
  const data = await getHynixArbitrage();

  return (
    <div>
      {/* 折溢价对比表 */}
      <table>
        {data.instruments.map(inst => (
          <tr key={inst.ticker}>
            <td>{inst.name}</td>
            <td>{inst.price_local.toLocaleString()} {inst.currency}</td>
            <td className={inst.premium_pct > 0 ? "text-up" : "text-down"}>
              {inst.premium_pct.toFixed(2)}%
            </td>
          </tr>
        ))}
      </table>

      {/* 时间序列图 — 使用折溢价历史 */}
      {/* GET /api/v1/arbitrage/SKHY/history 获取 ADR 溢价历史 */}
    </div>
  );
}`}</pre>
        </div>

        {/* Deployment */}
        <h4 className="text-xs font-medium text-ink mb-3">部署步骤</h4>
        <ol className="space-y-3 text-xs text-muted list-decimal list-inside ml-2">
          <li>
            <strong className="text-ink">启动 API 服务：</strong>
            <code className="font-mono bg-surface px-1 py-0.5 rounded text-ink">
              python -m uvicorn hynix.api:app --host 0.0.0.0 --port 8008
            </code>
          </li>
          <li>
            <strong className="text-ink">初始化数据：</strong>
            <code className="font-mono bg-surface px-1 py-0.5 rounded text-ink">
              python -m hynix.pipeline --init
            </code>
          </li>
          <li>
            <strong className="text-ink">设置定时拉取（可选）：</strong>
            <br />
            使用 cron 或 systemd timer 在每个交易日 16:30 KST 运行：
            <code className="font-mono bg-surface px-1 py-0.5 rounded text-ink">
              python -m hynix.pipeline
            </code>
          </li>
          <li>
            <strong className="text-ink">前端配置环境变量：</strong>
            <br />
            <code className="font-mono bg-surface px-1 py-0.5 rounded text-ink">
              NEXT_PUBLIC_HYNIX_API_URL=http://YOUR_SERVER:8008
            </code>
          </li>
        </ol>

        <div className="mt-6 p-4 rounded-lg bg-blue-50 dark:bg-blue-900/10 border border-blue-200 dark:border-blue-900/30">
          <p className="text-xs text-blue-800 dark:text-blue-300 font-medium">关键展示建议</p>
          <ul className="text-xs text-blue-700 dark:text-blue-400 mt-2 space-y-1 list-disc list-inside">
            <li><strong>折溢价表格：</strong>按 premium_pct 降序排列，正值标红、负值标绿</li>
            <li><strong>折溢价时序图：</strong>用折线图展示 ADR 和 ETF 溢价的时间序列，标注 0% 基准线</li>
            <li><strong>标的卡片：</strong>每个标的一个卡片，显示最新价格、杠杆倍数、折溢价</li>
            <li><strong>数据时效说明：</strong>明确标注不同市场价格存在时区差异，溢价可能部分反映异步定价</li>
            <li><strong>ADR 机制说明：</strong>务必向用户解释 ADR 溢价的结构性原因（单向转换、供给受限），避免误解为"定价错误"</li>
            <li><strong>杠杆风险提示：</strong>对杠杆产品务必展示风险提示，包括波动率衰减说明</li>
          </ul>
        </div>
      </div>
    </div>
  );
}
