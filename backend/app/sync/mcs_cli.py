"""Optional Validate / Changes / Pull / Push via Microsoft's manage-agent script.

Runs `scripts/manage-agent.bundle.js` from a local checkout of
microsoft/skills-for-copilot-studio (MCS_SKILLS_DIR). The script is used as-is
(its README asks not to modify it). It needs Node 18+, the Copilot Studio VS Code
extension (it drives that extension's language server) and a workspace cloned with
the extension (.mcs/conn.json). Sign-in happens interactively in the browser.
"""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

from pydantic import BaseModel

from .. import config
from ..workspace.model import WorkspaceInfo

COMMANDS = {"validate", "changes", "pull", "push"}


class SyncStatus(BaseModel):
    available: bool
    reasons: list[str]
    connected: bool
    environmentId: str | None = None
    agentId: str | None = None


def _script() -> Path | None:
    if not config.MCS_SKILLS_DIR:
        return None
    p = Path(config.MCS_SKILLS_DIR).expanduser() / "scripts" / "manage-agent.bundle.js"
    return p if p.exists() else None


def _conn(info: WorkspaceInfo) -> dict | None:
    p = Path(info.path) / ".mcs" / "conn.json"
    try:
        return json.loads(p.read_text()) if p.exists() else None
    except json.JSONDecodeError:
        return None


def status(info: WorkspaceInfo) -> SyncStatus:
    reasons = []
    if not shutil.which("node"):
        reasons.append("Node.js 18+ is not installed")
    if _script() is None:
        reasons.append("Set MCS_SKILLS_DIR in .env to a local clone of microsoft/skills-for-copilot-studio")
    if not list(Path.home().glob(".vscode/extensions/ms-copilotstudio.vscode-copilotstudio*")):
        reasons.append("Install the Copilot Studio VS Code extension (ms-copilotstudio.vscode-copilotstudio)")
    conn = _conn(info)
    if conn is None:
        reasons.append("This workspace isn't cloned with the VS Code extension (no .mcs/conn.json). "
                       "Use 'Copy into cloned agent' instead.")
    return SyncStatus(available=not reasons, reasons=reasons, connected=conn is not None,
                      environmentId=(conn or {}).get("EnvironmentId"), agentId=(conn or {}).get("AgentId"))


async def run(info: WorkspaceInfo, command: str, timeout: int = 600) -> dict:
    if command not in COMMANDS:
        raise ValueError(f"Unsupported command {command}")
    st = status(info)
    if not st.available:
        raise RuntimeError("; ".join(st.reasons))
    conn = _conn(info) or {}
    args = ["node", str(_script()), command, "--workspace", info.path]
    tenant = (conn.get("AccountInfo") or {}).get("TenantId")
    if tenant:
        args += ["--tenant-id", tenant]
    if conn.get("EnvironmentId"):
        args += ["--environment-id", conn["EnvironmentId"]]
    if conn.get("DataverseEndpoint"):
        args += ["--environment-url", conn["DataverseEndpoint"]]
    if conn.get("AgentManagementEndpoint"):
        args += ["--agent-mgmt-url", conn["AgentManagementEndpoint"]]
    proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError(f"{command} timed out after {timeout}s (did the browser sign-in complete?)")
    text = out.decode(errors="replace")
    result: dict = {"exitCode": proc.returncode, "stdout": text[-20000:], "stderr": err.decode(errors="replace")[-5000:]}
    # The script prints JSON (possibly after progress lines); surface the last JSON object.
    for line in reversed(text.strip().splitlines()):
        try:
            result["json"] = json.loads(line)
            break
        except json.JSONDecodeError:
            continue
    return result
