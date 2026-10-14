"""Workspace -> documentation model (pure data; renderers in html.py / docx_export.py)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from ..analyzer.rules import Finding, analyze
from ..graph.agent_map import agent_map
from ..graph.flow_graph import flow_graph
from ..graph.refs import dialog_refs, flow_refs, resolve_dialog, workflow_ids
from ..graph.topic_graph import topic_graph
from ..validation.validator import flow_definition, iter_flow_actions
from ..workspace.model import Component, Workspace
from .mermaid import to_mermaid
from .summaries import cached


class Diagram(BaseModel):
    id: str
    title: str
    mermaid: str


class TopicDoc(BaseModel):
    id: str
    name: str
    owner: str | None
    trigger: str
    description: str
    phrases: list[str]
    summary: str
    variables: list[str]
    redirects: list[str]
    flows: list[str]
    nodeCount: int
    diagram: str


class FlowDoc(BaseModel):
    id: str
    name: str
    trigger: str
    inputs: list[str]
    outputs: list[str]
    connectors: list[str]
    steps: list[str]
    errorHandling: bool
    summary: str
    diagram: str


class Doc(BaseModel):
    title: str
    generatedAt: str
    orchestration: str
    instructions: str
    starters: list[str]
    overview: str
    model: str
    counts: dict[str, int]
    childAgents: list[dict[str, Any]]
    topics: list[TopicDoc]
    tools: list[dict[str, Any]]
    flows: list[FlowDoc]
    knowledge: list[dict[str, Any]]
    variables: list[dict[str, Any]]
    connections: list[str]
    findings: list[Finding]
    diagrams: list[Diagram] = Field(default_factory=list)


def _topic_doc(ws: Workspace, c: Component, summaries: dict[str, str], wf_ids: dict) -> TopicDoc:
    d = c.data if isinstance(c.data, dict) else {}
    bd = d.get("beginDialog") if isinstance(d.get("beginDialog"), dict) else {}
    intent = bd.get("intent") or {}
    variables = sorted({str(n.get("variable", "")).removeprefix("init:")
                        for n in _iter(d) if isinstance(n.get("variable"), str)})
    redirects = []
    for _, ref in dialog_refs(c):
        t = resolve_dialog(ws, ref)
        redirects.append(t.displayName if t else f"{ref} (missing)")
    flows = [(wf_ids.get(f.lower()).displayName if wf_ids.get(f.lower()) else f) for f in flow_refs(c)]
    g = topic_graph(d)
    return TopicDoc(
        id=c.id, name=c.displayName, owner=c.owner, trigger=str(bd.get("kind", "")).removeprefix("On"),
        description=str(d.get("modelDescription") or bd.get("description") or ""),
        phrases=[str(p) for p in (intent.get("triggerQueries") or [])] if isinstance(intent, dict) else [],
        summary=summaries.get(c.id, ""), variables=variables, redirects=sorted(set(redirects)),
        flows=sorted(set(flows)), nodeCount=max(len(g.nodes) - 2, 0), diagram=f"topic-{c.id}",
    )


def _iter(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _iter(v)
    elif isinstance(node, list):
        for v in node:
            yield from _iter(v)


def _props(rec: Any) -> list[str]:
    props = (rec or {}).get("properties") if isinstance(rec, dict) else None
    return [f"{k} ({(v or {}).get('type', '')})" for k, v in (props or {}).items()]


def _flow_doc(c: Component, summaries: dict[str, str]) -> FlowDoc:
    data = c.data or {}
    meta = data.get("metadata") or {}
    d = flow_definition(data.get("workflow")) or {}
    acts = list(iter_flow_actions(d.get("actions") or {}))
    connectors = sorted({str(((a.get("inputs") or {}).get("host") or {}).get("apiId") or "").rsplit("/", 1)[-1]
                         .removeprefix("shared_") for _, a, _ in acts
                         if isinstance(a.get("inputs"), dict) and isinstance(a["inputs"].get("host"), dict)} - {""})
    trig = next(iter((d.get("triggers") or {}).values()), {}) or {}
    return FlowDoc(
        id=c.id, name=c.displayName,
        trigger="When an agent calls the flow" if trig.get("kind") == "Skills" else str(trig.get("type", "")),
        inputs=_props(meta.get("inputType")), outputs=_props(meta.get("outputType")), connectors=connectors,
        steps=[f"{n.replace('_', ' ')} · {a.get('type', '')}" for n, a, _ in acts],
        errorHandling=any(any(set(s or []) & {"Failed", "TimedOut"} for s in (a.get("runAfter") or {}).values())
                          for _, a, _ in acts),
        summary=summaries.get(c.id, ""), diagram=f"flow-{c.id}",
    )


def _tool_doc(c: Component, summaries: dict[str, str], wf_ids: dict) -> dict:
    d = c.data if isinstance(c.data, dict) else {}
    action = d.get("action") or {}
    kind = action.get("kind", "")
    target = ""
    if kind == "InvokeFlowTaskAction":
        wf = wf_ids.get(str(action.get("flowId", "")).lower())
        target = f"flow: {wf.displayName if wf else action.get('flowId')}"
    elif action.get("connectionReference"):
        target = f"{action.get('connectionReference')} · {action.get('operationId') or (action.get('operationDetails') or {}).get('operationId', '')}"
    return {"id": c.id, "name": str(d.get("modelDisplayName") or c.displayName), "type": kind.removeprefix("Invoke").removesuffix("TaskAction"),
            "target": target, "description": str(d.get("modelDescription") or ""),
            "inputs": [f"{i.get('propertyName')} ({'AI' if i.get('kind') == 'AutomaticTaskInput' else 'fixed'})"
                       for i in d.get("inputs") or [] if isinstance(i, dict)],
            "summary": summaries.get(c.id, "")}


def build_doc(ws: Workspace) -> Doc:
    summaries = cached(ws)
    wf_ids = workflow_ids(ws)
    agent = ws.agent
    ad = agent.data if agent and isinstance(agent.data, dict) else {}

    diagrams = [Diagram(id="agent-map", title="Agent architecture", mermaid=to_mermaid(agent_map(ws), "LR"))]
    topics = []
    for c in ws.by_category("topic"):
        t = _topic_doc(ws, c, summaries, wf_ids)
        topics.append(t)
        diagrams.append(Diagram(id=t.diagram, title=f"Topic: {t.name}", mermaid=to_mermaid(topic_graph(c.data))))
    flows = []
    for c in ws.by_category("workflow"):
        f = _flow_doc(c, summaries)
        flows.append(f)
        diagrams.append(Diagram(id=f.diagram, title=f"Flow: {f.name}",
                                mermaid=to_mermaid(flow_graph((c.data or {}).get("workflow")))))

    children = []
    for c in ws.by_category("childAgent"):
        d = c.data if isinstance(c.data, dict) else {}
        children.append({"id": c.id, "name": c.displayName,
                         "kind": "Child agent" if d.get("kind") == "AgentDialog" else str(d.get("kind", "")),
                         "description": str((d.get("beginDialog") or {}).get("description") or ""),
                         "instructions": str((d.get("settings") or {}).get("instructions") or d.get("instructions") or ""),
                         "inputs": _props(d.get("inputType")), "outputs": _props(d.get("outputType")),
                         "summary": summaries.get(c.id, "")})

    knowledge = []
    for c in ws.by_category("knowledge"):
        d = c.data if isinstance(c.data, dict) else {}
        src = d.get("source") or {}
        knowledge.append({"id": c.id, "name": c.displayName, "owner": c.owner or "main agent",
                          "type": str(src.get("kind", "")).removesuffix("SearchSource"),
                          "detail": str(src.get("site") or src.get("url") or src.get("siteUrl") or "")})

    variables = []
    for c in ws.by_category("variable"):
        d = c.data if isinstance(c.data, dict) else {}
        var = d.get("variable") or {}
        variables.append({"name": c.displayName, "description": str(d.get("description") or ""),
                          "visibility": str((var or {}).get("aIVisibility", "")) if isinstance(var, dict) else ""})

    conn = next((c for c in ws.components if c.category == "connectionReferences"), None)
    connections = []
    if conn and isinstance(conn.data, dict):
        for item in conn.data.get("connectionReferences") or []:
            if isinstance(item, dict):
                connections.append(str(item.get("connectionReferenceLogicalName") or item.get("connectorId") or item))

    model = ((ad.get("aISettings") or {}).get("model") or {}).get("modelNameHint", "") if isinstance(ad.get("aISettings"), dict) else ""
    return Doc(
        title=agent.displayName if agent else ws.info.name,
        generatedAt=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        orchestration="Generative" if ws.generative_orchestration() else "Classic",
        instructions=str(ad.get("instructions") or ""),
        starters=[str(s.get("title") or s.get("text")) for s in ad.get("conversationStarters") or [] if isinstance(s, dict)],
        overview=summaries.get(agent.id, "") if agent else "", model=str(model),
        counts={"topics": len(topics), "childAgents": len(children), "tools": len(ws.by_category("action")),
                "flows": len(flows), "knowledge": len(knowledge), "variables": len(variables)},
        childAgents=children, topics=topics,
        tools=[_tool_doc(c, summaries, wf_ids) for c in ws.by_category("action")],
        flows=flows, knowledge=knowledge, variables=variables, connections=connections,
        findings=analyze(ws), diagrams=diagrams,
    )
