import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, ChevronDown, ChevronRight, CircleSlash, FlaskConical, Loader2, Play, Save, X, XCircle } from "lucide-react";
import { ActionMock, ActionRun, api, Component, FlowRun } from "../api";
import { Modal, useToast } from "./ui";
import { useWs } from "../pages/WorkspaceLayout";

const MARK: Record<string, string> = { Succeeded: "ok", Failed: "failed", TimedOut: "failed", Skipped: "skipped", Cancelled: "failed" };
export const runMarks = (run: FlowRun) => Object.fromEntries(run.actions.map((a) => [a.pointer, MARK[a.status] ?? "visited"]));

function StatusIcon({ s }: { s: string }) {
  if (s === "Succeeded") return <span className="ico ok"><CheckCircle2 size={12} /></span>;
  if (s === "Failed" || s === "TimedOut" || s === "Cancelled") return <span className="ico bad"><XCircle size={12} /></span>;
  return <span className="ico"><CircleSlash size={12} /></span>;
}

function statusPill(s: string) {
  return <span className={`pill-sm ${s === "Succeeded" ? "ok" : s === "Failed" || s === "TimedOut" ? "error" : ""}`}>{s}</span>;
}

const indent = (p: string) => Math.max(0, p.split("/actions/").length - 2) * 14;

export function RunDetails({ run, selectedPointer }: { run: FlowRun; selectedPointer?: string | null }) {
  const [openPtr, setOpenPtr] = useState<string | null>(null);
  useEffect(() => { if (selectedPointer) setOpenPtr(selectedPointer); }, [selectedPointer]);
  return (
    <div className="stack" style={{ gap: 12 }}>
      <div className="row wrap">{statusPill(run.status)}<span className="small muted">{run.durationMs} ms · {new Date(run.startedAt).toLocaleTimeString()}</span>
        <span className="tiny faint mono">run {run.id}</span></div>
      {run.error && <div className="small" style={{ color: "var(--c-danger)" }}>{run.error.code}: {run.error.message}</div>}
      {run.response && (
        <div><div className="act-turn" style={{ marginTop: 0 }}>Returned to the agent</div>
          <pre className="json-box">{JSON.stringify(Object.keys(run.outputs).length ? run.outputs : run.response.body, null, 2)}</pre></div>
      )}
      <div className="run-list">
        <div className="act-turn" style={{ marginTop: 0 }}>Actions</div>
        {run.actions.map((a) => <ActionRow key={a.pointer} a={a} open={openPtr === a.pointer} sel={selectedPointer === a.pointer}
                                            onToggle={() => setOpenPtr(openPtr === a.pointer ? null : a.pointer)} />)}
      </div>
    </div>
  );
}

function ActionRow({ a, open, sel, onToggle }: { a: ActionRun; open: boolean; sel: boolean; onToggle: () => void }) {
  return (
    <div style={{ marginLeft: indent(a.pointer) }}>
      <div className={`act-row clickable${sel ? " sel" : ""}`} onClick={onToggle}>
        <StatusIcon s={a.status} />
        <div className="grow">
          <div className="row" style={{ gap: 6 }}><b style={{ fontWeight: 500 }}>{a.name.replace(/_/g, " ")}</b>
            <span className="tiny faint">{a.type}</span>
            {a.source && <span className="pill-sm" title="Where the simulated result came from">{a.source} mock</span>}
            {a.iterations != null && <span className="pill-sm">{a.iterations}×</span>}</div>
          {a.error && <div className="tiny" style={{ color: "var(--c-danger)" }}>{a.error.message}</div>}
          {a.note && <div className="tiny faint">{a.note}</div>}
        </div>
        <span className="tiny faint">{a.status === "Skipped" ? "" : `${a.ms} ms`}</span>
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
      </div>
      {open && (
        <div style={{ padding: "0 8px 8px 36px" }}>
          {a.inputs !== null && a.inputs !== undefined && <><div className="tiny faint">Inputs</div><pre className="json-box">{JSON.stringify(a.inputs, null, 2)}</pre></>}
          {a.outputs !== null && a.outputs !== undefined && <><div className="tiny faint" style={{ marginTop: 6 }}>Outputs</div><pre className="json-box">{JSON.stringify(a.outputs, null, 2)}</pre></>}
          {a.status === "Skipped" && <div className="tiny faint">Skipped — its runAfter conditions were not met.</div>}
        </div>
      )}
    </div>
  );
}

