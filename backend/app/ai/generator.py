"""GenAI generation: prompt -> ChangeSet -> validate -> (auto)repair -> Proposal."""
from __future__ import annotations

import asyncio
import difflib
import json
import logging
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field

from .. import config, knowledge as kn
from ..analyzer.rules import Finding, analyze
from ..genai_client import GenAIError, complete_json, extract_json, llm_stream
from ..graph.flow_graph import flow_graph
from ..graph.topic_graph import Graph, topic_graph
from ..validation.validator import FileReport, validate_text
from ..workspace import store
from ..workspace.loader import SKIP_DIRS, load_workspace
from ..workspace.model import ChangeSet, FileChange, Workspace
from ..workspace.yamlio import load_plain
from . import history, mock
from .autofix import fix_ids
from .context import Mode, build_system_ex, file_texts, infer_mode

logger = logging.getLogger(__name__)

_ALLOWED_ROOTS = ("agent.mcs.yml", "settings.mcs.yml", "connectionreferences.mcs.yml", "topics/", "actions/",
                  "knowledge/", "variables/", "agents/", "workflows/", "trigger/")


class ChatTurn(BaseModel):
    role: str
    content: str


class GenerateRequest(BaseModel):
    mode: Mode | Literal["auto"]
    prompt: str
    target: str | None = None          # component id for modify/fix
    findings: list[str] = Field(default_factory=list)   # analyzer messages to fix
    patterns: list[str] = Field(default_factory=list)
    history: list[ChatTurn] = Field(default_factory=list)


class ProposedFile(BaseModel):
    path: str
    content: str | None
    before: str | None
    isNew: bool
    diff: str
    report: FileReport | None = None
    graph: Graph | None = None


class Requirement(BaseModel):
    """One requirement from the maker's request and where (or whether) the proposal implements it."""
    requirement: str
    status: Literal["covered", "partial", "missing"]
    where: str = ""
    comment: str = ""


class Proposal(BaseModel):
    id: str
    workspaceId: str
    mode: str
    prompt: str
    summary: str
    notes: list[str]
    files: list[ProposedFile]
    findings: list[Finding] = Field(default_factory=list)
    coverage: list[Requirement] = Field(default_factory=list)
    patterns: list[str] = Field(default_factory=list)
    repairRounds: int = 0
    valid: bool = True
    applied: list[str] | None = None   # paths written by "Apply" (None = not applied yet)


# In-memory proposal cache (single-user local app).
_PROPOSALS: dict[str, Proposal] = {}


def get_proposal(pid: str) -> Proposal | None:
    if pid in _PROPOSALS:
        return _PROPOSALS[pid]
    for info in store.list_workspaces():          # survived a restart: look in the saved chat history
        raw = history.get_proposal(info.path, pid)
        if raw:
            prop = Proposal(**raw)
            _PROPOSALS[pid] = prop
            return prop
    return None


def file_label(path: str, content: str | None = None) -> str:
    """Human name for a workspace path: 'Topic · Reset Password', 'Flow · CreateTicket'."""
    parts = path.split("/")
    stem = re.sub(r"\.(mcs\.)?ya?ml$|\.json$", "", parts[-1])
    if path == "agent.mcs.yml":
        return "Agent"
    if parts[0] == "topics":
        m = re.search(r"^#\s*Name:\s*(.+)$", content or "", re.M)
        return f"Topic · {m.group(1).strip() if m else stem}"
    if parts[0] == "actions":
        return f"Tool · {stem}"
    if parts[0] == "agents" and len(parts) > 2:
        return f"Child agent · {parts[1]}" if parts[-1].startswith("agent.mcs") else f"{parts[1]} · {stem}"
    if parts[0] == "workflows" and len(parts) > 2:
        return f"Flow · {parts[1]} ({'definition' if parts[-1] == 'workflow.json' else 'metadata'})"
    return {"knowledge": "Knowledge", "variables": "Variable", "trigger": "Event trigger"}.get(parts[0], "File") + \
        (f" · {stem}" if len(parts) > 1 else f" · {stem}")


def _normalise_path(p: str) -> str:
    p = p.strip().lstrip("./").replace("\\", "/")
    p = re.sub(r"\.topic\.mcs\.yml$", ".mcs.yml", p)
    if p.endswith(".mcs.yaml"):
        p = p[:-5] + "yml"
    return p


