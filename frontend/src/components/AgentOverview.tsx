import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { BookOpen, Bot, Eye, Loader2, MessageSquare, Pencil, Plus, Save, Sparkles, Trash2, Workflow, Wrench } from "lucide-react";
import { api, Component } from "../api";
import { useToast } from "./ui";
import { useWs } from "../pages/WorkspaceLayout";

const LIMIT = 8000; // Copilot Studio instructions limit

/** Copilot Studio-style agent overview: instructions, starters, orchestration and what the agent can use. */
export default function AgentOverview({ comp }: { comp: Component }) {
  const { ws, select, openAI } = useWs();
  const d = (comp.data ?? {}) as { instructions?: string; conversationStarters?: { title?: string; text?: string }[] };
  const qc = useQueryClient();
  const toast = useToast();
  const [instr, setInstr] = useState(d.instructions ?? "");
  const [preview, setPreview] = useState(false);
  const [starters, setStarters] = useState(d.conversationStarters ?? []);
  const saved = (r: { valid: boolean }) => { qc.invalidateQueries({ queryKey: ["ws", ws.info.id] }); toast(r.valid ? "Saved" : "Saved — validation errors", r.valid ? "success" : "error"); };
  const save = useMutation({
    mutationFn: (fields: Record<string, unknown>) => api.setFields(ws.info.id, comp.id, "", fields),
    onSuccess: saved, onError: (e: Error) => toast(e.message, "error"),
  });
  const settings = ws.components.find((c) => c.category === "settings");
  const genOn = (() => {
    const s = (settings?.data ?? {}) as Record<string, any>;
    const v = s.GenerativeActionsEnabled ?? s.settings?.GenerativeActionsEnabled;
    return v === undefined ? true : !!v;
  })();
  const toggleGen = useMutation({
    mutationFn: () => settings
      ? api.setFields(ws.info.id, settings.id, "", { GenerativeActionsEnabled: !genOn })
      : api.writeFile(ws.info.id, "settings.mcs.yml", `GenerativeActionsEnabled: ${!genOn}\n`),
    onSuccess: saved, onError: (e: Error) => toast(e.message, "error"),
  });

  const main = (c: Component) => !c.owner;
  const topics = ws.components.filter((c) => c.category === "topic" && main(c));
  const tools = ws.components.filter((c) => c.category === "action" && main(c));
  const children = ws.components.filter((c) => c.category === "childAgent");
  const knowledge = ws.components.filter((c) => c.category === "knowledge");

  return (
    <div className="overview">
      <section className="card stack">
        <div className="row between"><div className="row"><Bot size={16} color="var(--c-primary)" /><b>Instructions</b></div>
          <div className="row">
            <span className={`tiny ${instr.length > LIMIT ? "" : "faint"}`} style={instr.length > LIMIT ? { color: "var(--c-danger)" } : undefined}>{instr.length} / {LIMIT}</span>
            <button className="btn btn-ghost btn-sm" onClick={() => setPreview((p) => !p)}>{preview ? <Pencil size={12} /> : <Eye size={12} />}{preview ? "Edit" : "Preview"}</button>
            <button className="btn btn-ghost btn-sm" onClick={() => openAI({ mode: "modify", target: comp.id, prompt: "Improve the agent instructions: " })}><Sparkles size={12} />Improve with AI</button>
          </div></div>
        {preview ? <div className="msg-ai small" style={{ minHeight: 160 }}><ReactMarkdown remarkPlugins={[remarkGfm]}>{instr}</ReactMarkdown></div>
          : <textarea className="form-input" rows={10} value={instr} onChange={(e) => setInstr(e.target.value)}
                      placeholder="Describe the agent's role, tone, which tools and child agents to use when, and what it must never do." />}
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <button className="btn btn-primary btn-sm" disabled={instr === (d.instructions ?? "") || save.isPending} onClick={() => save.mutate({ instructions: instr })}>
            {save.isPending ? <Loader2 size={14} className="spin" /> : <Save size={14} />}Save instructions</button></div>
      </section>

      <div className="grid-auto">
        <section className="card stack">
          <div className="row"><MessageSquare size={16} color="var(--c-primary)" /><b>Conversation starters</b></div>
          {starters.map((s, i) => (
            <div key={i} className="row">
              <input className="form-input" style={{ width: 140 }} placeholder="Title" value={s.title ?? ""} onChange={(e) => setStarters(starters.map((x, j) => (j === i ? { ...x, title: e.target.value } : x)))} />
              <input className="form-input grow" placeholder="Message sent when clicked" value={s.text ?? ""} onChange={(e) => setStarters(starters.map((x, j) => (j === i ? { ...x, text: e.target.value } : x)))} />
              <button className="btn-icon" onClick={() => setStarters(starters.filter((_, j) => j !== i))} aria-label="Remove starter"><Trash2 size={14} /></button>
            </div>))}
          <div className="row between">
            <button className="btn btn-ghost btn-sm" onClick={() => setStarters([...starters, { title: "", text: "" }])} disabled={starters.length >= 6}><Plus size={12} />Add starter</button>
            <button className="btn btn-secondary btn-sm" disabled={JSON.stringify(starters) === JSON.stringify(d.conversationStarters ?? []) || save.isPending}
                    onClick={() => save.mutate({ conversationStarters: starters.filter((s) => s.title || s.text) })}><Save size={14} />Save</button>
          </div>
        </section>
        <section className="card stack">
          <div className="row"><Sparkles size={16} color="var(--c-primary)" /><b>Orchestration</b></div>
          <label className="row small" style={{ alignItems: "flex-start" }}>
            <input type="checkbox" checked={genOn} disabled={toggleGen.isPending} onChange={() => toggleGen.mutate()} />
            <span><b>Generative orchestration</b><br /><span className="muted">The agent picks topics, tools and child agents from their descriptions. Off = classic trigger-phrase routing.</span></span>
          </label>
          <div className="tiny faint">Stored in settings.mcs.yml · GenerativeActionsEnabled</div>
        </section>
      </div>

      <DescCards title="Topics" icon={<MessageSquare size={14} />} items={topics} field={{ pointer: "", key: "modelDescription" }}
                 empty="No topics yet" onOpen={select} onNew={() => openAI({ mode: "topic" })} />
      <DescCards title="Tools" icon={<Wrench size={14} />} items={tools} field={{ pointer: "", key: "modelDescription" }}
                 empty="No tools yet" onOpen={select} onNew={() => openAI({ mode: "flow" })} />
      <DescCards title="Child agents" icon={<Bot size={14} />} items={children} field={{ pointer: "beginDialog", key: "description" }}
                 empty="No child agents" onOpen={select} onNew={() => openAI({ mode: "childAgent" })} />
      {knowledge.length > 0 && <DescCards title="Knowledge" icon={<BookOpen size={14} />} items={knowledge} empty="" onOpen={select} />}
      {ws.components.some((c) => c.category === "workflow") && (
        <div className="small muted row"><Workflow size={12} />{ws.components.filter((c) => c.category === "workflow").length} agent flow(s) — open them from the explorer.</div>
      )}
    </div>
  );
}

