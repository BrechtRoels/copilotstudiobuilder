"""Simulated results for connector / HTTP / child-flow actions — nothing leaves the machine.

Precedence (first hit wins), each result tagged with its `source` so the UI can show it:
  1. `override`  — set in the Test flow dialog for this run (mock output or forced failure)
  2. `saved`     — the user's saved mock, `<ws>/.builder/test/mocks.json`
  3. `static`    — the action's own `runtimeConfiguration.staticResult` (Power Automate's format)
  4. `schema`    — sample built from the operation's outputType in Microsoft's vendored
                   connector reference (Office 365, Users, SharePoint, DevOps)
  5. `ai`        — PwC GenAI invents a plausible response (cached so reruns are stable)
  6. `stub`      — empty 200 response (mock-AI mode or when the LLM is unavailable)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from .. import config
from ..genai_client import GenAIError, complete_json
from ..workspace.yamlio import load_plain

log = logging.getLogger(__name__)


@dataclass
class MockResult:
    status: str               # Succeeded | Failed | TimedOut
    outputs: Any
    source: str
    error: dict | None = None


# --- storage ------------------------------------------------------------------------------------------
def _file(ws_path: str | Path) -> Path:
    return Path(ws_path) / ".builder" / "test" / "mocks.json"


def load(ws_path: str | Path) -> dict:
    f = _file(ws_path)
    if f.exists():
        try:
            data = json.loads(f.read_text())
            if isinstance(data, dict):
                return {"actions": data.get("actions") or {}, "aiCache": data.get("aiCache") or {}}
        except json.JSONDecodeError:
            log.warning("Ignoring unreadable %s", f)
    return {"actions": {}, "aiCache": {}}


def save(ws_path: str | Path, data: dict) -> dict:
    clean = {"actions": data.get("actions") or {}, "aiCache": data.get("aiCache") or {}}
    for key, m in clean["actions"].items():
        if not isinstance(m, dict) or m.get("mode") not in ("mock", "fail"):
            raise ValueError(f"Mock for '{key}' needs mode 'mock' or 'fail'")
    f = _file(ws_path)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(clean, indent=2, ensure_ascii=False))
    return clean


def mock_key(component_id: str, action_name: str) -> str:
    return f"{component_id}::{action_name}"


# --- Microsoft's connector reference ----------------------------------------------------------------
@lru_cache(maxsize=1)
def _connectors() -> dict[str, dict[str, dict]]:
    out: dict[str, dict[str, dict]] = {}
    folder = config.KNOWLEDGE_DIR / "reference" / "connectors"
    for f in sorted(folder.glob("*.yml")):
        try:
            data = load_plain(f.read_text())
        except Exception as e:  # noqa: BLE001 — a broken reference file must not break testing
            log.warning("Connector reference %s unreadable: %s", f.name, e)
            continue
        ops = {}
        for op in (data or {}).get("operations") or []:
            if isinstance(op, dict) and op.get("operationId"):
                ops[str(op["operationId"]).lower()] = op
        out[f.stem.lower()] = ops
    return out


def api_name(host: dict) -> str:
    raw = str(host.get("apiId") or host.get("connectionName") or host.get("connection", {}).get("name", "")
              if isinstance(host, dict) else "")
    return raw.rsplit("/", 1)[-1].lower()


def connector_operation(api: str, operation_id: str) -> dict | None:
    return _connectors().get(api.lower(), {}).get(str(operation_id).lower())


def _sample_string(name: str) -> str:
    n = name.lower()
    if "mail" in n or "upn" in n or "principal" in n:
        return "alex.wilber@contoso.com"
    if n in ("id", "itemid", "messageid") or n.endswith("id"):
        return "AAMkAGI2TG93AAA="
    if "name" in n:
        return "Alex Wilber"
    if "url" in n or "link" in n or "path" in n:
        return "https://contoso.sharepoint.com/sites/helpdesk/item"
    if "phone" in n or "mobile" in n:
        return "+1 425 555 0109"
    if "date" in n or "time" in n:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"Sample {name}"


def sample_from_type(t: Any, name: str = "value", depth: int = 0) -> Any:
    """Plausible value for a ConnectorDefinition type (String / Record / Table …)."""
    if depth > 5:
        return None
    if isinstance(t, str):
        k = t.lower()
        if k in ("boolean", "bool"):
            return True
        if k in ("number", "integer", "float", "decimal", "int32", "int64"):
            return 1
        if k in ("datetime", "date"):
            return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if k in ("any", "unknown", "object"):
            return {}
        return _sample_string(name)
    if isinstance(t, dict):
        kind = str(t.get("kind", "Record")).lower()
        props = t.get("properties") or {}
        rec = {p: sample_from_type((v or {}).get("type", "String") if isinstance(v, dict) else v, p, depth + 1)
               for p, v in props.items()}
        return [rec] if kind == "table" else rec
    return None


def schema_sample(api: str, operation_id: str) -> dict | None:
    op = connector_operation(api, operation_id)
    if op is None:
        return None
    out = op.get("outputType")
    body = sample_from_type(out) if out else None
    return {"statusCode": 200 if body is not None else 202, "headers": {}, "body": body}


# --- resolution ---------------------------------------------------------------------------------------
def _from_override(m: dict, source: str) -> MockResult:
    if m.get("mode") == "fail":
        status = m.get("status") if m.get("status") in ("Failed", "TimedOut") else "Failed"
        err = {"code": m.get("code") or ("ActionTimedOut" if status == "TimedOut" else "SimulatedFailure"),
               "message": m.get("error") or "Failure injected by the test (Force failure)."}
        return MockResult(status, {"statusCode": m.get("statusCode", 500), "body": {"error": err}}, source, err)
    outputs = m.get("outputs")
    if not (isinstance(outputs, dict) and ("body" in outputs or "statusCode" in outputs)):
        outputs = {"statusCode": 200, "headers": {}, "body": outputs}
    return MockResult("Succeeded", outputs, source)


def _static_result(action: dict, static_results: dict) -> MockResult | None:
    rc = action.get("runtimeConfiguration") or {}
    sr = rc.get("staticResult") if isinstance(rc, dict) else None
    if not isinstance(sr, dict) or sr.get("staticResultOptions", "Enabled") != "Enabled":
        return None
    defn = (static_results or {}).get(sr.get("name") or "")
    if not isinstance(defn, dict):
        return None
    status = defn.get("status", "Succeeded")
    err = defn.get("error") if status != "Succeeded" else None
    return MockResult(status, defn.get("outputs") or {}, "static", err)


async def connector_result(*, ws_path: str, component_id: str, name: str, action: dict, inputs: Any,
                           override: dict | None, static_results: dict, saved: dict) -> MockResult:
    if override:
        return _from_override(override, "override")
    key = mock_key(component_id, name)
    if key in saved.get("actions", {}):
        return _from_override(saved["actions"][key], "saved")
    st = _static_result(action, static_results)
    if st:
        return st
    host = (action.get("inputs") or {}).get("host") if isinstance(action.get("inputs"), dict) else None
    api = api_name(host or {})
    op_id = str((host or {}).get("operationId") or "")
    if api and op_id:
        sample = schema_sample(api, op_id)
        if sample is not None:
            return MockResult("Succeeded", sample, "schema")
    cache_key = f"{key}::{op_id or action.get('type')}"
    if cache_key in saved.get("aiCache", {}):
        return MockResult("Succeeded", saved["aiCache"][cache_key], "ai")
    if config.USE_MOCK_AI or not config.GENAI_API_KEY:
        return MockResult("Succeeded", {"statusCode": 200, "headers": {}, "body": {}}, "stub")
    try:
        gen = await complete_json(
            "Invent a realistic, small JSON response for this Power Automate action so a flow can be tested "
            "offline. Return ONLY {\"statusCode\": 200, \"body\": …}.\n\n"
            f"Action type: {action.get('type')}\nConnector: {api or '-'}\nOperation: {op_id or '-'}\n"
            f"Inputs (already evaluated): {json.dumps(inputs, default=str)[:3000]}",
            model=config.GENAI_FAST_MODEL)
    except GenAIError as e:
        log.info("AI mock unavailable for %s: %s", key, e)
        return MockResult("Succeeded", {"statusCode": 200, "headers": {}, "body": {}}, "stub")
    outputs = {"statusCode": gen.get("statusCode", 200), "headers": {}, "body": gen.get("body", gen)}
    saved.setdefault("aiCache", {})[cache_key] = outputs
    try:
        save(ws_path, saved)
    except (OSError, ValueError) as e:
        log.warning("Could not cache AI mock: %s", e)
    return MockResult("Succeeded", outputs, "ai")
