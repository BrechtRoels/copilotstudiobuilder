"""Build the system prompt for a generation request from the knowledge pack."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from .. import config, knowledge as kn
from ..workspace import store
from ..workspace.model import Workspace

Mode = Literal["agent", "topic", "flow", "childAgent", "modify", "fix", "ask"]

OUTPUT_CONTRACT = """\
## Output contract
First write a short visible plan for the maker: 2-6 lines, each starting with `PLAN:` (what you will create or
change and why, in plain language). Then reply with ONE JSON object and nothing after it:
{
  "summary": "1-3 sentences: what you built/changed and why",
  "notes": ["manual follow-ups for the maker, e.g. connections to create, things to verify"],
  "files": [ {"path": "topics/CreateTicket.mcs.yml", "content": "<full file content>"} ]
}
Rules:
- `content` is the COMPLETE file, never a fragment or diff. Escape newlines properly in JSON.
- Paths are relative to the agent root. Use the folder layout below. Never invent other folders.
- Only include files you create or change. To delete a file use "content": null.
- Every node needs a unique id: `<camelCaseKind>_<6 random alphanumerics>` (e.g. `sendActivity_k2Jd9Q`).
  Never leave `_REPLACE` ids or `<placeholder>` values unless the maker must fill them in (then say so in notes).
- Topic files start with a `# Name: <Display Name>` comment.
- Only use `kind:` values from the valid-kinds list. Only use supported Power Fx functions.
- If something can't be done in YAML (e.g. needs the Copilot Studio UI or a connection), say so in notes.
  Anything the schema can express (file analysis, web browsing, model knowledge, ...) goes in the files, not in notes.
- When existing files are shown below, build on them: keep what the request doesn't touch, and return only the files
  you change. Delete files that become obsolete (a flow is the whole `workflows/<Name>/` folder) and update every
  instruction, description and redirect that still names them.
- `notes` are only things the maker must do or decide outside these files. Don't ask them to verify property
  names: the files are checked against the schema automatically.

