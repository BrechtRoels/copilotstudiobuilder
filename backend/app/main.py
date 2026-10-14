from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from . import config, knowledge as kn
from .ai import generator, history as ai_history
from .ai.autofix import fix_ids
from .analyzer.rules import analyze
from .docs.builder import build_doc
from .docs.docx_export import render_docx
from .docs.html import render_html
from .docs import summaries
from .genai_client import GenAIError
from .graph.agent_map import agent_map
from .graph.flow_graph import flow_graph
from .graph.topic_graph import topic_graph
from .exporters.new_solution import SolutionIdentity, load_identity, new_base, save_identity
from .exporters.solution_patch import build_solution, inspect_base
from .importers.solution_zip import import_solution
from .sim import cases as sim_cases, flow_runner, knowledge as sim_knowledge, mocks as sim_mocks, runtime as sim
from .sync import mcs_cli
from .validation.validator import validate_text
from .workspace import export, patch, store
from .workspace.model import ChangeSet, FileChange
from .workspace.store import WorkspaceError
from .workspace.yamlio import header_name, load_plain

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="Copilot Studio Builder")
app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(WorkspaceError)
async def _ws_error(_: Request, e: WorkspaceError):
    return JSONResponse(status_code=400, content={"detail": str(e)})


@app.exception_handler(GenAIError)
async def _ai_error(_: Request, e: GenAIError):
    return JSONResponse(status_code=502, content={"detail": str(e)})


def _base_path(info) -> Path:
    return Path(info.path) / ".builder" / "base-solution.zip"


@app.exception_handler(ValueError)
async def _value_error(_: Request, e: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(e)})


# --- meta ---------------------------------------------------------------------
@app.get("/api/health")
def health():
    return {"ok": True, "mock": config.USE_MOCK_AI, "model": config.GENAI_LLM_MODEL,
            "hasKey": bool(config.GENAI_API_KEY)}


@app.get("/api/knowledge/patterns")
def list_patterns():
    return [{"id": p.id, "name": p.name, "description": p.description, "challenge": p.challenge,
             "status": p.status, "tags": p.tags} for p in kn.patterns().values()]


@app.get("/api/knowledge/patterns/{pid}")
def get_pattern(pid: str):
    p = kn.patterns().get(pid)
    if not p:
        raise HTTPException(404)
    return {"id": p.id, "name": p.name, "body": p.body}


@app.get("/api/knowledge/templates")
def list_templates():
    return [{"path": k, "description": kn.template_description(v), "content": v} for k, v in kn.templates().items()]


# --- workspaces -----------------------------------------------------------------
class CreateWs(BaseModel):
    name: str
    description: str = ""


class OpenWs(BaseModel):
    path: str
    name: str | None = None


@app.get("/api/workspaces")
def list_ws():
    return store.list_workspaces()


@app.post("/api/workspaces")
def create_ws(body: CreateWs):
    return store.create_workspace(body.name, body.description)


@app.post("/api/workspaces/open")
def open_ws(body: OpenWs):
    return store.register_folder(body.path, body.name)


@app.post("/api/workspaces/import")
async def import_ws(file: UploadFile = File(...)):
    raw = await file.read()
    cs, name = import_solution(raw)
    info = store.create_workspace(name, f"Imported from {file.filename}", origin="solution")
    store.apply_changeset(info, cs)
    # Keep the original as the base for "Export as importable solution".
    _base_path(info).parent.mkdir(parents=True, exist_ok=True)
    _base_path(info).write_bytes(raw)
    return {"workspace": info, "notes": cs.notes, "files": [f.path for f in cs.files]}


@app.delete("/api/workspaces/{wid}")
def delete_ws(wid: str, deleteFiles: bool = False):
    store.remove_workspace(wid, deleteFiles)
    return {"ok": True}


@app.get("/api/workspaces/{wid}")
def get_ws(wid: str):
    return store.get_workspace(wid)


class FileBody(BaseModel):
    path: str
    content: str


@app.get("/api/workspaces/{wid}/file")
def read_file(wid: str, path: str):
    return {"path": path, "content": store.read_file(store.get_info(wid), path)}


@app.put("/api/workspaces/{wid}/file")
def write_file(wid: str, body: FileBody):
    store.write_file(store.get_info(wid), body.path, body.content)
    return validate_text(body.path, body.content)


