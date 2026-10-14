"""What handles a user message: trigger-phrase recognition (classic) or the generative planner.

Copilot Studio's NLU and orchestrator are cloud models, so this is the one place the simulator
approximates. It is prompted with exactly what the real planner sees — instructions, topic
`modelDescription` / trigger phrases, tool and child-agent descriptions, knowledge — and every
decision is returned with its reasoning so the Activity tab can show *why* something was picked.
With USE_MOCK_AI it falls back to deterministic word-overlap scoring (no network).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .. import config
from ..genai_client import complete_json
from ..workspace.model import Component, Workspace

_STOP = set("a an the i im i'm me my mine you your to of in on at for and or is are am be it its this that "
            "do does can could would please pls hi hello hey want need like with about how what some".split())
_WORD = re.compile(r"[a-z0-9']+")

ESCALATE_PHRASES = ["talk to agent", "talk to a person", "speak to a human", "talk to someone", "escalate",
                    "customer service", "human agent", "live agent"]


def terms(s: str) -> set[str]:
    return {w.strip("'") for w in _WORD.findall(s.lower()) if w.strip("'") and w not in _STOP}


def lexical(text: str, phrases: list[str]) -> float:
    """Best F1 word overlap between the message and any phrase (1.0 = same words)."""
    t = terms(text)
    if not t:
        return 0.0
    best = 0.0
    for p in phrases:
        if not p:
            continue
        if p.strip().lower() == text.strip().lower():
            return 1.0
        pt = terms(p)
        if not pt:
            continue
        hit = len(t & pt)
        best = max(best, 2 * hit / (len(t) + len(pt)))
    return round(best, 3)


# --- what the planner can see -------------------------------------------------------------------------
def trigger_kind(c: Component) -> str:
    bd = (c.data or {}).get("beginDialog") if isinstance(c.data, dict) else None
    return str((bd or {}).get("kind") or "")


def phrases(c: Component) -> list[str]:
    bd = (c.data or {}).get("beginDialog") or {}
    q = list(((bd.get("intent") or {}).get("triggerQueries") or [])) if isinstance(bd.get("intent"), dict) else []
    if trigger_kind(c) == "OnEscalate":
        q += ESCALATE_PHRASES
    return [str(x) for x in q]


def topic_description(c: Component) -> str:
    d = c.data if isinstance(c.data, dict) else {}
    bd = d.get("beginDialog") or {}
    return str(d.get("modelDescription") or bd.get("description") or "")


def topic_inputs(c: Component) -> list[dict]:
    props = (((c.data or {}).get("inputType") or {}).get("properties") or {}) if isinstance(c.data, dict) else {}
    return [{"name": k, "description": (v or {}).get("description", ""), "required": True} for k, v in props.items()]


def tool_info(c: Component) -> dict:
    d = c.data if isinstance(c.data, dict) else {}
    return {"id": c.id, "name": str(d.get("modelDisplayName") or c.displayName),
            "description": str(d.get("modelDescription") or ""),
            "inputs": [{"name": i.get("propertyName"), "description": i.get("description", ""),
                        "required": i.get("isRequired", i.get("shouldPromptUser", True)) is not False}
                       for i in d.get("inputs") or [] if isinstance(i, dict) and i.get("kind") != "ManualTaskInput"]}


def agent_info(c: Component) -> dict:
    d = c.data if isinstance(c.data, dict) else {}
    return {"id": c.id, "name": c.displayName, "description": str((d.get("beginDialog") or {}).get("description") or "")}


@dataclass
class Scope:
    """The orchestrator's view for one agent (main or child)."""
    name: str
    instructions: str
    topics: list[Component] = field(default_factory=list)
    tools: list[Component] = field(default_factory=list)
    agents: list[Component] = field(default_factory=list)
    knowledge: list[Component] = field(default_factory=list)
    child: bool = False


