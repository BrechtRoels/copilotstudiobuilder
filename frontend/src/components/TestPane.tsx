import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  AlertTriangle, Bot, BookmarkPlus, BookOpen, CheckCircle2, ChevronDown, ChevronRight, Crosshair, CornerDownRight,
  GitBranch, Info, Loader2, MessageSquare, Play, RotateCcw, Send, Sparkles, Trash2, Upload, User, Workflow,
  Wrench, XCircle,
} from "lucide-react";
import { api, CaseResult, SimEvent, TestCase, TestSession } from "../api";
import AdaptiveCardView from "./AdaptiveCardView";
import { FlowRunModal } from "./FlowTest";
import { useToast } from "./ui";
import { Marks, useWs } from "../pages/WorkspaceLayout";

interface Entry { key: string; turn: number; user?: string; ev?: SimEvent }
type Tab = "chat" | "activity" | "variables" | "tests" | "settings";

/** Turn the current conversation into a regression test: what happened now becomes the expectation. */
function toCase(log: Entry[], n: number): TestCase {
  const turns: TestCase["turns"] = [];
  for (const e of log) {
    if (e.user !== undefined) { turns.push({ user: e.user, expect: {} }); continue; }
    const t = turns[turns.length - 1];
    const ev = e.ev;
    if (!t || !ev || e.turn === 0) continue;
    if (ev.type === "topic" && ev.data.action === "start" && !t.expect.topic) t.expect.topic = ev.data.name;
    if ((ev.type === "flow" || ev.type === "tool") && !t.expect.tool) t.expect.tool = ev.data.tool ?? ev.data.flow ?? ev.data.name;
    if (ev.type === "message" && !t.expect.contains && ev.data.text) {
      const first = String(ev.data.text).split(/(?<=[.!?])\s/)[0];
      t.expect.contains = first.length > 60 ? first.slice(0, 60) : first;
    }
  }
  return { name: `Conversation ${n}`, turns: turns.filter((t) => !t.user.startsWith("Submitted the card")) };
}

const RUN_MARK: Record<string, string> = { Succeeded: "ok", Failed: "failed", TimedOut: "failed", Skipped: "skipped", Cancelled: "failed" };

function settle(m: Marks, keepActive: boolean): Marks {
  if (keepActive) return m;
  const out: Marks = {};
  for (const [c, ptrs] of Object.entries(m)) {
    out[c] = Object.fromEntries(Object.entries(ptrs).map(([p, s]) => [p, s === "active" ? "visited" : s]));
  }
  return out;
}

