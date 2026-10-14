"""Run an agent flow (workflow.json) locally, the way Power Automate's "Test flow" does.

Execution follows the real semantics:
  * an action runs once every `runAfter` predecessor has finished with one of the listed
    statuses (default `Succeeded`); otherwise it is **Skipped**, and skips propagate;
  * a Scope / If / Switch / Foreach / Until is Failed when an action inside it failed, so
    Try/Catch patterns (runAfter Failed, TimedOut) behave as in the cloud;
  * the run is Failed when a top-level action failed and nothing ran after it to handle it;
  * Terminate ends the run with its status; Response (kind Skills) is what the agent receives.
Connector, HTTP and child-flow actions never call out — see `mocks.py`.
"""
from __future__ import annotations

import time
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from ..validation.validator import flow_definition
from ..workspace.model import Component
from . import mocks, wdl

MAX_LOOP = 100


class ActionRun(BaseModel):
    name: str
    pointer: str
    type: str
    status: str                       # Succeeded | Failed | Skipped | TimedOut | Cancelled
    inputs: Any = None
    outputs: Any = None
    error: dict | None = None
    ms: int = 0
    source: str | None = None         # mock source for connector actions
    iterations: int | None = None
    note: str = ""


class FlowRun(BaseModel):
    id: str
    componentId: str
    flowName: str
    status: str
    startedAt: str
    durationMs: int
    triggerInputs: dict = Field(default_factory=dict)
    actions: list[ActionRun] = Field(default_factory=list)
    response: dict | None = None      # what "Respond to the agent" returned
    outputs: dict = Field(default_factory=dict)   # response mapped to metadata outputType names
    error: dict | None = None


class _Terminate(Exception):
    def __init__(self, status: str, error: dict | None):
        self.status, self.error = status, error


_CONNECTOR_TYPES = {"OpenApiConnection", "ApiConnection", "ApiConnectionWebhook", "OpenApiConnectionWebhook",
                    "Http", "HttpWebhook", "Workflow", "Function", "ApiManagement", "Expression", "Flow"}
_FAIL = {"Failed", "TimedOut"}

# Recent runs per workspace (newest first), shown in the Test flow dialog.
HISTORY: dict[str, deque] = {}


def remember(wid: str, run: FlowRun) -> None:
    HISTORY.setdefault(wid, deque(maxlen=20)).appendleft(run)


def history(wid: str, component_id: str | None = None) -> list[FlowRun]:
    return [r for r in HISTORY.get(wid, []) if component_id is None or r.componentId == component_id]


def trigger_fields(comp: Component) -> list[dict]:
    """Input fields for the run form: metadata inputType merged with the trigger schema keys."""
    data = comp.data or {}
    meta = data.get("metadata") or {}
    d = flow_definition(data.get("workflow")) or {}
    trig = next(iter((d.get("triggers") or {}).values()), {}) or {}
    schema_props = (((trig.get("inputs") or {}).get("schema") or {}).get("properties") or {})
    required = set(((trig.get("inputs") or {}).get("schema") or {}).get("required") or [])
    by_title = {str((p or {}).get("title") or k).lower(): k for k, p in schema_props.items()}
    fields = []
    seen = set()
    for name, p in ((meta.get("inputType") or {}).get("properties") or {}).items():
        key = by_title.get(str(name).lower(), name)
        seen.add(key)
        fields.append({"name": name, "key": key, "type": (p or {}).get("type", "String"),
                       "description": (p or {}).get("description", ""), "required": key in required})
    for key, p in schema_props.items():
        if key not in seen:
            fields.append({"name": (p or {}).get("title") or key, "key": key, "type": (p or {}).get("type", "string"),
                           "description": (p or {}).get("description", ""), "required": key in required})
    return fields


