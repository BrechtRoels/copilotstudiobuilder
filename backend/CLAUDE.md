# Backend (FastAPI · Python 3.12 · uv)

`uv run uvicorn app.main:app --reload --port 8010` · `uv run pytest -q`

## Module map (`app/`)

| Path | Responsibility |
|---|---|
| `main.py` | All HTTP routes (`/api/...`) and error handlers. Thin: it delegates to the modules below. |
| `config.py` | Env vars (`GENAI_*` incl. `GENAI_MAX_OUTPUT_TOKENS`, `USE_MOCK_AI`, `WORKSPACES_DIR`, `MCS_SKILLS_DIR`, `AI_REPAIR_ATTEMPTS`). Read at import time. |
| `genai_client.py` | PwC GenAI `/v1/responses`: `llm_complete`, `llm_stream` (SSE deltas; retries only before the first delta) and `complete_json` (one corrective retry). |
| `workspace/model.py` | `Component`, `Workspace`, `WorkspaceInfo`, `ChangeSet`, `FileChange`. |
| `workspace/loader.py` | Agent folder → `Workspace`. Assigns a category by path, falling back to `kind`. A workflow folder becomes one component. |
| `workspace/store.py` | Registry and safe IO (`resolve_path`, `apply_changeset`, `register_folder`). |
| `workspace/patch.py` | Read or replace one node by pointer (canvas side panel); `set_fields` for form edits (root pointer `""`, dotted keys, `None` deletes). Flow pointers are relative to the definition. |
| `workspace/export.py` | Workspace zip (not importable); merge into a VS Code clone. |
| `workspace/yamlio.py` | `load_plain` (analysis); `load_rt`/`dump_rt` (comment-preserving edits); `header_name`. |
| `knowledge/__init__.py` | Access to the vendored pack: `guide()`, `patterns()`, `templates()`, `node_kinds()`, `resolve_kind()` (Python stand-in for Microsoft's schema-lookup). |
| `validation/validator.py` | `validate_text(path, text)` → `FileReport`: schema by `kind`, plus lint (unique ids, placeholders, Power Fx whitelist parsed from int-reference, AgentDialog rules, flow `runAfter`). |
| `analyzer/rules.py` | `analyze(ws)` → `Finding[]`: per-file validation plus cross-file rules (redirects, variables, triggers, flows, capabilities the instructions need but the config doesn't enable, unused globals, stale topic/tool names). |
| `graph/` | `topic_graph`, `flow_graph`, `agent_map` → `Graph{nodes, edges}` (shared by canvas, proposals and docs). `refs.py` resolves redirects and flow ids. |
| `ai/` | `context.py` (prompt assembly, `build_system_ex` returns the sources, `infer_mode`), `generator.py` (`generate_stream` with an emit callback: context → plan → files → validate → repair → analyzer → coverage → `Proposal`; `file_label`, `check_coverage`), `history.py` (chat and proposals per workspace), `autofix.py`, `mock.py` (the streamed mock adds `PLAN:` lines). |
| `docs/` | `builder.build_doc(ws)` → `Doc` (facts from the parser); renderers `html.py` (Jinja and Mermaid via CDN) and `docx_export.py` (PNG diagrams come from the browser); `summaries.py` (AI prose cached by content hash); `mermaid.py`. |
| `importers/solution_zip.py` | Solution .zip → workspace files. Routes components by YAML `kind`. |
| `exporters/` | `new_solution.py` (generated classic-agent base and `SolutionIdentity`) and `solution_patch.py` (`build_solution(ws, base)` → importable zip and `ExportReport`). |
| `sim/` | Test pane simulator (no Microsoft calls): `runtime.py` (Session, dialog stack, all node kinds, classic and generative routing, system topics, SSE events, session registry), `orchestrator.py` (recognizer and planner; PwC GenAI, or word overlap in mock mode), `powerfx.py`, `wdl.py` (flow expressions), `flow_runner.py` (`run_flow` → `FlowRun`, `runAfter` semantics, history), `mocks.py` (connector results; samples from `knowledge/mcs/reference/connectors/*.yml`), `knowledge.py` (test content retrieval and answers), `cases.py` (saved test conversations). |
| `sync/mcs_cli.py` | Optional wrapper around Microsoft's `manage-agent.bundle.js` (validate/changes/pull/push). Needs `MCS_SKILLS_DIR`, Node and the VS Code extension. |

## Tests

- `tests/conftest.py` sets `WORKSPACES_DIR` to a temp dir and `USE_MOCK_AI=true` *before* importing the app.
- The `workspace` fixture is the mock IT Helpdesk agent, created through the real generate → accept API.
- `test_core.py` covers the validator (on Microsoft's templates), graphs, patching, the API, docs, paste, path traversal and import.
- `test_simulator.py` covers:
  - Power Fx and WDL tables;
  - flow success, forced failure (catch path) and a missing required input;
  - saved mocks;
  - classic conversation, redirect, re-prompt, fallback and OnError;
  - generative tool, flow and child agent;
  - knowledge;
  - saved test cases.
  
  The SSE helper is `_events`.
- `test_ai_stream.py` covers the stream event order, auto mode, history, and Apply after a restart.
- `test_forms.py` covers `set_fields` keeping comments, `PATCH /fields`, graph node `meta`, and the overview with the last test run.
- `test_ai_quality.py` covers follow-up context and schemas in the prompt, the coverage event, whole-folder flow deletes, the capability / stale-name / unused-global rules, and AI settings in the export.
- `test_solution_export.py` covers patching a synthetic real-shaped base, the new-agent package, and identity.
- When you change generation or export, add an assertion on the produced files. Don't only check status codes.