type Override = { mode: "auto" | "mock" | "fail"; outputs: string; error: string };

/** Side panel on the Build page: run the selected agent flow like Power Automate's "Test flow". */
export function FlowTestPanel({ comp, selectedPointer, onClose }: { comp: Component; selectedPointer: string | null; onClose: () => void }) {
  const { ws, version, setMarks } = useWs();
  const wid = ws.info.id;
  const qc = useQueryClient();
  const toast = useToast();
  const { data: info, isLoading } = useQuery({ queryKey: ["flow-test", wid, comp.id, version], queryFn: () => api.flowTest(wid, comp.id) });
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const [ov, setOv] = useState<Record<string, Override>>({});
  const [run, setRun] = useState<FlowRun | null>(null);
  const [showSetup, setShowSetup] = useState(true);

  useEffect(() => {
    if (!info) return;
    setOv((cur) => {
      const next = { ...cur };
      for (const a of info.connectorActions) {
        if (next[a.name]) continue;
        const m = info.mocks[a.name];
        next[a.name] = m ? { mode: m.mode, outputs: m.outputs !== undefined ? JSON.stringify(m.outputs, null, 2) : "", error: m.error ?? "" }
          : { mode: "auto", outputs: "", error: "" };
      }
      return next;
    });
  }, [info]);

  const overrides = (): Record<string, ActionMock> => {
    const out: Record<string, ActionMock> = {};
    for (const [name, o] of Object.entries(ov)) {
      if (o.mode === "fail") out[name] = { mode: "fail", error: o.error || undefined };
      if (o.mode === "mock") {
        try { out[name] = { mode: "mock", outputs: o.outputs.trim() ? JSON.parse(o.outputs) : {} }; }
        catch { throw new Error(`Mock output for ${name} is not valid JSON`); }
      }
    }
    return out;
  };

  const exec = useMutation({
    mutationFn: () => api.runFlow(wid, comp.id, inputs, overrides()),
    onSuccess: (r) => {
      setRun(r); setShowSetup(false);
      setMarks((m) => ({ ...m, [comp.id]: runMarks(r) }));
      qc.invalidateQueries({ queryKey: ["flow-test", wid, comp.id] });
    },
    onError: (e: Error) => toast(e.message, "error"),
  });
  const saveMocks = useMutation({
    mutationFn: () => api.saveFlowMocks(wid, comp.id, overrides()),
    onSuccess: () => { toast("Saved as this flow's default mocks (also used when the agent calls it)", "success"); qc.invalidateQueries({ queryKey: ["flow-test", wid, comp.id] }); },
    onError: (e: Error) => toast(e.message, "error"),
  });

  const runs = info?.runs ?? [];
  const required = useMemo(() => (info?.fields ?? []).filter((f) => f.required && !inputs[f.name]?.trim()).map((f) => f.name), [info, inputs]);

  return (
    <aside className="side-panel">
      <div className="panel-head">
        <div className="row grow"><FlaskConical size={16} color="var(--c-primary)" />
          <div className="grow"><div className="tiny faint">Test flow · simulated, no connector is called</div><h3 className="ellipsis">{comp.displayName}</h3></div></div>
        <button className="btn-icon" onClick={() => { setMarks((m) => { const n = { ...m }; delete n[comp.id]; return n; }); onClose(); }} aria-label="Close"><X size={16} /></button>
      </div>
      <div className="panel-body stack" style={{ gap: 16 }}>
        {isLoading || !info ? <Loader2 className="spin" size={16} /> : (
          <>
            <button className="btn btn-ghost btn-sm" style={{ alignSelf: "flex-start" }} onClick={() => setShowSetup((s) => !s)}>
              {showSetup ? <ChevronDown size={14} /> : <ChevronRight size={14} />}Run setup</button>
            {showSetup && (
              <div className="stack" style={{ gap: 12 }}>
                <div className="stack">
                  <div className="act-turn" style={{ marginTop: 0 }}>Trigger inputs</div>
                  {info.fields.length === 0 && <div className="small faint">This trigger takes no inputs.</div>}
                  {info.fields.map((f) => (
                    <label key={f.key} className="stack" style={{ gap: 4 }}>
                      <span className="small">{f.name}{f.required && <span style={{ color: "var(--c-danger)" }}> *</span>} <span className="tiny faint">{f.type}</span></span>
                      <input className="form-input" value={inputs[f.name] ?? ""} placeholder={f.description}
                             onChange={(e) => setInputs({ ...inputs, [f.name]: e.target.value })} />
                    </label>
                  ))}
                </div>
                {info.connectorActions.length > 0 && (
                  <div className="stack">
                    <div className="act-turn" style={{ marginTop: 0 }}>Connector actions</div>
                    <div className="tiny faint">Auto = sample from Microsoft's connector schema when available, otherwise an AI-generated response.</div>
                    {info.connectorActions.map((a) => {
                      const o = ov[a.name] ?? { mode: "auto", outputs: "", error: "" };
                      return (
                        <div key={a.name} className="card stack" style={{ padding: 10 }}>
                          <div className="row between"><div className="grow"><div className="small">{a.name.replace(/_/g, " ")}</div>
                            <div className="tiny faint">{a.api || a.type}{a.operationId ? ` · ${a.operationId}` : ""}{a.hasSchema ? " · schema" : ""}</div></div>
                            <select className="form-input" style={{ width: 130 }} value={o.mode}
                                    onChange={(e) => setOv({ ...ov, [a.name]: { ...o, mode: e.target.value as Override["mode"] } })}>
                              <option value="auto">Auto</option><option value="mock">Mock output</option><option value="fail">Force failure</option>
                            </select></div>
                          {o.mode === "mock" && <textarea className="form-input mono" rows={4} placeholder='{"body": {"id": "123"}}' value={o.outputs}
                                                           onChange={(e) => setOv({ ...ov, [a.name]: { ...o, outputs: e.target.value } })} />}
                          {o.mode === "fail" && <input className="form-input" placeholder="Error message (optional)" value={o.error}
                                                       onChange={(e) => setOv({ ...ov, [a.name]: { ...o, error: e.target.value } })} />}
                        </div>
                      );
                    })}
                    <div><button className="btn btn-ghost btn-sm" onClick={() => saveMocks.mutate()}><Save size={12} />Save as default mocks</button></div>
                  </div>
                )}
              </div>
            )}
            <div className="row">
              <button className="btn btn-primary" disabled={exec.isPending || required.length > 0} onClick={() => exec.mutate()}
                      title={required.length ? `Fill in: ${required.join(", ")}` : undefined}>
                {exec.isPending ? <Loader2 size={16} className="spin" /> : <Play size={16} />}Run flow</button>
              {runs.length > 0 && (
                <select className="form-input" style={{ width: "auto" }} value={run?.id ?? ""}
                        onChange={(e) => { const r = runs.find((x) => x.id === e.target.value); if (r) { setRun(r); setMarks((m) => ({ ...m, [comp.id]: runMarks(r) })); } }}>
                  <option value="" disabled>Run history ({runs.length})</option>
                  {runs.map((r) => <option key={r.id} value={r.id}>{new Date(r.startedAt).toLocaleTimeString()} · {r.status}</option>)}
                </select>
              )}
            </div>
            {run && <RunDetails run={run} selectedPointer={selectedPointer} />}
          </>
        )}
      </div>
    </aside>
  );
}

