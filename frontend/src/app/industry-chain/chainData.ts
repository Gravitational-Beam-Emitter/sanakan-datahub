/**
 * 光通信产业链 — 节点与边定义
 *
 * 4层结构:
 *   Layer 0: 上游原材料 (Upstream Materials)
 *   Layer 1: 光纤预制棒 & 拉丝 (Fiber Preform & Drawing)
 *   Layer 2: 光芯片 & 器件 (Optical Chips & Components)
 *   Layer 3: 光模块 & 封装 (Optical Modules & Packaging)
 *   Layer 4: 系统集成 & 终端 (System Integration & End Users)
 */

export interface ChainNode {
  id: string;
  label: string;
  layer: number;
  /** 关键公司列表 */
  companies: string[];
  /** 技术演进标注 */
  evolution?: string;
  /** 对应概念板块 (cb source indicator_id) */
  cbIndicatorId?: number;
  /** 对应全球个股 indicator_ids */
  opticalIds?: number[];
  /** 材料/技术说明 */
  description?: string;
}

export interface ChainEdge {
  from: string;
  to: string;
  label?: string;
}

export const LAYER_COLORS = [
  "var(--card-0)", // 上游 — blue tint
  "var(--card-1)", // 中上游 — amber tint
  "var(--card-2)", // 中下游 — rose tint
  "var(--card-3)", // 光模块 — green tint
  "var(--card-4)", // 下游 — indigo tint
];

export const LAYER_LABELS = [
  "上游：原材料与衬底",
  "中上游：光纤预制棒 & 拉丝",
  "中下游：光芯片 & 器件",
  "光模块 & 封装",
  "下游：系统集成 & 终端",
];

