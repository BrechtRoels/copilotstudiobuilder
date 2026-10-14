"""Access to the vendored Copilot Studio knowledge pack.

`mcs/` is vendored verbatim from microsoft/skills-for-copilot-studio (MIT, see
mcs/SOURCE_COMMIT). `builder/` holds guides written for this app.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from .. import config

MCS = config.KNOWLEDGE_DIR
BUILDER = Path(__file__).resolve().parent / "builder"


def _strip_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    meta: dict = {}
    for line in text[3:end].strip().splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            v = v.strip().strip('"')
            if v.startswith("[") and v.endswith("]"):
                meta[k.strip()] = [t.strip() for t in v[1:-1].split(",") if t.strip()]
            else:
                meta[k.strip()] = v
    return meta, text[end + 4:].lstrip()


# Skill docs reference Node helper scripts we don't ship; tell the model so.
_SCRIPT_NOTE = re.compile(r"node \$\{CLAUDE_SKILL_DIR\}[^\n]*")


def guide(name: str) -> str:
    """Body of a vendored skill (skills/<name>/SKILL.md) or builder guide (<name>.md)."""
    p = BUILDER / f"{name}.md"
    if p.exists():
        return p.read_text()
    p = MCS / "skills" / name / "SKILL.md"
    if not p.exists():
        p = next(MCS.glob(f"skills/*/{name}.md"), None)
        if p is None:
            return ""
    _, body = _strip_frontmatter(p.read_text())
    body = _SCRIPT_NOTE.sub("(schema lookup is provided to you inline instead)", body)
    return body.replace("${CLAUDE_SKILL_DIR}/../../", "")


@dataclass
class Pattern:
    id: str
    name: str
    description: str
    challenge: str
    status: str
    tags: list[str] = field(default_factory=list)
    body: str = ""


@lru_cache(maxsize=1)
def patterns() -> dict[str, Pattern]:
    out = {}
    for p in sorted((MCS / "patterns").glob("*.md")):
        meta, body = _strip_frontmatter(p.read_text())
        out[p.stem] = Pattern(
            id=p.stem, name=meta.get("name", p.stem), description=meta.get("description", ""),
            challenge=meta.get("challenge", ""), status=meta.get("status", ""),
            tags=meta.get("tags", []) if isinstance(meta.get("tags"), list) else [], body=body,
        )
    return out


@lru_cache(maxsize=1)
def templates() -> dict[str, str]:
    """Template relative path -> text."""
    root = MCS / "templates"
    return {p.relative_to(root).as_posix(): p.read_text() for p in sorted(root.rglob("*.yml"))}


def template_description(text: str) -> str:
    m = re.search(r"^#\s*Template:\s*(.+)$", text, re.MULTILINE)
    return m.group(1).strip() if m else ""


# --- schema lookup (Python stand-in for Microsoft's schema-lookup.bundle.js) --
@lru_cache(maxsize=1)
def _defs() -> dict:
    return json.loads((MCS / "reference" / "bot.schema.yaml-authoring.json").read_text())["definitions"]


@lru_cache(maxsize=1)
def node_kinds() -> list[str]:
    """Every definition that declares a `kind` const — i.e. a valid `kind:` value."""
    return sorted(k for k, v in _defs().items()
                  if isinstance(v.get("properties", {}).get("kind"), dict) and "const" in v["properties"]["kind"])


def _type_of(prop: dict) -> str:
    if "$ref" in prop:
        return prop["$ref"].rsplit("/", 1)[-1]
    if "const" in prop:
        return repr(prop["const"])
    if "oneOf" in prop:
        consts = [o.get("const") for o in prop["oneOf"] if "const" in o]
        if consts:
            return " | ".join(map(str, consts))
        return " | ".join(_type_of(o) for o in prop["oneOf"])
    t = prop.get("type", "any")
    if t == "array" and isinstance(prop.get("items"), dict):
        return f"array<{_type_of(prop['items'])}>"
    return str(t)


def resolve_kind(kind: str, expand: bool = False) -> str:
    """Compact, model-friendly description of a schema definition.

    `expand` adds the property names of small referenced objects (e.g. `aISettings: AISettingsNoKind
    {useModelKnowledge, isFileAnalysisEnabled, ...}`), so nested keys are visible without a second lookup.
    """
    d = _defs().get(kind)
    if d is None:
        return f"{kind}: NOT A VALID KIND"
    if "oneOf" in d and "properties" not in d:
        alts = [_type_of(o) for o in d["oneOf"]]
        return f"{kind} = one of: {', '.join(alts)}"
    props = d.get("properties", {})
    req = set(d.get("required", []))
    lines = [f"{kind}:"]
    for name, p in props.items():
        if name == "kind":
            continue
        line = f"  {name}{' (required)' if name in req else ''}: {_type_of(p)}"
        if expand and "$ref" in p:
            sub = [k for k in (_defs().get(_type_of(p), {}).get("properties") or {}) if k != "kind"]
            if 0 < len(sub) <= 15:
                line += " {" + ", ".join(sub) + "}"
        lines.append(line)
    return "\n".join(lines)
