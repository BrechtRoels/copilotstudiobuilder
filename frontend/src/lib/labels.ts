// Human names for workspace paths ("Topic · Reset Password") — mirrors backend generator.file_label.
import type { Workspace } from "../api";

const stemOf = (p: string) => p.split("/").pop()!.replace(/\.(mcs\.)?ya?ml$|\.json$/, "");

export function fileLabel(path: string, content?: string | null, ws?: Workspace): string {
  const comp = ws?.components.find((c) => c.path === path || (path.startsWith(c.path + "/") && c.category === "workflow"));
  const parts = path.split("/");
  const stem = stemOf(path);
  if (path === "agent.mcs.yml") return "Agent";
  if (parts[0] === "topics") {
    const m = content?.match(/^#\s*Name:\s*(.+)$/m);
    return `Topic · ${m ? m[1].trim() : comp?.displayName ?? stem}`;
  }
  if (parts[0] === "actions") return `Tool · ${comp?.displayName ?? stem}`;
  if (parts[0] === "agents" && parts.length > 2) return parts[2].startsWith("agent.mcs") ? `Child agent · ${parts[1]}` : `${parts[1]} · ${stem}`;
  if (parts[0] === "workflows" && parts.length > 2) return `Flow · ${comp?.displayName ?? parts[1]}${parts[2] === "workflow.json" ? "" : " (metadata)"}`;
  if (parts[0] === "workflows") return `Flow · ${comp?.displayName ?? parts[1] ?? stem}`;
  const kind: Record<string, string> = { knowledge: "Knowledge", variables: "Variable", trigger: "Event trigger" };
  if (path.startsWith("settings.mcs")) return "Settings";
  if (path.startsWith("connectionreferences")) return "Connection references";
  return `${kind[parts[0]] ?? "File"} · ${comp?.displayName ?? stem}`;
}

/** Component the path belongs to (workflows are one component per folder). */
export function componentIdFor(path: string): string {
  return path.startsWith("workflows/") ? path.split("/").slice(0, 2).join("/") : path;
}
