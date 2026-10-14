import { ReactNode, useCallback, useEffect, useRef } from "react";
import { FlaskConical, Sparkles, X } from "lucide-react";

export type DockTab = "ai" | "test";
const MIN = 380;
const KEY = "csb-dock-w";

export function initialDockWidth(): number {
  try { return Math.max(MIN, Number(localStorage.getItem(KEY)) || 500); } catch { return 500; }
}

/** One docked, resizable right sidebar holding the AI Builder and the Test pane (the page makes room for it). */
export default function RightDock({ open, tab, onTab, onClose, width, onWidth, ai, test }: {
  open: boolean; tab: DockTab; onTab: (t: DockTab) => void; onClose: () => void;
  width: number; onWidth: (w: number) => void; ai: ReactNode; test: ReactNode;
}) {
  const dragging = useRef(false);
  useEffect(() => {
    document.documentElement.style.setProperty("--dock-w", `${open ? width : 0}px`);
  }, [open, width]);

  const onMove = useCallback((e: MouseEvent) => {
    if (!dragging.current) return;
    const w = Math.min(Math.max(MIN, window.innerWidth - e.clientX), Math.round(window.innerWidth * 0.7));
    onWidth(w);
  }, [onWidth]);
  const onUp = useCallback(() => {
    if (!dragging.current) return;
    dragging.current = false;
    document.body.classList.remove("resizing");
    try { localStorage.setItem(KEY, String(width)); } catch { /* storage unavailable */ }
  }, [width]);
  useEffect(() => {
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => { window.removeEventListener("mousemove", onMove); window.removeEventListener("mouseup", onUp); };
  }, [onMove, onUp]);

  return (
    <aside className={`dock${open ? " open" : ""}`} style={{ width }} aria-hidden={!open}>
      <div className="dock-resize" onMouseDown={() => { dragging.current = true; document.body.classList.add("resizing"); }}
           title="Drag to resize" />
      <div className="dock-head">
        <div className="tabs" style={{ padding: 0, border: "none", flex: 1 }}>
          <button className={`tab${tab === "ai" ? " active" : ""}`} onClick={() => onTab("ai")}><Sparkles size={14} />AI Builder</button>
          <button className={`tab${tab === "test" ? " active" : ""}`} onClick={() => onTab("test")}><FlaskConical size={14} />Test</button>
        </div>
        <button className="btn-icon" onClick={onClose} aria-label="Close panel"><X size={16} /></button>
      </div>
      <div className="dock-pane" hidden={tab !== "ai"}>{ai}</div>
      <div className="dock-pane" hidden={tab !== "test"}>{test}</div>
    </aside>
  );
}
