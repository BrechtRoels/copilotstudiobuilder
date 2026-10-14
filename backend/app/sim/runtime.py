"""Conversation runtime for the test pane: executes a workspace's topics like Copilot Studio does.

A `Session` holds the dialog stack (one `Frame` per active topic, each with nested `Block`s for
conditions / scopes / loops), Topic / Global / System variables and a pending Question or card.
`start()` and `user_message()` drive one turn and report everything through `emit()` as
`SimEvent`s — messages for the chat, `step` events with the node pointer (canvas highlighting),
routing / plan / tool / flow / knowledge traces for the Activity tab, and a variables snapshot.

System behaviour mirrors the service: Conversation Start greeting, Fallback with
System.FallbackCount, Escalate, Multiple Topics Matched, OnError with System.Error, Question
re-prompts / interruptions / default values, topic redirects with input and output binding.
Nodes the simulator can't execute emit a `warning` and are skipped — never silently.
"""
from __future__ import annotations

import json
import random
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from pydantic import BaseModel, Field

from .. import config
from ..genai_client import GenAIError
from ..graph.refs import resolve_dialog, workflow_ids
from ..graph.topic_graph import describe
from ..workspace.model import Component, Workspace
from . import flow_runner, knowledge, mocks, orchestrator as orch
from .powerfx import Env, FxError, _jsonable, eval_value, interpolate, is_blank, to_text
from .wdl import WdlError

MAX_STEPS = 1500

DEFAULT_GREETING = ("Hello, I'm {bot}, a virtual assistant. Just so you are aware, I sometimes use AI to answer "
                    "your questions. How can I help?")
DEFAULT_FALLBACK = "I'm sorry, I'm not sure how to help with that. Can you try rephrasing?"
DEFAULT_ESCALATE = ("Escalating to a representative is not currently configured for this agent, however this is "
                    "where the agent could provide information about how to get in touch with someone another way.")
DEFAULT_REPROMPT = "I'm sorry, I didn't understand that."

YES = {"yes", "y", "yeah", "yep", "yup", "sure", "ok", "okay", "true", "correct", "right", "affirmative", "of course",
       "absolutely", "definitely", "i do", "please do", "ja"}
NO = {"no", "n", "nope", "nah", "false", "not really", "negative", "i don't", "i dont", "don't", "dont", "never",
      "no thanks", "nee"}
NUM_WORDS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
             "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "twenty": 20, "hundred": 100}


class SimError(Exception):
    def __init__(self, message: str, code: str = "SimulationError"):
        super().__init__(message)
        self.code = code


class SimEvent(BaseModel):
    seq: int
    type: str
    data: dict = Field(default_factory=dict)
    componentId: str | None = None
    pointer: str | None = None
    ts: float = 0


@dataclass
class Block:
    actions: list
    index: int
    ptr: str
    loop: dict | None = None      # {"items", "i", "value", "index"} for Foreach


@dataclass
class Frame:
    comp: Component
    topic: dict
    blocks: list[Block]
    output_binding: dict | None = None
    trigger_text: str = ""
    select_intent: bool = False   # Multiple Topics Matched: start the chosen topic when it ends
    agent: str | None = None      # owning child agent (for messages)


@dataclass
class Pending:
    kind: str                     # question | card | csat | disambiguation
    frame: Frame | None
    node: dict
    ptr: str
    attempts: int = 0
    options: list = field(default_factory=list)


