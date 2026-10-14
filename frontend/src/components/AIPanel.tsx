import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  AlertTriangle, BookOpen, Bot, Check, CheckCircle2, ChevronDown, ChevronRight, Circle, FileCode2, FlaskConical,
  HelpCircle, Library, ListChecks, Loader2, MessageSquare, Send, ShieldCheck, Sparkles, Square, Trash2, Users, Wand2,
  Workflow, Wrench, X, XCircle,
} from "lucide-react";
import { AiEvent, AiTurn, api, ChatTurn, Mode, Proposal, Requirement } from "../api";
import { fileLabel } from "../lib/labels";
import { useToast } from "./ui";
import { useWs } from "../pages/WorkspaceLayout";

export interface AIRequest { mode?: Mode; target?: string | null; findings?: string[]; prompt?: string }

const MODES: { id: Mode; label: string; icon: JSX.Element; needsTarget?: boolean; hint: string; starters: string[] }[] = [
  { id: "auto", label: "Auto", icon: <Wand2 size={12} />, hint: "Describe what you want — the builder picks agent, topic, flow, child agent, modify or ask.",
    starters: ["IT helpdesk orchestrator with an HR child agent, a password reset topic and a ticket flow",
               "Collect a leave request with an adaptive card form, validate dates with Power Fx and confirm",
               "Create a SharePoint list item for a new ticket and email the requester, with try/catch"] },
  { id: "agent", label: "Agent", icon: <Bot size={12} />, hint: "Design a whole agent: instructions, topics, child agents, tools and flows.",
    starters: ["Sales assistant grounded on SharePoint with a quote request flow and Teams hardening"] },
  { id: "topic", label: "Topic", icon: <MessageSquare size={12} />, hint: "Generate a new topic with nodes, Power Fx and cards.",
    starters: ["Order status lookup: ask order number, call a flow, branch on status, escalate if delayed"] },
  { id: "flow", label: "Flow", icon: <Workflow size={12} />, hint: "Agent flow (Power Automate) + the tool that exposes it.",
    starters: ["Look up a user's manager in Office 365 Users and return name and email"] },
  { id: "childAgent", label: "Child agent", icon: <Users size={12} />, hint: "Specialist child agent for multi-agent orchestration.",
    starters: ["Finance specialist for expense policy and invoice status questions"] },
  { id: "modify", label: "Modify", icon: <Wrench size={12} />, needsTarget: true, hint: "Change the selected component.",
    starters: ["Add an adaptive card confirmation before the final message", "Add error handling and a fallback to escalation"] },
  { id: "ask", label: "Ask", icon: <HelpCircle size={12} />, hint: "Ask anything about Copilot Studio or this agent.",
    starters: ["When should I use a child agent vs a topic?", "How do I pass context from the orchestrator into a topic?"] },
];
const MODE_LABEL: Record<string, string> = { agent: "agent", topic: "topic", flow: "flow", childAgent: "child agent", modify: "modify", fix: "fix", ask: "question" };

interface Live { prompt: string; events: AiEvent[]; proposal?: Proposal; error?: string; stopped?: boolean; done?: boolean }

