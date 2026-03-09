"""Tests for D-P35 — NPC role constraint injection (tag-driven truth-source data).

Covers:
  - guild_girl has 'receptionist' tag in characters.json
  - _extract_receptionist_data returns correct structure
  - _extract_role_data dispatches to correct extractor
  - _format_role_constraint_block formats receptionist/merchant blocks
  - _build_npc_prompt_text injects role_block when role_data provided
  - build_npc_full_context and build_npc_system_prompt inject role_data for receptionist NPC
  - Non-specialized NPCs (party members) get no constraint block
  - AreaSlice.get_all_board_bulletins API
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from app.game_core.bootstrap import build_default_world, build_runtime_for_world
from app.game_core.content import WorldInstance
from app.game_core.narrative.context_builder import (
    AgentContextBuilder,
    _build_npc_prompt_text,
    _extract_receptionist_data,
    _extract_merchant_data,
    _extract_role_data,
    _format_role_constraint_block,
)
from app.game_core.state import StateContainer
from app.game_core.state.slices.area import AreaSlice


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

CHARACTERS_JSON = Path(__file__).parent.parent / "data/goblin_slayer/v2/characters.json"

# Tags registry must contain every tag referenced by character entries.
_TAGS_REGISTRY = {
    "receptionist": {"id": "receptionist", "tags": ["receptionist"]},
    "guild_staff": {"id": "guild_staff", "tags": ["guild_staff"]},
    "recruitable": {"id": "recruitable", "tags": ["recruitable"]},
    "warrior": {"id": "warrior", "tags": ["warrior"]},
    "merchant": {"id": "merchant", "tags": ["merchant"]},
}


def _make_world_with_receptionist() -> WorldInstance:
    return build_default_world(
        "test_world",
        world_data={
            "tags": _TAGS_REGISTRY,
            "characters": {
                "receptionist_npc": {
                    "id": "receptionist_npc",
                    "name": "Guild Clerk",
                    "personality": "Efficient and friendly.",
                    "tags": ["receptionist", "guild_staff"],
                },
                "fighter_npc": {
                    "id": "fighter_npc",
                    "name": "Strong Fighter",
                    "personality": "Tough and direct.",
                    "tags": ["recruitable", "warrior"],
                },
            },
        },
    )


def _make_state_with_bulletins(world: WorldInstance) -> StateContainer:
    runtime = build_runtime_for_world(world)
    state = runtime.state
    state.player.restore({
        "character_name": "Hero",
        "current_area": "frontier_town",
        "current_location": "guild_hall",
    })
    state.areas.add_board_bulletin(
        "frontier_town",
        "guild_board",
        {
            "quest_id": "goblin_cave_q1",
            "title": "哥布林巢穴讨伐",
            "summary": "清剿附近洞窟的哥布林群落",
        },
    )
    state.areas.add_board_bulletin(
        "frontier_town",
        "guild_board",
        {
            "quest_id": "lost_sheep_q2",
            "title": "寻找丢失的羊群",
            "summary": "帮农夫找回走失的羊",
        },
    )
    return state


def _make_state_with_active_quest(world: WorldInstance) -> StateContainer:
    runtime = build_runtime_for_world(world)
    state = runtime.state
    state.player.restore({
        "character_name": "Hero",
        "current_area": "frontier_town",
        "current_location": "guild_hall",
    })
    state.areas.add_board_bulletin(
        "frontier_town",
        "guild_board",
        {"quest_id": "dq_goblin_raid", "title": "紧急委托：哥布林袭击", "summary": ""},
    )
    state.quests.restore({
        "dynamic_quests": {
            "dq_goblin_raid": {
                "quest_id": "dq_goblin_raid",
                "title": "紧急委托：哥布林袭击",
                "status": "active",
            },
            "dq_old_quest": {
                "quest_id": "dq_old_quest",
                "title": "已完成任务",
                "status": "completed",
            },
        }
    })
    return state


def _make_world_with_merchant() -> WorldInstance:
    return build_default_world(
        "test_world",
        world_data={
            "tags": _TAGS_REGISTRY,
            "characters": {
                "merchant_npc": {
                    "id": "merchant_npc",
                    "name": "Blacksmith",
                    "personality": "Gruff but fair.",
                    "tags": ["merchant"],
                },
            },
        },
    )


def _make_state_with_merchant_inventory(world: WorldInstance, npc_id: str) -> StateContainer:
    runtime = build_runtime_for_world(world)
    state = runtime.state
    state.player.restore({"character_name": "Hero", "current_area": "market"})
    state.relations.restore({
        "shop_states": {
            npc_id: {
                "items": [
                    {"item_id": "iron_sword", "price": 50, "stock": 3},
                    {"item_id": "healing_potion", "price": 30, "stock": 10},
                ],
            }
        }
    })
    return state


# ------------------------------------------------------------------
# Phase 1 test: characters.json data validation
# ------------------------------------------------------------------

def test_guild_girl_has_receptionist_tag():
    """guild_girl in characters.json should have 'receptionist' tag."""
    data = json.loads(CHARACTERS_JSON.read_text())
    guild_girl = data.get("guild_girl", {})
    tags = guild_girl.get("tags", [])
    assert "receptionist" in tags, f"Expected 'receptionist' in guild_girl tags, got: {tags}"


# ------------------------------------------------------------------
# Phase 3 tests: AreaSlice.get_all_board_bulletins
# ------------------------------------------------------------------

def test_get_all_board_bulletins_returns_all_boards():
    """get_all_board_bulletins returns all bulletins keyed by board_id."""
    slice_ = AreaSlice()
    slice_.add_board_bulletin("zone1", "board_a", {"quest_id": "q1", "title": "Q1"})
    slice_.add_board_bulletin("zone1", "board_a", {"quest_id": "q2", "title": "Q2"})
    slice_.add_board_bulletin("zone1", "board_b", {"quest_id": "q3", "title": "Q3"})

    result = slice_.get_all_board_bulletins("zone1")
    assert "board_a" in result
    assert "board_b" in result
    assert len(result["board_a"]) == 2
    assert len(result["board_b"]) == 1
    assert result["board_a"][0]["quest_id"] == "q1"


def test_get_all_board_bulletins_missing_area_returns_empty():
    slice_ = AreaSlice()
    assert slice_.get_all_board_bulletins("nonexistent") == {}


def test_get_all_board_bulletins_defensive_copy():
    """Returned dicts should be copies, not references."""
    slice_ = AreaSlice()
    slice_.add_board_bulletin("zone1", "board_a", {"quest_id": "q1"})
    result = slice_.get_all_board_bulletins("zone1")
    result["board_a"][0]["quest_id"] = "MUTATED"
    # Original should be unchanged
    original = slice_.get_board_bulletins("zone1", "board_a")
    assert original[0]["quest_id"] == "q1"


# ------------------------------------------------------------------
# Phase 2 tests: _extract_receptionist_data
# ------------------------------------------------------------------

def test_extract_receptionist_data_structure():
    """_extract_receptionist_data returns dict with role/bulletins/active_quests keys."""
    world = _make_world_with_receptionist()
    state = _make_state_with_bulletins(world)

    result = _extract_receptionist_data(state)
    assert result["role"] == "receptionist"
    assert isinstance(result["bulletins"], list)
    assert isinstance(result["active_quests"], list)


def test_extract_receptionist_data_bulletins_populated():
    """Bulletins from the player's current area are returned."""
    world = _make_world_with_receptionist()
    state = _make_state_with_bulletins(world)

    result = _extract_receptionist_data(state)
    bulletins = result["bulletins"]
    assert len(bulletins) == 2
    quest_ids = {b["quest_id"] for b in bulletins}
    assert "goblin_cave_q1" in quest_ids
    assert "lost_sheep_q2" in quest_ids


