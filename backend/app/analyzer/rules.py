"""Workspace-wide analysis: per-file validation + cross-file rules."""
from __future__ import annotations

import re
from itertools import combinations
from typing import Literal

from pydantic import BaseModel

from ..graph.refs import dialog_refs, flow_refs, resolve_dialog, workflow_ids
from ..validation.validator import flow_definition, iter_flow_actions, validate_text
from ..workspace.model import Workspace

Severity = Literal["error", "warning", "info"]


class Finding(BaseModel):
    severity: Severity
    rule: str
    component: str          # component id
    message: str
    pointer: str = ""
    fixable: bool = True    # offer the "AI fix" button


_VAR_RE = re.compile(r"\bTopic\.([A-Za-z_][A-Za-z0-9_]*)")


def _topic_variables(data) -> tuple[set[str], set[str]]:
    """(assigned, referenced) Topic.* variable names."""
    assigned, used = set(), set()
    if isinstance(data, dict):
        for section in ("inputType", "outputType"):
            assigned |= set(((data.get(section) or {}).get("properties") or {}).keys())

    def walk(node, key=None):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, k)
        elif isinstance(node, list):
            for v in node:
                walk(v, key)
        elif isinstance(node, str):
            names = _VAR_RE.findall(node)
            # `variable:` and output bindings assign; everything else reads.
            if key in ("variable",) or (node.startswith(("Topic.", "init:Topic.")) and key not in ("value", "condition")):
                assigned.update(names)
            else:
                used.update(names)

    walk(data)
    return assigned, used


def _phrases(data) -> list[str]:
    bd = (data or {}).get("beginDialog") if isinstance(data, dict) else None
    intent = (bd or {}).get("intent") if isinstance(bd, dict) else None
    return [str(q) for q in ((intent or {}).get("triggerQueries") or [])] if isinstance(intent, dict) else []


def _tokens(s: str) -> frozenset[str]:
    return frozenset(w for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) > 2)


def analyze(ws: Workspace) -> list[Finding]:
    out: list[Finding] = []
    gen = ws.generative_orchestration()

    # 1. per-file validation
    for c in ws.components:
        if c.error:
            out.append(Finding(severity="error", rule="parse", component=c.id, message=c.error))
            continue
        if c.category in ("settings", "connectionReferences"):
            continue
        rep = validate_text(c.path, c.text)
        for i in rep.issues:
            out.append(Finding(severity=i.severity, rule=i.rule, component=c.id, message=i.message, pointer=i.path))

    wf_ids = workflow_ids(ws)
    for c in ws.components:
        if c.error or not isinstance(c.data, dict):
            continue
        # 2. references
        for kind, ref in dialog_refs(c):
            if resolve_dialog(ws, ref) is None:
                out.append(Finding(severity="error", rule="broken-redirect", component=c.id,
                                   message=f"{kind} targets '{ref}', which isn't in this workspace"))
        for fid in flow_refs(c):
            if fid.lower() not in wf_ids and not fid.startswith("<"):
                out.append(Finding(severity="warning", rule="missing-flow", component=c.id, fixable=False,
                                   message=f"Flow '{fid}' isn't in workflows/ (it may exist only in the environment)"))

        # 3. topic-level rules
        if c.category == "topic":
            bd = c.data.get("beginDialog") or {}
            trig = bd.get("kind") if isinstance(bd, dict) else None
            if trig == "OnRecognizedIntent":
                if gen and not c.data.get("modelDescription"):
                    out.append(Finding(severity="warning", rule="model-description", component=c.id,
                                       message="No modelDescription: generative orchestration routes on it, so this topic may never be selected"))
                if not gen and len(_phrases(c.data)) < 3:
                    out.append(Finding(severity="info", rule="few-phrases", component=c.id,
                                       message="Classic orchestration works best with 5-10 trigger phrases"))
            assigned, used = _topic_variables(c.data)
            for v in sorted(used - assigned):
                out.append(Finding(severity="warning", rule="undefined-variable", component=c.id,
                                   message=f"Topic.{v} is read but never assigned in this topic"))
            for v in sorted(assigned - used):
                out.append(Finding(severity="info", rule="unused-variable", component=c.id, fixable=False,
                                   message=f"Topic.{v} is assigned but never used"))
        if c.category == "action" and not c.data.get("modelDescription"):
            out.append(Finding(severity="warning", rule="model-description", component=c.id,
                               message="Tool has no modelDescription; the orchestrator can't tell when to use it"))

    # 4. trigger overlap between topics
    topics = [(c, [(_tokens(p), p) for p in _phrases(c.data)]) for c in ws.by_category("topic")]
    for (a, pa), (b, pb) in combinations(topics, 2):
        for ta, sa in pa:
            hit = next((sb for tb, sb in pb if ta and tb and len(ta & tb) / len(ta | tb) >= 0.8), None)
            if hit:
                out.append(Finding(severity="warning", rule="trigger-overlap", component=a.id,
                                   message=f"Trigger '{sa}' overlaps with '{hit}' in {b.displayName}"))
                break

    # 5. flows
    for c in ws.by_category("workflow"):
        wf = (c.data or {}).get("workflow")
        d = flow_definition(wf)
        if not d:
            continue
        acts = list(iter_flow_actions(d.get("actions") or {}))
        has_error_path = any(any(set(s or []) & {"Failed", "TimedOut"} for s in (a.get("runAfter") or {}).values())
                             for _, a, _ in acts)
        if acts and not has_error_path:
            out.append(Finding(severity="warning", rule="flow-error-handling", component=c.id,
                               message="No action runs after Failed/TimedOut: the agent gets no answer when the flow fails"))
        skills_trigger = any((t or {}).get("kind") == "Skills" for t in (d.get("triggers") or {}).values())
        if skills_trigger and not any(a.get("type") == "Response" for _, a, _ in acts):
            out.append(Finding(severity="error", rule="flow-response", component=c.id,
                               message="Agent-called flow never responds to the agent"))

    out += _capability_findings(ws)
    out += _global_variable_findings(ws)
    out += _stale_name_findings(ws)
    return out


