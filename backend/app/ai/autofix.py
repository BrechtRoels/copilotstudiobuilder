"""Cheap deterministic fixes applied before spending an LLM repair round."""
from __future__ import annotations

import secrets
import string

from ..workspace.yamlio import dump_rt, load_rt

_ALPHABET = string.ascii_letters + string.digits


def new_id(kind: str) -> str:
    prefix = (kind[:1].lower() + kind[1:]) if kind else "node"
    return f"{prefix}_{''.join(secrets.choice(_ALPHABET) for _ in range(6))}"


def fix_ids(text: str) -> str:
    """Replace `_REPLACE` placeholder ids and duplicate ids with fresh unique ones.

    Leaves the file untouched (byte-identical) when nothing needs fixing.
    """
    try:
        doc = load_rt(text)
    except Exception:  # noqa: BLE001 - unparsable YAML is the LLM repair loop's job
        return text
    seen: set[str] = set()
    changed = False

    def walk(node, parent_key=None):
        nonlocal changed
        if isinstance(node, dict):
            nid = node.get("id")
            if isinstance(nid, str) and parent_key != "intent" and nid != "main":
                if "_REPLACE" in nid or nid in seen:
                    node["id"] = new_id(str(node.get("kind") or nid.split("_")[0]))
                    changed = True
                seen.add(node["id"])
            for k, v in node.items():
                walk(v, k)
        elif isinstance(node, list):
            for v in node:
                walk(v, parent_key)

    walk(doc)
    return dump_rt(doc) if changed else text