export default function AIPanel({ wid, selected, request, onApplied }: {
  wid: string; selected: { id: string; name: string } | null;
  request: AIRequest | null; onApplied: (paths: string[]) => void;
}) {
  const { setPending, openReview } = useWs();
  const qc = useQueryClient();
  const toast = useToast();
  const [mode, setMode] = useState<Mode>("auto");
  const [prompt, setPrompt] = useState("");
  const [patterns, setPatterns] = useState<string[]>([]);
  const [showPatterns, setShowPatterns] = useState(false);
  const [findings, setFindings] = useState<string[]>([]);
  const [target, setTarget] = useState<string | null>(null);
  const [useSelection, setUseSelection] = useState(true);
  const [live, setLive] = useState<Live | null>(null);
  const abort = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const { data: hist } = useQuery({ queryKey: ["ai-history", wid], queryFn: () => api.aiHistory(wid) });
  const { data: patternList = [] } = useQuery({ queryKey: ["patterns"], queryFn: api.patterns, staleTime: Infinity });

  useEffect(() => {
    if (!request) return;
    setMode(request.mode ?? "auto");
    if (request.prompt !== undefined) setPrompt(request.prompt);
    setFindings(request.findings ?? []);
    setTarget(request.target ?? null);
    setUseSelection(true);
  }, [request]);

  const turns = hist?.turns ?? [];
  const proposals = hist?.proposals ?? {};

  // The newest unapplied proposal drives the "AI · new / changed" badges in the explorer.
  useEffect(() => {
    if (live?.proposal && !live.proposal.applied) { setPending(live.proposal); return; }
    const last = [...turns].reverse().find((t) => t.role === "assistant" && t.proposalId);
    const p = last?.proposalId ? proposals[last.proposalId] : undefined;
    setPending(p && !p.applied && p.files.length ? p : null);
  }, [hist, live, setPending]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { bottom.current?.scrollIntoView({ behavior: "smooth" }); }, [turns.length, live?.events.length, live?.done]);

  const m = MODES.find((x) => x.id === mode)!;
  const effTarget = target ?? (useSelection ? selected?.id ?? null : null);
  const busy = !!live && !live.done;

  const send = async (text?: string) => {
    const body = (text ?? prompt).trim() || (findings.length ? "Fix these problems." : "");
    if (!body || busy) return;
    if (m.needsTarget && !effTarget) { toast("Select a component in the explorer first", "error"); return; }
    const history: ChatTurn[] = turns.slice(-6).map((t) => ({ role: t.role, content: t.text || (t.error ? `(failed: ${t.error})` : "") }));
    const ctrl = new AbortController();
    abort.current = ctrl;
    setPrompt("");
    const state: Live = { prompt: body, events: [] };
    setLive(state);
    try {
      await api.generateStream(wid, { mode: findings.length && mode === "auto" ? "fix" : mode, prompt: body, target: effTarget, findings, patterns, history },
        (e) => {
          if (e.type === "proposal") state.proposal = e.data.proposal as Proposal;
          else if (e.type === "error") state.error = String(e.data.text);
          else if (e.type !== "done") state.events = [...state.events, e];
          setLive({ ...state });
        }, ctrl.signal);
    } catch (e) {
      if ((e as Error).name === "AbortError") state.stopped = true;
      else state.error = (e as Error).message;
    } finally {
      abort.current = null;
      setFindings([]);
      // An agent is designed once; follow-ups go through Auto so they iterate on the files instead.
      if (mode === "agent" && state.proposal) setMode("auto");
      await qc.invalidateQueries({ queryKey: ["ai-history", wid] });
      setLive(state.stopped || state.error ? { ...state, done: true } : null);
    }
  };

  const clear = useMutation({
    mutationFn: () => api.clearAiHistory(wid),
    onSuccess: () => { setLive(null); qc.invalidateQueries({ queryKey: ["ai-history", wid] }); },
  });

  return (
    <div className="dock-body">
      <div className="pane-bar">
        <span className="tiny faint grow ellipsis">PwC GenAI · Microsoft Copilot Studio knowledge pack</span>
        {turns.length > 0 && <button className="btn btn-ghost btn-sm" onClick={() => clear.mutate()} title="Clear the conversation"><Trash2 size={12} />Clear</button>}
      </div>
      <div className="panel-body">
        {turns.length === 0 && !live && (
          <div className="stack" style={{ gap: 12 }}>
            <div className="muted small">{m.hint}</div>
            <div className="row wrap">{m.starters.map((s) => <button key={s} className="suggestion" onClick={() => setPrompt(s)}>{s}</button>)}</div>
          </div>
        )}
        {turns.map((t, i) => (
          <TurnView key={`${t.ts}-${i}`} t={t} proposal={t.proposalId ? proposals[t.proposalId] : undefined} wid={wid}
                    onApplied={onApplied} onOpenReview={openReview} onFollowUp={(s) => send(s)} />
        ))}
        {live && (
          <>
            {turns[turns.length - 1]?.role !== "user" || turns[turns.length - 1]?.text !== live.prompt
              ? <div className="msg"><div className="msg-user">{live.prompt}</div></div> : null}
            <div className="msg">
              <RunTimeline events={live.events} live={!live.done} proposal={live.proposal} error={live.error} stopped={live.stopped} />
              {live.proposal && <ProposalResult p={live.proposal} wid={wid} onApplied={onApplied} onOpenReview={openReview} onFollowUp={(s) => send(s)} />}
            </div>
          </>
        )}
        <div ref={bottom} />
      </div>

      <div className="panel-foot stack">
        <div className="row wrap" style={{ gap: 4 }}>
          {MODES.map((x) => (
            <button key={x.id} className={`filter-btn${mode === x.id && !findings.length ? " active" : ""}`}
                    onClick={() => { setMode(x.id); setFindings([]); }} title={x.hint}>
              <span className="row" style={{ gap: 4 }}>{x.icon}{x.label}</span></button>))}
        </div>
        {(effTarget || findings.length > 0) && (
          <div className="row wrap small">
            {effTarget && <span className="pill-sm accent"><FileCode2 size={11} />context: {selected?.id === effTarget ? selected.name : fileLabel(effTarget)}
              <button className="btn-icon" style={{ width: 16, height: 16 }} title="Don't use as context" onClick={() => { setTarget(null); setUseSelection(false); }}><X size={10} /></button></span>}
            {findings.length > 0 && <span className="pill-sm warning">fixing {findings.length} finding(s)
              <button className="btn-icon" style={{ width: 16, height: 16 }} onClick={() => setFindings([])}><X size={10} /></button></span>}
          </div>
        )}
        {!effTarget && selected && !useSelection && (
          <button className="suggestion" style={{ alignSelf: "flex-start" }} onClick={() => setUseSelection(true)}>
            <FileCode2 size={11} />Use {selected.name} as context</button>
        )}
        <div>
          <button className="btn-ghost btn btn-sm" onClick={() => setShowPatterns((s) => !s)}>
            {showPatterns ? <ChevronDown size={14} /> : <ChevronRight size={14} />}<Library size={14} />
            Patterns{patterns.length ? ` (${patterns.length})` : ""}<span className="faint tiny">· auto-suggested when relevant</span></button>
          {showPatterns && (
            <div className="row wrap" style={{ gap: 4, maxHeight: 120, overflowY: "auto", marginTop: 6 }}>
              {patternList.map((p) => (
                <button key={p.id} title={p.description} className={`suggestion${patterns.includes(p.id) ? " active" : ""}`}
                        onClick={() => setPatterns((s) => s.includes(p.id) ? s.filter((x) => x !== p.id) : [...s, p.id])}>{p.name}</button>))}
            </div>
          )}
        </div>
        <div className="row" style={{ alignItems: "flex-end" }}>
          <textarea className="form-input" rows={3} value={prompt} placeholder={`${m.hint} (⌘⏎ to send)`}
                    onChange={(e) => setPrompt(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) send(); }} />
          {busy ? <button className="btn btn-danger" onClick={() => abort.current?.abort()} aria-label="Stop" title="Stop"><Square size={14} /></button>
            : <button className="btn btn-primary" onClick={() => send()} aria-label="Send"><Send size={16} /></button>}
        </div>
      </div>
    </div>
  );
}

