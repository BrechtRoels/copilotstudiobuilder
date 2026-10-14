"""Workflow Definition Language expressions (Power Automate / Logic Apps) for the flow test runner.

`"@expr"` is a whole-value expression, `"…@{expr}…"` is string interpolation and `"@@"` escapes
a literal `@`. Functions resolve against a `Ctx` (trigger, action results, variables, loop items).
Unknown functions raise `WdlError`, which fails the action like the real runtime would.
"""
from __future__ import annotations

import base64
import json
import random
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import quote, unquote


class WdlError(Exception):
    pass


@dataclass
class Ctx:
    trigger: dict = field(default_factory=dict)            # {"outputs": {"body": …, "headers": …}}
    actions: dict[str, dict] = field(default_factory=dict)  # name -> {status, inputs, outputs, …}
    variables: dict[str, Any] = field(default_factory=dict)
    items: dict[str, Any] = field(default_factory=dict)     # loop name -> current item
    item_stack: list[Any] = field(default_factory=list)
    parameters: dict[str, Any] = field(default_factory=dict)
    scopes: dict[str, list[str]] = field(default_factory=dict)  # scope name -> child action names
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:24].upper())


# --- tokenizer / parser -----------------------------------------------------------------------------
_TOK = re.compile(r"""\s*(?:
    (?P<num>-?\d+(?:\.\d+)?)
  | (?P<str>'(?:[^']|'')*')
  | (?P<id>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op>\?\[|\?\.|[()\[\],.])
)""", re.VERBOSE)


def _tokens(src: str) -> list[tuple[str, Any]]:
    out, i = [], 0
    while i < len(src):
        if src[i].isspace():
            i += 1
            continue
        m = _TOK.match(src, i)
        if not m or m.end() == i:
            raise WdlError(f"Unexpected character {src[i]!r} in expression: {src}")
        k = m.lastgroup
        v = m.group(k)
        if k == "num":
            out.append(("num", float(v) if "." in v else int(v)))
        elif k == "str":
            out.append(("str", v[1:-1].replace("''", "'")))
        else:
            out.append((k, v))
        i = m.end()
    out.append(("eof", None))
    return out


class _P:
    def __init__(self, src: str):
        self.src, self.t, self.i = src, _tokens(src), 0

    def peek(self):
        return self.t[self.i]

    def take(self):
        tok = self.t[self.i]
        self.i += 1
        return tok

    def want(self, op):
        k, v = self.take()
        if v != op:
            raise WdlError(f"Expected '{op}' in expression: {self.src}")

    def parse(self):
        n = self.expr()
        if self.peek()[0] != "eof":
            raise WdlError(f"Unexpected '{self.peek()[1]}' in expression: {self.src}")
        return n

    def expr(self):
        k, v = self.take()
        if k == "num" or k == "str":
            node = ("lit", v)
        elif k == "id":
            if v in ("true", "false"):
                node = ("lit", v == "true")
            elif v == "null":
                node = ("lit", None)
            else:
                self.want("(")
                args = []
                if self.peek()[1] != ")":
                    args.append(self.expr())
                    while self.peek()[1] == ",":
                        self.take()
                        args.append(self.expr())
                self.want(")")
                node = ("call", v, args)
        else:
            raise WdlError(f"Unexpected '{v}' in expression: {self.src}")
        while True:
            k, v = self.peek()
            if v in ("[", "?["):
                self.take()
                idx = self.expr()
                self.want("]")
                node = ("idx", node, idx, v == "?[")
            elif v in (".", "?."):
                self.take()
                _, name = self.take()
                node = ("idx", node, ("lit", name), v == "?.")
            else:
                return node


_CACHE: dict[str, Any] = {}


def _parse(src: str):
    if src not in _CACHE:
        _CACHE[src] = _P(src).parse()
    return _CACHE[src]


# --- evaluation ---------------------------------------------------------------------------------------
def evaluate(src: str, ctx: Ctx) -> Any:
    try:
        return _ev(_parse(src), ctx)
    except WdlError:
        raise
    except (TypeError, ValueError, KeyError, IndexError, ZeroDivisionError, AttributeError) as e:
        raise WdlError(f"{type(e).__name__} in '{src}': {e}")


