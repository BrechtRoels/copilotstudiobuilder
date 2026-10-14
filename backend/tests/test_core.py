import io
import json
import zipfile
from pathlib import Path

import pytest

from app import config
from app.ai.autofix import fix_ids
from app.graph.flow_graph import flow_graph
from app.graph.topic_graph import topic_graph
from app.validation.validator import validate_text
from app.workspace import patch
from app.workspace.yamlio import load_plain

TEMPLATES = sorted((config.KNOWLEDGE_DIR / "templates").rglob("*.topic.mcs.yml"))


# --- validator ---------------------------------------------------------------------
@pytest.mark.parametrize("tpl", TEMPLATES, ids=lambda p: p.name)
def test_templates_valid_once_ids_are_fixed(tpl: Path):
    """Microsoft's topic templates pass the schema once `_REPLACE` ids are replaced."""
    text = fix_ids(tpl.read_text())
    rep = validate_text(tpl.name, text)
    # conversation-init ships with an empty `modelDescription:` for the maker to fill in.
    errors = [i for i in rep.issues if i.severity == "error" and "None is not of type" not in i.message]
    assert not errors, errors


def test_validator_catches_bad_kind_unknown_property_and_dup_ids():
    bad = """kind: AdaptiveDialog
beginDialog:
  kind: OnRecognizedIntent
  id: main
  intent: {displayName: T, triggerQueries: [hi]}
  actions:
    - {kind: SendActivty, id: a1, activity: hi}
    - {kind: SendActivity, id: a1, activity: hi, colour: red}
    - {kind: SetVariable, id: a3, variable: init:Topic.X, value: =Foo(1)}
"""
    rules = {(i.rule, i.severity) for i in validate_text("topics/t.mcs.yml", bad).issues}
    assert ("schema", "error") in rules
    assert ("unique-id", "error") in rules
    assert ("powerfx", "warning") in rules


def test_child_agent_with_actions_is_error():
    text = """kind: AgentDialog
beginDialog:
  kind: OnToolSelected
  id: main
  description: x
  actions:
    - {kind: SendActivity, id: s1, activity: hi}
"""
    assert any(i.rule == "child-agent-actions" for i in validate_text("agents/x/agent.mcs.yml", text).issues)


def test_fix_ids_is_noop_when_clean():
    text = "# Name: X\nkind: AdaptiveDialog\nbeginDialog:\n  kind: OnConversationStart\n  id: main\n"
    assert fix_ids(text) == text


# --- graphs -------------------------------------------------------------------------
def test_topic_graph_branches_and_end():
    tpl = config.KNOWLEDGE_DIR / "templates" / "topics" / "question-topic.topic.mcs.yml"
    g = topic_graph(load_plain(tpl.read_text()))
    kinds = [n.kind for n in g.nodes]
    assert kinds[0] == "OnRecognizedIntent" and "ConditionGroup" in kinds
    assert any(e.variant == "branch" for e in g.edges)
    ids = {n.id for n in g.nodes}
    assert all(e.source in ids and e.target in ids for e in g.edges)


def test_flow_graph_scopes_and_error_edges():
    from app.ai.mock import FLOW_JSON
    g = flow_graph(FLOW_JSON)
    labels = {n.label for n in g.nodes}
    assert {"When an agent calls the flow", "Try", "Send an email", "Respond on failure"} <= labels
    assert any(e.variant == "error" for e in g.edges)


# --- node patching keeps comments ------------------------------------------------------
def test_replace_node_keeps_header_comment():
    from app.ai.mock import RESET_TOPIC
    out = patch.replace_node(RESET_TOPIC, "beginDialog/actions/0",
                             "kind: Question\nid: question_Rp4kQ1\nvariable: init:Topic.HasPhone\n"
                             "prompt: Changed?\nentity: BooleanPrebuiltEntity\n", is_flow=False)
    assert out.startswith("# Name: Reset Password")
    assert "Changed?" in out and validate_text("topics/x.mcs.yml", out).valid


