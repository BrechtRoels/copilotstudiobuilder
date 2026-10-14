// Typed client for the FastAPI backend (mirrors backend Pydantic models).

export type Category =
  | "agent" | "settings" | "connectionReferences" | "topic" | "action" | "knowledge"
  | "variable" | "trigger" | "childAgent" | "workflow" | "other";

export interface Component {
  id: string; path: string; category: Category; kind: string | null; name: string;
  displayName: string; owner: string | null; data: unknown; text: string; error: string | null;
}
export interface WorkspaceInfo {
  id: string; name: string; path: string; origin: "generated" | "cloned" | "solution" | "empty";
  createdAt: string; description: string;
}
export interface Workspace { info: WorkspaceInfo; components: Component[]; connected: boolean }

export type Severity = "error" | "warning" | "info";
export interface Issue { severity: Severity; message: string; path: string; rule: string }
export interface FileReport { path: string; kind: string | null; valid: boolean; issues: Issue[] }
export interface Finding { severity: Severity; rule: string; component: string; message: string; pointer: string; fixable: boolean }

export interface GNode { id: string; kind: string; label: string; detail: string; pointer: string; variant: string; status: string | null; meta?: Record<string, any> }
export interface GEdge { id: string; source: string; target: string; label: string; variant: string }
export interface Graph { nodes: GNode[]; edges: GEdge[] }

export type Mode = "auto" | "agent" | "topic" | "flow" | "childAgent" | "modify" | "fix" | "ask";
export interface ChatTurn { role: "user" | "assistant"; content: string }
export interface GenerateRequest { mode: Mode; prompt: string; target?: string | null; findings?: string[]; patterns?: string[]; history?: ChatTurn[] }
export interface ProposedFile { path: string; content: string | null; before: string | null; isNew: boolean; diff: string; report: FileReport | null; graph: Graph | null }
export interface Requirement { requirement: string; status: "covered" | "partial" | "missing"; where: string; comment: string }
export interface Proposal {
  id: string; workspaceId: string; mode: Mode; prompt: string; summary: string; notes: string[];
  files: ProposedFile[]; findings: Finding[]; coverage?: Requirement[]; patterns: string[]; repairRounds: number; valid: boolean;
  applied: string[] | null;
}
export type AiEventType = "context" | "step" | "plan" | "file" | "writing" | "notice" | "parsed" | "validate"
  | "repair" | "repaired" | "analyze" | "coverage" | "proposal" | "error" | "done";
export interface AiEvent { type: AiEventType; data: Record<string, any> }
export interface AiTurn {
  role: "user" | "assistant"; text: string; ts?: number; mode?: Mode; target?: string | null;
  proposalId?: string; events?: AiEvent[]; error?: string; stopped?: boolean;
}
export interface AiHistory { turns: AiTurn[]; proposals: Record<string, Proposal> }
export interface Pattern { id: string; name: string; description: string; challenge: string; status: string; tags: string[] }
export interface Template { path: string; description: string; content: string }
export interface Health { ok: boolean; mock: boolean; model: string; hasKey: boolean }
export interface SyncStatus { available: boolean; reasons: string[]; connected: boolean; environmentId: string | null; agentId: string | null }

export interface SolutionIdentity { uniqueName: string; displayName: string; botSchema: string; botName: string; publisherUniqueName: string; publisherDisplayName: string; prefix: string }
export interface SolutionBase { present: boolean; identity?: SolutionIdentity; uniqueName?: string; displayName?: string; version?: string; managed?: boolean; bots?: { schema: string; name: string; components: number }[] }
export interface ExportItem { source: string; target: string; action: "update" | "add" | "skip"; note: string }
export interface ExportReport { bot: string; version: string; items: ExportItem[]; warnings: string[] }

