"""GenAI client: output cap is sent, and truncated replies raise a clear error (not "invalid JSON")."""
import asyncio
import json

import httpx
import pytest

from app import config, genai_client
from app.genai_client import GenAIError


def _patch_client(monkeypatch, handler):
    real = httpx.AsyncClient
    monkeypatch.setattr(genai_client.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(config, "GENAI_API_KEY", "test-key")


def test_complete_sends_cap_and_raises_on_truncation(monkeypatch):
    sent = {}

    def handler(req):
        sent.update(json.loads(req.content))
        return httpx.Response(200, json={"status": "incomplete", "output": [
            {"type": "message", "content": [{"type": "output_text", "text": '{"files": ['}]}]})

    _patch_client(monkeypatch, handler)
    with pytest.raises(GenAIError, match="cut off at the output limit"):
        asyncio.run(genai_client.llm_complete("hi"))
    assert sent["max_output_tokens"] == config.GENAI_MAX_OUTPUT_TOKENS


def test_stream_raises_on_incomplete(monkeypatch):
    events = [{"type": "response.output_text.delta", "delta": '{"files": ['},
              {"type": "response.completed", "response": {"status": "incomplete"}}]
    body = "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"
    _patch_client(monkeypatch, lambda req: httpx.Response(200, text=body))

    async def run():
        return [d async for d in genai_client.llm_stream("hi")]

    with pytest.raises(GenAIError, match="cut off"):
        asyncio.run(run())


def test_stream_completed_passes(monkeypatch):
    events = [{"type": "response.output_text.delta", "delta": "{}"},
              {"type": "response.completed", "response": {"status": "completed"}}]
    body = "".join(f"data: {json.dumps(e)}\n\n" for e in events)
    _patch_client(monkeypatch, lambda req: httpx.Response(200, text=body))

    async def run():
        return [d async for d in genai_client.llm_stream("hi")]

    assert asyncio.run(run()) == ["{}"]