def _parse_changeset(raw: dict) -> ChangeSet:
    files = []
    for f in raw.get("files") or []:
        if not isinstance(f, dict) or not f.get("path"):
            continue
        content = f.get("content")
        if isinstance(content, (dict, list)):          # model sent JSON object for workflow.json
            content = json.dumps(content, indent=2)
        if isinstance(content, str) and not content.strip():
            content = None                             # "" means delete; an empty workflow.json breaks loading
        path = _normalise_path(str(f["path"]))
        if not path.startswith(_ALLOWED_ROOTS):
            logger.warning("Dropping file outside agent layout: %s", path)
            continue
        if content is not None and not str(content).endswith("\n"):
            content = f"{content}\n"
        files.append(FileChange(path=path, content=content))
    return ChangeSet(summary=str(raw.get("summary") or ""), notes=[str(n) for n in raw.get("notes") or []], files=files)


def _user_prompt(req: GenerateRequest, ws: Workspace) -> str:
    parts = []
    if req.history:
        parts.append("## Conversation so far")
        for t in req.history[-6:]:
            parts.append(f"{t.role.upper()}: {t.content[:2000]}")
    if req.target:
        comp = ws.get(req.target)
        if comp:
            if comp.category == "workflow":
                meta = store.read_file(ws.info, f"workflows/{comp.name}/metadata.yaml")
                parts.append(f"## Target flow: workflows/{comp.name}/\nmetadata.yaml:\n```yaml\n{meta}\n```\n"
                             f"workflow.json:\n```json\n{comp.text}\n```")
            else:
                parts.append(f"## Target file: {comp.path}\n```yaml\n{comp.text}\n```")
    if req.findings:
        parts.append("## Problems to fix\n" + "\n".join(f"- {f}" for f in req.findings))
    iterate = ("Update the existing agent (its files are in the workspace section) to satisfy the request. "
               "Keep everything the request doesn't affect exactly as it is. Return only the files you change, "
               "delete the ones that become obsolete, and update every reference to them.")
    task = {
        "agent": iterate if ws.agent is not None else
                 "Design and generate a complete agent: agent.mcs.yml (instructions, conversation starters), "
                 "the topics, child agents, tools/flows and knowledge it needs.",
        "topic": "Generate a new topic.",
        "flow": "Generate an agent flow (workflows/<Name>/metadata.yaml + workflow.json) and the TaskDialog tool "
                "in actions/ that exposes it to the orchestrator.",
        "childAgent": "Generate a child agent under agents/<Name>/agent.mcs.yml.",
        "modify": "Modify the target file as requested. Return the complete updated file. If the request also needs "
                  "changes elsewhere (instructions, redirects, new files), include those files too."
                  if req.target else iterate,
        "fix": "Fix the listed problems in the target file. Return the complete corrected file.",
        "ask": "Answer the question.",
    }[req.mode]
    parts.append(f"## Task\n{task}\n\n## Maker's request\n{req.prompt}")
    return "\n\n".join(parts)


def _node_at(data: Any, pointer: str) -> Any:
    cur = data
    for part in [p for p in pointer.split("/") if p]:
        if isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        elif isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _repair_hints(path: str, content: str, report: FileReport) -> str:
    kinds: set[str] = set()
    data = None
    if path.endswith((".yml", ".yaml")):
        try:
            data = load_plain(content)
        except Exception:  # noqa: BLE001
            data = None
    for i in report.issues:
        if i.severity != "error":
            continue
        node = _node_at(data, i.path) if data is not None else None
        if isinstance(node, dict) and node.get("kind"):
            kinds.add(str(node["kind"]))
        kinds.update(re.findall(r"'([A-Z][A-Za-z]+)' is not a valid kind", i.message))
    schema = "\n".join(kn.resolve_kind(k) for k in sorted(kinds))
    return schema


async def _repair(system: str, req_text: str, cs: ChangeSet, reports: dict[str, FileReport]) -> ChangeSet:
    broken = [f for f in cs.files if f.content is not None and reports.get(f.path) and not reports[f.path].valid]
    blocks = []
    for f in broken:
        rep = reports[f.path]
        errs = "\n".join(f"- [{i.rule}] at {i.path or '(root)'}: {i.message}" for i in rep.issues if i.severity == "error")
        hints = _repair_hints(f.path, f.content or "", rep)
        blocks.append(f"### {f.path}\nErrors:\n{errs}\n" + (f"Schema for the kinds involved:\n{hints}\n" if hints else "")
                      + f"Current content:\n```\n{f.content}\n```")
    prompt = (f"{req_text}\n\n## Validation failed\nThese generated files failed Microsoft's Copilot Studio schema/lint "
              f"checks. Return the SAME JSON contract with corrected complete versions of ONLY these files.\n\n"
              + "\n\n".join(blocks))
    raw = await complete_json(prompt, system=system)
    fixed = {f.path: f for f in _parse_changeset(raw).files}
    return cs.model_copy(update={"files": [fixed.get(f.path, f) for f in cs.files]})


