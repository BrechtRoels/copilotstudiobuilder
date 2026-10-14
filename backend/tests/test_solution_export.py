"""Export as importable solution: patch a base solution shaped like a real Copilot Studio export."""
import io
import re
import zipfile

import pytest

from app.ai.mock import ESCALATE_TOPIC
from app.exporters.solution_patch import SolutionError, build_solution, inspect_base
from app.importers.solution_zip import import_solution
from app.workspace import store

BOT = "cr1a2_itHelpdesk"

SOLUTION_XML = """<ImportExportXml version="9.2.26053.126" SolutionPackageVersion="9.2" languagecode="1033" generatedBy="CrmLive" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <SolutionManifest>
    <UniqueName>ITHelpdesk</UniqueName>
    <LocalizedNames><LocalizedName description="IT Helpdesk" languagecode="1033" /></LocalizedNames>
    <Descriptions />
    <Version>1.0.0.3</Version>
    <Managed>{managed}</Managed>
    <Publisher><UniqueName>contoso</UniqueName><CustomizationPrefix>cr1a2</CustomizationPrefix></Publisher>
    <RootComponents />
    <MissingDependencies />
  </SolutionManifest>
</ImportExportXml>"""

CUSTOMIZATIONS_XML = """<ImportExportXml xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <Entities></Entities>
  <Roles></Roles>
  <Workflows></Workflows>
  <Languages><Language>1033</Language></Languages>
</ImportExportXml>"""

CONTENT_TYPES = ('﻿<?xml version="1.0" encoding="utf-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                 '<Default Extension="xml" ContentType="application/octet-stream" /><Default Extension="json" ContentType="application/octet-stream" />'
                 f'<Override PartName="/botcomponents/{BOT}.topic.Escalate/data" ContentType="application/octet-stream" /></Types>')

BC_XML = f"""<botcomponent schemaname="{BOT}.topic.Escalate">
  <componenttype>9</componenttype>
  <description>Escalate</description>
  <iscustomizable>0</iscustomizable>
  <name>Escalate</name>
  <parentbotid>
    <schemaname>{BOT}</schemaname>
  </parentbotid>
  <statecode>0</statecode>
  <statuscode>1</statuscode>
</botcomponent>"""

CONFIG = '{"$kind": "BotConfiguration", "agentSettings": {"$kind": "AgentSettings", "instructions": {"$kind": "Instructions", "segments": [{"$kind": "StaticSegment", "value": "old"}]}}}'


def make_base(managed: int = 0) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", CONTENT_TYPES.encode("utf-8"))
        zf.writestr("solution.xml", SOLUTION_XML.format(managed=managed))
        zf.writestr("customizations.xml", CUSTOMIZATIONS_XML)
        zf.writestr(f"bots/{BOT}/bot.xml", "<bot><name>IT Helpdesk &amp; Support</name></bot>")
        zf.writestr(f"bots/{BOT}/configuration.json", CONFIG)
        zf.writestr(f"botcomponents/{BOT}.topic.Escalate/botcomponent.xml", BC_XML)
        zf.writestr(f"botcomponents/{BOT}.topic.Escalate/data", "kind: AdaptiveDialog\n")
    return buf.getvalue()


@pytest.fixture
def exported(client, workspace):
    ws = store.get_workspace(workspace["id"])
    data, report = build_solution(ws, make_base())
    return zipfile.ZipFile(io.BytesIO(data)), report


def test_inspect_base():
    info = inspect_base(make_base())
    assert info.uniqueName == "ITHelpdesk" and info.version == "1.0.0.3"
    assert info.bots[0] == {"schema": BOT, "name": "IT Helpdesk & Support", "components": 1}


def test_rejects_non_solution_and_managed(workspace):
    ws = store.get_workspace(workspace["id"])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("agent.mcs.yml", "kind: GptComponentMetadata\n")
    with pytest.raises(SolutionError, match="solution.xml"):
        build_solution(ws, buf.getvalue())
    with pytest.raises(SolutionError, match="managed"):
        build_solution(ws, make_base(managed=1))


