import { createContext, useContext, useEffect, useMemo, useRef } from "react";
import {
  Background, Controls, Edge, Handle, MarkerType, MiniMap, Node, NodeProps, Position, ReactFlow,
  ReactFlowProvider, useReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import dagre from "@dagrejs/dagre";
import {
  ArrowRight, Bot, BookOpen, Database, GitBranch, HelpCircle, LayoutTemplate, MessageSquare, Play, Sparkles, Square,
  Variable, Workflow, Zap, CornerDownRight, CircleDot, Wrench,
} from "lucide-react";
import type { GNode, Graph } from "../api";

const NODE_W = 260;

/** Copilot Studio-like node families; each has a --n-<family> colour token (canvas only). */
export type Family = "trigger" | "message" | "question" | "condition" | "variable" | "redirect" | "flow" | "ai" | "knowledge" | "end" | "scope";

export function family(n: GNode): Family {
  const k = n.kind;
  if (n.variant === "trigger" || k === "Agent") return "trigger";
  if (k === "SendActivity" || k === "Response" || k === "topic") return "message";
  if (["Question", "AdaptiveCardPrompt", "CSATQuestion", "OAuthInput"].includes(k)) return "question";
  if (k === "ConditionGroup" || k === "If" || k === "Switch") return "condition";
  if (/^(Set|Parse|Reset|ClearAll|EditTable|InitializeVariable|IncrementVariable|AppendTo|Compose)/.test(k) || k === "variable") return "variable";
  if (["BeginDialog", "ReplaceDialog", "GotoAction", "RepeatDialog"].includes(k) || k === "trigger") return "redirect";
  if (k.startsWith("Invoke") || ["OpenApiConnection", "ApiConnection", "Http", "HttpRequestAction", "action", "workflow"].includes(k)) return "flow";
  if (["SearchAndSummarizeContent", "AnswerQuestionWithAI", "SearchKnowledgeSources", "CreateSearchQuery", "childAgent"].includes(k)) return "ai";
  if (k === "knowledge") return "knowledge";
  if (["Scope", "Foreach", "Until", "ActionScope"].includes(k)) return "scope";
  if (n.variant === "end" || n.variant === "terminal" || k === "Terminate") return "end";
  return "variable";
}

const TYPE_NAME: Record<string, string> = {
  SendActivity: "Message", Question: "Question", ConditionGroup: "Condition", SetVariable: "Set variable",
  SetTextVariable: "Set text variable", ParseValue: "Parse value", BeginDialog: "Redirect", ReplaceDialog: "Replace topic",
  InvokeFlowAction: "Run a flow", InvokeConnectorAction: "Connector", HttpRequestAction: "HTTP request",
  SearchAndSummarizeContent: "Generative answers", AnswerQuestionWithAI: "AI answer", AdaptiveCardPrompt: "Adaptive card",
  EndDialog: "End current topic", EndConversation: "End conversation", CancelAllDialogs: "End all topics",
  TransferConversation: "Transfer to agent", TransferConversationV2: "Transfer to agent", GotoAction: "Go to step",
  OpenApiConnection: "Connector", Compose: "Compose", If: "Condition", Switch: "Switch", Scope: "Scope", Foreach: "Apply to each",
  Response: "Respond", InitializeVariable: "Initialize variable", topic: "Topic", action: "Tool", workflow: "Agent flow",
  childAgent: "Child agent", knowledge: "Knowledge", variable: "Global variable", Agent: "Agent", End: "End", Request: "Trigger",
  ActionScope: "Scope", Foreach_: "For each",
};

function iconFor(n: GNode) {
  const f = family(n);
  if (n.kind === "childAgent") return <Bot size={13} />;
  if (n.kind === "AdaptiveCardPrompt") return <LayoutTemplate size={13} />;
  switch (f) {
    case "trigger": return <Zap size={13} />;
    case "message": return <MessageSquare size={13} />;
    case "question": return <HelpCircle size={13} />;
    case "condition": return <GitBranch size={13} />;
    case "variable": return n.kind === "variable" ? <Database size={13} /> : <Variable size={13} />;
    case "redirect": return <CornerDownRight size={13} />;
    case "flow": return n.kind === "action" ? <Wrench size={13} /> : <Workflow size={13} />;
    case "ai": return <Sparkles size={13} />;
    case "knowledge": return <BookOpen size={13} />;
    case "scope": return <CircleDot size={13} />;
    case "end": return <Square size={13} />;
  }
  return <Play size={13} />;
}

/** Navigation from inside a node (redirect target → open that topic). */
const NavCtx = createContext<((componentId: string) => void) | undefined>(undefined);

type FlowNodeData = { g: GNode; selected: boolean; run?: string };
const RUN_LABEL: Record<string, string> = { active: "running", ok: "succeeded", failed: "failed", skipped: "skipped" };

function Body({ n }: { n: GNode }) {
  const m = n.meta ?? {};
  const nav = useContext(NavCtx);
  const chips: JSX.Element[] = [];
  if (m.entity) chips.push(<span key="e" className="nchip">Identify: {m.entity}</span>);
  if (m.variable) chips.push(<span key="v" className="nchip mono">{m.variable}</span>);
  if (m.card) chips.push(<span key="c" className="nchip">Adaptive card</span>);
  if (m.variants) chips.push(<span key="x" className="nchip">{m.variants} variations</span>);
  if (m.connector) chips.push(<span key="k" className="nchip">{m.connector}{m.operation ? ` · ${m.operation}` : ""}</span>);
  if (m.errors) chips.push(<span key="er" className="nchip err">{m.errors} error(s)</span>);
  if (m.warnings) chips.push(<span key="wa" className="nchip warn">{m.warnings} warning(s)</span>);
  return (
    <div className="nbody">
      {m.phrases?.length > 0 && <div className="nphrases">{(m.phrases as string[]).slice(0, 3).map((p) => <span key={p} className="nchip">“{p}”</span>)}
        {m.phrases.length > 3 && <span className="nchip">+{m.phrases.length - 3}</span>}</div>}
      {m.text && <div className="ntext">{m.text}</div>}
      {m.value !== undefined && m.value !== "" && <div className="ntext mono">= {m.value}</div>}
      {m.options?.length > 0 && <div className="nphrases">{(m.options as string[]).slice(0, 4).map((o) => <span key={o} className="nchip">{o}</span>)}</div>}
      {m.target && (m.targetId && nav
        ? <button className="nlink nodrag" onClick={(e) => { e.stopPropagation(); nav(m.targetId); }}><ArrowRight size={11} />{m.target}</button>
        : <div className="ntext"><ArrowRight size={11} /> {m.target}</div>)}
      {m.flow && (m.targetId && nav
        ? <button className="nlink nodrag" onClick={(e) => { e.stopPropagation(); nav(m.targetId); }}><Workflow size={11} />{m.flow}</button>
        : <div className="ntext">{m.flow}</div>)}
      {!m.text && !m.phrases?.length && !m.target && !m.flow && m.value === undefined && n.detail && <div className="ntext faintish">{n.detail}</div>}
      {chips.length > 0 && <div className="nphrases">{chips}</div>}
    </div>
  );
}

function GraphNode({ data, sourcePosition, targetPosition }: NodeProps<Node<FlowNodeData>>) {
  const n = data.g;
  const fam = family(n);
  const type = TYPE_NAME[n.kind] ?? (n.variant === "trigger" ? "Trigger" : n.kind);
  return (
    <div className={`gnode f-${fam} v-${n.variant}${n.status ? ` s-${n.status}` : ""}${data.run ? ` r-${data.run}` : ""}${data.selected ? " selected" : ""}`}
         title={n.detail}>
      <Handle type="target" position={targetPosition ?? Position.Top} />
      <div className="nhead">
        <span className="nicon">{iconFor(n)}</span>
        <span className="ntype">{type}</span>
        {n.status && n.status !== "same" && <span className={`pill-sm ${n.status === "new" ? "ok" : "warning"}`}>{n.status}</span>}
        {data.run && RUN_LABEL[data.run] && <span className={`pill-sm run-${data.run}`}>{RUN_LABEL[data.run]}</span>}
      </div>
      <div className="l ellipsis">{n.label}</div>
      <Body n={n} />
      <Handle type="source" position={sourcePosition ?? Position.Bottom} />
    </div>
  );
}

const nodeTypes = { g: GraphNode };

/** Rough card height so dagre leaves room for the content. */
function estHeight(n: GNode): number {
  const m = n.meta ?? {};
  let h = 52;
  const lines = (s?: string) => (s ? Math.min(3, Math.ceil(s.length / 34)) * 17 : 0);
  h += lines(m.text) || (n.detail && !m.phrases?.length && !m.target && !m.flow ? 17 : 0);
  if (m.phrases?.length) h += 26;
  if (m.options?.length) h += 26;
  if (m.value !== undefined && m.value !== "") h += 17;
  if (m.target || m.flow) h += 22;
  if (m.entity || m.variable || m.card || m.connector || m.variants || m.errors || m.warnings) h += 26;
  return h;
}

function layout(graph: Graph, dir: "TB" | "LR") {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: dir, nodesep: 40, ranksep: dir === "LR" ? 80 : 56 });
  g.setDefaultEdgeLabel(() => ({}));
  graph.nodes.forEach((n) => g.setNode(n.id, { width: NODE_W, height: estHeight(n) }));
  graph.edges.forEach((e) => g.setEdge(e.source, e.target));
  dagre.layout(g);
  return graph.nodes.map((n) => {
    const p = g.node(n.id);
    return { id: n.id, x: (p?.x ?? 0) - NODE_W / 2, y: (p?.y ?? 0) - estHeight(n) / 2 };
  });
}

