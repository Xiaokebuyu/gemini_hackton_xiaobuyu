from __future__ import annotations

import asyncio
from types import SimpleNamespace

from google.genai import types

from app.llm_gemini import GeminiLlmAdapter


def test_generate_sets_low_thinking(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _FakeModels:
        async def generate_content(self, *, model, contents, config):
            captured["model"] = model
            captured["contents"] = contents
            captured["config"] = config
            part = SimpleNamespace(function_call=None, text="done")
            candidate = SimpleNamespace(content=SimpleNamespace(parts=[part]))
            return SimpleNamespace(candidates=[candidate])

    class _FakeClient:
        def __init__(self):
            self.aio = SimpleNamespace(models=_FakeModels())

    monkeypatch.setattr("app.llm_gemini.genai.Client", _FakeClient)
    adapter = GeminiLlmAdapter()
    response = asyncio.run(adapter.generate("system", [{"role": "user", "parts": [{"text": "hi"}]}], []))

    assert response.text == "done"
    config = captured["config"]
    assert isinstance(config, types.GenerateContentConfig)
    assert config.thinking_config is not None
    assert config.thinking_config.thinking_level == types.ThinkingLevel.LOW


def test_generate_stream_sets_low_thinking(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def _chunk_iter():
        yield SimpleNamespace(text="alpha")
        yield SimpleNamespace(text="beta")

    class _FakeModels:
        async def generate_content_stream(self, *, model, contents, config):
            captured["model"] = model
            captured["contents"] = contents
            captured["config"] = config
            return _chunk_iter()

    class _FakeClient:
        def __init__(self):
            self.aio = SimpleNamespace(models=_FakeModels())

    monkeypatch.setattr("app.llm_gemini.genai.Client", _FakeClient)
    adapter = GeminiLlmAdapter()
    chunks = asyncio.run(_collect_chunks(adapter))

    assert chunks == ["alpha", "beta"]
    config = captured["config"]
    assert isinstance(config, types.GenerateContentConfig)
    assert config.thinking_config is not None
    assert config.thinking_config.thinking_level == types.ThinkingLevel.LOW
    assert config.tool_config is not None
    assert config.tool_config.function_calling_config.mode == "NONE"


async def _collect_chunks(adapter: GeminiLlmAdapter) -> list[str]:
    chunks: list[str] = []
    async for chunk in adapter.generate_stream(
        "system",
        [{"role": "user", "parts": [{"text": "hi"}]}],
        [],
    ):
        chunks.append(chunk)
    return chunks
