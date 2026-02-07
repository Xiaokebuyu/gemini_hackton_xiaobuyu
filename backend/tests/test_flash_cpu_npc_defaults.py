import pytest
from unittest.mock import AsyncMock

from app.models.admin_protocol import FlashOperation, FlashRequest
from app.services.admin.flash_cpu_service import FlashCPUService


@pytest.mark.asyncio
async def test_npc_dialogue_calls_flash_direct_generation():
    service = FlashCPUService()
    service.llm_service = AsyncMock()
    service.llm_service.generate_simple = AsyncMock(return_value="ok")

    result = await service.execute_request(
        world_id="test_world",
        session_id="test_session",
        request=FlashRequest(
            operation=FlashOperation.NPC_DIALOGUE,
            parameters={"npc_id": "priestess", "message": "hello"},
        ),
    )

    assert result.success is True
    service.llm_service.generate_simple.assert_awaited_once()


@pytest.mark.asyncio
async def test_npc_dialogue_direct_prompt_contains_message():
    service = FlashCPUService()
    service.llm_service = AsyncMock()
    service.llm_service.generate_simple = AsyncMock(return_value="ok")

    result = await service.execute_request(
        world_id="test_world",
        session_id="test_session",
        request=FlashRequest(
            operation=FlashOperation.NPC_DIALOGUE,
            parameters={"npc_id": "priestess", "message": "hello", "tier": "main"},
        ),
    )

    assert result.success is True
    called_prompt = service.llm_service.generate_simple.await_args.args[0]
    assert "hello" in called_prompt
