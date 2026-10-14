"""YAML helpers.

Two loaders on purpose:
  * `load_plain` -> plain dicts/lists for analysis, graphs and validation.
  * `load_rt` / `dump_rt` -> ruamel round-trip objects that keep comments and key
    order, used when we patch a single node inside an existing file.
Copilot Studio files carry meaningful leading comments (`# Name: ...`), so any
write that isn't a full-text replacement must go through the round-trip pair.
"""
from __future__ import annotations

import io
import re
from typing import Any

from ruamel.yaml import YAML

_safe = YAML(typ="safe", pure=True)


def _rt() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def load_plain(text: str) -> Any:
    return _safe.load(text) if text.strip() else None


def load_rt(text: str) -> Any:
    return _rt().load(text)


def dump_rt(data: Any) -> str:
    buf = io.StringIO()
    _rt().dump(data, buf)
    return buf.getvalue()


_NAME_RE = re.compile(r"^#\s*Name:\s*(.+?)\s*$", re.MULTILINE)


def header_name(text: str) -> str | None:
    """Display name from the `# Name: X` header comment Copilot Studio writes."""
    m = _NAME_RE.search(text[:500])
    if not m:
        return None
    name = m.group(1)
    return None if name.startswith("<") else name