class Session:
    def __init__(self, ws: Workspace, user: dict | None = None):
        self.id = uuid.uuid4().hex[:16]
        self.ws = ws
        self.wid = ws.info.id
        self.created = time.time()
        self.touched = time.time()
        self.user = {"DisplayName": "Test User", "Email": "test.user@contoso.com", "Id": "test-user", "IsLoggedIn": True,
                     **(user or {})}
        self.events: list[SimEvent] = []
        self.listener: Callable[[SimEvent], None] | None = None
        self.generative = ws.generative_orchestration()
        self.reset()

    # --- state ----------------------------------------------------------------------------------------
    def reset(self) -> None:
        self.stack: list[Frame] = []
        self.pending: Pending | None = None
        self.history: list[dict] = []
        self.ended = False
        self.turn = 0
        self.fallback = 0
        self.steps = 0
        self.in_error = False
        self.sent_this_turn = 0
        self.last_topic: dict = {}
        self.conversation_id = uuid.uuid4().hex
        self.globals: dict[str, Any] = {}
        for c in self.ws.by_category("variable"):
            d = c.data if isinstance(c.data, dict) else {}
            name = str(d.get("name") or c.name)
            dv = d.get("defaultValue")
            try:
                self.globals[name] = eval_value(dv, self._env(None)) if dv is not None else None
            except FxError:
                self.globals[name] = dv
        bot = self.ws.agent.displayName if self.ws.agent else self.ws.info.name
        self.system: dict[str, Any] = {
            "Bot": {"Name": bot, "Id": self.ws.info.id},
            "Conversation": {"Id": self.conversation_id, "InTestMode": True, "LocalTimeZone": "UTC"},
            "Activity": {"Text": "", "ChannelId": "pva-studio", "Type": "message"},
            "LastMessage": {"Text": "", "Id": ""},
            "User": dict(self.user),
            "FallbackCount": 0,
            "Error": {},
            "Recognizer": {"IntentOptions": [], "SelectedIntent": None, "TriggerMessage": {"Text": ""}},
            "SignInReason": "",
        }

    def _env(self, frame: Frame | None) -> Env:
        return Env({"Topic": frame.topic if frame else {}, "Global": getattr(self, "globals", {}),
                    "System": getattr(self, "system", {}), "Env": {}})

    def emit(self, type_: str, frame: Frame | None = None, pointer: str | None = None, **data: Any) -> None:
        comp = frame.comp.id if frame else data.get("component")
        ev = SimEvent(seq=len(self.events), type=type_, data=_jsonable(data), componentId=comp, pointer=pointer,
                      ts=time.time())
        self.events.append(ev)
        if self.listener:
            self.listener(ev)

    def say(self, text: str, frame: Frame | None = None, attachments: list | None = None,
            suggestions: list | None = None, source: str | None = None, citations: list | None = None,
            agent: str | None = None) -> None:
        agent = agent or (frame.agent if frame else None)
        self.emit("message", frame, text=text, attachments=attachments or [], suggestions=suggestions or [],
                  source=source, agent=agent, citations=citations or [])
        self.history.append({"role": "assistant", "text": text or ("[card]" if attachments else "")})
        self.sent_this_turn += 1

    def _set(self, frame: Frame | None, var: str, value: Any) -> None:
        name = str(var).removeprefix("init:")
        scope, _, rest = name.partition(".")
        if scope == "Global":
            self.globals[rest] = value
        elif scope == "System":
            target = self.system
            parts = rest.split(".")
            for p in parts[:-1]:
                target = target.setdefault(p, {})
            target[parts[-1]] = value
        elif frame is not None:
            frame.topic[rest if scope == "Topic" else name] = value

    def _get(self, frame: Frame | None, var: str) -> Any:
        name = str(var).removeprefix("init:")
        scope, _, rest = name.partition(".")
        if scope == "Global":
            return self.globals.get(rest)
        if scope == "System":
            cur: Any = self.system
            for p in rest.split("."):
                cur = cur.get(p) if isinstance(cur, dict) else None
            return cur
        return (frame.topic if frame else {}).get(rest if scope == "Topic" else name)

    def snapshot(self) -> dict:
        top = self.stack[-1] if self.stack else None
        return {"topicName": top.comp.displayName if top else self.last_topic.get("name"),
                "Topic": dict(top.topic) if top else dict(self.last_topic.get("vars") or {}),
                "Global": dict(self.globals),
                "System": {k: self.system.get(k) for k in ("Activity", "Conversation", "User", "FallbackCount", "Error",
                                                           "Recognizer", "Bot", "LastMessage")}}

    # --- turn entry points ----------------------------------------------------------------------------
    async def start(self) -> None:
        self.emit("notice", text=f"Conversation started · {'generative' if self.generative else 'classic'} orchestration",
                  orchestration="generative" if self.generative else "classic")
        t = self._system_topic("OnConversationStart")
        if t:
            await self._begin_topic(t, reason="Conversation start")
        else:
            self.say(DEFAULT_GREETING.format(bot=self.system["Bot"]["Name"]), source="default Conversation Start")
        self._finish_turn()

    async def user_message(self, text: str | None = None, card: dict | None = None) -> None:
        self.touched = time.time()
        self.sent_this_turn = 0
        if self.ended:
            self.reset()
            self.emit("notice", text="The previous conversation ended — started a new one.")
        self.turn += 1
        shown = text if text is not None else json.dumps(card or {})
        self.history.append({"role": "user", "text": shown})
        self.system["Activity"]["Text"] = text if text is not None else ""
        self.system["Activity"]["Value"] = card
        self.system["LastMessage"] = {"Text": shown, "Id": uuid.uuid4().hex[:8]}
        self.system["Recognizer"]["TriggerMessage"] = {"Text": shown}
        try:
            if self.pending:
                await self._answer_pending(text, card)
            elif self.generative:
                await self._generative(text or shown, orch.scope_for(self.ws, None), None, 0)
            else:
                await self._classic(text or shown)
        except GenAIError as e:
            self.emit("error", text=f"PwC GenAI call failed: {e}", code="GenAIError")
            self.say("(The simulator could not reach PwC GenAI for routing — check your API key or use mock mode.)",
                     source="simulator")
        self._finish_turn()

    def _finish_turn(self) -> None:
        self.emit("variables", **self.snapshot())
        awaiting = self.pending.kind if self.pending else None
        self.emit("done", awaiting=awaiting, ended=self.ended, stack=[f.comp.displayName for f in self.stack])

    # --- routing ----------------------------------------------------------------------------------------
    def _system_topic(self, trigger: str, owner: str | None = None) -> Component | None:
        return next((c for c in self.ws.by_category("topic")
                     if c.owner == owner and orch.trigger_kind(c) == trigger), None)

    def _intent_topics(self) -> list[Component]:
        return [c for c in self.ws.by_category("topic") if c.owner is None
                and orch.trigger_kind(c) in ("OnRecognizedIntent", "OnEscalate") and self._trigger_condition(c)]

    def _trigger_condition(self, c: Component) -> bool:
        cond = ((c.data or {}).get("beginDialog") or {}).get("condition")
        if not cond:
            return True
        try:
            return bool(eval_value(cond, self._env(None)))
        except FxError:
            return True

    async def _classic(self, text: str) -> None:
        ranked, reason, ai = await orch.recognize(text, self._intent_topics(), self.history)
        threshold = 0.7 if ai else 0.5
        confident = [(c, s) for c, s in ranked if s >= threshold]
        self.emit("routing", mode="classic", ai=ai, reason=reason, threshold=threshold,
                  candidates=[{"id": c.id, "name": c.displayName, "score": s} for c, s in ranked[:4]])
        if not confident:
            await self._fallback(text)
            return
        if len(confident) > 1 and confident[1][1] >= confident[0][1] - 0.1:
            await self._disambiguate(text, confident[:3])
            return
        await self._begin_topic(confident[0][0], reason=f"Trigger phrase match ({confident[0][1]:.2f})", trigger_text=text)

    async def _fallback(self, text: str) -> None:
        self.fallback += 1
        self.system["FallbackCount"] = self.fallback
        t = self._system_topic("OnUnknownIntent")
        if t:
            await self._begin_topic(t, reason=f"No topic matched · System.FallbackCount = {self.fallback}", trigger_text=text,
                                    keep_fallback=True)
        elif self.fallback >= 3:
            await self._escalate("Fallback count reached 3")
        else:
            self.say(DEFAULT_FALLBACK, source="default Fallback")

    async def _escalate(self, reason: str) -> None:
        t = self._system_topic("OnEscalate")
        if t:
            await self._begin_topic(t, reason=reason)
        else:
            self.say(DEFAULT_ESCALATE, source="default Escalate")

    async def _disambiguate(self, text: str, options: list[tuple[Component, float]]) -> None:
        opts = [{"DisplayName": c.displayName, "TopicId": c.id, "TriggerId": "main", "Score": s} for c, s in options]
        self.system["Recognizer"]["IntentOptions"] = opts
        t = self._system_topic("OnSelectIntent")
        if t:
            await self._begin_topic(t, reason="Multiple topics matched", trigger_text=text, select_intent=True)
            return
        self.emit("routing", mode="classic", reason="Multiple topics matched (built-in disambiguation)")
        self.say("To clarify, did you mean:", suggestions=[o["DisplayName"] for o in opts] + ["None of these"],
                 source="default Multiple Topics Matched")
        self.pending = Pending("disambiguation", None, {}, "", options=opts)

    async def _begin_topic(self, comp: Component, *, reason: str = "", trigger_text: str = "", inputs: dict | None = None,
                           output_binding: dict | None = None, select_intent: bool = False, keep_fallback: bool = False,
                           agent: str | None = None, replace: bool = False) -> None:
        d = comp.data if isinstance(comp.data, dict) else {}
        if comp.category == "childAgent" or d.get("kind") == "AgentDialog":
            await self._generative(trigger_text or self.system["Activity"]["Text"], orch.scope_for(self.ws, comp.owner),
                                   comp, 1)
            return
        if not keep_fallback and orch.trigger_kind(comp) == "OnRecognizedIntent":
            self.fallback = 0
            self.system["FallbackCount"] = 0
        bd = d.get("beginDialog") or {}
        frame = Frame(comp=comp, topic=dict(inputs or {}), blocks=[Block(bd.get("actions") or [], 0, "beginDialog/actions")],
                      output_binding=output_binding, trigger_text=trigger_text, select_intent=select_intent,
                      agent=agent or comp.owner)
        if replace and self.stack:
            old = self.stack.pop()
            self.emit("topic", old, action="end", name=old.comp.displayName, reason="replaced")
        self.stack.append(frame)
        self.emit("topic", frame, pointer="beginDialog", action="start", name=comp.displayName, reason=reason,
                  trigger=orch.trigger_kind(comp))
        await self._run()

    # --- execution loop ---------------------------------------------------------------------------------
    async def _run(self) -> None:
        while self.stack:
            if self.pending:
                return
            self.steps += 1
            if self.steps > MAX_STEPS:
                await self._error(self.stack[-1], "", SimError("Too many steps in one conversation — check GotoAction / "
                                                                "RepeatDialog loops", "LoopLimit"))
                return
            fr = self.stack[-1]
            if not fr.blocks:
                await self._end_frame()
                continue
            blk = fr.blocks[-1]
            if blk.index >= len(blk.actions):
                if blk.loop and self._next_iteration(fr, blk):
                    continue
                fr.blocks.pop()
                continue
            node = blk.actions[blk.index]
            ptr = f"{blk.ptr}/{blk.index}"
            blk.index += 1
            if not isinstance(node, dict) or node.get("disabled") is True:
                continue
            label = describe(node)[0]
            self.emit("step", fr, ptr, kind=node.get("kind"), label=label, id=node.get("id"))
            try:
                outcome = await self._exec(fr, node, ptr)
            except (FxError, SimError, WdlError) as e:
                await self._error(fr, ptr, e)
                return
            if outcome == "wait":
                return
            if outcome == "stop":
                return

    async def _end_frame(self) -> None:
        fr = self.stack.pop()
        self.last_topic = {"name": fr.comp.displayName, "vars": dict(fr.topic)}
        self.emit("topic", fr, action="end", name=fr.comp.displayName)
        caller = self.stack[-1] if self.stack else None
        if fr.output_binding and caller is not None:
            for out_name, target in fr.output_binding.items():
                self._set(caller, str(target), fr.topic.get(out_name))
        if fr.select_intent:
            sel = self.system["Recognizer"].get("SelectedIntent")
            tid = sel.get("TopicId") if isinstance(sel, dict) else None
            chosen = self.ws.get(tid) if tid else None
            if chosen is not None:
                await self._begin_topic(chosen, reason="Selected in Multiple Topics Matched", trigger_text=fr.trigger_text)

    def _next_iteration(self, fr: Frame, blk: Block) -> bool:
        loop = blk.loop or {}
        loop["i"] += 1
        if loop["i"] >= len(loop["items"]):
            return False
        self._loop_vars(fr, loop)
        blk.index = 0
        return True

    def _loop_vars(self, fr: Frame, loop: dict) -> None:
        item = loop["items"][loop["i"]]
        if loop.get("value"):
            self._set(fr, loop["value"], item.get("Value") if isinstance(item, dict) and set(item) == {"Value"} else item)
        if loop.get("index"):
            self._set(fr, loop["index"], loop["i"])

    async def _error(self, fr: Frame | None, ptr: str, e: Exception) -> None:
        code = getattr(e, "code", None) or ("PowerFxError" if isinstance(e, FxError) else
                                            "FlowActionException" if isinstance(e, WdlError) else "SimulationError")
        self.system["Error"] = {"Message": str(e), "Code": code}
        self.emit("error", fr, ptr, text=str(e), code=code)
        self.stack.clear()
        self.pending = None
        t = self._system_topic("OnError")
        if t and not self.in_error:
            self.in_error = True
            try:
                await self._begin_topic(t, reason=f"Error: {code}")
            finally:
                self.in_error = False
                self.stack.clear()
                self.pending = None
        else:
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
            self.say(f"An error has occurred.\nError code: {code}\nConversation Id: {self.conversation_id}\nTime (UTC): {now}.",
                     source="default On Error")

    # --- nodes --------------------------------------------------------------------------------------------
    async def _exec(self, fr: Frame, n: dict, ptr: str) -> str | None:
        k = n.get("kind")
        env = self._env(fr)
        if k == "SendActivity":
            self._send_activity(fr, n.get("activity"), env)
        elif k == "Question":
            return await self._question(fr, n, ptr)
        elif k in ("SetVariable",):
            self._set(fr, n.get("variable", ""), eval_value(n.get("value"), env))
        elif k == "SetTextVariable":
            v = n.get("value")
            self._set(fr, n.get("variable", ""), interpolate(str(v), env) if isinstance(v, str) and not v.startswith("=")
                      else eval_value(v, env))
        elif k == "SetMultipleVariables":
            for a in n.get("assignments") or []:
                self._set(fr, a.get("variable", ""), eval_value(a.get("value"), self._env(fr)))
        elif k == "ParseValue":
            v = eval_value(n.get("value"), env)
            if isinstance(v, str):
                try:
                    v = json.loads(v)
                except json.JSONDecodeError:
                    pass
            self._set(fr, n.get("variable", ""), v)
        elif k == "ResetVariable":
            self._set(fr, n.get("variable", ""), None)
        elif k == "ClearAllVariables":
            which = str(n.get("variables") or "")
            if "Global" in which or "Conversation" in which or not which:
                for g in self.globals:
                    self.globals[g] = None
            self.emit("notice", fr, ptr, text=f"Cleared variables ({which or 'all'})")
        elif k in ("EditTable", "EditTableV2"):
            self._edit_table(fr, n, env)
        elif k == "ConditionGroup":
            for j, cond in enumerate(n.get("conditions") or []):
                if bool(eval_value(cond.get("condition", "=false"), env)):
                    fr.blocks.append(Block(cond.get("actions") or [], 0, f"{ptr}/conditions/{j}/actions"))
                    self.emit("branch", fr, ptr, taken=f"{ptr}/conditions/{j}", label=cond.get("displayName") or cond.get("condition"))
                    break
            else:
                fr.blocks.append(Block(n.get("elseActions") or [], 0, f"{ptr}/elseActions"))
                self.emit("branch", fr, ptr, taken=f"{ptr}/elseActions", label="else")
        elif k == "ActionScope":
            fr.blocks.append(Block(n.get("actions") or [], 0, f"{ptr}/actions"))
        elif k == "Foreach":
            items = eval_value(n.get("items"), env)
            items = items if isinstance(items, list) else ([] if items is None else [items])
            if items:
                loop = {"items": items, "i": 0, "value": n.get("value"), "index": n.get("index")}
                self._loop_vars(fr, loop)
                fr.blocks.append(Block(n.get("actions") or [], 0, f"{ptr}/actions", loop))
        elif k in ("BreakLoop", "ContinueLoop"):
            while fr.blocks and not fr.blocks[-1].loop:
                fr.blocks.pop()
            if fr.blocks:
                if k == "BreakLoop":
                    fr.blocks.pop()
                else:
                    fr.blocks[-1].index = len(fr.blocks[-1].actions)
        elif k == "GotoAction":
            self._goto(fr, str(n.get("actionId", "")))
        elif k in ("BeginDialog", "ReplaceDialog"):
            await self._redirect(fr, n, ptr, replace=k == "ReplaceDialog")
            return "stop" if self.pending else None
        elif k == "RepeatDialog":
            bd = (fr.comp.data or {}).get("beginDialog") or {}
            fr.blocks = [Block(bd.get("actions") or [], 0, "beginDialog/actions")]
        elif k == "EndDialog":
            fr.blocks.clear()
        elif k in ("CancelAllDialogs", "CancelDialog"):
            self.emit("notice", fr, ptr, text="All topics cancelled")
            self.stack.clear()
            return "stop"
        elif k == "EndConversation":
            self.emit("notice", fr, ptr, text="Conversation ended")
            self.stack.clear()
            self.ended = True
            return "stop"
        elif k in ("TransferConversation", "TransferConversationV2"):
            self.emit("notice", fr, ptr, text="Transferred to a live agent (simulated — no handoff happens in the test pane)")
            self.stack.clear()
            self.ended = True
            return "stop"
        elif k == "InvokeFlowAction":
            await self._invoke_flow(fr, n, ptr, env)
        elif k in ("InvokeConnectorAction", "HttpRequestAction", "InvokeAIBuilderModelAction", "InvokeSkillAction",
                   "InvokeCustomModelAction", "InvokeMcpToolAction"):
            await self._invoke_mocked(fr, n, ptr, env)
        elif k in ("SearchAndSummarizeContent", "AnswerQuestionWithAI", "SearchAndSummarizeWithCustomModel",
                   "SearchKnowledgeSources"):
            await self._knowledge_node(fr, n, ptr, env)
        elif k == "CreateSearchQuery":
            q = eval_value(n.get("userInput") or "=System.Activity.Text", env)
            self._set(fr, n.get("result") or n.get("variable") or "Topic.SearchQuery", q)
        elif k == "AdaptiveCardPrompt":
            card = self._card(n.get("card"), env)
            self.say("", fr, attachments=[card], source="Adaptive card")
            self.pending = Pending("card", fr, n, ptr)
            return "wait"
        elif k == "CSATQuestion":
            self.say("How would you rate your experience?", fr, suggestions=["1", "2", "3", "4", "5"])
            self.pending = Pending("csat", fr, n, ptr)
            return "wait"
        elif k == "OAuthInput":
            if not self.user.get("IsLoggedIn"):
                self.emit("notice", fr, ptr, text="Sign-in card (simulated) — test user signed in automatically")
                self.user["IsLoggedIn"] = True
                self.system["User"]["IsLoggedIn"] = True
            if n.get("tokenVariable"):
                self._set(fr, n["tokenVariable"], "simulated-access-token")
        elif k == "SignOutUser":
            self.user["IsLoggedIn"] = False
            self.system["User"]["IsLoggedIn"] = False
            self.emit("notice", fr, ptr, text="User signed out (simulated)")
        elif k == "RecognizeIntent":
            text = to_text(eval_value(n.get("userInput") or "=System.Activity.Text", env))
            ranked, _, _ = await orch.recognize(text, self._intent_topics(), self.history)
            if n.get("variable"):
                self._set(fr, n["variable"], {"TopicId": ranked[0][0].id, "Score": ranked[0][1]} if ranked else None)
        elif k in ("LogCustomTelemetryEvent", "EmitEvent", "DeleteActivity", "UpdateActivity", "DisableTrigger",
                   "ActivateExternalTrigger", "GetActivityMembers", "GetConversationMembers", "SetConversationTopic"):
            self.emit("notice", fr, ptr, text=f"{k} — recorded, no effect in the test pane",
                      detail=_jsonable({kk: vv for kk, vv in n.items() if kk not in ("kind", "id")}))
        else:
            self.emit("warning", fr, ptr, text=f"Node kind '{k}' is not simulated — skipped")
        return None

    def _send_activity(self, fr: Frame, act: Any, env: Env) -> None:
        text, attachments, suggestions = self._activity(act, env)
        self.say(text, fr, attachments=attachments, suggestions=suggestions)

    def _activity(self, act: Any, env: Env) -> tuple[str, list, list]:
        attachments: list = []
        suggestions: list = []
        if isinstance(act, list):
            act = random.choice(act) if act else ""
        if isinstance(act, dict):
            raw = act.get("text")
            if isinstance(raw, list):
                raw = random.choice(raw) if raw else ""
            text = str(raw or "")
            for a in act.get("attachments") or []:
                content = a.get("cardContent") if isinstance(a, dict) else None
                if content is not None:
                    attachments.append(self._card(content, env))
            for qr in act.get("quickReplies") or act.get("suggestedActions") or []:
                if isinstance(qr, dict):
                    suggestions.append(str(qr.get("title") or qr.get("text") or qr.get("value") or ""))
                elif qr:
                    suggestions.append(str(qr))
        else:
            text = "" if act is None else str(act)
        if text.startswith("="):
            text = to_text(eval_value(text, env))
        else:
            text = interpolate(text, env)
        return text, attachments, suggestions

    def _card(self, content: Any, env: Env) -> dict:
        if isinstance(content, dict):
            return _jsonable(eval_value(content, env))
        s = str(content or "").strip()
        if s.startswith("="):
            v = eval_value(s, env)
            if isinstance(v, str):
                v = json.loads(v)
            return _jsonable(v)
        try:
            return json.loads(s)
        except json.JSONDecodeError as e:
            raise SimError(f"Adaptive card JSON is invalid: {e}", "InvalidCard")

    def _edit_table(self, fr: Frame, n: dict, env: Env) -> None:
        var = n.get("itemsVariable") or n.get("variable") or ""
        table = list(self._get(fr, var) or [])
        change = str(n.get("changeType") or "Add")
        if change == "Add":
            table.append(eval_value(n.get("value"), env))
        elif change == "Clear":
            table = []
        elif change == "RemoveFirst" and table:
            table.pop(0)
        elif change == "RemoveLast" and table:
            table.pop()
        elif change == "Remove":
            v = eval_value(n.get("value"), env)
            table = [r for r in table if r != v]
        self._set(fr, var, table)

    def _goto(self, fr: Frame, target: str) -> None:
        bd = (fr.comp.data or {}).get("beginDialog") or {}
        path = _find_path(bd.get("actions") or [], target, "beginDialog/actions")
        if path is None:
            raise SimError(f"GotoAction target '{target}' not found in this topic", "GotoTargetNotFound")
        fr.blocks = [Block(a, i, p) for a, i, p in path]

    async def _redirect(self, fr: Frame, n: dict, ptr: str, replace: bool) -> None:
        ref = str(n.get("dialog", ""))
        target = resolve_dialog(self.ws, ref)
        if target is None:
            raise SimError(f"Redirect target '{ref}' does not exist in this workspace", "TopicNotFound")
        env = self._env(fr)
        inputs = {k: eval_value(v, env) for k, v in (((n.get("input") or {}).get("binding")) or {}).items()}
        binding = ((n.get("output") or {}).get("binding")) or None
        if replace:
            self.stack.remove(fr)
            self.emit("topic", fr, action="end", name=fr.comp.displayName, reason="replaced")
        await self._begin_topic(target, reason=f"{'Replaced by' if replace else 'Redirect from'} {fr.comp.displayName}",
                                trigger_text=fr.trigger_text, inputs=inputs, output_binding=None if replace else binding)

    # --- questions ------------------------------------------------------------------------------------------
    async def _question(self, fr: Frame, n: dict, ptr: str) -> str | None:
        var = str(n.get("variable") or "")
        always = n.get("alwaysPrompt") is True
        if not always and var and not is_blank(self._get(fr, var)):
            self.emit("notice", fr, ptr, text=f"Question skipped — {var.removeprefix('init:')} already has a value")
            return None
        ent, options = self._entity(fr, n.get("entity"))
        if fr.trigger_text and not always and ent not in ("StringPrebuiltEntity", "BooleanPrebuiltEntity", "") \
                and not ent.startswith("Dynamic"):
            val = _local_entity(ent, options, fr.trigger_text)
            if val is not None:
                self._set(fr, var, val)
                self.emit("notice", fr, ptr, text=f"{var.removeprefix('init:')} filled from the triggering message (slot filling)")
                return None
        self._prompt(fr, n, options)
        self.pending = Pending("question", fr, n, ptr, options=options)
        return "wait"

    def _prompt(self, fr: Frame, n: dict, options: list, prefix: str = "") -> None:
        text, attachments, suggestions = self._activity(n.get("prompt"), self._env(fr))
        if options and not suggestions:
            suggestions = [o["title"] for o in options]
        self.say(f"{prefix}{text}" if prefix else text, fr, attachments=attachments, suggestions=suggestions)

    def _entity(self, fr: Frame, ent: Any) -> tuple[str, list[dict]]:
        """(entity name, closed-list options [{title, value, synonyms}])."""
        if isinstance(ent, str) or ent is None:
            return str(ent or "StringPrebuiltEntity"), []
        kind = str(ent.get("kind", ""))
        if kind == "EmbeddedEntity":
            return self._entity(fr, ent.get("definition"))
        if kind == "ClosedListEntity":
            return "ClosedListEntity", [{"title": str(i.get("displayName") or i.get("id")), "value": i.get("id") or i.get("displayName"),
                                         "synonyms": [str(s) for s in (i.get("synonyms") or [])]}
                                        for i in ent.get("items") or [] if isinstance(i, dict)]
        if kind == "DynamicClosedListEntity":
            rows = eval_value(ent.get("items"), self._env(fr)) or []
            return "DynamicClosedListEntity", [{"title": to_text((r or {}).get("DisplayName") or (r or {}).get("Value") or r),
                                                "value": r, "synonyms": []} for r in rows]
        if kind == "ClosedListEntityReference":
            ref = str(ent.get("entityReference") or "")
            tail = ref.rsplit(".", 1)[-1].lower()
            for c in self.ws.components:
                d = c.data if isinstance(c.data, dict) else {}
                if d.get("kind") in ("ClosedListEntity", "EntityComponent") and tail in (c.name.lower(), c.displayName.lower()):
                    return self._entity(fr, d.get("definition") or d)
            return f"Entity '{ref}'", []
        if kind == "RegexEntity":
            return "RegexEntity:" + str(ent.get("pattern", "")), []
        return kind or "StringPrebuiltEntity", []

    async def _answer_pending(self, text: str | None, card: dict | None) -> None:
        p = self.pending
        assert p is not None
        if p.kind == "disambiguation":
            self.pending = None
            chosen = _match_option([{"title": o["DisplayName"], "value": o, "synonyms": []} for o in p.options], text or "")
            if chosen and isinstance(chosen, dict):
                comp = self.ws.get(chosen["TopicId"])
                if comp:
                    await self._begin_topic(comp, reason="Selected in Multiple Topics Matched", trigger_text=text or "")
                    return
            await self._fallback(text or "")
            return
        fr = p.frame
        if p.kind == "card":
            if card is not None:
                self.pending = None
                binding = ((p.node.get("output") or {}).get("binding")) or {}
                for key, target in binding.items():
                    self._set(fr, str(target), card.get(key))
                self.emit("notice", fr, p.ptr, text="Card submitted", data=card)
                await self._run()
                return
            if await self._try_interrupt(p, text or ""):
                return
            await self._reprompt(p, "Please respond using the card above.")
            return
        if p.kind == "csat":
            m = re.search(r"[1-5]", text or "")
            if m:
                self.pending = None
                if p.node.get("variable"):
                    self._set(fr, p.node["variable"], int(m.group()))
                self.say("Thanks for your feedback.", fr)
                await self._run()
                return
            await self._reprompt(p, "Please choose a rating from 1 to 5.")
            return
        # question
        ent, options = self._entity(fr, p.node.get("entity"))
        options = p.options or options
        value = _local_entity(ent, options, text or "")
        if value is None and not options and ent not in ("StringPrebuiltEntity", "BooleanPrebuiltEntity",
                                                         "NumberPrebuiltEntity", "EMailPrebuiltEntity") \
                and not ent.startswith("RegexEntity"):
            value = await orch.extract_entity(ent, text or "", self._activity(p.node.get("prompt"), self._env(fr))[0])
        if value is not None:
            var = str(p.node.get("variable") or "")
            ok = True
            validation = p.node.get("validation")
            if isinstance(validation, dict) and validation.get("condition"):
                prev = self._get(fr, var)
                self._set(fr, var, value)
                ok = bool(eval_value(validation["condition"], self._env(fr)))
                if not ok:
                    self._set(fr, var, prev)
            if ok:
                self._set(fr, var, value)
                self.pending = None
                self.emit("notice", fr, p.ptr, text=f"{var.removeprefix('init:')} = {to_text(value)[:120]}")
                await self._run()
                return
            inv = p.node.get("invalidPrompt")
            await self._reprompt(p, self._activity(inv, self._env(fr))[0] if inv else DEFAULT_REPROMPT)
            return
        if await self._try_interrupt(p, text or ""):
            return
        unrec = p.node.get("unrecognizedPrompt")
        await self._reprompt(p, self._activity(unrec, self._env(fr))[0] if unrec else DEFAULT_REPROMPT)

    async def _reprompt(self, p: Pending, message: str) -> None:
        p.attempts += 1
        limit = int(p.node.get("repeatCount", 2) if p.node.get("repeatCount") is not None else 2)
        fr = p.frame
        if p.attempts > limit:
            self.pending = None
            if "defaultValue" in p.node and p.kind == "question":
                val = eval_value(p.node.get("defaultValue"), self._env(fr))
                self._set(fr, str(p.node.get("variable") or ""), val)
                if p.node.get("defaultValueResponse"):
                    self._send_activity(fr, p.node["defaultValueResponse"], self._env(fr))
                self.emit("notice", fr, p.ptr, text=f"No valid answer after {limit} re-prompts — default value used")
                await self._run()
                return
            self.emit("notice", fr, p.ptr, text=f"No valid answer after {limit} re-prompts — escalating")
            self.stack.clear()
            await self._escalate("Question could not be answered")
            return
        if p.kind == "question":
            self.pending = None
            self._prompt(fr, p.node, p.options, prefix=f"{message} " if message else "")
            self.pending = p
        else:
            self.say(message, fr)

    async def _try_interrupt(self, p: Pending, text: str) -> bool:
        pol = p.node.get("interruptionPolicy")
        if isinstance(pol, dict) and pol.get("allowInterruption") is False:
            return False
        if not text.strip():
            return False
        ranked, reason, ai = await orch.recognize(text, self._intent_topics(), self.history)
        threshold = 0.7 if ai else 0.6
        top = next(((c, s) for c, s in ranked if s >= threshold and (p.frame is None or c.id != p.frame.comp.id)), None)
        if not top:
            return False
        self.emit("routing", mode="interruption", reason=f"'{top[0].displayName}' matched while waiting for an answer ({top[1]:.2f})",
                  candidates=[{"id": c.id, "name": c.displayName, "score": s} for c, s in ranked[:3]], ai=ai)
        if p.frame is not None and p.frame.blocks:
            p.frame.blocks[-1].index -= 1        # re-ask the interrupted question when we come back
        self.pending = None
        await self._begin_topic(top[0], reason="Topic switch (interruption)", trigger_text=text)
        return True

    # --- flows, connectors, knowledge ------------------------------------------------------------------
    async def _invoke_flow(self, fr: Frame | None, n: dict, ptr: str, env: Env) -> dict:
        flow_id = str(n.get("flowId", ""))
        wf = workflow_ids(self.ws).get(flow_id.lower())
        if wf is None:
            raise SimError(f"Flow '{flow_id}' is not in this workspace", "FlowNotFound")
        inputs = {k: eval_value(v, env) for k, v in (((n.get("input") or {}).get("binding")) or {}).items()}
        run = await flow_runner.run_flow(self.ws.info.path, wf, inputs)
        flow_runner.remember(self.wid, run)
        self.emit("flow", fr, ptr, runId=run.id, flow=wf.displayName, flowComponent=wf.id, status=run.status,
                  inputs=inputs, outputs=run.outputs, error=run.error, durationMs=run.durationMs,
                  actions=[{"name": a.name, "pointer": a.pointer, "status": a.status, "source": a.source} for a in run.actions])
        if run.status != "Succeeded":
            raise SimError(f"Flow '{wf.displayName}' failed: {(run.error or {}).get('message', run.status)}", "FlowActionException")
        body = (run.response or {}).get("body") if isinstance((run.response or {}).get("body"), dict) else {}
        low = {str(k).lower(): v for k, v in (body or {}).items()}
        low.update({str(k).lower(): v for k, v in run.outputs.items()})
        for out_name, target in (((n.get("output") or {}).get("binding")) or {}).items():
            self._set(fr, str(target), low.get(str(out_name).lower()))
        return run.outputs

    async def _invoke_mocked(self, fr: Frame, n: dict, ptr: str, env: Env) -> None:
        k = n.get("kind")
        inputs = eval_value(n.get("input") or {}, env)
        action = {"type": k, "inputs": {"host": {"apiId": _api_from_ref(str(n.get("connectionReference") or "")),
                                                 "operationId": n.get("operationId") or ""}}}
        r = await mocks.connector_result(ws_path=self.ws.info.path, component_id=fr.comp.id, name=str(n.get("id") or ptr),
                                         action=action, inputs=inputs, override=None, static_results={},
                                         saved=mocks.load(self.ws.info.path))
        self.emit("tool", fr, ptr, name=describe(n)[0], kind=k, inputs=inputs, outputs=r.outputs, source=r.source, status=r.status)
        if r.status != "Succeeded":
            raise SimError(f"{k} failed: {(r.error or {}).get('message')}", "ConnectorActionFailed")
        body = (r.outputs or {}).get("body") if isinstance(r.outputs, dict) else r.outputs
        if k == "HttpRequestAction" and n.get("response"):
            self._set(fr, n["response"], body)
        out = n.get("output")
        if isinstance(out, dict) and isinstance(out.get("binding"), dict):
            low = {str(kk).lower(): vv for kk, vv in (body or {}).items()} if isinstance(body, dict) else {}
            for key, target in out["binding"].items():
                self._set(fr, str(target), low.get(str(key).lower(), body if len(out["binding"]) == 1 else None))
        elif isinstance(out, str):
            self._set(fr, out, body)

    async def _knowledge_node(self, fr: Frame, n: dict, ptr: str, env: Env) -> None:
        query = to_text(eval_value(n.get("userInput") or "=System.Activity.Text", env))
        only = [str(s).rsplit(".", 1)[-1] for s in (n.get("knowledgeSources") or {}).get("knowledgeSources", [])] \
            if isinstance(n.get("knowledgeSources"), dict) else None
        ad = self.ws.agent.data if self.ws.agent and isinstance(self.ws.agent.data, dict) else {}
        self.emit("typing")
        res = await knowledge.answer(self.ws, query, str(ad.get("instructions") or ""), only or None,
                                     str(n.get("additionalInstructions") or ""))
        self.emit("knowledge", fr, ptr, **res)
        if n.get("variable"):
            self._set(fr, n["variable"], res.get("answer"))
        if res.get("answer") and n.get("autoSend", True) is not False:
            self.say(res["answer"], fr, source="Generative answers", citations=res.get("citations"))

    # --- generative orchestration -------------------------------------------------------------------------
    async def _generative(self, text: str, sc: orch.Scope, child: Component | None, depth: int) -> None:
        agent_label = child.displayName if child else None
        if child is not None:
            self.emit("agent", component=child.id, action="start", name=child.displayName)
        observations: list[dict] = []
        sent_before = self.sent_this_turn
        for _ in range(6):
            self.emit("typing")
            decision, ai = await orch.plan(sc, text, self.history, observations)
            action = str(decision.get("action") or "done")
            target_id = decision.get("id")
            self.emit("plan", component=child.id if child else None, agent=sc.name, action=action, id=target_id,
                      thought=decision.get("thought"), inputs=decision.get("inputs") or {}, ai=ai,
                      query=decision.get("query"))
            if action in ("respond", "ask"):
                msg = str(decision.get("message") or "").strip()
                if msg:
                    self.say(msg, None, agent=agent_label,
                             source="Generative response (AI)" if ai else "Generative response (mock)")
                break
            if action == "done":
                break
            comp = self.ws.get(str(target_id)) if target_id else None
            if action == "knowledge":
                ad = sc.instructions
                res = await knowledge.answer(self.ws, str(decision.get("query") or text), ad,
                                             [c.name for c in sc.knowledge] or None)
                self.emit("knowledge", **res)
                observations.append({"action": "knowledge", "result": {"answer": res.get("answer"), "citations": res.get("citations")}})
                if res.get("answer") and not ai:
                    self.say(res["answer"], None, agent=agent_label, source="Generative answers", citations=res.get("citations"))
                    break
                if not res.get("answer") and not ai:
                    break
                continue
            if comp is None:
                self.emit("warning", text=f"Planner chose '{target_id}', which is not a component of this agent")
                break
            if action == "topic":
                inputs = {k: v for k, v in (decision.get("inputs") or {}).items()}
                await self._begin_topic(comp, reason="Selected by the generative orchestrator", trigger_text=text,
                                        inputs=inputs, agent=agent_label)
                if self.pending or self.ended:
                    return
                observations.append({"action": "topic", "id": comp.id, "result": {"completed": True, **self.last_topic.get("vars", {})}})
                if not orch.topic_inputs(comp) and not (comp.data or {}).get("outputType"):
                    break
                continue
            if action == "agent":
                await self._generative(text, orch.scope_for(self.ws, comp.owner), comp, depth + 1)
                observations.append({"action": "agent", "id": comp.id, "result": "child agent responded"})
                break
            if action == "tool":
                info = orch.tool_info(comp)
                given = decision.get("inputs") or {}
                missing = [i for i in info["inputs"] if i["required"] and is_blank(given.get(i["name"]))]
                if missing:
                    self.say(await orch.tool_question(info, missing), None, source="Generative orchestration (missing input)")
                    break
                try:
                    result = await self._run_tool(comp, given)
                except SimError as e:
                    self.emit("error", component=comp.id, text=str(e), code=e.code)
                    observations.append({"action": "tool", "id": comp.id, "result": {"error": str(e)}})
                    if not ai:
                        await self._error(None, "", e)
                        return
                    continue
                observations.append({"action": "tool", "id": comp.id, "result": result})
                continue
            self.emit("warning", text=f"Unknown planner action '{action}'")
            break
        else:
            self.emit("warning", text="Planner stopped after 6 steps")
        if child is not None:
            self.emit("agent", component=child.id, action="end", name=child.displayName)
        if depth == 0 and self.sent_this_turn == sent_before and not self.pending and not self.stack:
            await self._fallback(text)

    async def _run_tool(self, comp: Component, given: dict) -> Any:
        d = comp.data if isinstance(comp.data, dict) else {}
        action = d.get("action") or {}
        kind = str(action.get("kind", ""))
        inputs = dict(given)
        for i in d.get("inputs") or []:
            if isinstance(i, dict) and i.get("kind") == "ManualTaskInput":
                inputs[str(i.get("propertyName"))] = eval_value(i.get("value"), self._env(None))
        name = str(d.get("modelDisplayName") or comp.displayName)
        if kind == "InvokeFlowTaskAction":
            flow_id = str(action.get("flowId", ""))
            wf = workflow_ids(self.ws).get(flow_id.lower())
            if wf is None:
                raise SimError(f"Tool '{name}' points at flow '{flow_id}', which is not in this workspace", "FlowNotFound")
            run = await flow_runner.run_flow(self.ws.info.path, wf, inputs)
            flow_runner.remember(self.wid, run)
            self.emit("flow", component=wf.id, runId=run.id, flow=wf.displayName, flowComponent=wf.id, status=run.status,
                      inputs=inputs, outputs=run.outputs, error=run.error, durationMs=run.durationMs, tool=name,
                      actions=[{"name": a.name, "pointer": a.pointer, "status": a.status, "source": a.source} for a in run.actions])
            if run.status != "Succeeded":
                raise SimError(f"Flow '{wf.displayName}' failed: {(run.error or {}).get('message', run.status)}", "FlowActionException")
            return run.outputs
        mock_action = {"type": kind, "inputs": {"host": {"apiId": _api_from_ref(str(action.get("connectionReference") or "")),
                                                         "operationId": action.get("operationId") or
                                                         (action.get("operationDetails") or {}).get("operationId") or ""}}}
        r = await mocks.connector_result(ws_path=self.ws.info.path, component_id=comp.id, name=name, action=mock_action,
                                         inputs=inputs, override=None, static_results={}, saved=mocks.load(self.ws.info.path))
        self.emit("tool", component=comp.id, name=name, kind=kind, inputs=inputs, outputs=r.outputs, source=r.source,
                  status=r.status)
        if r.status != "Succeeded":
            raise SimError(f"Tool '{name}' failed: {(r.error or {}).get('message')}", "ConnectorActionFailed")
        return (r.outputs or {}).get("body") if isinstance(r.outputs, dict) else r.outputs


