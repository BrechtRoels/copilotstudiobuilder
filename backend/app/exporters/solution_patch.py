"""Workspace -> importable Dataverse solution .zip, by patching a real exported solution.

Why patch instead of generate: a solution's bots/<schema>/bot.xml is ~35 KB of
environment-specific configuration (publisher, auth, channels, ...). Forging it
gives imports that fail in obscure ways. Instead the maker exports their agent's
solution once (Copilot Studio / Power Apps -> Solutions -> Export -> Unmanaged)
and we write the workspace into it:

  * existing botcomponents (matched by schema-name tail) get their `data` replaced
  * new topics / tools / child agents / knowledge get botcomponents/<schema>/{botcomponent.xml,data}
    plus an Override in [Content_Types].xml (extensionless parts need one)
  * agent instructions go to the GPT component, or to bots/<bot>/configuration.json
    on newer agents that keep them there
  * existing Workflows/*.json are replaced; new flows get a customizations.xml
    <Workflow> entry + RootComponent type 29 (experimental)
  * solution.xml <Version> is bumped; every other part is copied byte-for-byte

Layout facts verified against an exported solution from microsoft/copilot-studio-gallery.
botcomponent.componenttype values: Microsoft Learn botcomponent table reference.
"""
from __future__ import annotations

import io
import json
import re
import uuid
import zipfile
from dataclasses import dataclass
from html import unescape
from typing import Callable
from xml.sax.saxutils import escape, quoteattr

from pydantic import BaseModel

from ..graph.refs import REDIRECT_KINDS, resolve_dialog
from ..workspace.model import Component, Workspace
from ..workspace.yamlio import dump_rt, load_rt

# botcomponent.componenttype (Microsoft Learn: botcomponent table)
COMPONENT_TYPE = {"AdaptiveDialog": 9, "TaskDialog": 9, "AgentDialog": 9, "GptComponentMetadata": 15,
                  "KnowledgeSourceConfiguration": 16, "GlobalVariableComponent": 12}
# schema-name infix Copilot Studio uses per kind (first match in the base solution wins)
INFIXES = {"AdaptiveDialog": ["topic"], "TaskDialog": ["action", "tool"], "AgentDialog": ["agent"],
           "KnowledgeSourceConfiguration": ["knowledge"], "GlobalVariableComponent": ["variable", "globalvariable"],
           "GptComponentMetadata": ["gpt"]}
WORKFLOW_COMPONENT_TYPE = 29  # solution component type for processes/flows (fixed)


class SolutionError(ValueError):
    pass


class BaseInfo(BaseModel):
    uniqueName: str
    displayName: str
    version: str
    managed: bool
    bots: list[dict]


class ExportItem(BaseModel):
    source: str          # workspace component id
    target: str          # part path in the solution
    action: str          # update | add | skip
    note: str = ""


class ExportReport(BaseModel):
    bot: str
    version: str
    items: list[ExportItem]
    warnings: list[str]


@dataclass
class _Pkg:
    order: list[str]
    parts: dict[str, bytes]

    def text(self, name: str) -> str:
        raw = self.parts[name]
        return raw.decode("utf-8-sig")

    def put(self, name: str, data: bytes | str) -> None:
        if isinstance(data, str):
            data = data.encode("utf-8")
        if name not in self.parts:
            self.order.append(name)
        self.parts[name] = data


def _read(base: bytes) -> _Pkg:
    try:
        zf = zipfile.ZipFile(io.BytesIO(base))
    except zipfile.BadZipFile as e:
        raise SolutionError("Base solution is not a valid .zip") from e
    order, parts = [], {}
    for i in zf.infolist():
        if not i.is_dir():
            order.append(i.filename)
            parts[i.filename] = zf.read(i)
    for required in ("solution.xml", "customizations.xml", "[Content_Types].xml"):
        if required not in parts:
            raise SolutionError(f"Base is not an exported solution: {required} missing at the root")
    return _Pkg(order, parts)


def _tag(xml: str, tag: str) -> str | None:
    m = re.search(rf"<{tag}>(.*?)</{tag}>", xml, re.S)
    return m.group(1).strip() if m else None


def _bots(pkg: _Pkg) -> list[dict]:
    schemas = sorted({n.split("/")[1] for n in pkg.order if re.match(r"bots/[^/]+/bot\.xml$", n)})
    owners: dict[str, int] = {}
    for n in pkg.order:
        if n.startswith("botcomponents/") and n.endswith("/botcomponent.xml"):
            parent = _tag(_tag(pkg.text(n), "parentbotid") or "", "schemaname")
            if parent:
                owners[parent] = owners.get(parent, 0) + 1
    out = []
    for s in schemas:
        name = unescape(_tag(pkg.text(f"bots/{s}/bot.xml"), "name") or s)
        out.append({"schema": s, "name": name, "components": owners.get(s, 0)})
    return sorted(out, key=lambda b: -b["components"])