def _validate(cs: ChangeSet) -> dict[str, FileReport]:
    return {f.path: validate_text(f.path, f.content) for f in cs.files if f.content is not None}


def _graph_with_status(path: str, after: str, before: str | None) -> Graph | None:
    try:
        if path.endswith("workflow.json"):
            new_g = flow_graph(json.loads(after))
            old_g = flow_graph(json.loads(before)) if before else None
        elif path.startswith(("topics/", "agents/")) and path.endswith(".yml"):
            new_g = topic_graph(load_plain(after))
            old_g = topic_graph(load_plain(before)) if before else None
        else:
            return None
    except Exception:  # noqa: BLE001
        return None
    old = {n.id: (n.kind, n.label, n.detail) for n in (old_g.nodes if old_g else [])}
    for n in new_g.nodes:
        if n.id not in old:
            n.status = "new"
        elif old[n.id] != (n.kind, n.label, n.detail):
            n.status = "changed"
        else:
            n.status = "same"
    return new_g


def _merged(ws: Workspace, cs: ChangeSet) -> tuple[list[Finding], list[tuple[str, str]]]:
    """Analyzer findings for the touched components, and every file's text, on a scratch copy of the
    workspace with the changes applied."""
    with tempfile.TemporaryDirectory() as tmp:
        dst = Path(tmp) / "ws"
        if Path(ws.info.path).exists():
            shutil.copytree(ws.info.path, dst, ignore=shutil.ignore_patterns(*SKIP_DIRS))
        else:
            dst.mkdir()
        info = ws.info.model_copy(update={"path": str(dst)})
        store.apply_changeset(info, cs)
        merged = load_workspace(info)
        touched = {f.path for f in cs.files}
        touched |= {p.rsplit("/", 1)[0] for p in touched if p.startswith("workflows/")}
        return [f for f in analyze(merged) if f.component in touched], file_texts(merged)


def _expand_deletes(cs: ChangeSet, ws: Workspace) -> ChangeSet:
    """Deleting any file of a flow deletes its whole workflows/<Name>/ folder. A leftover metadata.yaml
    without workflow.json is a broken flow, and the model often only knows about one of the two files."""
    have = {f.path for f in cs.files}
    extra = []
    for f in cs.files:
        if f.content is not None or not f.path.startswith("workflows/") or f.path.count("/") < 2:
            continue
        folder = "/".join(f.path.split("/")[:2])
        root = store.resolve_path(ws.info, folder)
        for p in sorted(root.rglob("*")) if root.is_dir() else []:
            rel = f"{folder}/{p.relative_to(root).as_posix()}"
            if p.is_file() and rel not in have:
                have.add(rel)
                extra.append(FileChange(path=rel, content=None))
    return cs.model_copy(update={"files": [*cs.files, *extra]}) if extra else cs


COVERAGE_SYSTEM = """\
You review a Microsoft Copilot Studio agent (YAML files in the VS Code extension format, plus agent flows)
against the maker's request. Split the request into its concrete requirements (numbered items, constraints such
as "only in the chat" or "never use general knowledge", inputs and outputs). For each one decide whether the files,
as they will be after this change, implement it:
- covered: clearly implemented; `where` names the file and the node, setting or instruction that does it.
- partial: only partly, or only in instructions where a node or setting is needed; say what is missing.
- missing: not implemented.
Also check the earlier requests: list one only when this change breaks or removes it (status missing).
Judge the files, not the summary. Be strict and specific; never invent a location.
Reply with ONE JSON object: {"requirements": [{"requirement": "...", "status": "covered|partial|missing",
"where": "topics/X.mcs.yml · node id or key", "comment": "one short sentence"}]}"""

_COVERAGE_FILE_CHARS = 15000
_COVERAGE_TOTAL_CHARS = 80000


def _mock_coverage(req: GenerateRequest, cs: ChangeSet) -> list[Requirement]:
    items = [s.strip(" -*") for s in re.split(r"\n+|(?<=[.!?])\s+", req.prompt) if len(s.strip(" -*")) > 8]
    where = cs.files[0].path if cs.files else ""
    return [Requirement(requirement=s[:200], status="covered", where=where, comment="[mock] not checked")
            for s in items[:8]]


