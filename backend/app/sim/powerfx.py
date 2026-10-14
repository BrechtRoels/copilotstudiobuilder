"""Power Fx evaluator for the subset Copilot Studio topics use.

Microsoft's engine is .NET only, so the test pane needs its own. Scope:
expressions in `condition:` / `value: =…`, `{…}` interpolation in messages,
and the functions on Copilot Studio's supported list (int-reference). Anything
outside that raises `FxError` — the simulator reports it instead of guessing.

Values: None = Blank, bool, int/float, str, datetime, dict = record,
list[dict] = table (single-column tables use the `Value` column).
"""
from __future__ import annotations

import json
import math
import random
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable


class FxError(Exception):
    pass


# --- tokenizer ------------------------------------------------------------------------------------
_TOKEN_RE = re.compile(r"""
    (?P<ws>\s+)
  | (?P<num>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)
  | (?P<istr>\$")
  | (?P<str>"(?:[^"]|"")*")
  | (?P<qid>'(?:[^']|'')*')
  | (?P<id>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op><>|<=|>=|&&|\|\||[-+*/^&=<>!(){}\[\],.:;%@])
""", re.VERBOSE)


@dataclass
class Tok:
    kind: str   # num | str | istr | id | op | eof
    val: Any
    pos: int


def _tokenize(src: str) -> list[Tok]:
    out: list[Tok] = []
    i = 0
    while i < len(src):
        if src.startswith('$"', i):
            # Interpolated string: keep the raw body; parsed later.
            j, depth, body = i + 2, 0, []
            while j < len(src):
                ch = src[j]
                if depth == 0 and ch == '"':
                    if src.startswith('""', j):
                        body.append('"'); j += 2; continue
                    break
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                body.append(ch)
                j += 1
            else:
                raise FxError("Unterminated interpolated string")
            out.append(Tok("istr", "".join(body), i))
            i = j + 1
            continue
        m = _TOKEN_RE.match(src, i)
        if not m:
            raise FxError(f"Unexpected character {src[i]!r} at {i}")
        kind = m.lastgroup
        text = m.group()
        if kind == "num":
            out.append(Tok("num", float(text) if any(c in text for c in ".eE") else int(text), i))
        elif kind == "str":
            out.append(Tok("str", text[1:-1].replace('""', '"'), i))
        elif kind == "qid":
            out.append(Tok("id", text[1:-1].replace("''", "'"), i))
        elif kind == "id":
            out.append(Tok("id", text, i))
        elif kind == "op":
            out.append(Tok("op", text, i))
        i = m.end()
    out.append(Tok("eof", None, len(src)))
    return out


# --- parser (Pratt) -------------------------------------------------------------------------------
# AST nodes are tuples: ("lit", v) ("name", n) ("member", obj, n) ("call", name, args) ("un", op, x)
# ("bin", op, a, b) ("rec", [(k, node)]) ("table", [node]) ("interp", [str | node]) ("chain", [node])

_BINARY = {
    "||": 1, "Or": 1, "&&": 2, "And": 2,
    "=": 3, "<>": 3, "<": 3, ">": 3, "<=": 3, ">=": 3, "in": 3, "exactin": 3,
    "&": 4, "+": 5, "-": 5, "*": 6, "/": 6, "^": 7,
}