def connector_actions(comp: Component) -> list[dict]:
    """Actions that would call a service (candidates for mock output / forced failure)."""
    from ..validation.validator import iter_flow_actions
    d = flow_definition((comp.data or {}).get("workflow")) or {}
    out = []
    for name, act, _ in iter_flow_actions(d.get("actions") or {}):
        t = str(act.get("type", ""))
        if t in _CONNECTOR_TYPES:
            host = (act.get("inputs") or {}).get("host") if isinstance(act.get("inputs"), dict) else None
            api = mocks.api_name(host or {}) if host else ""
            op = str((host or {}).get("operationId") or "")
            out.append({"name": name, "type": t, "api": api.removeprefix("shared_"), "operationId": op,
                        "hasSchema": bool(api and op and mocks.connector_operation(api, op))})
    return out


def _coerce(v: Any, t: str) -> Any:
    t = str(t).lower()
    if not isinstance(v, str):
        return v
    try:
        if t in ("number", "integer"):
            return float(v) if "." in v else int(v)
        if t == "boolean":
            return v.strip().lower() in ("true", "yes", "1")
    except ValueError:
        return v
    return v


class _Runner:
    def __init__(self, ws_path: str, comp: Component, overrides: dict, saved: dict, definition: dict):
        self.ws_path = ws_path
        self.comp = comp
        self.overrides = overrides or {}
        self.saved = saved
        self.static_results = definition.get("staticResults") or {}
        self.ctx = wdl.Ctx(parameters={k: (v or {}).get("defaultValue") for k, v in (definition.get("parameters") or {}).items()})
        self.records: list[ActionRun] = []
        self.response: dict | None = None

    async def scope(self, actions: dict, pointer: str, scope_name: str | None) -> list[str]:
        """Run one scope; returns the statuses of its actions."""
        names = [n for n, a in (actions or {}).items() if isinstance(a, dict)]
        if scope_name:
            self.ctx.scopes[scope_name] = names
        done: dict[str, str] = {}
        pending = list(names)
        while pending:
            progressed = False
            for name in list(pending):
                act = actions[name]
                run_after = act.get("runAfter") or {}
                deps = {d: (s or ["Succeeded"]) for d, s in run_after.items()}
                if any(d not in done for d in deps if d in actions):
                    continue
                pending.remove(name)
                progressed = True
                ok = all(done.get(d, "Succeeded") in allowed for d, allowed in deps.items())
                ptr = f"{pointer}/{name}"
                if not ok:
                    self._skip(name, act, ptr)
                    done[name] = "Skipped"
                    continue
                done[name] = await self.action(name, act, ptr)
            if not progressed:  # unresolvable runAfter (cycle / unknown name)
                for name in pending:
                    self._skip(name, actions[name], f"{pointer}/{name}", "runAfter refers to an action that never runs")
                    done[name] = "Skipped"
                break
        return [done[n] for n in names]

    def _skip(self, name: str, act: dict, ptr: str, note: str = "") -> None:
        self.ctx.actions[name] = {"status": "Skipped", "inputs": None, "outputs": None}
        self.records.append(ActionRun(name=name, pointer=ptr, type=str(act.get("type", "?")), status="Skipped", note=note))
        for sub_ptr, sub in _child_scopes(act, ptr):
            for n, a in sub.items():
                if isinstance(a, dict):
                    self._skip(n, a, f"{sub_ptr}/{n}")

    def _record(self, name: str, act: dict, ptr: str, status: str, t0: float, **kw) -> str:
        self.ctx.actions[name] = {"name": name, "status": status, "inputs": kw.get("inputs"), "outputs": kw.get("outputs"),
                                  "error": kw.get("error"), "code": (kw.get("error") or {}).get("code") if status in _FAIL else "OK"}
        self.records.append(ActionRun(name=name, pointer=ptr, type=str(act.get("type", "?")), status=status,
                                      ms=int((time.perf_counter() - t0) * 1000), **kw))
        return status

    async def action(self, name: str, act: dict, ptr: str) -> str:
        t0 = time.perf_counter()
        t = str(act.get("type", ""))
        idx = len(self.records)
        try:
            if t in ("Scope",):
                self.records.append(ActionRun(name=name, pointer=ptr, type=t, status="Running"))
                statuses = await self.scope(act.get("actions") or {}, f"{ptr}/actions", name)
                return self._finish_scope(idx, name, act, ptr, statuses, t0)
            if t == "If":
                cond = self._condition(act.get("expression"))
                self.records.append(ActionRun(name=name, pointer=ptr, type=t, status="Running"))
                if cond:
                    statuses = await self.scope(act.get("actions") or {}, f"{ptr}/actions", name)
                    self._skip_all((act.get("else") or {}).get("actions") or {}, f"{ptr}/else/actions")
                else:
                    statuses = await self.scope((act.get("else") or {}).get("actions") or {}, f"{ptr}/else/actions", name)
                    self._skip_all(act.get("actions") or {}, f"{ptr}/actions")
                return self._finish_scope(idx, name, act, ptr, statuses, t0, inputs={"expression": cond},
                                          note=f"Condition was {'true' if cond else 'false'}")
            if t == "Switch":
                value = wdl.resolve(act.get("expression"), self.ctx)
                cases = act.get("cases") or {}
                chosen = next((c for c, case in cases.items() if wdl._eq((case or {}).get("case"), value)), None)
                self.records.append(ActionRun(name=name, pointer=ptr, type=t, status="Running"))
                statuses: list[str] = []
                for cname, case in cases.items():
                    cptr = f"{ptr}/cases/{cname}/actions"
                    if cname == chosen:
                        statuses = await self.scope((case or {}).get("actions") or {}, cptr, name)
                    else:
                        self._skip_all((case or {}).get("actions") or {}, cptr)
                dflt = (act.get("default") or {}).get("actions") or {}
                if chosen is None:
                    statuses = await self.scope(dflt, f"{ptr}/default/actions", name)
                else:
                    self._skip_all(dflt, f"{ptr}/default/actions")
                return self._finish_scope(idx, name, act, ptr, statuses, t0, inputs={"expression": value},
                                          note=f"Case: {chosen or 'default'}")
            if t == "Foreach":
                items = wdl.resolve(act.get("foreach"), self.ctx)
                if not isinstance(items, list):
                    raise wdl.WdlError(f"foreach expects an array, got {type(items).__name__}")
                self.records.append(ActionRun(name=name, pointer=ptr, type=t, status="Running"))
                statuses = []
                for item in items[:MAX_LOOP]:
                    self.ctx.items[name] = item
                    self.ctx.item_stack.append(item)
                    try:
                        statuses += await self.scope(act.get("actions") or {}, f"{ptr}/actions", name)
                    finally:
                        self.ctx.item_stack.pop()
                if not items:
                    self._skip_all(act.get("actions") or {}, f"{ptr}/actions")
                return self._finish_scope(idx, name, act, ptr, statuses, t0, inputs=items, iterations=len(items[:MAX_LOOP]),
                                          note="Inner actions show the last iteration" if len(items) > 1 else "")
            if t == "Until":
                limit = min(int(((act.get("limit") or {}).get("count")) or 60), MAX_LOOP)
                self.records.append(ActionRun(name=name, pointer=ptr, type=t, status="Running"))
                statuses, n = [], 0
                while n < limit:
                    n += 1
                    statuses += await self.scope(act.get("actions") or {}, f"{ptr}/actions", name)
                    if self._condition(act.get("expression")):
                        break
                return self._finish_scope(idx, name, act, ptr, statuses, t0, iterations=n,
                                          note="" if n < limit else f"Stopped at the loop limit ({limit})")
            return await self._simple(name, act, ptr, t, t0)
        except _Terminate:
            raise
        except wdl.WdlError as e:
            if idx < len(self.records) and self.records[idx].status == "Running":
                del self.records[idx]
            return self._record(name, act, ptr, "Failed", t0, error={"code": "InvalidTemplate", "message": str(e)})

    def _skip_all(self, actions: dict, ptr: str) -> None:
        for n, a in (actions or {}).items():
            if isinstance(a, dict):
                self._skip(n, a, f"{ptr}/{n}")

    def _finish_scope(self, idx: int, name: str, act: dict, ptr: str, statuses: list[str], t0: float, **kw) -> str:
        status = "Failed" if any(s in _FAIL for s in statuses) else "Succeeded"
        err = {"code": "ActionFailed", "message": "An action failed. No dependent actions succeeded."} if status == "Failed" else None
        self.ctx.actions[name] = {"name": name, "status": status, "inputs": kw.get("inputs"), "outputs": None, "error": err,
                                  "code": "ActionFailed" if err else "OK"}
        rec = ActionRun(name=name, pointer=ptr, type=str(act.get("type")), status=status, error=err,
                        ms=int((time.perf_counter() - t0) * 1000), **kw)
        self.records[idx] = rec
        return status

    def _condition(self, expr: Any) -> bool:
        if isinstance(expr, str):
            return bool(wdl.resolve(expr, self.ctx))
        if isinstance(expr, bool):
            return expr
        if isinstance(expr, dict) and len(expr) == 1:
            op, args = next(iter(expr.items()))
            op = op.lower()
            if op == "and":
                return all(self._condition(a) for a in args)
            if op == "or":
                return any(self._condition(a) for a in args)
            if op == "not":
                return not self._condition(args)
            vals = [wdl.resolve(a, self.ctx) for a in (args if isinstance(args, list) else [args])]
            fn = wdl._FN.get(op)
            if fn is None:
                raise wdl.WdlError(f"Condition operator '{op}' is not simulated")
            return bool(fn(self.ctx, *vals))
        raise wdl.WdlError("Unrecognised condition expression")

    async def _simple(self, name: str, act: dict, ptr: str, t: str, t0: float) -> str:
        raw = act.get("inputs")
        override = self.overrides.get(name)
        if override and override.get("mode") == "fail" and t not in _CONNECTOR_TYPES:
            r = mocks._from_override(override, "override")
            return self._record(name, act, ptr, r.status, t0, outputs=r.outputs, error=r.error, source="override")
        if t in ("InitializeVariable",):
            inputs = wdl.resolve(raw, self.ctx)
            for v in (inputs or {}).get("variables") or []:
                self.ctx.variables[v["name"]] = v.get("value", _default_for(v.get("type")))
            return self._record(name, act, ptr, "Succeeded", t0, inputs=inputs)
        if t in ("SetVariable", "IncrementVariable", "DecrementVariable", "AppendToArrayVariable", "AppendToStringVariable"):
            inputs = wdl.resolve(raw, self.ctx) or {}
            vname = inputs.get("name")
            if vname not in self.ctx.variables:
                raise wdl.WdlError(f"Variable '{vname}' is not initialized")
            cur, val = self.ctx.variables[vname], inputs.get("value")
            if t == "SetVariable":
                cur = val
            elif t == "IncrementVariable":
                cur = (cur or 0) + (val if val is not None else 1)
            elif t == "DecrementVariable":
                cur = (cur or 0) - (val if val is not None else 1)
            elif t == "AppendToArrayVariable":
                cur = [*(cur or []), val]
            else:
                cur = f"{cur or ''}{wdl.to_string(val)}"
            self.ctx.variables[vname] = cur
            return self._record(name, act, ptr, "Succeeded", t0, inputs=inputs, outputs={"body": {"value": cur}})
        if t == "Compose":
            inputs = wdl.resolve(raw, self.ctx)
            return self._record(name, act, ptr, "Succeeded", t0, inputs=inputs, outputs=inputs)
        if t == "ParseJson":
            inputs = wdl.resolve(raw, self.ctx) or {}
            content = inputs.get("content")
            if isinstance(content, str):
                content = wdl._json(self.ctx, content)
            return self._record(name, act, ptr, "Succeeded", t0, inputs=inputs, outputs={"body": content})
        if t == "Query":
            src = wdl.resolve((raw or {}).get("from"), self.ctx) or []
            out = []
            for it in src:
                self.ctx.item_stack.append(it)
                try:
                    if self._condition((raw or {}).get("where")):
                        out.append(it)
                finally:
                    self.ctx.item_stack.pop()
            return self._record(name, act, ptr, "Succeeded", t0, inputs={"from": src}, outputs={"body": out})
        if t == "Select":
            src = wdl.resolve((raw or {}).get("from"), self.ctx) or []
            out = []
            for it in src:
                self.ctx.item_stack.append(it)
                try:
                    out.append(wdl.resolve((raw or {}).get("select"), self.ctx))
                finally:
                    self.ctx.item_stack.pop()
            return self._record(name, act, ptr, "Succeeded", t0, inputs={"from": src}, outputs={"body": out})
        if t == "Join":
            inputs = wdl.resolve(raw, self.ctx) or {}
            out = str(inputs.get("joinWith", "")).join(wdl.to_string(x) for x in inputs.get("from") or [])
            return self._record(name, act, ptr, "Succeeded", t0, inputs=inputs, outputs={"body": out})
        if t == "Table":
            inputs = wdl.resolve(raw, self.ctx) or {}
            rows = inputs.get("from") or []
            cols = list(rows[0].keys()) if rows and isinstance(rows[0], dict) else []
            if str(inputs.get("format", "")).upper() == "CSV":
                body = "\n".join([",".join(cols)] + [",".join(wdl.to_string(r.get(c)) for c in cols) for r in rows])
            else:
                body = "<table><tr>" + "".join(f"<th>{c}</th>" for c in cols) + "</tr>" + "".join(
                    "<tr>" + "".join(f"<td>{wdl.to_string(r.get(c))}</td>" for c in cols) + "</tr>" for r in rows) + "</table>"
            return self._record(name, act, ptr, "Succeeded", t0, inputs=inputs, outputs={"body": body})
        if t == "Wait":
            return self._record(name, act, ptr, "Succeeded", t0, inputs=wdl.resolve(raw, self.ctx),
                                note="Delay skipped in the simulator")
        if t == "Response":
            inputs = wdl.resolve(raw, self.ctx) or {}
            self.response = {"statusCode": inputs.get("statusCode", 200), "body": inputs.get("body")}
            return self._record(name, act, ptr, "Succeeded", t0, inputs=inputs, outputs=self.response)
        if t == "Terminate":
            inputs = wdl.resolve(raw, self.ctx) or {}
            status = inputs.get("runStatus", "Failed")
            err = inputs.get("runError") if status == "Failed" else None
            self._record(name, act, ptr, "Succeeded", t0, inputs=inputs)
            raise _Terminate(status, err)
        # Everything that would talk to a service is mocked.
        inputs = wdl.resolve(raw, self.ctx)
        r = await mocks.connector_result(ws_path=self.ws_path, component_id=self.comp.id, name=name, action=act,
                                         inputs=inputs, override=override, static_results=self.static_results, saved=self.saved)
        note = "" if t in _CONNECTOR_TYPES else f"Action type '{t}' is not simulated — treated like a connector call"
        return self._record(name, act, ptr, r.status, t0, inputs=inputs, outputs=r.outputs, error=r.error,
                            source=r.source, note=note)


