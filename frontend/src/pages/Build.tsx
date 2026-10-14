import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bot, BookOpen, ClipboardPaste, Code2, Database, FileCode2, GitFork, Loader2, MessageSquare, Plus, Save,
  Settings, Sparkles, Trash2, Workflow, Wrench, X, Zap, AlertTriangle, CheckCircle2, FlaskConical,
} from "lucide-react";
import { api, Category, Component, FileReport, GNode } from "../api";
import Canvas from "../components/Canvas";
import { CodeEditor } from "../components/Editors";
import { FlowTestPanel } from "../components/FlowTest";
import AgentOverview from "../components/AgentOverview";
import { FORM_KINDS, NodeForm } from "../components/NodeForms";
import { componentIdFor, fileLabel } from "../lib/labels";
import { Empty, Modal, SeverityPill, useToast } from "../components/ui";
import { useWs } from "./WorkspaceLayout";

const GROUPS: { cats: Category[]; label: string; icon: JSX.Element; aiMode?: "topic" | "flow" | "childAgent" }[] = [
  { cats: ["agent"], label: "Agent", icon: <Bot size={14} /> },
  { cats: ["childAgent"], label: "Child agents", icon: <GitFork size={14} />, aiMode: "childAgent" },
  { cats: ["topic"], label: "Topics", icon: <MessageSquare size={14} />, aiMode: "topic" },
  { cats: ["action"], label: "Tools", icon: <Wrench size={14} />, aiMode: "flow" },
  { cats: ["workflow"], label: "Agent flows", icon: <Workflow size={14} />, aiMode: "flow" },
  { cats: ["knowledge"], label: "Knowledge", icon: <BookOpen size={14} /> },
  { cats: ["variable"], label: "Global variables", icon: <Database size={14} /> },
  { cats: ["trigger"], label: "Event triggers", icon: <Zap size={14} /> },
  { cats: ["settings", "connectionReferences"], label: "Settings", icon: <Settings size={14} /> },
  { cats: ["other"], label: "Other", icon: <FileCode2 size={14} /> },
];

const hasGraph = (c: Component) => ["topic", "childAgent", "workflow", "trigger"].includes(c.category);

const CAT_OF_PATH = (p: string): Category =>
  p === "agent.mcs.yml" ? "agent" : p.startsWith("topics/") ? "topic" : p.startsWith("actions/") ? "action"
    : p.startsWith("workflows/") ? "workflow" : p.startsWith("knowledge/") ? "knowledge" : p.startsWith("variables/") ? "variable"
    : /^agents\/[^/]+\/agent\.mcs\.ya?ml$/.test(p) ? "childAgent" : p.startsWith("trigger/") ? "trigger" : "other";