def test_root_files_version_and_overrides(exported):
    zf, report = exported
    names = zf.namelist()
    assert names[:3] == ["[Content_Types].xml", "solution.xml", "customizations.xml"]
    assert report.version == "1.0.0.4"
    assert "<Version>1.0.0.4</Version>" in zf.read("solution.xml").decode()
    ct = zf.read("[Content_Types].xml").decode("utf-8-sig")
    data_parts = [n for n in names if n.startswith("botcomponents/") and n.endswith("/data")]
    for part in data_parts:  # every extensionless part needs an Override
        assert f'PartName="/{part}"' in ct
    assert zf.read("[Content_Types].xml").startswith(b"\xef\xbb\xbf")
    assert zf.read(f"bots/{BOT}/bot.xml") == b"<bot><name>IT Helpdesk &amp; Support</name></bot>"  # untouched


def test_existing_updated_new_added_and_redirects_rewritten(exported):
    zf, report = exported
    actions = {i.source: i.action for i in report.items}
    assert actions["topics/Escalate.mcs.yml"] == "update"
    assert actions["topics/ResetPassword.mcs.yml"] == "add"
    assert actions["agents/HRSpecialist/agent.mcs.yml"] == "add"
    reset = zf.read(f"botcomponents/{BOT}.topic.ResetPassword/data").decode()
    assert f"dialog: {BOT}.topic.Escalate" in reset          # was contoso_itHelpdesk.topic.Escalate
    assert not reset.startswith("#")
    xml = zf.read(f"botcomponents/{BOT}.topic.ResetPassword/botcomponent.xml").decode()
    assert f'schemaname="{BOT}.topic.ResetPassword"' in xml and "<name>Reset Password</name>" in xml
    assert f"<schemaname>{BOT}</schemaname>" in xml
    assert zf.read(f"botcomponents/{BOT}.topic.Escalate/data").decode().startswith("kind: AdaptiveDialog\nbeginDialog")


def test_instructions_and_flow(exported):
    zf, report = exported
    cfg = zf.read(f"bots/{BOT}/configuration.json").decode()
    assert "IT Helpdesk agent for Contoso" in cfg
    cust = zf.read("customizations.xml").decode()
    assert re.search(r'<Workflow WorkflowId="\{6f1c2a9e-0d1b-4c55-9a51-1d2a3b4c5d6e\}"', cust)
    assert "<Category>5</Category>" in cust
    wf = [n for n in zf.namelist() if n.startswith("Workflows/")]
    assert wf == ["Workflows/CreateHelpdeskTicket-6F1C2A9E-0D1B-4C55-9A51-1D2A3B4C5D6E.json"]
    assert 'type="29"' in zf.read("solution.xml").decode()


def test_export_reimports(exported):
    """The exported zip parses back into the same components with our own importer."""
    zf, _ = exported
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as out:
        for n in zf.namelist():
            out.writestr(n, zf.read(n))
    cs, _ = import_solution(buf.getvalue())
    paths = {f.path for f in cs.files}
    assert {"topics/ResetPassword.mcs.yml", "topics/Escalate.mcs.yml", "agents/HRSpecialist/agent.mcs.yml"} <= paths


