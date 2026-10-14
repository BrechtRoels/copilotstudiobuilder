"""Doc -> standalone PwC-styled HTML file (Mermaid diagrams render client-side)."""
from __future__ import annotations

from jinja2 import Environment, select_autoescape

from .builder import Doc

_env = Environment(autoescape=select_autoescape(default=True), trim_blocks=True, lstrip_blocks=True)

TEMPLATE = _env.from_string(r"""<!doctype html>
<html lang="en" data-theme="light">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ d.title }} — Agent documentation</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{--c-page:#1a1a1a;--c-surface:#232323;--c-raised:#2d2d2d;--c-edge:#3d3d3d;--c-fg:#f2f2f2;--c-fg2:#a3a3a3;--c-fg3:#737373;
--c-primary:#d04a02;--c-primary-10:rgba(208,74,2,.10);--c-danger:#e0301e;--c-success:#2d8c3c;--c-warn:#ffb600;color-scheme:dark}
[data-theme="light"]{--c-page:#f5f5f0;--c-surface:#ffffff;--c-raised:#f7f5f2;--c-edge:#d9d4cc;--c-fg:#2d2d2d;--c-fg2:#5c5c5c;
--c-fg3:#8a8a8a;--c-danger:#c42a1a;--c-success:#1e7a30;--c-warn:#d49600;color-scheme:light}
*{box-sizing:border-box}body{margin:0;background:var(--c-page);color:var(--c-fg);font:14px/1.5 Inter,system-ui,sans-serif;-webkit-font-smoothing:antialiased}
main{max-width:960px;margin:0 auto;padding:40px 16px}
h1{font-size:24px;font-weight:700;letter-spacing:-.02em;margin:0 0 4px}h2{font-size:16px;font-weight:600;margin:40px 0 12px;padding-bottom:8px;border-bottom:2px solid var(--c-primary)}
h3{font-size:14px;font-weight:600;margin:0 0 8px}.meta{color:var(--c-fg2);font-size:13px}
.card{background:var(--c-surface);border:1px solid var(--c-edge);border-radius:12px;padding:16px;margin-bottom:8px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px;margin-top:16px}
.stat{background:var(--c-surface);border:1px solid var(--c-edge);border-radius:12px;padding:12px 16px}
.stat b{display:block;font-size:22px}.stat span{color:var(--c-fg2);font-size:12px}
table{width:100%;border-collapse:collapse;font-size:13px;background:var(--c-surface);border:1px solid var(--c-edge);border-radius:12px;overflow:hidden}
th,td{text-align:left;padding:8px 12px;border-bottom:1px solid var(--c-edge);vertical-align:top}th{background:var(--c-raised);font-weight:600;color:var(--c-fg2);font-size:12px}
.pill{display:inline-block;padding:1px 8px;border-radius:999px;background:var(--c-primary-10);color:var(--c-primary);font-size:11px;font-weight:500;margin:0 4px 4px 0}
.sev-error{color:var(--c-danger);font-weight:600}.sev-warning{color:var(--c-warn);font-weight:600}.sev-info{color:var(--c-fg3)}
pre{white-space:pre-wrap;background:var(--c-raised);border:1px solid var(--c-edge);border-radius:8px;padding:12px;font-size:12px}
.mermaid{background:var(--c-surface);border:1px solid var(--c-edge);border-radius:12px;padding:16px;overflow-x:auto;margin:8px 0}
.toc a{color:var(--c-primary);text-decoration:none;margin-right:16px;font-size:13px}
.kv{display:grid;grid-template-columns:140px 1fr;gap:4px 12px;font-size:13px}.kv dt{color:var(--c-fg2)}.kv dd{margin:0}
@media print{.toc{display:none}.card,.mermaid,table{break-inside:avoid}}
</style>
</head>
<body><main>
<h1>{{ d.title }}</h1>
<div class="meta">Agent documentation · generated {{ d.generatedAt }} by Copilot Studio Builder · {{ d.orchestration }} orchestration{% if d.model %} · model {{ d.model }}{% endif %}</div>
<div class="grid">
{% for k, v in [("Topics", d.counts.topics), ("Child agents", d.counts.childAgents), ("Tools", d.counts.tools), ("Agent flows", d.counts.flows), ("Knowledge", d.counts.knowledge), ("Global variables", d.counts.variables)] %}
<div class="stat"><b>{{ v }}</b><span>{{ k }}</span></div>
{% endfor %}
</div>
<p class="toc" style="margin-top:24px"><a href="#overview">Overview</a><a href="#architecture">Architecture</a>{% if d.childAgents %}<a href="#agents">Child agents</a>{% endif %}<a href="#topics">Topics</a>{% if d.tools %}<a href="#tools">Tools</a>{% endif %}{% if d.flows %}<a href="#flows">Flows</a>{% endif %}{% if d.knowledge %}<a href="#knowledge">Knowledge</a>{% endif %}<a href="#quality">Quality</a></p>

<h2 id="overview">1. Overview</h2>
{% if d.overview %}<p>{{ d.overview }}</p>{% endif %}
{% if d.instructions %}<h3>Agent instructions</h3><pre>{{ d.instructions }}</pre>{% endif %}
{% if d.starters %}<h3>Conversation starters</h3><p>{% for s in d.starters %}<span class="pill">{{ s }}</span>{% endfor %}</p>{% endif %}

<h2 id="architecture">2. Architecture</h2>
{% for g in d.diagrams if g.id == "agent-map" %}<pre class="mermaid">{{ g.mermaid }}</pre>{% endfor %}

{% if d.childAgents %}
<h2 id="agents">3. Child agents</h2>
{% for a in d.childAgents %}
<div class="card"><h3>{{ a.name }} <span class="pill">{{ a.kind }}</span></h3>
{% if a.summary %}<p>{{ a.summary }}</p>{% endif %}
<dl class="kv"><dt>Routing description</dt><dd>{{ a.description or "—" }}</dd>
<dt>Inputs</dt><dd>{{ a.inputs|join(", ") or "—" }}</dd><dt>Outputs</dt><dd>{{ a.outputs|join(", ") or "—" }}</dd></dl>
{% if a.instructions %}<pre>{{ a.instructions }}</pre>{% endif %}</div>
{% endfor %}
{% endif %}

<h2 id="topics">4. Topics</h2>
<table><tr><th>Topic</th><th>Trigger</th><th>Routing</th><th>Nodes</th></tr>
{% for t in d.topics %}<tr><td><a href="#{{ t.diagram }}">{{ t.name }}</a>{% if t.owner %}<br><span class="meta">{{ t.owner }}</span>{% endif %}</td><td>{{ t.trigger }}</td>
<td>{{ t.description or t.phrases[:3]|join(" · ") or "—" }}</td><td>{{ t.nodeCount }}</td></tr>{% endfor %}
</table>
{% for t in d.topics %}
<div class="card" id="{{ t.diagram }}" style="margin-top:16px"><h3>{{ t.name }} <span class="pill">{{ t.trigger }}</span></h3>
{% if t.summary %}<p>{{ t.summary }}</p>{% endif %}
<dl class="kv">
{% if t.description %}<dt>Model description</dt><dd>{{ t.description }}</dd>{% endif %}
{% if t.phrases %}<dt>Trigger phrases</dt><dd>{% for p in t.phrases %}<span class="pill">{{ p }}</span>{% endfor %}</dd>{% endif %}
<dt>Variables</dt><dd>{{ t.variables|join(", ") or "—" }}</dd>
<dt>Redirects to</dt><dd>{{ t.redirects|join(", ") or "—" }}</dd>
<dt>Flows called</dt><dd>{{ t.flows|join(", ") or "—" }}</dd></dl>
{% for g in d.diagrams if g.id == t.diagram %}<pre class="mermaid">{{ g.mermaid }}</pre>{% endfor %}
</div>
{% endfor %}

{% if d.tools %}
<h2 id="tools">5. Tools</h2>
<table><tr><th>Tool</th><th>Type</th><th>Target</th><th>Inputs</th><th>Description</th></tr>
{% for t in d.tools %}<tr><td>{{ t.name }}</td><td>{{ t.type }}</td><td>{{ t.target }}</td><td>{{ t.inputs|join(", ") }}</td><td>{{ t.description }}{% if t.summary %}<br><span class="meta">{{ t.summary }}</span>{% endif %}</td></tr>{% endfor %}
</table>
{% endif %}

{% if d.flows %}
<h2 id="flows">6. Agent flows</h2>
{% for f in d.flows %}
<div class="card"><h3>{{ f.name }}</h3>{% if f.summary %}<p>{{ f.summary }}</p>{% endif %}
<dl class="kv"><dt>Trigger</dt><dd>{{ f.trigger }}</dd><dt>Inputs</dt><dd>{{ f.inputs|join(", ") or "—" }}</dd>
<dt>Outputs</dt><dd>{{ f.outputs|join(", ") or "—" }}</dd><dt>Connectors</dt><dd>{{ f.connectors|join(", ") or "—" }}</dd>
<dt>Error handling</dt><dd class="{{ 'sev-info' if f.errorHandling else 'sev-warning' }}">{{ "Yes" if f.errorHandling else "No failure path" }}</dd></dl>
{% for g in d.diagrams if g.id == f.diagram %}<pre class="mermaid">{{ g.mermaid }}</pre>{% endfor %}</div>
{% endfor %}
{% endif %}

{% if d.knowledge or d.variables or d.connections %}
<h2 id="knowledge">7. Knowledge, variables &amp; connections</h2>
{% if d.knowledge %}<table><tr><th>Knowledge source</th><th>Type</th><th>Used by</th><th>Location</th></tr>
{% for k in d.knowledge %}<tr><td>{{ k.name }}</td><td>{{ k.type }}</td><td>{{ k.owner }}</td><td>{{ k.detail }}</td></tr>{% endfor %}</table>{% endif %}
{% if d.variables %}<br><table><tr><th>Global variable</th><th>AI visibility</th><th>Description</th></tr>
{% for v in d.variables %}<tr><td>{{ v.name }}</td><td>{{ v.visibility }}</td><td>{{ v.description }}</td></tr>{% endfor %}</table>{% endif %}
{% if d.connections %}<br><table><tr><th>Connection reference</th></tr>{% for c in d.connections %}<tr><td>{{ c }}</td></tr>{% endfor %}</table>{% endif %}
{% endif %}

<h2 id="quality">8. Quality findings</h2>
{% if d.findings %}<table><tr><th>Severity</th><th>Component</th><th>Finding</th></tr>
{% for f in d.findings|sort(attribute="severity") %}<tr><td class="sev-{{ f.severity }}">{{ f.severity }}</td><td>{{ f.component }}</td><td>{{ f.message }}</td></tr>{% endfor %}</table>
{% else %}<p>No findings.</p>{% endif %}
</main>
<script type="module">
import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";
mermaid.initialize({ startOnLoad: true, theme: "base", themeVariables: { primaryColor: "#f7f5f2", primaryBorderColor: "#d04a02", lineColor: "#8a8a8a", fontFamily: "Inter" } });
</script>
</body></html>
""")


def render_html(doc: Doc) -> str:
    return TEMPLATE.render(d=doc)
