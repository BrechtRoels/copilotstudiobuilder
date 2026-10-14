import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import {
  ArrowRight, BookOpen, Bot, CheckCircle2, CircleAlert, FlaskConical, GitFork, Loader2, MessageSquare,
  Play, Rocket, ShieldCheck, Sparkles, Workflow, Wrench, XCircle,
} from "lucide-react";
import { api, CheckItem } from "../api";
import { Empty, SeverityPill, useToast } from "../components/ui";
import { fileLabel } from "../lib/labels";
import { useWs } from "./WorkspaceLayout";

const COUNTS: { cat: string; label: string; icon: JSX.Element }[] = [
  { cat: "topic", label: "Topics", icon: <MessageSquare size={16} /> }, { cat: "action", label: "Tools", icon: <Wrench size={16} /> },
  { cat: "workflow", label: "Agent flows", icon: <Workflow size={16} /> }, { cat: "childAgent", label: "Child agents", icon: <GitFork size={16} /> },
  { cat: "knowledge", label: "Knowledge", icon: <BookOpen size={16} /> }, { cat: "variable", label: "Global variables", icon: <Bot size={16} /> },
];

function ago(ts: number | string | null): string {
  if (!ts) return "";
  const t = typeof ts === "number" ? ts * 1000 : Date.parse(ts);
  const s = Math.round((Date.now() - t) / 1000);
  return s < 60 ? "just now" : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago` : new Date(t).toLocaleDateString();
}

export default function OverviewPage() {
  const { ws, version, openAI, openTest, select } = useWs();
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const wid = ws.info.id;
  const { data: ov, isLoading } = useQuery({ queryKey: ["overview", wid, version], queryFn: () => api.overview(wid) });
  const runTests = useMutation({
    mutationFn: () => api.runTestCases(wid),
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ["overview", wid] }); toast(`${r.filter((x) => x.passed).length}/${r.length} test case(s) passed`, r.every((x) => x.passed) ? "success" : "error"); },
    onError: (e: Error) => toast(e.message, "error"),
  });
  const go = (link: string) => {
    if (link.startsWith("build:")) select(link.slice(6));
    else if (link === "test") openTest();
    else nav(`/w/${wid}/${link}`);
  };

  if (ws.components.length === 0) return (
    <Empty icon={<Sparkles size={18} />} title="Empty workspace">
      <div className="small muted">Describe your agent and the AI Builder generates it.</div>
      <button className="btn btn-primary" onClick={() => openAI({ mode: "agent" })}><Sparkles size={16} />Build an agent with AI</button>
    </Empty>
  );
  if (isLoading || !ov) return <div className="empty"><Loader2 className="spin" /></div>;
  const agent = ws.components.find((c) => c.category === "agent");
  const instr = String(((agent?.data ?? {}) as { instructions?: string }).instructions ?? "");

  return (
    <div className="page-container" style={{ maxWidth: 1080 }}>
      <div className="row between" style={{ marginBottom: 20 }}>
        <div>
          <h1 style={{ marginBottom: 4 }}>{agent?.displayName ?? ws.info.name}</h1>
          <div className="small muted">{ov.orchestration} orchestration · {ws.components.length} components</div>
        </div>
        <div className="row">
          <button className="btn btn-secondary" onClick={openTest}><FlaskConical size={16} />Test</button>
          <button className="btn btn-primary" onClick={() => nav(`/w/${wid}/deploy`)}><Rocket size={16} />Deploy</button>
        </div>
      </div>

      <div className="dash-grid">
        <section className="card stack dash-wide">
          <div className="row between"><div className="row"><Rocket size={16} color="var(--c-primary)" /><b>Ready to deploy</b></div>
            {ov.ready ? <span className="pill-sm ok"><CheckCircle2 size={11} />ready</span> : <span className="pill-sm error">blocked</span>}</div>
          <div className="stack" style={{ gap: 2 }}>{ov.checklist.map((c) => <CheckRow key={c.id} c={c} onGo={go} />)}</div>
        </section>

        <section className="card stack">
          <div className="row between"><div className="row"><ShieldCheck size={16} color="var(--c-primary)" /><b>Health</b></div>
            <button className="btn btn-ghost btn-sm" onClick={() => nav(`/w/${wid}/analyzer`)}>Analyzer<ArrowRight size={12} /></button></div>
          <div className="row" style={{ gap: 16 }}>
            <div className="stat"><b style={{ color: ov.findings.errors ? "var(--c-danger)" : undefined }}>{ov.findings.errors}</b><span>errors</span></div>
            <div className="stat"><b style={{ color: ov.findings.warnings ? "var(--c-warn)" : undefined }}>{ov.findings.warnings}</b><span>warnings</span></div>
            <div className="stat"><b>{ov.findings.infos}</b><span>infos</span></div>
          </div>
          {ov.findings.top.length === 0 ? <div className="small" style={{ color: "var(--c-success)" }}><CheckCircle2 size={12} /> No problems found.</div>
            : ov.findings.top.map((f, i) => (
              <div key={i} className="row small" style={{ alignItems: "flex-start" }}><SeverityPill s={f.severity} />
                <span className="grow"><span className="muted">{fileLabel(f.component, null, ws)}:</span> {f.message}</span>
                {f.fixable && <button className="btn btn-ghost btn-sm" onClick={() => openAI({ mode: "fix", target: f.component, findings: [f.message], prompt: "" })}><Sparkles size={12} />Fix</button>}
              </div>))}
        </section>

        <section className="card stack">
          <div className="row between"><div className="row"><FlaskConical size={16} color="var(--c-primary)" /><b>Tests</b></div>
            <button className="btn btn-ghost btn-sm" onClick={openTest}>Test pane<ArrowRight size={12} /></button></div>
          {ov.tests.cases === 0 ? (
            <div className="small muted">No saved test cases. Chat with the agent in the test pane and save the conversation with the bookmark icon.</div>
          ) : (
            <>
              <div className="row" style={{ gap: 16 }}>
                <div className="stat"><b>{ov.tests.cases}</b><span>cases</span></div>
                <div className="stat"><b style={{ color: ov.tests.ran && ov.tests.passed < ov.tests.cases ? "var(--c-danger)" : ov.tests.ran ? "var(--c-success)" : undefined }}>
                  {ov.tests.ran ? `${ov.tests.passed}/${ov.tests.cases}` : "—"}</b><span>{ov.tests.ran ? `passed · ${ago(ov.tests.ts)}` : "not run yet"}</span></div>
              </div>
              <div><button className="btn btn-secondary btn-sm" disabled={runTests.isPending} onClick={() => runTests.mutate()}>
                {runTests.isPending ? <Loader2 size={14} className="spin" /> : <Play size={14} />}Run all</button></div>
            </>
          )}
        </section>

        <section className="card stack">
          <div className="row"><b>Components</b></div>
          <div className="dash-counts">{COUNTS.map((c) => (
            <button key={c.cat} className="dash-count" onClick={() => nav(`/w/${wid}`)}>
              <span className="icon-box" style={{ width: 32, height: 32, borderRadius: 8 }}>{c.icon}</span>
              <span><b>{ov.counts[c.cat] ?? 0}</b><span className="tiny muted">{c.label}</span></span></button>))}</div>
        </section>

        <section className="card stack">
          <div className="row between"><div className="row"><Bot size={16} color="var(--c-primary)" /><b>Instructions</b></div>
            {agent && <button className="btn btn-ghost btn-sm" onClick={() => select(agent.id)}>Edit<ArrowRight size={12} /></button>}</div>
          <div className="small muted dash-clamp">{instr || "No instructions yet."}</div>
        </section>

        <section className="card stack dash-wide">
          <div className="row between"><div className="row"><Sparkles size={16} color="var(--c-primary)" /><b>Recent AI changes</b></div>
            <button className="btn btn-ghost btn-sm" onClick={() => openAI()}>AI Builder<ArrowRight size={12} /></button></div>
          {ov.recent.length === 0 ? <div className="small faint">Nothing applied from the AI Builder yet.</div> : ov.recent.map((r, i) => (
            <div key={i} className="row small" style={{ alignItems: "flex-start" }}>
              <span className="pill-sm">{r.mode}</span>
              <span className="grow">{r.summary.split(/(?<=[.!?])\s/)[0]} <span className="faint">· {r.files.length} file(s)</span></span>
              <span className="tiny faint">{ago(r.ts)}</span>
            </div>))}
        </section>
      </div>
    </div>
  );
}

function CheckRow({ c, onGo }: { c: CheckItem; onGo: (link: string) => void }) {
  const icon = c.state === "ok" ? <CheckCircle2 size={14} color="var(--c-success)" /> : c.state === "warn" ? <CircleAlert size={14} color="var(--c-warn)" /> : <XCircle size={14} color="var(--c-danger)" />;
  return (
    <button className="check-row" onClick={() => onGo(c.link)}>
      {icon}<span className="grow"><span className="small">{c.label}</span><span className="tiny faint" style={{ display: "block" }}>{c.detail}</span></span>
      {c.state !== "ok" && <span className="tiny" style={{ color: "var(--c-primary)" }}>Fix<ArrowRight size={11} /></span>}
    </button>
  );
}
