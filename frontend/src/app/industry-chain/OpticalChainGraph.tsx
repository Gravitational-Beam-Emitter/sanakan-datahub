"use client";

import { useCallback, useMemo } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  type Node,
  type Edge,
  Position,
  useNodesState,
  useEdgesState,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import dagre from "dagre";

import { CHAIN_NODES, CHAIN_EDGES, LAYER_COLORS, LAYER_LABELS } from "./chainData";

/* ── Dagre layout ──────────────────────────────────────────── */

const NODE_WIDTH = 200;
const NODE_HEIGHT = 120;

function getLayoutedElements(nodes: Node[], edges: Edge[]) {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: "TB", ranksep: 120, nodesep: 60 });

  nodes.forEach((n) => {
    g.setNode(n.id, { width: NODE_WIDTH, height: NODE_HEIGHT + 40 });
  });
  edges.forEach((e) => {
    g.setEdge(e.source, e.target);
  });

  dagre.layout(g);

  const layoutedNodes = nodes.map((n) => {
    const pos = g.node(n.id);
    return {
      ...n,
      position: { x: pos.x - NODE_WIDTH / 2, y: pos.y - NODE_HEIGHT / 2 },
    };
  });

  return { nodes: layoutedNodes, edges };
}

/* ── Custom Node ───────────────────────────────────────────── */

function ChainNode({ data }: { data: { label: string; layer: number; companies: string[]; evolution?: string; description?: string } }) {
  const bg = LAYER_COLORS[data.layer] || "var(--surface)";

  return (
    <div
      className="rounded-xl p-3 border shadow-sm text-xs"
      style={{
        background: bg,
        borderColor: "var(--border)",
        color: "var(--ink)",
        width: NODE_WIDTH,
      }}
    >
      <div
        className="font-semibold mb-1.5 text-center whitespace-pre-line leading-tight"
        style={{ fontSize: "12px" }}
      >
        {data.label}
      </div>
      <div className="space-y-1">
        <div className="flex flex-wrap gap-0.5">
          {data.companies.slice(0, 3).map((c) => (
            <span
              key={c}
              className="px-1 py-0.5 rounded"
              style={{
                background: "var(--primary-a10)",
                color: "var(--primary)",
              }}
            >
              {c}
            </span>
          ))}
          {data.companies.length > 3 && (
            <span className="text-muted">+{data.companies.length - 3}</span>
          )}
        </div>
        {data.evolution && (
          <div
            className="rounded px-1 py-0.5"
            style={{
              background: "var(--surface)",
              color: "var(--up)",
              fontSize: "10px",
            }}
          >
            {data.evolution}
          </div>
        )}
      </div>
    </div>
  );
}

const nodeTypes = { chainNode: ChainNode };

/* ── Graph Component ───────────────────────────────────────── */

export default function OpticalChainGraph() {
  const initNodes: Node[] = useMemo(
    () =>
      CHAIN_NODES.map((n) => ({
        id: n.id,
        type: "chainNode",
        position: { x: 0, y: 0 }, // dagre will reposition
        data: {
          label: n.label,
          layer: n.layer,
          companies: n.companies,
          evolution: n.evolution,
          description: n.description,
        },
        sourcePosition: Position.Bottom,
        targetPosition: Position.Top,
      })),
    [],
  );

  const initEdges: Edge[] = useMemo(
    () =>
      CHAIN_EDGES.map((e) => ({
        id: `${e.from}-${e.to}`,
        source: e.from,
        target: e.to,
        label: e.label,
        style: { stroke: "var(--border)" },
        labelStyle: { fill: "var(--muted)", fontSize: 10 },
        labelBgStyle: { fill: "var(--surface)" },
        animated: false,
      })),
    [],
  );

  const { nodes: layoutedNodes, edges: layoutedEdges } = getLayoutedElements(initNodes, initEdges);

  const [nodes, setNodes, onNodesChange] = useNodesState(layoutedNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(layoutedEdges);

  return (
    <div style={{ width: "100%", height: "85vh" }} className="glass rounded-xl">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        attributionPosition="bottom-right"
        minZoom={0.3}
        maxZoom={2}
      >
        <Background color="var(--border)" gap={20} />
        <Controls />
        <MiniMap
          nodeColor={(n) => LAYER_COLORS[(n.data as { layer: number }).layer] || "#ddd"}
          style={{ background: "var(--surface)" }}
        />
      </ReactFlow>
    </div>
  );
}

/* ── Legend ────────────────────────────────────────────────── */

export function ChainLegend() {
  return (
    <div className="flex flex-wrap gap-3 justify-center py-2">
      {LAYER_LABELS.map((label, i) => (
        <div key={i} className="flex items-center gap-1.5 text-xs">
          <span
            className="w-3 h-3 rounded-full inline-block"
            style={{ background: LAYER_COLORS[i], border: "1px solid var(--border)" }}
          />
          <span className="text-muted">{label}</span>
        </div>
      ))}
    </div>
  );
}