def _child_scopes(act: dict, ptr: str) -> list[tuple[str, dict]]:
    out = []
    if isinstance(act.get("actions"), dict):
        out.append((f"{ptr}/actions", act["actions"]))
    if isinstance((act.get("else") or {}).get("actions"), dict):
        out.append((f"{ptr}/else/actions", act["else"]["actions"]))
    for cname, case in (act.get("cases") or {}).items():
        if isinstance((case or {}).get("actions"), dict):
            out.append((f"{ptr}/cases/{cname}/actions", case["actions"]))
    if isinstance((act.get("default") or {}).get("actions"), dict):
        out.append((f"{ptr}/default/actions", act["default"]["actions"]))
    return out


def _default_for(t: Any) -> Any:
    return {"string": "", "integer": 0, "float": 0.0, "boolean": False, "array": [], "object": {}}.get(str(t).lower())


def _map_outputs(comp: Component, body: Any) -> dict:
    props = (((comp.data or {}).get("metadata") or {}).get("outputType") or {}).get("properties") or {}
    if not isinstance(body, dict):
        return {next(iter(props)): body} if props and body is not None else {}
    low = {str(k).lower(): v for k, v in body.items()}
    if not props:
        return dict(body)
    return {name: low.get(str(name).lower()) for name in props}


