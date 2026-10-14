"""Knowledge for the test pane: user-supplied test content stands in for SharePoint / websites.

Each knowledge source can have test content stored in `<ws>/.builder/test/knowledge/<name>.txt`
(pasted text or an uploaded .txt / .md / .docx). Generative answers retrieve passages by keyword
overlap and ground the answer on them with citations. No match means "no answer", which falls
through to the fallback just like in Copilot Studio when search returns nothing.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from pathlib import Path

from .. import config
from ..genai_client import GenAIError, complete_json
from ..workspace.model import Component, Workspace

_WORD = re.compile(r"[a-z0-9]{3,}")
_STOP = set("the and for are you your with this that what how can from have has was were will would should "
            "about into they them their there here when where which who why our not but all any get got".split())


def _dir(ws_path: str | Path) -> Path:
    return Path(ws_path) / ".builder" / "test" / "knowledge"


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name)[:120] or "source"


def source_name(c: Component) -> str:
    return c.name


def sources(ws: Workspace) -> list[Component]:
    return ws.by_category("knowledge")


def describe(c: Component) -> dict:
    src = (c.data or {}).get("source") or {} if isinstance(c.data, dict) else {}
    return {"id": c.id, "name": c.name, "displayName": c.displayName, "owner": c.owner,
            "kind": src.get("kind", c.kind or ""), "site": src.get("site") or src.get("url") or "",
            "description": (c.data or {}).get("description", "") if isinstance(c.data, dict) else ""}


def get_content(ws_path: str | Path, name: str) -> str:
    f = _dir(ws_path) / f"{_safe(name)}.txt"
    return f.read_text() if f.exists() else ""


def set_content(ws_path: str | Path, name: str, text: str) -> None:
    d = _dir(ws_path)
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{_safe(name)}.txt"
    if text.strip():
        f.write_text(text)
    elif f.exists():
        f.unlink()


def extract_text(filename: str, raw: bytes) -> str:
    if filename.lower().endswith(".docx"):
        from docx import Document
        doc = Document(io.BytesIO(raw))
        return "\n".join(p.text for p in doc.paragraphs)
    return raw.decode("utf-8", errors="replace")


@dataclass
class Passage:
    source: str
    title: str
    text: str
    score: float


def _terms(s: str) -> set[str]:
    return {w for w in _WORD.findall(s.lower()) if w not in _STOP}


def _chunks(text: str, size: int = 900) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    out, buf = [], ""
    for p in paras:
        if len(buf) + len(p) > size and buf:
            out.append(buf)
            buf = ""
        buf = f"{buf}\n\n{p}" if buf else p
    if buf:
        out.append(buf)
    return out


def retrieve(ws: Workspace, query: str, only: list[str] | None = None, k: int = 4) -> tuple[list[Passage], list[str]]:
    """Top passages for a query, plus the names of sources that have no test content."""
    q = _terms(query)
    found: list[Passage] = []
    empty: list[str] = []
    for c in sources(ws):
        if only and c.name not in only and c.displayName not in only:
            continue
        text = get_content(ws.info.path, c.name)
        if not text.strip():
            empty.append(c.displayName)
            continue
        for chunk in _chunks(text):
            terms = _terms(chunk)
            if not q or not terms:
                continue
            hit = len(q & terms)
            if hit:
                found.append(Passage(c.name, c.displayName, chunk, hit / len(q)))
    found.sort(key=lambda p: p.score, reverse=True)
    return [p for p in found[:k] if p.score >= 0.2], empty


async def answer(ws: Workspace, query: str, instructions: str = "", only: list[str] | None = None,
                 additional: str = "") -> dict:
    """{answer: str | None, citations: [...], searched: [...], empty: [...], ai: bool}"""
    passages, empty = retrieve(ws, query, only)
    searched = [describe(c)["displayName"] for c in sources(ws) if not only or c.name in only or c.displayName in only]
    base = {"citations": [{"n": i + 1, "source": p.title, "excerpt": p.text[:300]} for i, p in enumerate(passages)],
            "searched": searched, "empty": empty, "query": query}
    if not passages:
        return {**base, "answer": None, "ai": False}
    if config.USE_MOCK_AI or not config.GENAI_API_KEY:
        snippet = " ".join(passages[0].text.split())[:400]
        return {**base, "answer": f"{snippet} [1]", "ai": False}
    ctx = "\n\n".join(f"[{i + 1}] ({p.title})\n{p.text}" for i, p in enumerate(passages))
    try:
        res = await complete_json(
            "Answer the user's question using ONLY the numbered passages. Cite passages inline like [1]. "
            "If the passages do not contain the answer, return {\"answer\": null}. "
            "Return JSON {\"answer\": string | null}.\n\n"
            f"Agent instructions (tone/format):\n{instructions[:2000]}\n{additional}\n\nPassages:\n{ctx}\n\nQuestion: {query}",
            model=config.GENAI_FAST_MODEL)
    except GenAIError as e:
        return {**base, "answer": None, "ai": True, "error": str(e)}
    return {**base, "answer": res.get("answer") or None, "ai": True}
