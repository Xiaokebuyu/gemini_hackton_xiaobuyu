import types
from unittest.mock import AsyncMock

import pytest

from app.models.admin_protocol import FlashOperation, FlashResponse
from app.services.admin.flash_cpu_service import FlashCPUService
from app.services.llm_service import LLMResponse, ThinkingMetadata


@pytest.mark.asyncio
async def test_agentic_process_disables_request_cached_content_for_tools():

    service = FlashCPUService()
    fake_response = LLMResponse(
        text="测试叙述",
        thinking=ThinkingMetadata(
            thinking_enabled=True,
            thinking_level="high",
            thoughts_summary="",
            thoughts_token_count=1,
            output_token_count=2,
            total_token_count=3,
        ),
        raw_response=types.SimpleNamespace(
            candidates=[types.SimpleNamespace(finish_reason="STOP")]
        ),
    )
    mock_agentic_generate = AsyncMock(return_value=fake_response)
    service.llm_service = types.SimpleNamespace(agentic_generate=mock_agentic_generate)

    context = {
        "player_character_summary": "玩家",
        "world_background": "世界背景",
        "character_roster": "",
        "available_destinations": [],
        "sub_locations": [],
        "state": "exploring",
        "location": {"location_id": "town_square", "location_name": "城镇广场"},
        "time": {"day": 1, "hour": 8, "minute": 0, "formatted": "第1天 08:00"},
        "chapter_info": {},
        "conversation_history": "",
        "memory_summary": "",
        "story_directives": [],
        "pending_flash_conditions": [],
    }

    result = await service.agentic_process(
        world_id="test_world",
        session_id="test_session",
        player_input="观察周围",
        context=context,
    )

    call_kwargs = mock_agentic_generate.await_args.kwargs
    assert call_kwargs["cached_content"] is None
    assert result.usage["cache_mode"] == "disabled"
    assert result.narration == "测试叙述"


@pytest.mark.asyncio
async def test_run_required_tool_repair_executes_forced_tool_calls():
    service = FlashCPUService()
    service.llm_service = types.SimpleNamespace(
        agentic_force_tool_calls=AsyncMock(
            return_value=[
                {
                    "name": "add_teammate",
                    "args": {
                        "character_id": "ally_1",
                        "name": "艾拉",
                        "role": "support",
                        "personality": "冷静",
                        "response_tendency": 0.6,
                    },
                }
            ]
        )
    )
    service.execute_request = AsyncMock(
        return_value=FlashResponse(
            success=True,
            operation=FlashOperation.ADD_TEAMMATE,
            result={"summary": "艾拉加入了队伍"},
        )
    )

    context = {
        "player_character_summary": "玩家",
        "world_background": "世界背景",
        "character_roster": "",
        "available_destinations": [],
        "sub_locations": [],
        "state": "exploring",
        "location": {"location_id": "town_square", "location_name": "城镇广场"},
        "time": {"day": 1, "hour": 8, "minute": 0, "formatted": "第1天 08:00"},
        "chapter_info": {},
        "conversation_history": "",
        "memory_summary": "",
        "story_directives": [],
        "pending_flash_conditions": [],
    }

    result = await service.run_required_tool_repair(
        world_id="test_world",
        session_id="test_session",
        player_input="让艾拉加入队伍",
        context=context,
        missing_requirements=["add_teammate"],
        repair_tool_names=["add_teammate"],
        enforcement_reason="missing required tools",
    )

    service.llm_service.agentic_force_tool_calls.assert_awaited_once()
    assert result.usage["repair_attempted"] is True
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "add_teammate"
    assert result.flash_results[0].operation == FlashOperation.ADD_TEAMMATE


@pytest.mark.asyncio
async def test_run_required_tool_repair_finalizes_narration_with_function_response():
    service = FlashCPUService()
    force_round = AsyncMock(
        return_value=types.SimpleNamespace(
            function_calls=[
                {
                    "name": "add_teammate",
                    "args": {
                        "character_id": "ally_1",
                        "name": "艾拉",
                        "role": "support",
                        "personality": "冷静",
                        "response_tendency": 0.6,
                    },
                }
            ],
            response_content=types.SimpleNamespace(parts=[]),
        )
    )
    finalize_round = AsyncMock(
        return_value=LLMResponse(
            text="艾拉点头加入了你的队伍。",
            thinking=ThinkingMetadata(
                thinking_enabled=True,
                thinking_level="high",
                thoughts_summary="",
                thoughts_token_count=2,
                output_token_count=9,
                total_token_count=11,
            ),
            raw_response=types.SimpleNamespace(
                candidates=[types.SimpleNamespace(finish_reason="STOP")]
            ),
        )
    )
    service.llm_service = types.SimpleNamespace(
        agentic_force_tool_calls_round=force_round,
        agentic_finalize_with_function_responses=finalize_round,
    )
    service.execute_request = AsyncMock(
        return_value=FlashResponse(
            success=True,
            operation=FlashOperation.ADD_TEAMMATE,
            result={"summary": "艾拉加入了队伍"},
        )
    )

    context = {
        "player_character_summary": "玩家",
        "world_background": "世界背景",
        "character_roster": "",
        "available_destinations": [],
        "sub_locations": [],
        "state": "exploring",
        "location": {"location_id": "town_square", "location_name": "城镇广场"},
        "time": {"day": 1, "hour": 8, "minute": 0, "formatted": "第1天 08:00"},
        "chapter_info": {},
        "conversation_history": "",
        "memory_summary": "",
        "story_directives": [],
        "pending_flash_conditions": [],
    }

    result = await service.run_required_tool_repair(
        world_id="test_world",
        session_id="test_session",
        player_input="让艾拉加入队伍",
        context=context,
        missing_requirements=["add_teammate"],
        repair_tool_names=["add_teammate"],
        enforcement_reason="missing required tools",
    )

    force_round.assert_awaited_once()
    finalize_round.assert_awaited_once()
    assert result.narration == "艾拉点头加入了你的队伍。"
    assert result.usage["finalize_status"] == "ok"
    assert result.finish_reason == "STOP"