def test_extract_receptionist_data_active_quests_only():
    """Only 'active' quests appear in active_quests; completed quests are excluded."""
    world = _make_world_with_receptionist()
    state = _make_state_with_active_quest(world)

    result = _extract_receptionist_data(state)
    quest_ids = {q["quest_id"] for q in result["active_quests"]}
    assert "dq_goblin_raid" in quest_ids
    assert "dq_old_quest" not in quest_ids


def test_extract_receptionist_data_empty_area():
    """Empty area returns empty bulletins list without error."""
    world = _make_world_with_receptionist()
    runtime = build_runtime_for_world(world)
    state = runtime.state
    state.player.restore({"current_area": "empty_area"})

    result = _extract_receptionist_data(state)
    assert result["role"] == "receptionist"
    assert result["bulletins"] == []


# ------------------------------------------------------------------
# _extract_role_data dispatch tests
# ------------------------------------------------------------------

def test_extract_role_data_receptionist_tag():
    """'receptionist' tag dispatches to _extract_receptionist_data."""
    world = _make_world_with_receptionist()
    state = _make_state_with_bulletins(world)

    result = _extract_role_data(["receptionist", "guild_staff"], "clerk", state, world)
    assert result is not None
    assert result["role"] == "receptionist"


def test_extract_role_data_merchant_tag():
    """'merchant' tag dispatches to _extract_merchant_data."""
    world = _make_world_with_merchant()
    state = _make_state_with_merchant_inventory(world, "merchant_npc")

    result = _extract_role_data(["merchant"], "merchant_npc", state, world)
    assert result is not None
    assert result["role"] == "merchant"


