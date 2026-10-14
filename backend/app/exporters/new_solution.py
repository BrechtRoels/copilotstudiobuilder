"""Generate a base solution for a brand-new classic Copilot Studio agent.

The structure mirrors a real classic-agent export (Microsoft CopilotStudioSamples,
AccountLookupAgent): bots/<schema>/{bot.xml, configuration.json} with template
default-2.1.0, a <schema>.gpt.default component (type 15) and system topics.
No RootComponent is needed for the agent: real exports include bots/botcomponents
through their folders alone. The workspace is then written into this base by
solution_patch.build_solution, exactly as for an uploaded base.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import struct
import zipfile
import zlib
from pathlib import Path
from xml.sax.saxutils import escape

from pydantic import BaseModel

from ..workspace.model import Workspace


class SolutionIdentity(BaseModel):
    """Stored per workspace so every export upgrades the same solution/agent."""
    uniqueName: str
    displayName: str
    botSchema: str
    botName: str
    publisherUniqueName: str = "CopilotStudioBuilder"
    publisherDisplayName: str = "Copilot Studio Builder"
    prefix: str = "csb"


def _pascal(s: str) -> str:
    return "".join(w[:1].upper() + w[1:] for w in re.findall(r"[A-Za-z0-9]+", s)) or "Agent"


def identity_file(ws: Workspace) -> Path:
    return Path(ws.info.path) / ".builder" / "solution.json"


def default_identity(ws: Workspace) -> SolutionIdentity:
    agent = ws.agent
    name = agent.displayName if agent else ws.info.name
    p = _pascal(name)[:40]
    return SolutionIdentity(uniqueName=p, displayName=name, botSchema=f"csb_{p}", botName=name)


def load_identity(ws: Workspace) -> SolutionIdentity:
    f = identity_file(ws)
    if f.exists():
        return SolutionIdentity(**json.loads(f.read_text()))
    return default_identity(ws)


def save_identity(ws: Workspace, ident: SolutionIdentity) -> SolutionIdentity:
    if not re.fullmatch(r"[a-z][a-z0-9]{1,7}", ident.prefix):
        raise ValueError("Publisher prefix must be 2-8 lowercase letters/digits, starting with a letter")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", ident.uniqueName):
        raise ValueError("Solution name must start with a letter and contain only letters, digits and _")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", ident.publisherUniqueName):
        raise ValueError("Publisher name must start with a letter and contain only letters, digits and _")
    if not ident.botSchema.startswith(ident.prefix + "_"):
        ident.botSchema = f"{ident.prefix}_{ident.botSchema.split('_', 1)[-1]}"
    f = identity_file(ws)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(ident.model_dump_json(indent=2))
    return ident


def _option_value_prefix(prefix: str) -> int:
    # Dataverse requires 10000-99999; derive a stable value from the prefix.
    return 10000 + int(hashlib.sha1(prefix.encode()).hexdigest(), 16) % 90000


def _icon_png(size: int = 96) -> str:
    """Solid PwC-orange PNG (no image libraries needed)."""
    row = b"\x00" + bytes([0xD0, 0x4A, 0x02]) * size
    raw = row * size

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
    return base64.b64encode(png).decode()


def _bc_xml(schema: str, ctype: int, name: str, description: str, bot: str) -> str:
    return (f'<botcomponent schemaname="{escape(schema)}">\n  <componenttype>{ctype}</componenttype>\n'
            f"  <description>{escape(description)}</description>\n  <iscustomizable>0</iscustomizable>\n"
            f"  <name>{escape(name)}</name>\n  <parentbotid>\n    <schemaname>{escape(bot)}</schemaname>\n"
            f"  </parentbotid>\n  <statecode>0</statecode>\n  <statuscode>1</statuscode>\n</botcomponent>")


def _system_topics(bot: str) -> dict[str, tuple[str, str, str, str]]:
    """trigger kind -> (schema tail, display name, description, data)."""
    return {
        "OnConversationStart": ("ConversationStart", "Conversation Start", "This system topic triggers when the agent receives an Activity indicating the beginning of a new conversation.",
                                "kind: AdaptiveDialog\nbeginDialog:\n  kind: OnConversationStart\n  id: main\n  actions:\n"
                                "    - kind: SendActivity\n      id: sendMessage_csbSt1\n      activity:\n        text:\n"
                                "          - Hello, I'm {System.Bot.Name}. How can I help?\n"),
        "OnUnknownIntent": ("Fallback", "Fallback", "This system topic triggers when the user's utterance does not match any existing topics.",
                            "kind: AdaptiveDialog\nbeginDialog:\n  kind: OnUnknownIntent\n  id: main\n  actions:\n"
                            "    - kind: ConditionGroup\n      id: conditionGroup_csbFb1\n      conditions:\n"
                            "        - id: conditionItem_csbFb2\n          condition: =System.FallbackCount < 3\n          actions:\n"
                            "            - kind: SendActivity\n              id: sendMessage_csbFb3\n"
                            "              activity: I'm sorry, I'm not sure how to help with that. Can you try rephrasing?\n\n"
                            "      elseActions:\n        - kind: BeginDialog\n          id: beginDialog_csbFb4\n"
                            f"          dialog: {bot}.topic.Escalate\n"),
        "OnEscalate": ("Escalate", "Escalate", "This system topic is triggered when the user indicates they would like to speak to a human agent.",
                       "kind: AdaptiveDialog\nbeginDialog:\n  kind: OnEscalate\n  id: main\n  intent:\n    triggerQueries:\n"
                       "      - Talk to agent\n      - Talk to a person\n      - Speak to a representative\n  actions:\n"
                       "    - kind: SendActivity\n      id: sendMessage_csbEs1\n"
                       "      activity: Escalating to a representative is not currently configured for this agent.\n"),
        "OnError": ("OnError", "On Error", "This system topic triggers when the agent encounters an error.",
                    "kind: AdaptiveDialog\nstartBehavior: UseLatestPublishedContentAndCancelOtherTopics\nbeginDialog:\n"
                    "  kind: OnError\n  id: main\n  actions:\n    - kind: SetVariable\n      id: setVariable_csbEr1\n"
                    "      variable: init:Topic.CurrentTime\n      value: =Text(Now(), DateTimeFormat.UTC)\n\n"
                    "    - kind: ConditionGroup\n      id: conditionGroup_csbEr2\n      conditions:\n"
                    "        - id: conditionItem_csbEr3\n          condition: =System.Conversation.InTestMode = true\n"
                    "          actions:\n            - kind: SendActivity\n              id: sendMessage_csbEr4\n"
                    "              activity: \"Error: {System.Error.Message} (code {System.Error.Code}, {Topic.CurrentTime} UTC)\"\n\n"
                    "      elseActions:\n        - kind: SendActivity\n          id: sendMessage_csbEr5\n"
                    "          activity: \"An error has occurred. Error code: {System.Error.Code}. Conversation Id: {System.Conversation.Id}.\"\n\n"
                    "    - kind: CancelAllDialogs\n      id: cancelAllDialogs_csbEr6\n"),
    }


def _ws_triggers(ws: Workspace) -> set[str]:
    out = set()
    for c in ws.by_category("topic"):
        bd = c.data.get("beginDialog") if isinstance(c.data, dict) else None
        if isinstance(bd, dict) and bd.get("kind"):
            out.add(str(bd["kind"]))
    return out


def new_base(ws: Workspace, ident: SolutionIdentity) -> bytes:
    bot = ident.botSchema
    parts: dict[str, str] = {}
    overrides: list[str] = []

    parts["solution.xml"] = f"""<ImportExportXml version="9.2.25123.162" SolutionPackageVersion="9.2" languagecode="1033" generatedBy="CrmLive" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <SolutionManifest>
    <UniqueName>{escape(ident.uniqueName)}</UniqueName>
    <LocalizedNames>
      <LocalizedName description="{escape(ident.displayName)}" languagecode="1033" />
    </LocalizedNames>
    <Descriptions />
    <Version>1.0.0.0</Version>
    <Managed>0</Managed>
    <Publisher>
      <UniqueName>{escape(ident.publisherUniqueName)}</UniqueName>
      <LocalizedNames>
        <LocalizedName description="{escape(ident.publisherDisplayName)}" languagecode="1033" />
      </LocalizedNames>
      <Descriptions />
      <EMailAddress xsi:nil="true"></EMailAddress>
      <SupportingWebsiteUrl xsi:nil="true"></SupportingWebsiteUrl>
      <CustomizationPrefix>{escape(ident.prefix)}</CustomizationPrefix>
      <CustomizationOptionValuePrefix>{_option_value_prefix(ident.prefix)}</CustomizationOptionValuePrefix>
      <Addresses />
    </Publisher>
    <RootComponents />
    <MissingDependencies />
  </SolutionManifest>
