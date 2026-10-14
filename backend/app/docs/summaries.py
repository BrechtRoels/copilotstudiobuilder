"""Plain-language AI summaries per component, cached by content hash.

Facts in the docs always come from the parser; the model only writes prose.
Cache: <workspace>/.builder/summaries.json
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

from .. import config
from ..genai_client import llm_complete
from ..workspace.model import Component, Workspace

SYSTEM = ("You write documentation for Microsoft Copilot Studio agents for business and IT readers. "
          "Explain what the component does, when it runs and what the user experiences, in 2-4 plain sentences. "
          "No YAML, no headings, no speculation beyond the definition.")

DOC_CATEGORIES = ("agent", "topic", "action", "workflow", "childAgent")


def _cache_file(ws: Workspace) -> Path:
    return Path(ws.info.path) / ".builder" / "summaries.json"


def _hash(c: Component) -> str:
    return hashlib.sha256(c.text.encode()).hexdigest()[:16]


def cached(ws: Workspace) -> dict[str, str]:
    """component id -> summary, only for entries whose content hash still matches."""
    f = _cache_file(ws)
    if not f.exists():
        return {}
    raw = json.loads(f.read_text())
    current = {c.id: _hash(c) for c in ws.components}
    return {cid: v["text"] for cid, v in raw.items() if current.get(cid) == v.get("hash")}


async def refresh(ws: Workspace, concurrency: int = 4) -> dict[str, str]:
    f = _cache_file(ws)
    raw = json.loads(f.read_text()) if f.exists() else {}
    todo = [c for c in ws.components
            if c.category in DOC_CATEGORIES and c.text and raw.get(c.id, {}).get("hash") != _hash(c)]
    sem = asyncio.Semaphore(concurrency)

    async def one(c: Component) -> None:
        async with sem:
            if config.USE_MOCK_AI:
                text = f"[mock summary] {c.displayName} is a {c.category} in this agent."
            else:
                text = (await llm_complete(f"Component type: {c.category} ({c.kind})\nName: {c.displayName}\n\n"
                                           f"Definition:\n{c.text[:12000]}",
                                           model=config.GENAI_FAST_MODEL, system=SYSTEM)).strip()
            raw[c.id] = {"hash": _hash(c), "text": text}

    await asyncio.gather(*(one(c) for c in todo))
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(raw, indent=2))
    return cached(ws)