def test_api_flow(client):
    ws = client.post("/api/workspaces", json={"name": "Api export"}).json()
    client.post(f"/api/workspaces/{ws['id']}/paste", json={"content": ESCALATE_TOPIC})
    base = client.get(f"/api/workspaces/{ws['id']}/solution/base").json()
    assert base["present"] is False and base["identity"]["prefix"] == "csb"
    assert client.get(f"/api/workspaces/{ws['id']}/solution/export").status_code == 200  # new-agent package
    r = client.post(f"/api/workspaces/{ws['id']}/solution/base", files={"file": ("base.zip", make_base(), "application/zip")})
    assert r.json()["uniqueName"] == "ITHelpdesk"
    plan = client.get(f"/api/workspaces/{ws['id']}/solution/plan").json()
    assert plan["items"][0]["action"] == "update"
    r = client.get(f"/api/workspaces/{ws['id']}/solution/export")
    assert r.status_code == 200 and r.content[:2] == b"PK"
    assert 'filename="ITHelpdesk_1_0_0_4.zip"' in r.headers["content-disposition"]


# --- new agent (no base solution) --------------------------------------------
from app.exporters.new_solution import SolutionIdentity, load_identity, new_base, save_identity  # noqa: E402
from app.validation.validator import validate_text  # noqa: E402


def test_new_agent_package(workspace):
    ws = store.get_workspace(workspace["id"])
    ident = load_identity(ws)
    assert ident.botSchema == "csb_ITHelpdesk" and ident.uniqueName == "ITHelpdesk"
    data, report = build_solution(ws, new_base(ws, ident))
    zf = zipfile.ZipFile(io.BytesIO(data))
    names = zf.namelist()
    assert names[:3] == ["[Content_Types].xml", "solution.xml", "customizations.xml"]
    bot_xml = zf.read("bots/csb_ITHelpdesk/bot.xml").decode()
    assert "<template>default-2.1.0</template>" in bot_xml and "<name>IT Helpdesk</name>" in bot_xml
    cfg = zf.read("bots/csb_ITHelpdesk/configuration.json").decode()
    assert '"defaultSchemaName": "csb_ITHelpdesk.gpt.default"' in cfg
    gpt = zf.read("botcomponents/csb_ITHelpdesk.gpt.default/data").decode()
    assert "IT Helpdesk agent for Contoso" in gpt and "displayName: IT Helpdesk" in gpt
    # system topics only where the workspace has no topic for that trigger (it has its own Escalate)
    assert "botcomponents/csb_ITHelpdesk.topic.Fallback/data" in names
    assert "botcomponents/csb_ITHelpdesk.topic.OnError/data" in names
    assert sum(1 for n in names if n.endswith("topic.Escalate/data")) == 1
    escalate = zf.read("botcomponents/csb_ITHelpdesk.topic.Escalate/data").decode()
    assert "service desk" in escalate  # the workspace's Escalate replaced the default
    # every component data is schema-valid
    for n in names:
        if n.startswith("botcomponents/") and n.endswith("/data"):
            rep = validate_text(n + ".mcs.yml", zf.read(n).decode())
            assert rep.valid, (n, rep.issues)
    # flow connection reference was a placeholder -> real logical name, declared for import mapping
    cust = zf.read("customizations.xml").decode()
    assert 'connectionreferencelogicalname="csb_sharedoffice365"' in cust
    assert "/providers/Microsoft.PowerApps/apis/shared_office365" in cust
    wf = zf.read([n for n in names if n.startswith("Workflows/")][0]).decode()
    assert '"connectionReferenceLogicalName": "csb_sharedoffice365"' in wf
    assert any("csb_sharedoffice365" in w for w in report.warnings)
    sol = zf.read("solution.xml").decode()
    assert "<CustomizationPrefix>csb</CustomizationPrefix>" in sol and "<Version>1.0.0.1</Version>" in sol


def test_identity_validation_and_persistence(workspace):
    ws = store.get_workspace(workspace["id"])
    ident = load_identity(ws).model_copy(update={"prefix": "pwc", "uniqueName": "HelpdeskAgent"})
    saved = save_identity(ws, ident)
    assert saved.botSchema == "pwc_ITHelpdesk"
    assert load_identity(ws).uniqueName == "HelpdeskAgent"
    with pytest.raises(ValueError):
        save_identity(ws, ident.model_copy(update={"prefix": "Bad-Prefix"}))
