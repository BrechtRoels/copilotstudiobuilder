import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { Outlet, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Cloud, CloudOff, FlaskConical, FolderOpen, Loader2, Sparkles } from "lucide-react";
import { api, Component, Proposal, Workspace } from "../api";
import AIPanel, { AIRequest } from "../components/AIPanel";
import TestPane from "../components/TestPane";
import RightDock, { DockTab, initialDockWidth } from "../components/RightDock";
import ReviewChanges from "../components/ReviewChanges";
import CommandPalette from "../components/CommandPalette";

/** Canvas marks from the test pane / flow runs: component id -> node pointer -> run status. */
export type Marks = Record<string, Record<string, string>>;

interface Ctx {
  ws: Workspace;
  version: string;          // changes whenever any file content changes (query-key salt)
  selected: Component | null;
  select: (id: string | null) => void;
  openAI: (req?: AIRequest) => void;
  marks: Marks;
  setMarks: (fn: (m: Marks) => Marks) => void;
  openTest: () => void;
  /** newest AI proposal that hasn't been applied (explorer / map badges) */
  pending: Proposal | null;
  setPending: (p: Proposal | null) => void;
  openReview: (p: Proposal) => void;
}
const WsCtx = createContext<Ctx | null>(null);
export const useWs = () => useContext(WsCtx)!;

export default function WorkspaceLayout() {
  const { wid } = useParams();
  const [params, setParams] = useSearchParams();
  const [dockOpen, setDockOpen] = useState(false);
  const [dockTab, setDockTab] = useState<DockTab>("ai");
  const [dockW, setDockW] = useState(initialDockWidth);
  const [aiReq, setAiReq] = useState<AIRequest | null>(null);
  const [testStarted, setTestStarted] = useState(false);
  const [marks, setMarksState] = useState<Marks>({});
  const [pending, setPendingState] = useState<Proposal | null>(null);
  const [review, setReview] = useState<Proposal | null>(null);
  const setMarks = useCallback((fn: (m: Marks) => Marks) => setMarksState(fn), []);
  const setPending = useCallback((p: Proposal | null) => setPendingState(p), []);
  const { data: ws, isLoading, error } = useQuery({ queryKey: ["ws", wid], queryFn: () => api.workspace(wid!) });
  const location = useLocation();
  const navigate = useNavigate();

  // Home → "Create & open AI Builder" passes the first request via router state.
  useEffect(() => {
    const ai = (location.state as { ai?: AIRequest } | null)?.ai;
    if (ai) {
      setAiReq(ai);
      setDockTab("ai");
      setDockOpen(true);
      navigate(location.pathname + location.search, { replace: true, state: null });
    }
  }, [location, navigate]);
  useEffect(() => () => { document.documentElement.style.setProperty("--dock-w", "0px"); }, []);

  const selId = params.get("c");
  const select = useCallback((id: string | null) => {
    if (location.pathname !== `/w/${wid}`) {
      navigate(id ? `/w/${wid}?c=${encodeURIComponent(id)}` : `/w/${wid}`);
      return;
    }
    setParams((p) => { const n = new URLSearchParams(p); if (id) n.set("c", id); else n.delete("c"); return n; }, { replace: true });
  }, [setParams, location.pathname, navigate, wid]);
  const openAI = useCallback((req?: AIRequest) => { setAiReq(req ? { ...req } : null); setDockTab("ai"); setDockOpen(true); }, []);
  const openTest = useCallback(() => { setTestStarted(true); setDockTab("test"); setDockOpen(true); }, []);
  const toggle = (t: DockTab) => {
    if (dockOpen && dockTab === t) setDockOpen(false);
    else if (t === "test") openTest();
    else { setDockTab("ai"); setDockOpen(true); }
  };

  if (isLoading) return <div className="empty"><Loader2 className="spin" /></div>;
  if (error || !ws) return <div className="empty">Couldn't load workspace: {(error as Error)?.message}</div>;
  const selected = ws.components.find((c) => c.id === selId) ?? null;
  const version = contentVersion(ws);
  const onApplied = (paths: string[]) => {
    const first = paths.find((p) => p.startsWith("topics/")) ?? paths[0];
    if (first && !selected) select(first.startsWith("workflows/") ? first.split("/").slice(0, 2).join("/") : first);
  };

  return (
    <WsCtx.Provider value={{ ws, version, selected, select, openAI, marks, setMarks, openTest, pending, setPending, openReview: setReview }}>
      <div className={`ws-header${dockOpen ? " with-dock" : ""}`}>
        <div className="grow">
          <div className="row"><h1 style={{ fontSize: 18 }}>{ws.info.name}</h1>
            {ws.connected ? <span className="pill-sm ok"><Cloud size={11} />cloned from Copilot Studio</span>
              : <span className="pill-sm"><CloudOff size={11} />local only</span>}
            <span className="pill-sm">{ws.components.length} components</span>
            {pending && <button className="pill-sm accent clickable" style={{ border: "none" }} onClick={() => setReview(pending)}>
              <Sparkles size={11} />{pending.files.length} AI change(s) to review</button>}
          </div>
          <div className="tiny faint row ellipsis" title={ws.info.path}><FolderOpen size={11} />{ws.info.path}</div>
        </div>
        <div className="row">
          <span className="tiny faint kbd-hint">⌘K</span>
          <button className={`btn ${dockOpen && dockTab === "test" ? "btn-primary" : "btn-secondary"}`} onClick={() => toggle("test")}
                  title="Chat with this agent in the local simulator"><FlaskConical size={16} />Test</button>
          <button className={`btn ${dockOpen && dockTab === "ai" ? "btn-primary" : "btn-secondary"}`} onClick={() => toggle("ai")}>
            <Sparkles size={16} />AI Builder</button>
        </div>
      </div>
      <div className={`ws-body${dockOpen ? " with-dock" : ""}`}><Outlet /></div>
      <RightDock open={dockOpen} tab={dockTab} onTab={(t) => (t === "test" ? openTest() : setDockTab(t))} onClose={() => setDockOpen(false)}
                 width={dockW} onWidth={setDockW}
                 ai={<AIPanel wid={ws.info.id} request={aiReq} onApplied={onApplied}
                              selected={selected ? { id: selected.id, name: selected.displayName } : null} />}
                 test={testStarted ? <TestPane open={dockOpen && dockTab === "test"} /> : null} />
      {review && <ReviewChanges p={review} onClose={() => setReview(null)} onApplied={onApplied} />}
      <CommandPalette />
    </WsCtx.Provider>
  );
}

function contentVersion(ws: Workspace): string {
  let h = 0;
  for (const c of ws.components) {
    const s = c.id + c.text;
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  }
  return `${ws.components.length}:${h}`;
}