def scope_for(ws: Workspace, owner: str | None) -> Scope:
    def mine(c: Component) -> bool:
        return c.owner == owner

    if owner is None:
        ad = ws.agent.data if ws.agent and isinstance(ws.agent.data, dict) else {}
        name = ws.agent.displayName if ws.agent else ws.info.name
        instructions = str(ad.get("instructions") or "")
    else:
        child = next((c for c in ws.by_category("childAgent") if c.owner == owner), None)
        d = child.data if child and isinstance(child.data, dict) else {}
        name = child.displayName if child else owner
        instructions = str((d.get("settings") or {}).get("instructions") or d.get("instructions") or "")
    selectable = {"OnRecognizedIntent", "OnEscalate", "OnToolSelected"}
    return Scope(
        name=name, instructions=instructions,
        topics=[c for c in ws.by_category("topic") if mine(c) and trigger_kind(c) in selectable],
        tools=[c for c in ws.by_category("action") if mine(c)],
        agents=[c for c in ws.by_category("childAgent") if owner is None],
        knowledge=[c for c in ws.by_category("knowledge") if mine(c) or owner is not None and c.owner is None],
        child=owner is not None,
    )


# --- classic recognition -------------------------------------------------------------------------------
async def recognize(text: str, topics: list[Component], history: list[dict]) -> tuple[list[tuple[Component, float]], str, bool]:
    """Ranked (topic, score) list, the reasoning, and whether an LLM made the call."""
    cands = [c for c in topics if phrases(c)]
    if not cands:
        return [], "No topic has trigger phrases.", False
    lex = sorted(((c, lexical(text, phrases(c) + [c.displayName])) for c in cands), key=lambda x: x[1], reverse=True)
    if config.USE_MOCK_AI or not config.GENAI_API_KEY:
        return [x for x in lex if x[1] > 0], "Word-overlap with trigger phrases (mock mode).", False
    catalog = [{"id": c.id, "name": c.displayName, "triggerPhrases": phrases(c)[:12]} for c in cands]
    res = await complete_json(
        "You are the natural-language recognizer of a Microsoft Copilot Studio agent with CLASSIC orchestration. "
        "Score how well the user's message matches each topic's trigger phrases (semantic intent match, not exact words). "
        "Return JSON {\"matches\":[{\"id\":topicId,\"score\":0..1}], \"reason\": short}. Scores >= 0.7 mean confident.\n\n"
        f"Topics: {json.dumps(catalog)}\nRecent conversation: {json.dumps(history[-4:])}\nUser message: {text}",
        model=config.GENAI_FAST_MODEL)
    by_id = {c.id: c for c in cands}
    ranked = [(by_id[m["id"]], float(m.get("score", 0))) for m in res.get("matches") or []
              if isinstance(m, dict) and m.get("id") in by_id]
    ranked.sort(key=lambda x: x[1], reverse=True)
    return ranked, str(res.get("reason") or ""), True


# --- generative planner --------------------------------------------------------------------------------
PLANNER = """You are simulating Microsoft Copilot Studio's GENERATIVE ORCHESTRATION planner, so a maker can test their agent.
Behave like the real planner:
- Pick the topic, tool or child agent whose description best matches the user's intent. Topic descriptions
  and trigger phrases, tool descriptions and child-agent descriptions are all you know about them.
- Fill tool / topic inputs from the conversation. If a REQUIRED input is missing, ask the user for it
  (action "ask") with a short natural question - do not guess values.
- You may chain several steps (e.g. a tool, then respond using its result). Never invent tool results.
- If no topic/tool/agent fits, search knowledge (action "knowledge") when knowledge sources exist; otherwise
  respond directly following the agent instructions (action "respond").
- When you have what you need, finish with action "respond" and the final message to the user, written
  per the agent instructions. If you ran a topic, the topic already talked to the user: finish with "done".
Return ONLY JSON: {"thought": "why", "action": "topic|tool|agent|knowledge|ask|respond|done",
 "id": "<component id or null>", "inputs": {}, "query": "<knowledge query or null>", "message": "<text or null>"}"""