def _ev(n, ctx: Ctx) -> Any:
    if n[0] == "lit":
        return n[1]
    if n[0] == "idx":
        obj = _ev(n[1], ctx)
        key = _ev(n[2], ctx)
        if obj is None:
            if n[3]:
                return None
            raise WdlError(f"Cannot read '{key}' of null (use ?[] to allow null)")
        if isinstance(obj, list):
            return obj[int(key)] if -len(obj) <= int(key) < len(obj) else None
        if isinstance(obj, dict):
            if key in obj:
                return obj[key]
            low = {str(k).lower(): v for k, v in obj.items()}
            if str(key).lower() in low:
                return low[str(key).lower()]
            if n[3]:
                return None
            raise WdlError(f"Property '{key}' doesn't exist (available: {', '.join(map(str, obj)) or 'none'})")
        raise WdlError(f"Cannot index a {type(obj).__name__}")
    if n[0] == "call":
        name = n[1]
        lazy = _LAZY.get(name.lower())
        if lazy:
            return lazy(n[2], ctx)
        fn = _FN.get(name.lower())
        if not fn:
            raise WdlError(f"Expression function '{name}' is not simulated")
        return fn(ctx, *[_ev(a, ctx) for a in n[2]])
    raise WdlError("Bad expression")


def resolve(value: Any, ctx: Ctx) -> Any:
    """Evaluate every expression inside an action's inputs (deep)."""
    if isinstance(value, str):
        if value.startswith("@@"):
            return value[1:]
        if value.startswith("@") and not value.startswith("@{"):
            return evaluate(value[1:], ctx)
        if "@{" in value:
            return _interp(value, ctx)
        return value
    if isinstance(value, dict):
        return {resolve(k, ctx) if isinstance(k, str) and "@" in k else k: resolve(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, ctx) for v in value]
    return value


def _interp(s: str, ctx: Ctx) -> str:
    out, i = [], 0
    while True:
        j = s.find("@{", i)
        if j < 0:
            out.append(s[i:])
            break
        out.append(s[i:j])
        depth, k, in_str = 1, j + 2, False
        while k < len(s) and depth:
            c = s[k]
            if c == "'":
                in_str = not in_str
            elif not in_str and c == "{":
                depth += 1
            elif not in_str and c == "}":
                depth -= 1
            k += 1
        out.append(to_string(evaluate(s[j + 2:k - 1], ctx)))
        i = k
    return "".join(out)


