"""Cross-component reference helpers (topic redirects, flow ids, child agents)."""
from __future__ import annotations

import re
from typing import Any, Iterator

from ..workspace.model import Component, Workspace

REDIRECT_KINDS = {"BeginDialog", "ReplaceDialog"}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def resolve_dialog(ws: Workspace, ref: str) -> Component | None:
    """Resolve `cr123_agent.topic.Escalate` (or a bare name) to a topic / child agent."""
    if not isinstance(ref, str) or not ref:
        return None
    tail = ref
    for marker in (".topic.", ".agent.", ".dialog."):
        if marker in ref:
            tail = ref.rsplit(marker, 1)[1]
            break
    else:
        tail = ref.rsplit(".", 1)[-1]
    key = _norm(tail)
    for c in ws.components:
        if c.category in ("topic", "childAgent") and key in (_norm(c.name), _norm(c.displayName)):
            return c
    return None


def iter_nodes(node: Any) -> Iterator[dict]:
    """Every dict with a `kind` inside a component (depth-first)."""
    if isinstance(node, dict):
        if "kind" in node:
            yield node
        for v in node.values():
            yield from iter_nodes(v)
    elif isinstance(node, list):
        for v in node:
            yield from iter_nodes(v)


def workflow_ids(ws: Workspace) -> dict[str, Component]:
    """workflowId (lower-case) -> workflow component; folder name also accepted."""
    out: dict[str, Component] = {}
    for c in ws.by_category("workflow"):
        meta = (c.data or {}).get("metadata") or {}
        for key in (meta.get("workflowId"), meta.get("id"), c.name):
            if key:
                out[str(key).lower()] = c
    return out


def flow_refs(comp: Component) -> list[str]:
    return [str(n["flowId"]) for n in iter_nodes(comp.data)
            if n.get("kind") in ("InvokeFlowAction", "InvokeFlowTaskAction") and n.get("flowId")]


def dialog_refs(comp: Component) -> list[tuple[str, str]]:
    """(kind, target) for every redirect node."""
    return [(n["kind"], str(n["dialog"])) for n in iter_nodes(comp.data)
            if n.get("kind") in REDIRECT_KINDS and n.get("dialog")]
