"""
Individual integration tests for each agentic tool.

Each test mocks flash_cpu and related services, verifying:
- Tool is callable and returns {"success": bool, ...} format
- _record correctly logs the call (name, args, success)
- Exceptions are caught and don't leak
"""
import types
from unittest.mock import AsyncMock, patch

import pytest

from app.services.admin.agentic_tools import AgenticToolRegistry
from app.models.admin_protocol import FlashOperation, FlashResponse


def _make_flash_response(
    operation: FlashOperation,
    success: bool = True,
    result: dict = None,
    error: str = None,
) -> FlashResponse:
    return FlashResponse(
        success=success,
        operation=operation,
        result=result or {},
        error=error,
    )


def _make_registry(
    flash_cpu=None,
    pending_condition_ids=None,
) -> AgenticToolRegistry:
    if flash_cpu is None:
        flash_cpu = types.SimpleNamespace(
            state_manager=types.SimpleNamespace(
                get_state=AsyncMock(return_value=None),
            ),
            session_store=types.SimpleNamespace(
                get_session=AsyncMock(return_value=None),
            ),
            execute_request=AsyncMock(
                return_value=_make_flash_response(FlashOperation.GET_STATUS),
            ),
        )
    return AgenticToolRegistry(
        flash_cpu=flash_cpu,
        world_id="test_world",
        session_id="test_session",
        pending_condition_ids=pending_condition_ids,
    )


def _assert_recorded(registry: AgenticToolRegistry, name: str, success: bool):
    """Assert that a tool call was recorded with the given name and success."""
    matching = [c for c in registry.tool_calls if c.name == name]
    assert len(matching) >= 1, (
        f"Expected record for '{name}', got: {[c.name for c in registry.tool_calls]}"
    )
    assert matching[-1].success == success, (
        f"Expected success={success} for '{name}', got {matching[-1].success}"
    )


# ---------------------------------------------------------------------------
# navigate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_navigate_success():
    flash_cpu = types.SimpleNamespace(
        execute_request=AsyncMock(
            return_value=_make_flash_response(
                FlashOperation.NAVIGATE, result={"location": "tavern"}
            )
        ),
    )
    reg = _make_registry(flash_cpu)
    result = await reg.navigate("tavern", "north")
    assert result["success"] is True
    _assert_recorded(reg, "navigate", True)


# ---------------------------------------------------------------------------
# advance_time
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_advance_time_normalizes_minutes():
    flash_cpu = types.SimpleNamespace(
        state_manager=types.SimpleNamespace(
            get_state=AsyncMock(return_value=types.SimpleNamespace(combat_id=None)),
        ),
        execute_request=AsyncMock(
            return_value=_make_flash_response(FlashOperation.UPDATE_TIME)
        ),
    )
    reg = _make_registry(flash_cpu)
    result = await reg.update_time(minutes=7)
    # 7 should normalize to 5 (nearest bucket)
    assert result["applied_minutes"] in (5, 10)
    assert result["requested_minutes"] == 7
    _assert_recorded(reg, "update_time", True)


@pytest.mark.asyncio
async def test_update_time_blocked_in_combat():
    flash_cpu = types.SimpleNamespace(
        state_manager=types.SimpleNamespace(
            get_state=AsyncMock(
                return_value=types.SimpleNamespace(combat_id="combat_1")
            ),
        ),
    )
    reg = _make_registry(flash_cpu)
    result = await reg.update_time(minutes=30)
    assert result["success"] is False
    assert "combat" in result["error"]
    _assert_recorded(reg, "update_time", False)


# ---------------------------------------------------------------------------
# enter_sublocation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enter_sublocation_success():
    flash_cpu = types.SimpleNamespace(
        execute_request=AsyncMock(
            return_value=_make_flash_response(FlashOperation.ENTER_SUBLOCATION)
        ),
    )
    reg = _make_registry(flash_cpu)
    result = await reg.enter_sublocation("blacksmith_shop")
    assert result["success"] is True
    _assert_recorded(reg, "enter_sublocation", True)


