"""Tests for Phase 4c — GM extra_tools factory (C2).

Validates:
- 8 MCP-dependent tools via build_gm_extra_tools
- Engine exclusion filtering
- SSE event push for npc_dialogue and ability_check
- Combat resolution flow
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from app.world.gm_extra_tools import build_gm_extra_tools, ENGINE_TOOL_EXCLUSIONS


def _run(coro):
    return asyncio.run(coro)


def _make_flash_response(success=True, result=None, error=None):
    resp = MagicMock()
    resp.success = success
    resp.result = result or {}
    resp.error = error
    return resp


def _make_deps(**overrides):
    session = MagicMock()
    session.world_id = "w1"
    session.session_id = "s1"
    session.player = MagicMock()
    session.player.to_combat_player_state.return_value = {"hp": 100}
    session.game_state = MagicMock()
    session.game_state.combat_id = None
    session.world = MagicMock()
    session.world.get_character.return_value = {"name": "Test NPC"}
    session.narrative = MagicMock()
    session.narrative.npc_interactions = {}

    flash_cpu = MagicMock()
    flash_cpu.execute_request = AsyncMock(return_value=_make_flash_response())
    flash_cpu.call_combat_tool = AsyncMock(return_value={})

    graph_store = MagicMock()
    event_queue = asyncio.Queue()

    deps = {
        "session": session,
        "flash_cpu": flash_cpu,
        "graph_store": graph_store,
        "event_queue": event_queue,
    }
    deps.update(overrides)
    return deps


def _get_tool(tools, name):
    for t in tools:
        if t.__name__ == name:
            return t
    return None


# =========================================================================
# Engine exclusion
# =========================================================================


class TestEngineExclusions:
    def test_no_exclusion_all_tools(self):
        deps = _make_deps()
        tools = build_gm_extra_tools(**deps)
        names = {t.__name__ for t in tools}
        assert "npc_dialogue" in names
        assert len(tools) == 8

    def test_talk_excludes_npc_dialogue(self):
        deps = _make_deps()
        tools = build_gm_extra_tools(**deps, engine_executed={"type": "talk"})
        names = {t.__name__ for t in tools}
        assert "npc_dialogue" not in names
        assert len(tools) == 7

    def test_use_item_excludes_multiple(self):
        deps = _make_deps()
        tools = build_gm_extra_tools(**deps, engine_executed={"type": "use_item"})
        names = {t.__name__ for t in tools}
        assert "add_item" not in names
        assert "remove_item" not in names
        assert "heal_player" not in names

    def test_unknown_type_no_exclusion(self):
        deps = _make_deps()
        tools = build_gm_extra_tools(**deps, engine_executed={"type": "unknown"})
        assert len(tools) == 8


# =========================================================================
# NPC dialogue
# =========================================================================


class TestNpcDialogue:
    def test_calls_flash_cpu(self):
        deps = _make_deps()
        deps["flash_cpu"].execute_request = AsyncMock(
            return_value=_make_flash_response(True, {"response": "Hello!"})
        )
        tools = build_gm_extra_tools(**deps)
        npc_dialogue = _get_tool(tools, "npc_dialogue")
        result = _run(npc_dialogue(npc_id="npc1", message="hi"))
        assert result["success"] is True
        assert result["npc_name"] == "Test NPC"
        deps["flash_cpu"].execute_request.assert_called_once()

    def test_sse_npc_response(self):
        deps = _make_deps()
        deps["flash_cpu"].execute_request = AsyncMock(
            return_value=_make_flash_response(True, {"response": "世界你好"})
        )
        tools = build_gm_extra_tools(**deps)
        npc_dialogue = _get_tool(tools, "npc_dialogue")
        _run(npc_dialogue(npc_id="npc1", message="hey"))
        event = deps["event_queue"].get_nowait()
        assert event["type"] == "npc_response"
        assert event["dialogue"] == "世界你好"

    def test_timeout_returns_error(self):
        deps = _make_deps()
        deps["flash_cpu"].execute_request = AsyncMock(side_effect=asyncio.TimeoutError())
        tools = build_gm_extra_tools(**deps)
        npc_dialogue = _get_tool(tools, "npc_dialogue")
        result = _run(npc_dialogue(npc_id="npc1", message="hi"))
        assert result["success"] is False
        assert "timeout" in result["error"]


# =========================================================================
# Ability check
# =========================================================================


class TestAbilityCheck:
    def test_calls_flash_cpu(self):
        deps = _make_deps()
        deps["flash_cpu"].execute_request = AsyncMock(
            return_value=_make_flash_response(True, {"roll": 15, "total": 17, "success": True})
        )
        tools = build_gm_extra_tools(**deps)
        ability_check = _get_tool(tools, "ability_check")
        result = _run(ability_check(ability="str", skill="athletics", dc=12))
        assert result["success"] is True
        assert result["roll"] == 15

    def test_sse_dice_result(self):
        deps = _make_deps()
        deps["flash_cpu"].execute_request = AsyncMock(
            return_value=_make_flash_response(True, {"roll": 20, "total": 22, "success": True, "is_critical": True})
        )
        tools = build_gm_extra_tools(**deps)
        ability_check = _get_tool(tools, "ability_check")
        _run(ability_check(ability="dex", dc=15))
        event = deps["event_queue"].get_nowait()
        assert event["type"] == "dice_result"
        assert event["roll"] == 20
        assert event["is_critical"] is True


# =========================================================================
# Combat tools
# =========================================================================


class TestStartCombat:
    def test_with_enemy_dicts(self):
        deps = _make_deps()
        deps["flash_cpu"].execute_request = AsyncMock(
            return_value=_make_flash_response(True, {"combat_id": "c1"})
        )
        tools = build_gm_extra_tools(**deps)
        start = _get_tool(tools, "start_combat")
        result = _run(start(enemies=[{"enemy_id": "goblin", "count": 3, "level": 2}]))
        assert result["success"] is True
        deps["flash_cpu"].execute_request.assert_called_once()

    def test_with_string_enemies(self):
        deps = _make_deps()
        deps["flash_cpu"].execute_request = AsyncMock(
            return_value=_make_flash_response(True, {"combat_id": "c1"})
        )
        tools = build_gm_extra_tools(**deps)
        start = _get_tool(tools, "start_combat")
        _run(start(enemies=["goblin", "orc"]))
        call_args = deps["flash_cpu"].execute_request.call_args
        params = call_args[1]["request"].parameters if "request" in call_args[1] else call_args[0][2].parameters
        # Just verify call succeeded
        deps["flash_cpu"].execute_request.assert_called_once()


class TestChooseCombatAction:
    def test_no_active_combat(self):
        deps = _make_deps()
        deps["session"].game_state.combat_id = None
        tools = build_gm_extra_tools(**deps)
        choose = _get_tool(tools, "choose_combat_action")
        result = _run(choose(action_id="attack"))
        assert result["success"] is False
        assert "no active combat" in result["error"]

    def test_combat_action_with_resolution(self):
        deps = _make_deps()
        deps["session"].game_state.combat_id = "c1"
        # First call: execute action, combat ends
        deps["flash_cpu"].call_combat_tool = AsyncMock(side_effect=[
            {"combat_state": {"is_ended": True}, "final_result": {"result": "victory"}},
            {"rewards": {"xp": 100}},  # resolve_combat_session_v3
        ])
        # Disable P6 WorldEvent to simplify test
        deps["session"].world_graph = None
        tools = build_gm_extra_tools(**deps)
        choose = _get_tool(tools, "choose_combat_action")
        result = _run(choose(action_id="attack"))
        assert result.get("resolve") is not None


# =========================================================================
# Party tools
# =========================================================================


class TestPartyTools:
    def test_add_teammate(self):
        deps = _make_deps()
        deps["flash_cpu"].execute_request = AsyncMock(
            return_value=_make_flash_response(True, {"character_id": "warrior"})
        )
        tools = build_gm_extra_tools(**deps)
        add_tm = _get_tool(tools, "add_teammate")
        result = _run(add_tm(character_id="warrior", name="战士"))
        assert result["success"] is True

    def test_remove_teammate(self):
        deps = _make_deps()
        deps["flash_cpu"].execute_request = AsyncMock(
            return_value=_make_flash_response(True, {})
        )
        tools = build_gm_extra_tools(**deps)
        remove_tm = _get_tool(tools, "remove_teammate")
        result = _run(remove_tm(character_id="warrior", reason="故事需要"))
        assert result["success"] is True

    def test_disband_party(self):
        deps = _make_deps()
        deps["flash_cpu"].execute_request = AsyncMock(
            return_value=_make_flash_response(True, {})
        )
        tools = build_gm_extra_tools(**deps)
        disband = _get_tool(tools, "disband_party")
        result = _run(disband(reason="分道扬镳"))
        assert result["success"] is True