# --- helpers -----------------------------------------------------------------------------------------------
def _children(n: dict, ptr: str) -> list[tuple[list, str]]:
    out = []
    if n.get("kind") == "ConditionGroup":
        for j, c in enumerate(n.get("conditions") or []):
            out.append((c.get("actions") or [], f"{ptr}/conditions/{j}/actions"))
        out.append((n.get("elseActions") or [], f"{ptr}/elseActions"))
    elif n.get("kind") in ("ActionScope", "Foreach"):
        out.append((n.get("actions") or [], f"{ptr}/actions"))
    return out


def _find_path(actions: list, target: str, ptr: str) -> list[tuple[list, int, str]] | None:
    for i, n in enumerate(actions):
        if not isinstance(n, dict):
            continue
        if n.get("id") == target:
            return [(actions, i, ptr)]
        for sub, sub_ptr in _children(n, f"{ptr}/{i}"):
            r = _find_path(sub, target, sub_ptr)
            if r:
                return [(actions, i + 1, ptr)] + r
    return None


def _api_from_ref(ref: str) -> str:
    low = re.sub(r"[^a-z0-9]", "", ref.lower())
    for api in mocks._connectors():
        if api.removeprefix("shared_").replace("_", "") in low:
            return api
    return ref


def _match_option(options: list[dict], text: str) -> Any:
    t = text.strip().lower()
    if not t:
        return None
    if t.isdigit() and 1 <= int(t) <= len(options):
        return options[int(t) - 1]["value"]
    for o in options:
        if t == o["title"].lower() or t == str(o["value"]).lower() or t in (s.lower() for s in o["synonyms"]):
            return o["value"]
    for o in options:
        words = [o["title"].lower(), *(s.lower() for s in o["synonyms"])]
        if any(re.search(rf"\b{re.escape(w)}\b", t) for w in words if w):
            return o["value"]
    return None


