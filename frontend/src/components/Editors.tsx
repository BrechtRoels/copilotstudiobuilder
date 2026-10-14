import { useEffect, useId, useRef, useState } from "react";
import MonacoEditor from "@monaco-editor/react";
import "./monacoSetup";
import mermaid from "mermaid";

function currentTheme() {
  return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}

export function useTheme() {
  const [theme, setTheme] = useState(currentTheme());
  useEffect(() => {
    const obs = new MutationObserver(() => setTheme(currentTheme()));
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => obs.disconnect();
  }, []);
  return theme;
}

export function CodeEditor({ value, onChange, language, readOnly, height = "100%" }: {
  value: string; onChange?: (v: string) => void; language: "yaml" | "json"; readOnly?: boolean; height?: string | number;
}) {
  const theme = useTheme();
  return (
    <MonacoEditor
      height={height} language={language} value={value} theme={theme === "light" ? "vs" : "vs-dark"}
      onChange={(v) => onChange?.(v ?? "")}
      options={{
        readOnly, minimap: { enabled: false }, fontSize: 12, tabSize: 2, scrollBeyondLastLine: false,
        wordWrap: "on", renderWhitespace: "boundary", automaticLayout: true,
      }}
    />
  );
}

export function DiffView({ diff }: { diff: string }) {
  if (!diff) return <div className="faint small">No changes.</div>;
  return (
    <div className="diff">
      {diff.split("\n").map((l, i) => {
        const cls = l.startsWith("@@") ? "hunk" : l.startsWith("+") && !l.startsWith("+++") ? "add"
          : l.startsWith("-") && !l.startsWith("---") ? "del" : "";
        return <div key={i} className={cls}>{l || " "}</div>;
      })}
    </div>
  );
}

let mermaidTheme = "";
export function Mermaid({ code, onSvg }: { code: string; onSvg?: (svg: string) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const id = useId().replace(/:/g, "");
  const theme = useTheme();
  useEffect(() => {
    let cancelled = false;
    if (mermaidTheme !== theme) {
      mermaid.initialize({
        startOnLoad: false, securityLevel: "strict", theme: "base", fontFamily: "Inter",
        // SVG text labels (no foreignObject) so diagrams can be rasterised for the .docx export.
        flowchart: { htmlLabels: false }, htmlLabels: false,
        themeVariables: theme === "light"
          ? { primaryColor: "#f7f5f2", primaryBorderColor: "#d04a02", primaryTextColor: "#2d2d2d", lineColor: "#8a8a8a" }
          : { primaryColor: "#2d2d2d", primaryBorderColor: "#d04a02", primaryTextColor: "#f2f2f2", lineColor: "#737373" },
      });
      mermaidTheme = theme;
    }
    mermaid.render(`m${id}`, code).then(({ svg }) => {
      if (!cancelled && ref.current) { ref.current.innerHTML = svg; onSvg?.(svg); }
    }).catch((e) => { if (ref.current) ref.current.textContent = `Diagram error: ${e}`; });
    return () => { cancelled = true; };
  }, [code, id, theme, onSvg]);
  return <div className="mermaid-box" ref={ref} />;
}

/** Rasterise an SVG string to a PNG data URL (for the .docx export). Light background for print. */
export async function svgToPng(svg: string, scale = 2): Promise<string> {
  const doc = new DOMParser().parseFromString(svg, "image/svg+xml");
  const el = doc.documentElement;
  const vb = el.getAttribute("viewBox")?.split(/\s+/).map(Number);
  const w = vb?.[2] || 800, h = vb?.[3] || 600;
  el.setAttribute("width", String(w)); el.setAttribute("height", String(h));
  const blob = new Blob([new XMLSerializer().serializeToString(el)], { type: "image/svg+xml" });
  const url = URL.createObjectURL(blob);
  try {
    const img = new Image();
    await new Promise<void>((res, rej) => { img.onload = () => res(); img.onerror = () => rej(new Error("svg load")); img.src = url; });
    const canvas = document.createElement("canvas");
    canvas.width = w * scale; canvas.height = h * scale;
    const ctx = canvas.getContext("2d")!;
    ctx.fillStyle = "#ffffff"; ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL("image/png");
  } finally {
    URL.revokeObjectURL(url);
  }
}
