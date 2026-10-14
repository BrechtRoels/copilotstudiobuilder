# Copilot Studio Builder

A local web app to generate, visualise, validate, document and deploy Microsoft Copilot Studio agents (topics, agent flows, tools, knowledge, child agents) with PwC GenAI. It has a FastAPI backend in `backend/` and a React frontend in `frontend/`. The user-facing overview is in `README.md`.

## Run / test

```bash
./start.sh                                   # backend :8010 (BACKEND_PORT), frontend :5173 → http://localhost:5173
cd backend && uv run pytest -q               # 79 tests, mock AI, isolated temp WORKSPACES_DIR
cd frontend && npx tsc -b && npx vite build  # typecheck + build
```

- **Port 8000 is taken** on this machine by another project and Docker. Never use it. Vite proxies `/api` to `BACKEND_PORT` (default 8010).
- `.env` holds `GENAI_API_KEY` (PwC GenAI). `USE_MOCK_AI=true` returns canned, schema-valid output (`backend/app/ai/mock.py`) and spends no tokens.
- The user often has `./start.sh` running, with real data in `workspaces/`. Before a browser or e2e test, check `curl localhost:8010/api/health`: `"mock": false` means it's their live app. For tests, start your own servers on other ports with a temp `WORKSPACES_DIR` and `USE_MOCK_AI=true`, as tracked background tasks.

## Core concepts

- **Workspace = an agent folder in the Copilot Studio VS Code extension format.** This is the app's single source of truth:
  - `agent.mcs.yml`, `settings.mcs.yml`, `connectionreferences.mcs.yml`
  - `topics/`, `actions/`, `knowledge/`, `variables/`, `trigger/`
  - `agents/<Child>/agent.mcs.yml`
  - `workflows/<Flow>/{metadata.yaml, workflow.json}`