def to_string(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "True" if v else "False"
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


# --- functions -----------------------------------------------------------------------------------------
_FN: dict[str, Callable] = {}
_LAZY: dict[str, Callable] = {}


def fn(*names):
    def deco(f):
        for nm in names:
            _FN[nm.lower()] = f
        return f
    return deco


def lazy(name):
    def deco(f):
        _LAZY[name] = f
        return f
    return deco


def _action(ctx: Ctx, name: str) -> dict:
    if name not in ctx.actions:
        raise WdlError(f"The action '{name}' has not run yet (check runAfter)")
    return ctx.actions[name]


# referencing
fn("triggerBody")(lambda ctx: (ctx.trigger.get("outputs") or {}).get("body"))
fn("triggerOutputs")(lambda ctx: ctx.trigger.get("outputs"))
fn("trigger")(lambda ctx: ctx.trigger)
fn("outputs")(lambda ctx, name: _action(ctx, name).get("outputs"))
fn("actions", "action")(lambda ctx, name=None: _action(ctx, name))


@fn("body")
def _body(ctx, name):
    out = _action(ctx, name).get("outputs")
    return out.get("body") if isinstance(out, dict) and "body" in out else out


@fn("result")
def _result(ctx, name):
    return [{"name": n, **{k: v for k, v in _action(ctx, n).items() if k in ("status", "inputs", "outputs", "error", "code")}}
            for n in ctx.scopes.get(name, []) if n in ctx.actions]


@fn("items")
def _items(ctx, name):
    if name not in ctx.items:
        raise WdlError(f"items('{name}') used outside that loop")
    return ctx.items[name]


@fn("item")
def _item(ctx):
    if not ctx.item_stack:
        raise WdlError("item() used outside a loop")
    return ctx.item_stack[-1]


@fn("variables")
def _variables(ctx, name):
    if name not in ctx.variables:
        raise WdlError(f"Variable '{name}' is not initialized")
    return ctx.variables[name]


fn("parameters")(lambda ctx, name: ctx.parameters.get(name, {}))
fn("workflow")(lambda ctx: {"run": {"name": ctx.run_id}, "name": "test", "tags": {}})


# logic
def _eq(a, b):
    if isinstance(a, str) and isinstance(b, str):
        return a.lower() == b.lower()
    return a == b


fn("equals")(lambda ctx, a, b: _eq(a, b))
fn("not")(lambda ctx, a: not bool(a))
fn("greater")(lambda ctx, a, b: a > b)
fn("greaterOrEquals")(lambda ctx, a, b: a >= b)
fn("less")(lambda ctx, a, b: a < b)
fn("lessOrEquals")(lambda ctx, a, b: a <= b)


@lazy("if")
def _if(args, ctx):
    return _ev(args[1], ctx) if bool(_ev(args[0], ctx)) else _ev(args[2], ctx)


@lazy("and")
def _and(args, ctx):
    return all(bool(_ev(a, ctx)) for a in args)


@lazy("or")
def _or(args, ctx):
    return any(bool(_ev(a, ctx)) for a in args)


@fn("coalesce")
def _coalesce(ctx, *vs):
    return next((v for v in vs if v is not None), None)


@fn("empty")
def _empty(ctx, v):
    return v is None or (hasattr(v, "__len__") and len(v) == 0)


@fn("contains")
def _contains(ctx, coll, v):
    if isinstance(coll, str):
        return str(v).lower() in coll.lower()
    if isinstance(coll, dict):
        return v in coll
    return v in (coll or [])


# strings
fn("concat")(lambda ctx, *vs: "".join(to_string(v) for v in vs) if not all(isinstance(v, list) for v in vs) else sum(vs, []))
fn("substring")(lambda ctx, s, start, length=None: s[start:] if length is None else s[start:start + length])
fn("slice")(lambda ctx, s, start, end=None: s[start:end])
fn("replace")(lambda ctx, s, old, new: str(s).replace(old, new))
fn("toLower")(lambda ctx, s: str(s or "").lower())
fn("toUpper")(lambda ctx, s: str(s or "").upper())
fn("trim")(lambda ctx, s: str(s or "").strip())
fn("split")(lambda ctx, s, sep: str(s).split(sep))
fn("indexOf")(lambda ctx, s, sub: str(s).lower().find(str(sub).lower()))
fn("lastIndexOf")(lambda ctx, s, sub: str(s).lower().rfind(str(sub).lower()))
fn("startsWith")(lambda ctx, s, p: str(s).lower().startswith(str(p).lower()))
fn("endsWith")(lambda ctx, s, p: str(s).lower().endswith(str(p).lower()))
fn("length")(lambda ctx, v: len(v or []))
fn("guid")(lambda ctx, fmt=None: str(uuid.uuid4()))
fn("encodeUriComponent", "uriComponent")(lambda ctx, s: quote(to_string(s), safe=""))
fn("decodeUriComponent", "uriComponentToString")(lambda ctx, s: unquote(s))
fn("base64")(lambda ctx, s: base64.b64encode(to_string(s).encode()).decode())
fn("base64ToString")(lambda ctx, s: base64.b64decode(s).decode())
fn("nthIndexOf")(lambda ctx, s, sub, n: _nth(s, sub, n))


def _nth(s, sub, n):
    idx = -1
    for _ in range(n):
        idx = str(s).find(sub, idx + 1)
        if idx < 0:
            return -1
    return idx


# collections
fn("join")(lambda ctx, arr, sep: sep.join(to_string(v) for v in arr or []))
fn("first")(lambda ctx, v: (v[0] if v else None))
fn("last")(lambda ctx, v: (v[-1] if v else None))
fn("take")(lambda ctx, v, n: v[:n])
fn("skip")(lambda ctx, v, n: v[n:])
fn("createArray")(lambda ctx, *vs: list(vs))
fn("range")(lambda ctx, start, count: list(range(start, start + count)))
fn("union")(lambda ctx, *vs: {k: v for d in vs for k, v in d.items()} if isinstance(vs[0], dict)
            else _dedupe([x for v in vs for x in v]))
fn("intersection")(lambda ctx, a, *rest: [x for x in a if all(x in r for r in rest)])
fn("reverse")(lambda ctx, v: list(reversed(v)))
fn("sort")(lambda ctx, v, key=None: sorted(v, key=(lambda x: x.get(key)) if key else None))


def _dedupe(xs):
    out = []
    for x in xs:
        if x not in out:
            out.append(x)
    return out


# objects
@fn("setProperty", "addProperty")
def _setprop(ctx, obj, key, value):
    return {**(obj or {}), key: value}


@fn("removeProperty")
def _removeprop(ctx, obj, key):
    return {k: v for k, v in (obj or {}).items() if k != key}


# conversion
@fn("json")
def _json(ctx, v):
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (json.JSONDecodeError, TypeError) as e:
        raise WdlError(f"json(): {e}")


fn("string")(lambda ctx, v: to_string(v))
fn("int")(lambda ctx, v: int(float(v)))
fn("float")(lambda ctx, v: float(v))
fn("bool")(lambda ctx, v: v if isinstance(v, bool) else str(v).lower() in ("true", "1"))
fn("array")(lambda ctx, v: v if isinstance(v, list) else [v])
fn("isInt")(lambda ctx, v: _try(lambda: int(str(v))) is not None)
fn("isFloat")(lambda ctx, v: _try(lambda: float(str(v))) is not None)


def _try(f):
    try:
        return f()
    except (TypeError, ValueError):
        return None


# math
fn("add")(lambda ctx, a, b: a + b)
fn("sub")(lambda ctx, a, b: a - b)
fn("mul")(lambda ctx, a, b: a * b)
fn("div")(lambda ctx, a, b: a // b if isinstance(a, int) and isinstance(b, int) else a / b)
fn("mod")(lambda ctx, a, b: a % b)
fn("min")(lambda ctx, *vs: min(vs[0]) if len(vs) == 1 and isinstance(vs[0], list) else min(vs))
fn("max")(lambda ctx, *vs: max(vs[0]) if len(vs) == 1 and isinstance(vs[0], list) else max(vs))
fn("rand")(lambda ctx, a, b: random.randrange(a, b))


# dates (UTC ISO strings, like the real runtime)
def _dt(s) -> datetime:
    if isinstance(s, datetime):
        return s
    d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{d.microsecond:06d}0Z"


_DOTNET = [("yyyy", "%Y"), ("yy", "%y"), ("MMMM", "%B"), ("MMM", "%b"), ("MM", "%m"), ("dddd", "%A"), ("ddd", "%a"),
           ("dd", "%d"), ("HH", "%H"), ("hh", "%I"), ("mm", "%M"), ("ss", "%S"), ("tt", "%p"), ("fff", "%f")]


def _format(d: datetime, fmt: str | None) -> str:
    if not fmt or fmt == "o":
        return _iso(d)
    if fmt in ("d",):
        return d.strftime("%-m/%-d/%Y")
    if fmt in ("g",):
        return d.strftime("%-m/%-d/%Y %-I:%M %p")
    out = fmt
    for a, b in _DOTNET:
        out = out.replace(a, b)
    return d.strftime(out)


fn("utcNow")(lambda ctx, fmt=None: _format(datetime.now(timezone.utc), fmt))
fn("formatDateTime")(lambda ctx, s, fmt=None, *_: _format(_dt(s), fmt))
fn("addDays")(lambda ctx, s, n, fmt=None: _format(_dt(s) + timedelta(days=n), fmt))
fn("addHours")(lambda ctx, s, n, fmt=None: _format(_dt(s) + timedelta(hours=n), fmt))
fn("addMinutes")(lambda ctx, s, n, fmt=None: _format(_dt(s) + timedelta(minutes=n), fmt))
fn("addSeconds")(lambda ctx, s, n, fmt=None: _format(_dt(s) + timedelta(seconds=n), fmt))
fn("startOfDay")(lambda ctx, s, fmt=None: _format(_dt(s).replace(hour=0, minute=0, second=0, microsecond=0), fmt))
fn("convertTimeZone", "convertFromUtc", "convertToUtc")(lambda ctx, s, *args: _format(_dt(s), args[-1] if len(args) >= 2 else None))
fn("ticks")(lambda ctx, s: int((_dt(s) - datetime(1, 1, 1, tzinfo=timezone.utc)).total_seconds() * 10_000_000))
fn("dayOfWeek")(lambda ctx, s: (_dt(s).weekday() + 1) % 7)
fn("dayOfMonth")(lambda ctx, s: _dt(s).day)
