"""Saved test conversations (a local, lightweight take on Copilot Studio's agent evaluation).

A case is a list of user turns with optional expectations per turn — the topic that should start,
the tool / flow that should run, text the reply should contain. "Save as test" records the
current behaviour as the baseline; "Run all" replays every case in a fresh session and diffs.
Stored in `<ws>/.builder/test/cases.json`.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from ..workspace.model import Workspace
from . import runtime


class Expect(BaseModel):
    topic: str | None = None        # display name or component id
    tool: str | None = None         # tool / flow display name
    contains: str | None = None     # case-insensitive substring of any bot message


class Turn(BaseModel):
    user: str
    expect: Expect = Field(default_factory=Expect)


class Case(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:10])
    name: str
    turns: list[Turn]
    createdAt: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class Check(BaseModel):
    kind: str
    expected: str
    actual: str
    passed: bool


class TurnResult(BaseModel):
    user: str
    passed: bool
    checks: list[Check]
    bot: list[str]
    errors: list[str]


class CaseResult(BaseModel):
    id: str
    name: str
    passed: bool
    turns: list[TurnResult]


def _file(ws_path: str | Path) -> Path:
    return Path(ws_path) / ".builder" / "test" / "cases.json"


def load(ws_path: str | Path) -> list[Case]:
    f = _file(ws_path)
    if not f.exists():
        return []
    try:
        return [Case(**c) for c in json.loads(f.read_text())]
    except (json.JSONDecodeError, TypeError, ValueError):
        return []


def save(ws_path: str | Path, cases: list[Case]) -> list[Case]:
    f = _file(ws_path)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps([c.model_dump() for c in cases], indent=2, ensure_ascii=False))
    return cases


def _norm(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())


async def run_case(ws: Workspace, case: Case) -> CaseResult:
    s = runtime.Session(ws)
    await s.start()
    results: list[TurnResult] = []
    for turn in case.turns:
        first = len(s.events)
        await s.user_message(turn.user)
        evs = s.events[first:]
        topics = [e.data.get("name", "") for e in evs if e.type == "topic" and e.data.get("action") == "start"]
        topic_ids = [e.componentId or "" for e in evs if e.type == "topic" and e.data.get("action") == "start"]
        tools = [str(e.data.get("tool") or e.data.get("flow") or e.data.get("name") or "") for e in evs if e.type in ("flow", "tool")]
        bot = [str(e.data.get("text") or "") for e in evs if e.type == "message"]
        errors = [str(e.data.get("text") or "") for e in evs if e.type == "error"]
        checks: list[Check] = []
        x = turn.expect
        if x.topic:
            ok = any(_norm(x.topic) in (_norm(n), _norm(i)) for n, i in zip(topics, topic_ids))
            checks.append(Check(kind="topic", expected=x.topic, actual=", ".join(topics) or "none", passed=ok))
        if x.tool:
            ok = any(_norm(x.tool) == _norm(t) for t in tools)
            checks.append(Check(kind="tool", expected=x.tool, actual=", ".join(tools) or "none", passed=ok))
        if x.contains:
            ok = any(x.contains.lower() in b.lower() for b in bot)
            checks.append(Check(kind="contains", expected=x.contains, actual=" | ".join(bot)[:300] or "no reply", passed=ok))
        if errors:
            checks.append(Check(kind="no errors", expected="no runtime errors", actual="; ".join(errors)[:300], passed=False))
        results.append(TurnResult(user=turn.user, passed=all(c.passed for c in checks), checks=checks, bot=bot, errors=errors))
    return CaseResult(id=case.id, name=case.name, passed=all(r.passed for r in results), turns=results)


def _last_file(ws_path: str | Path) -> Path:
    return Path(ws_path) / ".builder" / "test" / "last-run.json"


def save_last_run(ws_path: str | Path, results: list[CaseResult], partial: bool) -> None:
    """Remember the latest results per case (a partial run updates only the cases it ran)."""
    prev = {r["id"]: r for r in (load_last_run(ws_path) or {}).get("results", [])} if partial else {}
    prev.update({r.id: r.model_dump() for r in results})
    f = _last_file(ws_path)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "results": list(prev.values())}))


def load_last_run(ws_path: str | Path) -> dict | None:
    f = _last_file(ws_path)
    try:
        return json.loads(f.read_text()) if f.exists() else None
    except json.JSONDecodeError:
        return None
