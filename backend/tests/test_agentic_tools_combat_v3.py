import types
from unittest.mock import AsyncMock

import pytest

from app.services.admin.agentic_tools import AgenticToolRegistry
from app.models.admin_protocol import FlashOperation


@pytest.mark.asyncio
async def test_start_combat_accepts_structured_enemy_specs():
    registry = AgenticToolRegistry(
        flash_cpu=types.SimpleNamespace(),
        world_id="w",
        session_id="s",
    )

    captured = {}

    async def _fake_execute(operation, parameters):
        captured["operation"] = operation
        captured["parameters"] = parameters
        return {"success": True}

    registry._execute_flash_operation = _fake_execute  # type: ignore[assignment]

    await registry.start_combat(
        [
            {
                "enemy_id": "monster_goblin_scout",
                "count": 2,
                "level": 3,
                "overrides": {"max_hp": 30},
            }
        ]
    )

    assert captured["operation"] == FlashOperation.START_COMBAT
    payload = captured["parameters"]["enemies"]
    assert payload[0]["enemy_id"] == "monster_goblin_scout"
    assert payload[0]["count"] == 2
    assert payload[0]["level"] == 3


@pytest.mark.asyncio
async def test_get_combat_options_returns_actions_v3():
    session_store = types.SimpleNamespace(
        get_session=AsyncMock(return_value=types.SimpleNamespace(active_combat_id="combat_1"))
    )

    async def _call_tool(name, arguments):
        if name == "get_available_actions_for_actor":
            return {
                "actions": [
                    {
                        "action_id": "attack_goblin_1",
                        "type": "attack",
                        "display_name": "攻击",
                        "description": "普通攻击",
                        "target_id": "goblin_1",
                        "cost_type": "action",
                    }
                ]
            }
        if name == "get_combat_state":
            return {"combat_id": "combat_1", "state": "waiting_player_input"}
        raise AssertionError(f"unexpected tool: {name}")

    flash_cpu = types.SimpleNamespace(
        session_store=session_store,
        call_combat_tool=_call_tool,
    )

    registry = AgenticToolRegistry(flash_cpu=flash_cpu, world_id="w", session_id="s")
    payload = await registry.get_combat_options(actor_id="player")

    assert payload["success"] is True
    assert payload["combat_id"] == "combat_1"
    assert payload["actions_v3"][0]["action_type"] == "attack"


@pytest.mark.asyncio
async def test_choose_combat_action_falls_back_to_execute_action():
    session_store = types.SimpleNamespace(
        get_session=AsyncMock(return_value=types.SimpleNamespace(active_combat_id="combat_1"))
    )

    async def _call_tool(name, arguments):
        if name == "execute_action_for_actor":
            return {"error": "actor mismatch"}
        if name == "execute_action":
            return {
                "combat_state": {"is_ended": False},
                "action_result": {"display_text": "命中"},
            }
        raise AssertionError(f"unexpected tool: {name}")

    flash_cpu = types.SimpleNamespace(
        session_store=session_store,
        call_combat_tool=_call_tool,
    )

    registry = AgenticToolRegistry(flash_cpu=flash_cpu, world_id="w", session_id="s")
    payload = await registry.choose_combat_action("attack_goblin_1", actor_id="player")

    assert payload["success"] is True
    assert payload["action_result"]["display_text"] == "命中"