async def check_coverage(req: GenerateRequest, cs: ChangeSet, texts: list[tuple[str, str]]) -> list[Requirement]:
    """Map each requirement in the request to the file that implements it, or flag it as partial/missing."""
    if config.USE_MOCK_AI:
        return _mock_coverage(req, cs)
    changed = {f.path for f in cs.files}
    budget, blocks = _COVERAGE_TOTAL_CHARS, []
    for path, text in sorted(texts, key=lambda t: t[0] not in changed):    # changed files first
        body = text if len(text) <= _COVERAGE_FILE_CHARS else text[:_COVERAGE_FILE_CHARS] + "\n… (truncated)"
        if len(body) > budget:
            blocks.append(f"### {path}\n(not shown)")
            continue
        budget -= len(body)
        blocks.append(f"### {path}{' (changed)' if path in changed else ''}\n```\n{body}\n```")
    earlier = [t.content[:1500] for t in req.history if t.role == "user"][-4:]
    deleted = [f.path for f in cs.files if f.content is None]
    prompt = "\n\n".join([
        f"## Request\n{req.prompt}",
        *(["## Earlier requests in this conversation\n" + "\n".join(f"- {e}" for e in earlier)] if earlier else []),
        f"## Summary of the change (by the author; verify it)\n{cs.summary}",
        *([f"## Deleted by this change\n" + "\n".join(f"- {p}" for p in deleted)] if deleted else []),
        "## Files after the change\n" + "\n\n".join(blocks),
    ])
    raw = await complete_json(prompt, system=COVERAGE_SYSTEM)
    out = []
    for r in raw.get("requirements") or []:
        if isinstance(r, dict) and r.get("requirement"):
            status = str(r.get("status") or "").lower()
            out.append(Requirement(requirement=str(r["requirement"]),
                                   status=status if status in ("covered", "partial", "missing") else "partial",
                                   where=str(r.get("where") or ""), comment=str(r.get("comment") or "")))
    return out


Emit = Callable[..., None]
_PATH_RE = re.compile(r'"path"\s*:\s*"([^"]+)"')


def _mock_text(mode: str, prompt: str, target: str | None, ws: Workspace) -> str:
    raw = mock.respond(mode, prompt, target, ws)
    plan = ["PLAN: [mock] Read the request and pick the matching Microsoft templates.",
            *(f"PLAN: [mock] Write {file_label(f['path'], f['content'])}." for f in raw["files"][:4]),
            "PLAN: [mock] Validate every file against the authoring schema."]
    return "\n".join(plan) + "\n" + json.dumps(raw)


async def _stream_reply(user: str, system: str, req: GenerateRequest, ws: Workspace, emit: Emit) -> dict:
    """Stream the model reply, surfacing PLAN: lines and file paths as they appear."""
    buf, plan_done, seen = "", False, set()
    last_progress = 0.0
    line_start = 0

    def scan() -> None:
        nonlocal plan_done, line_start
        if not plan_done:
            while "\n" in buf[line_start:]:
                end = buf.index("\n", line_start)
                line = buf[line_start:end].strip()
                line_start = end + 1
                if line.upper().startswith("PLAN:"):
                    emit("plan", text=line[5:].strip())
                elif line.startswith("{") or line.startswith("```"):
                    plan_done = True
                    break
            if "{" in buf[line_start:]:
                plan_done = True
        for m in _PATH_RE.finditer(buf):
            path = _normalise_path(m.group(1))
            if path not in seen:
                seen.add(path)
                emit("file", path=path, label=file_label(path), status="writing")

    if config.USE_MOCK_AI:
        text = _mock_text(req.mode, req.prompt, req.target, ws)
        for i in range(0, len(text), 700):
            buf += text[i:i + 700]
            scan()
            await asyncio.sleep(0.01)
    else:
        async for delta in llm_stream(user, system=system):
            buf += delta
            scan()
            now = asyncio.get_event_loop().time()
            if now - last_progress > 0.8:
                last_progress = now
                emit("writing", chars=len(buf))
    raw = extract_json(buf)
    if raw is None:
        if config.USE_MOCK_AI:
            raise GenAIError("Mock reply was not valid JSON")
        emit("notice", text="The reply was not valid JSON — asking the model to resend it.")
        raw = await complete_json(user + "\n\nReturn ONLY the JSON object.", system=system)
    return raw


def _fix(cs: ChangeSet) -> ChangeSet:
    cs.files = [f if f.content is None or not f.path.endswith(".yml") else f.model_copy(update={"content": fix_ids(f.content)})
                for f in cs.files]
    return cs


def _emit_reports(emit: Emit, reports: dict[str, FileReport], phase: str) -> None:
    for path, r in reports.items():
        errs = [i for i in r.issues if i.severity == "error"]
        emit("validate", path=path, label=file_label(path), valid=r.valid, errors=len(errs), phase=phase,
             messages=[f"{i.path or '(root)'}: {i.message}" for i in errs[:3]])


