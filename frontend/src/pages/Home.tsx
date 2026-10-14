import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, FileArchive, FolderOpen, Loader2, Sparkles, Trash2, ArrowRight } from "lucide-react";
import { api, WorkspaceInfo } from "../api";
import { Modal, useToast } from "../components/ui";

type Start = "ai" | "folder" | "zip";

const ORIGIN: Record<WorkspaceInfo["origin"], string> = {
  generated: "AI generated", cloned: "cloned folder", solution: "from solution", empty: "new",
};

export default function Home() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const { data: list = [], isLoading } = useQuery({ queryKey: ["workspaces"], queryFn: api.workspaces });
  const [start, setStart] = useState<Start>("ai");
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [folder, setFolder] = useState("");
  const [toDelete, setToDelete] = useState<WorkspaceInfo | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const go = (w: WorkspaceInfo, state?: unknown) => { qc.invalidateQueries({ queryKey: ["workspaces"] }); nav(`/w/${w.id}`, { state }); };

  const create = useMutation({
    mutationFn: () => api.createWorkspace(name.trim() || "New agent", desc),
    onSuccess: (w) => go(w, desc.trim() ? { ai: { mode: "agent", prompt: desc.trim() } } : { ai: { mode: "agent" } }),
    onError: (e: Error) => toast(e.message, "error"),
  });
  const open = useMutation({
    mutationFn: () => api.openWorkspace(folder.trim()),
    onSuccess: (w) => go(w), onError: (e: Error) => toast(e.message, "error"),
  });
  const imp = useMutation({
    mutationFn: (f: File) => api.importSolution(f),
    onSuccess: (r) => { toast(`Imported ${r.files.length} files`, "success"); go(r.workspace); },
    onError: (e: Error) => toast(e.message, "error"),
  });
  const del = useMutation({
    mutationFn: ({ w, files }: { w: WorkspaceInfo; files: boolean }) => api.deleteWorkspace(w.id, files),
    onSuccess: () => { setToDelete(null); qc.invalidateQueries({ queryKey: ["workspaces"] }); },
  });

  return (
    <div className="page-container">
      <div className="page-header">
        <div><h1>Copilot Studio Builder</h1>
          <div className="muted" style={{ marginTop: 4 }}>Generate, visualise, validate and document Copilot Studio agents, topics and flows as code.</div></div>
      </div>

      <section className="section">
        <h2>Start</h2>
        <div className="grid-3" style={{ marginBottom: 16 }}>
          <button className={`source-toggle${start === "ai" ? " active" : ""}`} onClick={() => setStart("ai")}>
            <div className="icon-box accent"><Sparkles size={18} /></div>
            <div><h3>Build with AI</h3><div className="small muted">Describe the agent, get the full workspace</div></div></button>
          <button className={`source-toggle${start === "folder" ? " active" : ""}`} onClick={() => setStart("folder")}>
            <div className="icon-box"><FolderOpen size={18} /></div>
            <div><h3>Open agent folder</h3><div className="small muted">Cloned with the VS Code extension</div></div></button>
          <button className={`source-toggle${start === "zip" ? " active" : ""}`} onClick={() => setStart("zip")}>
            <div className="icon-box"><FileArchive size={18} /></div>
            <div><h3>Import solution</h3><div className="small muted">Optional · exported .zip</div></div></button>
        </div>

        <div className="card">
          {start === "ai" && (
            <>
              <div className="field"><label className="form-label" htmlFor="n">Agent name</label>
                <input id="n" className="form-input" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. IT Helpdesk" /></div>
              <div className="field"><label className="form-label" htmlFor="d">What should it do? (optional — you can also chat after)</label>
                <textarea id="d" className="form-input" rows={4} value={desc} onChange={(e) => setDesc(e.target.value)}
                          placeholder="An orchestrator for employee IT questions. Child agent for HR. Topic to reset passwords. A flow that creates a ticket in SharePoint and emails the service desk. Ground answers on our IT SharePoint site." /></div>
              <div className="row between"><span className="tiny faint">Creates a local workspace in the Copilot Studio VS Code format.</span>
                <button className="btn btn-primary" disabled={create.isPending} onClick={() => create.mutate()}>
                  {create.isPending ? <Loader2 size={16} className="spin" /> : <Sparkles size={16} />}Create & open AI Builder</button></div>
            </>
          )}
          {start === "folder" && (
            <>
              <div className="field"><label className="form-label" htmlFor="f">Folder path</label>
                <input id="f" className="form-input mono" value={folder} onChange={(e) => setFolder(e.target.value)}
                       placeholder="/Users/you/agents/it-helpdesk" /></div>
              <div className="small muted" style={{ marginBottom: 16 }}>In VS Code: Copilot Studio extension → Agents → right-click → <b>Clone agent</b>. Files are edited in place; use <b>Apply changes</b> in VS Code (or Deploy → Push) to sync back.</div>
              <div className="row" style={{ justifyContent: "flex-end" }}>
                <button className="btn btn-primary" disabled={!folder.trim() || open.isPending} onClick={() => open.mutate()}>
                  <FolderOpen size={16} />Open folder</button></div>
            </>
          )}
          {start === "zip" && (
            <>
              <div className="small muted" style={{ marginBottom: 16 }}>Export the solution that contains your agent from Power Apps (<b>Solutions → Export → Unmanaged</b>).
                Topics, tools, knowledge, child agents and cloud flows are converted to the workspace format. Nothing is uploaded anywhere.</div>
              <input ref={fileRef} type="file" accept=".zip" hidden onChange={(e) => e.target.files?.[0] && imp.mutate(e.target.files[0])} />
              <div className="row" style={{ justifyContent: "flex-end" }}>
                <button className="btn btn-primary" disabled={imp.isPending} onClick={() => fileRef.current?.click()}>
                  {imp.isPending ? <Loader2 size={16} className="spin" /> : <FileArchive size={16} />}Choose solution .zip</button></div>
            </>
          )}
        </div>
      </section>

      <section className="section">
        <h2>Workspaces</h2>
        {isLoading ? <Loader2 className="spin" /> : list.length === 0 ? (
          <div className="card muted small">No workspaces yet.</div>
        ) : (
          <div className="list">
            {[...list].reverse().map((w) => (
              <div key={w.id} className="list-card" onClick={() => nav(`/w/${w.id}/overview`)} role="link" tabIndex={0}
                   onKeyDown={(e) => e.key === "Enter" && nav(`/w/${w.id}/overview`)}>
                <div className="icon-box"><Bot size={18} /></div>
                <div className="grow">
                  <h3>{w.name}</h3>
                  <div className="row small faint"><span className="pill-sm">{ORIGIN[w.origin]}</span><span className="ellipsis mono tiny">{w.path}</span></div>
                </div>
                <button className="btn-icon reveal" aria-label="Remove" onClick={(e) => { e.stopPropagation(); setToDelete(w); }}><Trash2 size={16} /></button>
                <ArrowRight size={16} className="faint" />
              </div>
            ))}
          </div>
        )}
      </section>

      {toDelete && (
        <Modal title={`Remove ${toDelete.name}?`} onClose={() => setToDelete(null)} actions={<>
          <button className="btn btn-secondary" onClick={() => del.mutate({ w: toDelete, files: false })}>Remove from list</button>
          {toDelete.origin !== "cloned" && <button className="btn btn-danger" onClick={() => del.mutate({ w: toDelete, files: true })}>Delete files too</button>}
        </>}>
          <div className="small muted">{toDelete.origin === "cloned"
            ? "This only removes it from the app. Your cloned folder is never deleted."
            : "Deleting files removes the workspace folder created by this app. This can't be undone."}</div>
        </Modal>
      )}
    </div>
  );
}
