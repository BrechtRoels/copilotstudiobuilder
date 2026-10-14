"""Test pane simulator: Power Fx, flow expressions, the flow runner and conversations (mock AI)."""
import json

import pytest

from app.sim.powerfx import Env, FxError, evaluate, interpolate
from app.sim.wdl import Ctx, resolve

ENV = Env({"Topic": {"HasPhone": True, "n": 3, "Name": "Ann", "Items": [{"Value": 1}, {"Value": 5}]},
           "Global": {}, "System": {"FallbackCount": 2}})


@pytest.mark.parametrize("expr,expected", [
    ("Topic.HasPhone = true", True),
    ("!IsBlank(Topic.Name)", True),
    ("System.FallbackCount < 3", True),
    ("Text(Topic.n + 2)", "5"),
    ("1 + 2 * 3 ^ 2", 19),
    ('If(Topic.n > 2, "big", "small")', "big"),
    ("CountRows(Filter(Topic.Items, Value > 2))", 1),
    ("Sum(Topic.Items, Value)", 6),
    ('"a" & 1 & true', "a1true"),
    ("Topic.Missing", None),
    ("Not(false) && Or(false, true)", True),
    ('First(Split("a,b", ",")).Value', "a"),
    ('Text(1234.5, "#,##0.00")', "1,234.50"),
    ('Switch(Topic.n, 1, "one", 3, "three", "other")', "three"),
    ('$"Hi {Topic.Name}!"', "Hi Ann!"),
    ('{ DisplayName: "x", Score: 1.0 }.DisplayName', "x"),
    ('Lower("ABC") in "xxabcxx"', True),
])
def test_powerfx(expr, expected):
    assert evaluate(expr, ENV) == expected


def test_powerfx_unsupported_function_is_an_error():
    with pytest.raises(FxError):
        evaluate("Launch(\"x\")", ENV)


def test_message_interpolation():
    assert interpolate("Hello {Topic.Name}, {Topic.n * 2} items", ENV) == "Hello Ann, 6 items"


def test_wdl_expressions():
    ctx = Ctx(trigger={"outputs": {"body": {"text": "VPN down"}}}, actions={"Id": {"status": "Succeeded", "outputs": "abc"}},
              variables={"n": 2})
    assert resolve("[@{outputs('Id')}] @{triggerBody()?['text']}", ctx) == "[abc] VPN down"
    assert resolve("@if(greater(variables('n'), 1), 'big', 'small')", ctx) == "big"
    assert resolve({"a": "@triggerBody()?['missing']", "b": "@@literal"}, ctx) == {"a": None, "b": "@literal"}


# --- flows ---------------------------------------------------------------------------------------------
FLOW = "workflows/CreateHelpdeskTicket"


def test_flow_success_path(client, workspace):
    wid = workspace["id"]
    info = client.get(f"/api/workspaces/{wid}/test/flows", params={"component": FLOW}).json()
    assert [f["name"] for f in info["fields"]] == ["Title", "Description"]
    run = client.post(f"/api/workspaces/{wid}/test/flows/run",
                      json={"componentId": FLOW, "inputs": {"Title": "VPN", "Description": "down"}}).json()
    st = {a["name"]: a["status"] for a in run["actions"]}
    assert run["status"] == "Succeeded"
    assert st["Respond_to_the_agent"] == "Succeeded" and st["Respond_on_failure"] == "Skipped"
    assert run["outputs"]["TicketId"]
    email = next(a for a in run["actions"] if a["name"] == "Send_an_email")
    assert email["source"] == "schema"                     # sample from Microsoft's Office 365 reference
    assert email["inputs"]["parameters"]["emailMessage/Subject"].endswith("] VPN")
    assert email["pointer"] == "actions/Try/actions/Send_an_email"