export interface Diagram { id: string; title: string; mermaid: string }
export interface Doc {
  title: string; generatedAt: string; orchestration: string; instructions: string; starters: string[];
  overview: string; model: string; counts: Record<string, number>;
  childAgents: { id: string; name: string; kind: string; description: string; instructions: string; inputs: string[]; outputs: string[]; summary: string }[];
  topics: { id: string; name: string; owner: string | null; trigger: string; description: string; phrases: string[]; summary: string; variables: string[]; redirects: string[]; flows: string[]; nodeCount: number; diagram: string }[];
  tools: { id: string; name: string; type: string; target: string; description: string; inputs: string[]; summary: string }[];
  flows: { id: string; name: string; trigger: string; inputs: string[]; outputs: string[]; connectors: string[]; steps: string[]; errorHandling: boolean; summary: string; diagram: string }[];
  knowledge: { id: string; name: string; owner: string; type: string; detail: string }[];
  variables: { name: string; description: string; visibility: string }[];
  connections: string[]; findings: Finding[]; diagrams: Diagram[];
}

// --- test pane (local simulator) ---
export type SimEventType =
  | "message" | "step" | "topic" | "branch" | "routing" | "plan" | "agent" | "tool" | "flow" | "knowledge"
  | "variables" | "notice" | "warning" | "error" | "typing" | "done";
export interface SimEvent { seq: number; type: SimEventType; data: Record<string, any>; componentId: string | null; pointer: string | null; ts: number }
export interface TestSession { sessionId: string; orchestration: "generative" | "classic"; mock: boolean; events: SimEvent[] }
export interface TriggerField { name: string; key: string; type: string; description: string; required: boolean }
export interface ActionRun {
  name: string; pointer: string; type: string; status: string; inputs: unknown; outputs: unknown;
  error: { code?: string; message?: string } | null; ms: number; source: string | null; iterations: number | null; note: string;
}
export interface FlowRun {
  id: string; componentId: string; flowName: string; status: string; startedAt: string; durationMs: number;
  triggerInputs: Record<string, unknown>; actions: ActionRun[]; response: { statusCode: number; body: unknown } | null;
  outputs: Record<string, unknown>; error: { code?: string; message?: string } | null;
}
export interface ActionMock { mode: "mock" | "fail"; outputs?: unknown; error?: string; status?: "Failed" | "TimedOut" }
export interface ConnectorAction { name: string; type: string; api: string; operationId: string; hasSchema: boolean }
export interface FlowTestInfo { fields: TriggerField[]; runs: FlowRun[]; connectorActions: ConnectorAction[]; mocks: Record<string, ActionMock> }
export interface KnowledgeSource { id: string; name: string; displayName: string; owner: string | null; kind: string; site: string; description: string; content: string }

export interface TestExpect { topic?: string | null; tool?: string | null; contains?: string | null }
export interface TestCase { id?: string; name: string; turns: { user: string; expect: TestExpect }[]; createdAt?: string }
export interface CaseResult {
  id: string; name: string; passed: boolean;
  turns: { user: string; passed: boolean; checks: { kind: string; expected: string; actual: string; passed: boolean }[]; bot: string[]; errors: string[] }[];
}

export interface CheckItem { id: string; label: string; state: "ok" | "warn" | "fail"; detail: string; link: string }
export interface Overview {
  counts: Record<string, number>; orchestration: string;
  findings: { errors: number; warnings: number; infos: number; top: Finding[] };
  tests: { cases: number; ran: number; passed: number; ts: string | null };
  recent: { ts: number; summary: string; files: string[]; mode: string }[];
  checklist: CheckItem[]; ready: boolean;
}

