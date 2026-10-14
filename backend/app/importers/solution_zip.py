"""Optional: convert an exported Power Platform solution .zip into an agent workspace.

Solution layout (unmanaged export):
    solution.xml, customizations.xml
    bots/<botschema>/bot.xml, configuration.json
    botcomponents/<componentschema>/botcomponent.xml, data      (data = component YAML)
    Workflows/<Name>-<GUID>.json                                 (flow clientdata)
Components are routed by the YAML `kind`, so we don't depend on componenttype codes.
The result is not attached to an environment; use "Reattach agent" in the VS Code
extension (or copy into a cloned folder) to sync it.
"""
from __future__ import annotations

import io
import json
import re
import xml.etree.ElementTree as ET
import zipfile

from ..workspace.model import ChangeSet, FileChange
from ..workspace.yamlio import load_plain

_FOLDER_BY_KIND = {
    "AdaptiveDialog": "topics", "TaskDialog": "actions", "KnowledgeSourceConfiguration": "knowledge",
    "GlobalVariableComponent": "variables",
}


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "", name) or "component"


def _tail(schema: str) -> str:
    for marker in (".topic.", ".action.", ".knowledge.", ".agent.", ".gpt.", ".variable."):
        if marker in schema:
            return schema.rsplit(marker, 1)[1]
    return schema.rsplit(".", 1)[-1]


def import_solution(data: bytes) -> tuple[ChangeSet, str]:
    """Return (files to write, agent display name)."""
    zf = zipfile.ZipFile(io.BytesIO(data))
    names = zf.namelist()
    files: list[FileChange] = []
    notes: list[str] = []
    agent_name = "Imported agent"

    bot_xml = next((n for n in names if re.match(r"bots/[^/]+/bot\.xml$", n)), None)
    if bot_xml:
        try:
            agent_name = ET.fromstring(zf.read(bot_xml)).findtext("name") or agent_name
        except ET.ParseError:
            pass

    used: set[str] = set()

    def unique(path: str) -> str:
        base, n = path, 2
        while path in used:
            stem, ext = base.rsplit(".mcs.yml", 1)[0], ".mcs.yml"
            path = f"{stem}{n}{ext}"
            n += 1
        used.add(path)
        return path

    for n in names:
        m = re.match(r"botcomponents/([^/]+)/data$", n)
        if not m:
            continue
        schema = m.group(1)
        text = zf.read(n).decode("utf-8", errors="replace")
        try:
            parsed = load_plain(text)
        except Exception:  # noqa: BLE001
            notes.append(f"Skipped {schema}: data is not YAML")
            continue
        kind = parsed.get("kind") if isinstance(parsed, dict) else None
        display = None
        xml_name = f"botcomponents/{schema}/botcomponent.xml"
        if xml_name in names:
            try:
                display = ET.fromstring(zf.read(xml_name)).findtext("name")
            except ET.ParseError:
                pass
        stem = _safe(_tail(schema))
        if kind == "GptComponentMetadata":
            path = "agent.mcs.yml"
        elif kind == "AgentDialog":
            path = f"agents/{stem}/agent.mcs.yml"
        elif kind in _FOLDER_BY_KIND:
            path = unique(f"{_FOLDER_BY_KIND[kind]}/{stem}.mcs.yml")
        else:
            notes.append(f"Skipped {schema}: kind {kind!r} not supported in workspace format")
            continue
        if kind == "AdaptiveDialog" and display and not text.lstrip().startswith("# Name:"):
            text = f"# Name: {display}\n{text}"
        files.append(FileChange(path=path, content=text if text.endswith("\n") else text + "\n"))

    # flows: map Workflows/*.json -> id/name via customizations.xml
    wf_meta: dict[str, dict] = {}
    if "customizations.xml" in names:
        try:
            root = ET.fromstring(zf.read("customizations.xml"))
            for wf in root.iter("Workflow"):
                jf = (wf.findtext("JsonFileName") or "").lstrip("/")
                if jf:
                    wf_meta[jf] = {"workflowId": (wf.get("WorkflowId") or "").strip("{}"),
                                   "displayName": wf.get("Name") or ""}
        except ET.ParseError:
            notes.append("customizations.xml could not be parsed; flow ids not recovered")
    for n in names:
        if not (n.startswith("Workflows/") and n.endswith(".json")):
            continue
        meta = wf_meta.get(n, {})
        stem = re.sub(r"-[0-9A-Fa-f-]{36}$", "", n.rsplit("/", 1)[1][:-5])
        folder = f"workflows/{_safe(stem)}"
        try:
            wf_json = json.loads(zf.read(n))
        except json.JSONDecodeError:
            notes.append(f"Skipped {n}: invalid JSON")
            continue
        meta_yaml = (f"displayName: {json.dumps(meta.get('displayName') or stem)}\n"
                     f"workflowId: {meta.get('workflowId', '')}\n")
        files.append(FileChange(path=f"{folder}/metadata.yaml", content=meta_yaml))
        files.append(FileChange(path=f"{folder}/workflow.json", content=json.dumps(wf_json, indent=2) + "\n"))

    if not files:
        raise ValueError("No Copilot Studio components found in this zip. Is it a solution export containing an agent?")
    notes.append("Imported from a solution: not attached to an environment. Copy into a folder cloned with the "
                 "Copilot Studio VS Code extension, or use 'Reattach agent', to sync.")
    return ChangeSet(summary=f"Imported {len(files)} files", notes=notes, files=files), agent_name
