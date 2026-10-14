# Frontend (Vite · React 18 · TypeScript)

`BACKEND_PORT=8010 npx vite` · `npx tsc -b` (strict; unused locals fail) · `npx vite build`

## Structure (`src/`)

| Path | Responsibility |
|---|---|
| `api.ts` | Every backend call plus TS mirrors of the backend Pydantic models. Keep in sync with `backend/app`. |
| `App.tsx` | Router and sidebar. Routes: `/`, `/patterns`, `/w/:wid` (Build), `/map`, `/analyzer`, `/docs`, `/deploy`. |
| `pages/WorkspaceLayout.tsx` | Loads the workspace. Provides `useWs()` → `{ws, version, selected, select, openAI, openTest, marks, setMarks, pending, setPending, openReview}`. Hosts the `RightDock` (AI Builder and Test tabs, resizable; the page makes room via `--dock-w`), `ReviewChanges` and `CommandPalette` (⌘K). The selected component is in `?c=<component id>`; `select()` navigates to Build from other pages. |
| `pages/Overview.tsx` | Dashboard (`api.overview`): deploy checklist, health, tests, components, instructions, recent AI changes. |
| `components/RightDock.tsx`, `ReviewChanges.tsx`, `CommandPalette.tsx` | The docked sidebar; the full-screen proposal review; ⌘K navigation. |
| `components/NodeForms.tsx`, `AgentOverview.tsx` | Form editors that write through `api.setFields` (`FORM_KINDS`, variable picker); the agent Overview tab. |
| `lib/labels.ts` | `fileLabel(path)` readable names ("Topic · Reset Password"), mirroring backend `generator.file_label`. |
| `pages/Build.tsx` | Explorer, Visual (canvas) and code tabs, `NodePanel` (edit one node), paste modal, live validation. |
| `pages/Deploy.tsx` + `components/SolutionExport.tsx` | Importable solution (new agent / update existing), VS Code merge, sync, workspace zip. |
| `pages/Docs.tsx` | Documentation preview. Mermaid SVGs are collected and rasterised (`svgToPng`) for the .docx export. |
| `components/TestPane.tsx` | The test pane slide-in (460 px; the workspace shrinks via `.with-test`). Tabs: Chat (SSE via `api.sendTest`), Activity, Variables, Tests (saved cases, Run all), Settings (test user, knowledge test content). "Track between topics" navigates to the running component. |
| `components/FlowTest.tsx` | `FlowTestPanel` (the Build-page side panel for an agent flow: inputs, per-connector Auto/Mock/Force failure, run history), `RunDetails`, `FlowRunModal` (runs started from the chat). |
| `components/AdaptiveCardView.tsx` | Microsoft `adaptivecards` SDK renderer, with a host config built from the theme CSS variables. |
| `components/AIPanel.tsx` | AI Builder pane: Auto mode and the mode chips, a context chip, Stop (AbortController), history from the backend, `RunTimeline` (live steps from stream events), `ProposalResult` (files changed, Review / Apply all, follow-ups), `useApply`. |
| `components/Canvas.tsx` | React Flow and dagre layout (height per node). `family(n)` maps each node to a colour family `f-*` (`--n-*` tokens). The card body comes from `GNode.meta`. A ResizeObserver refits the canvas unless the user has panned. `onNavigate` handles redirect and flow links. |
| `components/Editors.tsx` | Monaco `CodeEditor`, `DiffView`, `Mermaid` (`htmlLabels` off, so it can be rasterised), `svgToPng`, `useTheme`. |
| `components/ui.tsx` | `ToastProvider`/`useToast`, `Modal`, `SeverityPill`, `Empty`. |
| `theme.css` | PwC tokens and every component class (`btn-*`, `card`, `pill-sm`, `tabs`, `slide-panel`, `gnode`, …). |

## Rules

- **Styling:**
  - Use the existing classes and CSS variables only; never hard-code colours.
  - Orange is the only accent. The one exception, agreed with the user, is the canvas node families (`--n-*`, Copilot Studio-like), which are used on the canvas and legends only.
  - Radii: 8 for inputs and buttons, 12 for cards, 16 for modals, 999 for pills.
- **Canvas marks:** `useWs().marks[componentId][pointer] = active | visited | ok | failed | skipped`. They are set by the test pane and flow runs, and passed to `<Canvas marks>`. Changing marks must not change the `graph` object, which would trigger a refit.
- **Query keys:** any query whose result depends on file contents must include `version` from `useWs()`.
- **Downloads:** use plain `<a href={api.…Url()}>` for GET downloads, or `downloadBlob` for POST results.
- **E2E checks:** Playwright with `channel="chrome"`, headless, via `uv run --with playwright python script.py`. Point it at your own test servers, not the user's running app.