/** POST that answers with server-sent events; calls onEvent for every `data:` line. */
async function stream<E = SimEvent>(url: string, body: unknown, onEvent: (e: E) => void, signal?: AbortSignal): Promise<void> {
  const res = await fetch(`/api${url}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal });
  if (!res.ok || !res.body) {
    let msg = `${res.status} ${res.statusText}`;
    try { const j = await res.json(); if (j.detail) msg = String(j.detail); } catch { /* not json */ }
    throw new Error(msg);
  }
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i: number;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, i);
      buf = buf.slice(i + 2);
      for (const line of chunk.split("\n")) if (line.startsWith("data: ")) onEvent(JSON.parse(line.slice(6)));
    }
  }
}

async function req<T>(method: string, url: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, headers: {} };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    init.body = JSON.stringify(body);
    (init.headers as Record<string, string>)["Content-Type"] = "application/json";
  }
  const res = await fetch(`/api${url}`, init);
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try { const j = await res.json(); if (j.detail) msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch { /* not json */ }
    throw new Error(msg);
  }
  const ct = res.headers.get("content-type") || "";
  return (ct.includes("json") ? res.json() : res.blob()) as Promise<T>;
}

const q = encodeURIComponent;
export const api = {
  health: () => req<Health>("GET", "/health"),
  patterns: () => req<Pattern[]>("GET", "/knowledge/patterns"),
  pattern: (id: string) => req<{ id: string; name: string; body: string }>("GET", `/knowledge/patterns/${id}`),
  templates: () => req<Template[]>("GET", "/knowledge/templates"),

  workspaces: () => req<WorkspaceInfo[]>("GET", "/workspaces"),
  createWorkspace: (name: string, description = "") => req<WorkspaceInfo>("POST", "/workspaces", { name, description }),
  openWorkspace: (path: string, name?: string) => req<WorkspaceInfo>("POST", "/workspaces/open", { path, name }),
  importSolution: (file: File) => {
    const fd = new FormData(); fd.append("file", file);
    return req<{ workspace: WorkspaceInfo; notes: string[]; files: string[] }>("POST", "/workspaces/import", fd);
  },
  deleteWorkspace: (id: string, deleteFiles: boolean) => req("DELETE", `/workspaces/${id}?deleteFiles=${deleteFiles}`),
  workspace: (id: string) => req<Workspace>("GET", `/workspaces/${id}`),

  writeFile: (wid: string, path: string, content: string) => req<FileReport>("PUT", `/workspaces/${wid}/file`, { path, content }),
  deleteFile: (wid: string, path: string) => req("DELETE", `/workspaces/${wid}/file?path=${q(path)}`),
  validate: (path: string, content: string) => req<FileReport>("POST", "/validate", { path, content }),
  paste: (wid: string, content: string, overwrite = false) =>
    req<{ path: string; report: FileReport }>("POST", `/workspaces/${wid}/paste`, { content, overwrite }),

  agentGraph: (wid: string) => req<Graph>("GET", `/workspaces/${wid}/graph/agent`),
  componentGraph: (wid: string, id: string) => req<Graph>("GET", `/workspaces/${wid}/graph/component?id=${q(id)}`),
  node: (wid: string, component: string, pointer: string) =>
    req<{ text: string }>("GET", `/workspaces/${wid}/node?component=${q(component)}&pointer=${q(pointer)}`),
  saveNode: (wid: string, component: string, pointer: string, text: string) =>
    req<FileReport>("PUT", `/workspaces/${wid}/node`, { component, pointer, text }),

  setFields: (wid: string, component: string, pointer: string, fields: Record<string, unknown>) =>
    req<FileReport>("PATCH", `/workspaces/${wid}/fields`, { component, pointer, fields }),
  overview: (wid: string) => req<Overview>("GET", `/workspaces/${wid}/overview`),
  analyze: (wid: string) => req<Finding[]>("GET", `/workspaces/${wid}/analyze`),
  generate: (wid: string, body: GenerateRequest) => req<Proposal>("POST", `/workspaces/${wid}/ai/generate`, body),
  generateStream: (wid: string, body: GenerateRequest, onEvent: (e: AiEvent) => void, signal?: AbortSignal) =>
    stream<AiEvent>(`/workspaces/${wid}/ai/generate/stream`, body, onEvent, signal),
  aiHistory: (wid: string) => req<AiHistory>("GET", `/workspaces/${wid}/ai/history`),
  clearAiHistory: (wid: string) => req("DELETE", `/workspaces/${wid}/ai/history`),
  accept: (pid: string, paths?: string[]) => req<{ written: string[] }>("POST", `/proposals/${pid}/accept`, { paths }),

  docs: (wid: string) => req<Doc>("GET", `/workspaces/${wid}/docs`),
  refreshSummaries: (wid: string) => req<Record<string, string>>("POST", `/workspaces/${wid}/docs/summaries`),
  docx: (wid: string, images: Record<string, string>) => req<Blob>("POST", `/workspaces/${wid}/docs/docx`, { images }),
  htmlUrl: (wid: string) => `/api/workspaces/${wid}/docs/html?download=true`,
  zipUrl: (wid: string) => `/api/workspaces/${wid}/export/zip`,
  merge: (wid: string, target: string) => req<{ written: string[] }>("POST", `/workspaces/${wid}/export/merge`, { target }),
  solutionBase: (wid: string) => req<SolutionBase>("GET", `/workspaces/${wid}/solution/base`),
  uploadSolutionBase: (wid: string, file: File) => {
    const fd = new FormData(); fd.append("file", file);
    return req<SolutionBase>("POST", `/workspaces/${wid}/solution/base`, fd);
  },
  saveSolutionIdentity: (wid: string, body: SolutionIdentity) => req<SolutionIdentity>("PUT", `/workspaces/${wid}/solution/identity`, body),
  removeSolutionBase: (wid: string) => req("DELETE", `/workspaces/${wid}/solution/base`),
  solutionPlan: (wid: string, bot?: string) => req<ExportReport>("GET", `/workspaces/${wid}/solution/plan${bot ? `?bot=${q(bot)}` : ""}`),
  solutionExportUrl: (wid: string, bot?: string) => `/api/workspaces/${wid}/solution/export${bot ? `?bot=${q(bot)}` : ""}`,
  syncStatus: (wid: string) => req<SyncStatus>("GET", `/workspaces/${wid}/sync/status`),
  startTest: (wid: string, user?: Record<string, unknown>) => req<TestSession>("POST", `/workspaces/${wid}/test/sessions`, { user }),
  sendTest: (sid: string, body: { text?: string; card?: Record<string, unknown> }, onEvent: (e: SimEvent) => void) =>
    stream(`/test/sessions/${sid}/messages`, body, onEvent),
  endTest: (sid: string) => req("DELETE", `/test/sessions/${sid}`),
  flowTest: (wid: string, component: string) => req<FlowTestInfo>("GET", `/workspaces/${wid}/test/flows?component=${q(component)}`),
  runFlow: (wid: string, componentId: string, inputs: Record<string, unknown>, overrides: Record<string, ActionMock>) =>
    req<FlowRun>("POST", `/workspaces/${wid}/test/flows/run`, { componentId, inputs, overrides }),
  flowRun: (wid: string, rid: string) => req<FlowRun>("GET", `/workspaces/${wid}/test/runs/${rid}`),
  saveFlowMocks: (wid: string, componentId: string, mocks: Record<string, ActionMock>) =>
    req("PUT", `/workspaces/${wid}/test/flows/mocks`, { componentId, mocks }),
  testKnowledge: (wid: string) => req<KnowledgeSource[]>("GET", `/workspaces/${wid}/test/knowledge`),
  saveKnowledge: (wid: string, name: string, text: string) => req("PUT", `/workspaces/${wid}/test/knowledge/${q(name)}`, { text }),
  uploadKnowledge: (wid: string, name: string, file: File) => {
    const fd = new FormData(); fd.append("file", file);
    return req<{ ok: boolean; chars: number }>("POST", `/workspaces/${wid}/test/knowledge/${q(name)}/upload`, fd);
  },
  testCases: (wid: string) => req<TestCase[]>("GET", `/workspaces/${wid}/test/cases`),
  saveTestCases: (wid: string, cases: TestCase[]) => req<TestCase[]>("PUT", `/workspaces/${wid}/test/cases`, cases),
  runTestCases: (wid: string, ids?: string[]) => req<CaseResult[]>("POST", `/workspaces/${wid}/test/cases/run`, { ids }),
  sync: (wid: string, command: string) => req<{ exitCode: number; stdout: string; stderr: string; json?: unknown }>("POST", `/workspaces/${wid}/sync/${command}`),
};

export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