- **Registry and storage.** `workspaces/registry.json` lists the workspaces. Folders cloned with VS Code are edited in place. Per-workspace app state lives in `<ws>/.builder/`: `summaries.json`, `base-solution.zip`, `solution.json` (solution identity).
- **Knowledge pack.** `backend/app/knowledge/mcs/` is vendored, unmodified, from `microsoft/skills-for-copilot-studio` (MIT, pinned in `SOURCE_COMMIT`). It contains the YAML authoring JSON schema, templates, patterns and skill guides. **Never edit it; re-vendor to update.** App-written guides go in `knowledge/builder/` (e.g. `agent-flows.md`, `agent-capabilities.md`: file upload, web search, model knowledge → the YAML keys).
- **AI pipeline** (`ai/generator.py`). `generate_stream(ws, req, emit)` is the real implementation; `generate()` wraps it.
  - `POST /ai/generate/stream` streams events over SSE in this order:
    1. `context` (the sources, and the mode, which `context.infer_mode` picks for `mode: "auto"`);
    2. `plan` (the model writes `PLAN:` lines before its JSON);
    3. `file` (per file, as it's written);
    4. `parsed`;
    5. `validate`;
    6. `repair` / `repaired`;
    7. `analyze`;
    8. `coverage` (each requirement → covered / partial / missing, `AI_COVERAGE_CHECK`);
    9. `proposal`.
  - The chat and its proposals are saved in `<ws>/.builder/ai-history.json` (`ai/history.py`), so Apply works after a restart.

  The steps:
  1. `context.build_system` builds the prompt from the guides, templates and patterns that match the mode and keywords, the schema of the kinds likely used (`schema_kinds`), and the **full text of the existing files** (`workspace_summary`, budget `AI_CONTEXT_CHARS`), so follow-ups edit instead of rewriting blind. A change request on an existing agent infers `modify`; with no target it gets the agent-level guides.
  2. The model returns JSON `{summary, notes, files:[{path, content}]}`.
  3. `autofix.fix_ids` repairs IDs. Deleting any file of a flow deletes its whole folder (`_expand_deletes`).
  4. `validator.validate_text` checks each file; failures trigger up to `AI_REPAIR_ATTEMPTS` LLM repair rounds.
  5. The result is a `Proposal` (diff, graph with new/changed node status, analyzer findings on a merged temp copy). Nothing is written until `/api/proposals/{id}/accept`.
- **Validation layers:**
  - `validation/validator.py`: per-file schema (by `kind`) plus lint.
  - `analyzer/rules.py`: cross-file checks (redirects, variables, trigger overlap, flow error handling).
- **Importable solution (Power Apps → Solutions → Import)**, in `exporters/`. The workspace zip is *not* importable.
  - `new_solution.py` generates a classic-agent base: `bot.xml` template `default-2.1.0`, `configuration.json`, `gpt.default`, system topics.
  - `solution_patch.py` writes the workspace into a base, either that generated one or the user's uploaded export:
    - existing components are matched by schema-name tail;
    - redirects are rewritten to the solution's schema names;
    - every extensionless `data` part gets an `Override` in `[Content_Types].xml`;
    - flow connection references are declared in `customizations.xml`;
    - the version is bumped;
    - all other parts stay byte-identical.
  - The structure was copied from real exports (CopilotStudioSamples AccountLookupAgent, Copilot Studio gallery). It has **not yet been confirmed by a real import**. Treat user-reported import errors as ground truth.
  - On macOS, Safari auto-unzips downloads, and Finder "Compress" then makes a folder zip that Power Apps rejects.

- **Test pane = local simulator** (`backend/app/sim/`). The user requires it to run inside the app: **never call Microsoft endpoints** (Copilot Studio, Direct Line, Dataverse, Flow API, connectors). The user explicitly approved PwC GenAI for routing, the planner, knowledge answers and AI mocks; everything works offline with `USE_MOCK_AI`.
  - `runtime.Session` keeps a dialog stack of `Frame`s, each with `Block`s. It emits `SimEvent`s, which the API streams over SSE: `POST /api/test/sessions/{sid}/messages`.
  - `step` events carry the same pointers as `topic_graph` / `flow_graph`. The frontend turns them into canvas `marks`.
  - Supporting modules:
    - `powerfx.py` and `wdl.py` are expression evaluators. They raise on unsupported functions and never guess.
    - `flow_runner.py` implements the real `runAfter` / scope-failure semantics.
    - `mocks.py` resolves connector results in a fixed precedence: override → saved → staticResult → connector-schema sample → AI → stub.
    - `knowledge.py` holds user test content.
    - `cases.py` stores saved test conversations.
  - Per-workspace data lives in `<ws>/.builder/test/`. Sessions and flow-run history are kept in memory.

- **Form edits** use `workspace/patch.set_fields` via `PATCH /fields`. It does a ruamel round-trip; pointer `""` is the document root, dotted keys create nested maps, and `None` deletes a key. Comments are kept.
- **Dashboard:** `GET /overview` aggregates the analyzer, test cases and their last run (`.builder/test/last-run.json`), recent AI changes and the deploy checklist.

## Conventions

- **Python:** 3.12+, uv, Pydantic models, small focused modules, and a module docstring explaining *why*.
  - YAML is read with `yamlio.load_plain` for analysis. Edits go through `load_rt`/`dump_rt` (ruamel round-trip), so comments such as `# Name:` survive.
  - File writes always go through `workspace/store.py` (`resolve_path` blocks path traversal).
- **PwC GenAI** (`genai_client.py`): `api-key` header (never Bearer), `/v1/responses`, retries only on 5xx and network errors, `_extract_text` handles all three response shapes.
- **Frontend:**
  - PwC design tokens in `src/theme.css` (dark default, `[data-theme="light"]`), Inter, orange `#d04a02` as the only accent, lucide icons. No Tailwind.
  - Server state via react-query. Query keys include `version` from `useWs()` (a content hash), so views refresh after any file change.
  - Workspace pages read `useWs()` (`pages/WorkspaceLayout.tsx`): `ws`, `selected`, `select`, `openAI`.
  - Monaco is bundled locally (`components/monacoSetup.ts`), because a CDN may be blocked.
- **Adding an API endpoint:**
  1. Add the route in `backend/app/main.py`.
  2. Add a typed method in `frontend/src/api.ts`.
  3. Add a test in `backend/tests/`.
- **Errors:** raise `WorkspaceError` or `ValueError` (→ 400) or `GenAIError` (→ 502). Handlers live in `main.py`.

## Gotchas

- `mcs.metadata` is a VS Code wrapper key. Strip it before schema validation and solution export. `GptComponentMetadata` keeps `displayName` in solutions.
- The schema root only covers AdaptiveDialog and TaskDialog. Validate against `definitions[kind]` (see `validator._validator`).
- Microsoft's own templates skip `init:`, so the `init-prefix` lint is info-level only.
- `workflow.json` format is best effort; Microsoft's pack has no flow guide. Accept both `properties.definition` and a bare `definition` (`validator.flow_definition`).
- Unknown topic node kinds must emit a `warning` event in the simulator, never be silently skipped. Fidelity gaps are labelled in the UI.
- The auto-mode classifier blocks killing processes found by port, `rm -rf` on paths you didn't create, and running code from external repos (e.g. the Microsoft skills scripts). Ask the user for those.
