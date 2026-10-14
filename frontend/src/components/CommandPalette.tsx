import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { CornerDownLeft, FileCode2, FlaskConical, LayoutDashboard, Search, Sparkles } from "lucide-react";
import { useWs } from "../pages/WorkspaceLayout";
import { fileLabel } from "../lib/labels";

interface Item { id: string; title: string; hint: string; icon: JSX.Element; run: () => void }

/** ⌘K / Ctrl+K: jump to any component or page, open the AI Builder or the test pane. */
export default function CommandPalette() {
  const { ws, select, openAI, openTest } = useWs();
  const nav = useNavigate();
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [idx, setIdx] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const base = `/w/${ws.info.id}`;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setOpen((o) => !o); setQ(""); setIdx(0); }
      else if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  useEffect(() => { if (open) setTimeout(() => input.current?.focus(), 0); }, [open]);

  const items = useMemo<Item[]>(() => {
    const pages: Item[] = [
      ["Overview", "", <LayoutDashboard size={14} />], ["Build", "", <FileCode2 size={14} />], ["Agent map", "/map", <LayoutDashboard size={14} />],
      ["Analyzer", "/analyzer", <LayoutDashboard size={14} />], ["Documentation", "/docs", <LayoutDashboard size={14} />],
      ["Deploy · importable solution", "/deploy", <LayoutDashboard size={14} />],
    ].map(([t, p, i]) => ({ id: `page:${t}`, title: t as string, hint: "page", icon: i as JSX.Element,
                            run: () => nav(t === "Overview" ? `${base}/overview` : `${base}${p}`) }));
    const actions: Item[] = [
      { id: "ai", title: "Open AI Builder", hint: "action", icon: <Sparkles size={14} />, run: () => openAI() },
      { id: "test", title: "Test the agent", hint: "action", icon: <FlaskConical size={14} />, run: openTest },
      { id: "newtopic", title: "New topic with AI", hint: "action", icon: <Sparkles size={14} />, run: () => openAI({ mode: "topic" }) },
      { id: "newflow", title: "New agent flow with AI", hint: "action", icon: <Sparkles size={14} />, run: () => openAI({ mode: "flow" }) },
    ];
    const comps: Item[] = ws.components.filter((c) => c.category !== "other").map((c) => ({
      id: c.id, title: fileLabel(c.path, c.text, ws), hint: c.path, icon: <FileCode2 size={14} />, run: () => select(c.id),
    }));
    return [...comps, ...pages, ...actions];
  }, [ws, nav, base, select, openAI, openTest]);

  const shown = useMemo(() => {
    const words = q.toLowerCase().split(/\s+/).filter(Boolean);
    return items.filter((i) => words.every((w) => `${i.title} ${i.hint}`.toLowerCase().includes(w))).slice(0, 12);
  }, [items, q]);

  if (!open) return null;
  const go = (i: Item | undefined) => { if (i) { setOpen(false); i.run(); } };
  return (
    <>
      <div className="modal-backdrop" onClick={() => setOpen(false)} />
      <div className="palette" role="dialog" aria-label="Command palette">
        <div className="row palette-input"><Search size={16} color="var(--c-fg3)" />
          <input ref={input} value={q} placeholder="Jump to a topic, flow, page or action…"
                 onChange={(e) => { setQ(e.target.value); setIdx(0); }}
                 onKeyDown={(e) => {
                   if (e.key === "ArrowDown") { e.preventDefault(); setIdx((i) => Math.min(i + 1, shown.length - 1)); }
                   if (e.key === "ArrowUp") { e.preventDefault(); setIdx((i) => Math.max(i - 1, 0)); }
                   if (e.key === "Enter") go(shown[idx]);
                 }} /></div>
        <div className="palette-list">
          {shown.length === 0 && <div className="small faint" style={{ padding: 12 }}>No matches</div>}
          {shown.map((i, k) => (
            <button key={i.id} className={`palette-item${k === idx ? " active" : ""}`} onMouseEnter={() => setIdx(k)} onClick={() => go(i)}>
              {i.icon}<span className="grow ellipsis">{i.title}</span><span className="tiny faint ellipsis" style={{ maxWidth: 220 }}>{i.hint}</span>
              {k === idx && <CornerDownLeft size={12} color="var(--c-fg3)" />}
            </button>
          ))}
        </div>
      </div>
    </>
  );
}