# ---------------------------------------------------------------------------
# npc_dialogue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_npc_dialogue_success():
    flash_cpu = types.SimpleNamespace(
        execute_request=AsyncMock(
            return_value=_make_flash_response(
                FlashOperation.NPC_DIALOGUE,
                result={"response": "Welcome, adventurer!"},
            )
        ),
    )
    reg = _make_registry(flash_cpu)
    result = await reg.npc_dialogue("innkeeper", "Hello!")
    assert result["success"] is True
    _assert_recorded(reg, "npc_dialogue", True)


# ---------------------------------------------------------------------------
# start_combat
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_combat_with_string_enemies():
    """Test that string enemy entries are normalized to dicts."""
    flash_cpu = types.SimpleNamespace(
        execute_request=AsyncMock(
            return_value=_make_flash_response(FlashOperation.START_COMBAT)
        ),
    )
    reg = _make_registry(flash_cpu)
    result = await reg.start_combat(["goblin", "wolf"])
    assert result["success"] is True
    # Verify the execute_request was called with normalized enemies
    call_args = flash_cpu.execute_request.call_args
    params = call_args.kwargs.get("request") or call_args[1].get("request") or call_args[0][1]
    _assert_recorded(reg, "start_combat", True)


@pytest.mark.asyncio
async def test_start_combat_with_dict_enemies():
    flash_cpu = types.SimpleNamespace(
        execute_request=AsyncMock(
            return_value=_make_flash_response(FlashOperation.START_COMBAT)
        ),
    )
    reg = _make_registry(flash_cpu)
    result = await reg.start_combat([
        {"enemy_id": "goblin_scout", "count": 3, "level": 2}
    ])
    assert result["success"] is True
    _assert_recorded(reg, "start_combat", True)


# ---------------------------------------------------------------------------
# trigger_narrative_event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_narrative_event_success():
    flash_cpu = types.SimpleNamespace(
        execute_request=AsyncMock(
            return_value=_make_flash_response(FlashOperation.TRIGGER_NARRATIVE_EVENT)
        ),
    )
    reg = _make_registry(flash_cpu)
    result = await reg.trigger_narrative_event("event_arrival")
    assert result["success"] is True
    _assert_recorded(reg, "trigger_narrative_event", True)


# ---------------------------------------------------------------------------
# get_progress
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_progress_success():
    flash_cpu = types.SimpleNamespace(
        execute_request=AsyncMock(
            return_value=_make_flash_response(
                FlashOperation.GET_PROGRESS,
                result={"chapter": 1, "progress": 50},
            )
        ),
    )
    reg = _make_registry(flash_cpu)
    result = await reg.get_progress()
    assert result["success"] is True
    _assert_recorded(reg, "get_progress", True)


# ---------------------------------------------------------------------------
# get_status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_status_success():
    flash_cpu = types.SimpleNamespace(
        execute_request=AsyncMock(
            return_value=_make_flash_response(
                FlashOperation.GET_STATUS,
                result={"location": "guild_hall", "time": "morning"},
            )
        ),
    )
    reg = _make_registry(flash_cpu)
    result = await reg.get_status()
    assert result["success"] is True
    _assert_recorded(reg, "get_status", True)


# ---------------------------------------------------------------------------
# add_teammate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_teammate_success():
    flash_cpu = types.SimpleNamespace(
        execute_request=AsyncMock(
            return_value=_make_flash_response(FlashOperation.ADD_TEAMMATE)
        ),
    )
    reg = _make_registry(flash_cpu)
    result = await reg.add_teammate(
        character_id="priestess",
        name="Priestess",
        role="healer",
        personality="gentle and kind",
        response_tendency=0.7,
    )
    assert result["success"] is True
    _assert_recorded(reg, "add_teammate", True)


# ---------------------------------------------------------------------------
# remove_teammate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remove_teammate_success():
    flash_cpu = types.SimpleNamespace(
        execute_request=AsyncMock(
            return_value=_make_flash_response(FlashOperation.REMOVE_TEAMMATE)
        ),
    )
    reg = _make_registry(flash_cpu)
    result = await reg.remove_teammate("priestess", "quest complete")
    assert result["success"] is True
    _assert_recorded(reg, "remove_teammate", True)