def test_extract_role_data_no_specialized_tag_returns_none():
    """NPC without receptionist/merchant tag returns None."""
    world = _make_world_with_receptionist()
    runtime = build_runtime_for_world(world)
    state = runtime.state

    result = _extract_role_data(["recruitable", "warrior"], "fighter_npc", state, world)
    assert result is None


def test_extract_role_data_empty_tags_returns_none():
    world = _make_world_with_receptionist()
    runtime = build_runtime_for_world(world)
    state = runtime.state

    result = _extract_role_data([], "npc", state, world)
    assert result is None


# ------------------------------------------------------------------
# _format_role_constraint_block tests
# ------------------------------------------------------------------

def test_format_receptionist_block_with_bulletins():
    """Receptionist block lists bulletin task titles."""
    role_data = {
        "role": "receptionist",
        "bulletins": [{"quest_id": "q1", "title": "哥布林讨伐", "summary": "猎杀哥布林"}],
        "active_quests": [],
    }
    block = _format_role_constraint_block(role_data)
    assert "哥布林讨伐" in block
    assert "你的职责" in block
    assert "约束规则" in block
    assert "绝不编造" in block


def test_format_receptionist_block_empty_board():
    """When no bulletins, shows '没有可接取的任务'."""
    role_data = {
        "role": "receptionist",
        "bulletins": [],
        "active_quests": [],
    }
    block = _format_role_constraint_block(role_data)
    assert "没有可接取的任务" in block


def test_format_receptionist_block_with_active_quests():
    """Active quests are listed in the constraint block."""
    role_data = {
        "role": "receptionist",
        "bulletins": [],
        "active_quests": [{"quest_id": "dq_raid", "title": "哥布林袭击任务"}],
    }
    block = _format_role_constraint_block(role_data)
    assert "哥布林袭击任务" in block


def test_format_receptionist_block_no_active_quests():
    """When no active quests, shows '没有进行中的任务'."""
    role_data = {
        "role": "receptionist",
        "bulletins": [],
        "active_quests": [],
    }
    block = _format_role_constraint_block(role_data)
    assert "没有进行中的任务" in block


def test_format_merchant_block_with_inventory():
    """Merchant block lists item IDs and prices."""
    role_data = {
        "role": "merchant",
        "inventory": [{"item_id": "iron_sword", "price": 50, "stock": 3}],
    }
    block = _format_role_constraint_block(role_data)
    assert "iron_sword" in block
    assert "50" in block
    assert "商人" in block
    assert "绝不编造" in block


def test_format_merchant_block_empty_inventory():
    """Merchant block with empty inventory shows '没有库存'."""
    role_data = {"role": "merchant", "inventory": []}
    block = _format_role_constraint_block(role_data)
    assert "没有库存" in block


def test_format_unknown_role_returns_empty():
    """Unknown role returns empty string — no constraint block."""
    block = _format_role_constraint_block({"role": "wizard"})
    assert block == ""


# ------------------------------------------------------------------
# _build_npc_prompt_text injection tests
# ------------------------------------------------------------------

def _minimal_profile(name: str = "Test NPC", tags: list[str] | None = None) -> dict:
    return {"name": name, "tags": tags or [], "personality": "A test character."}


def test_prompt_text_no_role_data_no_constraint_block():
    """Without role_data, prompt does not contain constraint section."""
    prompt = _build_npc_prompt_text(
        _minimal_profile(),
        disposition={},
        stage="stranger",
        impressions=[],
    )
    assert "你的职责" not in prompt
    assert "约束规则" not in prompt