export default function BuildPage() {
  const { ws, selected, select, openAI, pending, openReview } = useWs();
  const [paste, setPaste] = useState(false);

  const groups = useMemo(() => GROUPS.map((g) => ({ ...g, items: ws.components.filter((c) => g.cats.includes(c.category)) })),
    [ws.components]);
  // AI proposal not applied yet: badge existing components, list new ones as ghosts.
  const pendingBy = useMemo(() => {
    const m = new Map<string, { status: "new" | "changed" | "delete"; path: string; content: string | null }>();
    for (const f of pending?.files ?? []) {
      const id = componentIdFor(f.path);
      const exists = ws.components.some((c) => c.id === id);
      const status = f.content === null ? "delete" : exists ? "changed" : "new";
      if (!m.has(id) || status === "changed") m.set(id, { status, path: f.path, content: f.content });
    }
    return m;
  }, [pending, ws.components]);

  return (
    <>
      <aside className="explorer">
        <div className="row between" style={{ padding: "0 4px 4px" }}>
          <button className="btn btn-ghost btn-sm" onClick={() => setPaste(true)}><ClipboardPaste size={14} />Paste YAML</button>
          <button className="btn btn-ghost btn-sm" onClick={() => openAI({ mode: "topic" })}><Sparkles size={14} />New</button>
        </div>
        {groups.map((g) => {
          const ghosts = [...pendingBy.entries()].filter(([id, v]) => v.status === "new" && g.cats.includes(CAT_OF_PATH(v.path)) && !ws.components.some((c) => c.id === id));
          return (g.items.length > 0 || g.aiMode || ghosts.length > 0) && (
          <div key={g.label}>
            <div className="tree-group"><span>{g.label}</span>
              {g.aiMode && <button className="btn-icon" style={{ width: 20, height: 20 }} title={`Generate ${g.label.toLowerCase()} with AI`}
                                    onClick={() => openAI({ mode: g.aiMode })}><Plus size={12} /></button>}</div>
            {g.items.map((c) => (
              <button key={c.id} className={`tree-item${selected?.id === c.id ? " active" : ""}`} onClick={() => select(c.id)} title={c.path}>
                {g.icon}<span className="name">{c.displayName}</span>
                {c.error && <AlertTriangle size={12} color="var(--c-danger)" />}
                {c.owner && c.category !== "childAgent" && <span className="pill-sm">{c.owner}</span>}
                {pendingBy.has(c.id) && <span className={`pill-sm ${pendingBy.get(c.id)!.status === "delete" ? "error" : "warning"}`} title="Proposed by the AI Builder, not applied yet">
                  AI · {pendingBy.get(c.id)!.status}</span>}
              </button>
            ))}
            {ghosts.map(([id, v]) => (
              <button key={id} className="tree-item ghost" onClick={() => pending && openReview(pending)} title="Proposed by the AI Builder — click to review">
                {g.icon}<span className="name">{fileLabel(v.path, v.content).split(" · ").pop()}</span><span className="pill-sm ok">AI · new</span>
              </button>
            ))}
          </div>
          );
        })}
      </aside>
      {selected ? <ComponentView key={selected.id} comp={selected} /> : (
        <div className="editor-area">
          <Empty icon={<Sparkles size={18} />} title={ws.components.length ? "Select a component" : "Empty workspace"}>
            <div className="small muted" style={{ maxWidth: 420 }}>
              {ws.components.length ? "Pick a topic, flow or child agent on the left to see it as a diagram and edit it."
                : "Describe your agent to the AI Builder, paste YAML copied from Copilot Studio's code editor, or open a cloned agent folder from Workspaces."}</div>
            <div className="row"><button className="btn btn-primary" onClick={() => openAI({ mode: "agent" })}><Sparkles size={16} />Build an agent with AI</button>
              <button className="btn btn-secondary" onClick={() => setPaste(true)}><ClipboardPaste size={16} />Paste YAML</button></div>
          </Empty>
        </div>
      )}
      {paste && <PasteModal onClose={() => setPaste(false)} />}
    </>
  );
}