export const CHAIN_NODES: ChainNode[] = [
  // ═════════════════════════ Layer 0: 上游原材料 ═════════════════════════
  {
    id: "sio2",
    label: "高纯石英砂\nSiO₂ / GeCl₄",
    layer: 0,
    companies: ["菲利华", "石英股份"],
    description: "光纤芯料和包层的基础玻璃原料。GeCl₄ 掺入纤芯提高折射率。",
  },
  {
    id: "inp",
    label: "磷化铟晶圆\nInP Substrate",
    layer: 0,
    companies: ["住友电工", "AXT"],
    description: "DFB/EML激光器衬底。光芯片最核心的上游材料。",
    cbIndicatorId: 512, // 小金属概念
  },
  {
    id: "gaas",
    label: "砷化镓晶圆\nGaAs / SOI",
    layer: 0,
    companies: ["稳懋 (3105)", "三安光电"],
    description: "VCSEL激光器、硅光芯片衬底。",
  },
  {
    id: "linbo3",
    label: "铌酸锂 / TFLN\nLiNbO₃ Electro-Optic",
    layer: 0,
    companies: ["光库科技", "富士通光器件"],
    description: "高速电光调制器材料。从体材料铌酸锂向薄膜铌酸锂(TFLN)演进。",
    evolution: "体材料 → TFLN → BTO",
  },
  {
    id: "rare_earth",
    label: "稀土掺杂\nEr / Yb / Ge",
    layer: 0,
    companies: ["云南锗业"],
    description: "EDFA光纤放大器掺杂元素。",
  },
  {
    id: "ultra_pure_gas",
    label: "高纯气体\nSiCl₄ / Fluorine",
    layer: 0,
    companies: ["空气化工", "林德"],
    description: "MCVD/OVD工艺前驱体。氟掺入包层降低折射率。",
  },

  // ═════════════════════════ Layer 1: 光纤预制棒 & 拉丝 ═════════════════════════
  {
    id: "preform",
    label: "光纤预制棒\nMCVD / OVD / VAD",
    layer: 1,
    companies: ["康宁 (GLW)", "长飞光纤", "古河电工 (5801)", "住友电工 (5802)", "普睿司曼"],
    description: "占成品光纤成本约70%，技术壁垒最高的环节之一。四大工艺：MCVD、OVD、VAD、PCVD。",
    cbIndicatorId: 518, // 光纤概念指数
    opticalIds: [534, 552, 553], // GLW rev, 古河 rev, 住友 rev
  },
  {
    id: "fiber_draw",
    label: "光纤拉丝\n250μm → 200μm → 150μm",
    layer: 1,
    companies: ["康宁 (GLW)", "藤仓 (5803)", "住友电工 (5802)", "OFS"],
    description: "预制棒在1900-2200°C下拉成125μm光纤。涂层直径从传统250μm向200μm/150μm演进，光缆密度提升64%。",
    evolution: "250μm (标准) → 200μm (IEC认证) → 150μm (试产)",
    cbIndicatorId: 518,
  },
  {
    id: "cable_assembly",
    label: "光缆组装\n着色 / 松套管 / 铠装",
    layer: 1,
    companies: ["亨通光电", "中天科技", "烽火通信"],
    description: "多芯光纤集束成缆。AI数据中心光缆用量为传统云的16-36倍。",
    cbIndicatorId: 518,
  },

  // ═════════════════════════ Layer 2: 光芯片 & 器件 ═════════════════════════
  {
    id: "active_chip",
    label: "有源光芯片\nDFB / EML / VCSEL",
    layer: 2,
    companies: ["Lumentum (LITE)", "Coherent (COHR)", "博通 (AVGO)", "源杰科技"],
    description: "电→光转换核心。200G/通道 EML 为当前量产最高速率。800G/1.6T光模块 BOM中光芯片成本>50%。",
    evolution: "100G/ch EML → 200G/ch EML → 200G+ 相干",
    cbIndicatorId: 505, // 光通信模块
    opticalIds: [527, 529], // COHR rev, LITE rev
  },
  {
    id: "dsp_chip",
    label: "电芯片\nDSP / Driver / TIA",
    layer: 2,
    companies: ["Marvell (MRVL)", "博通 (AVGO)", "Credo (CRDO)"],
    description: "信号完整性的核心瓶颈层。Marvell 全球PAM4 DSP份额约60-70%。SerDes从112G→224G→448G。",
    evolution: "112G SerDes → 224G → 448G (研发中)",
    opticalIds: [539], // CRDO rev
  },
  {
    id: "passive_comp",
    label: "无源器件\nMPO连接器 / AWG / WDM",
    layer: 2,
    companies: ["天孚通信", "太辰光", "光库科技"],
    description: "MPO连接器通道数从8→16→32→64演进，推动量价齐升。",
    evolution: "8ch MPO → 16ch → 32ch → 64ch+",
    cbIndicatorId: 505,
  },
  {
    id: "siph_chip",
    label: "硅光芯片 (SiPh PIC)\nSOI衬底 + InP激光器集成",
    layer: 2,
    companies: ["台积电 (2330)", "英特尔 (INTC)", "博通 (AVGO)"],
    description: "在硅片上集成光子功能。台积电 COUPE 平台支持3D异构集成。硅光市场份额预计2026年超50%。",
    evolution: "InP 分立 → SOI硅光 → 异质集成 → LNOI/BTO",
    cbIndicatorId: 506, // CPO概念
    opticalIds: [540], // TSMC TW rev
  },

  // ═════════════════════════ Layer 3: 光模块 & 封装 ═════════════════════════
  {
    id: "transceiver",
    label: "光模块\n800G / 1.6T / 3.2T",
    layer: 3,
    companies: ["中际旭创 (#1 23.4%)", "Coherent (COHR #2 16.9%)", "Fabrinet (FN)", "新易盛 (#5 8.8%)"],
    description: "电↔光转换的完整封装。AI集群后端网络最大受益层。接口从8通道(800G)向16通道(3.2T)演进。",
    evolution: "8ch (1.6T OSFP) → 16ch (3.2T OSFP-XD) → 32-64ch (6.4T-12.8T)",
    cbIndicatorId: 505,
    opticalIds: [527, 531], // COHR rev, FN rev
  },
  {
    id: "cpo",
    label: "CPO 共封装光学\n2.5D/3D集成 <5pJ/bit",
    layer: 3,
    companies: ["博通 (AVGO)", "台积电 (2330)", "众达-KY (4977)", "上诠 (3363)"],
    description: "光引擎与ASIC 2.5D/3D共封装，功耗<5pJ/bit。不可热插拔但密度最高。",
    evolution: "可插拔 → LPO → NPO(近封装) → CPO(共封装) → OIO(片内光互联)",
    cbIndicatorId: 506,
  },
  {
    id: "glass_sub",
    label: "玻璃基板\nGlass Core Substrate",
    layer: 3,
    companies: ["康宁 (GLW)", "英特尔 (INTC)", "三星 (005930)"],
    description: "替代有机ABF基板的下一代先进封装载板。与CPO/NPO联动。",
    cbIndicatorId: 519, // 玻璃基板
  },
  {
    id: "copper_inter",
    label: "铜缆高速连接\nDAC / AEC / AOC",
    layer: 3,
    companies: ["安费诺 (APH)", "莫仕 (Molex)"],
    description: "短距离(<7m)AI集群互连方案。与光模块在特定场景互补/竞争。",
    cbIndicatorId: 521, // 铜缆高速连接
  },

  // ═════════════════════════ Layer 4: 系统集成 & 终端 ═════════════════════════
  {
    id: "dc_switch",
    label: "数据中心交换机\nSpine-Leaf Architecture",
    layer: 4,
    companies: ["Arista (ANET)", "思科 (CSCO)", "华为"],
    description: "AI集群叶脊架构核心。Arista为超大规模数据中心交换机龙头。",
    opticalIds: [532], // ANET rev
  },
  {
    id: "ai_cluster",
    label: "AI GPU 集群\nNVIDIA / AMD",
    layer: 4,
    companies: ["NVIDIA (NVDA)", "AMD", "Intel"],
    description: "数万张GPU的后端网络（InfiniBand / RoCE）是光模块最大需求来源。",
  },
  {
    id: "data_center",
    label: "超大规模数据中心\nHyperscalers",
    layer: 4,
    companies: ["MSFT", "AMZN", "GOOGL", "META"],
    description: "四大云厂商合计 CapEx 持续攀升。美国预计到2029年需新增2.13亿芯英里光纤。",
    cbIndicatorId: 508,
  },
  {
    id: "submarine",
    label: "海底光缆\nSubmarine Cable",
    layer: 4,
    companies: ["NEC", "ASN (Nokia)", "SubCom", "华为海洋"],
    description: "洲际骨干网。空芯光纤有望将延迟降低30%以上，对金融交易等低延迟场景影响深远。",
  },
  {
    id: "telecom",
    label: "电信 5G / 6G\n前传 / 中传 / 回传",
    layer: 4,
    companies: ["Ciena (CIEN)", "华为", "中兴", "诺基亚"],
    description: "5G前传主要用25G灰光，中传回传用100G/400G相干光。6G时代将全面升级。",
    opticalIds: [536], // CIEN rev
  },
];