# --- capabilities the instructions rely on but the configuration doesn't switch on ---------------
_UPLOAD_RE = re.compile(r"\b(upload(s|ed|ing)?|attach(ed|es|ment|ments)?)\b", re.I)
_WEB_RE = re.compile(r"\b(internet|web ?search|search(es|ing)? the web|brows(e|ing) the web|online research|"
                     r"research(es|ing)? online)\b", re.I)
_NO_MODEL_KNOWLEDGE_RE = re.compile(r"(never|do not|don't|must not) (use|rely on) (your |any )?general knowledge", re.I)
_AI_NODE_KINDS = {"AnswerQuestionWithAI", "SearchAndSummarizeContent"}


def _d(x) -> dict:
    return x if isinstance(x, dict) else {}


def _ai_settings(ws: Workspace) -> dict:
    """Agent-wide AI settings: agent.mcs.yml `aISettings`, overridden by settings.mcs.yml `configuration.aISettings`
    (the one that becomes the bot's configuration.json)."""
    agent = _d(ws.agent.data) if ws.agent else {}
    return {**_d(agent.get("aISettings")), **_d(_d(ws.settings.get("configuration")).get("aISettings"))}


def _instructions(c) -> str:
    data = _d(c.data)
    if c.category == "agent":
        return str(data.get("instructions") or "")
    if c.category == "childAgent":
        return str(_d(data.get("settings")).get("instructions") or "")
    return ""


def _web_enabled(ws: Workspace, c) -> bool:
    caps = _d(c.data).get("gptCapabilities") if c.category == "agent" else _d(_d(c.data).get("settings")).get("gptCapabilities")
    if _d(caps).get("webBrowsing") is True:
        return True
    if any("PublicSiteSearchSource" in k.text or "BingCustomSearch" in k.text for k in ws.by_category("knowledge")):
        return True
    return any(re.search(r"^\s*webBrowsing:\s*true\s*$", t.text, re.M) for t in ws.components if t.category == "topic")


def _ai_nodes(data, path: tuple = ()):
    if isinstance(data, dict):
        if data.get("kind") in _AI_NODE_KINDS:
            yield "/".join(map(str, path)), data
        for k, v in data.items():
            yield from _ai_nodes(v, path + (k,))
    elif isinstance(data, list):
        for i, v in enumerate(data):
            yield from _ai_nodes(v, path + (i,))