function DescCards({ title, icon, items, field, empty, onOpen, onNew }: {
  title: string; icon: JSX.Element; items: Component[]; field?: { pointer: string; key: string }; empty: string;
  onOpen: (id: string) => void; onNew?: () => void;
}) {
  return (
    <section className="stack">
      <div className="row between"><div className="row small"><b>{title}</b><span className="pill-sm">{items.length}</span></div>
        {onNew && <button className="btn btn-ghost btn-sm" onClick={onNew}><Sparkles size={12} />New with AI</button>}</div>
      {items.length === 0 ? <div className="small faint">{empty}</div> : (
        <div className="grid-auto">{items.map((c) => <DescCard key={c.id} c={c} icon={icon} field={field} onOpen={onOpen} />)}</div>
      )}
    </section>
  );
}

function DescCard({ c, icon, field, onOpen }: { c: Component; icon: JSX.Element; field?: { pointer: string; key: string }; onOpen: (id: string) => void }) {
  const { ws } = useWs();
  const qc = useQueryClient();
  const toast = useToast();
  const d = (c.data ?? {}) as Record<string, any>;
  const current: string = field ? String((field.pointer ? d[field.pointer]?.[field.key] : d[field.key]) ?? "") : String(d.source?.site ?? "");
  const [text, setText] = useState(current);
  const [editing, setEditing] = useState(false);
  const save = useMutation({
    mutationFn: () => api.setFields(ws.info.id, c.id, field!.pointer, { [field!.key]: text.trim() || null }),
    onSuccess: () => { setEditing(false); qc.invalidateQueries({ queryKey: ["ws", ws.info.id] }); toast("Saved", "success"); },
    onError: (e: Error) => toast(e.message, "error"),
  });
  const name = (d.modelDisplayName as string) || c.displayName;
  return (
    <div className="card stack" style={{ padding: 12, gap: 6 }}>
      <div className="row"><span className="icon-box" style={{ width: 28, height: 28, borderRadius: 8 }}>{icon}</span>
        <b className="small grow ellipsis">{name}</b>
        <button className="btn btn-ghost btn-sm" onClick={() => onOpen(c.id)}>Open</button></div>
      {editing ? (
        <>
          <textarea className="form-input" rows={3} value={text} onChange={(e) => setText(e.target.value)} />
          <div className="row" style={{ justifyContent: "flex-end" }}>
            <button className="btn btn-ghost btn-sm" onClick={() => { setText(current); setEditing(false); }}>Cancel</button>
            <button className="btn btn-primary btn-sm" disabled={save.isPending} onClick={() => save.mutate()}><Save size={12} />Save</button></div>
        </>
      ) : (
        <button className="desc-text" onClick={() => field && setEditing(true)} disabled={!field} title={field ? "Click to edit — the orchestrator routes on this" : undefined}>
          {current || <span className="faint">{field ? "No description — the orchestrator can't route here well. Click to add one." : ""}</span>}
        </button>
      )}
    </div>
  );
}
