import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from "react";
import { X } from "lucide-react";
import type { Severity } from "../api";

type ToastKind = "success" | "error" | "info";
interface Toast { id: number; kind: ToastKind; text: string }

const ToastCtx = createContext<(text: string, kind?: ToastKind) => void>(() => {});
export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((text: string, kind: ToastKind = "info") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, kind, text }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === "error" ? 7000 : 3500);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts">
        {toasts.map((t) => <div key={t.id} className={`toast ${t.kind}`}>{t.text}</div>)}
      </div>
    </ToastCtx.Provider>
  );
}

export function Modal({ title, onClose, children, wide, actions }: {
  title: string; onClose: () => void; children: ReactNode; wide?: boolean; actions?: ReactNode;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <>
      <div className="modal-backdrop" onClick={onClose} />
      <div className={`modal-panel${wide ? " wide" : ""}`} role="dialog" aria-label={title}>
        <div className="modal-head">
          <h2 style={{ margin: 0, fontSize: 16 }}>{title}</h2>
          <button className="btn-icon" onClick={onClose} aria-label="Close"><X size={16} /></button>
        </div>
        {children}
        {actions && <div className="modal-actions">{actions}</div>}
      </div>
    </>
  );
}

export function SeverityPill({ s }: { s: Severity }) {
  const cls = s === "error" ? "error" : s === "warning" ? "warning" : "";
  return <span className={`pill-sm ${cls}`}>{s}</span>;
}

export function Empty({ icon, title, children }: { icon: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <div className="icon-box accent">{icon}</div>
      <h3>{title}</h3>
      {children}
    </div>
  );
}
