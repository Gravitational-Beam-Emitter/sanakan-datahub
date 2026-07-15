import ThemeToggle from "@/components/ThemeToggle";
import NavBar from "@/components/NavBar";
import OpticalChainGraph, { ChainLegend } from "./OpticalChainGraph";
import TechEvolution from "./TechEvolution";

export default function IndustryChainPage() {
  return (
    <div className="flex flex-col flex-1 max-w-7xl mx-auto w-full px-4 py-6 sm:px-6 sm:py-8 gap-6">
      <header className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-xl font-semibold">光通信产业链</h1>
          <p className="text-sm text-muted mt-1">
            从高纯石英砂到AI GPU集群 — 全球光通信产业全链图谱
          </p>
        </div>
        <div className="flex items-center gap-3">
          <div className="text-xs text-muted">
            收录美股 · 台股 · 日股 · 韩股 · A股 | 30+ 关键公司
          </div>
          <ThemeToggle />
        </div>
      </header>

      <NavBar />

      {/* Legend */}
      <ChainLegend />

      {/* Main Graph */}
      <OpticalChainGraph />

      {/* Tech Evolution Timeline */}
      <TechEvolution />

      {/* Data Notes */}
      <div className="glass rounded-xl p-4 text-xs text-muted space-y-1">
        <p className="font-medium text-sm text-ink mb-2">数据说明</p>
        <p>
          产业链节点标注的公司为各环节全球主要参与者。公司后括号内为股票代码或当地市场代码。
        </p>
        <p>
          数据仅供参考研究，不构成投资建议。
        </p>
      </div>

      <footer className="text-center text-xs text-muted py-4 border-t border-border space-y-1">
        <p>光通信产业链图谱 · 数据仅供参考，不构成投资建议</p>
        <p>个人学习与 Vibe Coding 练习项目，仅供研究用途，非商业用途。</p>
      </footer>
    </div>
  );
}
