"""Graph -> Mermaid flowchart text (used by HTML docs and the docs page)."""
from __future__ import annotations

import re

from ..graph.topic_graph import Graph

_SHAPES = {
    "trigger": ("([", "])"), "branch": ("{", "}"), "end": ("(", ")"), "terminal": ("(", ")"),
    "redirect": ("[/", "/]"), "flow": ("[[", "]]"), "ai": ("{{", "}}"), "scope": ("[", "]"),
}


def _esc(s: str) -> str:
    s = re.sub(r'["`<>{}\[\]|#]', " ", s or "")
    return " ".join(s.split())[:60]


def to_mermaid(g: Graph, direction: str = "TD") -> str:
    ids = {n.id: f"n{i}" for i, n in enumerate(g.nodes)}
    lines = [f"flowchart {direction}"]
    for n in g.nodes:
        o, c = _SHAPES.get(n.variant, ("[", "]"))
        text = _esc(n.label) + (f"<br/>{_esc(n.detail)}" if n.detail else "")
        lines.append(f'  {ids[n.id]}{o}"{text}"{c}')
    for e in g.edges:
        if e.source not in ids or e.target not in ids:
            continue
        arrow = "-.->" if e.variant in ("goto", "error", "else") else "-->"
        label = f"|{_esc(e.label)}|" if e.label else ""
        lines.append(f"  {ids[e.source]} {arrow}{label} {ids[e.target]}")
    return "\n".join(lines)
