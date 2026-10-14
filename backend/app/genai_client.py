"""PwC GenAI Shared Service client (LLM only).

Adapted from Documents/podcast/backend/app/genai_client.py. Quirks:
  * Auth via the `api-key` header, never `Authorization: Bearer`.
  * Retry 3x on 5xx / transient network errors only, never on 4xx.
  * The LLM endpoint answers in one of three shapes; `_extract_text` handles all.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re

import httpx

from . import config

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_BACKOFF = [1, 3, 8]


class GenAIError(RuntimeError):
    pass


def _params() -> dict:
    return {"api-version": config.GENAI_API_VERSION} if config.GENAI_API_VERSION else {}


def _headers() -> dict:
    return {"api-key": config.GENAI_API_KEY, "Content-Type": "application/json"}


def _extract_text(data: dict) -> str:
    """Extract text from any of the three LLM response shapes."""
    if isinstance(data, dict):
        if "output" in data:
            for item in data["output"]:
                if item.get("type") == "message":
                    for c in item.get("content", []):
                        if c.get("type") == "output_text":
                            return c["text"]
        if "choices" in data:
            return data["choices"][0]["message"]["content"]
        if "response" in data:
            return data["response"]
    return str(data)


async def llm_complete(prompt: str, model: str | None = None, system: str | None = None) -> str:
    if not config.GENAI_API_KEY:
        raise GenAIError("GENAI_API_KEY is not set. Add it to .env or set USE_MOCK_AI=true.")
    model = model or config.GENAI_LLM_MODEL
    payload: dict = {"model": model, "input": prompt, "max_output_tokens": config.GENAI_MAX_OUTPUT_TOKENS}
    if system:
        payload["instructions"] = system

    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=30)) as client:
                resp = await client.post(
                    f"{config.GENAI_BASE_URL}/v1/responses",
                    params=_params(),
                    headers=_headers(),
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                usage = data.get("usage", {}) if isinstance(data, dict) else {}
                logger.info("LLM %s usage=%s", model, usage)
                if isinstance(data, dict) and data.get("status") == "incomplete":
                    raise GenAIError(_truncated_msg())
                return _extract_text(data)
        except (httpx.TimeoutException, httpx.ReadError, httpx.WriteError,
                httpx.ConnectError, OSError) as e:
            last_error = e
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(RETRY_BACKOFF[attempt])
        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500 and attempt < MAX_RETRIES - 1:
                last_error = e
                await asyncio.sleep(RETRY_BACKOFF[attempt])
            else:
                raise GenAIError(f"GenAI {e.response.status_code}: {e.response.text[:500]}") from e
    assert last_error is not None
    raise GenAIError(f"GenAI request failed: {last_error}") from last_error


async def llm_stream(prompt: str, model: str | None = None, system: str | None = None):
    """Yield text deltas (`stream: true`). Retries only while nothing has been yielded yet."""
    if not config.GENAI_API_KEY:
        raise GenAIError("GENAI_API_KEY is not set. Add it to .env or set USE_MOCK_AI=true.")
    model = model or config.GENAI_LLM_MODEL
    payload: dict = {"model": model, "input": prompt, "stream": True,
                     "max_output_tokens": config.GENAI_MAX_OUTPUT_TOKENS}
    if system:
        payload["instructions"] = system
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        yielded = False
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(180, read=120)) as client:
                async with client.stream("POST", f"{config.GENAI_BASE_URL}/v1/responses", params=_params(),
                                         headers=_headers(), json=payload) as resp:
                    if resp.status_code >= 400:
                        body = (await resp.aread()).decode(errors="replace")
                        if resp.status_code >= 500 and attempt < MAX_RETRIES - 1:
                            last_error = GenAIError(f"GenAI {resp.status_code}")
                            await asyncio.sleep(RETRY_BACKOFF[attempt])
                            continue
                        raise GenAIError(f"GenAI {resp.status_code}: {body[:500]}")
                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data = line[6:]
                        if data.strip() == "[DONE]":
                            return
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        if _is_incomplete(chunk):
                            raise GenAIError(_truncated_msg())
                        delta = _delta(chunk)
                        if delta:
                            yielded = True
                            yield delta
                    return
        except (httpx.TimeoutException, httpx.ReadError, httpx.WriteError, httpx.ConnectError, OSError) as e:
            if yielded:
                raise GenAIError(f"GenAI stream interrupted: {e}") from e
            last_error = e
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(RETRY_BACKOFF[attempt])
    raise GenAIError(f"GenAI request failed: {last_error}")


def _truncated_msg() -> str:
    return (f"The model's reply was cut off at the output limit ({config.GENAI_MAX_OUTPUT_TOKENS} tokens). "
            "Raise GENAI_MAX_OUTPUT_TOKENS in .env, or ask for the change in smaller steps.")


def _is_incomplete(chunk: dict) -> bool:
    """True when the service ended the stream early (e.g. hit max_output_tokens)."""
    if not isinstance(chunk, dict):
        return False
    resp = chunk.get("response")
    return chunk.get("type") == "response.incomplete" or (
        isinstance(resp, dict) and resp.get("status") == "incomplete")


def _delta(chunk: dict) -> str:
    """Text delta from any of the streaming event shapes."""
    if not isinstance(chunk, dict):
        return ""
    if chunk.get("type") in ("response.completed", "response.output_text.done"):
        return ""
    d = chunk.get("delta")
    if isinstance(d, str):
        return d
    if "choices" in chunk:
        return (chunk["choices"][0].get("delta") or {}).get("content") or ""
    return ""


# --- Strict-JSON prompting ---------------------------------------------------
def extract_json(text: str) -> dict | None:
    """Pull a single JSON object out of an LLM reply (strips ``` fences)."""
    if not text:
        return None
    text = re.sub(r"^```[a-zA-Z]*\n?|```$", "", text.strip()).strip()
    start, depth, in_str, esc = text.find("{"), 0, False, False
    if start == -1:
        return None
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


async def complete_json(prompt: str, model: str | None = None, system: str | None = None) -> dict:
    """LLM call that must return a JSON object; one corrective retry on failure."""
    raw = await llm_complete(prompt, model=model, system=system)
    data = extract_json(raw)
    if data is not None:
        return data
    fixed = await llm_complete(
        prompt + "\n\nYour previous reply was not valid JSON. Return ONLY a valid JSON object, nothing else.",
        model=model, system=system,
    )
    data = extract_json(fixed)
    if data is None:
        raise GenAIError("Model did not return valid JSON")
    return data
