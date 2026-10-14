import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { CheckCircle2, Loader2, RefreshCw, ShieldCheck, Wrench } from "lucide-react";
import { api, Finding, Severity } from "../api";
import { SeverityPill } from "../components/ui";
import { useWs } from "./WorkspaceLayout";

export default function AnalyzerPage() {
  const { ws, version, openAI } = useWs();
  const nav = useNavigate();
  const [filter, setFilter] = useState<Severity | "all">("all");
  const { data = [], isLoading, refetch, isFetching } = useQuery({
    queryKey: ["analyze", ws.info.id, version],
    queryFn: () => api.analyze(ws.info.id),
  });
  const counts = useMemo(() => ({
    error: data.filter((f) => f.severity === "error").length,
    warning: data.filter((f) => f.severity === "warning").length,
    info: data.filter((f) => f.severity === "info").length,
  }), [data]);
  const byComp = useMemo(() => {
    const m = new Map<string, Finding[]>();
    data.filter((f) => filter === "all" || f.severity === filter).forEach((f) => m.set(f.component, [...(m.get(f.component) ?? []), f]));
    return [...m.entries()];
  }, [data, filter]);
  const name = (id: string) => ws.components.find((c) => c.id === id)?.displayName ?? id;

  return (
    <div className="page-container" style={{ overflowY: "auto" }}>
      <div className="page-header">
        <div><h1>Analyzer</h1><div className="muted" style={{ marginTop: 4 }}>Microsoft's authoring schema, lint rules and cross-file checks: redirects, variables, trigger overlap, routing descriptions, flow error handling.</div></div>
        <button className="btn btn-secondary" onClick={() => refetch()}><RefreshCw size={16} className={isFetching ? "spin" : ""} />Re-run</button>
      </div>
      <div className="grid-3" style={{ marginBottom: 24 }}>
        {(["error", "warning", "info"] as const).map((s) => (
          <button key={s} className={`source-toggle${filter === s ? " active" : ""}`} onClick={() => setFilter(filter === s ? "all" : s)}>
            <div className="stat"><b style={{ color: s === "error" ? "var(--c-danger)" : s === "warning" ? "var(--c-warn)" : undefined }}>{counts[s]}</b><span>{s}s</span></div>
          </button>
        ))}
      </div>
      {isLoading ? <Loader2 className="spin" /> : byComp.length === 0 ? (
        <div className="card row"><CheckCircle2 size={18} color="var(--c-success)" />No findings{filter !== "all" ? ` at ${filter} level` : ""}.</div>
      ) : (
        <div className="list">
          {byComp.map(([cid, fs]) => (
            <div key={cid} className="card">
              <div className="row between" style={{ marginBottom: 8 }}>
                <button className="tree-item" style={{ width: "auto", padding: 0 }} onClick={() => nav(`/w/${ws.info.id}?c=${encodeURIComponent(cid)}`)}>
                  <ShieldCheck size={14} /><b>{name(cid)}</b><span className="mono tiny faint">{cid}</span></button>
                {fs.some((f) => f.fixable && f.severity !== "info") && (
                  <button className="btn btn-ghost btn-sm" onClick={() => openAI({
                    mode: "fix", target: cid, prompt: "",
                    findings: fs.filter((f) => f.fixable && f.severity !== "info").map((f) => `${f.pointer ? f.pointer + ": " : ""}${f.message}`),
                  })}><Wrench size={14} />AI fix all</button>)}
              </div>
              {fs.map((f, i) => (
                <div key={i} className="row small" style={{ alignItems: "flex-start", padding: "4px 0" }}>
                  <SeverityPill s={f.severity} />
                  <span className="grow">{f.message}{f.pointer && <span className="mono faint tiny"> · {f.pointer}</span>}</span>
                  <span className="pill-sm">{f.rule}</span>
                </div>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