function ComponentView({ comp }: { comp: Component }) {
  const { ws, select, openAI, marks, openTest } = useWs();
  const qc = useQueryClient();
  const toast = useToast();
  const isAgent = comp.category === "agent";
  const [tab, setTab] = useState<"visual" | "code" | "overview">(isAgent ? "overview" : hasGraph(comp) ? "visual" : "code");
  const [node, setNode] = useState<GNode | null>(null);
  const [text, setText] = useState(comp.text);
  const [report, setReport] = useState<FileReport | null>(null);
  // Pick up changes made elsewhere (AI apply, node edits) unless the user has unsaved edits.
  const lastLoaded = useRef(comp.text);
  useEffect(() => {
    const prev = lastLoaded.current;          // capture now: the updater below runs after this effect
    lastLoaded.current = comp.text;
    setText((cur) => (cur === prev ? comp.text : cur));
  }, [comp.text]);
  const dirty = text !== comp.text;
  const isFlow = comp.category === "workflow";
  const [testing, setTesting] = useState(false);

  const { data: graph, isLoading } = useQuery({
    queryKey: ["graph", ws.info.id, comp.id, comp.text], queryFn: () => api.componentGraph(ws.info.id, comp.id), enabled: hasGraph(comp),
  });

  // live validation while typing (debounced)
  useEffect(() => {
    const t = setTimeout(() => { api.validate(comp.path, text).then(setReport).catch(() => {}); }, 500);
    return () => clearTimeout(t);
  }, [text, comp.path]);

  const save = useMutation({
    mutationFn: () => api.writeFile(ws.info.id, comp.path, text),
    onSuccess: (r) => { setReport(r); qc.invalidateQueries({ queryKey: ["ws", ws.info.id] }); toast(r.valid ? "Saved" : "Saved with validation errors", r.valid ? "success" : "error"); },
    onError: (e: Error) => toast(e.message, "error"),
  });
  const del = useMutation({
    mutationFn: () => api.deleteFile(ws.info.id, comp.path),
    onSuccess: () => { select(null); qc.invalidateQueries({ queryKey: ["ws", ws.info.id] }); toast("Deleted", "info"); },
  });
  const [confirmDel, setConfirmDel] = useState(false);

  const onKey = useCallback((e: KeyboardEvent) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "s") { e.preventDefault(); if (dirty) save.mutate(); }
  }, [dirty, save]);
  useEffect(() => { window.addEventListener("keydown", onKey); return () => window.removeEventListener("keydown", onKey); }, [onKey]);

  const errors = report?.issues.filter((i) => i.severity === "error") ?? [];

  return (
    <>
      <section className="editor-area">
        <div className="editor-toolbar">
          <div className="tabs">
            {isAgent && <button className={`tab${tab === "overview" ? " active" : ""}`} onClick={() => setTab("overview")}><Bot size={14} />Overview</button>}
            {hasGraph(comp) && <button className={`tab${tab === "visual" ? " active" : ""}`} onClick={() => setTab("visual")}><GitFork size={14} />Visual</button>}
            <button className={`tab${tab === "code" ? " active" : ""}`} onClick={() => setTab("code")}><Code2 size={14} />{isFlow ? "workflow.json" : "YAML"}{dirty ? " •" : ""}</button>
          </div>
          <div className="row">
            <span className="mono tiny faint ellipsis path" style={{ maxWidth: 260 }} title={comp.path}>{comp.path}</span>
            {report && (report.valid ? <span className="pill-sm ok"><CheckCircle2 size={11} />valid</span>
              : <span className="pill-sm error">{errors.length} error(s)</span>)}
            {isFlow ? <button className={`btn btn-sm ${testing ? "btn-primary" : "btn-secondary"}`} onClick={() => { setTesting((t) => !t); setTab("visual"); }}>
                <FlaskConical size={14} />Test</button>
              : comp.category === "topic" && <button className="btn btn-ghost btn-sm" onClick={openTest} title="Chat with the agent in the test pane"><FlaskConical size={14} />Test</button>}
            <button className="btn btn-ghost btn-sm" onClick={() => openAI({ mode: "modify", target: comp.id })}><Sparkles size={14} />Ask AI</button>
            {errors.length > 0 && <button className="btn btn-ghost btn-sm" onClick={() => openAI({ mode: "fix", target: comp.id, findings: errors.map((i) => `${i.path}: ${i.message}`), prompt: "" })}><Wrench size={14} />AI fix</button>}
            <button className="btn btn-secondary btn-sm" disabled={!dirty || save.isPending} onClick={() => save.mutate()}><Save size={14} />Save</button>
            <button className="btn-icon" aria-label="Delete file" onClick={() => setConfirmDel(true)}><Trash2 size={14} /></button>
          </div>
        </div>
        {tab === "overview" && isAgent ? (
          <div className="canvas-wrap" style={{ overflowY: "auto" }}><AgentOverview key={comp.text.length} comp={comp} /></div>
        ) : tab === "visual" && hasGraph(comp) ? (
          <div className="canvas-wrap">
            {isLoading || !graph ? <div className="empty"><Loader2 className="spin" /></div>
              : graph.nodes.length === 0 ? <Empty icon={<GitFork size={18} />} title="Nothing to draw">{comp.error && <div className="small">{comp.error}</div>}</Empty>
              : <Canvas graph={graph} selected={node?.id} onSelect={setNode} marks={marks[comp.id]} onNavigate={select} />}
          </div>
        ) : (
          <div className="canvas-wrap stack" style={{ gap: 0 }}>
            <div style={{ flex: 1, minHeight: 0 }}><CodeEditor value={text} onChange={setText} language={isFlow ? "json" : "yaml"} /></div>
            {report && report.issues.length > 0 && (
              <div style={{ maxHeight: 160, overflowY: "auto", borderTop: "1px solid var(--c-edge)", padding: "8px 12px", background: "var(--c-surface)" }}>
                {report.issues.map((i, k) => (
                  <div key={k} className="row small" style={{ alignItems: "flex-start", marginBottom: 2 }}>
                    <SeverityPill s={i.severity} /><span className="mono faint">{i.path || "(root)"}</span><span>{i.message}</span></div>))}
              </div>
            )}
          </div>
        )}
      </section>
      {testing && isFlow && <FlowTestPanel comp={comp} selectedPointer={node?.pointer ?? null} onClose={() => setTesting(false)} />}
      {!testing && tab === "visual" && node && node.pointer && (!node.id.startsWith("__") || node.kind === "OnRecognizedIntent") && (
        <NodePanel comp={comp} node={node} onClose={() => setNode(null)} />
      )}
      {confirmDel && (
        <Modal title={`Delete ${comp.displayName}?`} onClose={() => setConfirmDel(false)} actions={<>
          <button className="btn btn-secondary" onClick={() => setConfirmDel(false)}>Cancel</button>
          <button className="btn btn-danger" onClick={() => del.mutate()}>Delete</button></>}>
          <div className="small muted">Deletes <span className="mono">{comp.path}</span> from the workspace folder.</div>
        </Modal>
      )}
    </>
  );
}

