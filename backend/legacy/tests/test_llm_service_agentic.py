import types

import pytest

from app.services.llm_service import LLMService


class _FakeAgenticModels:
    def __init__(self):
        self.calls = []
        self._count = 0

    async def generate_content(self, *, model, contents, config):
        self.calls.append(getattr(config, "cached_content", None))
        if self._count == 0:
            self._count += 1
            raise Exception(
                "400 INVALID_ARGUMENT. CachedContent can not be used with GenerateContent request "
                "setting system_instruction, tools or tool_config."
            )

        return types.SimpleNamespace(
            candidates=[
                types.SimpleNamespace(
                    content=types.SimpleNamespace(
                        parts=[types.SimpleNamespace(text="ok", thought=False)]
                    )
                )
            ],
            usage_metadata=types.SimpleNamespace(
                thoughts_token_count=0,
                candidates_token_count=1,
                total_token_count=1,
            ),
            text="ok",
        )


class _FakeForcedCallModels:
    def __init__(self):
        self.last_config = None

    async def generate_content(self, *, model, contents, config):
        self.last_config = config
        return types.SimpleNamespace(
            function_calls=[
                types.SimpleNamespace(
                    name="add_teammate",
                    args={"character_id": "npc_1", "name": "艾拉"},
                )
            ],
            candidates=[
                types.SimpleNamespace(
                    content=types.SimpleNamespace(
                        parts=[
                            types.SimpleNamespace(
                                function_call=types.SimpleNamespace(
                                    name="remove_item",
                                    args={"item_id": "potion", "quantity": 1},
                                )
                            )
                        ]
                    )
                )
            ],
        )


class _FakeFinalizeModels:
    def __init__(self):
        self.last_contents = None
        self.last_config = None

    async def generate_content(self, *, model, contents, config):
        self.last_contents = contents
        self.last_config = config
        return types.SimpleNamespace(
            candidates=[
                types.SimpleNamespace(
                    content=types.SimpleNamespace(
                        parts=[types.SimpleNamespace(text="修复后的叙述", thought=False)]
                    ),
                    finish_reason="STOP",
                )
            ],
            usage_metadata=types.SimpleNamespace(
                thoughts_token_count=2,
                candidates_token_count=8,
                total_token_count=10,
            ),
            text="修复后的叙述",
        )


@pytest.mark.asyncio
async def test_agentic_generate_raises_on_incompatible_cached_content():
    service = LLMService()
    fake_models = _FakeAgenticModels()
    service.client = types.SimpleNamespace(
        aio=types.SimpleNamespace(models=fake_models),
    )

    with pytest.raises(Exception, match="CachedContent can not be used"):
        await service.agentic_generate(
            user_prompt="玩家输入",
            system_instruction="系统指令",
            tools=[],
            cached_content="cached-content-1",
        )

    assert fake_models.calls == ["cached-content-1"]


@pytest.mark.asyncio
async def test_agentic_force_tool_calls_uses_any_mode_and_extracts_calls():
    service = LLMService()
    fake_models = _FakeForcedCallModels()
    service.client = types.SimpleNamespace(
        aio=types.SimpleNamespace(models=fake_models),
    )

    calls = await service.agentic_force_tool_calls(
        user_prompt="玩家输入",
        system_instruction="系统指令",
        tools=[],
        allowed_function_names=["add_teammate", "remove_item"],
    )

    assert [call["name"] for call in calls] == ["add_teammate", "remove_item"]
    assert calls[0]["args"]["character_id"] == "npc_1"
    assert calls[1]["args"]["item_id"] == "potion"

    cfg = fake_models.last_config
    assert cfg.automatic_function_calling.disable is True
    assert cfg.tool_config.function_calling_config.mode.value == "ANY"
    assert cfg.tool_config.function_calling_config.allowed_function_names == [
        "add_teammate",
        "remove_item",
    ]


@pytest.mark.asyncio
async def test_agentic_force_tool_calls_round_keeps_response_content():
    service = LLMService()
    fake_models = _FakeForcedCallModels()
    service.client = types.SimpleNamespace(
        aio=types.SimpleNamespace(models=fake_models),
    )

    round_payload = await service.agentic_force_tool_calls_round(
        user_prompt="玩家输入",
        system_instruction="系统指令",
        tools=[],
        allowed_function_names=["add_teammate", "remove_item"],
    )

    assert [call["name"] for call in round_payload.function_calls] == ["add_teammate", "remove_item"]
    assert round_payload.response_content is not None
    cfg = fake_models.last_config
    assert cfg.tool_config.function_calling_config.mode.value == "ANY"


@pytest.mark.asyncio
async def test_agentic_finalize_with_function_responses_returns_text():
    service = LLMService()
    fake_models = _FakeFinalizeModels()
    service.client = types.SimpleNamespace(
        aio=types.SimpleNamespace(models=fake_models),
    )

    forced_content = types.SimpleNamespace(
        parts=[
            types.SimpleNamespace(
                function_call=types.SimpleNamespace(
                    name="add_teammate",
                    args={"character_id": "ally_1"},
                )
            )
        ]
    )
    response = await service.agentic_finalize_with_function_responses(
        user_prompt="玩家输入",
        system_instruction="系统指令",
        forced_response_content=forced_content,
        function_responses=[
            {
                "name": "add_teammate",
                "response": {"result": {"success": True}},
            }
        ],
    )

    assert response.text == "修复后的叙述"
    assert len(fake_models.last_contents) == 3
    assert fake_models.last_contents[1] is forced_content
    assert fake_models.last_contents[2].role == "user"
