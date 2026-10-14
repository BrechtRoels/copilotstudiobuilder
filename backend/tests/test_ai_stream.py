"""Streamed AI Builder: progress events, auto mode, persisted history (mock AI)."""
import json

from app.ai import generator
from app.ai.context import infer_mode
from app.workspace.model import Workspace, WorkspaceInfo


def _events(resp):
    assert resp.headers["content-type"].startswith("text/event-stream")
    return [json.loads(line[6:]) for line in resp.text.splitlines() if line.startswith("data: ")]


def test_stream_event_order(client):
    ws = client.post("/api/workspaces", json={"name": "Stream test"}).json()
    ev = _events(client.post(f"/api/workspaces/{ws['id']}/ai/generate/stream",
                             json={"mode": "auto", "prompt": "IT helpdesk orchestrator with an HR child agent"}))
    types = [e["type"] for e in ev]
    order = [types.index(t) for t in ("context", "plan", "file", "parsed", "validate", "analyze", "proposal")]
    assert order == sorted(order) and types[-1] == "done"
    ctx = ev[0]["data"]
    assert ctx["mode"] == "agent" and ctx["inferred"] is True and "int-reference" in ctx["guides"]
    files = [e["data"]["path"] for e in ev if e["type"] == "file"]
    assert "topics/ResetPassword.mcs.yml" in files
    parsed = next(e for e in ev if e["type"] == "parsed")["data"]["files"]
    assert any(f["label"] == "Topic · Reset Password" and f["isNew"] and f["added"] > 5 for f in parsed)
    prop = next(e for e in ev if e["type"] == "proposal")["data"]["proposal"]
    assert prop["mode"] == "agent" and len(prop["files"]) == 7

    hist = client.get(f"/api/workspaces/{ws['id']}/ai/history").json()
    assert [t["role"] for t in hist["turns"]] == ["user", "assistant"]
    assert hist["turns"][1]["proposalId"] == prop["id"] and prop["id"] in hist["proposals"]
    assert any(e["type"] == "plan" for e in hist["turns"][1]["events"])

    # Apply still works after the in-memory cache is gone (backend restart).
    generator._PROPOSALS.clear()
    written = client.post(f"/api/proposals/{prop['id']}/accept", json={}).json()["written"]
    assert "agent.mcs.yml" in written
    assert client.get(f"/api/workspaces/{ws['id']}/ai/history").json()["proposals"][prop["id"]]["applied"] == written
    client.delete(f"/api/workspaces/{ws['id']}/ai/history")
    assert client.get(f"/api/workspaces/{ws['id']}/ai/history").json()["turns"] == []


def test_infer_mode():
    ws = Workspace(info=WorkspaceInfo(id="x", name="x", path="/tmp", createdAt=""))
    assert infer_mode("IT helpdesk orchestrator with an HR child agent", None, ws) == "agent"
    assert infer_mode("Create a flow that emails the manager", None, ws) == "flow"
    assert infer_mode("How do I pass variables between topics?", None, ws) == "ask"
    assert infer_mode("Finance specialist child agent", None, ws) == "childAgent"
    assert infer_mode("Order status lookup topic", None, ws) == "topic"
    assert infer_mode("IT helpdesk orchestrator with an HR child agent, a reset topic and a ticket flow", None, ws) == "agent"
    assert infer_mode("Help employees book meeting rooms", None, ws) == "agent"      # empty workspace


def test_auto_mode_with_selection_modifies(client, workspace):
    ev = _events(client.post(f"/api/workspaces/{workspace['id']}/ai/generate/stream",
                             json={"mode": "auto", "prompt": "add a confirmation message",
                                   "target": "topics/ResetPassword.mcs.yml"}))
    assert ev[0]["data"]["mode"] == "modify"
