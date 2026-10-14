"""Output quality: follow-ups see the files, schemas up front, capabilities, coverage, analyzer rules (mock AI)."""
import io
import json
import zipfile
from pathlib import Path

from app.ai import generator
from app.ai.context import build_system_ex, infer_mode
from app.analyzer.rules import analyze
from app.exporters.new_solution import load_identity, new_base
from app.exporters.solution_patch import build_solution
from app.validation.validator import validate_text
from app.workspace import store
from app.workspace.loader import load_workspace
from app.workspace.model import ChangeSet, FileChange, WorkspaceInfo


def _events(resp):
    return [json.loads(line[6:]) for line in resp.text.splitlines() if line.startswith("data: ")]


def test_followup_prompt_has_existing_files_and_schemas(workspace):
    ws = store.get_workspace(workspace["id"])
    assert infer_mode("No, it should not email the service desk", None, ws) == "modify"
    assert infer_mode("Add a new topic for feedback", None, ws) == "topic"
    system, _, src = build_system_ex("modify", "make it accept uploaded files", ws)
    assert "aka.ms/sspr" in system                                    # full topic text, not just the path
    assert "### workflows/CreateHelpdeskTicket/metadata.yaml" in system  # both files of a flow are listed
    assert "agent-capabilities" in src["guides"]                      # no target: agent-level guides
    assert "AnswerQuestionWithAI" in src["schemas"] and "GptComponentMetadata" in src["schemas"]
    assert "AISettingsNoKind {useModelKnowledge, isFileAnalysisEnabled" in system
    # the target file is shown once (under Target), not twice
    system, _, _ = build_system_ex("modify", "add a message", ws, target="topics/ResetPassword.mcs.yml")
    assert system.count("aka.ms/sspr") == 0 and "(shown in full under Target)" in system


def test_coverage_event_and_proposal(client):
    ws = client.post("/api/workspaces", json={"name": "Coverage"}).json()
    ev = _events(client.post(f"/api/workspaces/{ws['id']}/ai/generate/stream",
                             json={"mode": "agent", "prompt": "IT helpdesk agent. It must create tickets for outages."}))
    types = [e["type"] for e in ev]
    assert types.index("analyze") < types.index("coverage") < types.index("proposal")
    prop = next(e for e in ev if e["type"] == "proposal")["data"]["proposal"]
    assert len(prop["coverage"]) == 2 and {r["status"] for r in prop["coverage"]} == {"covered"}


def test_flow_delete_removes_whole_folder(workspace):
    ws = store.get_workspace(workspace["id"])
    cs = generator._expand_deletes(ChangeSet(files=[FileChange(path="workflows/CreateHelpdeskTicket/workflow.json",
                                                               content=None)]), ws)
    assert {f.path for f in cs.files} == {"workflows/CreateHelpdeskTicket/workflow.json",
                                          "workflows/CreateHelpdeskTicket/metadata.yaml"}
    store.apply_changeset(ws.info, cs)
    assert not (Path(ws.info.path) / "workflows" / "CreateHelpdeskTicket").exists()
    assert generator._parse_changeset({"files": [{"path": "topics/X.mcs.yml", "content": "  "}]}).files[0].content is None


AGENT = """\
kind: GptComponentMetadata
instructions: |
  Read the documents the user uploaded and research the internet for regulations.
  Never use general knowledge. Use the "Map Regulations" topic, then the SIPOC Step Review topic.
"""
TOPIC = """\
# Name: Map Regulations
kind: AdaptiveDialog
beginDialog:
  kind: OnRecognizedIntent
  id: main
  intent:
    triggerQueries:
      - map regulations
  actions:
    - kind: AnswerQuestionWithAI
      id: answerQuestionWithAI_a1b2c3
      userInput: =System.Activity.Text
"""
VARIABLE = """\
# Name: Step
kind: GlobalVariableComponent
schemaName: <agentSchemaName>.globalvariable.Step
variable:
  name: Step
  scope: Conversation
  aIVisibility: Hidden
"""


def _ws(tmp_path, files: dict[str, str]):
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    return load_workspace(WorkspaceInfo(id="t", name="t", path=str(tmp_path), createdAt=""))


def test_analyzer_capability_stale_and_global_rules(tmp_path):
    ws = _ws(tmp_path, {"agent.mcs.yml": AGENT, "topics/MapRegulations.mcs.yml": TOPIC,
                        "variables/Step.mcs.yml": VARIABLE})
    (tmp_path / "workflows" / "Old").mkdir(parents=True)   # leftover of a deleted flow
    ws = load_workspace(ws.info)
    rules = {(f.rule, f.component) for f in analyze(ws)}
    assert ("capability-file-analysis", "agent.mcs.yml") in rules
    assert ("capability-file-analysis", "topics/MapRegulations.mcs.yml") in rules
    assert ("capability-web", "agent.mcs.yml") in rules
    assert ("capability-model-knowledge", "agent.mcs.yml") in rules
    assert ("unused-global", "variables/Step.mcs.yml") in rules
    stale = [f.message for f in analyze(ws) if f.rule == "stale-reference"]
    assert stale == ["Mentions the topic 'SIPOC Step Review', which isn't in this workspace"]  # "Map Regulations" exists
    assert not any(f.component.startswith("workflows/") for f in analyze(ws))  # a leftover folder is not a flow
    assert any(i.rule == "placeholder" and "<agentSchemaName>" in i.message
               for i in validate_text("variables/Step.mcs.yml", VARIABLE).issues)

    # Configured in YAML, the capability findings go away.
    (tmp_path / "settings.mcs.yml").write_text("GenerativeActionsEnabled: true\nconfiguration:\n  aISettings:\n"
                                               "    isFileAnalysisEnabled: true\n    useModelKnowledge: false\n")
    (tmp_path / "agent.mcs.yml").write_text(AGENT + "gptCapabilities:\n  webBrowsing: true\n")
    (tmp_path / "topics/MapRegulations.mcs.yml").write_text(TOPIC + "      isFileAnalysisEnabled: true\n")
    ws = load_workspace(ws.info)
    assert not [f for f in analyze(ws) if f.rule.startswith("capability-")]


def test_export_uses_ai_settings(workspace):
    ws = store.get_workspace(workspace["id"])
    store.write_file(ws.info, "settings.mcs.yml", "GenerativeActionsEnabled: true\nconfiguration:\n  aISettings:\n"
                                                   "    useModelKnowledge: false\n")
    ws = store.get_workspace(workspace["id"])
    data, report = build_solution(ws, new_base(ws, load_identity(ws)))
    cfg = json.loads(zipfile.ZipFile(io.BytesIO(data)).read("bots/csb_ITHelpdesk/configuration.json"))
    assert cfg["aISettings"]["useModelKnowledge"] is False and cfg["aISettings"]["isFileAnalysisEnabled"] is True
    assert any(i.source == "settings.mcs.yml" for i in report.items)
