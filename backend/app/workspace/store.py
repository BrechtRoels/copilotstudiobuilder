"""Workspace registry + safe file IO.

New workspaces live in WORKSPACES_DIR/<slug>. Existing agent folders (cloned
with the VS Code extension) are registered by absolute path and edited in place.
"""
from __future__ import annotations

import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .. import config
from .loader import load_workspace
from .model import ChangeSet, Workspace, WorkspaceInfo

REGISTRY = config.WORKSPACES_DIR / "registry.json"


class WorkspaceError(ValueError):
    pass


def _read_registry() -> list[WorkspaceInfo]:
    if not REGISTRY.exists():
        return []
    return [WorkspaceInfo(**w) for w in json.loads(REGISTRY.read_text())]


def _write_registry(items: list[WorkspaceInfo]) -> None:
    config.WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(json.dumps([i.model_dump() for i in items], indent=2))


def slugify(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-") or "agent"


def list_workspaces() -> list[WorkspaceInfo]:
    return _read_registry()


def get_info(wid: str) -> WorkspaceInfo:
    for w in _read_registry():
        if w.id == wid:
            return w
    raise WorkspaceError(f"Unknown workspace {wid}")


def get_workspace(wid: str) -> Workspace:
    return load_workspace(get_info(wid))


def create_workspace(name: str, description: str = "", origin: str = "empty") -> WorkspaceInfo:
    base = config.WORKSPACES_DIR / slugify(name)
    path, n = base, 2
    while path.exists():
        path = base.with_name(f"{base.name}-{n}")
        n += 1
    path.mkdir(parents=True)
    info = WorkspaceInfo(
        id=uuid.uuid4().hex[:12], name=name, path=str(path), origin=origin,
        createdAt=datetime.now(timezone.utc).isoformat(), description=description,
    )
    _write_registry([*_read_registry(), info])
    return info


def register_folder(folder: str, name: str | None = None) -> WorkspaceInfo:
    path = Path(folder).expanduser().resolve()
    if not path.is_dir():
        raise WorkspaceError(f"Folder not found: {path}")
    if not any(path.glob("agent.mcs.y*ml")) and not (path / "topics").is_dir():
        raise WorkspaceError("This folder doesn't look like a Copilot Studio agent (no agent.mcs.yml or topics/).")
    existing = next((w for w in _read_registry() if Path(w.path) == path), None)
    if existing:
        return existing
    info = WorkspaceInfo(
        id=uuid.uuid4().hex[:12], name=name or path.name, path=str(path), origin="cloned",
        createdAt=datetime.now(timezone.utc).isoformat(),
    )
    _write_registry([*_read_registry(), info])
    return info


def remove_workspace(wid: str, delete_files: bool = False) -> None:
    info = get_info(wid)
    _write_registry([w for w in _read_registry() if w.id != wid])
    # Only ever delete folders we created ourselves inside WORKSPACES_DIR.
    if delete_files and Path(info.path).resolve().is_relative_to(config.WORKSPACES_DIR):
        shutil.rmtree(info.path, ignore_errors=True)


def resolve_path(info: WorkspaceInfo, rel: str) -> Path:
    root = Path(info.path).resolve()
    target = (root / rel).resolve()
    if not target.is_relative_to(root) or target == root:
        raise WorkspaceError(f"Path escapes workspace: {rel}")
    return target


def read_file(info: WorkspaceInfo, rel: str) -> str:
    p = resolve_path(info, rel)
    return p.read_text(encoding="utf-8") if p.exists() else ""


def write_file(info: WorkspaceInfo, rel: str, content: str) -> None:
    p = resolve_path(info, rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def apply_changeset(info: WorkspaceInfo, cs: ChangeSet) -> list[str]:
    root = Path(info.path).resolve()
    written = []
    for f in cs.files:
        p = resolve_path(info, f.path)
        if f.content is None:
            if p.exists():
                p.unlink()
            # Drop folders the delete left empty (workflows/<Flow>/, agents/<Child>/), so no half component remains.
            parent = p.parent
            while parent != root and parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f.content, encoding="utf-8")
        written.append(f.path)
    return written
