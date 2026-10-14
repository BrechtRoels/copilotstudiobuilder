"""Read a Copilot Studio agent folder (VS Code extension layout) into a Workspace.

Layout (see Microsoft Learn "Clone your agent in VS Code"):
    agent.mcs.yml, settings.mcs.yml, connectionreferences.mcs.yml
    topics/*.mcs.yml  actions/*.mcs.yml  knowledge/**  variables/*.mcs.yml
    trigger/*.mcs.yml agents/<Child>/agent.mcs.yml (+ agents/<Child>/knowledge/**)
    workflows/<Name>/{metadata.yaml, workflow.json}
"""
from __future__ import annotations

import json
from pathlib import Path

from .model import Component, Workspace, WorkspaceInfo
from .yamlio import header_name, load_plain

YAML_EXT = (".yml", ".yaml")
SKIP_DIRS = {".git", ".mcs", ".builder", "node_modules", ".vscode"}

_KIND_CATEGORY = {
    "GptComponentMetadata": "agent",
    "AdaptiveDialog": "topic",
    "TaskDialog": "action",
    "KnowledgeSourceConfiguration": "knowledge",
    "GlobalVariableComponent": "variable",
    "AgentDialog": "childAgent",
}

_FOLDER_CATEGORY = {
    "topics": "topic", "actions": "action", "knowledge": "knowledge",
    "variables": "variable", "trigger": "trigger", "triggers": "trigger",
}


def _stem(p: Path) -> str:
    name = p.name
    for suffix in (".mcs.yml", ".mcs.yaml", ".yml", ".yaml", ".json"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    # templates use e.g. greeting.topic.mcs.yml
    for infix in (".topic", ".knowledge", ".variable", ".action"):
        if name.endswith(infix):
            name = name[: -len(infix)]
    return name


def _categorise(rel: Path, kind: str | None) -> tuple[str, str | None]:
    """Return (category, owner child-agent folder)."""
    parts = rel.parts
    owner = None
    if parts[0] == "agents" and len(parts) >= 3:
        owner = parts[1]
        parts = parts[2:]
        if parts == ("agent.mcs.yml",) or parts == ("agent.mcs.yaml",):
            return "childAgent", owner
    if len(parts) == 1:
        lower = parts[0].lower()
        if lower.startswith("agent.mcs"):
            return "agent", owner
        if lower.startswith("settings.mcs"):
            return "settings", owner
        if lower.startswith("connectionreferences.mcs") or lower.startswith("connectioreferences.mcs"):
            return "connectionReferences", owner
    if parts[0] == "agents" and len(parts) == 2:
        return "childAgent", None
    if parts[0] in _FOLDER_CATEGORY:
        return _FOLDER_CATEGORY[parts[0]], owner
    if kind in _KIND_CATEGORY:
        return _KIND_CATEGORY[kind], owner
    return "other", owner


def _display_name(data, text: str, stem: str) -> str:
    if isinstance(data, dict):
        meta = data.get("mcs.metadata") or {}
        if isinstance(meta, dict) and meta.get("componentName"):
            return str(meta["componentName"])
        if data.get("displayName"):
            return str(data["displayName"])
        intent = (data.get("beginDialog") or {}).get("intent") if isinstance(data.get("beginDialog"), dict) else None
        if isinstance(intent, dict) and intent.get("displayName"):
            return str(intent["displayName"])
        if data.get("modelDisplayName"):
            return str(data["modelDisplayName"])
    return header_name(text) or stem


def load_component(root: Path, file: Path) -> Component:
    rel = file.relative_to(root)
    text = file.read_text(encoding="utf-8", errors="replace")
    data, error = None, None
    try:
        data = json.loads(text) if file.suffix == ".json" else load_plain(text)
    except Exception as e:  # noqa: BLE001 - surface any parse error to the UI
        error = f"{type(e).__name__}: {e}"
    kind = data.get("kind") if isinstance(data, dict) else None
    category, owner = _categorise(rel, kind)
    stem = _stem(file)
    if category == "childAgent" and owner:
        stem = owner
    return Component(
        id=rel.as_posix(), path=rel.as_posix(), category=category, kind=kind,
        name=stem, displayName=_display_name(data, text, stem), owner=owner,
        data=data, text=text, error=error,
    )


def _load_workflow(root: Path, folder: Path) -> Component:
    rel = folder.relative_to(root)
    wf_file = folder / "workflow.json"
    meta_file = folder / "metadata.yaml"
    if not meta_file.exists():
        meta_file = folder / "metadata.yml"
    text = wf_file.read_text(encoding="utf-8", errors="replace") if wf_file.exists() else ""
    data: dict = {"metadata": {}, "workflow": None}
    error = None
    try:
        if meta_file.exists():
            data["metadata"] = load_plain(meta_file.read_text(encoding="utf-8")) or {}
        if text:
            data["workflow"] = json.loads(text)
    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {e}"
    meta = data["metadata"] if isinstance(data["metadata"], dict) else {}
    return Component(
        id=rel.as_posix(), path=(rel / "workflow.json").as_posix(), category="workflow",
        kind="CloudFlow", name=folder.name,
        displayName=str(meta.get("displayName") or meta.get("name") or folder.name),
        data=data, text=text, error=error,
    )


def load_workspace(info: WorkspaceInfo) -> Workspace:
    root = Path(info.path)
    comps: list[Component] = []
    if root.exists():
        wf_root = root / "workflows"
        for file in sorted(root.rglob("*")):
            rel_parts = file.relative_to(root).parts
            if any(p in SKIP_DIRS for p in rel_parts) or not file.is_file():
                continue
            if file.is_relative_to(wf_root):
                continue
            if file.suffix in YAML_EXT:
                comps.append(load_component(root, file))
        if wf_root.is_dir():
            # A folder without files is a leftover of a deleted flow, not a flow.
            for folder in sorted(p for p in wf_root.iterdir() if p.is_dir() and any(f.is_file() for f in p.iterdir())):
                comps.append(_load_workflow(root, folder))
    return Workspace(info=info, components=comps, connected=(root / ".mcs" / "conn.json").exists())
