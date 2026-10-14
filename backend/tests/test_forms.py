"""Form edits (set_fields / PATCH /fields) and canvas node metadata."""
from app.workspace.patch import set_fields

TOPIC = """# Name: Reset Password
kind: AdaptiveDialog
beginDialog:
  kind: OnRecognizedIntent
  id: main
  intent:
    triggerQueries:
      - reset my password
  actions:
    # the first message
    - kind: SendActivity
      id: sendActivity_a1
      activity: Hello
"""


def test_set_fields_keeps_comments():
    out = set_fields(TOPIC, "beginDialog/actions/0", {"activity": "Hi there"})
    assert out.startswith("# Name: Reset Password") and "# the first message" in out
    assert "activity: Hi there" in out and "id: sendActivity_a1" in out
    out = set_fields(out, "", {"modelDescription": "Resets passwords", "instructions": "line one\nline two"})
    assert "modelDescription: Resets passwords" in out and "instructions: |" in out
    out = set_fields(out, "beginDialog", {"intent.triggerQueries": ["a", "b"]})
    assert "- a\n" in out and "reset my password" not in out
    out = set_fields(out, "", {"modelDescription": None})
    assert "modelDescription" not in out


def test_patch_fields_endpoint(client, workspace):
    wid = workspace["id"]
    rep = client.patch(f"/api/workspaces/{wid}/fields", json={
        "component": "agent.mcs.yml", "pointer": "",
        "fields": {"instructions": "Be brief.\nAlways greet.", "conversationStarters": [{"title": "Hi", "text": "Hello"}]}}).json()
    assert rep["valid"] is True
    text = client.get(f"/api/workspaces/{wid}/file", params={"path": "agent.mcs.yml"}).json()["content"]
    assert "Always greet." in text and "title: Hi" in text and "componentName: IT Helpdesk" in text
    bad = client.patch(f"/api/workspaces/{wid}/fields", json={"component": "workflows/CreateHelpdeskTicket", "fields": {}})
    assert bad.status_code == 400


def test_graph_meta(client, workspace):
    g = client.get(f"/api/workspaces/{workspace['id']}/graph/component", params={"id": "topics/ResetPassword.mcs.yml"}).json()
    by_kind = {n["kind"]: n for n in g["nodes"]}
    assert by_kind["Question"]["meta"]["entity"] == "Boolean" and by_kind["Question"]["meta"]["variable"] == "Topic.HasPhone"
    assert by_kind["BeginDialog"]["meta"]["targetId"] == "topics/Escalate.mcs.yml"
    assert "reset my password" in by_kind["OnRecognizedIntent"]["meta"]["phrases"]


def test_overview_and_last_run(client, workspace):
    wid = workspace["id"]
    ov = client.get(f"/api/workspaces/{wid}/overview").json()
    assert ov["counts"]["topic"] == 2 and ov["tests"]["cases"] == 0
    ids = {c["id"]: c["state"] for c in ov["checklist"]}
    assert ids["agent"] == "ok" and ids["tests"] == "warn" and ids["connections"] == "warn"
    client.put(f"/api/workspaces/{wid}/test/cases", json=[{"name": "c", "turns": [{"user": "hello", "expect": {}}]}])
    client.post(f"/api/workspaces/{wid}/test/cases/run", json={})
    assert client.get(f"/api/workspaces/{wid}/test/cases/last-run").json()["results"][0]["passed"] is True
    ov = client.get(f"/api/workspaces/{wid}/overview").json()
    assert ov["tests"] == {**ov["tests"], "cases": 1, "ran": 1, "passed": 1}
    assert next(c for c in ov["checklist"] if c["id"] == "tests")["state"] == "ok"
