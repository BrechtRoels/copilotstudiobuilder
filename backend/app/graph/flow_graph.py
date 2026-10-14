"""Power Automate workflow definition -> node/edge graph."""
from __future__ import annotations

from typing import Any

from ..validation.validator import flow_definition
from .topic_graph import GEdge, GNode, Graph

_SCOPE_TYPES = {"Scope", "If", "Foreach", "Until", "Switch"}


def _label(name: str) -> str:
    return name.replace("_", " ")


def _detail(act: dict) -> str:
    t = act.get("type", "")
    inputs = act.get("inputs") if isinstance(act.get("inputs"), dict) else {}
    host = inputs.get("host") if isinstance(inputs.get("host"), dict) else {}
    if host.get("operationId"):
        api = str(host.get("apiId") or host.get("connectionName") or "").rsplit("/", 1)[-1].removeprefix("shared_")
        return f"{api} · {host['operationId']}"
    if t == "If":
        return "condition"
    if t == "Foreach":
        return f"for each {str(act.get('foreach', ''))[:60]}"
    if t == "Response" and act.get("kind") == "Skills":
        return "respond to the agent"
    if t == "Http":
        return f"{inputs.get('method', '')} {str(inputs.get('uri', ''))[:60]}"
    return t


def _meta(act: dict) -> dict:
    t = act.get("type", "")
    inputs = act.get("inputs") if isinstance(act.get("inputs"), dict) else {}
    host = inputs.get("host") if isinstance(inputs.get("host"), dict) else {}
    m: dict = {}
    if host.get("operationId"):
        m["connector"] = str(host.get("apiId") or host.get("connectionName") or "").rsplit("/", 1)[-1].removeprefix("shared_")
        m["operation"] = str(host["operationId"])
    if t == "Response":
        body = inputs.get("body")
        m["outputs"] = list(body.keys()) if isinstance(body, dict) else []
    if t in ("If", "Until"):
        m["text"] = str(act.get("expression") if isinstance(act.get("expression"), str) else "condition")[:120]
    if t == "Compose":
        m["text"] = str(act.get("inputs"))[:120]
    if t in ("InitializeVariable", "SetVariable"):
        v = (inputs.get("variables") or [inputs])[0] if isinstance(inputs, dict) else {}
        m["variable"] = str((v or {}).get("name", ""))
        m["value"] = str((v or {}).get("value", ""))[:80]
    return m


def _variant(act: dict) -> str:
    t = act.get("type")
    if t == "Response":
        return "end"
    if t in ("If", "Switch"):
        return "branch"
    if t in _SCOPE_TYPES:
        return "scope"
    if t in ("OpenApiConnection", "Http", "ApiConnection"):
        return "flow"
    if t == "Terminate":
        return "terminal"
    return "action"


class _FlowBuilder:
    def __init__(self) -> None:
        self.nodes: list[GNode] = []
        self.edges: list[GEdge] = []

    def edge(self, s: str, t: str, label: str = "", variant: str = "default") -> None:
        self.edges.append(GEdge(id=f"e{len(self.edges)}", source=s, target=t, label=label, variant=variant))

    def scope(self, actions: dict, entry: tuple[str, str, str] | None, pointer: str) -> None:
        for name, act in (actions or {}).items():
            if not isinstance(act, dict):
                continue
            ptr = f"{pointer}/{name}"
            self.nodes.append(GNode(id=ptr, kind=str(act.get("type", "?")), label=_label(name),
                                    detail=_detail(act), pointer=ptr, variant=_variant(act), meta=_meta(act)))
            run_after = act.get("runAfter") or {}
            if not run_after and entry:
                self.edge(entry[0], ptr, entry[1], entry[2])
            for dep, statuses in run_after.items():
                st = [s for s in (statuses or []) if s != "Succeeded"]
                self.edge(f"{pointer}/{dep}", ptr, ", ".join(st), "error" if st else "default")
            t = act.get("type")
            if t == "If":
                self.scope(act.get("actions") or {}, (ptr, "yes", "branch"), f"{ptr}/actions")
                self.scope((act.get("else") or {}).get("actions") or {}, (ptr, "no", "else"), f"{ptr}/else/actions")
            elif t == "Switch":
                for cname, case in (act.get("cases") or {}).items():
                    self.scope((case or {}).get("actions") or {}, (ptr, cname, "branch"), f"{ptr}/cases/{cname}/actions")
                self.scope((act.get("default") or {}).get("actions") or {}, (ptr, "default", "else"), f"{ptr}/default/actions")
            elif t in _SCOPE_TYPES:
                self.scope(act.get("actions") or {}, (ptr, "do", "branch"), f"{ptr}/actions")


def flow_graph(workflow: Any) -> Graph:
    d = flow_definition(workflow)
    if not d:
        return Graph(nodes=[], edges=[])
    b = _FlowBuilder()
    entry = None
    for tname, trig in (d.get("triggers") or {}).items():
        kind = (trig or {}).get("kind")
        label = "When an agent calls the flow" if kind == "Skills" else _label(tname)
        tid = f"triggers/{tname}"
        b.nodes.append(GNode(id=tid, kind=str((trig or {}).get("type", "Trigger")), label=label,
                             detail=str((trig or {}).get("type", "")), pointer=tid, variant="trigger"))
        entry = (tid, "", "default")
    b.scope(d.get("actions") or {}, entry, "actions")
    return Graph(nodes=b.nodes, edges=b.edges)