function TurnView({ t, proposal, wid, onApplied, onOpenReview, onFollowUp }: {
  t: AiTurn; proposal?: Proposal; wid: string; onApplied: (p: string[]) => void;
  onOpenReview: (p: Proposal) => void; onFollowUp: (s: string) => void;
}) {
  if (t.role === "user") return <div className="msg"><div className="msg-user">{t.text}</div></div>;
  return (
    <div className="msg">
      <RunTimeline events={t.events ?? []} live={false} proposal={proposal} error={t.error} stopped={t.stopped} />
      {proposal && proposal.files.length > 0 ? <ProposalResult p={proposal} wid={wid} onApplied={onApplied} onOpenReview={onOpenReview} onFollowUp={onFollowUp} />
        : proposal && <div className="msg-ai"><ReactMarkdown remarkPlugins={[remarkGfm]}>{proposal.summary}</ReactMarkdown></div>}
      {!proposal && t.text && <div className="msg-ai"><ReactMarkdown remarkPlugins={[remarkGfm]}>{t.text}</ReactMarkdown></div>}
    </div>
  );
}

// --- live progress ----------------------------------------------------------------------------------
type StepState = "pending" | "running" | "done" | "error";
interface Step { key: string; icon: JSX.Element; title: string; state: StepState; body?: JSX.Element }

