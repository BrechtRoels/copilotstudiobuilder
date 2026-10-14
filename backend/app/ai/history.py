"""AI Builder chat history per workspace: `<ws>/.builder/ai-history.json`.

Keeps the conversation (with each turn's progress timeline) and the proposals it produced, so the
panel survives a reload and "Apply" still works after a backend restart.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

MAX_TURNS = 40
MAX_PROPOSALS = 20


def _file(ws_path: str | Path) -> Path:
    return Path(ws_path) / ".builder" / "ai-history.json"


def load(ws_path: str | Path) -> dict:
    f = _file(ws_path)
    if f.exists():
        try:
            data = json.loads(f.read_text())
            if isinstance(data, dict):
                return {"turns": data.get("turns") or [], "proposals": data.get("proposals") or {}}
        except json.JSONDecodeError:
            log.warning("Ignoring unreadable %s", f)
    return {"turns": [], "proposals": {}}


def _save(ws_path: str | Path, data: dict) -> None:
    turns = data["turns"][-MAX_TURNS:]
    keep = {t.get("proposalId") for t in turns if t.get("proposalId")}
    props = {k: v for k, v in data["proposals"].items() if k in keep}
    if len(props) > MAX_PROPOSALS:
        props = dict(list(props.items())[-MAX_PROPOSALS:])
    f = _file(ws_path)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"turns": turns, "proposals": props}, ensure_ascii=False))


def add_turn(ws_path: str | Path, turn: dict, proposal: dict | None = None) -> None:
    data = load(ws_path)
    data["turns"].append({"ts": time.time(), **turn})
    if proposal:
        data["proposals"][proposal["id"]] = proposal
    _save(ws_path, data)


def get_proposal(ws_path: str | Path, pid: str) -> dict | None:
    return load(ws_path)["proposals"].get(pid)


def mark_applied(ws_path: str | Path, pid: str, written: list[str]) -> None:
    data = load(ws_path)
    if pid in data["proposals"]:
        data["proposals"][pid]["applied"] = written
        _save(ws_path, data)


def clear(ws_path: str | Path) -> None:
    f = _file(ws_path)
    if f.exists():
        f.unlink()


def recent_changes(ws_path: str | Path, limit: int = 8) -> list[dict[str, Any]]:
    """Applied proposals, newest first (for the dashboard)."""
    data = load(ws_path)
    out = []
    for t in reversed(data["turns"]):
        p = data["proposals"].get(t.get("proposalId") or "")
        if p and p.get("applied"):
            out.append({"ts": t.get("ts"), "summary": p.get("summary", ""), "files": p["applied"], "mode": p.get("mode")})
        if len(out) >= limit:
            break
    return out
