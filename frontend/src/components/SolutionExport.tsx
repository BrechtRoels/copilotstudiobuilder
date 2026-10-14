import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, FileArchive, Loader2, PackageCheck, PackagePlus, RefreshCw, Save, Upload, X } from "lucide-react";
import { fileLabel } from "../lib/labels";
import { api, SolutionIdentity } from "../api";
import { useToast } from "./ui";

/** Deploy → importable solution. New agent: generated package. Existing agent: patch an uploaded export. */
export default function SolutionExport({ wid }: { wid: string }) {
  const qc = useQueryClient();
  const toast = useToast();
  const fileRef = useRef<HTMLInputElement>(null);
  const [bot, setBot] = useState<string | undefined>();
  const { data: base, isLoading } = useQuery({ queryKey: ["solbase", wid], queryFn: () => api.solutionBase(wid) });
  const { data: plan, error: planError, isFetching, refetch } = useQuery({
    queryKey: ["solplan", wid, bot, base?.present, base?.identity], queryFn: () => api.solutionPlan(wid, bot), enabled: !!base, retry: false,
  });
  const refresh = () => { qc.invalidateQueries({ queryKey: ["solbase", wid] }); qc.invalidateQueries({ queryKey: ["solplan", wid] }); };
  const upload = useMutation({
    mutationFn: (f: File) => api.uploadSolutionBase(wid, f),
    onSuccess: () => { refresh(); toast("Base solution stored: exports will update that agent", "success"); },
    onError: (e: Error) => toast(e.message, "error"),
  });
  const removeBase = useMutation({ mutationFn: () => api.removeSolutionBase(wid), onSuccess: refresh });

  if (isLoading || !base) return <div className="card"><Loader2 className="spin" /></div>;
  const picker = <input ref={fileRef} type="file" accept=".zip" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) upload.mutate(f); e.target.value = ""; }} />;

  const counts = { update: 0, add: 0, skip: 0 };
  plan?.items.forEach((i) => { counts[i.action] += 1; });

  return (
    <div className="card stack" style={{ gap: 16 }}>
      {picker}
      <div className="grid-2">
        <button className={`source-toggle${!base.present ? " active" : ""}`} onClick={() => base.present && removeBase.mutate()}>
          <div className={`icon-box${!base.present ? " accent" : ""}`}><PackagePlus size={18} /></div>
          <div><h3>New agent</h3><div className="small muted">Generated package, nothing to upload</div></div></button>
        <button className={`source-toggle${base.present ? " active" : ""}`} onClick={() => !base.present && fileRef.current?.click()}>
          <div className={`icon-box${base.present ? " accent" : ""}`}><FileArchive size={18} /></div>
          <div><h3>Update existing agent</h3><div className="small muted">{base.present ? (base.displayName || base.uniqueName) : "Upload its exported solution once"}</div></div></button>
      </div>

      {base.present ? (
        <div className="row between wrap small">
          <span className="muted">Base <b>{base.uniqueName}</b> v{base.version} → export v{plan?.version ?? "…"}. Components are matched by name and updated in place.</span>
          <div className="row">
            {(base.bots?.length ?? 0) > 1 && (
              <select className="form-input" style={{ width: 240, padding: "6px 10px" }} value={bot ?? base.bots![0].schema} onChange={(e) => setBot(e.target.value)} aria-label="Target agent">
                {base.bots!.map((b) => <option key={b.schema} value={b.schema}>{b.name}</option>)}
              </select>)}
            <button className="btn btn-ghost btn-sm" onClick={() => fileRef.current?.click()}><Upload size={14} />Replace</button>
            <button className="btn btn-ghost btn-sm" onClick={() => removeBase.mutate()}><X size={14} />Remove</button>
          </div>
        </div>
      ) : base.identity && <IdentityForm wid={wid} identity={base.identity} onSaved={refresh} />}

      {planError ? (
        <div className="row small" style={{ color: "var(--c-danger)" }}><AlertTriangle size={14} />{(planError as Error).message}</div>
      ) : !plan ? <div className="row small muted"><Loader2 size={14} className="spin" />Preparing export plan…</div> : (
        <>
          <div className="row wrap small">
            {counts.update > 0 && <span className="pill-sm warning">{counts.update} update</span>}
            <span className="pill-sm ok">{counts.add} new</span>
            {counts.skip > 0 && <span className="pill-sm">{counts.skip} skipped</span>}
            <span className="faint tiny">export v{plan.version}</span>
            <button className="btn btn-ghost btn-sm" onClick={() => refetch()}><RefreshCw size={14} className={isFetching ? "spin" : ""} />Refresh</button>
          </div>
          <div style={{ border: "1px solid var(--c-edge)", borderRadius: 8, maxHeight: 240, overflowY: "auto" }}>
            <table className="tbl"><tbody>
              {plan.items.map((i) => (
                <tr key={i.source}>
                  <td style={{ width: 70 }}><span className={`pill-sm ${i.action === "add" ? "ok" : i.action === "update" ? "warning" : ""}`}>{i.action}</span></td>
                  <td><div className="small">{fileLabel(i.source)}</div><div className="mono tiny faint">{i.source}</div></td>
                  <td className="mono tiny faint">{i.target}{i.note && <div className="small" style={{ fontFamily: "Inter" }}>{i.note}</div>}</td>
                </tr>))}
            </tbody></table>
          </div>
          {plan.warnings.map((w) => <div key={w} className="row small" style={{ alignItems: "flex-start" }}><AlertTriangle size={14} color="var(--c-warn)" />{w}</div>)}
          <div className="row between wrap" style={{ alignItems: "flex-end" }}>
            <ol className="tiny muted" style={{ margin: 0, paddingLeft: 16, maxWidth: 560 }}>
              <li>Download, then <b>don't unzip</b> it. In Safari turn off <i>Open “safe” files after downloading</i>, or Finder re-compression breaks the package.</li>
              <li>Power Apps → <b>Solutions → Import solution</b> → pick this .zip → map the connection references.</li>
              <li>Open the agent in Copilot Studio and <b>Publish</b>. Re-importing a later export upgrades the same agent.</li>
            </ol>
            <a className="btn btn-primary" href={api.solutionExportUrl(wid, bot)}><PackageCheck size={16} />Download importable solution</a>
          </div>
          {counts.skip === 0 && plan.warnings.length === 0 && <div className="row small" style={{ color: "var(--c-success)" }}><CheckCircle2 size={14} />Everything in the workspace maps onto the solution.</div>}
        </>
      )}
    </div>
  );
}