def test_prompt_text_with_receptionist_role_data_injects_block():
    """With receptionist role_data, constraint block appears in prompt."""
    role_data = {
        "role": "receptionist",
        "bulletins": [{"quest_id": "q1", "title": "杀哥布林", "summary": ""}],
        "active_quests": [],
    }
    prompt = _build_npc_prompt_text(
        _minimal_profile(tags=["receptionist"]),
        disposition={},
        stage="stranger",
        impressions=[],
        role_data=role_data,
    )
    assert "你的职责" in prompt
    assert "杀哥布林" in prompt
    assert "约束规则" in prompt


def test_prompt_text_role_block_placed_before_tool_rules():
    """Role constraint block appears in prompt before 'Tool usage rules'."""
    role_data = {
        "role": "receptionist",
        "bulletins": [],
        "active_quests": [],
    }
    prompt = _build_npc_prompt_text(
        _minimal_profile(tags=["receptionist"]),
        disposition={},
        stage="stranger",
        impressions=[],
        role_data=role_data,
    )
    constraint_pos = prompt.find("你的职责")
    tool_rules_pos = prompt.find("Tool usage rules")
    assert constraint_pos != -1
    assert tool_rules_pos != -1
    assert constraint_pos < tool_rules_pos


# ------------------------------------------------------------------
# build_npc_full_context integration tests (async)
# ------------------------------------------------------------------

def test_receptionist_full_context_prompt_contains_bulletins():
    """build_npc_full_context for receptionist NPC injects bulletin data."""
    async def _run() -> None:
        world = _make_world_with_receptionist()
        state = _make_state_with_bulletins(world)
        builder = AgentContextBuilder(world, state)

        result = await builder.build_npc_full_context("receptionist_npc")
        assert result is not None
        assert "你的职责" in result.system_prompt
        assert "哥布林巢穴讨伐" in result.system_prompt
        assert "寻找丢失的羊群" in result.system_prompt

    asyncio.run(_run())


def test_receptionist_full_context_empty_board_shows_placeholder():
    """Receptionist prompt with empty board shows placeholder text."""
    async def _run() -> None:
        world = _make_world_with_receptionist()
        runtime = build_runtime_for_world(world)
        state = runtime.state
        state.player.restore({"current_area": "frontier_town"})
        builder = AgentContextBuilder(world, state)

        result = await builder.build_npc_full_context("receptionist_npc")
        assert result is not None
        assert "没有可接取的任务" in result.system_prompt

    asyncio.run(_run())


def test_non_specialized_npc_no_constraint_block():
    """NPC without receptionist/merchant tag has no constraint block in prompt."""
    async def _run() -> None:
        world = _make_world_with_receptionist()
        state = _make_state_with_bulletins(world)
        builder = AgentContextBuilder(world, state)

        result = await builder.build_npc_full_context("fighter_npc")
        assert result is not None
        assert "你的职责" not in result.system_prompt
        assert "约束规则" not in result.system_prompt

    asyncio.run(_run())


def test_receptionist_system_prompt_also_injects_role():
    """build_npc_system_prompt (simpler path) also injects role constraint block."""
    async def _run() -> None:
        world = _make_world_with_receptionist()
        state = _make_state_with_bulletins(world)
        builder = AgentContextBuilder(world, state)

        prompt = await builder.build_npc_system_prompt("receptionist_npc")
        assert prompt is not None
        assert "你的职责" in prompt

    asyncio.run(_run())


def test_receptionist_full_context_active_quests_in_prompt():
    """Active quests are listed in receptionist prompt."""
    async def _run() -> None:
        world = _make_world_with_receptionist()
        state = _make_state_with_active_quest(world)
        builder = AgentContextBuilder(world, state)

        result = await builder.build_npc_full_context("receptionist_npc")
        assert result is not None
        assert "紧急委托：哥布林袭击" in result.system_prompt

    asyncio.run(_run())


def test_constraint_rules_text_present():
    """Constraint rules text appears in receptionist prompt."""
    async def _run() -> None:
        world = _make_world_with_receptionist()
        state = _make_state_with_bulletins(world)
        builder = AgentContextBuilder(world, state)

        result = await builder.build_npc_full_context("receptionist_npc")
        assert result is not None
        assert "绝不编造" in result.system_prompt

    asyncio.run(_run())