def _local_entity(ent: str, options: list[dict], text: str) -> Any:
    """Parse an answer for the common entities; None when it doesn't contain one."""
    t = text.strip()
    low = t.lower().strip(" .!?")
    if options:
        return _match_option(options, t)
    if ent == "BooleanPrebuiltEntity":
        if low in YES or any(low.startswith(w + " ") for w in ("yes", "yeah", "yep", "sure")):
            return True
        if low in NO or any(low.startswith(w + " ") for w in ("no", "nope", "nah")):
            return False
        return None
    if ent in ("NumberPrebuiltEntity", "AgePrebuiltEntity", "MoneyPrebuiltEntity", "PercentagePrebuiltEntity",
               "TemperaturePrebuiltEntity", "DimensionPrebuiltEntity", "OrdinalPrebuiltEntity"):
        m = re.search(r"-?\d+(?:[.,]\d+)?", t)
        if m:
            s = m.group().replace(",", ".")
            return float(s) if "." in s else int(s)
        for w in low.split():
            if w in NUM_WORDS:
                return NUM_WORDS[w]
        return None
    if ent == "EMailPrebuiltEntity":
        m = re.search(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", t)
        return m.group() if m else None
    if ent in ("URLPrebuiltEntity",):
        m = re.search(r"https?://\S+|www\.\S+", t)
        return m.group() if m else None
    if ent == "PhoneNumberPrebuiltEntity":
        m = re.search(r"\+?[\d\s\-()]{7,}", t)
        return m.group().strip() if m else None
    if ent in ("DateTimePrebuiltEntity", "DatePrebuiltEntity"):
        return _parse_when(low)
    if ent.startswith("RegexEntity:"):
        m = re.search(ent.split(":", 1)[1], t)
        return m.group() if m else None
    if ent in ("StringPrebuiltEntity", "") or ent.startswith("Entity '"):
        return t or None
    return None  # other prebuilt entities: LLM extraction


def _parse_when(low: str) -> datetime | None:
    from datetime import timedelta
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    if "today" in low or low == "now":
        return today
    if "tomorrow" in low:
        return today + timedelta(days=1)
    if "yesterday" in low:
        return today - timedelta(days=1)
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%d-%m-%Y", "%d %B %Y", "%B %d %Y", "%B %d, %Y", "%d %b %Y"):
        try:
            return datetime.strptime(low.title() if "%B" in fmt or "%b" in fmt else low, fmt)
        except ValueError:
            continue
    return None


# --- session registry ---------------------------------------------------------------------------------------
SESSIONS: dict[str, Session] = {}
_TTL = 3 * 3600


def create(ws: Workspace, user: dict | None = None) -> Session:
    now = time.time()
    for sid in [s for s, v in SESSIONS.items() if now - v.touched > _TTL]:
        SESSIONS.pop(sid, None)
    while len(SESSIONS) >= 50:
        SESSIONS.pop(min(SESSIONS, key=lambda s: SESSIONS[s].touched))
    s = Session(ws, user)
    SESSIONS[s.id] = s
    return s


def get(sid: str) -> Session | None:
    return SESSIONS.get(sid)


def mock_mode() -> bool:
    return config.USE_MOCK_AI or not config.GENAI_API_KEY