def inspect_base(base: bytes) -> BaseInfo:
    pkg = _read(base)
    sol = pkg.text("solution.xml")
    m = re.search(r'<LocalizedName description="([^"]*)"', sol)
    bots = _bots(pkg)
    if not bots:
        raise SolutionError("This solution contains no agent (no bots/ folder). Add the agent to the solution and export again.")
    return BaseInfo(uniqueName=_tag(sol, "UniqueName") or "", displayName=m.group(1) if m else "",
                    version=_tag(sol, "Version") or "1.0.0.0", managed=_tag(sol, "Managed") == "1", bots=bots)


# --- helpers -----------------------------------------------------------------
def _rewrite_dialogs(node, rewrite: Callable[[str], str | None]) -> None:
    if isinstance(node, dict):
        if node.get("kind") in REDIRECT_KINDS and isinstance(node.get("dialog"), str):
            new = rewrite(node["dialog"])
            if new:
                node["dialog"] = new
        for v in node.values():
            _rewrite_dialogs(v, rewrite)
    elif isinstance(node, list):
        for v in node:
            _rewrite_dialogs(v, rewrite)


def _component_data(c: Component, rewrite: Callable[[str], str | None] | None = None) -> str:
    """Workspace YAML -> solution `data`: drop VS Code wrapper keys and header comments,
    and point redirects at the schema names the components have in this solution."""
    doc = load_rt(c.text)
    if rewrite:
        _rewrite_dialogs(doc, rewrite)
    if isinstance(doc, dict):
        doc.pop("mcs.metadata", None)  # VS Code extension wrapper; real exports keep displayName
        if hasattr(doc, "ca"):
            doc.ca.comment = None
    text = dump_rt(doc)
    return re.sub(r"\A(\s*#.*\n)+", "", text)


def _botcomponents(pkg: _Pkg) -> dict[str, dict]:
    out = {}
    for n in pkg.order:
        m = re.match(r"botcomponents/([^/]+)/botcomponent\.xml$", n)
        if m:
            xml = pkg.text(n)
            out[m.group(1)] = {"xml": xml, "type": _tag(xml, "componenttype"),
                               "parent": _tag(_tag(xml, "parentbotid") or "", "schemaname")}
    return out


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _find_existing(comps: dict[str, dict], bot: str, kind: str, stem: str) -> str | None:
    key = _norm(stem)
    for schema, info in comps.items():
        if info["parent"] not in (bot, None) or not schema.startswith(bot + "."):
            continue
        rest = schema[len(bot) + 1:]
        infix, _, tail = rest.partition(".")
        if infix in INFIXES.get(kind, []) and (_norm(tail) == key or _norm(re.sub(r"_[A-Za-z0-9]{3}$", "", tail)) == key):
            return schema
    return None


def _infix(comps: dict[str, dict], bot: str, kind: str) -> str:
    for cand in INFIXES[kind]:
        if any(s.startswith(f"{bot}.{cand}.") for s in comps):
            return cand
    return INFIXES[kind][0]


def _new_schema(bot: str, infix: str, stem: str, taken: set[str]) -> str:
    safe = re.sub(r"[^A-Za-z0-9_\-]", "", stem) or "Component"
    base = f"{bot}.{infix}.{safe}"[:96]
    schema, n = base, 2
    while schema in taken:
        schema = f"{base[:93]}_{n}"
        n += 1
    taken.add(schema)
    return schema


def _botcomponent_xml(schema: str, ctype: int, name: str, description: str, bot: str, template: str | None) -> str:
    if template:
        xml = re.sub(r'schemaname="[^"]*"', f"schemaname={quoteattr(schema)}", template, count=1)
        xml = re.sub(r"<name>.*?</name>", f"<name>{escape(name)}</name>", xml, count=1, flags=re.S)
        if "<description>" in xml:
            xml = re.sub(r"<description>.*?</description>", f"<description>{escape(description)}</description>", xml, count=1, flags=re.S)
        return xml
    return (f'<botcomponent schemaname={quoteattr(schema)}>\n'
            f"  <componenttype>{ctype}</componenttype>\n"
            f"  <description>{escape(description)}</description>\n"
            f"  <iscustomizable>1</iscustomizable>\n"
            f"  <name>{escape(name)}</name>\n"
            f"  <parentbotid>\n    <schemaname>{escape(bot)}</schemaname>\n  </parentbotid>\n"
            f"  <statecode>0</statecode>\n  <statuscode>1</statuscode>\n</botcomponent>")