@app.delete("/api/workspaces/{wid}/file")
def delete_file(wid: str, path: str):
    store.apply_changeset(store.get_info(wid), ChangeSet(files=[FileChange(path=path, content=None)]))
    return {"ok": True}


@app.post("/api/validate")
def validate(body: FileBody):
    return validate_text(body.path, body.content)


class PasteBody(BaseModel):
    content: str
    overwrite: bool = False


@app.post("/api/workspaces/{wid}/paste")
def paste_yaml(wid: str, body: PasteBody):
    """Add a component from YAML copied out of Copilot Studio's code editor."""
    info = store.get_info(wid)
    data = load_plain(body.content)
    if not isinstance(data, dict) or not data.get("kind"):
        raise ValueError("Pasted YAML has no `kind`. Copy the whole topic from Copilot Studio's code editor.")
    kind = data["kind"]
    folder = {"AdaptiveDialog": "topics", "TaskDialog": "actions", "KnowledgeSourceConfiguration": "knowledge",
              "GlobalVariableComponent": "variables"}.get(kind)
    name = header_name(body.content) or data.get("modelDisplayName") or \
        ((data.get("beginDialog") or {}).get("intent") or {}).get("displayName") or "Pasted"
    stem = re.sub(r"[^A-Za-z0-9]+", "", str(name).title()) or "Pasted"
    if kind == "AgentDialog":
        path = f"agents/{stem}/agent.mcs.yml"
    elif kind == "GptComponentMetadata":
        path = "agent.mcs.yml"
    elif folder:
        path = f"{folder}/{stem}.mcs.yml"
    else:
        raise ValueError(f"Unsupported kind {kind}")
    if store.read_file(info, path) and not body.overwrite:
        raise ValueError(f"{path} already exists. Tick 'overwrite' to replace it.")
    content = fix_ids(body.content if body.content.endswith("\n") else body.content + "\n")
    store.write_file(info, path, content)
    return {"path": path, "report": validate_text(path, content)}


# --- graphs ----------------------------------------------------------------------
@app.get("/api/workspaces/{wid}/graph/agent")
def graph_agent(wid: str):
    return agent_map(store.get_workspace(wid))


@app.get("/api/workspaces/{wid}/graph/component")
def graph_component(wid: str, id: str):
    ws = store.get_workspace(wid)
    comp = ws.get(id)
    if not comp:
        raise HTTPException(404, "Unknown component")
    if comp.category == "workflow":
        return flow_graph((comp.data or {}).get("workflow"))
    return topic_graph(comp.data, ws)


class NodeBody(BaseModel):
    component: str
    pointer: str
    text: str


def _comp_file(wid: str, cid: str):
    ws = store.get_workspace(wid)
    comp = ws.get(cid)
    if not comp:
        raise HTTPException(404, "Unknown component")
    return ws.info, comp


@app.get("/api/workspaces/{wid}/node")
def get_node(wid: str, component: str, pointer: str):
    info, comp = _comp_file(wid, component)
    return {"text": patch.get_node(comp.text, pointer, comp.category == "workflow")}


@app.put("/api/workspaces/{wid}/node")
def put_node(wid: str, body: NodeBody):
    info, comp = _comp_file(wid, body.component)
    new_text = patch.replace_node(comp.text, body.pointer, body.text, comp.category == "workflow")
    store.write_file(info, comp.path, new_text)
    return validate_text(comp.path, new_text)


class FieldsBody(BaseModel):
    component: str
    pointer: str = ""
    fields: dict


@app.patch("/api/workspaces/{wid}/fields")
def patch_fields(wid: str, body: FieldsBody):
    """Form edits: set keys on one node (or the file root) without touching the rest of the YAML."""
    info, comp = _comp_file(wid, body.component)
    if comp.category == "workflow":
        raise HTTPException(400, "Form edits are for YAML components; edit flows in workflow.json")
    new_text = patch.set_fields(comp.text, body.pointer, body.fields)
    store.write_file(info, comp.path, new_text)
    return validate_text(comp.path, new_text)


# --- analysis & AI -------------------------------------------------------------------
@app.get("/api/workspaces/{wid}/analyze")
def analyze_ws(wid: str):
    return analyze(store.get_workspace(wid))