async def generate(ws: Workspace, req: GenerateRequest) -> Proposal:
    return await generate_stream(ws, req, lambda *_a, **_k: None)


async def generate_stream(ws: Workspace, req: GenerateRequest, emit: Emit) -> Proposal:
    inferred = req.mode == "auto"
    mode: Mode = infer_mode(req.prompt, req.target, ws) if inferred else req.mode  # type: ignore[assignment]
    if req.findings and inferred:
        mode = "fix"
    req = req.model_copy(update={"mode": mode})
    system, pats, sources = build_system_ex(mode, req.prompt, ws, req.patterns, target=req.target)
    emit("context", mode=mode, inferred=inferred, target=req.target, **sources)
    user = _user_prompt(req, ws)

    emit("step", step="write", status="running")
    raw = await _stream_reply(user, system, req, ws, emit)
    cs = _expand_deletes(_fix(_parse_changeset(raw)), ws)
    emit("step", step="write", status="done")
    parsed = []
    for f in cs.files:
        before = store.read_file(ws.info, f.path) or None
        a, d = _line_delta(before or "", f.content or "")
        parsed.append({"path": f.path, "label": file_label(f.path, f.content), "isNew": before is None,
                       "delete": f.content is None, "added": a, "removed": d})
    emit("parsed", files=parsed, summary=cs.summary)

    reports = _validate(cs)
    if reports:
        emit("step", step="validate", status="running")
        _emit_reports(emit, reports, "initial")
        emit("step", step="validate", status="done")
    rounds = 0
    while (not config.USE_MOCK_AI and rounds < config.AI_REPAIR_ATTEMPTS
           and any(not r.valid for r in reports.values())):
        rounds += 1
        broken = [{"path": p, "label": file_label(p), "errors": [i.message for i in r.issues if i.severity == "error"][:3]}
                  for p, r in reports.items() if not r.valid]
        emit("repair", round=rounds, files=broken)
        cs = _expand_deletes(_fix(await _repair(system, user, cs, reports)), ws)
        reports = _validate(cs)
        _emit_reports(emit, reports, f"repair {rounds}")
        emit("repaired", round=rounds, valid=all(r.valid for r in reports.values()))

    files = []
    for f in cs.files:
        before = store.read_file(ws.info, f.path) or None
        diff = "".join(difflib.unified_diff((before or "").splitlines(keepends=True),
                                            (f.content or "").splitlines(keepends=True),
                                            fromfile=f"a/{f.path}", tofile=f"b/{f.path}"))
        files.append(ProposedFile(path=f.path, content=f.content, before=before, isNew=before is None, diff=diff,
                                  report=reports.get(f.path),
                                  graph=_graph_with_status(f.path, f.content, before) if f.content else None))

    findings: list[Finding] = []
    coverage: list[Requirement] = []
    if cs.files:
        emit("step", step="analyze", status="running")
        findings, texts = _merged(ws, cs)
        emit("analyze", findings=len(findings), errors=sum(1 for f in findings if f.severity == "error"),
             warnings=sum(1 for f in findings if f.severity == "warning"))
        if config.AI_COVERAGE_CHECK and req.mode not in ("ask", "fix"):
            emit("step", step="coverage", status="running")
            try:
                coverage = await check_coverage(req, cs, texts)
                emit("coverage", requirements=[r.model_dump() for r in coverage],
                     **{s: sum(1 for r in coverage if r.status == s) for s in ("covered", "partial", "missing")})
            except GenAIError as e:
                emit("notice", text=f"Requirement check skipped: {e}")
    prop = Proposal(
        id=uuid.uuid4().hex[:12], workspaceId=ws.info.id, mode=req.mode, prompt=req.prompt,
        summary=cs.summary, notes=cs.notes, files=files, findings=findings, coverage=coverage,
        patterns=pats, repairRounds=rounds, valid=all(r.valid for r in reports.values()),
    )
    _PROPOSALS[prop.id] = prop
    emit("proposal", proposal=prop.model_dump())
    return prop


def _line_delta(before: str, after: str) -> tuple[int, int]:
    added = removed = 0
    for line in difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=0):
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed


def accept(prop: Proposal, paths: list[str] | None = None) -> list[str]:
    info = store.get_info(prop.workspaceId)
    chosen = [f for f in prop.files if paths is None or f.path in paths]
    written = store.apply_changeset(info, ChangeSet(files=[FileChange(path=f.path, content=f.content) for f in chosen]))
    _PROPOSALS.pop(prop.id, None)
    history.mark_applied(info.path, prop.id, written)
    return written
