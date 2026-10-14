import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Check, CheckCircle2, FileCode2, GitFork, Loader2, X } from "lucide-react";
import type { Proposal } from "../api";
import { fileLabel } from "../lib/labels";
import Canvas from "./Canvas";
import { CodeEditor, DiffView } from "./Editors";
import { SeverityPill } from "./ui";
import { CoverageList, useApply, useApplyGuard } from "./AIPanel";
import { useWs } from "../pages/WorkspaceLayout";

/** Full-screen review of an AI proposal: files on the left, the change in the middle, checks on the right. */
export default function ReviewChanges({ p, onClose, onApplied }: { p: Proposal; onClose: () => void; onApplied: (paths: string[]) => void }) {
  const { ws } = useWs();
  const [chosen, setChosen] = useState<string[]>(p.files.map((f) => f.path));
  const [sel, setSel] = useState(p.files.find((f) => f.graph)?.path ?? p.files[0]?.path);
  const file = p.files.find((f) => f.path === sel);
  const [view, setView] = useState<"visual" | "diff" | "file">(file?.graph ? "visual" : "diff");
  useEffect(() => { setView(file?.graph ? "visual" : file?.isNew ? "file" : "diff"); }, [sel]); // eslint-disable-line react-hooks/exhaustive-deps
  const apply = useApply(p, (paths) => { onApplied(paths); onClose(); });
  const guard = useApplyGuard(p);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const comp = sel?.startsWith("workflows/") ? sel.split("/").slice(0, 2).join("/") : sel;
  const findings = useMemo(() => p.findings.filter((f) => f.component === comp), [p.findings, comp]);
  const changed = file?.graph?.nodes.filter((n) => n.status === "new" || n.status === "changed").length ?? 0;
  const done = !!p.applied;

  return (
    <div className="review">
      <div className="review-head">
        <div className="grow" style={{ minWidth: 0 }}>
          <div className="tiny faint">Review changes · nothing is written until you apply</div>
          <h2 className="ellipsis" style={{ margin: 0, fontSize: 16 }}>{p.summary.split(/(?<=[.!?])\s/)[0]}</h2>
        </div>
        {p.valid ? <span className="pill-sm ok"><CheckCircle2 size={11} />schema-valid</span> : <span className="pill-sm error"><AlertTriangle size={11} />validation errors</span>}
        {!done && guard.armed && <span className="small" style={{ color: "var(--c-danger)" }}>{guard.errors} error(s) · click again to apply anyway</span>}
        {!done && <button className={`btn ${guard.armed ? "btn-danger" : "btn-primary"}`} disabled={!chosen.length || apply.isPending}
                          onClick={() => guard.confirm(() => apply.mutate(chosen))}>
          {apply.isPending ? <Loader2 size={16} className="spin" /> : guard.errors ? <AlertTriangle size={16} /> : <Check size={16} />}
          {guard.armed ? "Apply anyway" : "Apply"} {chosen.length} of {p.files.length}</button>}
        <button className="btn-icon" onClick={onClose} aria-label="Close review"><X size={18} /></button>
      </div>
      <div className="review-body">
        <aside className="review-files">
          {p.files.map((f) => {
            const errs = f.report?.issues.filter((i) => i.severity === "error").length ?? 0;
            return (
              <div key={f.path} className={`review-file${sel === f.path ? " active" : ""}`} onClick={() => setSel(f.path)}>
                <input type="checkbox" checked={chosen.includes(f.path)} disabled={done} onClick={(e) => e.stopPropagation()}
                       onChange={(e) => setChosen((c) => (e.target.checked ? [...c, f.path] : c.filter((x) => x !== f.path)))} aria-label={`Include ${f.path}`} />
                <div className="grow" style={{ minWidth: 0 }}>
                  <div className="ellipsis small">{fileLabel(f.path, f.content, ws)}</div>
                  <div className="tiny faint mono ellipsis">{f.path}</div>
                </div>
                {errs > 0 && <span className="pill-sm error">{errs}</span>}
                <span className={`pill-sm ${f.content === null ? "error" : f.isNew ? "ok" : "warning"}`}>{f.content === null ? "delete" : f.isNew ? "new" : "changed"}</span>
              </div>
            );
          })}
        </aside>
        <section className="review-main">
          {file && (
            <>
              <div className="tabs">
                {file.graph && <button className={`tab${view === "visual" ? " active" : ""}`} onClick={() => setView("visual")}><GitFork size={14} />Visual{changed ? ` · ${changed} new/changed` : ""}</button>}
                {!file.isNew && <button className={`tab${view === "diff" ? " active" : ""}`} onClick={() => setView("diff")}>Diff</button>}
                {file.content !== null && <button className={`tab${view === "file" ? " active" : ""}`} onClick={() => setView("file")}><FileCode2 size={14} />{file.isNew ? "New file" : "After"}</button>}
              </div>
              <div className="review-view">
                {view === "visual" && file.graph && <Canvas graph={file.graph} />}
                {view === "diff" && <div style={{ overflow: "auto", height: "100%", padding: 12 }}><DiffView diff={file.diff} /></div>}
                {view === "file" && file.content !== null && <CodeEditor value={file.content} readOnly language={file.path.endsWith(".json") ? "json" : "yaml"} />}
              </div>
            </>
          )}
        </section>
        <aside className="review-checks">
          {(p.coverage?.length ?? 0) > 0 && <>
            <div className="act-turn" style={{ marginTop: 0 }}>Requirements (whole change)</div>
            <CoverageList items={p.coverage!} />
          </>}
          <div className="act-turn" style={p.coverage?.length ? undefined : { marginTop: 0 }}>Schema & lint</div>
          {file?.report ? (file.report.issues.length ? file.report.issues.map((i, k) => (
            <div key={k} className="row small" style={{ alignItems: "flex-start", marginBottom: 6 }}><SeverityPill s={i.severity} />
              <span><span className="mono faint">{i.path || "(root)"}</span> {i.message}</span></div>))
            : <div className="small" style={{ color: "var(--c-success)" }}><CheckCircle2 size={12} /> Passes Microsoft's authoring schema and lint rules.</div>)
            : <div className="small faint">Not validated (non-YAML or deleted).</div>}
          <div className="act-turn">Analyzer (merged result)</div>
          {findings.length ? findings.map((f, k) => (
            <div key={k} className="row small" style={{ alignItems: "flex-start", marginBottom: 6 }}><SeverityPill s={f.severity} /><span>{f.message}</span></div>))
            : <div className="small faint">No findings for this component.</div>}
          {p.notes.length > 0 && <>
            <div className="act-turn">Follow-ups for you</div>
            <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>{p.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
          </>}
        </aside>
      </div>
    </div>
  );
}