@app.post("/api/workspaces/{wid}/ai/generate")
async def ai_generate(wid: str, req: generator.GenerateRequest):
    return await generator.generate(store.get_workspace(wid), req)


@app.post("/api/workspaces/{wid}/ai/generate/stream")
async def ai_generate_stream(wid: str, req: generator.GenerateRequest):
    """Streams progress (context, plan, files, validation, repair, analyzer) then the proposal, as SSE."""
    ws = store.get_workspace(wid)
    ai_history.add_turn(ws.info.path, {"role": "user", "text": req.prompt, "mode": req.mode, "target": req.target})

    async def stream():
        queue: asyncio.Queue = asyncio.Queue()
        events: list[dict] = []

        def emit(type_: str, **data):
            ev = {"type": type_, "data": data}
            if type_ not in ("proposal", "writing"):
                events.append(ev)
            queue.put_nowait(ev)

        async def run():
            try:
                prop = await generator.generate_stream(ws, req, emit)
                ai_history.add_turn(ws.info.path, {"role": "assistant", "text": prop.summary, "proposalId": prop.id,
                                                   "events": events}, proposal=prop.model_dump())
            except asyncio.CancelledError:
                ai_history.add_turn(ws.info.path, {"role": "assistant", "text": "", "stopped": True, "events": events})
                raise
            except Exception as e:  # noqa: BLE001 — surface to the chat, keep the server alive
                logging.getLogger(__name__).exception("Generation failed")
                emit("error", text=str(e) if isinstance(e, GenAIError) else f"{type(e).__name__}: {e}")
                ai_history.add_turn(ws.info.path, {"role": "assistant", "text": "", "error": str(e), "events": events})
            finally:
                queue.put_nowait(None)

        task = asyncio.create_task(run())
        try:
            while (ev := await queue.get()) is not None:
                yield f"data: {json.dumps(ev, default=str)}\n\n"
            yield 'data: {"type": "done", "data": {}}\n\n'
        finally:
            if not task.done():          # client pressed Stop / disconnected
                task.cancel()

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/workspaces/{wid}/ai/history")
def ai_history_get(wid: str):
    return ai_history.load(store.get_info(wid).path)


@app.delete("/api/workspaces/{wid}/ai/history")
def ai_history_clear(wid: str):
    ai_history.clear(store.get_info(wid).path)
    return {"ok": True}


class AcceptBody(BaseModel):
    paths: list[str] | None = None


@app.post("/api/proposals/{pid}/accept")
def accept_proposal(pid: str, body: AcceptBody):
    prop = generator.get_proposal(pid)
    if not prop:
        raise HTTPException(404, "Proposal expired; generate again")
    return {"written": generator.accept(prop, body.paths)}


# --- docs ------------------------------------------------------------------------------
@app.get("/api/workspaces/{wid}/docs")
def docs_json(wid: str):
    return build_doc(store.get_workspace(wid))


@app.post("/api/workspaces/{wid}/docs/summaries")
async def docs_summaries(wid: str):
    return await summaries.refresh(store.get_workspace(wid))