</ImportExportXml>"""
    parts["customizations.xml"] = """<ImportExportXml xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <Entities></Entities>
  <Roles></Roles>
  <Workflows></Workflows>
  <FieldSecurityProfiles></FieldSecurityProfiles>
  <Templates />
  <EntityMaps />
  <EntityRelationships />
  <OrganizationSettings />
  <optionsets />
  <CustomControls />
  <EntityDataProviders />
  <Languages>
    <Language>1033</Language>
  </Languages>
</ImportExportXml>"""
    parts[f"bots/{bot}/bot.xml"] = (f'<bot schemaname="{escape(bot)}">\n  <authenticationmode>2</authenticationmode>\n'
                                    f"  <authenticationtrigger>1</authenticationtrigger>\n  <iconbase64>{_icon_png()}</iconbase64>\n"
                                    f"  <iscustomizable>0</iscustomizable>\n  <language>1033</language>\n"
                                    f"  <name>{escape(ident.botName)}</name>\n  <runtimeprovider>0</runtimeprovider>\n"
                                    f"  <template>default-2.1.0</template>\n  <timezoneruleversionnumber>4</timezoneruleversionnumber>\n</bot>")
    parts[f"bots/{bot}/configuration.json"] = json.dumps({
        "$kind": "BotConfiguration",
        "settings": {"GenerativeActionsEnabled": ws.generative_orchestration()},
        "isAgentConnectable": True,
        "gPTSettings": {"$kind": "GPTSettings", "defaultSchemaName": f"{bot}.gpt.default"},
        "aISettings": {"$kind": "AISettings", "useModelKnowledge": True, "isFileAnalysisEnabled": True,
                       "isSemanticSearchEnabled": True, "optInUseLatestModels": False},
        "recognizer": {"$kind": "GenerativeAIRecognizer"},
    }, indent=2)

    gpt = f"{bot}.gpt.default"
    parts[f"botcomponents/{gpt}/botcomponent.xml"] = _bc_xml(gpt, 15, ident.botName, ident.displayName, bot)
    parts[f"botcomponents/{gpt}/data"] = (f"kind: GptComponentMetadata\ndisplayName: {json.dumps(ident.botName)}\n"
                                          "instructions: \"\"\n")
    overrides.append(f"botcomponents/{gpt}/data")

    have = _ws_triggers(ws)
    for trigger, (tail, name, desc, data) in _system_topics(bot).items():
        if trigger in have:
            continue
        schema = f"{bot}.topic.{tail}"
        parts[f"botcomponents/{schema}/botcomponent.xml"] = _bc_xml(schema, 9, name, desc, bot)
        parts[f"botcomponents/{schema}/data"] = data
        overrides.append(f"botcomponents/{schema}/data")

    ct = ('﻿<?xml version="1.0" encoding="utf-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="xml" ContentType="application/octet-stream" />'
          '<Default Extension="json" ContentType="application/octet-stream" />'
          + "".join(f'<Override PartName="/{escape(o)}" ContentType="application/octet-stream" />' for o in overrides)
          + "</Types>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", ct.encode("utf-8"))
        for name, text in parts.items():
            zf.writestr(name, text.encode("utf-8"))
    return buf.getvalue()
