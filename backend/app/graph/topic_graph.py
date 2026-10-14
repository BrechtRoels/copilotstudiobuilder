"""Topic YAML (AdaptiveDialog / AgentDialog) -> node/edge graph for the canvas."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class GNode(BaseModel):
    id: str
    kind: str
    label: str
    detail: str = ""
    pointer: str = ""          # location in the file, e.g. beginDialog/actions/2
    variant: str = "action"    # trigger | action | branch | redirect | flow | ai | end | terminal
    status: str | None = None  # new | changed | same (proposal previews)
    meta: dict = Field(default_factory=dict)  # node content for the canvas card (text, entity, variable, target…)


class GEdge(BaseModel):
    id: str
    source: str
    target: str
    label: str = ""
    variant: str = "default"   # default | branch | else | error | goto


class Graph(BaseModel):
    nodes: list[GNode]
    edges: list[GEdge]


TERMINAL = {"EndDialog", "CancelAllDialogs", "EndConversation", "ReplaceDialog", "GotoAction", "TransferConversation",
            "TransferConversationV2"}


def _text(v: Any, n: int = 90) -> str:
    if isinstance(v, str):
        s = v
    elif isinstance(v, list):
        s = " / ".join(_text(x, n) for x in v[:2])
    elif isinstance(v, dict):
        if v.get("attachments"):
            return "Adaptive card"
        s = _text(v.get("text") or v.get("speak") or v.get("displayName") or "", n)
    else:
        s = "" if v is None else str(v)
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def describe(node: dict) -> tuple[str, str, str]:
    """(label, detail, variant) for one action node."""
    k = node.get("kind", "?")
    dn = node.get("displayName")
    if k == "SendActivity":
        return dn or "Message", _text(node.get("activity")), "action"
    if k == "Question":
        var = str(node.get("variable", "")).removeprefix("init:")
        return dn or "Question", f"{_text(node.get('prompt'))} → {var}", "action"
    if k in ("SetVariable", "SetTextVariable", "ParseValue"):
        var = str(node.get("variable", "")).removeprefix("init:")
        return dn or "Set variable", f"{var} = {_text(node.get('value'), 70)}", "action"
    if k == "ConditionGroup":
        return dn or "Condition", f"{len(node.get('conditions') or [])} branch(es)", "branch"
    if k in ("BeginDialog", "ReplaceDialog"):
        target = str(node.get("dialog", "")).rsplit(".", 1)[-1]
        return dn or ("Redirect" if k == "BeginDialog" else "Replace topic"), target, "redirect"
    if k in ("InvokeFlowAction",):
        return dn or "Run flow", str(node.get("flowId", "")), "flow"
    if k in ("InvokeConnectorAction", "InvokeAIBuilderModelAction", "HttpRequestAction", "InvokeSkillAction"):
        return dn or k.removeprefix("Invoke"), str(node.get("operationId") or node.get("url") or ""), "flow"
    if k in ("SearchAndSummarizeContent", "AnswerQuestionWithAI", "SearchKnowledgeSources", "CreateSearchQuery"):
        return dn or {"SearchAndSummarizeContent": "Generative answers"}.get(k, k), _text(node.get("userInput") or node.get("variable")), "ai"
    if k in ("EndDialog", "EndConversation", "CancelAllDialogs"):
        return dn or k, "", "terminal"
    if k == "AdaptiveCardPrompt":
        return dn or "Adaptive card", _text(node.get("output") or ""), "action"
    return dn or k, "", "action"


def _entity(e: Any) -> tuple[str, list[str]]:
    if isinstance(e, str):
        return e.removesuffix("PrebuiltEntity") or "Text", []
    if isinstance(e, dict):
        d = e.get("definition") if e.get("kind") == "EmbeddedEntity" else e
        d = d if isinstance(d, dict) else {}
        items = [str(i.get("displayName") or i.get("id")) for i in d.get("items") or [] if isinstance(i, dict)]
        kind = str(d.get("kind") or e.get("kind") or "Entity")
        return ("Multiple choice" if "ClosedList" in kind else kind.removesuffix("Entity")), items
    return "Text", []


def _card_title(card: Any) -> str:
    try:
        import json as _json
        c = _json.loads(card) if isinstance(card, str) and card.strip().startswith("{") else card
        for b in (c or {}).get("body") or []:
            if isinstance(b, dict) and b.get("type") == "TextBlock" and b.get("text"):
                return str(b["text"])[:80]
    except (ValueError, AttributeError):
        pass
    return ""


def node_meta(node: dict, ws: Any = None) -> dict:
    """Content shown on the canvas card. `ws` (optional) resolves redirect / flow targets."""
    k = node.get("kind")
    m: dict[str, Any] = {}
    if k == "SendActivity":
        act = node.get("activity")
        m["text"] = _text(act, 300)
        if isinstance(act, dict) and act.get("attachments"):
            m["card"] = True
        if isinstance(act, dict) and isinstance(act.get("text"), list) and len(act["text"]) > 1:
            m["variants"] = len(act["text"])
    elif k == "Question":
        m["text"] = _text(node.get("prompt"), 300)
        m["entity"], m["options"] = _entity(node.get("entity"))
        m["variable"] = str(node.get("variable", "")).removeprefix("init:")
    elif k in ("SetVariable", "SetTextVariable", "ParseValue"):
        m["variable"] = str(node.get("variable", "")).removeprefix("init:")
        m["value"] = _text(node.get("value"), 120)
    elif k == "ConditionGroup":
        m["branches"] = [_text(c.get("condition") or c.get("displayName") or "", 80) for c in node.get("conditions") or []]
        m["hasElse"] = bool(node.get("elseActions"))
    elif k in ("BeginDialog", "ReplaceDialog"):
        ref = str(node.get("dialog", ""))
        m["target"] = ref.rsplit(".", 1)[-1]
        if ws is not None:
            from .refs import resolve_dialog
            t = resolve_dialog(ws, ref)
            if t is not None:
                m["target"], m["targetId"] = t.displayName, t.id
    elif k == "InvokeFlowAction":
        fid = str(node.get("flowId", ""))
        m["flow"] = fid
        if ws is not None:
            from .refs import workflow_ids
            wf = workflow_ids(ws).get(fid.lower())
            if wf is not None:
                m["flow"], m["targetId"] = wf.displayName, wf.id
        m["inputs"] = list(((node.get("input") or {}).get("binding") or {}).keys())
    elif k in ("InvokeConnectorAction", "HttpRequestAction", "InvokeAIBuilderModelAction", "InvokeSkillAction"):
        m["connector"] = str(node.get("connectionReference") or node.get("url") or "")
        m["operation"] = str(node.get("operationId") or node.get("method") or "")
    elif k in ("SearchAndSummarizeContent", "AnswerQuestionWithAI", "SearchKnowledgeSources", "CreateSearchQuery"):
        m["text"] = _text(node.get("userInput") or "", 120)
        if node.get("variable"):
            m["variable"] = str(node["variable"]).removeprefix("init:")
    elif k == "AdaptiveCardPrompt":
        m["text"] = _card_title(node.get("card"))
        m["inputs"] = list((((node.get("output") or {}).get("binding")) or {}).keys())
    return m


TRIGGER_LABELS = {
    "OnRecognizedIntent": "Trigger phrases", "OnConversationStart": "Conversation starts",
    "OnUnknownIntent": "No topic matched (fallback)", "OnEscalate": "User asks for a human", "OnError": "On error",
    "OnSystemRedirect": "Redirect only", "OnSelectIntent": "Multiple topics matched", "OnSignIn": "Sign-in required",
    "OnToolSelected": "Selected by orchestrator", "OnKnowledgeRequested": "Knowledge search requested",
    "OnGeneratedResponse": "AI response generated", "OnActivity": "Activity received", "OnRedirect": "Redirect",
}


def trigger_label(kind: str) -> str:
    return TRIGGER_LABELS.get(kind, kind.removeprefix("On"))


def _trigger(bd: dict) -> GNode:
    k = bd.get("kind", "Trigger")
    detail = ""
    intent = bd.get("intent") or {}
    if isinstance(intent, dict) and intent.get("triggerQueries"):
        q = intent["triggerQueries"]
        detail = f"{len(q)} phrase(s): " + _text(q[:3], 80)
    elif bd.get("description"):
        detail = _text(bd["description"])
    elif bd.get("condition"):
        detail = _text(bd["condition"])
    meta = {"phrases": [str(q) for q in (intent.get("triggerQueries") or [])[:6]] if isinstance(intent, dict) else [],
            "text": _text(bd.get("description") or "", 200)}
    return GNode(id="__trigger", kind=k, label=trigger_label(k), detail=detail, pointer="beginDialog", variant="trigger",
                 meta={k2: v for k2, v in meta.items() if v})


class _Builder:
    def __init__(self, ws: Any = None) -> None:
        self.ws = ws
        self.nodes: list[GNode] = []
        self.edges: list[GEdge] = []
        self.ids: set[str] = set()
        self.gotos: list[tuple[str, str]] = []

    def _nid(self, node: dict, pointer: str) -> str:
        nid = str(node.get("id") or pointer)
        if nid in self.ids:
            nid = f"{nid}@{pointer}"
        self.ids.add(nid)
        return nid

    def edge(self, src: str, tgt: str, label: str = "", variant: str = "default") -> None:
        self.edges.append(GEdge(id=f"e{len(self.edges)}", source=src, target=tgt, label=label, variant=variant))

    def seq(self, actions: list, preds: list[tuple[str, str, str]], pointer: str) -> list[tuple[str, str, str]]:
        """Lay out a list of actions; returns dangling exits (src, label, variant)."""
        for i, node in enumerate(actions or []):
            if not isinstance(node, dict):
                continue
            ptr = f"{pointer}/{i}"
            label, detail, variant = describe(node)
            nid = self._nid(node, ptr)
            self.nodes.append(GNode(id=nid, kind=node.get("kind", "?"), label=label, detail=detail,
                                    pointer=ptr, variant=variant, meta=node_meta(node, self.ws)))
            for src, lbl, v in preds:
                self.edge(src, nid, lbl, v)
            kind = node.get("kind")
            if kind == "ConditionGroup":
                exits: list[tuple[str, str, str]] = []
                for j, cond in enumerate(node.get("conditions") or []):
                    lbl = _text(cond.get("displayName") or cond.get("condition") or f"branch {j + 1}", 40)
                    exits += self.seq(cond.get("actions") or [], [(nid, lbl, "branch")], f"{ptr}/conditions/{j}/actions")
                exits += self.seq(node.get("elseActions") or [], [(nid, "else", "else")], f"{ptr}/elseActions")
                preds = exits
            elif kind in ("ActionScope", "Foreach"):
                loop = kind == "Foreach"
                inner = self.seq(node.get("actions") or [], [(nid, "each item" if loop else "", "branch" if loop else "default")],
                                 f"{ptr}/actions")
                preds = inner + [(nid, "done", "default")] if loop else (inner or [(nid, "", "default")])
            elif kind == "GotoAction":
                self.gotos.append((nid, str(node.get("actionId", ""))))
                preds = []
            elif kind in TERMINAL:
                preds = []
            else:
                preds = [(nid, "", "default")]
        return preds


def topic_graph(data: Any, ws: Any = None) -> Graph:
    b = _Builder(ws)
    if not isinstance(data, dict):
        return Graph(nodes=[], edges=[])
    bd = data.get("beginDialog") or {}
    b.nodes.append(_trigger(bd))
    b.ids.add("__trigger")
    exits = b.seq(bd.get("actions") or [], [("__trigger", "", "default")], "beginDialog/actions")
    # Extra triggers (e.g. OnActivity handlers on the same dialog)
    for t_i, trig in enumerate(data.get("triggers") or []):
        if isinstance(trig, dict):
            tid = f"__trigger{t_i}"
            b.nodes.append(GNode(id=tid, kind=trig.get("kind", "Trigger"), label=trigger_label(str(trig.get("kind", ""))),
                                 pointer=f"triggers/{t_i}", variant="trigger"))
            exits += b.seq(trig.get("actions") or [], [(tid, "", "default")], f"triggers/{t_i}/actions")
    for src, target in b.gotos:
        if target in b.ids:
            b.edge(src, target, "go to", "goto")
    if exits:
        b.nodes.append(GNode(id="__end", kind="End", label="End of topic", variant="end"))
        for src, lbl, v in exits:
            b.edge(src, "__end", lbl, v)
    return Graph(nodes=b.nodes, edges=b.edges)