def _fname(title: str, ext: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-") + f"-documentation.{ext}"


@app.get("/api/workspaces/{wid}/docs/html")
def docs_html(wid: str, download: bool = False):
    doc = build_doc(store.get_workspace(wid))
    headers = {"Content-Disposition": f'attachment; filename="{_fname(doc.title, "html")}"'} if download else {}
    return HTMLResponse(render_html(doc), headers=headers)


class DocxBody(BaseModel):
    images: dict[str, str] = {}


@app.post("/api/workspaces/{wid}/docs/docx")
def docs_docx(wid: str, body: DocxBody):
    doc = build_doc(store.get_workspace(wid))
    return Response(render_docx(doc, body.images),
                    media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": f'attachment; filename="{_fname(doc.title, "docx")}"'})


# --- export & sync --------------------------------------------------------------------
@app.get("/api/workspaces/{wid}/export/zip")
def export_zip(wid: str):
    info = store.get_info(wid)
    return Response(export.zip_workspace(info), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{store.slugify(info.name)}.zip"'})


class MergeBody(BaseModel):
    target: str


@app.post("/api/workspaces/{wid}/export/merge")
def export_merge(wid: str, body: MergeBody):
    return {"written": export.merge_into(store.get_info(wid), body.target)}


# --- importable solution (patches a real exported solution) -------------------
@app.get("/api/workspaces/{wid}/solution/base")
def solution_base(wid: str):
    ws = store.get_workspace(wid)
    p = _base_path(ws.info)
    if not p.exists():
        return {"present": False, "identity": load_identity(ws)}
    return {"present": True, **inspect_base(p.read_bytes()).model_dump()}


@app.put("/api/workspaces/{wid}/solution/identity")
def solution_identity(wid: str, body: SolutionIdentity):
    return save_identity(store.get_workspace(wid), body)


@app.delete("/api/workspaces/{wid}/solution/base")
def remove_solution_base(wid: str):
    _base_path(store.get_info(wid)).unlink(missing_ok=True)
    return {"ok": True}


@app.post("/api/workspaces/{wid}/solution/base")
async def upload_solution_base(wid: str, file: UploadFile = File(...)):
    raw = await file.read()
    base = inspect_base(raw)  # validates: real solution, contains an agent
    p = _base_path(store.get_info(wid))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(raw)
    return {"present": True, **base.model_dump()}


def _base_bytes(ws) -> bytes:
    """Uploaded/imported base (update an existing agent) or a generated one (new agent)."""
    p = _base_path(ws.info)
    return p.read_bytes() if p.exists() else new_base(ws, load_identity(ws))


def _build(wid: str, bot: str | None):
    ws = store.get_workspace(wid)
    base = _base_bytes(ws)
    return ws, base, *build_solution(ws, base, bot)


@app.get("/api/workspaces/{wid}/solution/plan")
def solution_plan(wid: str, bot: str | None = None):
    _, _, _, report = _build(wid, bot)
    return report


@app.get("/api/workspaces/{wid}/solution/export")
def solution_export(wid: str, bot: str | None = None):
    _, base_bytes, data, report = _build(wid, bot)
    base = inspect_base(base_bytes)
    fname = f"{base.uniqueName}_{report.version.replace('.', '_')}.zip"
    return Response(data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@app.get("/api/workspaces/{wid}/sync/status")
def sync_status(wid: str):
    return mcs_cli.status(store.get_info(wid))


@app.post("/api/workspaces/{wid}/sync/{command}")
async def sync_run(wid: str, command: str):
    try:
        return await mcs_cli.run(store.get_info(wid), command)
    except RuntimeError as e:
        raise HTTPException(400, str(e)) from e


# --- test pane (local simulator; nothing is sent to Microsoft) ------------------------------------------
class TestSessionBody(BaseModel):
    user: dict | None = None


class TestMessageBody(BaseModel):
    text: str | None = None
    card: dict | None = None


def _session(sid: str) -> sim.Session:
    s = sim.get(sid)
    if s is None:
        raise HTTPException(404, "Test session expired — restart the conversation")
    return s


@app.post("/api/workspaces/{wid}/test/sessions")
async def test_session(wid: str, body: TestSessionBody):
    s = sim.create(store.get_workspace(wid), body.user)
    await s.start()
    return {"sessionId": s.id, "orchestration": "generative" if s.generative else "classic", "mock": sim.mock_mode(),
            "events": s.events}


@app.post("/api/test/sessions/{sid}/messages")
async def test_message(sid: str, body: TestMessageBody):
    """Runs one turn and streams its SimEvents as server-sent events."""
    s = _session(sid)
    if body.text is None and body.card is None:
        raise HTTPException(400, "Send text or card data")
    lock = s.__dict__.setdefault("_lock", asyncio.Lock())

    async def stream():
        async with lock:
            queue: asyncio.Queue = asyncio.Queue()
            s.listener = queue.put_nowait

            async def turn():
                try:
                    await s.user_message(body.text, body.card)
                except Exception as e:  # noqa: BLE001 — report simulator bugs in the chat, keep the session
                    logging.getLogger(__name__).exception("Simulator turn failed")
                    s.emit("error", text=f"Simulator error: {type(e).__name__}: {e}", code="SimulatorBug")
                    s.emit("done", awaiting=None, ended=False, stack=[])
                finally:
                    queue.put_nowait(None)

            task = asyncio.create_task(turn())
            try:
                while (ev := await queue.get()) is not None:
                    yield f"data: {ev.model_dump_json()}\n\n"
                await task
            finally:
                s.listener = None

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.delete("/api/test/sessions/{sid}")
def test_session_delete(sid: str):
    sim.SESSIONS.pop(sid, None)
    return {"ok": True}


def _workflow(wid: str, cid: str):
    ws = store.get_workspace(wid)
    comp = ws.get(cid)
    if comp is None or comp.category != "workflow":
        raise HTTPException(404, f"No agent flow '{cid}'")
    return ws, comp


@app.get("/api/workspaces/{wid}/test/flows")
def test_flow_info(wid: str, component: str):
    ws, comp = _workflow(wid, component)
    saved = sim_mocks.load(ws.info.path)["actions"]
    prefix = sim_mocks.mock_key(comp.id, "")
    return {"fields": flow_runner.trigger_fields(comp), "runs": flow_runner.history(wid, comp.id),
            "connectorActions": flow_runner.connector_actions(comp),
            "mocks": {k[len(prefix):]: v for k, v in saved.items() if k.startswith(prefix)}}


class FlowRunBody(BaseModel):
    componentId: str
    inputs: dict = {}
    overrides: dict = {}


@app.post("/api/workspaces/{wid}/test/flows/run")
async def test_flow_run(wid: str, body: FlowRunBody):
    ws, comp = _workflow(wid, body.componentId)
    run = await flow_runner.run_flow(ws.info.path, comp, body.inputs, body.overrides)
    flow_runner.remember(wid, run)
    return run


@app.get("/api/workspaces/{wid}/test/runs/{rid}")
def test_flow_run_get(wid: str, rid: str):
    run = next((r for r in flow_runner.history(wid) if r.id == rid), None)
    if run is None:
        raise HTTPException(404, "Run not found (only the last 20 runs are kept)")
    return run


class FlowMocksBody(BaseModel):
    componentId: str
    mocks: dict          # action name -> {mode: mock|fail, outputs?, error?}


@app.put("/api/workspaces/{wid}/test/flows/mocks")
def test_flow_mocks(wid: str, body: FlowMocksBody):
    ws, comp = _workflow(wid, body.componentId)
    data = sim_mocks.load(ws.info.path)
    prefix = sim_mocks.mock_key(comp.id, "")
    data["actions"] = {k: v for k, v in data["actions"].items() if not k.startswith(prefix)}
    data["actions"].update({sim_mocks.mock_key(comp.id, name): m for name, m in body.mocks.items()})
    sim_mocks.save(ws.info.path, data)
    return {"ok": True}


@app.get("/api/workspaces/{wid}/test/knowledge")
def test_knowledge(wid: str):
    ws = store.get_workspace(wid)
    return [{**sim_knowledge.describe(c), "content": sim_knowledge.get_content(ws.info.path, c.name)}
            for c in sim_knowledge.sources(ws)]


class KnowledgeBody(BaseModel):
    text: str


@app.put("/api/workspaces/{wid}/test/knowledge/{name}")
def test_knowledge_put(wid: str, name: str, body: KnowledgeBody):
    ws = store.get_workspace(wid)
    if not any(c.name == name for c in sim_knowledge.sources(ws)):
        raise HTTPException(404, f"No knowledge source '{name}'")
    sim_knowledge.set_content(ws.info.path, name, body.text)
    return {"ok": True, "chars": len(body.text)}


@app.post("/api/workspaces/{wid}/test/knowledge/{name}/upload")
async def test_knowledge_upload(wid: str, name: str, file: UploadFile = File(...)):
    ws = store.get_workspace(wid)
    if not any(c.name == name for c in sim_knowledge.sources(ws)):
        raise HTTPException(404, f"No knowledge source '{name}'")
    text = sim_knowledge.extract_text(file.filename or "", await file.read())
    existing = sim_knowledge.get_content(ws.info.path, name)
    merged = f"{existing}\n\n# {file.filename}\n{text}" if existing.strip() else text
    sim_knowledge.set_content(ws.info.path, name, merged)
    return {"ok": True, "chars": len(merged)}


@app.get("/api/workspaces/{wid}/test/cases")
def test_cases(wid: str):
    return sim_cases.load(store.get_info(wid).path)


@app.put("/api/workspaces/{wid}/test/cases")
def test_cases_put(wid: str, body: list[sim_cases.Case]):
    return sim_cases.save(store.get_info(wid).path, body)


class RunCasesBody(BaseModel):
    ids: list[str] | None = None


@app.post("/api/workspaces/{wid}/test/cases/run")
async def test_cases_run(wid: str, body: RunCasesBody):
    ws = store.get_workspace(wid)
    todo = [c for c in sim_cases.load(ws.info.path) if body.ids is None or c.id in body.ids]
    results = [await sim_cases.run_case(ws, c) for c in todo]
    sim_cases.save_last_run(ws.info.path, results, partial=body.ids is not None)
    return results


@app.get("/api/workspaces/{wid}/test/cases/last-run")
def test_cases_last_run(wid: str):
    return sim_cases.load_last_run(store.get_info(wid).path)


@app.get("/api/workspaces/{wid}/overview")
def overview(wid: str):
    """Dashboard: health, tests, recent AI changes and a ready-to-deploy checklist."""
    from .exporters.new_solution import load_identity
    ws = store.get_workspace(wid)
    findings = analyze(ws)
    errors = [f for f in findings if f.severity == "error"]
    warnings = [f for f in findings if f.severity == "warning"]
    cases = sim_cases.load(ws.info.path)
    last = sim_cases.load_last_run(ws.info.path)
    ids = {c.id for c in cases}
    results = [r for r in (last or {}).get("results", []) if r.get("id") in ids]
    passed = sum(1 for r in results if r.get("passed"))
    counts: dict[str, int] = {}
    for c in ws.components:
        counts[c.category] = counts.get(c.category, 0) + 1
    placeholders = [c.displayName for c in ws.by_category("workflow") if "<connection-reference" in c.text]
    agent = ws.agent
    instructions = str((agent.data or {}).get("instructions") or "") if agent and isinstance(agent.data, dict) else ""
    identity_saved = (Path(ws.info.path) / ".builder" / "solution.json").exists()
    by_rule = lambda rule: [f for f in findings if f.rule == rule]  # noqa: E731
    check = []

    def item(cid, label, state, detail, link):
        check.append({"id": cid, "label": label, "state": state, "detail": detail, "link": link})

    item("agent", "Agent has instructions", "ok" if instructions.strip() else "fail",
         f"{len(instructions)} characters" if instructions else "agent.mcs.yml has no instructions", "build:agent.mcs.yml")
    item("errors", "No analyzer errors", "ok" if not errors else "fail",
         f"{len(errors)} error(s), {len(warnings)} warning(s)", "analyzer")
    if ws.generative_orchestration():
        md = by_rule("model-description")
        item("descriptions", "Topics and tools have routing descriptions", "ok" if not md else "warn",
             f"{len(md)} without a description" if md else "Generative orchestration can route everything", "analyzer")
    fe = by_rule("flow-error-handling") + by_rule("flow-response")
    if ws.by_category("workflow"):
        item("flows", "Flows handle errors and respond to the agent", "ok" if not fe else "warn",
             f"{len(fe)} issue(s)" if fe else f"{len(ws.by_category('workflow'))} flow(s) OK", "analyzer")
        item("connections", "Connection references", "warn" if placeholders else "ok",
             (f"Placeholders in {', '.join(placeholders)} — filled in at export; map them to connections during import"
              if placeholders else "All flows name their connection references"), "deploy")
    item("identity", "Solution identity saved", "ok" if identity_saved else "warn",
         f"{load_identity(ws).uniqueName}" + ("" if identity_saved else " (defaults — save it so re-imports upgrade the same agent)"), "deploy")
    item("tests", "Test cases pass", "warn" if not cases or not results else ("ok" if passed == len(cases) else "fail"),
         "No saved test cases yet" if not cases else ("Not run yet" if not results else f"{passed}/{len(cases)} passed"), "test")
    return {
        "counts": counts, "orchestration": "generative" if ws.generative_orchestration() else "classic",
        "findings": {"errors": len(errors), "warnings": len(warnings), "infos": len(findings) - len(errors) - len(warnings),
                     "top": [f.model_dump() for f in (errors + warnings)[:6]]},
        "tests": {"cases": len(cases), "ran": len(results), "passed": passed, "ts": (last or {}).get("ts")},
        "recent": ai_history.recent_changes(ws.info.path),
        "checklist": check,
        "ready": all(c["state"] != "fail" for c in check),
    }