# --- API end to end (mock AI) ------------------------------------------------------------
def test_generate_accept_and_analyze(client, workspace):
    ws = client.get(f"/api/workspaces/{workspace['id']}").json()
    cats = {c["category"] for c in ws["components"]}
    assert {"agent", "topic", "childAgent", "action", "workflow"} <= cats
    amap = client.get(f"/api/workspaces/{workspace['id']}/graph/agent").json()
    assert any(e["label"] == "runs" for e in amap["edges"])          # tool -> flow
    assert any(e["label"] == "redirect" for e in amap["edges"])      # topic -> topic
    findings = client.get(f"/api/workspaces/{workspace['id']}/analyze").json()
    assert not [f for f in findings if f["severity"] == "error"], findings


def test_proposal_marks_new_nodes(client):
    ws = client.post("/api/workspaces", json={"name": "Preview"}).json()
    prop = client.post(f"/api/workspaces/{ws['id']}/ai/generate", json={"mode": "topic", "prompt": "reset"}).json()
    f = prop["files"][0]
    assert f["isNew"] and f["report"]["valid"]
    assert all(n["status"] == "new" for n in f["graph"]["nodes"])


def test_docs_html_and_docx(client, workspace):
    wid = workspace["id"]
    assert client.post(f"/api/workspaces/{wid}/docs/summaries").status_code == 200
    doc = client.get(f"/api/workspaces/{wid}/docs").json()
    assert doc["counts"]["topics"] == 2 and doc["flows"][0]["errorHandling"]
    assert doc["topics"][0]["summary"].startswith("[mock summary]")
    html = client.get(f"/api/workspaces/{wid}/docs/html").text
    assert "Reset Password" in html and "flowchart" in html
    r = client.post(f"/api/workspaces/{wid}/docs/docx", json={"images": {}})
    assert r.status_code == 200 and r.content[:2] == b"PK"


def test_paste_yaml(client):
    ws = client.post("/api/workspaces", json={"name": "Paste"}).json()
    from app.ai.mock import ESCALATE_TOPIC
    r = client.post(f"/api/workspaces/{ws['id']}/paste", json={"content": ESCALATE_TOPIC}).json()
    assert r["path"] == "topics/Escalate.mcs.yml" and r["report"]["valid"]


def test_path_traversal_rejected(client):
    ws = client.post("/api/workspaces", json={"name": "Safe"}).json()
    r = client.put(f"/api/workspaces/{ws['id']}/file", json={"path": "../../evil.yml", "content": "x"})
    assert r.status_code == 400


def test_solution_zip_import(client):
    from app.ai.mock import FLOW_JSON, RESET_TOPIC
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("solution.xml", "<ImportExportXml/>")
        zf.writestr("customizations.xml",
                    '<ImportExportXml><Workflows><Workflow WorkflowId="{6f1c2a9e-0d1b-4c55-9a51-1d2a3b4c5d6e}" '
                    'Name="Create Ticket"><JsonFileName>/Workflows/CreateTicket-6F1C2A9E-0D1B-4C55-9A51-1D2A3B4C5D6E.json'
                    '</JsonFileName></Workflow></Workflows></ImportExportXml>')
        zf.writestr("bots/contoso_helpdesk/bot.xml", "<bot><name>Contoso Helpdesk</name></bot>")
        zf.writestr("botcomponents/contoso_helpdesk.topic.ResetPassword/data", RESET_TOPIC)
        zf.writestr("botcomponents/contoso_helpdesk.topic.ResetPassword/botcomponent.xml",
                    "<botcomponent><name>Reset Password</name></botcomponent>")
        zf.writestr("Workflows/CreateTicket-6F1C2A9E-0D1B-4C55-9A51-1D2A3B4C5D6E.json", json.dumps(FLOW_JSON))
    r = client.post("/api/workspaces/import", files={"file": ("sol.zip", buf.getvalue(), "application/zip")}).json()
    assert r["workspace"]["name"] == "Contoso Helpdesk"
    assert set(r["files"]) == {"topics/ResetPassword.mcs.yml", "workflows/CreateTicket/metadata.yaml",
                               "workflows/CreateTicket/workflow.json"}
