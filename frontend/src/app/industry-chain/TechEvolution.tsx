/**
 * 光通信关键技术演进时间线
 */

const EVOLUTION_TRACKS = [
  {
    title: "光纤涂层直径",
    stages: [
      { label: "250μm", status: "current", desc: "ITU-T G.652/G.657 标准，数十年验证" },
      { label: "200μm", status: "ramping", desc: "IEC 60793-2-50 认证，大批量部署" },
      { label: "150μm", status: "future", desc: "研发试产阶段，光缆密度提升64%" },
    ],
  },
  {
    title: "光纤类型演进",
    stages: [
      { label: "G.652 标准单模", status: "current", desc: "主流长途/城域光纤" },
      { label: "G.657 抗弯 / PM保偏", status: "ramping", desc: "数据中心高密度布线 / 相干通信" },
      { label: "空芯光纤 (HCF)", status: "future", desc: "延迟降低30%+，实验阶段" },
    ],
  },
  {
    title: "光模块接口",
    stages: [
      { label: "8通道 (1.6T)", status: "current", desc: "OSFP / QSFP-DD，当前主流" },
      { label: "16通道 (3.2T)", status: "ramping", desc: "OSFP-XD，下一代标准" },
      { label: "32-64通道 (6.4T+)", status: "future", desc: "NPO/CPO板载光纤阵列" },
    ],
  },
  {
    title: "封装演进",
    stages: [
      { label: "可插拔 (QSFP/OSFP)", status: "current", desc: "热插拔，生态开放" },
      { label: "LPO → NPO", status: "ramping", desc: "线性可插拔 / 近封装光学" },
      { label: "CPO → OIO", status: "future", desc: "共封装<5pJ/bit / 片内光互联" },
    ],
  },
  {
    title: "激光器速率",
    stages: [
      { label: "100G/通道 EML", status: "current", desc: "800G 模块主流方案" },
      { label: "200G/通道 EML", status: "ramping", desc: "Lumentum独家量产，1.6T核心" },
      { label: "200G+ 相干集成", status: "future", desc: "片上激光器异质集成" },
    ],
  },
  {
    title: "调制器材料",
    stages: [
      { label: "体铌酸锂 / InP", status: "current", desc: "传统方案，成熟可靠" },
      { label: "SOI 硅光 MZM", status: "ramping", desc: "CMOS兼容，成本优势" },
      { label: "TFLN → BTO", status: "future", desc: "薄膜铌酸锂 / 超高带宽新材料" },
    ],
  },
  {
    title: "电SerDes速率",
    stages: [
      { label: "112G PAM4", status: "current", desc: "800G模块电接口" },
      { label: "224G PAM4", status: "ramping", desc: "1.6T/3.2T模块电接口" },
      { label: "448G (研发中)", status: "future", desc: "下一代电接口标准" },
    ],
  },
];

const STATUS_STYLES: Record<string, { bg: string; dot: string; border: string }> = {
  current: {
    bg: "var(--card-1)",
    dot: "var(--up)",
    border: "var(--up)",
  },
  ramping: {
    bg: "var(--card-3)",
    dot: "var(--down)",
    border: "var(--down)",
  },
  future: {
    bg: "var(--card-4)",
    dot: "var(--primary)",
    border: "var(--primary)",
  },
};

const STATUS_LABELS: Record<string, string> = {
  current: "当前主流",
  ramping: "规模爬坡",
  future: "前沿研发",
};

export default function TechEvolution() {
  return (
    <div className="glass rounded-xl p-5">
      <h2 className="text-lg font-semibold mb-1">技术演进时间线</h2>
      <p className="text-xs text-muted mb-5">
        基于 OFC 2026、LightCounting、Cignal AI、Yole 等行业报告
      </p>

      {/* Legend */}
      <div className="flex gap-4 mb-5 text-xs">
        {Object.entries(STATUS_LABELS).map(([key, label]) => (
          <div key={key} className="flex items-center gap-1.5">
            <span
              className="w-2.5 h-2.5 rounded-full"
              style={{ background: STATUS_STYLES[key].dot }}
            />
            <span className="text-muted">{label}</span>
          </div>
        ))}
      </div>

      {/* Tracks */}
      <div className="space-y-4">
        {EVOLUTION_TRACKS.map((track) => (
          <div key={track.title}>
            <div className="text-sm font-medium mb-2">{track.title}</div>
            <div className="grid grid-cols-3 gap-2">
              {track.stages.map((stage) => {
                const style = STATUS_STYLES[stage.status];
                return (
                  <div
                    key={stage.label}
                    className="rounded-lg p-2.5 border text-xs"
                    style={{
                      background: style.bg,
                      borderColor: style.border,
                      borderLeftWidth: 3,
                    }}
                  >
                    <div
                      className="font-semibold mb-0.5"
                      style={{ color: "var(--ink)" }}
                    >
                      {stage.label}
                    </div>
                    <div className="text-muted leading-tight">{stage.desc}</div>
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
