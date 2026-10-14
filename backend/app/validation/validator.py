"""Single-file validation for Copilot Studio YAML and agent-flow JSON.

1. Structural: Microsoft's YAML authoring JSON schema (vendored from
   microsoft/skills-for-copilot-studio), selected by the file's `kind`.
2. Lint: rules from the vendored int-reference guide that the schema can't
   express (unique ids, leftover template placeholders, Power Fx function
   whitelist, init: on first assignment, AgentDialog without actions, ...).

Cross-file checks (redirect targets, trigger overlap, ...) live in analyzer/.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any, Iterator, Literal

from jsonschema import Draft7Validator
from pydantic import BaseModel

from .. import config
from ..workspace.yamlio import load_plain

Severity = Literal["error", "warning", "info"]


class Issue(BaseModel):
    severity: Severity
    message: str
    path: str = ""          # JSON-pointer-ish location inside the file
    rule: str = ""


class FileReport(BaseModel):
    path: str
    kind: str | None = None
    valid: bool
    issues: list[Issue]


# --- schema -------------------------------------------------------------------
@lru_cache(maxsize=1)
def _schema() -> dict:
    return json.loads((config.KNOWLEDGE_DIR / "reference" / "bot.schema.yaml-authoring.json").read_text())


@lru_cache(maxsize=64)
def _validator(kind: str) -> Draft7Validator | None:
    defs = _schema()["definitions"]
    if kind not in defs:
        return None
    return Draft7Validator({"$ref": f"#/definitions/{kind}", "definitions": defs})


def known_kinds() -> set[str]:
    return set(_schema()["definitions"])


def _fmt_path(p) -> str:
    return "/".join(str(x) for x in p)


def _kind_branch_errors(err) -> list:
    """For a oneOf/anyOf failure, keep only errors from the branch whose `kind` matched.

    Copilot Studio uses `kind` as the discriminator, so the branch that *didn't*
    complain about `kind` is the one the author meant.
    """
    branches: dict[Any, list] = {}
    for sub in err.context:
        branches.setdefault(sub.schema_path[0] if sub.schema_path else None, []).append(sub)
    matching = [
        errs for errs in branches.values()
        if not any(e.validator == "const" and list(e.path)[-1:] == ["kind"] for e in errs)
    ]
    if len(matching) == 1:
        return matching[0]
    return []


def _schema_issues(data: Any, kind: str) -> Iterator[Issue]:
    v = _validator(kind)
    if v is None:
        return
    seen = set()
    stack = list(v.iter_errors(data))
    while stack:
        e = stack.pop(0)
        if e.validator in ("oneOf", "anyOf") and e.context:
            inner = _kind_branch_errors(e)
            if inner:
                stack[:0] = inner
                continue
            kind_here = None
            inst = e.instance
            if isinstance(inst, dict):
                kind_here = inst.get("kind")
            msg = (f"'{kind_here}' is not a valid kind here or has invalid properties"
                   if kind_here else "Value doesn't match any allowed shape")
        elif e.validator == "additionalProperties":
            msg = e.message.replace("Additional properties are not allowed", "Unknown property")
        else:
            msg = e.message
        key = (_fmt_path(e.absolute_path), msg)
        if key in seen:
            continue
        seen.add(key)
        if len(msg) > 300:
            msg = msg[:300] + "…"
        yield Issue(severity="error", message=msg, path=_fmt_path(e.absolute_path), rule="schema")


# --- lint ---------------------------------------------------------------------
@lru_cache(maxsize=1)
def supported_powerfx() -> frozenset[str]:
    """Parse the supported Power Fx function list from the vendored reference."""
    text = (config.KNOWLEDGE_DIR / "skills" / "int-reference" / "SKILL.md").read_text()
    section = text.split("### Supported Power Fx Functions", 1)[-1].split("\n## ", 1)[0]
    return frozenset(re.findall(r"`([A-Za-z][A-Za-z0-9]*)`", section))


_CALL_RE = re.compile(r"(?<![\w.])([A-Z][A-Za-z0-9]*)\s*\(")
_STRING_LIT_RE = re.compile(r'"(?:[^"\\]|\\.)*"')


# A placeholder inside an identifier, e.g. `<agentSchemaName>.globalvariable.Step`. Only identifier keys are
# checked, and only camelCase or dashed names, so HTML in messages (`<p>`, `<br>`) never matches.
_NAME_KEYS = {"schemaName", "dialog", "flowId", "connectionReference", "connectionReferenceLogicalName", "name"}
_INLINE_PLACEHOLDER_RE = re.compile(r"<(?:[a-z][a-z0-9]*[A-Z][A-Za-z0-9]*|[a-z][a-z0-9]*(?:[-_][a-z0-9]+)+)>")


def _walk(node: Any, path: tuple = ()) -> Iterator[tuple[tuple, Any]]:
    yield path, node
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _walk(v, path + (k,))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(v, path + (i,))


def _lint_yaml(data: Any, text: str, kind: str | None) -> Iterator[Issue]:
    ids: dict[str, str] = {}
    first_assign: dict[str, bool] = {}
    declared_inputs: set[str] = set()
    if isinstance(data, dict):
        for inp in data.get("inputs") or []:
            if isinstance(inp, dict) and inp.get("propertyName"):
                declared_inputs.add(str(inp["propertyName"]))
        for section in ("inputType", "outputType"):
            for prop in ((data.get(section) or {}).get("properties") or {}):
                declared_inputs.add(str(prop))

    allowed_fx = supported_powerfx()
    for path, node in _walk(data):
        p = _fmt_path(path)
        if isinstance(node, dict):
            nid = node.get("id")
            if isinstance(nid, str) and path and path[-1] != "intent":
                if nid in ids:
                    yield Issue(severity="error", rule="unique-id", path=p,
                                message=f"Duplicate node id '{nid}' (also at {ids[nid]})")
                else:
                    ids[nid] = p
                if "_REPLACE" in nid:
                    yield Issue(severity="error", rule="placeholder", path=p,
                                message=f"Template placeholder id '{nid}' must be replaced with a unique id")
            k = node.get("kind")
            if k == "OnOutgoingMessage":
                yield Issue(severity="error", rule="non-functional", path=p,
                            message="OnOutgoingMessage exists in the schema but does not fire at runtime. Use OnGeneratedResponse.")
            var = node.get("variable")
            if isinstance(var, str):
                is_init = var.startswith("init:")
                name = var.removeprefix("init:")
                if name.startswith("Topic."):
                    short = name.split(".", 1)[1]
                    if name not in first_assign:
                        first_assign[name] = is_init
                        if not is_init and short not in declared_inputs:
                            yield Issue(severity="info", rule="init-prefix", path=p,
                                        message=f"First assignment of {name} should use 'init:{name}'")
        elif isinstance(node, str):
            if node.startswith("<") and node.endswith(">") and len(node) < 120:
                yield Issue(severity="warning", rule="placeholder", path=p,
                            message=f"Unfilled template placeholder {node}")
            elif path and path[-1] in _NAME_KEYS and (m := _INLINE_PLACEHOLDER_RE.search(node)):
                yield Issue(severity="warning", rule="placeholder", path=p,
                            message=f"Unfilled placeholder {m.group(0)} in {path[-1]} '{node}'")
            if node.startswith("="):
                expr = _STRING_LIT_RE.sub('""', node[1:])
                for fn in _CALL_RE.findall(expr):
                    if fn not in allowed_fx:
                        yield Issue(severity="warning", rule="powerfx", path=p,
                                    message=f"Power Fx function '{fn}' is not in Copilot Studio's supported list")

    if kind == "AgentDialog" and isinstance(data, dict):
        bd = data.get("beginDialog") or {}
        if isinstance(bd, dict) and bd.get("actions"):
            yield Issue(severity="error", rule="child-agent-actions", path="beginDialog/actions",
                        message="Child agents (AgentDialog) must not have beginDialog.actions; behaviour comes from settings.instructions.")
        if isinstance(bd, dict) and not bd.get("description"):
            yield Issue(severity="warning", rule="child-agent-description", path="beginDialog",
                        message="Child agent has no beginDialog.description; the orchestrator uses it for routing.")


# --- agent flows (workflow.json) ---------------------------------------------
def flow_definition(wf: Any) -> dict | None:
    """Accept both raw Logic Apps definitions and Power Automate clientdata wrappers."""
    if not isinstance(wf, dict):
        return None
    for candidate in (wf.get("properties", {}).get("definition") if isinstance(wf.get("properties"), dict) else None,
                      wf.get("definition"), wf):
        if isinstance(candidate, dict) and ("triggers" in candidate or "actions" in candidate):
            return candidate
    return None


def iter_flow_actions(actions: dict, parent: str = "") -> Iterator[tuple[str, dict, dict]]:
    """Yield (name, action, sibling-scope) for every action, recursing into scopes."""
    for name, act in (actions or {}).items():
        if not isinstance(act, dict):
            continue
        yield name, act, actions
        for sub in _child_scopes(act):
            yield from iter_flow_actions(sub, name)


def _child_scopes(act: dict) -> list[dict]:
    scopes = []
    if isinstance(act.get("actions"), dict):
        scopes.append(act["actions"])
    if isinstance(act.get("else"), dict) and isinstance(act["else"].get("actions"), dict):
        scopes.append(act["else"]["actions"])
    for case in (act.get("cases") or {}).values():
        if isinstance(case, dict) and isinstance(case.get("actions"), dict):
            scopes.append(case["actions"])
    if isinstance(act.get("default"), dict) and isinstance(act["default"].get("actions"), dict):
        scopes.append(act["default"]["actions"])
    return scopes


def _lint_flow(wf: Any) -> Iterator[Issue]:
    d = flow_definition(wf)
    if d is None:
        yield Issue(severity="error", rule="flow-shape", message="workflow.json has no definition with triggers/actions")
        return
    if not d.get("triggers"):
        yield Issue(severity="error", rule="flow-trigger", path="triggers", message="Flow has no trigger")
    names = set()
    for name, act, siblings in iter_flow_actions(d.get("actions") or {}):
        if name in names:
            yield Issue(severity="error", rule="flow-unique", path=name, message=f"Duplicate action name '{name}'")
        names.add(name)
        if not act.get("type"):
            yield Issue(severity="error", rule="flow-type", path=name, message=f"Action '{name}' has no type")
        for dep in (act.get("runAfter") or {}):
            if dep not in siblings:
                yield Issue(severity="error", rule="flow-runafter", path=name,
                            message=f"Action '{name}' runs after '{dep}', which is not in the same scope")


# --- entry points -------------------------------------------------------------
def validate_text(path: str, text: str) -> FileReport:
    issues: list[Issue] = []
    kind = None
    if path.endswith(".json"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            return FileReport(path=path, valid=False, issues=[Issue(severity="error", rule="parse", message=str(e))])
        if path.endswith("workflow.json"):
            kind = "CloudFlow"
            issues.extend(_lint_flow(data))
    elif path.endswith((".yml", ".yaml")):
        try:
            data = load_plain(text)
        except Exception as e:  # noqa: BLE001
            return FileReport(path=path, valid=False,
                              issues=[Issue(severity="error", rule="parse", message=f"YAML error: {e}")])
        if isinstance(data, dict):
            kind = data.get("kind")
            # Wrapper keys the file format adds around the schema object.
            wrapper = {"mcs.metadata"} | ({"displayName"} if kind == "GptComponentMetadata" else set())
            body = {k: v for k, v in data.items() if k not in wrapper}
            if kind and kind in known_kinds():
                issues.extend(_schema_issues(body, kind))
            elif kind:
                issues.append(Issue(severity="warning", rule="kind", path="kind",
                                    message=f"Unknown kind '{kind}' (not in the authoring schema)"))
            issues.extend(_lint_yaml(body, text, kind))
    return FileReport(path=path, kind=kind, valid=not any(i.severity == "error" for i in issues), issues=issues)
