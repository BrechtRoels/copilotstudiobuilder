import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CloudUpload, Download, FolderInput, Loader2, Terminal, CheckCircle2, AlertTriangle } from "lucide-react";
import { api } from "../api";
import { Modal, useToast } from "../components/ui";
import SolutionExport from "../components/SolutionExport";
import { useWs } from "./WorkspaceLayout";

export default function DeployPage() {
  const { ws } = useWs();
  const qc = useQueryClient();
  const toast = useToast();
  const [target, setTarget] = useState("");
  const [confirmPush, setConfirmPush] = useState(false);
  const [output, setOutput] = useState<{ cmd: string; text: string; ok: boolean } | null>(null);
  const { data: sync } = useQuery({ queryKey: ["sync", ws.info.id], queryFn: () => api.syncStatus(ws.info.id) });

  const merge = useMutation({
    mutationFn: () => api.merge(ws.info.id, target.trim()),
    onSuccess: (r) => toast(`Copied ${r.written.length} file(s). Now use Apply changes in VS Code.`, "success"),
    onError: (e: Error) => toast(e.message, "error"),
  });
  const run = useMutation({
    mutationFn: (cmd: string) => api.sync(ws.info.id, cmd).then((r) => ({ cmd, r })),
    onSuccess: ({ cmd, r }) => {
      setOutput({ cmd, ok: r.exitCode === 0, text: r.json ? JSON.stringify(r.json, null, 2) : (r.stdout || r.stderr) });
      if (cmd === "pull") qc.invalidateQueries({ queryKey: ["ws", ws.info.id] });
    },
    onError: (e: Error) => toast(e.message, "error"),
  });

  return (
    <div className="page-container" style={{ overflowY: "auto" }}>
      <div className="page-header"><div><h1>Deploy</h1>
        <div className="muted" style={{ marginTop: 4 }}>Get this workspace into Copilot Studio: as a solution you import in Power Apps / Copilot Studio, or through Microsoft's VS Code extension.</div></div></div>

      <section className="section">
        <h2>1 · Importable solution (Power Apps / Copilot Studio)</h2>
        <SolutionExport wid={ws.info.id} />
      </section>

      <section className="section">
        <h2>2 · VS Code extension</h2>
        <div className="card small stack">
          {ws.connected ? (
            <div>This folder was cloned with the extension. Open it in VS Code → Copilot Studio panel → <b>Preview changes</b> → <b>Apply changes</b>.</div>
          ) : (
            <>
              <div>This workspace is local only. In Copilot Studio create the agent (or use an existing one), then in VS Code: <b>Copilot Studio → Clone agent</b> into a folder. Copy this workspace into that clone:</div>
              <div className="row">
                <input className="form-input mono" placeholder="/path/to/cloned-agent" value={target} onChange={(e) => setTarget(e.target.value)} />
                <button className="btn btn-primary" disabled={!target.trim() || merge.isPending} onClick={() => merge.mutate()}><FolderInput size={16} />Copy into clone</button>
              </div>
              <div className="faint tiny">Keeps the clone's .mcs/, settings and connection references; merges instructions and conversation starters into its agent.mcs.yml.</div>
            </>
          )}
          <code className="mono faint">{ws.info.path}</code>
        </div>
      </section>

      <section className="section">
        <h2>3 · Built-in sync (optional)</h2>
        <div className="card stack">
          <div className="small muted">Runs Microsoft's <span className="mono">manage-agent</span> script from skills-for-copilot-studio: full language-server validation, preview, pull and push. Sign-in opens in your browser.</div>
          {sync && !sync.available ? (
            <div className="stack small">{sync.reasons.map((r) => <div key={r} className="row" style={{ alignItems: "flex-start" }}><AlertTriangle size={14} color="var(--c-warn)" />{r}</div>)}</div>
          ) : sync && (
            <div className="row wrap">
              <button className="btn btn-secondary" disabled={run.isPending} onClick={() => run.mutate("validate")}><CheckCircle2 size={16} />Validate (LSP)</button>
              <button className="btn btn-secondary" disabled={run.isPending} onClick={() => run.mutate("changes")}><Terminal size={16} />Preview changes</button>
              <button className="btn btn-secondary" disabled={run.isPending} onClick={() => run.mutate("pull")}><Download size={16} />Pull</button>
              <button className="btn btn-primary" disabled={run.isPending} onClick={() => setConfirmPush(true)}><CloudUpload size={16} />Push</button>
              {run.isPending && <span className="row small muted"><Loader2 size={14} className="spin" />Running… complete the browser sign-in if asked</span>}
            </div>
          )}
          {output && (<div><div className={`pill-sm ${output.ok ? "ok" : "error"}`} style={{ marginBottom: 6 }}>{output.cmd} · {output.ok ? "ok" : "failed"}</div>
            <pre className="diff" style={{ padding: 10, whiteSpace: "pre-wrap" }}>{output.text}</pre></div>)}
        </div>
      </section>

      <section className="section">
        <h2>4 · Workspace files</h2>
        <div className="card row between"><div className="small muted">The raw workspace in the VS Code agent-folder format, for Git or sharing.
          <b> Not importable</b> in Power Apps; use the importable solution above for that.</div>
          <a className="btn btn-secondary" href={api.zipUrl(ws.info.id)}><Download size={16} />Workspace .zip</a></div>
      </section>

      {confirmPush && (
        <Modal title="Push to Copilot Studio?" onClose={() => setConfirmPush(false)} actions={<>
          <button className="btn btn-secondary" onClick={() => setConfirmPush(false)}>Cancel</button>
          <button className="btn btn-primary" onClick={() => { setConfirmPush(false); run.mutate("push"); }}><CloudUpload size={16} />Push</button></>}>
          <div className="small muted">This overwrites the draft of agent <span className="mono">{sync?.agentId}</span> in environment <span className="mono">{sync?.environmentId}</span> with your local files. Pull first to avoid version conflicts. Publishing stays a separate step in Copilot Studio.</div>
        </Modal>
      )}
    </div>
  );
}
