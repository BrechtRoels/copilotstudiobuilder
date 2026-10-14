import { useCallback, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpenText, Download, FileText, Loader2, Sparkles } from "lucide-react";
import { api, Diagram, downloadBlob } from "../api";
import { Mermaid, svgToPng } from "../components/Editors";
import { SeverityPill, useToast } from "../components/ui";
import { useWs } from "./WorkspaceLayout";

export default function DocsPage() {
  const { ws, version } = useWs();
  const qc = useQueryClient();
  const toast = useToast();
  const svgs = useRef<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const { data: doc, isLoading } = useQuery({ queryKey: ["docs", ws.info.id, version], queryFn: () => api.docs(ws.info.id) });

  const summaries = useMutation({
    mutationFn: () => api.refreshSummaries(ws.info.id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["docs", ws.info.id] }); toast("AI summaries updated", "success"); },
    onError: (e: Error) => toast(e.message, "error"),
  });

  const docx = async () => {
    if (!doc) return;
    setBusy(true);
    try {
      const images: Record<string, string> = {};
      for (const d of doc.diagrams) {
        const svg = svgs.current[d.id];
        if (svg) { try { images[d.id] = await svgToPng(svg); } catch { /* skip diagram */ } }
      }
      downloadBlob(await api.docx(ws.info.id, images), `${doc.title.replace(/[^A-Za-z0-9]+/g, "-")}-documentation.docx`);
    } catch (e) { toast((e as Error).message, "error"); } finally { setBusy(false); }
  };

  if (isLoading || !doc) return <div className="empty"><Loader2 className="spin" /></div>;
  const diagram = (id: string) => doc.diagrams.find((d) => d.id === id);
  const hasSummaries = doc.topics.some((t) => t.summary) || !!doc.overview;

  return (
    <div style={{ overflowY: "auto", flex: 1 }}>
      <div className="page-container" style={{ maxWidth: 960 }}>
        <div className="page-header">
          <div><h1>{doc.title}</h1>
            <div className="muted small" style={{ marginTop: 4 }}>Auto-generated documentation · {doc.orchestration} orchestration · updated {doc.generatedAt}</div></div>
          <div className="row">
            <button className="btn btn-secondary" disabled={summaries.isPending} onClick={() => summaries.mutate()}>
              {summaries.isPending ? <Loader2 size={16} className="spin" /> : <Sparkles size={16} />}{hasSummaries ? "Refresh" : "Add"} AI summaries</button>
            <a className="btn btn-secondary" href={api.htmlUrl(ws.info.id)}><Download size={16} />HTML</a>
            <button className="btn btn-primary" disabled={busy} onClick={docx}>{busy ? <Loader2 size={16} className="spin" /> : <FileText size={16} />}Word</button>
          </div>
        </div>

        <div className="grid-3" style={{ marginBottom: 32 }}>
          {[["Topics", doc.counts.topics], ["Child agents", doc.counts.childAgents], ["Tools", doc.counts.tools],
            ["Agent flows", doc.counts.flows], ["Knowledge", doc.counts.knowledge], ["Global variables", doc.counts.variables]].map(([k, v]) => (
            <div key={k} className="card stat"><b>{v}</b><span>{k}</span></div>))}
        </div>

        <Section n={1} title="Overview">
          {doc.overview && <p>{doc.overview}</p>}
          {doc.instructions ? <pre className="card mono" style={{ whiteSpace: "pre-wrap", margin: 0 }}>{doc.instructions}</pre> : <div className="faint small">No agent instructions.</div>}
          {doc.starters.length > 0 && <div className="row wrap" style={{ marginTop: 8 }}>{doc.starters.map((s) => <span key={s} className="pill">{s}</span>)}</div>}
        </Section>

        <Section n={2} title="Architecture"><Dia d={diagram("agent-map")} svgs={svgs} /></Section>

        {doc.childAgents.length > 0 && <Section n={3} title="Child agents">
          <div className="list">{doc.childAgents.map((a) => (
            <div key={a.id} className="card"><h3>{a.name}</h3>
              {a.summary && <p className="small">{a.summary}</p>}
              <div className="small muted"><b>Routing:</b> {a.description || "—"}</div>
              <div className="small muted"><b>Inputs:</b> {a.inputs.join(", ") || "—"} · <b>Outputs:</b> {a.outputs.join(", ") || "—"}</div>
            </div>))}</div>
        </Section>}

        <Section n={4} title="Topics">
          <div className="card" style={{ padding: 0, overflow: "hidden", marginBottom: 16 }}>
            <table className="tbl"><thead><tr><th>Topic</th><th>Trigger</th><th>Routing</th><th>Nodes</th></tr></thead>
              <tbody>{doc.topics.map((t) => (
                <tr key={t.id}><td><a href={`#${t.diagram}`}>{t.name}</a></td><td>{t.trigger}</td>
                  <td className="muted">{t.description || t.phrases.slice(0, 3).join(" · ") || "—"}</td><td>{t.nodeCount}</td></tr>))}</tbody></table>
          </div>
          {doc.topics.map((t) => (
            <div key={t.id} id={t.diagram} className="card" style={{ marginBottom: 8 }}>
              <div className="row"><h3>{t.name}</h3><span className="pill-sm accent">{t.trigger}</span></div>
              {t.summary && <p className="small">{t.summary}</p>}
              <div className="small muted">Variables: {t.variables.join(", ") || "—"} · Redirects: {t.redirects.join(", ") || "—"} · Flows: {t.flows.join(", ") || "—"}</div>
              <div style={{ marginTop: 8 }}><Dia d={diagram(t.diagram)} svgs={svgs} /></div>
            </div>))}
        </Section>

        {doc.tools.length > 0 && <Section n={5} title="Tools">
          <div className="card" style={{ padding: 0, overflow: "hidden" }}>
            <table className="tbl"><thead><tr><th>Tool</th><th>Type</th><th>Target</th><th>Inputs</th></tr></thead>
              <tbody>{doc.tools.map((t) => (<tr key={t.id}><td>{t.name}<div className="tiny muted">{t.description}</div></td><td>{t.type}</td><td>{t.target}</td><td>{t.inputs.join(", ")}</td></tr>))}</tbody></table>
          </div>
        </Section>}

        {doc.flows.length > 0 && <Section n={6} title="Agent flows">
          {doc.flows.map((f) => (
            <div key={f.id} className="card" style={{ marginBottom: 8 }}>
              <div className="row"><h3>{f.name}</h3>{f.errorHandling ? <span className="pill-sm ok">error handling</span> : <span className="pill-sm warning">no failure path</span>}</div>
              {f.summary && <p className="small">{f.summary}</p>}
              <div className="small muted">Trigger: {f.trigger} · Inputs: {f.inputs.join(", ") || "—"} · Outputs: {f.outputs.join(", ") || "—"} · Connectors: {f.connectors.join(", ") || "—"}</div>
              <div style={{ marginTop: 8 }}><Dia d={diagram(f.diagram)} svgs={svgs} /></div>
            </div>))}
        </Section>}

        {doc.knowledge.length > 0 && <Section n={7} title="Knowledge">
          <div className="card" style={{ padding: 0, overflow: "hidden" }}>
            <table className="tbl"><thead><tr><th>Source</th><th>Type</th><th>Used by</th><th>Location</th></tr></thead>
              <tbody>{doc.knowledge.map((k) => <tr key={k.id}><td>{k.name}</td><td>{k.type}</td><td>{k.owner}</td><td className="mono tiny">{k.detail}</td></tr>)}</tbody></table>
          </div>
        </Section>}

        <Section n={8} title="Quality findings">
          {doc.findings.length === 0 ? <div className="small muted">No findings.</div> : (
            <div className="card stack" style={{ gap: 4 }}>{doc.findings.filter((f) => f.severity !== "info").map((f, i) => (
              <div key={i} className="row small"><SeverityPill s={f.severity} /><span className="mono tiny faint">{f.component}</span><span>{f.message}</span></div>))}
              {doc.findings.every((f) => f.severity === "info") && <div className="small muted">Only informational notes.</div>}</div>
          )}
        </Section>
        <div className="tiny faint row"><BookOpenText size={12} />Regenerated from the workspace files every time you open this page.</div>
      </div>
    </div>
  );
}

function Section({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <section className="section">
      <h2 style={{ fontSize: 16, paddingBottom: 8, borderBottom: "2px solid var(--c-primary)" }}>{n}. {title}</h2>
      {children}
    </section>
  );
}

function Dia({ d, svgs }: { d?: Diagram; svgs: React.MutableRefObject<Record<string, string>> }) {
  const onSvg = useCallback((svg: string) => { if (d) svgs.current[d.id] = svg; }, [d, svgs]);
  if (!d) return null;
  return <Mermaid code={d.mermaid} onSvg={onSvg} />;
}