export const CHAIN_EDGES: ChainEdge[] = [
  // Layer 0 → Layer 1
  { from: "sio2", to: "preform", label: "玻璃原料" },
  { from: "ultra_pure_gas", to: "preform", label: "前驱体" },
  { from: "rare_earth", to: "preform", label: "掺杂剂" },
  { from: "preform", to: "fiber_draw", label: "加热拉丝" },
  { from: "fiber_draw", to: "cable_assembly", label: "着色/成带" },

  // Layer 0 → Layer 2
  { from: "inp", to: "active_chip", label: "衬底" },
  { from: "gaas", to: "active_chip", label: "衬底" },
  { from: "linbo3", to: "active_chip", label: "调制器材料" },
  { from: "gaas", to: "siph_chip", label: "SOI 衬底" },

  // Layer 1 → Layer 3
  { from: "fiber_draw", to: "transceiver", label: "光纤互联" },
  { from: "cable_assembly", to: "transceiver", label: "光缆跳线" },

  // Layer 2 → Layer 3
  { from: "active_chip", to: "transceiver", label: "TOSA/ROSA" },
  { from: "dsp_chip", to: "transceiver", label: "信号处理" },
  { from: "passive_comp", to: "transceiver", label: "MPO连接" },
  { from: "siph_chip", to: "cpo", label: "硅光引擎" },
  { from: "active_chip", to: "cpo", label: "激光器芯片" },
  { from: "dsp_chip", to: "cpo", label: "SerDes" },
  { from: "glass_sub", to: "cpo", label: "封装载板" },

  // Layer 3 → Layer 4
  { from: "transceiver", to: "ai_cluster", label: "800G/1.6T" },
  { from: "transceiver", to: "dc_switch", label: "QSFP/OSFP" },
  { from: "transceiver", to: "telecom", label: "400G相干" },
  { from: "transceiver", to: "submarine", label: "海底光缆" },
  { from: "cpo", to: "ai_cluster", label: "CPO引擎" },
  { from: "copper_inter", to: "dc_switch", label: "DAC短距" },
  { from: "dc_switch", to: "data_center", label: "Spine-Leaf" },
  { from: "ai_cluster", to: "data_center", label: "GPU集群" },
];
