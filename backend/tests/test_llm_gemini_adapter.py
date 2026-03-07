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
            usage = SimpleNamespace(
                prompt_token_count=11,
                candidates_token_count=7,
                tool_use_prompt_token_count=0,
                thoughts_token_count=3,
                total_token_count=21,
            )
            return SimpleNamespace(candidates=[candidate], usage_metadata=usage)

    class _FakeClient:
        def __init__(self):
            self.aio = SimpleNamespace(models=_FakeModels())

    monkeypatch.setattr("app.llm_gemini.genai.Client", _FakeClient)
    adapter = GeminiLlmAdapter()
    response = asyncio.run(adapter.generate("system", [{"role": "user", "parts": [{"text": "hi"}]}], []))

    assert response.text == "done"
    assert response.metadata["provider"] == "gemini"
    assert response.metadata["profile"] == "default"
    assert response.metadata["thinking_level"] == "low"
    assert response.metadata["token_usage"]["total_token_count"] == 21
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


def test_generate_supports_medium_thinking_profile(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _FakeModels:
        async def generate_content(self, *, model, contents, config):
            captured["config"] = config
            part = SimpleNamespace(function_call=None, text="done")
            candidate = SimpleNamespace(content=SimpleNamespace(parts=[part]))
            return SimpleNamespace(candidates=[candidate], usage_metadata=None)

    class _FakeClient:
        def __init__(self):
            self.aio = SimpleNamespace(models=_FakeModels())

    monkeypatch.setattr("app.llm_gemini.genai.Client", _FakeClient)
    adapter = GeminiLlmAdapter(thinking_level="medium", profile_name="osiris")
    response = asyncio.run(
        adapter.generate("system", [{"role": "user", "parts": [{"text": "hi"}]}], [])
    )

    assert response.metadata["profile"] == "osiris"
    assert response.metadata["thinking_level"] == "medium"
    config = captured["config"]
    assert isinstance(config, types.GenerateContentConfig)
    assert config.thinking_config is not None
    assert config.thinking_config.thinking_level == types.ThinkingLevel.MEDIUM


async def _collect_chunks(adapter: GeminiLlmAdapter) -> list[str]:
    chunks: list[str] = []
    async for chunk in adapter.generate_stream(
        "system",
        [{"role": "user", "parts": [{"text": "hi"}]}],
        [],
    ):
        chunks.append(chunk)
    return chunks