class _Parser:
    def __init__(self, src: str):
        self.src = src
        self.toks = _tokenize(src)
        self.i = 0

    def peek(self) -> Tok:
        return self.toks[self.i]

    def next(self) -> Tok:
        t = self.toks[self.i]
        self.i += 1
        return t

    def expect(self, op: str) -> None:
        t = self.next()
        if t.kind != "op" or t.val != op:
            raise FxError(f"Expected '{op}' at {t.pos} in: {self.src}")

    def parse(self):
        node = self.chain()
        if self.peek().kind != "eof":
            raise FxError(f"Unexpected '{self.peek().val}' at {self.peek().pos} in: {self.src}")
        return node

    def chain(self):
        items = [self.expr(0)]
        while self.peek().kind == "op" and self.peek().val == ";":
            self.next()
            if self.peek().kind == "eof":
                break
            items.append(self.expr(0))
        return items[0] if len(items) == 1 else ("chain", items)

    def _binop(self) -> str | None:
        t = self.peek()
        if t.kind == "op" and t.val in _BINARY:
            return t.val
        if t.kind == "id" and t.val in ("And", "Or", "in", "exactin"):
            return t.val
        return None

    def expr(self, min_prec: int):
        left = self.unary()
        while True:
            op = self._binop()
            if op is None or _BINARY[op] < min_prec:
                break
            prec = _BINARY[op]
            self.next()
            right = self.expr(prec if op == "^" else prec + 1)
            left = ("bin", {"Or": "||", "And": "&&"}.get(op, op), left, right)
        return left

    def unary(self):
        t = self.peek()
        if t.kind == "op" and t.val in ("-", "!", "+"):
            self.next()
            return ("un", t.val, self.unary())
        if t.kind == "id" and t.val == "Not" and not (self.toks[self.i + 1].kind == "op" and self.toks[self.i + 1].val == "("):
            self.next()
            return ("un", "!", self.unary())
        return self.postfix(self.primary())

    def postfix(self, node):
        while True:
            t = self.peek()
            if t.kind == "op" and t.val == ".":
                self.next()
                name = self.next()
                if name.kind != "id":
                    raise FxError(f"Expected a name after '.' at {name.pos}")
                node = ("member", node, name.val)
            elif t.kind == "op" and t.val == "%":
                self.next()
                node = ("bin", "/", node, ("lit", 100))
            else:
                return node

    def primary(self):
        t = self.next()
        if t.kind == "num":
            return ("lit", t.val)
        if t.kind == "str":
            return ("lit", t.val)
        if t.kind == "istr":
            return ("interp", _parse_template(t.val))
        if t.kind == "id":
            if t.val in ("true", "false"):
                return ("lit", t.val == "true")
            nt = self.peek()
            if nt.kind == "op" and nt.val == "(":
                self.next()
                args = []
                if not (self.peek().kind == "op" and self.peek().val == ")"):
                    args.append(self.expr(0))
                    while self.peek().kind == "op" and self.peek().val == ",":
                        self.next()
                        args.append(self.expr(0))
                self.expect(")")
                return ("call", t.val, args)
            return ("name", t.val)
        if t.kind == "op":
            if t.val == "(":
                e = self.chain()
                self.expect(")")
                return e
            if t.val == "{":
                fields = []
                while not (self.peek().kind == "op" and self.peek().val == "}"):
                    k = self.next()
                    if k.kind not in ("id", "str"):
                        raise FxError(f"Expected a field name at {k.pos}")
                    self.expect(":")
                    fields.append((k.val, self.expr(0)))
                    if self.peek().kind == "op" and self.peek().val == ",":
                        self.next()
                self.expect("}")
                return ("rec", fields)
            if t.val == "[":
                items = []
                while not (self.peek().kind == "op" and self.peek().val == "]"):
                    items.append(self.expr(0))
                    if self.peek().kind == "op" and self.peek().val == ",":
                        self.next()
                self.expect("]")
                return ("table", items)
        raise FxError(f"Unexpected '{t.val}' at {t.pos} in: {self.src}")


def _parse_template(body: str) -> list:
    """`Hello {Topic.Name}!` -> ["Hello ", <ast>, "!"] (shared by $"…" and message interpolation)."""
    parts: list = []
    buf: list[str] = []
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == "{":
            depth, j, in_str = 1, i + 1, False
            while j < len(body) and depth:
                c = body[j]
                if c == '"':
                    in_str = not in_str
                elif not in_str and c == "{":
                    depth += 1
                elif not in_str and c == "}":
                    depth -= 1
                j += 1
            if depth:
                buf.append(body[i:])
                break
            inner = body[i + 1:j - 1]
            if buf:
                parts.append("".join(buf)); buf = []
            parts.append(_Parser(inner).parse() if inner.strip() else "")
            i = j
            continue
        buf.append(ch)
        i += 1
    if buf:
        parts.append("".join(buf))
    return parts


_CACHE: dict[str, Any] = {}


def parse(src: str):
    if src not in _CACHE:
        if len(_CACHE) > 4000:
            _CACHE.clear()
        _CACHE[src] = _Parser(src).parse()
    return _CACHE[src]


# --- values -----------------------------------------------------------------------------------------
def is_blank(v: Any) -> bool:
    return v is None or v == ""


def to_text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() and abs(v) < 1e15 else repr(round(v, 10)).rstrip("0").rstrip(".")
    if isinstance(v, datetime):
        return v.strftime("%-m/%-d/%Y %-I:%M %p")
    if isinstance(v, date):
        return v.strftime("%-m/%-d/%Y")
    if isinstance(v, (dict, list)):
        return json.dumps(_jsonable(v), ensure_ascii=False)
    return str(v)


def _jsonable(v: Any) -> Any:
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    return v


def to_number(v: Any) -> float | int:
    if v is None or v == "":
        return 0
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        s = v.strip().replace(",", "")
        try:
            return int(s)
        except ValueError:
            try:
                return float(s)
            except ValueError:
                raise FxError(f"Cannot convert '{v}' to a number")
    if isinstance(v, datetime):
        return v.timestamp() * 1000
    raise FxError(f"Cannot convert {type(v).__name__} to a number")


def to_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if v is None or v == "":
        return False
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        low = v.strip().lower()
        if low in ("true", "yes"):
            return True
        if low in ("false", "no"):
            return False
        raise FxError(f"Cannot convert '{v}' to a boolean")
    return True


def _num(x: float | int) -> float | int:
    if isinstance(x, float) and x.is_integer() and abs(x) < 1e15:
        return int(x)
    return x