/** A run started by the agent in the test pane (tool / InvokeFlowAction). */
export function FlowRunModal({ wid, runId, onClose }: { wid: string; runId: string; onClose: () => void }) {
  const { setMarks } = useWs();
  const navigate = useNavigate();
  const { data: run, error } = useQuery({ queryKey: ["flow-run", wid, runId], queryFn: () => api.flowRun(wid, runId) });
  return (
    <Modal title={run ? `Flow run · ${run.flowName}` : "Flow run"} onClose={onClose} wide actions={run && (
      <button className="btn btn-secondary" onClick={() => { setMarks((m) => ({ ...m, [run.componentId]: runMarks(run) })); navigate(`/w/${wid}?c=${encodeURIComponent(run.componentId)}`); onClose(); }}>
        Show on canvas</button>)}>
      {error ? <div className="small" style={{ color: "var(--c-danger)" }}>{(error as Error).message}</div>
        : !run ? <Loader2 className="spin" size={16} /> : (
          <div style={{ maxHeight: "65vh", overflowY: "auto" }}>
            <div className="act-turn" style={{ marginTop: 0 }}>Inputs from the agent</div>
            <pre className="json-box">{JSON.stringify(run.triggerInputs, null, 2)}</pre>
            <div style={{ height: 12 }} />
            <RunDetails run={run} />
          </div>
        )}
    </Modal>
  );
}
