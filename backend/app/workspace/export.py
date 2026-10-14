"""Get a workspace out of the app: zip download, or merge into a cloned agent folder."""
from __future__ import annotations

import io
import shutil
import zipfile
from pathlib import Path

from .loader import SKIP_DIRS
from .model import WorkspaceInfo
from .yamlio import dump_rt, load_rt

# Fields of a generated agent.mcs.yml that we carry into a cloned agent's own file.
_AGENT_FIELDS = ("instructions", "conversationStarters", "description", "displayName")


def _files(root: Path):
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if p.is_file() and not any(part in SKIP_DIRS for part in rel.parts):
            yield p, rel


def zip_workspace(info: WorkspaceInfo) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p, rel in _files(Path(info.path)):
            zf.write(p, rel.as_posix())
    return buf.getvalue()


def merge_into(info: WorkspaceInfo, target: str) -> list[str]:
    """Copy components into a folder cloned with the VS Code extension.

    The clone's identity files (.mcs/, settings, connection references) are kept.
    agent.mcs.yml is merged field-by-field so the clone's metadata survives.
    """
    dst = Path(target).expanduser().resolve()
    if not (dst / ".mcs").is_dir() and not (dst / "agent.mcs.yml").exists():
        raise ValueError("Target doesn't look like a cloned Copilot Studio agent (no .mcs/ or agent.mcs.yml)")
    src = Path(info.path).resolve()
    if dst == src:
        raise ValueError("Target is this workspace")
    written = []
    for p, rel in _files(src):
        name = rel.as_posix()
        if name in ("settings.mcs.yml", "connectionreferences.mcs.yml"):
            continue
        out = dst / rel
        if name == "agent.mcs.yml" and out.exists():
            theirs = load_rt(out.read_text())
            ours = load_rt(p.read_text())
            for f in _AGENT_FIELDS:
                if f in ours:
                    theirs[f] = ours[f]
            out.write_text(dump_rt(theirs))
        else:
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, out)
        written.append(name)
    return written