export default function TestPane({ open }: { open: boolean }) {
  const { ws, version, select, setMarks } = useWs();
  const wid = ws.info.id;
  const navigate = useNavigate();
  const location = useLocation();
  const toast = useToast();
  const qc = useQueryClient();
  const [session, setSession] = useState<TestSession | null>(null);
  const [startVersion, setStartVersion] = useState(version);
  const [log, setLog] = useState<Entry[]>([]);
  const [busy, setBusy] = useState(false);
  const [typing, setTyping] = useState(false);
  const [awaiting, setAwaiting] = useState<string | null>(null);
  const [vars, setVars] = useState<Record<string, any> | null>(null);
  const [expired, setExpired] = useState(false);
  const [tab, setTab] = useState<Tab>("chat");
  const [track, setTrack] = useState(true);
  const [text, setText] = useState("");
  const [user, setUser] = useState({ DisplayName: "Test User", Email: "test.user@contoso.com", IsLoggedIn: true });
  const [runId, setRunId] = useState<string | null>(null);
  const turn = useRef(0);
  const bottom = useRef<HTMLDivElement>(null);
  const trackRef = useRef(track);
  trackRef.current = track;
  const where = useRef({ path: location.pathname, c: new URLSearchParams(location.search).get("c") });
  where.current = { path: location.pathname, c: new URLSearchParams(location.search).get("c") };

  const goTo = useCallback((componentId: string | null) => {
    if (!componentId) return;
    const onBuild = where.current.path === `/w/${wid}`;
    if (onBuild && where.current.c === componentId) return;
    if (onBuild) select(componentId);
    else navigate(`/w/${wid}?c=${encodeURIComponent(componentId)}`);
  }, [navigate, select, wid]);
  const follow = useCallback((componentId: string | null) => { if (trackRef.current) goTo(componentId); }, [goTo]);

  const handle = useCallback((ev: SimEvent, t: number) => {
    if (ev.type === "typing") { setTyping(true); return; }
    if (ev.type === "variables") { setVars(ev.data); return; }
    if (ev.type === "done") {
      setAwaiting(ev.data.awaiting ?? null);
      setMarks((m) => settle(m, !!ev.data.awaiting));
      return;
    }
    if (ev.type === "message") setTyping(false);
    if (ev.type === "step" && ev.componentId && ev.pointer) {
      const cid = ev.componentId, ptr = ev.pointer;
      setMarks((m) => ({ ...settle(m, false), [cid]: { ...settle(m, false)[cid], [ptr]: "active" } }));
      follow(cid);
    }
    if (ev.type === "topic" && ev.data.action === "start" && ev.componentId) {
      const cid = ev.componentId;
      setMarks((m) => ({ ...m, [cid]: { ...(m[cid] ?? {}), beginDialog: "visited" } }));
      follow(cid);
    }
    if (ev.type === "flow" && ev.data.flowComponent) {
      const marks = Object.fromEntries((ev.data.actions as { pointer: string; status: string }[])
        .map((a) => [a.pointer, RUN_MARK[a.status] ?? "visited"]));
      setMarks((m) => ({ ...m, [ev.data.flowComponent]: marks }));
    }
    setLog((l) => [...l, { key: `${t}-${ev.seq}`, turn: t, ev }]);
  }, [follow, setMarks]);

  const start = useCallback(async () => {
    if (session) api.endTest(session.sessionId).catch(() => {});
    setLog([]); setVars(null); setAwaiting(null); setExpired(false); setTyping(false);
    setMarks(() => ({}));
    turn.current = 0;
    setBusy(true);
    try {
      const s = await api.startTest(wid, user);
      setSession(s);
      setStartVersion(version);
      s.events.forEach((e) => handle(e, 0));
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  }, [session, wid, user, version, handle, setMarks, toast]);

  // start on first open
  const started = useRef(false);
  useEffect(() => { if (open && !started.current) { started.current = true; start(); } }, [open, start]);
  useEffect(() => { bottom.current?.scrollIntoView({ behavior: "smooth" }); }, [log, typing, tab]);
  useEffect(() => () => { setMarks(() => ({})); }, [setMarks]);

  const send = async (body: { text?: string; card?: Record<string, unknown> }) => {
    if (!session || busy) return;
    const t = ++turn.current;
    setLog((l) => [...l, { key: `u${t}`, turn: t, user: body.text ?? "Submitted the card" }]);
    setText("");
    setBusy(true);
    setAwaiting(null);
    setMarks((m) => settle(m, false));
    try {
      await api.sendTest(session.sessionId, body, (e) => handle(e, t));
    } catch (e) {
      const msg = (e as Error).message;
      if (msg.includes("expired")) setExpired(true);
      else toast(msg, "error");
    } finally {
      setBusy(false);
      setTyping(false);
    }
  };

  const starters = useMemo(() => {
    const agent = ws.components.find((c) => c.category === "agent");
    const d = (agent?.data ?? {}) as { conversationStarters?: { title?: string; text?: string }[] };
    return (d.conversationStarters ?? []).map((s) => s.text || s.title || "").filter(Boolean);
  }, [ws.components]);
  const lastMsgIdx = useMemo(() => { for (let i = log.length - 1; i >= 0; i--) if (log[i].ev?.type === "message") return i; return -1; }, [log]);
  const userAfterLast = log.slice(lastMsgIdx + 1).some((e) => e.user);
  const hasUser = log.some((e) => e.user);

  return (
    <div className="dock-body">
      <div className="pane-bar">
        <div className="tiny faint ellipsis grow">Simulated in Builder · {session?.orchestration ?? "…"} orchestration · {session?.mock ? "mock AI (offline)" : "PwC GenAI"}</div>
        <button className="btn-icon" title="Save this conversation as a test case" disabled={!hasUser}
                onClick={async () => {
                  try {
                    const existing = await api.testCases(wid);
                    await api.saveTestCases(wid, [...existing, toCase(log, existing.length + 1)]);
                    qc.invalidateQueries({ queryKey: ["test-cases", wid] });
                    toast("Saved as a test case — see the Tests tab", "success");
                  } catch (e) { toast((e as Error).message, "error"); }
                }}><BookmarkPlus size={16} /></button>
        <button className={`btn-icon${track ? " active-icon" : ""}`} title={track ? "Track between topics: on" : "Track between topics: off"}
                onClick={() => setTrack((t) => !t)} style={track ? { color: "var(--c-primary)" } : undefined}><Crosshair size={16} /></button>
        <button className="btn-icon" title="Restart conversation" onClick={start} disabled={busy}><RotateCcw size={16} /></button>
      </div>
      {(version !== startVersion || expired) && session && (
        <div className="test-banner"><Info size={14} />
          <span className="grow">{expired ? "This test session expired." : "The agent changed since this conversation started."}</span>
          <button className="btn btn-secondary btn-sm" onClick={start}><RotateCcw size={12} />Restart</button></div>
      )}
      <div className="tabs" style={{ padding: "0 12px" }}>
        {(["chat", "activity", "variables", "tests", "settings"] as Tab[]).map((t) => (
          <button key={t} className={`tab${tab === t ? " active" : ""}`} onClick={() => setTab(t)} style={{ padding: "10px 8px" }}>
            {{ chat: "Chat", activity: "Activity", variables: "Variables", tests: "Tests", settings: "Settings" }[t]}</button>))}
      </div>

      <div className="panel-body">
        {tab === "chat" && (
          <>
            {log.map((e, i) => <ChatEntry key={e.key} e={e} wid={wid}
                                          live={i === lastMsgIdx && !userAfterLast && awaiting === "card"}
                                          onCard={(d) => send({ card: d })} onRun={setRunId}
                                          onOpen={goTo} />)}
            {typing && <div className="chat-row bot"><div className="chat-bubble typing"><i /><i /><i /></div></div>}
            {lastMsgIdx >= 0 && !userAfterLast && !busy && (
              <div className="row wrap" style={{ gap: 6, margin: "4px 0 8px" }}>
                {(log[lastMsgIdx].ev!.data.suggestions as string[]).map((s) => (
                  <button key={s} className="suggestion" onClick={() => send({ text: s })}>{s}</button>))}
                {!hasUser && starters.map((s) => <button key={s} className="suggestion" onClick={() => send({ text: s })}>{s}</button>)}
              </div>
            )}
            {busy && !typing && log.length === 0 && <div className="row muted small"><Loader2 size={14} className="spin" />Starting…</div>}
          </>
        )}
        {tab === "activity" && <Activity log={log} onRun={setRunId} onOpen={goTo} />}
        {tab === "variables" && <Variables vars={vars} />}
        {tab === "tests" && <Tests />}
        {tab === "settings" && <Settings user={user} setUser={setUser} onRestart={start} />}
        <div ref={bottom} />
      </div>

      {tab === "chat" && (
        <div className="panel-foot">
          <form className="row" onSubmit={(e) => { e.preventDefault(); if (text.trim()) send({ text: text.trim() }); }}>
            <input className="form-input grow" placeholder={awaiting === "card" ? "Answer with the card above, or type to switch topic" : "Type your message"}
                   value={text} onChange={(e) => setText(e.target.value)} disabled={!session || expired} autoFocus />
            <button className="btn btn-primary" type="submit" disabled={!text.trim() || busy || !session}>
              {busy ? <Loader2 size={16} className="spin" /> : <Send size={16} />}</button>
          </form>
        </div>
      )}
      {runId && <FlowRunModal wid={wid} runId={runId} onClose={() => setRunId(null)} />}
    </div>
  );
}

function ChatEntry({ e, live, onCard, onRun, onOpen }: {
  e: Entry; wid: string; live: boolean; onCard: (d: Record<string, unknown>) => void; onRun: (id: string) => void;
  onOpen: (componentId: string | null) => void;
}) {
  if (e.user !== undefined) return <div className="chat-row user"><div className="chat-bubble">{e.user}</div></div>;
  const ev = e.ev!;
  const d = ev.data;
  switch (ev.type) {
    case "message":
      return (
        <div>
          <div className="chat-row bot">
            <div className="chat-bubble">
              {d.text && <ReactMarkdown remarkPlugins={[remarkGfm]} components={{ a: (p) => <a {...p} target="_blank" rel="noreferrer" /> }}>{d.text}</ReactMarkdown>}
              {(d.attachments as unknown[]).map((a, i) => <AdaptiveCardView key={i} card={a} onSubmit={live ? onCard : undefined} disabled={!live} />)}
              {(d.citations as { n: number; source: string; excerpt: string }[])?.length > 0 && (
                <div className="chat-cites">{(d.citations as { n: number; source: string; excerpt: string }[]).map((c) => (
                  <div key={c.n} title={c.excerpt}>[{c.n}] {c.source}</div>))}</div>
              )}
            </div>
          </div>
          {(d.agent || d.source) && <div className="chat-meta">{d.agent ? `${d.agent} · ` : ""}{d.source ?? ""}</div>}
        </div>
      );
    case "topic":
      return d.action === "start" ? <Chip icon={<CornerDownRight size={11} />} onClick={() => onOpen(ev.componentId)} title={d.reason}>Topic: {d.name}</Chip> : null;
    case "routing":
      return <Chip icon={<GitBranch size={11} />} title={d.reason}>
        {d.mode === "interruption" ? "Topic switch" : d.candidates?.length ? `Recognized ${d.candidates[0].name} (${Number(d.candidates[0].score).toFixed(2)})` : "No topic matched"}</Chip>;
    case "plan":
      return d.action === "done" ? null : <Chip icon={<Sparkles size={11} />} title={d.thought}>Plan: {d.action}{d.id ? ` · ${String(d.id).split("/").pop()?.replace(/\.mcs\.ya?ml$/, "")}` : ""}{d.ai ? "" : " (mock)"}</Chip>;
    case "agent":
      return d.action === "start" ? <Chip icon={<Bot size={11} />}>Child agent: {d.name}</Chip> : null;
    case "flow":
      return <Chip icon={<Workflow size={11} />} onClick={() => onRun(d.runId)}>Flow {d.flow} · {d.status} · {d.durationMs} ms — view run</Chip>;
    case "tool":
      return <Chip icon={<Wrench size={11} />} title={JSON.stringify(d.outputs)}>Tool {d.name} · {d.status} · {d.source} mock</Chip>;
    case "knowledge":
      return <Chip icon={<BookOpen size={11} />} title={d.query}>
        {d.answer ? `Knowledge: ${d.citations.length} passage(s)` : `Knowledge: no answer${d.empty?.length ? ` — no test content for ${d.empty.join(", ")}` : ""}`}</Chip>;
    case "notice":
      return <Chip icon={<Info size={11} />}>{d.text}</Chip>;
    case "warning":
      return <Chip kind="warning" icon={<AlertTriangle size={11} />}>{d.text}</Chip>;
    case "error":
      return <Chip kind="error" icon={<AlertTriangle size={11} />} title={d.code}>{d.text}</Chip>;
    default:
      return null;
  }
}

function Chip({ children, icon, onClick, title, kind }: { children: React.ReactNode; icon: React.ReactNode; onClick?: () => void; title?: string; kind?: string }) {
  return (
    <div className={`chat-chip${kind ? ` ${kind}` : ""}`}>
      {onClick ? <button onClick={onClick} title={title}>{icon}<span className="ellipsis">{children}</span></button>
        : <span title={title}>{icon}<span className="ellipsis">{children}</span></span>}
    </div>
  );
}

const ACT_ICON: Record<string, JSX.Element> = {
  step: <ChevronRight size={12} />, topic: <CornerDownRight size={12} />, routing: <GitBranch size={12} />,
  plan: <Sparkles size={12} />, agent: <Bot size={12} />, tool: <Wrench size={12} />, flow: <Workflow size={12} />,
  knowledge: <BookOpen size={12} />, message: <MessageSquare size={12} />, notice: <Info size={12} />,
  warning: <AlertTriangle size={12} />, error: <AlertTriangle size={12} />, branch: <GitBranch size={12} />,
};

function actTitle(ev: SimEvent): string {
  const d = ev.data;
  switch (ev.type) {
    case "step": return `${d.kind} · ${d.label}`;
    case "topic": return `${d.action === "start" ? "Started" : "Ended"} topic ${d.name}${d.reason ? ` — ${d.reason}` : ""}`;
    case "routing": return `${d.mode === "interruption" ? "Interruption" : "Recognition"}: ${d.reason || ""}`;
    case "plan": return `Planner → ${d.action}${d.id ? ` ${d.id}` : ""}${d.thought ? ` — ${d.thought}` : ""}`;
    case "agent": return `${d.action === "start" ? "Child agent started" : "Child agent finished"}: ${d.name}`;
    case "tool": return `Tool ${d.name} (${d.kind}) · ${d.status} · ${d.source} mock`;
    case "flow": return `Flow ${d.flow} · ${d.status} · ${d.durationMs} ms`;
    case "knowledge": return d.answer ? `Knowledge answer from ${d.citations.length} passage(s)` : `Knowledge: no answer (searched ${d.searched?.join(", ") || "nothing"})`;
    case "message": return `Message: ${String(d.text || (d.attachments?.length ? "[card]" : "")).slice(0, 120)}`;
    case "branch": return `Condition → ${d.label ?? ""}`;
    default: return String(d.text ?? ev.type);
  }
}

function Activity({ log, onRun, onOpen }: { log: Entry[]; onRun: (id: string) => void; onOpen: (cid: string | null) => void }) {
  const [openRow, setOpenRow] = useState<string | null>(null);
  const turns = useMemo(() => {
    const out: { turn: number; user?: string; items: SimEvent[] }[] = [];
    for (const e of log) {
      let t = out.find((x) => x.turn === e.turn);
      if (!t) { t = { turn: e.turn, items: [] }; out.push(t); }
      if (e.user !== undefined) t.user = e.user;
      else if (e.ev) t.items.push(e.ev);
    }
    return out;
  }, [log]);
  if (!log.length) return <div className="small muted">Nothing yet — send a message.</div>;
  return (
    <div>
      {turns.map((t) => (
        <div key={t.turn}>
          <div className="act-turn">{t.turn === 0 ? "Conversation start" : `Turn ${t.turn} · “${t.user}”`}</div>
          {t.items.map((ev) => {
            const k = `${t.turn}-${ev.seq}`;
            const cls = ev.type === "error" || ev.data.status === "Failed" ? "bad" : ["plan", "knowledge", "routing"].includes(ev.type) && ev.data.ai ? "ai"
              : ev.data.status === "Succeeded" ? "ok" : "";
            return (
              <div key={k}>
                <div className="act-row clickable" onClick={() => setOpenRow(openRow === k ? null : k)}>
                  <span className={`ico ${cls}`}>{ACT_ICON[ev.type] ?? <ChevronRight size={12} />}</span>
                  <div className="grow">
                    <div>{actTitle(ev)}</div>
                    {ev.componentId && <div className="tiny faint mono ellipsis">{ev.componentId}{ev.pointer ? ` · ${ev.pointer}` : ""}</div>}
                  </div>
                  {ev.type === "flow" && <button className="btn btn-ghost btn-sm" onClick={(e) => { e.stopPropagation(); onRun(ev.data.runId); }}>Run</button>}
                  {ev.componentId && ev.type !== "flow" && <button className="btn btn-ghost btn-sm" onClick={(e) => { e.stopPropagation(); onOpen(ev.componentId); }}>Open</button>}
                  {openRow === k ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                </div>
                {openRow === k && <pre className="json-box">{JSON.stringify(ev.data, null, 2)}</pre>}
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}

function fmt(v: unknown): JSX.Element {
  if (v === null || v === undefined || v === "") return <span className="faint">blank</span>;
  if (typeof v === "object") return <span className="mono">{JSON.stringify(v)}</span>;
  return <span>{String(v)}</span>;
}

function VarTable({ title, data }: { title: string; data: Record<string, unknown> | undefined }) {
  const entries = Object.entries(data ?? {});
  return (
    <div style={{ marginBottom: 16 }}>
      <div className="act-turn" style={{ marginTop: 0 }}>{title}</div>
      {entries.length === 0 ? <div className="small faint">none</div> : (
        <table className="var-table"><tbody>{entries.map(([k, v]) => <tr key={k}><td>{k}</td><td>{fmt(v)}</td></tr>)}</tbody></table>
      )}
    </div>
  );
}

function Variables({ vars }: { vars: Record<string, any> | null }) {
  if (!vars) return <div className="small muted">Variables appear after the first turn.</div>;
  const sys = vars.System ?? {};
  const flatSys: Record<string, unknown> = {
    "Activity.Text": sys.Activity?.Text, "Conversation.Id": sys.Conversation?.Id, "Conversation.InTestMode": sys.Conversation?.InTestMode,
    FallbackCount: sys.FallbackCount, "User.DisplayName": sys.User?.DisplayName, "User.Email": sys.User?.Email,
    "User.IsLoggedIn": sys.User?.IsLoggedIn, "Error.Message": sys.Error?.Message, "Error.Code": sys.Error?.Code,
    "Recognizer.IntentOptions": sys.Recognizer?.IntentOptions?.length ? sys.Recognizer.IntentOptions : undefined,
  };
  return (
    <div>
      <VarTable title={`Topic${vars.topicName ? ` · ${vars.topicName}` : ""}`} data={vars.Topic} />
      <VarTable title="Global" data={vars.Global} />
      <VarTable title="System" data={Object.fromEntries(Object.entries(flatSys).filter(([, v]) => v !== undefined))} />
    </div>
  );
}

function Settings({ user, setUser, onRestart }: {
  user: { DisplayName: string; Email: string; IsLoggedIn: boolean };
  setUser: (u: { DisplayName: string; Email: string; IsLoggedIn: boolean }) => void; onRestart: () => void;
}) {
  const { ws } = useWs();
  const { data: sources = [], isLoading } = useQuery({ queryKey: ["test-knowledge", ws.info.id], queryFn: () => api.testKnowledge(ws.info.id) });
  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="card stack">
        <div className="row"><User size={14} /><b className="small">Test user</b></div>
        <div className="small muted">Becomes System.User.* in the next conversation.</div>
        <input className="form-input" value={user.DisplayName} onChange={(e) => setUser({ ...user, DisplayName: e.target.value })} placeholder="Display name" />
        <input className="form-input" value={user.Email} onChange={(e) => setUser({ ...user, Email: e.target.value })} placeholder="Email" />
        <label className="row small"><input type="checkbox" checked={user.IsLoggedIn} onChange={(e) => setUser({ ...user, IsLoggedIn: e.target.checked })} />Signed in</label>
        <div><button className="btn btn-secondary btn-sm" onClick={onRestart}><RotateCcw size={12} />Restart with these settings</button></div>
      </div>
      <div className="stack">
        <div className="row"><BookOpen size={14} /><b className="small">Knowledge test content</b></div>
        <div className="small muted">SharePoint and websites aren't reached from the test pane. Paste or upload representative content per source;
          generative answers are grounded on it, with citations.</div>
        {isLoading ? <Loader2 className="spin" size={14} /> : sources.length === 0
          ? <div className="small faint">This agent has no knowledge sources.</div>
          : sources.map((s) => <KnowledgeEditor key={s.name} wid={ws.info.id} name={s.name} label={s.displayName} site={s.site} kind={s.kind} initial={s.content} />)}
      </div>
      <div className="small muted"><Workflow size={12} /> Connector results inside flows are set per flow: open an agent flow and use <b>Test</b>.</div>
    </div>
  );
}

function KnowledgeEditor({ wid, name, label, site, kind, initial }: { wid: string; name: string; label: string; site: string; kind: string; initial: string }) {
  const [text, setText] = useState(initial);
  const qc = useQueryClient();
  const toast = useToast();
  const file = useRef<HTMLInputElement>(null);
  useEffect(() => setText(initial), [initial]);
  const save = useMutation({
    mutationFn: () => api.saveKnowledge(wid, name, text),
    onSuccess: () => { toast("Test content saved", "success"); qc.invalidateQueries({ queryKey: ["test-knowledge", wid] }); },
    onError: (e: Error) => toast(e.message, "error"),
  });
  const upload = useMutation({
    mutationFn: (f: File) => api.uploadKnowledge(wid, name, f),
    onSuccess: () => { toast("File added", "success"); qc.invalidateQueries({ queryKey: ["test-knowledge", wid] }); },
    onError: (e: Error) => toast(e.message, "error"),
  });
  return (
    <div className="card stack">
      <div className="row between"><div className="grow"><b className="small">{label}</b><div className="tiny faint ellipsis">{kind.replace(/SearchSource$/, "")} · {site}</div></div>
        <span className={`pill-sm ${initial ? "ok" : "warning"}`}>{initial ? `${initial.length} chars` : "no content"}</span></div>
      <textarea className="form-input" rows={4} value={text} onChange={(e) => setText(e.target.value)} placeholder="Paste representative content (policies, FAQ, pages)…" />
      <div className="row">
        <button className="btn btn-secondary btn-sm" disabled={text === initial || save.isPending} onClick={() => save.mutate()}>Save</button>
        <button className="btn btn-ghost btn-sm" onClick={() => file.current?.click()}><Upload size={12} />Upload .txt / .md / .docx</button>
        <input ref={file} type="file" accept=".txt,.md,.docx" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) upload.mutate(f); e.target.value = ""; }} />
      </div>
    </div>
  );
}

function Tests() {
  const { ws } = useWs();
  const wid = ws.info.id;
  const qc = useQueryClient();
  const toast = useToast();
  const { data: cases = [], isLoading } = useQuery({ queryKey: ["test-cases", wid], queryFn: () => api.testCases(wid) });
  const [draft, setDraft] = useState<TestCase[] | null>(null);
  const [results, setResults] = useState<Record<string, CaseResult>>({});
  const list = draft ?? cases;
  const dirty = draft !== null;
  const save = useMutation({
    mutationFn: (c: TestCase[]) => api.saveTestCases(wid, c),
    onSuccess: () => { setDraft(null); qc.invalidateQueries({ queryKey: ["test-cases", wid] }); toast("Test cases saved", "success"); },
    onError: (e: Error) => toast(e.message, "error"),
  });
  const run = useMutation({
    mutationFn: (ids?: string[]) => api.runTestCases(wid, ids),
    onSuccess: (r) => setResults((cur) => ({ ...cur, ...Object.fromEntries(r.map((x) => [x.id, x])) })),
    onError: (e: Error) => toast(e.message, "error"),
  });
  const edit = (i: number, fn: (c: TestCase) => TestCase) => setDraft(list.map((c, j) => (j === i ? fn(structuredClone(c)) : c)));
  const passed = Object.values(results).filter((r) => r.passed).length;

  if (isLoading) return <Loader2 className="spin" size={16} />;
  return (
    <div className="stack" style={{ gap: 12 }}>
      <div className="row between">
        <div className="small muted grow">Replays each conversation in a fresh session and checks topic, tool and reply text.
          Use <BookmarkPlus size={12} /> in the header to save the current chat.</div>
      </div>
      <div className="row">
        <button className="btn btn-primary btn-sm" disabled={!cases.length || run.isPending || dirty} onClick={() => run.mutate(undefined)}>
          {run.isPending ? <Loader2 size={14} className="spin" /> : <Play size={14} />}Run all</button>
        {dirty && <button className="btn btn-secondary btn-sm" onClick={() => save.mutate(list)}>Save changes</button>}
        {dirty && <button className="btn btn-ghost btn-sm" onClick={() => setDraft(null)}>Discard</button>}
        {Object.keys(results).length > 0 && <span className={`pill-sm ${passed === Object.keys(results).length ? "ok" : "error"}`}>{passed}/{Object.keys(results).length} passed</span>}
      </div>
      {list.length === 0 && <div className="small faint">No test cases yet.</div>}
      {list.map((c, i) => {
        const r = c.id ? results[c.id] : undefined;
        return (
          <div key={c.id ?? i} className="card stack" style={{ padding: 12 }}>
            <div className="row">
              {r && (r.passed ? <CheckCircle2 size={14} color="var(--c-success)" /> : <XCircle size={14} color="var(--c-danger)" />)}
              <input className="form-input grow" value={c.name} onChange={(e) => edit(i, (x) => ({ ...x, name: e.target.value }))} />
              <button className="btn-icon" title="Run" disabled={!c.id || dirty || run.isPending} onClick={() => c.id && run.mutate([c.id])}><Play size={14} /></button>
              <button className="btn-icon" title="Delete" onClick={() => setDraft(list.filter((_, j) => j !== i))}><Trash2 size={14} /></button>
            </div>
            {c.turns.map((t, k) => {
              const tr = r?.turns[k];
              return (
                <div key={k} className="stack" style={{ gap: 4, paddingLeft: 8, borderLeft: `2px solid ${tr ? (tr.passed ? "var(--c-success)" : "var(--c-danger)") : "var(--c-edge)"}` }}>
                  <div className="small"><b style={{ fontWeight: 500 }}>User:</b> {t.user}</div>
                  <div className="row wrap" style={{ gap: 4 }}>
                    {(["topic", "tool", "contains"] as const).map((kind) => (
                      <input key={kind} className="form-input" style={{ flex: "1 1 120px", fontSize: 12, padding: "4px 8px" }}
                             placeholder={kind === "contains" ? "reply contains…" : `expected ${kind}`} value={t.expect[kind] ?? ""}
                             onChange={(e) => edit(i, (x) => { x.turns[k].expect[kind] = e.target.value || null; return x; })} />))}
                  </div>
                  {tr && tr.checks.filter((ch) => !ch.passed).map((ch, m) => (
                    <div key={m} className="tiny" style={{ color: "var(--c-danger)" }}>{ch.kind}: expected “{ch.expected}”, got “{ch.actual}”</div>))}
                </div>
              );
            })}
          </div>
        );
      })}
    </div>
  );
}