def _capability_findings(ws: Workspace) -> list[Finding]:
    out: list[Finding] = []
    agents = [c for c in ws.components if c.category in ("agent", "childAgent") and not c.error]
    settings = _ai_settings(ws)
    uploads = any(_UPLOAD_RE.search(_instructions(c)) for c in agents)
    if uploads and settings.get("isFileAnalysisEnabled") is not True and ws.agent:
        out.append(Finding(severity="warning", rule="capability-file-analysis", component=ws.agent.id,
                           message="The instructions expect uploaded documents, but file analysis isn't switched on "
                                   "(settings.mcs.yml → configuration.aISettings.isFileAnalysisEnabled: true)"))
    if uploads:
        for c in ws.by_category("topic"):
            for ptr, node in _ai_nodes(c.data):
                if node.get("isFileAnalysisEnabled") is not True:
                    out.append(Finding(severity="warning", rule="capability-file-analysis", component=c.id, pointer=ptr,
                                       message=f"{node.get('kind')} '{node.get('id', '')}' can't read uploaded documents "
                                               "without isFileAnalysisEnabled: true"))
    for c in agents:
        text = _instructions(c)
        if _WEB_RE.search(text) and not _web_enabled(ws, c):
            out.append(Finding(severity="warning", rule="capability-web", component=c.id,
                               message="The instructions rely on web search, but web browsing is off and there's no "
                                       "public website knowledge source (gptCapabilities.webBrowsing or "
                                       "PublicSiteSearchSource)"))
        if c.category == "agent" and _NO_MODEL_KNOWLEDGE_RE.search(text) and settings.get("useModelKnowledge") is not False:
            out.append(Finding(severity="info", rule="capability-model-knowledge", component=c.id,
                               message="The instructions forbid general knowledge, but it isn't switched off "
                                       "(settings.mcs.yml → configuration.aISettings.useModelKnowledge: false)"))
    return out


# --- global variables ------------------------------------------------------------------------------
def _global_variable_findings(ws: Workspace) -> list[Finding]:
    out: list[Finding] = []
    for v in ws.by_category("variable"):
        data = _d(v.data)
        inner = _d(data.get("variable"))
        name = str(inner.get("name") or data.get("name") or v.name)
        visibility = inner.get("aIVisibility") or data.get("aIVisibility")
        ref = re.compile(rf"\bGlobal\.{re.escape(name)}\b")
        assign = re.compile(rf"^\s*-?\s*variable:\s*(init:)?Global\.{re.escape(name)}\b")
        writes = reads = 0
        for c in ws.components:
            if c.id == v.id:
                continue
            for line in c.text.splitlines():
                n = len(ref.findall(line))
                if n and assign.match(line):
                    writes += 1
                    n -= 1
                reads += n
        if not writes and not reads:
            out.append(Finding(severity="warning", rule="unused-global", component=v.id,
                               message=f"Global.{name} is never set or read by any topic"))
        elif not reads and visibility != "UseInAIContext":
            out.append(Finding(severity="info", rule="unused-global", component=v.id, fixable=False,
                               message=f"Global.{name} is set but never read (and it is hidden from the AI)"))
    return out


# --- instructions and descriptions that name components which don't exist --------------------------
_NAMED_RE = re.compile(r"[\"“'‘]([^\"”'’\n]{3,60})[\"”'’]\s+(topic|tool)\b"
                       r"|\bthe\s+((?:[A-Z][\w-]*\s+){1,5})(topic|tool)\b")


def _stale_name_findings(ws: Workspace) -> list[Finding]:
    def norm(s: str) -> str:
        return re.sub(r"[^a-z0-9]", "", s.lower())

    known: dict[str, set[str]] = {"topic": set(), "tool": set()}
    for c in ws.components:
        bucket = "topic" if c.category == "topic" else "tool" if c.category in ("action", "workflow") else None
        if bucket:
            data = _d(c.data)
            intent = _d(_d(data.get("beginDialog")).get("intent"))
            for n in (c.name, c.displayName, intent.get("displayName"), data.get("modelDisplayName")):
                if n:
                    known[bucket].add(norm(str(n)))
    out: list[Finding] = []
    for c in ws.components:
        if c.error or c.category in ("settings", "connectionReferences", "workflow"):
            continue
        text = "\n".join(line for line in c.text.splitlines() if not line.lstrip().startswith("#"))
        seen = set()
        for m in _NAMED_RE.finditer(text):
            name, what = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
            key = norm(name)
            if key and key not in known[what] and (key, what) not in seen:
                seen.add((key, what))
                out.append(Finding(severity="warning", rule="stale-reference", component=c.id,
                                   message=f"Mentions the {what} '{name.strip()}', which isn't in this workspace"))
    return out
