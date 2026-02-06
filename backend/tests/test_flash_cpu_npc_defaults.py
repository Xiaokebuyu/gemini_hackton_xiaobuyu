import pytest

from app.models.admin_protocol import FlashOperation, FlashRequest
from app.services.admin.flash_cpu_service import FlashCPUService


@pytest.mark.asyncio
async def test_npc_dialogue_defaults_to_secondary_tier():
    service = FlashCPUService()
    captured = {}

    async def _fake_call_tool_with_fallback(tool_name, arguments, fallback):
        captured["tool_name"] = tool_name
        captured["arguments"] = arguments
        return {"response": "ok"}

    service._call_tool_with_fallback = _fake_call_tool_with_fallback  # type: ignore[assignment]

    result = await service.execute_request(
        world_id="test_world",
        session_id="test_session",
        request=FlashRequest(
            operation=FlashOperation.NPC_DIALOGUE,
            parameters={"npc_id": "priestess", "message": "hello"},
        ),
    )

    assert result.success is True
    assert captured["tool_name"] == "npc_respond"
    assert captured["arguments"]["tier"] == "secondary"


@pytest.mark.asyncio
async def test_npc_dialogue_keeps_explicit_tier():
    service = FlashCPUService()
    captured = {}

    async def _fake_call_tool_with_fallback(tool_name, arguments, fallback):
        captured["arguments"] = arguments
        return {"response": "ok"}

    service._call_tool_with_fallback = _fake_call_tool_with_fallback  # type: ignore[assignment]

    result = await service.execute_request(
        world_id="test_world",
        session_id="test_session",
        request=FlashRequest(
            operation=FlashOperation.NPC_DIALOGUE,
            parameters={"npc_id": "priestess", "message": "hello", "tier": "main"},
        ),
    )

    assert result.success is True
    assert captured["arguments"]["tier"] == "main"