def test_flow_forced_failure_runs_catch(client, workspace):
    wid = workspace["id"]
    run = client.post(f"/api/workspaces/{wid}/test/flows/run", json={
        "componentId": FLOW, "inputs": {"Title": "VPN", "Description": "down"},
        "overrides": {"Send_an_email": {"mode": "fail", "error": "Mailbox full"}}}).json()
    st = {a["name"]: a["status"] for a in run["actions"]}
    assert st["Try"] == "Failed" and st["Send_an_email"] == "Failed"
    assert st["Respond_to_the_agent"] == "Skipped" and st["Respond_on_failure"] == "Succeeded"
    assert run["status"] == "Succeeded"                    # failure handled by the catch branch
    assert run["outputs"]["TicketId"] == "ERROR"
    hist = client.get(f"/api/workspaces/{wid}/test/flows", params={"component": FLOW}).json()["runs"]
    assert hist[0]["id"] == run["id"]


def test_flow_missing_required_input(client, workspace):
    run = client.post(f"/api/workspaces/{workspace['id']}/test/flows/run",
                      json={"componentId": FLOW, "inputs": {"Title": "VPN"}}).json()
    assert run["status"] == "Failed" and "Description" in run["error"]["message"]


def test_saved_mock_is_used(client, workspace):
    wid = workspace["id"]
    client.put(f"/api/workspaces/{wid}/test/flows/mocks",
               json={"componentId": FLOW, "mocks": {"Send_an_email": {"mode": "fail"}}})
    run = client.post(f"/api/workspaces/{wid}/test/flows/run",
                      json={"componentId": FLOW, "inputs": {"Title": "a", "Description": "b"}}).json()
    email = next(a for a in run["actions"] if a["name"] == "Send_an_email")
    assert email["status"] == "Failed" and email["source"] == "saved"


# --- conversations -------------------------------------------------------------------------------------
def _events(resp) -> list[dict]:
    assert resp.headers["content-type"].startswith("text/event-stream")
    return [json.loads(line[6:]) for line in resp.text.splitlines() if line.startswith("data: ")]


def _say(client, sid, text):
    return _events(client.post(f"/api/test/sessions/{sid}/messages", json={"text": text}))


def _bot(events):
    return [e["data"]["text"] for e in events if e["type"] == "message"]


def _classic(client, wid):
    client.put(f"/api/workspaces/{wid}/file", json={"path": "settings.mcs.yml", "content": "GenerativeActionsEnabled: false\n"})


def test_classic_conversation_with_redirect(client, workspace):
    wid = workspace["id"]
    _classic(client, wid)
    start = client.post(f"/api/workspaces/{wid}/test/sessions", json={}).json()
    assert start["orchestration"] == "classic"
    assert any(e["type"] == "message" for e in start["events"])          # default greeting
    sid = start["sessionId"]

    ev = _say(client, sid, "I forgot my password")
    assert any(e["type"] == "topic" and e["data"]["name"] == "Reset Password" for e in ev)
    assert _bot(ev) == ["Do you have access to the phone registered for MFA?"]
    assert ev[-1]["type"] == "done" and ev[-1]["data"]["awaiting"] == "question"

    ev = _say(client, sid, "no")
    steps = [(e["componentId"], e["pointer"]) for e in ev if e["type"] == "step"]
    assert ("topics/ResetPassword.mcs.yml", "beginDialog/actions/1/elseActions/1") in steps
    assert ("topics/Escalate.mcs.yml", "beginDialog/actions/1") in steps
    assert any("service desk" in t for t in _bot(ev))
    variables = next(e for e in ev if e["type"] == "variables")["data"]
    assert variables["topicName"] == "Reset Password" and variables["Topic"]["HasPhone"] is False


def test_question_reprompt_and_fallback(client, workspace):
    wid = workspace["id"]
    _classic(client, wid)
    sid = client.post(f"/api/workspaces/{wid}/test/sessions", json={}).json()["sessionId"]
    _say(client, sid, "reset my password")
    ev = _say(client, sid, "purple")                                     # not a yes/no
    assert _bot(ev)[0].startswith("I'm sorry, I didn't understand")
    assert ev[-1]["data"]["awaiting"] == "question"
    _say(client, sid, "yes")
    ev = _say(client, sid, "what is the weather on mars")
    assert any(e["type"] == "routing" for e in ev)
    assert "not sure how to help" in _bot(ev)[0]


