import { useMemo } from "react";
import ReactFlow, {
  Background,
  Controls,
  Edge,
  MarkerType,
  Node,
  Position,
} from "reactflow";
import "reactflow/dist/style.css";

/** Top supervisor, middle row = pipeline, bottom = exception + email */
const ORDER = [
  "supervisor",
  "extraction",
  "screening",
  "validation",
  "matching",
  "exception",
  "email",
];

const LABELS: Record<string, string> = {
  supervisor: "Supervisor",
  extraction: "Extraction",
  screening: "Screening",
  validation: "Validation",
  matching: "Matching",
  exception: "Exception",
  email: "Email",
};

const POS: Record<string, { x: number; y: number }> = {
  supervisor: { x: 340, y: 0 },
  extraction: { x: 40, y: 160 },
  screening: { x: 240, y: 160 },
  validation: { x: 440, y: 160 },
  matching: { x: 640, y: 160 },
  exception: { x: 260, y: 320 },
  email: { x: 500, y: 320 },
};

type AgentStatus = "running" | "waiting" | "done" | "failed";

function nodeStyle(_id: string, status: AgentStatus): React.CSSProperties {
  const base: React.CSSProperties = {
    padding: "12px 14px",
    borderRadius: 8,
    fontSize: 12,
    fontWeight: 600,
    border: "2px solid",
    minWidth: 108,
    maxWidth: 140,
    textAlign: "center",
  };
  if (status === "failed") {
    return {
      ...base,
      background: "#450a0a",
      borderColor: "#ef4444",
      color: "#fecaca",
    };
  }
  if (status === "running") {
    return {
      ...base,
      background: "#052e16",
      borderColor: "#22c55e",
      color: "#bbf7d0",
    };
  }
  if (status === "done") {
    return {
      ...base,
      background: "#1e293b",
      borderColor: "#475569",
      color: "#94a3b8",
    };
  }
  return {
    ...base,
    background: "#0f172a",
    borderColor: "#334155",
    color: "#64748b",
  };
}

function nodeClass(status: AgentStatus): string | undefined {
  if (status === "failed") return "animate-blink-red rounded-lg";
  if (status === "running") return "animate-blink-green rounded-lg";
  return undefined;
}

export function AgentFlow(props: { agents: { name: string; status: string }[] }) {
  const map = useMemo(() => {
    const m = new Map<string, AgentStatus>();
    for (const a of props.agents || []) {
      m.set(a.name, a.status as AgentStatus);
    }
    return m;
  }, [props.agents]);

  const { nodes, edges } = useMemo(() => {
    const ns: Node[] = ORDER.map((id) => {
      const st = map.get(id) || "waiting";
      const p = POS[id] || { x: 0, y: 0 };
      return {
        id,
        data: { label: LABELS[id] || id },
        position: p,
        sourcePosition: Position.Bottom,
        targetPosition: Position.Top,
        style: nodeStyle(id, st),
        className: nodeClass(st),
      };
    });

    const es: Edge[] = [
      { id: "e-sup-ext", source: "supervisor", target: "extraction", ...edgeOpts(map) },
      { id: "e-ext-scr", source: "extraction", target: "screening", ...edgeOpts(map) },
      { id: "e-scr-val", source: "screening", target: "validation", ...edgeOpts(map) },
      { id: "e-val-mat", source: "validation", target: "matching", ...edgeOpts(map) },
      { id: "e-mat-exc", source: "matching", target: "exception", ...edgeOpts(map) },
      { id: "e-exc-em", source: "exception", target: "email", ...edgeOpts(map) },
    ];
    return { nodes: ns, edges: es };
  }, [map]);

  return (
    <div className="h-[480px] w-full rounded-lg border border-slate-800 bg-slate-900/50">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        panOnDrag
        zoomOnScroll
        proOptions={{ hideAttribution: true }}
      >
        <Background color="#334155" gap={20} />
        <Controls className="!bg-slate-800 !border-slate-600" />
      </ReactFlow>
    </div>
  );
}

function edgeOpts(map: Map<string, AgentStatus>) {
  const active = ["extraction", "screening", "validation", "matching", "exception", "email"].some(
    (id) => map.get(id) === "running"
  );
  return {
    animated: active,
    markerEnd: { type: MarkerType.ArrowClosed, color: "#64748b" },
    style: { stroke: "#475569", strokeWidth: 2 },
  };
}
