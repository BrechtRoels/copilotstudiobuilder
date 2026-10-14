import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { FileCode2, Library, Loader2 } from "lucide-react";
import { api, Template } from "../api";
import { CodeEditor } from "../components/Editors";
import { Modal } from "../components/ui";

export default function PatternsPage() {
  const { data: patterns = [], isLoading } = useQuery({ queryKey: ["patterns"], queryFn: api.patterns, staleTime: Infinity });
  const { data: templates = [] } = useQuery({ queryKey: ["templates"], queryFn: api.templates, staleTime: Infinity });
  const [openId, setOpenId] = useState<string | null>(null);
  const [tpl, setTpl] = useState<Template | null>(null);
  const [tag, setTag] = useState<string | null>(null);
  const { data: body } = useQuery({ queryKey: ["pattern", openId], queryFn: () => api.pattern(openId!), enabled: !!openId });
  const tags = [...new Set(patterns.flatMap((p) => p.tags))].sort();
  const shown = patterns.filter((p) => !tag || p.tags.includes(tag));

  return (
    <div className="page-container" style={{ overflowY: "auto" }}>
      <div className="page-header"><div><h1>Pattern library</h1>
        <div className="muted" style={{ marginTop: 4 }}>Proven Copilot Studio patterns and templates from Microsoft's <a href="https://github.com/microsoft/skills-for-copilot-studio" target="_blank" rel="noreferrer">skills-for-copilot-studio</a> (MIT). The AI Builder uses these automatically; you can also pin them in the AI panel.</div></div></div>

      <section className="section">
        <h2>Patterns</h2>
        <div className="filter-bar">
          <button className={`filter-btn${!tag ? " active" : ""}`} onClick={() => setTag(null)}>All</button>
          {tags.slice(0, 18).map((t) => <button key={t} className={`filter-btn${tag === t ? " active" : ""}`} onClick={() => setTag(t)}>{t}</button>)}
        </div>
        {isLoading ? <Loader2 className="spin" /> : (
          <div className="grid-2">{shown.map((p) => (
            <button key={p.id} className="card hoverable" style={{ textAlign: "left", cursor: "pointer", color: "inherit", font: "inherit" }} onClick={() => setOpenId(p.id)}>
              <div className="row between"><h3>{p.name}</h3><span className={`pill-sm ${p.status === "proven" ? "ok" : ""}`}>{p.status}</span></div>
              <div className="small muted" style={{ marginTop: 6 }}>{p.description}</div>
            </button>))}</div>
        )}
      </section>

      <section className="section">
        <h2>Templates</h2>
        <div className="list">{templates.map((t) => (
          <div key={t.path} className="list-card" onClick={() => setTpl(t)}>
            <div className="icon-box"><FileCode2 size={18} /></div>
            <div className="grow"><h3 className="mono" style={{ fontSize: 13 }}>{t.path}</h3><div className="small muted">{t.description || "—"}</div></div>
          </div>))}</div>
      </section>

      {openId && (
        <Modal wide title={body?.name ?? "Pattern"} onClose={() => setOpenId(null)}>
          {body ? <div className="msg-ai small"><ReactMarkdown remarkPlugins={[remarkGfm]}>{body.body}</ReactMarkdown></div> : <Loader2 className="spin" />}
        </Modal>
      )}
      {tpl && (
        <Modal wide title={tpl.path} onClose={() => setTpl(null)}>
          <div style={{ height: 420, border: "1px solid var(--c-edge)", borderRadius: 8, overflow: "hidden" }}>
            <CodeEditor value={tpl.content} language="yaml" readOnly /></div>
          <div className="row small faint" style={{ marginTop: 8 }}><Library size={12} />Templates contain `_REPLACE` ids; the AI Builder and Paste flow replace them for you.</div>
        </Modal>
      )}
    </div>
  );
}