def as_table(v: Any) -> list[dict]:
    if v is None:
        return []
    if isinstance(v, list):
        return [x if isinstance(x, dict) else {"Value": x} for x in v]
    if isinstance(v, dict):
        return [v]
    raise FxError("Expected a table")


def _parse_dt(v: Any) -> datetime | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    if isinstance(v, str):
        s = v.strip().replace("Z", "+00:00")
        for parse_fn in (datetime.fromisoformat,):
            try:
                return parse_fn(s)
            except ValueError:
                pass
        for fmt in ("%m/%d/%Y", "%m/%d/%Y %H:%M", "%m/%d/%Y %I:%M %p", "%d-%m-%Y", "%Y/%m/%d", "%d %B %Y", "%B %d, %Y"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                pass
    raise FxError(f"Cannot convert '{v}' to a date")


def _equal(a: Any, b: Any) -> bool:
    if is_blank(a) and is_blank(b):
        return True
    if is_blank(a) or is_blank(b):
        return False
    if isinstance(a, bool) or isinstance(b, bool):
        try:
            return to_bool(a) == to_bool(b)
        except FxError:
            return False
    if isinstance(a, (int, float)) or isinstance(b, (int, float)):
        try:
            return to_number(a) == to_number(b)
        except FxError:
            return False
    if isinstance(a, (datetime, date)) or isinstance(b, (datetime, date)):
        return _parse_dt(a) == _parse_dt(b)
    return a == b


def _compare(a: Any, b: Any) -> int:
    if isinstance(a, (datetime, date)) or isinstance(b, (datetime, date)):
        x, y = _parse_dt(a), _parse_dt(b)
        if x is None or y is None:
            raise FxError("Cannot compare a blank date")
        if (x.tzinfo is None) != (y.tzinfo is None):
            x, y = x.replace(tzinfo=None), y.replace(tzinfo=None)
    elif isinstance(a, str) and isinstance(b, str):
        x, y = a, b
    else:
        x, y = to_number(a), to_number(b)
    return (x > y) - (x < y)


# --- evaluator --------------------------------------------------------------------------------------
class Env:
    """Name resolution: dialog scopes (Topic/Global/System/…) plus record scopes for Filter/ForAll/With."""

    def __init__(self, roots: dict[str, Any], rows: list[dict] | None = None,
                 now: Callable[[], datetime] | None = None):
        self.roots = roots
        self.rows = rows or []
        self.now = now or (lambda: datetime.now(timezone.utc))

    def with_row(self, row: dict) -> "Env":
        return Env(self.roots, [row, *self.rows], self.now)

    def lookup(self, name: str) -> Any:
        if name == "ThisRecord" and self.rows:
            return self.rows[0]
        for row in self.rows:
            if isinstance(row, dict) and name in row:
                return row[name]
        if name in self.roots:
            return self.roots[name]
        return _Enum(name)


class _Enum(str):
    """Unknown root name (option sets / enums such as DateTimeFormat.UTC, SignInReason.SignInRequired)."""

    def member(self, n: str) -> "_Enum":
        return _Enum(n)


def evaluate(src: str, env: Env) -> Any:
    try:
        return _eval(parse(src), env)
    except FxError:
        raise
    except RecursionError:
        raise FxError("Expression too deeply nested")
    except (TypeError, ValueError, ZeroDivisionError, KeyError, IndexError, AttributeError) as e:
        raise FxError(f"{type(e).__name__}: {e}")


def _eval(n, env: Env) -> Any:
    t = n[0]
    if t == "lit":
        return n[1]
    if t == "name":
        v = env.lookup(n[1])
        if isinstance(v, _Enum) and n[1] in ("Blank",):
            return None
        return v
    if t == "member":
        obj = _eval(n[1], env)
        if isinstance(obj, _Enum):
            return obj.member(n[2])
        if obj is None:
            return None
        if isinstance(obj, dict):
            return obj.get(n[2])
        if isinstance(obj, list):  # column of a table
            return [{"Value": r.get(n[2]) if isinstance(r, dict) else None} for r in obj]
        raise FxError(f"'{n[2]}' is not a field of a {type(obj).__name__}")
    if t == "rec":
        return {k: _eval(v, env) for k, v in n[1]}
    if t == "table":
        return as_table([_eval(x, env) for x in n[1]])
    if t == "interp":
        return "".join(p if isinstance(p, str) else to_text(_eval(p, env)) for p in n[1])
    if t == "chain":
        v = None
        for x in n[1]:
            v = _eval(x, env)
        return v
    if t == "un":
        v = _eval(n[2], env)
        if n[1] == "!":
            return not to_bool(v)
        if n[1] == "-":
            return _num(-to_number(v))
        return to_number(v)
    if t == "bin":
        return _binary(n[1], n[2], n[3], env)
    if t == "call":
        return _call(n[1], n[2], env)
    raise FxError(f"Unknown node {t}")


def _binary(op: str, an, bn, env: Env) -> Any:
    if op == "&&":
        return to_bool(_eval(an, env)) and to_bool(_eval(bn, env))
    if op == "||":
        return to_bool(_eval(an, env)) or to_bool(_eval(bn, env))
    a, b = _eval(an, env), _eval(bn, env)
    if op == "=":
        return _equal(a, b)
    if op == "<>":
        return not _equal(a, b)
    if op in ("<", ">", "<=", ">="):
        c = _compare(a, b)
        return {"<": c < 0, ">": c > 0, "<=": c <= 0, ">=": c >= 0}[op]
    if op == "&":
        return to_text(a) + to_text(b)
    if op in ("in", "exactin"):
        exact = op == "exactin"
        if isinstance(b, list):
            vals = [r.get("Value") if isinstance(r, dict) and len(r) == 1 else r for r in b]
            return any((x == a) if exact else _equal(x, a) if not isinstance(x, str) else to_text(a).lower() == x.lower()
                       for x in vals)
        return (to_text(a) in to_text(b)) if exact else (to_text(a).lower() in to_text(b).lower())
    if op == "+" and isinstance(a, (datetime, date)):
        return _parse_dt(a) + timedelta(days=to_number(b))
    if op == "-" and isinstance(a, (datetime, date)) and isinstance(b, (datetime, date)):
        return _num((_parse_dt(a) - _parse_dt(b)).total_seconds() / 86400)
    x, y = to_number(a), to_number(b)
    if op == "+":
        return _num(x + y)
    if op == "-":
        return _num(x - y)
    if op == "*":
        return _num(x * y)
    if op == "/":
        if y == 0:
            raise FxError("Division by zero")
        return _num(x / y)
    if op == "^":
        return _num(x ** y)
    raise FxError(f"Unknown operator {op}")


# --- functions -------------------------------------------------------------------------------------
_LAZY: dict[str, Callable] = {}
_EAGER: dict[str, Callable] = {}


def _lazy(*names):
    def deco(fn):
        for nm in names:
            _LAZY[nm] = fn
        return fn
    return deco


def _fn(*names):
    def deco(fn):
        for nm in names:
            _EAGER[nm] = fn
        return fn
    return deco


def _call(name: str, args: list, env: Env) -> Any:
    if name in _LAZY:
        return _LAZY[name](args, env)
    if name in _EAGER:
        return _EAGER[name](*[_eval(a, env) for a in args])
    raise FxError(f"Function '{name}' is not simulated (or not supported by Copilot Studio)")


@_lazy("If")
def _if(args, env):
    i = 0
    while i + 1 < len(args):
        if to_bool(_eval(args[i], env)):
            return _eval(args[i + 1], env)
        i += 2
    return _eval(args[i], env) if i < len(args) else None


@_lazy("Switch")
def _switch(args, env):
    v = _eval(args[0], env)
    i = 1
    while i + 1 < len(args):
        if _equal(v, _eval(args[i], env)):
            return _eval(args[i + 1], env)
        i += 2
    return _eval(args[i], env) if i < len(args) else None


@_lazy("And")
def _and(args, env):
    return all(to_bool(_eval(a, env)) for a in args)


@_lazy("Or")
def _or(args, env):
    return any(to_bool(_eval(a, env)) for a in args)


@_lazy("Coalesce")
def _coalesce(args, env):
    for a in args:
        v = _eval(a, env)
        if not is_blank(v):
            return v
    return None


@_lazy("IfError")
def _iferror(args, env):
    try:
        return _eval(args[0], env)
    except FxError:
        return _eval(args[1], env) if len(args) > 1 else None


@_lazy("IsError")
def _iserror(args, env):
    try:
        _eval(args[0], env)
        return False
    except FxError:
        return True


@_lazy("IsBlankOrError")
def _isblankorerror(args, env):
    try:
        return is_blank(_eval(args[0], env))
    except FxError:
        return True


@_lazy("With")
def _with(args, env):
    rec = _eval(args[0], env)
    return _eval(args[1], env.with_row(rec if isinstance(rec, dict) else {}))


@_lazy("Filter")
def _filter(args, env):
    rows = as_table(_eval(args[0], env))
    return [r for r in rows if all(to_bool(_eval(c, env.with_row(r))) for c in args[1:])]


@_lazy("LookUp")
def _lookup(args, env):
    for r in as_table(_eval(args[0], env)):
        if to_bool(_eval(args[1], env.with_row(r))):
            return _eval(args[2], env.with_row(r)) if len(args) > 2 else r
    return None


@_lazy("CountIf")
def _countif(args, env):
    return sum(1 for r in as_table(_eval(args[0], env)) if to_bool(_eval(args[1], env.with_row(r))))


@_lazy("ForAll")
def _forall(args, env):
    return as_table([_eval(args[1], env.with_row(r)) for r in as_table(_eval(args[0], env))])


@_lazy("AddColumns")
def _addcolumns(args, env):
    rows = as_table(_eval(args[0], env))
    out = []
    for r in rows:
        nr = dict(r)
        for i in range(1, len(args) - 1, 2):
            nr[to_text(_eval(args[i], env)) if args[i][0] == "lit" else args[i][1]] = _eval(args[i + 1], env.with_row(r))
        out.append(nr)
    return out


def _agg(args, env, fn):
    if len(args) >= 2 and isinstance(_eval(args[0], env), list):
        rows = as_table(_eval(args[0], env))
        vals = [to_number(_eval(args[1], env.with_row(r))) for r in rows]
    else:
        vals = []
        for a in args:
            v = _eval(a, env)
            vals += [to_number(r.get("Value")) for r in as_table(v)] if isinstance(v, list) else [to_number(v)]
    return _num(fn(vals)) if vals else (0 if fn is sum else None)


@_lazy("Sum")
def _sum(args, env):
    return _agg(args, env, sum)


@_lazy("Average")
def _avg(args, env):
    return _agg(args, env, lambda v: sum(v) / len(v))


@_lazy("Max")
def _max(args, env):
    return _agg(args, env, max)


@_lazy("Min")
def _min(args, env):
    return _agg(args, env, min)


@_lazy("Sort")
def _sort(args, env):
    rows = as_table(_eval(args[0], env))
    desc = len(args) > 2 and str(_eval(args[2], env)).lower().endswith("descending")
    key = (lambda r: _sortkey(_eval(args[1], env.with_row(r)))) if len(args) > 1 else (lambda r: _sortkey(r.get("Value")))
    return sorted(rows, key=key, reverse=desc)


@_lazy("SortByColumns")
def _sortbycols(args, env):
    rows = as_table(_eval(args[0], env))
    col = to_text(_eval(args[1], env)) if args[1][0] == "lit" else args[1][1]
    desc = len(args) > 2 and str(_eval(args[2], env)).lower().endswith("descending")
    return sorted(rows, key=lambda r: _sortkey(r.get(col)), reverse=desc)


def _sortkey(v):
    if v is None:
        return (0, 0)
    if isinstance(v, (int, float, bool)):
        return (1, float(v))
    if isinstance(v, datetime):
        return (2, v.timestamp())
    return (3, str(v).lower())


@_lazy("Concat")
def _concat(args, env):
    rows = as_table(_eval(args[0], env))
    sep = to_text(_eval(args[2], env)) if len(args) > 2 else ""
    return sep.join(to_text(_eval(args[1], env.with_row(r))) for r in rows)


@_lazy("Trace", "Refresh")
def _noop(args, env):
    return True


@_fn("Blank")
def _blank():
    return None


@_fn("IsBlank")
def _isblank(v=None):
    return is_blank(v) or (isinstance(v, list) and not v)


@_fn("IsEmpty")
def _isempty(v=None):
    return v is None or (isinstance(v, list) and len(v) == 0)


@_fn("Not")
def _not(v):
    return not to_bool(v)


@_fn("IsNumeric")
def _isnumeric(v):
    try:
        to_number(v)
        return not is_blank(v)
    except FxError:
        return False


@_fn("Text")
def _text(v, fmt=None, *_):
    if fmt is None or isinstance(fmt, _Enum):
        if isinstance(v, (datetime, date)) and isinstance(fmt, _Enum):
            d = _parse_dt(v)
            f = str(fmt)
            if f in ("UTC", "ISO"):
                return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
            if f.startswith("ShortDate"):
                return d.strftime("%-m/%-d/%Y")
            if f.startswith("LongDate"):
                return d.strftime("%A, %B %-d, %Y")
            if f.startswith("ShortTime"):
                return d.strftime("%-I:%M %p")
        return to_text(v)
    fmt = to_text(fmt)
    if isinstance(v, (datetime, date)) or (isinstance(v, str) and re.search(r"[ymdhs]", fmt, re.I) and not re.search(r"[0#]", fmt)):
        return _fmt_date(_parse_dt(v), fmt)
    return _fmt_number(to_number(v), fmt)


def _fmt_date(d: datetime | None, fmt: str) -> str:
    if d is None:
        return ""
    out = fmt
    for token, val in (("yyyy", f"{d.year:04d}"), ("yy", f"{d.year % 100:02d}"), ("mmmm", d.strftime("%B")),
                       ("mmm", d.strftime("%b")), ("dddd", d.strftime("%A")), ("ddd", d.strftime("%a")),
                       ("dd", f"{d.day:02d}"), ("hh", f"{d.hour:02d}"), ("ss", f"{d.second:02d}")):
        out = re.sub(token, val, out, flags=re.I)
    out = re.sub(r"(?<=:)mm|mm(?=:)", f"{d.minute:02d}", out)
    out = re.sub(r"mm", f"{d.month:02d}", out, flags=re.I)
    out = re.sub(r"(?<![A-Za-z])m(?![A-Za-z])", str(d.month), out)
    out = re.sub(r"(?<![A-Za-z])d(?![A-Za-z])", str(d.day), out)
    return out


def _fmt_number(x: float, fmt: str) -> str:
    m = re.search(r"([#0,]*)(?:\.([0#]+))?", fmt)
    decimals = len(m.group(2)) if m and m.group(2) else 0
    grouping = "," in (m.group(1) if m else "")
    s = f"{x:,.{decimals}f}" if grouping else f"{x:.{decimals}f}"
    return re.sub(r"[#0,.]+", s, fmt, count=1)


@_fn("Value", "Decimal", "Float")
def _value(v, *_):
    return None if is_blank(v) else to_number(v)


@_fn("Boolean")
def _boolean(v):
    return None if is_blank(v) else to_bool(v)


@_fn("Concatenate")
def _concatenate(*vs):
    return "".join(to_text(v) for v in vs)


@_fn("Len")
def _len(v):
    return len(to_text(v))


@_fn("Lower")
def _lower(v):
    return to_text(v).lower()


@_fn("Upper")
def _upper(v):
    return to_text(v).upper()


@_fn("Proper")
def _proper(v):
    return to_text(v).title()


@_fn("Trim")
def _trim(v):
    return " ".join(to_text(v).split())


@_fn("TrimEnds")
def _trimends(v):
    return to_text(v).strip()


@_fn("Left")
def _left(v, n):
    return to_text(v)[: int(to_number(n))]


@_fn("Right")
def _right(v, n):
    n = int(to_number(n))
    return to_text(v)[-n:] if n else ""


@_fn("Mid")
def _mid(v, start, n=None):
    s = to_text(v)
    i = int(to_number(start)) - 1
    return s[i:] if n is None else s[i:i + int(to_number(n))]


@_fn("Find")
def _find(needle, hay, start=1):
    i = to_text(hay).find(to_text(needle), int(to_number(start)) - 1)
    return None if i < 0 else i + 1


@_fn("StartsWith")
def _startswith(v, p):
    return to_text(v).lower().startswith(to_text(p).lower())


@_fn("EndsWith")
def _endswith(v, p):
    return to_text(v).lower().endswith(to_text(p).lower())


@_fn("Substitute")
def _substitute(v, old, new, nth=None):
    s, old, new = to_text(v), to_text(old), to_text(new)
    if nth is None:
        return s.replace(old, new)
    idx = -1
    for _ in range(int(to_number(nth))):
        idx = s.find(old, idx + 1)
        if idx < 0:
            return s
    return s[:idx] + new + s[idx + len(old):]


@_fn("Replace")
def _replace(v, start, n, new):
    s = to_text(v)
    i = int(to_number(start)) - 1
    return s[:i] + to_text(new) + s[i + int(to_number(n)):]


@_fn("Split")
def _split(v, sep):
    s, sep = to_text(v), to_text(sep)
    return [{"Value": x} for x in (s.split(sep) if sep else list(s))]


@_fn("Char", "UniChar")
def _char(n):
    return chr(int(to_number(n)))


@_fn("EncodeUrl")
def _encodeurl(v):
    from urllib.parse import quote
    return quote(to_text(v), safe="")


@_fn("EncodeHTML")
def _encodehtml(v):
    import html
    return html.escape(to_text(v))


@_fn("PlainText")
def _plaintext(v):
    return re.sub(r"<[^>]+>", "", to_text(v))


@_fn("Search")
def _search(table, text, *cols):
    t = to_text(text).lower()
    return [r for r in as_table(table) if not t or any(t in to_text(r.get(to_text(c))).lower() for c in cols)]


_MATCH = {"Email": r"[^@\s]+@[^@\s]+\.[^@\s]+", "Digit": r"\d", "Letter": r"[A-Za-z]", "MultipleDigits": r"\d+",
          "Any": r".", "Space": r"\s", "Hyphen": r"-", "Period": r"\.", "Comma": r","}


def _pattern(p) -> str:
    return _MATCH.get(str(p), str(p)) if isinstance(p, _Enum) else to_text(p)


@_fn("IsMatch")
def _ismatch(v, pattern, options=None):
    flags = re.I if options is not None and "IgnoreCase" in str(options) else 0
    contains = options is not None and "Contains" in str(options)
    p = _pattern(pattern)
    return bool(re.search(p, to_text(v), flags) if contains else re.fullmatch(p, to_text(v), flags))


@_fn("Match")
def _match(v, pattern, *_):
    m = re.search(_pattern(pattern), to_text(v))
    return {"FullMatch": m.group(0), "StartMatch": m.start() + 1} if m else None


@_fn("MatchAll")
def _matchall(v, pattern, *_):
    return [{"FullMatch": m.group(0), "StartMatch": m.start() + 1} for m in re.finditer(_pattern(pattern), to_text(v))]


# math
for _name, _f in {"Abs": abs, "Sqrt": math.sqrt, "Exp": math.exp, "Ln": math.log, "Sin": math.sin, "Cos": math.cos,
                  "Tan": math.tan, "Asin": math.asin, "Acos": math.acos, "Atan": math.atan,
                  "Degrees": math.degrees, "Radians": math.radians}.items():
    _EAGER[_name] = (lambda f: lambda x: _num(f(to_number(x))))(_f)
_EAGER["Int"] = lambda x: math.floor(to_number(x))
_EAGER["Trunc"] = lambda x, d=0: _num(math.trunc(to_number(x) * 10 ** int(to_number(d))) / 10 ** int(to_number(d)))
_EAGER["Round"] = lambda x, d=0: _num(float(f"{to_number(x):.{int(to_number(d))}f}") if int(to_number(d)) >= 0 else round(to_number(x), int(to_number(d))))
_EAGER["RoundUp"] = lambda x, d=0: _num(math.ceil(to_number(x) * 10 ** int(to_number(d))) / 10 ** int(to_number(d)))
_EAGER["RoundDown"] = lambda x, d=0: _num(math.floor(to_number(x) * 10 ** int(to_number(d))) / 10 ** int(to_number(d)))
_EAGER["Mod"] = lambda a, b: _num(math.fmod(to_number(a), to_number(b)) if to_number(b) else 0)
_EAGER["Power"] = lambda a, b: _num(to_number(a) ** to_number(b))
_EAGER["Log"] = lambda x, base=10: _num(math.log(to_number(x), to_number(base)))
_EAGER["Pi"] = lambda: math.pi
_EAGER["Rand"] = lambda: random.random()
_EAGER["RandBetween"] = lambda a, b: random.randint(int(to_number(a)), int(to_number(b)))
_EAGER["Atan2"] = lambda x, y: math.atan2(to_number(y), to_number(x))
_EAGER["Dec2Hex"] = lambda x, places=None: format(int(to_number(x)), "X").zfill(int(to_number(places)) if places else 0)
_EAGER["Hex2Dec"] = lambda s: int(to_text(s), 16)


# tables
@_fn("Table")
def _table(*rows):
    return [r for r in rows if isinstance(r, dict)]


@_fn("CountRows")
def _countrows(t):
    return len(as_table(t))


@_fn("Count", "CountA")
def _count(t):
    return sum(1 for r in as_table(t) if not is_blank(r.get("Value")))


@_fn("First")
def _first(t):
    rows = as_table(t)
    return rows[0] if rows else None


@_fn("Last")
def _last(t):
    rows = as_table(t)
    return rows[-1] if rows else None


@_fn("FirstN")
def _firstn(t, n=1):
    return as_table(t)[: int(to_number(n))]


@_fn("LastN")
def _lastn(t, n=1):
    n = int(to_number(n))
    return as_table(t)[-n:] if n else []


@_fn("Index")
def _index(t, i):
    rows = as_table(t)
    i = int(to_number(i))
    if not 1 <= i <= len(rows):
        raise FxError(f"Index {i} out of range")
    return rows[i - 1]


@_fn("Distinct")
def _distinct(t, *_):
    seen, out = [], []
    for r in as_table(t):
        v = r.get("Value") if len(r) == 1 else r
        if v not in seen:
            seen.append(v)
            out.append({"Value": v})
    return out


@_fn("Sequence")
def _sequence(n, start=1, step=1):
    return [{"Value": _num(to_number(start) + i * to_number(step))} for i in range(int(to_number(n)))]


@_fn("Shuffle")
def _shuffle(t):
    rows = list(as_table(t))
    random.shuffle(rows)
    return rows


@_fn("ShowColumns")
def _showcols(t, *cols):
    names = [to_text(c) for c in cols]
    return [{k: r.get(k) for k in names} for r in as_table(t)]


@_fn("DropColumns")
def _dropcols(t, *cols):
    names = {to_text(c) for c in cols}
    return [{k: v for k, v in r.items() if k not in names} for r in as_table(t)]


@_fn("RenameColumns")
def _renamecols(t, *pairs):
    m = {to_text(pairs[i]): to_text(pairs[i + 1]) for i in range(0, len(pairs) - 1, 2)}
    return [{m.get(k, k): v for k, v in r.items()} for r in as_table(t)]


@_fn("ColumnNames")
def _colnames(rec):
    return [{"Value": k} for k in (rec or {})] if isinstance(rec, dict) else []


@_fn("Column")
def _column(rec, name):
    return (rec or {}).get(to_text(name)) if isinstance(rec, dict) else None


@_fn("Patch")
def _patch(base, *changes):
    if isinstance(base, dict):
        out = dict(base)
        for c in changes:
            if isinstance(c, dict):
                out.update(c)
        return out
    raise FxError("Patch on data sources is not simulated; only Patch(record, changes) is supported")


# dates
@_lazy("Now")
def _now(args, env):
    return env.now().astimezone().replace(tzinfo=None)


@_lazy("Today")
def _today(args, env):
    n = env.now().astimezone()
    return datetime(n.year, n.month, n.day)


@_lazy("IsToday")
def _istoday(args, env):
    d = _parse_dt(_eval(args[0], env))
    n = env.now().astimezone()
    return d is not None and d.date() == n.date()


_UNIT = {"milliseconds": 0.001, "seconds": 1, "minutes": 60, "hours": 3600, "days": 86400}


def _unit(u) -> str:
    return (str(u) if u is not None else "Days").lower()


@_fn("DateAdd")
def _dateadd(d, n, units=None):
    d0, n0, u = _parse_dt(d), to_number(n), _unit(units)
    if u in ("months", "quarters", "years"):
        months = int(n0) * {"months": 1, "quarters": 3, "years": 12}[u]
        y, m = divmod(d0.month - 1 + months, 12)
        import calendar
        day = min(d0.day, calendar.monthrange(d0.year + y, m + 1)[1])
        return d0.replace(year=d0.year + y, month=m + 1, day=day)
    return d0 + timedelta(seconds=n0 * _UNIT.get(u, 86400))


@_fn("DateDiff")
def _datediff(a, b, units=None):
    a0, b0, u = _parse_dt(a), _parse_dt(b), _unit(units)
    if (a0.tzinfo is None) != (b0.tzinfo is None):
        a0, b0 = a0.replace(tzinfo=None), b0.replace(tzinfo=None)
    if u == "months":
        return (b0.year - a0.year) * 12 + b0.month - a0.month
    if u == "quarters":
        return ((b0.year - a0.year) * 12 + b0.month - a0.month) // 3
    if u == "years":
        return b0.year - a0.year
    if u == "days":
        return (b0.date() - a0.date()).days
    return int((b0 - a0).total_seconds() // _UNIT.get(u, 86400))


@_fn("Date")
def _date(y, m, d):
    return datetime(int(to_number(y)), int(to_number(m)), int(to_number(d)))


@_fn("DateTime")
def _datetime(y, mo, d, h=0, mi=0, s=0, *_):
    return datetime(*(int(to_number(x)) for x in (y, mo, d, h, mi, s)))


@_fn("Time")
def _time(h, m, s=0, *_):
    return datetime(1970, 1, 1, int(to_number(h)), int(to_number(m)), int(to_number(s)))


@_fn("DateValue", "DateTimeValue", "TimeValue")
def _datevalue(v, *_):
    return _parse_dt(v)


@_fn("EDate")
def _edate(d, months):
    return _dateadd(d, months, _Enum("Months"))


@_fn("EOMonth")
def _eomonth(d, months=0):
    import calendar
    x = _dateadd(d, months, _Enum("Months"))
    return x.replace(day=calendar.monthrange(x.year, x.month)[1])


_EAGER["Year"] = lambda d: _parse_dt(d).year
_EAGER["Month"] = lambda d: _parse_dt(d).month
_EAGER["Day"] = lambda d: _parse_dt(d).day
_EAGER["Hour"] = lambda d: _parse_dt(d).hour
_EAGER["Minute"] = lambda d: _parse_dt(d).minute
_EAGER["Second"] = lambda d: _parse_dt(d).second
_EAGER["Weekday"] = lambda d, *_: (_parse_dt(d).weekday() + 1) % 7 + 1
_EAGER["WeekNum"] = lambda d, *_: int(_parse_dt(d).strftime("%U")) + 1
_EAGER["TimeZoneOffset"] = lambda *_: 0


# conversion / misc
@_fn("JSON")
def _json(v, *_):
    return json.dumps(_jsonable(v), ensure_ascii=False)


@_fn("ParseJSON")
def _parsejson(v):
    try:
        return json.loads(to_text(v))
    except json.JSONDecodeError as e:
        raise FxError(f"ParseJSON: {e}")


@_fn("GUID")
def _guid(v=None):
    return to_text(v) if v else str(uuid.uuid4())


@_fn("Language")
def _language():
    return "en-US"


@_fn("Error")
def _error(info=None):
    msg = info.get("Message") if isinstance(info, dict) else to_text(info)
    raise FxError(msg or "Error()")


@_fn("AsType", "IsType")
def _astype(v, *_):
    return v


def supported_functions() -> set[str]:
    return set(_LAZY) | set(_EAGER)


# --- helpers used by the runtime ---------------------------------------------------------------------
def eval_value(v: Any, env: Env) -> Any:
    """YAML value -> runtime value: `=expr` is Power Fx, other strings are literals (with {…} templates)."""
    if isinstance(v, str):
        if v.startswith("="):
            return evaluate(v[1:], env)
        return interpolate(v, env) if "{" in v else v
    if isinstance(v, dict):
        return {k: eval_value(x, env) for k, x in v.items()}
    if isinstance(v, list):
        return [eval_value(x, env) for x in v]
    return v


def interpolate(text: str, env: Env) -> str:
    """Message text: `Hello {Topic.Name}` — braces hold Power Fx (no leading '=')."""
    if "{" not in text:
        return text
    try:
        parts = _parse_template(text)
    except FxError:
        return text
    return "".join(p if isinstance(p, str) else to_text(_eval(p, env)) for p in parts)