function StateIcon({ s }: { s: StepState }) {
  if (s === "running") return <Loader2 size={14} className="spin" color="var(--c-primary)" />;
  if (s === "done") return <CheckCircle2 size={14} color="var(--c-success)" />;
  if (s === "error") return <XCircle size={14} color="var(--c-danger)" />;
  return <Circle size={14} color="var(--c-fg3)" />;
}

export function RunTimeline({ events, live, proposal, error, stopped }: {
  events: AiEvent[]; live: boolean; proposal?: Proposal; error?: string; stopped?: boolean;
}) {
  const [open, setOpen] = useState<boolean | null>(null);
  const v = useMemo(() => {
    const ctx = events.find((e) => e.type === "context")?.data;
    const plan = events.filter((e) => e.type === "plan").map((e) => String(e.data.text));
    const files = new Map<string, { label: string; status: string; added?: number; removed?: number; valid?: boolean; errors?: number; messages?: string[] }>();
    const parsed = events.find((e) => e.type === "parsed");
    for (const e of events) {
      if (e.type === "file") files.set(e.data.path, { ...(files.get(e.data.path) ?? {}), label: e.data.label, status: "writing" });
      if (e.type === "parsed") for (const f of e.data.files) files.set(f.path, { ...(files.get(f.path) ?? {}), label: f.label, status: f.delete ? "delete" : f.isNew ? "new" : "changed", added: f.added, removed: f.removed });
      if (e.type === "validate") { const f = files.get(e.data.path); if (f) files.set(e.data.path, { ...f, valid: e.data.valid, errors: e.data.errors, messages: e.data.messages }); }
    }
    const repairs = events.filter((e) => e.type === "repair").map((e) => ({
      round: e.data.round as number, files: e.data.files as { label: string; errors: string[] }[],
      result: events.find((x) => x.type === "repaired" && x.data.round === e.data.round)?.data }));
    const analyze = events.find((e) => e.type === "analyze")?.data;
    const coverage = events.find((e) => e.type === "coverage")?.data;
    const coverageRunning = events.some((e) => e.type === "step" && e.data.step === "coverage");
    const notices = events.filter((e) => e.type === "notice").map((e) => String(e.data.text));
    const writing = events.filter((e) => e.type === "writing").pop()?.data.chars as number | undefined;
    return { ctx, plan, files, parsed: !!parsed, repairs, analyze, coverage, coverageRunning, notices, writing,
             validated: events.some((e) => e.type === "validate") };
  }, [events]);

  const failed = !!error || !!stopped;
  const stage = proposal ? 6 : v.analyze ? 5 : v.validated ? 4 : v.parsed ? 3 : v.files.size || v.plan.length ? 2 : v.ctx ? 1 : 0;
  const st = (n: number): StepState => (stage > n || proposal ? "done" : stage === n ? (failed ? "error" : live ? "running" : "done") : "pending");
  const fileVals = [...v.files.values()];
  const invalid = fileVals.filter((f) => f.valid === false).length;

  const steps: Step[] = [
    { key: "read", icon: <BookOpen size={12} />, state: v.ctx ? "done" : live ? "running" : failed ? "error" : "pending",
      title: v.ctx ? `Read Microsoft's reference · ${v.ctx.guides.length} guide(s), ${v.ctx.templates.length} template(s)${v.ctx.patterns.length ? `, ${v.ctx.patterns.length} pattern(s)` : ""}${v.ctx.schemas?.length ? `, ${v.ctx.schemas.length} schema(s)` : ""} · ${MODE_LABEL[v.ctx.mode] ?? v.ctx.mode}${v.ctx.inferred ? " (auto)" : ""}` : "Reading the knowledge pack",
      body: v.ctx && <div className="row wrap" style={{ gap: 4 }}>{[...v.ctx.guides, ...v.ctx.templates.map((t: string) => t.split("/").pop()), ...v.ctx.patterns].map((g: string) => <span key={g} className="pill-sm">{g}</span>)}</div> },
  ];
  if (v.ctx?.mode !== "ask" || v.plan.length) steps.push({ key: "plan", icon: <Sparkles size={12} />, state: v.plan.length ? (stage > 2 ? "done" : st(2)) : st(2),
    title: v.plan.length ? "The model's plan" : "Planning",
    body: v.plan.length ? <ul className="plan-list">{v.plan.map((p, i) => <li key={i}>{p}</li>)}</ul> : undefined });
  if (v.files.size || (v.ctx && v.ctx.mode !== "ask")) steps.push({ key: "write", icon: <FileCode2 size={12} />, state: v.parsed ? "done" : st(2),
    title: v.files.size ? `${v.parsed ? "Wrote" : "Writing"} ${v.files.size} file(s)${!v.parsed && v.writing ? ` · ${(v.writing / 1000).toFixed(1)}k chars` : ""}` : "Writing files",
    body: v.files.size ? <div className="stack" style={{ gap: 2 }}>{[...v.files.entries()].map(([p, f]) => (
      <div key={p} className="file-line" title={p}>
        {f.status === "writing" && !v.parsed ? <Loader2 size={11} className="spin" /> : f.valid === false ? <XCircle size={11} color="var(--c-danger)" /> : <Check size={11} color="var(--c-success)" />}
        <span className="grow ellipsis">{f.label}</span>
        {f.status !== "writing" && <span className={`pill-sm ${f.status === "new" ? "ok" : f.status === "delete" ? "error" : "warning"}`}>{f.status}</span>}
        {f.added !== undefined && <span className="tiny mono"><span style={{ color: "var(--c-success)" }}>+{f.added}</span> <span style={{ color: "var(--c-danger)" }}>−{f.removed}</span></span>}
      </div>))}</div> : undefined });
  if (v.validated || (v.parsed && fileVals.length)) steps.push({ key: "validate", icon: <ShieldCheck size={12} />,
    state: v.validated ? (v.repairs.length || !invalid || !live ? (invalid && !live && !v.repairs.length ? "error" : "done") : "running") : st(3),
    title: v.validated ? (invalid ? `Schema check: ${invalid} file(s) with errors` : `Schema check: all ${fileVals.filter((f) => f.valid !== undefined).length} file(s) valid`) : "Checking against Microsoft's schema",
    body: invalid ? <div className="stack" style={{ gap: 2 }}>{fileVals.filter((f) => f.valid === false).map((f) => (
      <div key={f.label} className="tiny"><b>{f.label}</b>{f.messages?.map((msg, i) => <div key={i} className="faint ellipsis">{msg}</div>)}</div>))}</div> : undefined });
  for (const r of v.repairs) steps.push({ key: `repair${r.round}`, icon: <Wrench size={12} />,
    state: r.result ? (r.result.valid ? "done" : "error") : live ? "running" : "error",
    title: `Repair round ${r.round}: fixing ${r.files.map((f) => f.label).join(", ")}${r.result ? (r.result.valid ? " — fixed" : " — still has errors") : ""}`,
    body: <div className="stack" style={{ gap: 2 }}>{r.files.flatMap((f) => f.errors.map((e, i) => <div key={f.label + i} className="tiny faint ellipsis">{e}</div>))}</div> });
  if (v.analyze || stage >= 4) steps.push({ key: "analyze", icon: <ShieldCheck size={12} />, state: v.analyze ? "done" : st(5),
    title: v.analyze ? `Analyzer on the merged result: ${v.analyze.errors} error(s), ${v.analyze.warnings} warning(s)` : "Running the analyzer (redirects, variables, triggers, flows)" });
  if (v.coverage || v.coverageRunning) steps.push({ key: "coverage", icon: <ListChecks size={12} />,
    state: v.coverage ? (v.coverage.missing ? "error" : "done") : live ? "running" : "pending",
    title: v.coverage ? `Requirements: ${v.coverage.covered} covered, ${v.coverage.partial} partial, ${v.coverage.missing} missing` : "Checking each requirement against the files" });
  for (const n of v.notices) steps.push({ key: n, icon: <AlertTriangle size={12} />, state: "done", title: n });
  if (error) steps.push({ key: "error", icon: <AlertTriangle size={12} />, state: "error", title: `Failed: ${error}` });
  if (stopped) steps.push({ key: "stopped", icon: <Square size={12} />, state: "error", title: "Stopped" });

  const expanded = open ?? live;
  const summary = steps.filter((s) => s.state === "done").length;
  return (
    <div className={`timeline${live ? " live" : ""}`}>
      <button className="timeline-head" onClick={() => setOpen(!expanded)}>
        {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        {live ? <Loader2 size={12} className="spin" color="var(--c-primary)" /> : failed ? <XCircle size={12} color="var(--c-danger)" /> : <CheckCircle2 size={12} color="var(--c-success)" />}
        <span className="grow ellipsis">{live ? steps.filter((s) => s.state === "running").pop()?.title ?? "Working…" : failed ? (stopped ? "Stopped" : "Failed") : `Done · ${summary} step(s)${v.files.size ? ` · ${v.files.size} file(s)` : ""}`}</span>
      </button>
      {expanded && (
        <div className="timeline-steps">
          {steps.map((s) => (
            <div key={s.key} className={`tl-step ${s.state}`}>
              <div className="tl-icon"><StateIcon s={s.state} /></div>
              <div className="grow" style={{ minWidth: 0 }}>
                <div className="tl-title">{s.title}</div>
                {s.body && <div className="tl-body">{s.body}</div>}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// --- result ----------------------------------------------------------------------------------------------
export function useApply(p: Proposal, onApplied: (paths: string[]) => void) {
  const qc = useQueryClient();
  const toast = useToast();
  const { setPending } = useWs();
  return useMutation({
    mutationFn: (paths: string[]) => api.accept(p.id, paths),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["ws", p.workspaceId] });
      qc.invalidateQueries({ queryKey: ["ai-history", p.workspaceId] });
      setPending(null);
      toast(`Applied ${r.written.length} file(s)`, "success");
      onApplied(r.written);
    },
    onError: (e: Error) => toast(e.message, "error"),
  });
}

/** Schema errors plus error-level analyzer findings: applying those needs a second, explicit click. */
export function useApplyGuard(p: Proposal) {
  const [armed, setArmed] = useState(false);
  const errors = p.files.reduce((n, f) => n + (f.report?.issues.filter((i) => i.severity === "error").length ?? 0), 0)
    + p.findings.filter((f) => f.severity === "error").length;
  return { errors, armed, confirm: (run: () => void) => (errors && !armed ? setArmed(true) : run()) };
}

export const gapsPrompt = (gaps: Requirement[]) =>
  `Implement these requirements that are missing or only partly covered:\n${gaps.map((r) => `- ${r.requirement}${r.comment ? ` (${r.comment})` : ""}`).join("\n")}`;

/** Each requirement from the request and where the proposal implements it. */
export function CoverageList({ items, compact }: { items: Requirement[]; compact?: boolean }) {
  const shown = compact ? items.filter((r) => r.status !== "covered") : items;
  const covered = items.length - items.filter((r) => r.status !== "covered").length;
  return (
    <div className="stack" style={{ gap: 4 }}>
      {shown.map((r, i) => (
        <div key={i} className="row small" style={{ alignItems: "flex-start", gap: 6 }}>
          <span className={`pill-sm ${r.status === "covered" ? "ok" : r.status === "partial" ? "warning" : "error"}`}>{r.status}</span>
          <span className="grow" style={{ minWidth: 0 }}>{r.requirement}
            {(r.where || r.comment) && <div className="tiny faint">{r.where && <span className="mono">{r.where}</span>}{r.where && r.comment ? " · " : ""}{r.comment}</div>}
          </span>
        </div>))}
      {compact && covered > 0 && <div className="tiny faint"><CheckCircle2 size={11} color="var(--c-success)" /> {covered} of {items.length} requirement(s) covered</div>}
    </div>
  );
}

function ProposalResult({ p, wid, onApplied, onOpenReview, onFollowUp }: {
  p: Proposal; wid: string; onApplied: (paths: string[]) => void; onOpenReview: (p: Proposal) => void; onFollowUp: (s: string) => void;
}) {
  const { ws, openTest, select } = useWs();
  const nav = useNavigate();
  const apply = useApply(p, onApplied);
  const guard = useApplyGuard(p);
  const errors = p.files.reduce((n, f) => n + (f.report?.issues.filter((i) => i.severity === "error").length ?? 0), 0);
  const findings = p.findings.filter((f) => f.severity !== "info");
  const gaps = (p.coverage ?? []).filter((r) => r.status !== "covered");
  const applied = p.applied;
  return (
    <div className="msg-ai stack" style={{ gap: 10, marginTop: 8 }}>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{p.summary}</ReactMarkdown>
      {(p.coverage?.length ?? 0) > 0 && (
        <div className="small"><b>Requirements</b><div style={{ marginTop: 4 }}><CoverageList items={p.coverage!} compact /></div></div>
      )}
      {p.notes.length > 0 && (
        <div className="small"><b>Follow-ups for you</b><ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>{p.notes.map((n, i) => <li key={i}>{n}</li>)}</ul></div>
      )}
      <div className="card stack" style={{ padding: 10, gap: 4 }}>
        <div className="row between"><b className="small">Files changed</b>
          <div className="row" style={{ gap: 4 }}>
            {p.valid ? <span className="pill-sm ok"><CheckCircle2 size={11} />schema-valid</span> : <span className="pill-sm error">{errors} error(s)</span>}
            {p.repairRounds > 0 && <span className="pill-sm">repaired ×{p.repairRounds}</span>}
            {findings.length > 0 && <span className="pill-sm warning">{findings.length} finding(s)</span>}
          </div></div>
        {p.files.map((f) => {
          const errs = f.report?.issues.filter((i) => i.severity === "error").length ?? 0;
          const done = applied?.includes(f.path);
          return (
            <button key={f.path} className="file-line clickable" title={f.path}
                    onClick={() => (done ? select(f.path.startsWith("workflows/") ? f.path.split("/").slice(0, 2).join("/") : f.path) : onOpenReview(p))}>
              {done ? <Check size={11} color="var(--c-success)" /> : errs ? <XCircle size={11} color="var(--c-danger)" /> : <FileCode2 size={11} />}
              <span className="grow ellipsis">{fileLabel(f.path, f.content, ws)}</span>
              <span className={`pill-sm ${f.content === null ? "error" : f.isNew ? "ok" : "warning"}`}>{f.content === null ? "delete" : f.isNew ? "new" : "changed"}</span>
            </button>
          );
        })}
      </div>
      {applied ? (
        <div className="row small" style={{ color: "var(--c-success)" }}><Check size={14} />Applied {applied.length} file(s)</div>
      ) : (
        <div className="stack" style={{ gap: 6 }}>
          {guard.armed && <div className="row small" style={{ color: "var(--c-danger)" }}><AlertTriangle size={14} />{guard.errors} error(s) will be written to the workspace. Click again to apply anyway.</div>}
          <div className="row">
            <button className="btn btn-secondary btn-sm" onClick={() => onOpenReview(p)}><FileCode2 size={14} />Review changes</button>
            <button className={`btn ${guard.armed ? "btn-danger" : "btn-primary"} btn-sm`} disabled={apply.isPending}
                    onClick={() => guard.confirm(() => apply.mutate(p.files.map((f) => f.path)))}>
              {apply.isPending ? <Loader2 size={14} className="spin" /> : guard.errors ? <AlertTriangle size={14} /> : <Check size={14} />}
              {guard.armed ? `Apply anyway (${guard.errors} error(s))` : `Apply all (${p.files.length})`}</button>
          </div>
        </div>
      )}
      <div className="row wrap" style={{ gap: 4 }}>
        {gaps.length > 0 && <button className="suggestion" onClick={() => onFollowUp(gapsPrompt(gaps))}><ListChecks size={11} />Cover {gaps.length} gap(s)</button>}
        {applied && <button className="suggestion" onClick={openTest}><FlaskConical size={11} />Test it</button>}
        <button className="suggestion" onClick={() => onFollowUp("Add error handling and an escalation path where something can fail")}>Add error handling</button>
        {findings.length > 0 && <button className="suggestion" onClick={() => onFollowUp(`Fix these analyzer findings:\n${findings.map((f) => `- ${f.component}: ${f.message}`).join("\n")}`)}>Fix {findings.length} finding(s)</button>}
        {applied && <button className="suggestion" onClick={() => nav(`/w/${wid}/docs`)}><BookOpen size={11} />Update docs</button>}
      </div>
    </div>
  );
}