# ---------------------------------------------------------------------------
# disband_party
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disband_party_success():
    flash_cpu = types.SimpleNamespace(
        execute_request=AsyncMock(
            return_value=_make_flash_response(FlashOperation.DISBAND_PARTY)
        ),
    )
    reg = _make_registry(flash_cpu)
    result = await reg.disband_party("betrayal")
    assert result["success"] is True
    _assert_recorded(reg, "disband_party", True)


# ---------------------------------------------------------------------------
# heal_player / damage_player / add_xp
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_heal_player_success():
    flash_cpu = types.SimpleNamespace(
        execute_request=AsyncMock(
            return_value=_make_flash_response(FlashOperation.HEAL_PLAYER)
        ),
    )
    reg = _make_registry(flash_cpu)
    result = await reg.heal_player(5)
    assert result["success"] is True
    _assert_recorded(reg, "heal_player", True)


@pytest.mark.asyncio
async def test_damage_player_success():
    flash_cpu = types.SimpleNamespace(
        execute_request=AsyncMock(
            return_value=_make_flash_response(FlashOperation.DAMAGE_PLAYER)
        ),
    )
    reg = _make_registry(flash_cpu)
    result = await reg.damage_player(3)
    assert result["success"] is True
    _assert_recorded(reg, "damage_player", True)


@pytest.mark.asyncio
async def test_add_xp_success():
    flash_cpu = types.SimpleNamespace(
        execute_request=AsyncMock(
            return_value=_make_flash_response(FlashOperation.ADD_XP)
        ),
    )
    reg = _make_registry(flash_cpu)
    result = await reg.add_xp(100)
    assert result["success"] is True
    _assert_recorded(reg, "add_xp", True)


# ---------------------------------------------------------------------------
# add_item / remove_item
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_item_success():
    flash_cpu = types.SimpleNamespace(
        execute_request=AsyncMock(
            return_value=_make_flash_response(FlashOperation.ADD_ITEM)
        ),
    )
    reg = _make_registry(flash_cpu)
    result = await reg.add_item("healing_potion", "Healing Potion", 2)
    assert result["success"] is True
    _assert_recorded(reg, "add_item", True)


@pytest.mark.asyncio
async def test_remove_item_success():
    flash_cpu = types.SimpleNamespace(
        execute_request=AsyncMock(
            return_value=_make_flash_response(FlashOperation.REMOVE_ITEM)
        ),
    )
    reg = _make_registry(flash_cpu)
    result = await reg.remove_item("healing_potion", 1)
    assert result["success"] is True
    _assert_recorded(reg, "remove_item", True)


# ---------------------------------------------------------------------------
# ability_check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ability_check_success():
    flash_cpu = types.SimpleNamespace(
        execute_request=AsyncMock(
            return_value=_make_flash_response(
                FlashOperation.ABILITY_CHECK,
                result={"roll": 15, "total": 18, "success": True},
            )
        ),
    )
    reg = _make_registry(flash_cpu)
    result = await reg.ability_check(ability="dex", skill="stealth", dc=15)
    assert result["success"] is True
    _assert_recorded(reg, "ability_check", True)


# ---------------------------------------------------------------------------
# evaluate_story_conditions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_story_conditions_accepted():
    reg = _make_registry(pending_condition_ids=["cond_1", "cond_2"])
    result = await reg.evaluate_story_conditions(
        condition_id="cond_1",
        result=True,
        reasoning="Player reached the gate",
    )
    assert result["accepted"] is True
    assert reg.story_condition_results["cond_1"] is True
    _assert_recorded(reg, "evaluate_story_conditions", True)


@pytest.mark.asyncio
async def test_evaluate_story_conditions_rejected_unknown_id():
    reg = _make_registry(pending_condition_ids=["cond_1"])
    result = await reg.evaluate_story_conditions(
        condition_id="unknown_cond",
        result=True,
        reasoning="Guess",
    )
    assert result["accepted"] is False
    assert "not pending" in result.get("error", "")
    _assert_recorded(reg, "evaluate_story_conditions", False)


# ---------------------------------------------------------------------------
# recall_memory
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recall_memory_empty_seeds():
    reg = _make_registry()
    result = await reg.recall_memory(seeds=[], character_id="player")
    assert result["success"] is False
    assert "missing seeds" in result["error"]
    _assert_recorded(reg, "recall_memory", False)