def test_generative_tool_runs_flow(client, workspace):
    wid = workspace["id"]
    sid = client.post(f"/api/workspaces/{wid}/test/sessions", json={}).json()["sessionId"]
    ev = _say(client, sid, "please create a helpdesk ticket")
    plan = next(e for e in ev if e["type"] == "plan")
    assert plan["data"]["action"] == "tool" and plan["data"]["id"] == "actions/CreateHelpdeskTicket.mcs.yml"
    flow = next(e for e in ev if e["type"] == "flow")
    assert flow["data"]["status"] == "Succeeded" and flow["data"]["outputs"]["TicketId"]
    run = client.get(f"/api/workspaces/{wid}/test/runs/{flow['data']['runId']}").json()
    assert run["componentId"] == FLOW


def test_generative_child_agent(client, workspace):
    sid = client.post(f"/api/workspaces/{workspace['id']}/test/sessions", json={}).json()["sessionId"]
    ev = _say(client, sid, "how many vacation days do I have left")
    assert any(e["type"] == "agent" and e["data"]["name"] == "HRSpecialist" for e in ev)
    assert any(e["data"].get("agent") == "HRSpecialist" for e in ev if e["type"] == "message")


def test_knowledge_answers_from_test_content(client, workspace):
    wid = workspace["id"]
    client.put(f"/api/workspaces/{wid}/file", json={"path": "knowledge/Policies.mcs.yml", "content":
               "kind: KnowledgeSourceConfiguration\nsource:\n  kind: PublicSiteSearchSource\n  site: https://contoso.com\n"})
    src = client.get(f"/api/workspaces/{wid}/test/knowledge").json()
    assert src[0]["name"] == "Policies" and src[0]["content"] == ""
    client.put(f"/api/workspaces/{wid}/test/knowledge/Policies",
               json={"text": "Laptops are replaced every three years by the IT asset team."})
    sid = client.post(f"/api/workspaces/{wid}/test/sessions", json={}).json()["sessionId"]
    ev = _say(client, sid, "when are laptops replaced")
    k = next(e for e in ev if e["type"] == "knowledge")
    assert k["data"]["answer"] and k["data"]["citations"][0]["source"] == "Policies"
    assert any("three years" in t for t in _bot(ev))


def test_power_fx_error_runs_error_handler(client, workspace):
    wid = workspace["id"]
    _classic(client, wid)
    client.put(f"/api/workspaces/{wid}/file", json={"path": "topics/Broken.mcs.yml", "content": """kind: AdaptiveDialog
beginDialog:
  kind: OnRecognizedIntent
  id: main
  intent:
    triggerQueries:
      - break things
  actions:
    - kind: SetVariable
      id: setVariable_x1
      variable: init:Topic.X
      value: =1 / 0
"""})
    sid = client.post(f"/api/workspaces/{wid}/test/sessions", json={}).json()["sessionId"]
    ev = _say(client, sid, "break things")
    err = next(e for e in ev if e["type"] == "error")
    assert err["pointer"] == "beginDialog/actions/0" and "Division by zero" in err["data"]["text"]
    assert any(t.startswith("An error has occurred") for t in _bot(ev))


def test_unknown_session_is_404(client):
    assert client.post("/api/test/sessions/nope/messages", json={"text": "hi"}).status_code == 404


def test_saved_cases_replay(client, workspace):
    wid = workspace["id"]
    _classic(client, wid)
    cases = [{"name": "Password reset without phone", "turns": [
        {"user": "I forgot my password", "expect": {"topic": "Reset Password", "contains": "phone registered"}},
        {"user": "no", "expect": {"topic": "Escalate", "contains": "4 working hours"}}]},
        {"name": "Wrong expectation", "turns": [{"user": "I forgot my password", "expect": {"topic": "Escalate"}}]}]
    saved = client.put(f"/api/workspaces/{wid}/test/cases", json=cases).json()
    assert len(saved) == 2 and saved[0]["id"]
    res = client.post(f"/api/workspaces/{wid}/test/cases/run", json={}).json()
    assert [r["passed"] for r in res] == [True, False]
    bad = res[1]["turns"][0]["checks"][0]
    assert bad["kind"] == "topic" and bad["actual"] == "Reset Password"