function IdentityForm({ wid, identity, onSaved }: { wid: string; identity: SolutionIdentity; onSaved: () => void }) {
  const toast = useToast();
  const [v, setV] = useState(identity);
  useEffect(() => setV(identity), [identity]);
  const dirty = JSON.stringify(v) !== JSON.stringify(identity);
  const save = useMutation({
    mutationFn: () => api.saveSolutionIdentity(wid, v),
    onSuccess: () => { onSaved(); toast("Solution settings saved", "success"); },
    onError: (e: Error) => toast(e.message, "error"),
  });
  const field = (k: keyof SolutionIdentity, label: string, hint?: string) => (
    <div><label className="form-label" htmlFor={`id-${k}`}>{label}</label>
      <input id={`id-${k}`} className="form-input mono" style={{ padding: "6px 10px", fontSize: 13 }} value={v[k]} onChange={(e) => setV({ ...v, [k]: e.target.value })} />
      {hint && <div className="tiny faint" style={{ marginTop: 2 }}>{hint}</div>}</div>
  );
  return (
    <div className="stack" style={{ gap: 8 }}>
      <div className="grid-3">
        {field("uniqueName", "Solution name", "Keep it the same to upgrade later")}
        {field("botName", "Agent display name")}
        {field("botSchema", "Agent schema name", "Identity of the agent in Dataverse")}
        {field("publisherUniqueName", "Publisher")}
        {field("publisherDisplayName", "Publisher display name")}
        {field("prefix", "Publisher prefix", "2-8 lowercase; use your org's publisher to share one")}
      </div>
      {dirty && <div className="row" style={{ justifyContent: "flex-end" }}>
        <button className="btn btn-secondary btn-sm" disabled={save.isPending} onClick={() => save.mutate()}><Save size={14} />Save settings</button></div>}
    </div>
  );
}