async def run_flow(ws_path: str, comp: Component, inputs: dict | None = None, overrides: dict | None = None) -> FlowRun:
    started = datetime.now(timezone.utc)
    t0 = time.perf_counter()
    d = flow_definition((comp.data or {}).get("workflow"))
    run_id = uuid.uuid4().hex[:12]
    flow_name = comp.displayName
    if not d:
        return FlowRun(id=run_id, componentId=comp.id, flowName=flow_name, status="Failed", startedAt=started.isoformat(),
                       durationMs=0, error={"code": "InvalidDefinition", "message": "workflow.json has no triggers/actions"})
    fields = trigger_fields(comp)
    body: dict[str, Any] = {}
    given = {str(k).lower(): v for k, v in (inputs or {}).items()}
    for f in fields:
        for k in (f["key"], f["name"]):
            if str(k).lower() in given:
                body[f["key"]] = _coerce(given[str(k).lower()], f["type"])
                break
    runner = _Runner(ws_path, comp, overrides or {}, mocks.load(ws_path), d)
    runner.ctx.trigger = {"name": next(iter(d.get("triggers") or {"manual": {}})), "outputs": {"headers": {}, "body": body}}
    missing = [f["name"] for f in fields if f["required"] and is_missing(body.get(f["key"]))]
    trig_name = next(iter(d.get("triggers") or {}), "manual")
    trig_rec = ActionRun(name=trig_name, pointer=f"triggers/{trig_name}", type="Trigger",
                         status="Failed" if missing else "Succeeded", outputs={"body": body},
                         error={"code": "TriggerInputSchemaMismatch", "message": f"Missing required input(s): {', '.join(missing)}"} if missing else None)
    runner.records.append(trig_rec)
    status, error = "Succeeded", None
    if missing:
        status, error = "Failed", trig_rec.error
    else:
        try:
            statuses = await runner.scope(d.get("actions") or {}, "actions", None)
            top = {n: s for n, s in zip([n for n, a in (d.get("actions") or {}).items() if isinstance(a, dict)], statuses)}
            unhandled = [n for n, s in top.items() if s in _FAIL and not _handled(n, d.get("actions") or {}, top)]
            if unhandled:
                status = "Failed"
                first = next((r for r in runner.records if r.name == unhandled[0]), None)
                error = (first.error if first else None) or {"code": "ActionFailed", "message": f"'{unhandled[0]}' failed"}
        except _Terminate as term:
            status, error = term.status, term.error
    resp = runner.response
    latest: dict[str, ActionRun] = {}
    for r in runner.records:          # loops: keep each action once, with its last iteration
        latest[r.pointer] = r
    return FlowRun(id=run_id, componentId=comp.id, flowName=flow_name, status=status, startedAt=started.isoformat(),
                   durationMs=int((time.perf_counter() - t0) * 1000), triggerInputs=body, actions=list(latest.values()),
                   response=resp, outputs=_map_outputs(comp, (resp or {}).get("body")), error=error)


def is_missing(v: Any) -> bool:
    return v is None or v == ""


def _handled(name: str, actions: dict, statuses: dict[str, str]) -> bool:
    """A failure is handled when an action that runs after it (on failure) actually ran."""
    for n, a in actions.items():
        if isinstance(a, dict) and name in (a.get("runAfter") or {}) and statuses.get(n) not in (None, "Skipped"):
            return True
    return False