function NodePanel({ comp, node, onClose }: { comp: Component; node: GNode; onClose: () => void }) {
  const { ws, openAI } = useWs();
  const qc = useQueryClient();
  const toast = useToast();
  const isFlow = comp.category === "workflow";
  const hasForm = !isFlow && FORM_KINDS.has(node.kind);
  const [view, setView] = useState<"form" | "yaml">(hasForm ? "form" : "yaml");
  const { data, isLoading } = useQuery({
    queryKey: ["node", ws.info.id, comp.id, node.pointer, comp.text],
    queryFn: () => api.node(ws.info.id, comp.id, node.pointer),
    enabled: view === "yaml",
  });
  const [text, setText] = useState("");
  useEffect(() => { if (data) setText(data.text); }, [data]);
  const save = useMutation({
    mutationFn: () => api.saveNode(ws.info.id, comp.id, node.pointer, text),
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ["ws", ws.info.id] }); toast(r.valid ? "Node updated" : "Node saved — file has validation errors", r.valid ? "success" : "error"); },
    onError: (e: Error) => toast(e.message, "error"),
  });
  return (
    <aside className="side-panel">
      <div className="panel-head">
        <div className="grow"><div className="tiny faint">{node.kind}</div><h3 className="ellipsis">{node.label}</h3></div>
        <button className="btn-icon" onClick={onClose} aria-label="Close"><X size={16} /></button>
      </div>
      {hasForm && (
        <div className="tabs" style={{ padding: "0 12px" }}>
          <button className={`tab${view === "form" ? " active" : ""}`} onClick={() => setView("form")}>Form</button>
          <button className={`tab${view === "yaml" ? " active" : ""}`} onClick={() => setView("yaml")}><Code2 size={14} />YAML</button>
        </div>
      )}
      {view === "form" && hasForm ? (
        <div className="panel-body"><NodeForm comp={comp} node={node} /></div>
      ) : (
        <div className="panel-body stack" style={{ padding: 0, gap: 0 }}>
          {node.detail && <div className="small muted" style={{ padding: "12px 16px" }}>{node.detail}</div>}
          <div className="tiny faint mono" style={{ padding: "0 16px 8px" }}>{node.pointer}</div>
          <div style={{ flex: 1, minHeight: 280, borderTop: "1px solid var(--c-edge)" }}>
            {isLoading ? <div className="empty"><Loader2 className="spin" /></div>
              : <CodeEditor value={text} onChange={setText} language={isFlow ? "json" : "yaml"} />}
          </div>
        </div>
      )}
      <div className="panel-foot row between">
        <button className="btn btn-ghost btn-sm" onClick={() => openAI({ mode: "modify", target: comp.id, prompt: `In node "${node.id}" (${node.kind}): ` })}>
          <Sparkles size={14} />Change with AI</button>
        {view === "yaml" && <button className="btn btn-primary btn-sm" disabled={!data || text === data.text || save.isPending} onClick={() => save.mutate()}><Save size={14} />Save node</button>}
      </div>
    </aside>
  );
}

function PasteModal({ onClose }: { onClose: () => void }) {
  const { ws, select } = useWs();
  const qc = useQueryClient();
  const toast = useToast();
  const [content, setContent] = useState("");
  const [overwrite, setOverwrite] = useState(false);
  const m = useMutation({
    mutationFn: () => api.paste(ws.info.id, content, overwrite),
    onSuccess: async (r) => {
      await qc.invalidateQueries({ queryKey: ["ws", ws.info.id] });
      toast(r.report.valid ? `Added ${r.path}` : `Added ${r.path} — has validation errors`, r.report.valid ? "success" : "error");
      select(r.path); onClose();
    },
    onError: (e: Error) => toast(e.message, "error"),
  });
  return (
    <Modal title="Paste YAML from Copilot Studio" wide onClose={onClose} actions={<>
      <label className="row small muted" style={{ marginRight: "auto" }}><input type="checkbox" checked={overwrite} onChange={(e) => setOverwrite(e.target.checked)} />Overwrite if it exists</label>
      <button className="btn btn-secondary" onClick={onClose}>Cancel</button>
      <button className="btn btn-primary" disabled={!content.trim() || m.isPending} onClick={() => m.mutate()}><ClipboardPaste size={16} />Add to workspace</button></>}>
      <div className="small muted" style={{ marginBottom: 12 }}>In Copilot Studio open a topic → <b>⋯ → Open code editor</b>, copy everything and paste it here.</div>
      <div style={{ height: 380, border: "1px solid var(--c-edge)", borderRadius: 8, overflow: "hidden" }}>
        <CodeEditor value={content} onChange={setContent} language="yaml" /></div>
    </Modal>
  );
}
