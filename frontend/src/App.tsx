import { useState } from "react";
import { NavLink, Route, Routes, useMatch } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { LayoutDashboard,
  Blocks, BookOpenText, FolderKanban, Library, Moon, Network, Rocket, ShieldCheck, Sun, Hammer,
} from "lucide-react";
import { api } from "./api";
import Home from "./pages/Home";
import PatternsPage from "./pages/Patterns";
import WorkspaceLayout from "./pages/WorkspaceLayout";
import BuildPage from "./pages/Build";
import OverviewPage from "./pages/Overview";
import MapPage from "./pages/AgentMap";
import AnalyzerPage from "./pages/Analyzer";
import DocsPage from "./pages/Docs";
import DeployPage from "./pages/Deploy";

function ThemeToggle() {
  const [theme, setTheme] = useState(document.documentElement.dataset.theme === "light" ? "light" : "dark");
  const toggle = () => {
    const next = theme === "light" ? "dark" : "light";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("csb-theme", next); } catch { /* storage blocked */ }
    setTheme(next);
  };
  return (
    <button className="nav-link" onClick={toggle}>
      {theme === "light" ? <Moon size={18} /> : <Sun size={18} />}{theme === "light" ? "Dark theme" : "Light theme"}
    </button>
  );
}

function Sidebar() {
  const m = useMatch("/w/:wid/*");
  const wid = m?.params.wid;
  const { data: ws } = useQuery({ queryKey: ["ws", wid], queryFn: () => api.workspace(wid!), enabled: !!wid });
  const { data: health } = useQuery({ queryKey: ["health"], queryFn: api.health, staleTime: 60_000 });
  const link = ({ isActive }: { isActive: boolean }) => `nav-link${isActive ? " active" : ""}`;
  return (
    <nav className="sidebar">
      <div className="sidebar-brand">
        <div className="sidebar-logo-icon"><Blocks size={18} /></div>
        <div><div>Copilot Studio</div><div className="tiny faint" style={{ marginTop: -2 }}>Builder</div></div>
      </div>
      <NavLink to="/" end className={link}><FolderKanban size={18} />Workspaces</NavLink>
      <NavLink to="/patterns" className={link}><Library size={18} />Pattern library</NavLink>
      {wid && (
        <>
          <div className="sidebar-section ellipsis" title={ws?.info.name}>{ws?.info.name ?? "Agent"}</div>
          <NavLink to={`/w/${wid}/overview`} className={link}><LayoutDashboard size={18} />Overview</NavLink>
          <NavLink to={`/w/${wid}`} end className={link}><Hammer size={18} />Build</NavLink>
          <NavLink to={`/w/${wid}/map`} className={link}><Network size={18} />Agent map</NavLink>
          <NavLink to={`/w/${wid}/analyzer`} className={link}><ShieldCheck size={18} />Analyzer</NavLink>
          <NavLink to={`/w/${wid}/docs`} className={link}><BookOpenText size={18} />Documentation</NavLink>
          <NavLink to={`/w/${wid}/deploy`} className={link}><Rocket size={18} />Deploy</NavLink>
        </>
      )}
      <div className="sidebar-footer">
        {health && (
          <div className="tiny faint" style={{ padding: "0 12px 8px" }}>
            {health.mock ? <span className="pill-sm warning">mock AI</span>
              : health.hasKey ? <span className="pill-sm ok">{health.model}</span>
              : <span className="pill-sm error">no GENAI_API_KEY</span>}
          </div>
        )}
        <ThemeToggle />
      </div>
    </nav>
  );
}

export default function App() {
  return (
    <div className="app-shell">
      <Sidebar />
      <main className="main-content">
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/patterns" element={<PatternsPage />} />
          <Route path="/w/:wid" element={<WorkspaceLayout />}>
            <Route index element={<BuildPage />} />
            <Route path="overview" element={<OverviewPage />} />
            <Route path="map" element={<MapPage />} />
            <Route path="analyzer" element={<AnalyzerPage />} />
            <Route path="docs" element={<DocsPage />} />
            <Route path="deploy" element={<DeployPage />} />
          </Route>
        </Routes>
      </main>
    </div>
  );
}
