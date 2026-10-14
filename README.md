# Copilot Studio Builder

A local web app to **generate, visualise, validate and document** Microsoft Copilot Studio agents, using PwC GenAI. It covers topics, agent flows, tools, knowledge, and multi-agent (child agent) setups.

```bash
cp .env.example .env    # add GENAI_API_KEY, or set USE_MOCK_AI=true
./start.sh              # → http://localhost:5173
```

## How it works

Agents are handled **as code**, in the same folder format the
[Copilot Studio VS Code extension](https://learn.microsoft.com/microsoft-copilot-studio/visual-studio-code-extension-overview)
uses:

```
agent.mcs.yml  settings.mcs.yml  connectionreferences.mcs.yml
topics/*.mcs.yml   actions/*.mcs.yml   knowledge/*.mcs.yml   variables/*.mcs.yml
agents/<Child>/agent.mcs.yml        workflows/<Flow>/{metadata.yaml, workflow.json}
```

There are three ways in, and all of them end up as the same workspace folder:

| Start | What happens |
|---|---|
| **Build with AI** | You describe the agent. The AI generates the agent instructions, topics, child agents, flows and tools. |
| **Open agent folder** | Point the app at a folder you cloned with the VS Code extension. Files are edited in place. |
| **Import solution** (optional) | An exported solution `.zip` is converted to the folder format. |

### Pages

| Page | Purpose |
|---|---|
| **Overview** | Workspace dashboard: a *ready to deploy* checklist, analyzer health with AI fixes, test results with **Run all**, component counts, the instructions, and recent AI changes. |
| **Build** | File explorer (with *AI · new / changed* badges for unapplied proposals), a Copilot Studio-style canvas (colour per node type; nodes show their text, entity, variable and redirect target), **form editors** for common nodes (message, question, condition, set variable, redirect, run flow, trigger phrases) with YAML as a fallback, an agent **Overview** (instructions, starters, orchestration, descriptions), the Monaco editor with live validation, and paste-from-Copilot-Studio. Press **⌘K** to jump anywhere. |
| **AI Builder** (docked panel, with Test as a second tab) | **Auto** mode picks agent, topic, flow, child agent, modify or ask. Progress streams live. Results open in a full-screen **Review changes** view. Chat history is saved per workspace. See [AI generation](#ai-generation). |
| **Agent map** | The whole architecture: orchestrator → child agents, topics, tools, flows and knowledge, with redirect and flow edges. |
| **Analyzer** | Schema and lint checks, plus cross-file checks. See [Analyzer checks](#analyzer-checks). |
| **Documentation** | Always-current documentation built from the files. See [Documentation](#documentation). |
| **Test** (side panel, every page) | Chat with the agent as in Copilot Studio's test pane, and run agent flows as in Power Automate's *Test flow*. Runs locally. See [Testing](#testing). |
| **Deploy** | **Importable solution** (.zip for Power Apps → Solutions → Import), copy into a VS Code clone and **Apply changes**, the optional built-in Validate/Pull/Push, or the raw workspace zip. |

### AI generation

Each AI request goes through these steps:

1. The prompt is built from Microsoft's YAML reference, the matching templates and patterns, the list of valid `kind:` values, the schema of the kinds it will likely use, and the full text of the agent's current files (so a follow-up changes what is there instead of starting over).
2. The output is checked against the authoring JSON schema.
3. Duplicate or placeholder IDs are fixed automatically.
4. If validation still fails, the model gets up to 2 repair rounds.
5. Every step streams to the panel as it happens: the guides, templates and patterns being read; the model's plan; files appearing as they're written; schema results per file; repair rounds; and the analyzer.
6. You see a proposal before anything is written:
   - a visual preview with new and changed nodes highlighted;
   - a diff and the validation results;
   - analyzer findings on the merged result;
   - a requirement check: each requirement in your request marked covered, partial or missing, with where it is implemented (**Cover gaps** sends the rest back);
   - a checkbox per file, so you only apply what you choose.

### Analyzer checks

- Schema and lint: duplicate IDs, Power Fx functions Copilot Studio doesn't support, `init:` on first assignment, child agents with actions.
- Broken redirects, and undefined or unused variables.
- Overlapping trigger phrases, and missing `modelDescription` (generative orchestration routes on it).
- Flows without error handling or without a response to the agent.
- Capabilities the instructions rely on but the configuration doesn't switch on: uploaded documents without file analysis (agent-wide and per AI node), web research without web browsing or a website source, "never use general knowledge" with model knowledge still on.
- Global variables nobody uses, unfilled `<placeholders>` in schema names, and instructions or descriptions naming topics or tools that no longer exist.
- Applying a proposal with errors asks for a second click.
- Each finding has an **AI fix** button.

### Documentation

- Built from the workspace files every time you open the page, so it never goes stale.
- Contains:
  - an overview and the agent instructions;
  - an architecture diagram;
  - child agents and topics (with trigger phrases, variables, redirects and a diagram per topic);
  - tools, flows (connectors, error handling), knowledge and variables;
  - analyzer findings.
- AI summaries are optional, written by GenAI, and cached per content hash. All facts come from the parser.
- Export as **HTML** (PwC style, Mermaid diagrams) or **Word** (diagrams rendered to images).

### Testing

**Test** opens a test pane that runs the workspace in a **local simulator**. Nothing is sent to Copilot Studio, Power Automate, Dataverse or any connector.

- **Chat:**
  - The greeting runs `OnConversationStart`.
  - Routing follows the agent's orchestration mode:
    - **classic:** trigger phrases, *Multiple Topics Matched*, Fallback with `System.FallbackCount`, Escalate;
    - **generative:** a planner that picks topics, tools and child agents from their descriptions, and asks for missing tool inputs.
  - Questions behave as in Copilot Studio: entities, re-prompts, default values and topic switching. So do redirects with input and output binding, Power Fx conditions and variables, Adaptive Cards (rendered with Microsoft's `adaptivecards` SDK), generative answers, and `OnError` with `System.Error`.
- **Track between topics:** the canvas follows the conversation, highlighting the running node and the path taken.
- **Tabs:**
  - **Activity:** why each topic or tool was chosen, plus every node, flow run and knowledge search.
  - **Variables:** live Topic, Global and System values.
  - **Tests:** saved conversations, re-run with **Run all** as regression tests.
  - **Settings:** the test user, and test content for knowledge sources.
- **Test flow** (an agent flow → **Test**):
  - Enter trigger inputs, run, and see per action whether it Succeeded, Failed or was Skipped, with its inputs and outputs.
  - Execution follows the real `runAfter` rules, so Try/Catch scopes behave as in Power Automate.
  - Connector actions are simulated, from the first available of: your mock output or **Force failure**; the action's `staticResult`; a sample generated from Microsoft's connector schema; an AI-generated reply.

**What differs from the real service:**
- Intent recognition and generative orchestration use PwC GenAI (or word overlap with `USE_MOCK_AI`) instead of Microsoft's models.
- Connectors are mocked.
- Knowledge answers come from the test content you provide.
- Sign-in uses a test user.

The Activity tab marks every simulated or AI-decided step.

### Importable solution

Deploy → **Importable solution** produces a Dataverse solution package with `solution.xml`, `customizations.xml` and `[Content_Types].xml` at the root:

- **New agent:** the app generates the package itself, mirroring a real classic-agent export: a `bot.xml` with template `default-2.1.0`, `configuration.json`, `gpt.default`, and default system topics. The solution name, agent schema name and publisher are stored per workspace, so re-importing upgrades the same agent.
- **Update existing agent:** upload that agent's unmanaged export once (an imported solution is kept automatically). Components are matched by name and updated in place, and everything else is copied byte-for-byte.

In both modes, redirects are rewritten to the solution's schema names, flows get connection references to map at import, and the version is bumped. Download the zip and import it **without unzipping**: Safari's auto-unzip plus Finder's "Compress" produces a folder zip, which Power Apps rejects.

## Built on Microsoft's skills

`backend/app/knowledge/mcs/` is vendored, unmodified, from
[microsoft/skills-for-copilot-studio](https://github.com/microsoft/skills-for-copilot-studio) (MIT; the commit is in `SOURCE_COMMIT`). It contains:

- the YAML authoring JSON schema;
- the templates and patterns;
- the authoring guides.

To update the pack, re-vendor it; don't edit it in place. `backend/app/knowledge/builder/agent-flows.md` is the app's own guide for `workflow.json`, which Microsoft's pack doesn't cover.

You can install the same skills in Claude Code and work from the CLI alongside the app:
`/plugin marketplace add microsoft/skills-for-copilot-studio`.

## Limits

- **Validation coverage.** The JSON schema catches structural errors. Power Fx type errors and environment-specific checks need Microsoft's language server, which the optional Validate (LSP) button on the Deploy page runs. It needs `MCS_SKILLS_DIR`, Node 18+ and the VS Code extension.
- **Flow format.** The `workflow.json` shape is best effort. Review generated flows in the Power Automate designer.
- **Connections.** Connection references must exist in the target environment.
- **Experimental upstream.** Microsoft's skills repo and the extension's sync are experimental.

## Development

```bash
cd backend && uv run pytest          # 71 tests; uses mock AI
cd frontend && npx tsc -b && npx vite build
```