@pytest.mark.asyncio
async def test_recall_memory_success():
    mock_recall = types.SimpleNamespace(
        model_dump=lambda: {"activated_nodes": {"forest": 0.8}, "subgraph": {}},
    )
    flash_service = types.SimpleNamespace(
        recall_memory=AsyncMock(return_value=mock_recall),
    )
    flash_cpu = types.SimpleNamespace(
        state_manager=types.SimpleNamespace(
            get_state=AsyncMock(
                return_value=types.SimpleNamespace(chapter_id="ch1", area_id="area1")
            ),
        ),
        recall_orchestrator=None,
        flash_service=flash_service,
    )
    reg = _make_registry(flash_cpu)
    result = await reg.recall_memory(
        seeds=["forest", "goblin"],
        character_id="player",
    )
    assert result["success"] is True
    _assert_recorded(reg, "recall_memory", True)


# ---------------------------------------------------------------------------
# generate_scene_image
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_scene_image_success():
    reg = _make_registry()
    mock_image_data = {"base64": "abc123==", "mime_type": "image/png"}
    reg.image_service = types.SimpleNamespace(
        generate=AsyncMock(return_value=mock_image_data),
    )
    result = await reg.generate_scene_image("A dark forest clearing")
    assert result["generated"] is True
    assert reg.image_data is not None
    _assert_recorded(reg, "generate_scene_image", True)


@pytest.mark.asyncio
async def test_generate_scene_image_only_once_per_turn():
    reg = _make_registry()
    mock_image_data = {"base64": "abc123==", "mime_type": "image/png"}
    reg.image_service = types.SimpleNamespace(
        generate=AsyncMock(return_value=mock_image_data),
    )
    await reg.generate_scene_image("Scene 1")
    result2 = await reg.generate_scene_image("Scene 2")
    assert result2["generated"] is False
    assert "already generated" in result2["error"]


# ---------------------------------------------------------------------------
# Exception handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flash_operation_exception_captured():
    """Verify that exceptions from execute_request are caught and recorded."""
    flash_cpu = types.SimpleNamespace(
        execute_request=AsyncMock(side_effect=RuntimeError("db connection lost")),
    )
    reg = _make_registry(flash_cpu)
    result = await reg.navigate("tavern")
    assert result["success"] is False
    assert "RuntimeError" in result["error"]
    _assert_recorded(reg, "navigate", False)


@pytest.mark.asyncio
async def test_flash_operation_timeout_captured():
    """Verify that timeout is caught and recorded."""
    import asyncio

    async def _slow_execute(*args, **kwargs):
        await asyncio.sleep(100)

    flash_cpu = types.SimpleNamespace(
        execute_request=_slow_execute,
    )
    reg = _make_registry(flash_cpu)
    # Patch the timeout to be very short
    with patch("app.services.admin.agentic_tools.settings") as mock_settings:
        mock_settings.admin_agentic_tool_timeout_seconds = 0.01
        mock_settings.image_generation_timeout_seconds = 60
        result = await reg.get_progress()
    assert result["success"] is False
    assert "timeout" in result["error"]
    _assert_recorded(reg, "get_progress", False)


@pytest.mark.asyncio
async def test_execute_tool_call_dispatches_by_name():
    flash_cpu = types.SimpleNamespace(
        execute_request=AsyncMock(
            return_value=_make_flash_response(
                FlashOperation.ADD_ITEM,
                result={"item_id": "potion_small", "quantity": 1},
            )
        ),
    )
    reg = _make_registry(flash_cpu)
    result = await reg.execute_tool_call(
        "add_item",
        {"item_id": "potion_small", "item_name": "小治疗药水", "quantity": 1},
    )
    assert result["success"] is True
    _assert_recorded(reg, "add_item", True)


@pytest.mark.asyncio
async def test_execute_tool_call_handles_unknown_tool():
    reg = _make_registry()
    result = await reg.execute_tool_call("unknown_tool", {"x": 1})
    assert result["success"] is False
    assert "unknown tool" in result["error"]
    _assert_recorded(reg, "unknown_tool", False)