## Folder layout (Copilot Studio VS Code extension format)
agent.mcs.yml (GptComponentMetadata) · topics/<Name>.mcs.yml (AdaptiveDialog) · actions/<Name>.mcs.yml (TaskDialog)
knowledge/<Name>.mcs.yml (KnowledgeSourceConfiguration) · variables/<Name>.mcs.yml (GlobalVariableComponent)
agents/<ChildName>/agent.mcs.yml (AgentDialog) · workflows/<FlowName>/metadata.yaml + workflow.json
"""

ASK_CONTRACT = """\
## Output contract
Reply with ONE JSON object: {"summary": "<your answer in markdown>", "notes": [], "files": []}.
Answer the maker's question about Copilot Studio or this agent. Include YAML snippets inside the markdown when useful.
"""

ROLE = """\
You are a senior Microsoft Copilot Studio architect embedded in "Copilot Studio Builder".
You author agents as code: YAML files in the Copilot Studio VS Code extension format, plus Power Automate agent flows.
You know generative orchestration, child/connected agents, topics, Power Fx, adaptive cards, knowledge and tools well.
Prefer generative orchestration designs: good modelDescription/description text, tools with clear inputs,
and topics only where deterministic flow is needed. Follow Microsoft's reference below exactly.
"""

_MODE_GUIDES: dict[str, list[str]] = {
    "agent": ["edit-agent", "instructions-guide", "add-other-agents", "new-topic", "add-node", "agent-capabilities"],
    "topic": ["new-topic", "add-node"],
    "flow": ["agent-flows"],
    "childAgent": ["add-other-agents", "instructions-guide", "agent-capabilities"],
    "modify": ["add-node"],
    "fix": ["add-node"],
    "ask": [],
}

_MODE_TEMPLATES: dict[str, list[str]] = {
    "agent": ["agents/agent.mcs.yml", "agents/child-agent.mcs.yml", "topics/greeting.topic.mcs.yml",
              "topics/error-handler.topic.mcs.yml"],
    "childAgent": ["agents/child-agent.mcs.yml"],
    "topic": ["topics/question-topic.topic.mcs.yml"],
}

# keyword -> guides / templates pulled in on demand
_KEYWORDS: list[tuple[str, list[str], list[str]]] = [
    (r"adaptive card|\bcard", ["add-adaptive-card", "card-templates"], []),
    (r"knowledge|generative answer|search|sharepoint|website|document|rag",
     ["add-generative-answers", "add-knowledge"], ["topics/search-topic.topic.mcs.yml"]),
    (r"global variable|\bglobal\b", ["add-global-variable"], ["variables/global-variable.variable.mcs.yml"]),
    (r"trigger|event", ["edit-triggers"], []),
    (r"connector|tool|action|outlook|teams|dataverse|mcp", ["add-action"],
     ["actions/connector-action.mcs.yml", "actions/mcp-action.mcs.yml"]),
    (r"sharepoint list|list item", ["sharepoint-actions"], []),
    (r"flow|power automate|automate|workflow", ["agent-flows"], []),
    (r"child agent|sub-?agent|connected agent|multi-?agent|orchestrat", ["add-other-agents"],
     ["agents/child-agent.mcs.yml"]),
    (r"fallback|unknown", [], ["topics/fallback.topic.mcs.yml"]),
    (r"error", [], ["topics/error-handler.topic.mcs.yml"]),
    (r"sign.?in|auth|login", [], ["topics/auth-topic.topic.mcs.yml"]),
    (r"disambiguat|multiple topics", [], ["topics/disambiguation.topic.mcs.yml"]),
    (r"greet|welcome|conversation start", [], ["topics/greeting.topic.mcs.yml"]),
    (r"citation", [], ["topics/remove-citations.topic.mcs.yml"]),
    (r"upload|attach|\bfiles?\b|document|\bpdf|sop\b|spreadsheet|excel|internet|\bweb\b|online|brows|research|"
     r"general knowledge|only (use|from)|code interpreter|image", ["agent-capabilities"], []),
]

# Schema definitions sent up front, so the model writes exact property names instead of guessing
# (the same lookup the repair round uses). Kinds found in the chosen templates and the workspace are added.
_BASE_KINDS = ["AdaptiveDialog", "OnRecognizedIntent", "SendActivity", "Question", "ConditionGroup", "ConditionItem",
               "SetVariable", "BeginDialog", "EndDialog", "GotoAction", "AnswerQuestionWithAI",
               "SearchAndSummarizeContent"]
_MODE_KINDS: dict[str, list[str]] = {
    "agent": ["GptComponentMetadata", "AgentDialog", "OnToolSelected", "TaskDialog", "InvokeFlowTaskAction",
              "AutomaticTaskInput", "GlobalVariableComponent", "KnowledgeSourceConfiguration", *_BASE_KINDS],
    "topic": _BASE_KINDS,
    "modify": _BASE_KINDS,
    "fix": [],
    "flow": ["TaskDialog", "InvokeFlowTaskAction", "AutomaticTaskInput"],
    "childAgent": ["AgentDialog", "OnToolSelected", "GptComponentMetadata"],
    "ask": [],
}
_KIND_RE = re.compile(r"^\s*-?\s*kind:\s*([A-Z][A-Za-z]+)\s*$", re.M)
_MAX_KINDS = 40


def suggest_patterns(text: str, limit: int = 2) -> list[str]:
    words = set(re.findall(r"[a-z0-9]+", text.lower()))
    scored = []
    for pid, p in kn.patterns().items():
        vocab = set(re.findall(r"[a-z0-9]+", " ".join([p.name, p.description, *p.tags]).lower()))
        score = len(words & vocab - {"the", "and", "to", "a", "of", "for", "with", "agent", "topic", "user"})
        if score >= 3:
            scored.append((score, pid))
    return [pid for _, pid in sorted(scored, reverse=True)[:limit]]


_CONTENT_ORDER = ["agent", "settings", "topic", "childAgent", "action", "workflow", "variable", "knowledge"]


def file_texts(ws: Workspace) -> list[tuple[str, str]]:
    """(path, text) for every workspace file, most important first. A flow contributes both of its files."""
    order = {c: i for i, c in enumerate(_CONTENT_ORDER)}
    out = []
    for c in sorted(ws.components, key=lambda c: order.get(c.category, len(order))):
        if c.category == "workflow":
            folder = f"workflows/{c.name}"
            meta = next((f"{folder}/{n}" for n in ("metadata.yaml", "metadata.yml")
                         if (Path(ws.info.path) / folder / n).exists()), None)
            if meta:
                out.append((meta, store.read_file(ws.info, meta)))
            out.append((f"{folder}/workflow.json", c.text))
        else:
            out.append((c.path, c.text))
    return out


def workspace_summary(ws: Workspace, exclude: str | None = None, budget: int | None = None,
                      max_files: int = 120) -> str:
    """The workspace as the model sees it: settings, the full text of existing files (within a budget)
    and the remaining paths. Without the contents, follow-up requests rewrite files blind."""
    budget = config.AI_CONTEXT_CHARS if budget is None else budget
    lines = [f"## Current workspace: {ws.info.name}",
             f"Orchestration: {'generative' if ws.generative_orchestration() else 'classic'}"
             f" · connected to Copilot Studio: {'yes' if ws.connected else 'no'}"]
    files = file_texts(ws)
    if files:
        lines.append("These files exist now. Edit them rather than starting over; to delete one, return it with "
                     "\"content\": null.")
    listed: list[str] = []
    for path, text in files[:max_files]:
        if exclude and (path == exclude or path.startswith(exclude.rstrip("/") + "/")):
            lines.append(f"### {path}\n(shown in full under Target)")
        elif len(text) <= budget:
            budget -= len(text)
            fence = "json" if path.endswith(".json") else "yaml"
            lines.append(f"### {path}\n```{fence}\n{text.rstrip()}\n```")
        else:
            listed.append(path)
    if listed:
        lines.append("Other files (content not shown; ask for the target if you need to change one):\n"
                     + "\n".join(f"- {p}" for p in listed))
    # The agent schema name is the prefix of topic references (BeginDialog dialog: <schema>.topic.<Name>).
    schema = ws.settings.get("schemaName")
    refs = re.findall(r"dialog:\s*(\S+\.topic\.\S+)", "\n".join(c.text for c in ws.components))
    if schema:
        lines.append(f"Agent schemaName: {schema} → reference topics as `{schema}.topic.<FileName>`")
    elif refs:
        lines.append(f"Existing topic references look like: {refs[0]} (reuse this prefix for BeginDialog)")
    else:
        lines.append("No existing topic references; for BeginDialog use `<agentSchemaName>.topic.<FileName>` "
                     "and note that the maker must check the prefix.")
    wfs = [(c.name, ((c.data or {}).get("metadata") or {}).get("workflowId")) for c in ws.by_category("workflow")]
    if wfs:
        lines.append("Existing flows (name → workflowId): " + ", ".join(f"{n} → {i}" for n, i in wfs))
    return "\n".join(lines)


def infer_mode(prompt: str, target: str | None, ws: Workspace) -> Mode:
    """Pick a mode from the request and the selected component (the user can override)."""
    low = prompt.lower().strip()
    comp = ws.get(target) if target else None
    if re.match(r"^(what|why|how|when|which|can|should|is|are|does|do|explain)\b", low) or low.endswith("?"):
        return "ask"
    change = re.search(r"\b(add|change|update|modify|rename|rewrite|fix|improve|remove|delete|make|replace|extend|"
                       r"translate|shorten|refactor)\b", low)
    new_thing = re.search(r"\b(new|another|an?) (topic|agent|flow|child agent|tool)\b", low)
    if comp is not None and change and not new_thing:
        return "modify"
    # A change request on an existing agent iterates on it (sees every file) instead of re-designing it.
    if ws.agent is not None and (change or re.search(r"\b(should|instead|rather|no[, ]|not)\b", low)) and not new_thing:
        return "modify"
    if re.search(r"orchestrat|\b(an?|new|whole|full|complete) (agent|assistant|copilot|bot)\b|assistant (that|for|which)", low):
        return "agent"
    if re.search(r"child agent|sub-?agent|specialist|connected agent", low):
        return "childAgent"
    if re.search(r"\b(flow|power automate|workflow)\b", low) and not re.search(r"\btopic\b", low):
        return "flow"
    if re.search(r"\btopic\b", low):
        return "topic"
    return "topic" if ws.agent is not None else "agent"


def schema_kinds(mode: Mode, texts: list[str]) -> list[str]:
    """Kinds whose schema goes into the prompt: the mode's usual kinds plus those in templates and workspace."""
    found = [k for t in texts for k in _KIND_RE.findall(t)]
    valid = set(kn.node_kinds())
    return [k for k in dict.fromkeys([*_MODE_KINDS.get(mode, []), *found]) if k in valid][:_MAX_KINDS]