def _add_override(pkg: _Pkg, part: str) -> None:
    ct = pkg.text("[Content_Types].xml")
    pn = "/" + part
    if f'PartName="{pn}"' in ct:
        return
    ct = ct.replace("</Types>", f'<Override PartName={quoteattr(pn)} ContentType="application/octet-stream" /></Types>')
    bom = "﻿" if pkg.parts["[Content_Types].xml"].startswith(b"\xef\xbb\xbf") else ""
    pkg.put("[Content_Types].xml", bom + ct)


def _bump(version: str) -> str:
    parts = (version.split(".") + ["0", "0", "0", "0"])[:4]
    parts[3] = str(int(parts[3]) + 1 if parts[3].isdigit() else 1)
    return ".".join(parts)


def _workflow_xml(wid: str, name: str, json_path: str) -> str:
    return (f'<Workflow WorkflowId="{{{wid}}}" Name={quoteattr(name)}>'
            f"<JsonFileName>/{json_path}</JsonFileName><Type>1</Type><Subprocess>0</Subprocess><Category>5</Category>"
            f"<Mode>0</Mode><Scope>4</Scope><OnDemand>0</OnDemand><TriggerOnCreate>0</TriggerOnCreate>"
            f"<TriggerOnDelete>0</TriggerOnDelete><AsyncAutodelete>0</AsyncAutodelete>"
            f"<SyncWorkflowLogOnFailure>0</SyncWorkflowLogOnFailure><StateCode>1</StateCode><StatusCode>2</StatusCode>"
            f"<RunAs>1</RunAs><IsTransacted>1</IsTransacted><IntroducedVersion>1.0.0.0</IntroducedVersion>"
            f"<IsCustomizable>1</IsCustomizable><BusinessProcessType>0</BusinessProcessType>"
            f"<IsCustomProcessingStepAllowedForOtherPublishers>1</IsCustomProcessingStepAllowedForOtherPublishers>"
            f"<PrimaryEntity>none</PrimaryEntity><LocalizedNames><LocalizedName languagecode=\"1033\" "
            f"description={quoteattr(name)} /></LocalizedNames></Workflow>")


def _connection_reference_xml(logical: str, api: str) -> str:
    return (f'<connectionreference connectionreferencelogicalname={quoteattr(logical)}>'
            f"<connectionreferencedisplayname>{escape(logical)}</connectionreferencedisplayname>"
            f"<connectorid>/providers/Microsoft.PowerApps/apis/{escape(api)}</connectorid>"
            f"<iscustomizable>1</iscustomizable><promptingbehavior>0</promptingbehavior>"
            f"<statecode>0</statecode><statuscode>1</statuscode></connectionreference>")


def _ensure_connection_refs(wf: dict, prefix: str, cust: str) -> tuple[dict, str, list[str]]:
    """Give every flow connection a real logical name and declare it in customizations.xml,
    so Power Apps asks the maker to pick a connection during import."""
    wf = json.loads(json.dumps(wf))
    refs = (wf.get("properties") or {}).get("connectionReferences") or {}
    added = []
    for key, ref in refs.items():
        if not isinstance(ref, dict):
            continue
        api = str((ref.get("api") or {}).get("name") or key)
        conn = ref.setdefault("connection", {})
        logical = str(conn.get("connectionReferenceLogicalName") or "")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.\-]*", logical):
            logical = f"{prefix}_{re.sub(r'[^a-z0-9]', '', api.lower())}"
            conn["connectionReferenceLogicalName"] = logical
        if f'connectionreferencelogicalname="{logical}"' in cust:
            continue
        el = _connection_reference_xml(logical, api)
        if "</connectionreferences>" in cust:
            cust = cust.replace("</connectionreferences>", f"{el}</connectionreferences>", 1)
        elif "<Languages>" in cust:
            cust = cust.replace("<Languages>", f"<connectionreferences>{el}</connectionreferences>\n  <Languages>", 1)
        else:
            cust = cust.replace("</ImportExportXml>", f"<connectionreferences>{el}</connectionreferences></ImportExportXml>", 1)
        added.append(logical)
    return wf, cust, added


def _write(pkg: _Pkg) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # Dataverse wants these three at the root; keep them first like real exports.
        first = ["[Content_Types].xml", "solution.xml", "customizations.xml"]
        for name in first + [n for n in pkg.order if n not in first]:
            zf.writestr(name, pkg.parts[name])
    return buf.getvalue()


