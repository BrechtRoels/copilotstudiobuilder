"""Whole-agent map: agent -> topics / tools / flows / child agents / knowledge."""
from __future__ import annotations

from ..workspace.model import Workspace
from .refs import dialog_refs, flow_refs, resolve_dialog, workflow_ids
from .topic_graph import GEdge, GNode, Graph, trigger_label

_VARIANT = {
    "agent": "trigger", "topic": "action", "action": "flow", "workflow": "flow",
    "childAgent": "ai", "knowledge": "end", "variable": "branch", "trigger": "redirect",
}


def _topic_trigger(data) -> str:
    bd = (data or {}).get("beginDialog") if isinstance(data, dict) else None
    return str(bd.get("kind", "")) if isinstance(bd, dict) else ""


def agent_map(ws: Workspace) -> Graph:
    nodes: list[GNode] = []
    edges: list[GEdge] = []

    def edge(s: str, t: str, label: str = "", variant: str = "default") -> None:
        edges.append(GEdge(id=f"e{len(edges)}", source=s, target=t, label=label, variant=variant))

    root = ws.agent
    root_id = root.id if root else "__agent"
    nodes.append(GNode(id=root_id, kind="Agent", label=root.displayName if root else ws.info.name,
                       detail="generative orchestration" if ws.generative_orchestration() else "classic orchestration",
                       pointer=root.id if root else "", variant="trigger"))
    child_ids = {c.owner or c.name: c.id for c in ws.by_category("childAgent")}

    for c in ws.components:
        if c.category not in _VARIANT or c.category == "agent":
            continue
        detail = {"topic": trigger_label(_topic_trigger(c.data)), "action": "tool", "workflow": "agent flow",
                  "childAgent": "child agent", "knowledge": "knowledge", "variable": "global variable",
                  "trigger": "event trigger"}[c.category]
        nodes.append(GNode(id=c.id, kind=c.category, label=c.displayName, detail=detail, pointer=c.id,
                           variant=_VARIANT[c.category]))
        if c.owner and c.category != "childAgent":
            edge(child_ids.get(c.owner, root_id), c.id)
        elif c.category in ("childAgent", "action", "knowledge", "trigger"):
            edge(root_id, c.id)
        elif c.category == "topic" and _topic_trigger(c.data) in ("OnRecognizedIntent", "OnConversationStart", ""):
            edge(root_id, c.id, variant="else")

    # Global variables -> the topics / child agents that read or set them.
    for v in ws.by_category("variable"):
        name = str((v.data or {}).get("name") or v.name) if isinstance(v.data, dict) else v.name
        for c in ws.components:
            if c.category in ("topic", "childAgent", "action") and f"Global.{name}" in c.text:
                edge(v.id, c.id, "used by", "else")

    wf = workflow_ids(ws)
    for c in ws.components:
        if c.category not in ("topic", "action", "childAgent"):
            continue
        for kind, ref in dialog_refs(c):
            target = resolve_dialog(ws, ref)
            if target and target.id != c.id:
                edge(c.id, target.id, "redirect" if kind == "BeginDialog" else "replace", "goto")
        for fid in flow_refs(c):
            target = wf.get(fid.lower())
            if target:
                edge(c.id, target.id, "runs", "branch")
    return Graph(nodes=nodes, edges=edges)