def catalog(sc: Scope) -> dict:
    return {
        "agent": sc.name,
        "instructions": sc.instructions[:6000],
        "topics": [{"id": c.id, "name": c.displayName, "description": topic_description(c),
                    "triggerPhrases": phrases(c)[:8], "inputs": topic_inputs(c)} for c in sc.topics],
        "tools": [tool_info(c) for c in sc.tools],
        "childAgents": [agent_info(c) for c in sc.agents],
        "knowledgeSources": [c.displayName for c in sc.knowledge],
    }


async def plan(sc: Scope, text: str, history: list[dict], observations: list[dict]) -> tuple[dict, bool]:
    if config.USE_MOCK_AI or not config.GENAI_API_KEY:
        return mock_plan(sc, text, observations), False
    prompt = (f"Agent catalog:\n{json.dumps(catalog(sc), ensure_ascii=False)}\n\n"
              f"Conversation so far:\n{json.dumps(history[-10:], ensure_ascii=False)}\n\n"
              f"Current user message: {text}\n\n"
              f"Steps already taken this turn (with results):\n{json.dumps(observations, ensure_ascii=False, default=str)[:8000]}\n\n"
              "Decide the next step.")
    res = await complete_json(prompt, system=PLANNER, model=config.GENAI_LLM_MODEL)
    return res, True


def mock_plan(sc: Scope, text: str, observations: list[dict]) -> dict:
    """Deterministic stand-in for the planner (USE_MOCK_AI): best word overlap wins."""
    if observations:
        last = observations[-1]
        if last["action"] == "knowledge":
            ans = (last.get("result") or {}).get("answer")
            return {"thought": "Answer from knowledge.", "action": "respond" if ans else "done", "message": ans}
        if last["action"] in ("topic", "agent"):
            return {"thought": "The topic handled the message.", "action": "done"}
        return {"thought": "Report the tool result.", "action": "respond",
                "message": f"[mock] Done. Result: {json.dumps(last.get('result'), default=str)[:300]}"}
    scored: list[tuple[float, str, Component]] = []
    for c in sc.topics:
        scored.append((lexical(text, phrases(c) + [topic_description(c), c.displayName]), "topic", c))
    for c in sc.tools:
        info = tool_info(c)
        scored.append((lexical(text, [info["name"], info["description"]]) * 0.9, "tool", c))
    for c in sc.agents:
        info = agent_info(c)
        scored.append((lexical(text, [info["name"], info["description"]]) * 0.9, "agent", c))
    scored.sort(key=lambda x: x[0], reverse=True)
    if scored and scored[0][0] >= 0.3:
        score, kind, c = scored[0]
        inputs = {}
        if kind == "tool":
            inputs = {i["name"]: text for i in tool_info(c)["inputs"]}
        return {"thought": f"[mock] best word overlap {score:.2f} with {kind} '{c.displayName}'.",
                "action": kind, "id": c.id, "inputs": inputs}
    if sc.knowledge:
        return {"thought": "[mock] Nothing matched; search knowledge.", "action": "knowledge", "query": text}
    if sc.child:
        return {"thought": "[mock] Child agent answers from its instructions.", "action": "respond",
                "message": f"[mock] {sc.name} would answer this following its instructions. "
                           "Turn off USE_MOCK_AI to get a real answer."}
    return {"thought": "[mock] Nothing matched.", "action": "done"}


async def extract_entity(entity: str, text: str, prompt: str) -> Any:
    """LLM entity extraction for prebuilt entities the simulator can't parse itself."""
    if config.USE_MOCK_AI or not config.GENAI_API_KEY:
        return text.strip() or None
    res = await complete_json(
        f"Extract a value of Copilot Studio entity type {entity} from the user's reply to the question "
        f"\"{prompt}\". Return JSON {{\"value\": <value or null if the reply does not contain one>}}.\n\nReply: {text}",
        model=config.GENAI_FAST_MODEL)
    return res.get("value")


async def tool_question(tool: dict, missing: list[dict]) -> str:
    names = ", ".join(str(m.get("description") or m.get("name")) for m in missing)
    return f"To {tool['name'].lower()}, I need: {names}. Could you provide that?"
