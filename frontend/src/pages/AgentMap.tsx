import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { ArrowRight, FlaskConical, Loader2, Network, Sparkles, X } from "lucide-react";
import { api, GNode, Graph } from "../api";
import { SeverityPill } from "../components/ui";
import { componentIdFor, fileLabel } from "../lib/labels";
import Canvas, { FAMILY_LABEL, Family } from "../components/Canvas";
import { Empty } from "../components/ui";
import { useWs } from "./WorkspaceLayout";

export default function MapPage() {
  const { ws, version, openAI, openTest, pending, openReview } = useWs();
  const [sel, setSel] = useState<GNode | null>(null);
  const { data: findings = [] } = useQuery({ queryKey: ["analyze", ws.info.id, version], queryFn: () => api.analyze(ws.info.id) });
  const nav = useNavigate();
  const { data, isLoading } = useQuery({
    queryKey: ["agentmap", ws.info.id, version],
    queryFn: () => api.agentGraph(ws.info.id),
  });
  // Proposed-but-unapplied components show up as faded "new" nodes.
  const withBadges = useMemo<Graph | undefined>(() => {
    if (!data) return data;
    const by = new Map<string, { errors: number; warnings: number }>();
    for (const f of findings) {
      const b = by.get(f.component) ?? { errors: 0, warnings: 0 };
      if (f.severity === "error") b.errors++; else if (f.severity === "warning") b.warnings++;
      by.set(f.component, b);
    }
    return { ...data, nodes: data.nodes.map((n) => (by.has(n.id) ? { ...n, meta: { ...(n.meta ?? {}), ...by.get(n.id) } } : n)) };
  }, [data, findings]);
  const graph = useMemo<Graph | undefined>(() => {
    const data = withBadges;
    if (!data || !pending) return data;
    const root = data.nodes.find((n) => n.kind === "Agent")?.id ?? data.nodes[0]?.id;
    const have = new Set(data.nodes.map((n) => n.id));
    const extra = [...new Set(pending.files.filter((f) => f.content !== null && /^(topics|actions|agents|workflows|knowledge)\//.test(f.path))
      .map((f) => componentIdFor(f.path)))].filter((id) => !have.has(id));
    if (!extra.length) return data;
    return {
      nodes: [...data.nodes, ...extra.map((id) => {
        const f = pending.files.find((x) => componentIdFor(x.path) === id)!;
        const [kind, label] = fileLabel(f.path, f.content).split(" · ");
        return { id, kind, label: label ?? kind, detail: "proposed by AI — not applied", pointer: "", variant: "action", status: "new" };
      })],
      edges: [...data.edges, ...extra.map((id, i) => ({ id: `pending${i}`, source: root, target: id, label: "", variant: "else" }))],
    };
  }, [withBadges, pending]);
  if (isLoading || !data || !graph) return <div className="empty"><Loader2 className="spin" /></div>;
  if (data.nodes.length <= 1) return (
    <Empty icon={<Network size={18} />} title="Nothing to map yet">
      <button className="btn btn-primary" onClick={() => openAI({ mode: "agent" })}><Sparkles size={16} />Build an agent with AI</button>
    </Empty>
  );
  return (
    <div className="editor-area">
      <div className="editor-toolbar" style={{ padding: "8px 16px" }}>
        <div className="row small muted wrap">
          <b style={{ color: "var(--c-fg)" }}>Agent architecture</b>
          <div className="legend">{(["trigger", "message", "flow", "ai", "knowledge", "variable", "redirect"] as Family[]).map((f) => (
            <span key={f}><i style={{ background: `var(--n-${f})` }} />{FAMILY_LABEL[f]}</span>))}</div>
          <span className="faint">· Click a node to open it</span>
        </div>
      </div>
      <div className="row" style={{ flex: 1, minHeight: 0, alignItems: "stretch", gap: 0 }}>
        <div className="canvas-wrap">
          <Canvas graph={graph} direction="LR" selected={sel?.id} onSelect={(n) => {
            if (n && !n.pointer && n.status === "new" && pending) openReview(pending);
            else setSel(n);
          }} />
        </div>
        {sel && sel.pointer && (
          <aside className="side-panel" style={{ width: 320 }}>
            <div className="panel-head"><div className="grow"><div className="tiny faint">{sel.kind}</div><h3 className="ellipsis">{sel.label}</h3></div>
              <button className="btn-icon" onClick={() => setSel(null)} aria-label="Close"><X size={16} /></button></div>
            <div className="panel-body stack">
              {sel.detail && <div className="small muted">{sel.detail}</div>}
              <button className="btn btn-primary btn-sm" onClick={() => nav(`/w/${ws.info.id}?c=${encodeURIComponent(sel.pointer)}`)}><ArrowRight size={14} />Open in Build</button>
              <button className="btn btn-secondary btn-sm" onClick={() => openAI({ mode: "modify", target: sel.pointer })}><Sparkles size={14} />Change with AI</button>
              <button className="btn btn-secondary btn-sm" onClick={openTest}><FlaskConical size={14} />Test the agent</button>
              {findings.filter((f) => f.component === sel.pointer).map((f, i) => (
                <div key={i} className="row small" style={{ alignItems: "flex-start" }}><SeverityPill s={f.severity} /><span>{f.message}</span></div>))}
            </div>
          </aside>
        )}
      </div>
    </div>
  );
}
