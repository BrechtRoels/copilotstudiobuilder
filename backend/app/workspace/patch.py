"""Read/replace one node inside a component file (canvas side-panel edits).

YAML goes through ruamel round-trip so comments and formatting elsewhere survive.
Flow pointers (from flow_graph) are relative to the workflow *definition*.
"""
from __future__ import annotations

import json
from typing import Any

from ..validation.validator import flow_definition
from .yamlio import dump_rt, load_rt


def _resolve(doc, parts: list[str]):
    cur = doc
    for part in parts:
        if isinstance(cur, list):
            cur = cur[int(part)]
        elif isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            raise KeyError(f"Pointer segment '{part}' not found")
    return cur


def _parts(pointer: str) -> list[str]:
    parts = [p for p in pointer.split("/") if p]
    if not parts:
        raise ValueError("Empty pointer")
    return parts


def get_node(text: str, pointer: str, is_flow: bool) -> str:
    parts = _parts(pointer)
    if is_flow:
        return json.dumps(_resolve(flow_definition(json.loads(text)), parts), indent=2)
    return dump_rt(_resolve(load_rt(text), parts))


def replace_node(text: str, pointer: str, node_text: str, is_flow: bool) -> str:
    parts = _parts(pointer)
    if is_flow:
        doc = json.loads(text)
        parent = _resolve(flow_definition(doc), parts[:-1])
        new = json.loads(node_text)
    else:
        doc = load_rt(text)
        parent = _resolve(doc, parts[:-1])
        new = load_rt(node_text)
    if not isinstance(new, dict):
        raise ValueError("A node must be a mapping/object")
    key = parts[-1]
    if isinstance(parent, list):
        parent[int(key)] = new
    else:
        parent[key] = new
    return json.dumps(doc, indent=2) + "\n" if is_flow else dump_rt(doc)


def _to_rt(v: Any) -> Any:
    """Plain JSON value -> ruamel types, so new content is written in block style (multi-line text as `|`)."""
    from ruamel.yaml.comments import CommentedMap, CommentedSeq
    from ruamel.yaml.scalarstring import LiteralScalarString
    if isinstance(v, str) and "\n" in v:
        return LiteralScalarString(v if v.endswith("\n") else v + "\n")
    if isinstance(v, dict):
        m = CommentedMap()
        for k, x in v.items():
            m[k] = _to_rt(x)
        return m
    if isinstance(v, list):
        s = CommentedSeq()
        for x in v:
            s.append(_to_rt(x))
        return s
    return v


def set_fields(text: str, pointer: str, fields: dict[str, Any]) -> str:
    """Set keys on the YAML node at `pointer` ("" = document root), keeping comments and other keys.

    A key may be dotted (`intent.triggerQueries`) to reach nested mappings, created when missing.
    A value of None removes the key.
    """
    doc = load_rt(text) if text.strip() else None
    if doc is None:
        from ruamel.yaml.comments import CommentedMap
        doc = CommentedMap()
    node = _resolve(doc, [p for p in pointer.split("/") if p])
    if not isinstance(node, dict):
        raise ValueError("Fields can only be set on a mapping node")
    for key, value in fields.items():
        *path, last = key.split(".")
        target = node
        for p in path:
            if not isinstance(target.get(p), dict):
                if value is None:
                    target = None
                    break
                target[p] = _to_rt({})
            target = target[p]
        if target is None:
            continue
        if value is None:
            target.pop(last, None)
        else:
            target[last] = _to_rt(value)
    return dump_rt(doc)