# --- main --------------------------------------------------------------------
def build_solution(ws: Workspace, base: bytes, bot: str | None = None) -> tuple[bytes, ExportReport]:
    pkg = _read(base)
    info = inspect_base(base)
    if info.managed:
        raise SolutionError("The base solution is managed. Export it as Unmanaged so it can be updated.")
    bot = bot or info.bots[0]["schema"]
    if bot not in {b["schema"] for b in info.bots}:
        raise SolutionError(f"Agent {bot} isn't in the base solution")

    comps = _botcomponents(pkg)
    taken = set(comps)
    templates = {i["type"]: i["xml"] for i in comps.values() if i["parent"] == bot}
    items: list[ExportItem] = []
    warnings: list[str] = []
    sol = pkg.text("solution.xml")
    cust = pkg.text("customizations.xml")
    prefix = _tag(sol, "CustomizationPrefix") or "new"
    root_components: list[str] = []

    # Pass 1: final schema name for every exportable component, so redirects can be rewritten.
    schema_of: dict[str, tuple[str, bool]] = {}   # component id -> (schema, is_new)
    for c in ws.components:
        if c.error or not isinstance(c.data, dict) or c.kind not in COMPONENT_TYPE or c.kind == "GptComponentMetadata":
            continue
        if c.owner and c.category != "childAgent":
            continue
        stem = c.owner if c.category == "childAgent" and c.owner else c.name
        existing = _find_existing(comps, bot, c.kind, stem)
        schema_of[c.id] = (existing, False) if existing else (_new_schema(bot, _infix(comps, bot, c.kind), stem, taken), True)

    def rewrite(ref: str) -> str | None:
        target = resolve_dialog(ws, ref)
        return schema_of[target.id][0] if target and target.id in schema_of else None

    # Agent-wide AI settings (file analysis, model knowledge) from settings.mcs.yml `configuration.aISettings`.
    ai = (ws.settings.get("configuration") or {}).get("aISettings") if isinstance(ws.settings.get("configuration"), dict) else None
    cfg_path = f"bots/{bot}/configuration.json"
    if isinstance(ai, dict) and ai and cfg_path in pkg.parts:
        cfg = json.loads(pkg.text(cfg_path))
        cfg["aISettings"] = {"$kind": "AISettings", **(cfg.get("aISettings") or {}),
                             **{k: v for k, v in ai.items() if k != "kind"}}
        pkg.put(cfg_path, json.dumps(cfg, indent=2))
        items.append(ExportItem(source="settings.mcs.yml", target=cfg_path, action="update",
                                note="AI settings: " + ", ".join(f"{k}={v}" for k, v in ai.items() if k != "kind")))

    # Pass 2: write parts.
    for c in ws.components:
        if c.error or not isinstance(c.data, dict):
            continue
        kind = c.kind
        if c.owner and c.category != "childAgent":
            items.append(ExportItem(source=c.id, target="", action="skip",
                                    note="Child-agent knowledge: add after the child agent exists (Microsoft's two-phase rule)"))
            continue

        if kind == "GptComponentMetadata":
            target = _find_existing(comps, bot, kind, "default") or next(
                (s for s in comps if s.startswith(f"{bot}.gpt.")), None)
            if target:
                pkg.put(f"botcomponents/{target}/data", _component_data(c))
                items.append(ExportItem(source=c.id, target=f"botcomponents/{target}/data", action="update"))
                continue
            cfg_path = f"bots/{bot}/configuration.json"
            if cfg_path in pkg.parts:
                cfg = json.loads(pkg.text(cfg_path))
                instr = str(c.data.get("instructions") or "")
                agent_settings = cfg.setdefault("agentSettings", {"$kind": "AgentSettings"})
                agent_settings["instructions"] = {"$kind": "Instructions",
                                                  "segments": [{"$kind": "StaticSegment", "value": instr}]}
                pkg.put(cfg_path, json.dumps(cfg, indent=2))
                items.append(ExportItem(source=c.id, target=cfg_path, action="update",
                                        note="Instructions only; conversation starters are set in Copilot Studio for this agent type"))
            else:
                items.append(ExportItem(source=c.id, target="", action="skip", note="No place for agent instructions in the base"))
            continue

        if kind not in COMPONENT_TYPE:
            if c.category not in ("settings", "connectionReferences", "workflow"):
                items.append(ExportItem(source=c.id, target="", action="skip", note=f"Kind {kind} isn't exported to solutions"))
            continue

        schema, is_new = schema_of[c.id]
        if not is_new:
            pkg.put(f"botcomponents/{schema}/data", _component_data(c, rewrite))
            items.append(ExportItem(source=c.id, target=f"botcomponents/{schema}/data", action="update"))
            continue

        ctype = COMPONENT_TYPE[kind]
        bd = c.data.get("beginDialog")
        desc = str(c.data.get("modelDescription") or (bd.get("description") if isinstance(bd, dict) else None)
                   or c.displayName)
        pkg.put(f"botcomponents/{schema}/botcomponent.xml",
                _botcomponent_xml(schema, ctype, c.displayName, desc[:2000], bot, templates.get(str(ctype))))
        pkg.put(f"botcomponents/{schema}/data", _component_data(c, rewrite))
        _add_override(pkg, f"botcomponents/{schema}/data")
        note = ""
        if kind in ("GlobalVariableComponent", "KnowledgeSourceConfiguration") and str(ctype) not in templates:
            note = "First component of this type in the package; check it in Copilot Studio after import"
            warnings.append(f"{c.displayName}: {note}")
        items.append(ExportItem(source=c.id, target=f"botcomponents/{schema}", action="add", note=note))

        # If the base lists botcomponents individually as root components, list ours too.
        rc = re.search(r'<RootComponent type="(\d+)" schemaName="([^"]+)"[^>]*/>', sol)
        if rc and rc.group(2) in comps:
            root_components.append(f'<RootComponent type="{rc.group(1)}" schemaName={quoteattr(schema)} behavior="0" />')

    # flows
    existing_wf = {}
    for m in re.finditer(r'<Workflow WorkflowId="\{?([0-9a-fA-F-]{36})\}?"[^>]*>.*?<JsonFileName>/?([^<]+)</JsonFileName>', cust, re.S):
        existing_wf[m.group(1).lower()] = m.group(2)
    for c in ws.by_category("workflow"):
        meta = (c.data or {}).get("metadata") or {}
        wf = (c.data or {}).get("workflow")
        if not wf:
            continue
        wid = str(meta.get("workflowId") or "").strip("{}").lower()
        wf, cust, new_refs = _ensure_connection_refs(wf, prefix, cust)
        if new_refs:
            warnings.append(f"{c.displayName}: map connection reference(s) {', '.join(new_refs)} to a connection during import")
        body = json.dumps(wf, indent=2)
        if wid in existing_wf:
            pkg.put(existing_wf[wid], body)
            items.append(ExportItem(source=c.id, target=existing_wf[wid], action="update"))
            continue
        try:
            uuid.UUID(wid)
        except ValueError:
            wid = str(uuid.uuid4())
            warnings.append(f"{c.displayName}: workflowId wasn't a GUID; a new one was assigned, so tools pointing at the old id need updating")
        json_path = f"Workflows/{re.sub(r'[^A-Za-z0-9]', '', c.name) or 'Flow'}-{wid.upper()}.json"
        pkg.put(json_path, body)
        entry = _workflow_xml(wid, c.displayName, json_path)
        if re.search(r"<Workflows\s*/>", cust):
            cust = re.sub(r"<Workflows\s*/>", f"<Workflows>{entry}</Workflows>", cust, count=1)
        elif "</Workflows>" in cust:
            cust = cust.replace("</Workflows>", f"{entry}</Workflows>", 1)
        else:
            cust = cust.replace("</ImportExportXml>", f"<Workflows>{entry}</Workflows></ImportExportXml>", 1)
        root_components.append(f'<RootComponent type="{WORKFLOW_COMPONENT_TYPE}" id="{{{wid}}}" behavior="0" />')
        items.append(ExportItem(source=c.id, target=json_path, action="add",
                                note="New agent flow: after import, check it's turned on in Power Automate"))

    if root_components:
        joined = "".join(root_components)
        if re.search(r"<RootComponents\s*/>", sol):
            sol = re.sub(r"<RootComponents\s*/>", f"<RootComponents>{joined}</RootComponents>", sol, count=1)
        else:
            sol = sol.replace("</RootComponents>", f"{joined}</RootComponents>", 1)
    new_version = _bump(info.version)
    sol = re.sub(r"<Version>[^<]*</Version>", f"<Version>{new_version}</Version>", sol, count=1)
    pkg.put("solution.xml", sol)
    pkg.put("customizations.xml", cust)

    bad = [i for i in items if i.action == "skip"]
    if bad:
        warnings.append(f"{len(bad)} component(s) skipped; see the report")
    return _write(pkg), ExportReport(bot=bot, version=new_version, items=items, warnings=warnings)