def build_system(mode: Mode, prompt: str, ws: Workspace, patterns: list[str] | None = None) -> tuple[str, list[str]]:
    """Return (system prompt, pattern ids used)."""
    system, pats, _ = build_system_ex(mode, prompt, ws, patterns)
    return system, pats


def build_system_ex(mode: Mode, prompt: str, ws: Workspace, patterns: list[str] | None = None,
                    target: str | None = None) -> tuple[str, list[str], dict]:
    """Return (system prompt, pattern ids, sources {guides, templates, patterns, schemas} shown in the UI)."""
    # Modifying the agent as a whole (no target file) needs the agent-level guides and kinds.
    scope: Mode = "agent" if mode == "modify" and not target else mode
    guides = list(_MODE_GUIDES.get(scope, []))
    tmpls = list(_MODE_TEMPLATES.get(mode, []))
    low = prompt.lower()
    for rx, g, t in _KEYWORDS:
        if re.search(rx, low):
            guides += g
            tmpls += t
    pats = list(dict.fromkeys((patterns or []) + suggest_patterns(prompt)))

    parts = [ROLE, ASK_CONTRACT if mode == "ask" else OUTPUT_CONTRACT,
             "## Microsoft Copilot Studio YAML reference\n" + kn.guide("int-reference")]
    for g in dict.fromkeys(guides):
        body = kn.guide(g)
        if body:
            parts.append(f"## Guide: {g}\n{body}")
    all_t = kn.templates()
    for t in dict.fromkeys(tmpls):
        if t in all_t:
            parts.append(f"## Template: templates/{t}\n```yaml\n{all_t[t]}\n```")
    for pid in pats:
        p = kn.patterns().get(pid)
        if p:
            parts.append(f"## Pattern: {p.name} ({p.status})\n{p.description}\n{p.body}")
    parts.append("## Valid `kind:` values\n" + ", ".join(kn.node_kinds()))
    kinds = schema_kinds(scope, [all_t[t] for t in tmpls if t in all_t] + [c.text for c in ws.components])
    if kinds:
        parts.append("## Schema of the kinds you will most likely use\nExact property names and types; "
                     "`{...}` lists the keys of a nested object. Use no other properties.\n```\n"
                     + "\n".join(kn.resolve_kind(k, expand=True) for k in kinds) + "\n```")
    if mode != "ask" or ws.components:
        parts.append(workspace_summary(ws, exclude=target))
    sources = {"guides": ["int-reference", *[g for g in dict.fromkeys(guides) if kn.guide(g)]],
               "templates": [t for t in dict.fromkeys(tmpls) if t in all_t],
               "patterns": [kn.patterns()[p].name for p in pats if p in kn.patterns()],
               "schemas": kinds}
    return "\n\n".join(parts), pats, sources