const EDGE_COLOR: Record<string, string> = {
  default: "var(--c-edge-strong)", branch: "var(--n-condition)", else: "var(--c-fg3)",
  error: "var(--c-danger)", goto: "var(--n-redirect)",
};

function Inner({ graph, direction = "TB", selected, onSelect, minimap = true, marks }: CanvasProps) {
  const rf = useReactFlow();
  const wrap = useRef<HTMLDivElement>(null);
  const userMoved = useRef(false);
  const positions = useMemo(() => layout(graph, direction), [graph, direction]);

  const nodes: Node<FlowNodeData>[] = useMemo(() => graph.nodes.map((n, i) => ({
    id: n.id, type: "g", position: { x: positions[i].x, y: positions[i].y },
    data: { g: n, selected: n.id === selected, run: marks?.[n.pointer] }, draggable: true,
    sourcePosition: direction === "LR" ? Position.Right : Position.Bottom,
    targetPosition: direction === "LR" ? Position.Left : Position.Top,
  })), [graph, positions, selected, direction, marks]);

  const edges: Edge[] = useMemo(() => {
    const ptr = new Map(graph.nodes.map((n) => [n.id, n.pointer]));
    const ran = (id: string) => { const m = marks?.[ptr.get(id) ?? ""]; return !!m && m !== "skipped"; };
    return graph.edges.map((e) => {
      const taken = !!marks && ran(e.source) && ran(e.target);
      const color = taken ? "var(--c-primary)" : EDGE_COLOR[e.variant] ?? EDGE_COLOR.default;
      return {
        id: e.id, source: e.source, target: e.target, label: e.label || undefined,
        type: "smoothstep", animated: taken,
        labelStyle: { fill: "var(--c-fg2)", fontSize: 11 }, labelBgStyle: { fill: "var(--c-page)" },
        style: { stroke: color, strokeWidth: taken ? 2 : 1.25, strokeDasharray: !taken && ["else", "goto", "error"].includes(e.variant) ? "5 4" : undefined },
        markerEnd: { type: MarkerType.ArrowClosed, color },
      };
    });
  }, [graph, marks]);

  const fit = () => rf.fitView({ padding: 0.15, maxZoom: 1.05 });
  useEffect(() => { userMoved.current = false; const t = setTimeout(fit, 30); return () => clearTimeout(t); }, [graph, rf]); // eslint-disable-line react-hooks/exhaustive-deps
  // Refit when the container changes size (side panel / dock opens) unless the user has panned or zoomed.
  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    let t: ReturnType<typeof setTimeout>;
    const ro = new ResizeObserver(() => { clearTimeout(t); t = setTimeout(() => { if (!userMoved.current) fit(); }, 120); });
    ro.observe(el);
    return () => { ro.disconnect(); clearTimeout(t); };
  }, [rf]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div ref={wrap} style={{ width: "100%", height: "100%" }}>
      <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} fitView minZoom={0.1} maxZoom={2}
                 onMoveStart={(e) => { if (e) userMoved.current = true; }}
                 onNodeClick={(_, n) => onSelect?.((n.data as FlowNodeData).g)} onPaneClick={() => onSelect?.(null)}
                 proOptions={{ hideAttribution: true }} nodesConnectable={false}>
        <Background gap={20} size={1} color="var(--c-edge)" />
        <Controls showInteractive={false} />
        {minimap && graph.nodes.length > 12 && <MiniMap pannable zoomable nodeColor="var(--c-raised)" maskColor="rgba(0,0,0,0.2)" />}
      </ReactFlow>
    </div>
  );
}

interface CanvasProps {
  graph: Graph; direction?: "TB" | "LR"; selected?: string | null;
  onSelect?: (n: GNode | null) => void; minimap?: boolean;
  /** node pointer -> run status (active | visited | ok | failed | skipped) from the test pane */
  marks?: Record<string, string>;
  /** open another component (redirect / flow links inside nodes) */
  onNavigate?: (componentId: string) => void;
}

export default function Canvas(props: CanvasProps) {
  return <NavCtx.Provider value={props.onNavigate}><ReactFlowProvider><Inner {...props} /></ReactFlowProvider></NavCtx.Provider>;
}

export const FAMILY_LABEL: Record<Family, string> = {
  trigger: "Trigger", message: "Message / topic", question: "Question / card", condition: "Condition", variable: "Variable",
  redirect: "Redirect", flow: "Flow / tool", ai: "Generative AI / child agent", knowledge: "Knowledge", end: "End", scope: "Scope",
};
