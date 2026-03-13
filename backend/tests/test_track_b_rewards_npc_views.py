"""Track B integration tests: quest rewards, NPC data enrichment, quest views.

Covers:
- B-1: board_complete_quest grants gold/xp/items; receptionist_report grants rewards + rewards_claimed
- B-2: receptionist prompt includes quest objectives+rewards; merchant prompt includes item info;
       guard prompt includes semantic danger label
- B-3: active quest with no current_step gets initial navigation from objectives
- B-4: completed quest gets completed_summary
- B-5: story_facts from NarrativePlanSlice injected into NPC prompt
- B-6: receptionist accepts available quest not on board; rejects non-available quest not on board
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.game_core.content import WorldInstance
from app.game_core.content.registries import CharacterRegistry, MapRegistry
from app.game_core.content.registries.items import ItemRegistry
from app.game_core.narrative.context_builder import (
    AgentContextBuilder,
    _danger_label,
    _extract_guard_data,
    _extract_merchant_data,
    _extract_receptionist_data,
)
from app.game_core.rules import Command
from app.game_core.rules.handlers.board import BoardHandler
from app.game_core.rules.handlers.receptionist import ReceptionistHandler
from app.game_core.state import StateContainer
from app.game_core.state.slices import AreaSlice, NarrativePlanSlice, PlayerSlice, QuestSlice
from app.quest_views import normalize_dynamic_quest_view


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_board_world() -> WorldInstance:
    world = WorldInstance("test_world")
    maps = MapRegistry()
    maps.load(
        {
            "guild_hall": {
                "id": "guild_hall",
                "name": "Guild Hall",
                "sub_locations": {
                    "main_hall": {
                        "id": "main_hall",
                        "name": "Main Hall",
                        "interactables": [
                            {"id": "quest_board", "name": "Quest Board"},
                        ],
                    }
                },
            }
        }
    )
    world.register(maps)
    return world


def _make_receptionist_world() -> WorldInstance:
    world = WorldInstance("test_world")
    chars = CharacterRegistry()
    chars.load(
        {
            "receptionist_npc": {
                "id": "receptionist_npc",
                "name": "Guild Clerk",
                "current_area": "guild_hall",
                "current_location": "counter",
                "tags": ["receptionist"],
            }
        }
    )
    world.register(chars)
    return world


def _make_board_state_with_rewards(
    *,
    gold: int = 100,
    xp: int = 50,
    items: list[dict] | None = None,
) -> StateContainer:
    state = StateContainer()
    player = PlayerSlice()
    player.restore(
        {
            "current_area": "guild_hall",
            "current_location": "main_hall",
            "gold": 10,
            "xp": 0,
        }
    )
    state.register(player)

    areas = AreaSlice()
    areas.restore(
        {
            "areas": {
                "guild_hall": {
                    "board_bulletins": {
                        "quest_board": [
                            {"board_id": "quest_board", "quest_id": "dq_rat_hunt", "title": "Rat Hunt"},
                        ]
                    }
                }
            }
        }
    )
    state.register(areas)

    rewards: dict[str, Any] = {"gold": gold, "xp": xp}
    if items:
        rewards["items"] = items

    quests = QuestSlice()
    quests.restore(
        {
            "dynamic_quests": {
                "dq_rat_hunt": {
                    "status": "ready_to_report",
                    "title": "Hunt the Rats",
                    "rewards": rewards,
                }
            }
        }
    )
    state.register(quests)
    return state


def _make_receptionist_state(
    *,
    quest_status: str = "completed",
    requires_report: bool = True,
    rewards_claimed: bool = False,
    gold: int = 80,
    xp: int = 30,
) -> StateContainer:
    state = StateContainer()
    player = PlayerSlice()
    player.restore(
        {
            "current_area": "guild_hall",
            "current_location": "counter",
            "gold": 5,
            "xp": 0,
        }
    )
    state.register(player)

    areas = AreaSlice()
    areas.restore(
        {
            "areas": {
                "guild_hall": {
                    "board_bulletins": {
                        "board": [
                            {"board_id": "board", "quest_id": "dq_escort", "title": "Escort Quest"},
                        ]
                    }
                }
            }
        }
    )
    state.register(areas)

    quest_data: dict[str, Any] = {
        "status": quest_status,
        "title": "Escort Quest",
        "requires_report": requires_report,
        "rewards": {"gold": gold, "xp": xp},
    }
    if rewards_claimed:
        quest_data["rewards_claimed"] = True

    quests = QuestSlice()
    quests.restore({"dynamic_quests": {"dq_escort": quest_data}})
    state.register(quests)
    return state


# ---------------------------------------------------------------------------
# B-1: board_complete_quest rewards
# ---------------------------------------------------------------------------

def test_board_complete_quest_grants_gold_and_xp() -> None:
    state = _make_board_state_with_rewards(gold=100, xp=50)
    world = _make_board_world()
    result = BoardHandler().compute(
        Command(
            type="board_complete_quest",
            params={"board_id": "quest_board", "quest_id": "dq_rat_hunt"},
        ),
        state,
        world,
    )

    assert result.executed is True
    # Find gold and xp changes
    assert result.delta is not None
    gold_changes = [c for c in result.delta.changes if c.path == "gold"]
    xp_changes = [c for c in result.delta.changes if c.path == "xp"]
    assert len(gold_changes) == 1
    assert gold_changes[0].value == 110  # 10 existing + 100 reward
    assert len(xp_changes) == 1
    assert xp_changes[0].value == 50


def test_board_complete_quest_grants_items() -> None:
    items = [{"item_id": "potion_hp", "count": 2}]
    state = _make_board_state_with_rewards(gold=0, xp=0, items=items)
    world = _make_board_world()
    result = BoardHandler().compute(
        Command(
            type="board_complete_quest",
            params={"board_id": "quest_board", "quest_id": "dq_rat_hunt"},
        ),
        state,
        world,
    )

    assert result.executed is True
    assert result.delta is not None
    inv_changes = [c for c in result.delta.changes if c.path == "inventory"]
    assert len(inv_changes) == 1
    inv = inv_changes[0].value
    assert any(
        stack.get("item_id") == "potion_hp" and stack.get("count") == 2
        for stack in inv
    )


def test_board_complete_quest_reward_summary_in_metadata() -> None:
    state = _make_board_state_with_rewards(gold=100, xp=50)
    world = _make_board_world()
    result = BoardHandler().compute(
        Command(
            type="board_complete_quest",
            params={"board_id": "quest_board", "quest_id": "dq_rat_hunt"},
        ),
        state,
        world,
    )

    assert result.executed is True
    summary = result.metadata.get("reward_summary", {})
    assert summary.get("gold") == 100
    assert summary.get("xp") == 50


def test_board_complete_quest_sets_rewards_claimed() -> None:
    state = _make_board_state_with_rewards(gold=50, xp=0)
    world = _make_board_world()
    result = BoardHandler().compute(
        Command(
            type="board_complete_quest",
            params={"board_id": "quest_board", "quest_id": "dq_rat_hunt"},
        ),
        state,
        world,
    )

    assert result.executed is True
    quest_changes = [
        c for c in result.delta.changes
        if c.path.endswith("dq_rat_hunt") and c.slice == "quests"
    ]
    assert len(quest_changes) == 1
    assert quest_changes[0].value.get("rewards_claimed") is True


# ---------------------------------------------------------------------------
# B-1: receptionist_report rewards
# ---------------------------------------------------------------------------

def test_receptionist_report_grants_gold_and_xp() -> None:
    state = _make_receptionist_state(quest_status="completed", gold=80, xp=30)
    world = _make_receptionist_world()
    result = ReceptionistHandler().compute(
        Command(
            type="receptionist_report_quest",
            params={"npc_id": "receptionist_npc", "quest_id": "dq_escort"},
        ),
        state,
        world,
    )

    assert result.executed is True
    assert result.delta is not None
    gold_changes = [c for c in result.delta.changes if c.path == "gold"]
    xp_changes = [c for c in result.delta.changes if c.path == "xp"]
    assert len(gold_changes) == 1
    assert gold_changes[0].value == 85  # 5 + 80
    assert len(xp_changes) == 1
    assert xp_changes[0].value == 30


def test_receptionist_report_prevents_double_grant() -> None:
    """If rewards_claimed is already True, reporting again should not re-grant."""
    state = _make_receptionist_state(
        quest_status="completed", gold=80, xp=30, rewards_claimed=True
    )
    world = _make_receptionist_world()
    result = ReceptionistHandler().compute(
        Command(
            type="receptionist_report_quest",
            params={"npc_id": "receptionist_npc", "quest_id": "dq_escort"},
        ),
        state,
        world,
    )

    assert result.executed is True
    assert result.delta is not None
    # No gold or xp changes should exist (rewards already claimed)
    gold_changes = [c for c in result.delta.changes if c.path == "gold"]
    xp_changes = [c for c in result.delta.changes if c.path == "xp"]
    assert len(gold_changes) == 0
    assert len(xp_changes) == 0


def test_receptionist_report_sets_rewards_claimed_flag() -> None:
    state = _make_receptionist_state(quest_status="completed", gold=80, xp=30)
    world = _make_receptionist_world()
    result = ReceptionistHandler().compute(
        Command(
            type="receptionist_report_quest",
            params={"npc_id": "receptionist_npc", "quest_id": "dq_escort"},
        ),
        state,
        world,
    )

    assert result.executed is True
    assert result.metadata.get("rewards_claimed") is True
    # Check the rewards_claimed StateChange is present
    claimed_changes = [
        c for c in result.delta.changes
        if "rewards_claimed" in c.path
    ]
    assert len(claimed_changes) == 1
    assert claimed_changes[0].value is True


# ---------------------------------------------------------------------------
# B-2: context_builder NPC data enrichment
# ---------------------------------------------------------------------------

def _make_context_state_with_board() -> StateContainer:
    state = StateContainer()
    player = PlayerSlice()
    player.restore({"current_area": "frontier_town", "current_location": "guild_hall"})
    state.register(player)

    areas = AreaSlice()
    areas.restore(
        {
            "areas": {
                "frontier_town": {
                    "board_bulletins": {
                        "main_board": [
                            {"board_id": "main_board", "quest_id": "dq_wolves", "title": "Wolf Pack"},
                        ]
                    }
                }
            }
        }
    )
    state.register(areas)

    quests = QuestSlice()
    quests.restore(
        {
            "dynamic_quests": {
                "dq_wolves": {
                    "status": "available",
                    "title": "Wolf Pack",
                    "summary": "Clear the wolves near the village.",
                    "objectives": [
                        {"description": "Kill 5 wolves"},
                        {"description": "Report back to the guild"},
                    ],
                    "rewards": {"gold": 200, "xp": 100},
                    "difficulty": "medium",
                }
            }
        }
    )
    state.register(quests)
    return state


def test_receptionist_prompt_includes_quest_objectives() -> None:
    state = _make_context_state_with_board()
    data = _extract_receptionist_data(state)

    bulletins = data.get("bulletins", [])
    assert len(bulletins) == 1
    bulletin = bulletins[0]
    assert bulletin["quest_id"] == "dq_wolves"
    assert "objectives" in bulletin
    assert bulletin["objectives"] == ["Kill 5 wolves", "Report back to the guild"]


def test_receptionist_prompt_includes_quest_rewards() -> None:
    state = _make_context_state_with_board()
    data = _extract_receptionist_data(state)

    bulletin = data["bulletins"][0]
    assert "rewards" in bulletin
    assert bulletin["rewards"].get("gold") == 200
    assert bulletin["rewards"].get("xp") == 100


def _make_merchant_state_with_shop(npc_id: str) -> StateContainer:
    state = StateContainer()
    player = PlayerSlice()
    player.restore({"current_area": "frontier_town"})
    state.register(player)

    from app.game_core.state.slices import RelationSlice
    relations = RelationSlice()
    shop_data = {
        "current_stock": [
            {"item_id": "sword_iron", "base_price": 50, "remaining": 3},
        ]
    }
    relations.restore({"shop_states": {npc_id: shop_data}})
    state.register(relations)
    return state


def _make_item_world() -> WorldInstance:
    world = WorldInstance("test_world")
    items = ItemRegistry()
    items.load(
        {
            "sword_iron": {
                "id": "sword_iron",
                "name": "Iron Sword",
                "type": "weapon",
                "rarity": "common",
                "description": "A sturdy iron sword, reliable if not glamorous.",
                "base_price": 50,
            }
        }
    )
    world.register(items)
    return world


def test_merchant_prompt_includes_item_name_and_type() -> None:
    npc_id = "blacksmith"
    state = _make_merchant_state_with_shop(npc_id)
    world = _make_item_world()
    data = _extract_merchant_data(npc_id, state, world)

    inv = data.get("inventory", [])
    assert len(inv) == 1
    item = inv[0]
    assert item["name"] == "Iron Sword"
    assert item["type"] == "weapon"
    assert item["rarity"] == "common"
    assert "description" in item
    assert len(item["description"]) <= 50


def test_merchant_prompt_without_world_has_no_name() -> None:
    npc_id = "blacksmith"
    state = _make_merchant_state_with_shop(npc_id)
    data = _extract_merchant_data(npc_id, state, None)

    inv = data.get("inventory", [])
    assert len(inv) == 1
    item = inv[0]
    assert "name" not in item  # no enrichment without world


def test_guard_prompt_includes_semantic_danger_label() -> None:
    state = StateContainer()
    player = PlayerSlice()
    player.restore({"current_area": "frontier_town"})
    state.register(player)

    areas = AreaSlice()
    areas.restore({"areas": {"frontier_town": {"danger_level": 2.5}}})
    state.register(areas)

    world = WorldInstance("test_world")
    chars = CharacterRegistry()
    chars.load(
        {
            "guard_npc": {
                "id": "guard_npc",
                "name": "Gate Guard",
                "area_id": "frontier_town",
                "tags": ["guard"],
            }
        }
    )
    world.register(chars)
    data = _extract_guard_data("guard_npc", state, world)

    assert data.get("danger_level") == 2.5
    assert data.get("danger_label") == "中等危险"


def test_danger_label_thresholds() -> None:
    assert _danger_label(0.0) == "安全"
    assert _danger_label(0.5) == "安全"
    assert _danger_label(1.0) == "低风险"
    assert _danger_label(1.5) == "低风险"
    assert _danger_label(2.0) == "中等危险"
    assert _danger_label(3.0) == "中等危险"
    assert _danger_label(4.0) == "高度危险"
    assert _danger_label(5.0) == "高度危险"
    assert _danger_label(6.0) == "极度危险"


# ---------------------------------------------------------------------------
# B-3: initial navigation for newly accepted quests
# ---------------------------------------------------------------------------

def test_active_quest_with_objectives_gets_initial_current_step() -> None:
    quest = normalize_dynamic_quest_view(
        "dq_wolves",
        {
            "status": "active",
            "title": "Wolf Pack",
            "summary": "Clear the wolves.",
            "objectives": [
                {"description": "Kill 5 wolves"},
                {"description": "Report back"},
                {"description": "Collect bounty"},
            ],
        },
    )

    assert quest["current_step"] == "Kill 5 wolves"
    assert "Report back" in quest["next_steps"]
    assert "Collect bounty" in quest["next_steps"]


def test_active_quest_with_no_objectives_falls_back_to_summary() -> None:
    quest = normalize_dynamic_quest_view(
        "dq_simple",
        {
            "status": "active",
            "title": "Simple Task",
            "summary": "Go to the market.",
        },
    )

    assert quest["current_step"] == "Go to the market."
    assert quest["next_steps"] == []


def test_active_quest_with_existing_current_step_not_overridden() -> None:
    """If current_step is already set, B-3 fallback should not override it."""
    quest = normalize_dynamic_quest_view(
        "dq_custom",
        {
            "status": "active",
            "title": "Custom",
            "summary": "Something.",
            "current_step": "My custom step",
            "objectives": [{"description": "First objective"}],
        },
    )

    assert quest["current_step"] == "My custom step"


# ---------------------------------------------------------------------------
# B-4: completed_summary for completed quests
# ---------------------------------------------------------------------------

def test_completed_quest_has_completed_summary() -> None:
    quest = normalize_dynamic_quest_view(
        "dq_done",
        {
            "status": "completed",
            "title": "Done Quest",
            "summary": "All done.",
            "objectives": [
                {"description": "Step one done"},
                {"description": "Step two done"},
            ],
            "rewards": {"gold": 150, "xp": 75},
        },
    )

    assert "completed_summary" in quest
    summary = quest["completed_summary"]
    assert summary["rewards"].get("gold") == 150
    assert "Step one done" in summary["completed_objectives"]
    assert "Step two done" in summary["completed_objectives"]


def test_active_quest_has_no_completed_summary() -> None:
    quest = normalize_dynamic_quest_view(
        "dq_active",
        {"status": "active", "title": "Active Quest"},
    )

    assert "completed_summary" not in quest


# ---------------------------------------------------------------------------
# B-5: story_facts injected into NPC prompt
# ---------------------------------------------------------------------------

def _make_story_facts_state() -> StateContainer:
    state = StateContainer()
    player = PlayerSlice()
    player.restore({"current_area": "frontier_town"})
    state.register(player)

    narrative = NarrativePlanSlice()
    narrative.restore(
        {
            "story_facts": [
                {"subject": "goblin_slayer", "predicate": "killed", "object": "50 goblins"},
                {"content": "The frontier town is under threat from a goblin horde."},
            ]
        }
    )
    state.register(narrative)
    return state


def _make_npc_world_with_npc(npc_id: str) -> WorldInstance:
    world = WorldInstance("test_world")
    chars = CharacterRegistry()
    chars.load(
        {
            npc_id: {
                "id": npc_id,
                "name": "Test NPC",
                "personality": "Helpful",
                "area_id": "frontier_town",
                "tags": [],
            }
        }
    )
    world.register(chars)
    return world


def test_story_facts_injected_into_npc_prompt() -> None:
    async def _run() -> None:
        state = _make_story_facts_state()
        world = _make_npc_world_with_npc("test_npc")
        builder = AgentContextBuilder(world, state)
        result = await builder.build_npc_full_context("test_npc")
        assert result is not None
        prompt = result.system_prompt
        # story_facts should appear in the prompt
        assert "## Relevant world knowledge" in prompt
        assert "goblin" in prompt.lower() or "frontier" in prompt.lower()

    asyncio.run(_run())


def test_no_story_facts_no_knowledge_block() -> None:
    async def _run() -> None:
        state = StateContainer()
        player = PlayerSlice()
        player.restore({"current_area": "frontier_town"})
        state.register(player)
        # No narrative_plan slice → no story_facts

        world = _make_npc_world_with_npc("test_npc")
        builder = AgentContextBuilder(world, state)
        result = await builder.build_npc_full_context("test_npc")
        assert result is not None
        prompt = result.system_prompt
        # No story_facts → no knowledge block
        assert "## Relevant world knowledge" not in prompt

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# B-6: receptionist relaxed accept (already covered in test_receptionist_handler.py)
# but add a direct test with no board at all
# ---------------------------------------------------------------------------

def test_receptionist_accept_quest_allows_available_quest_off_board() -> None:
    """B-6: Quest in dynamic_quests as 'available' but not on any board → accepted."""
    state = StateContainer()
    player = PlayerSlice()
    player.restore({"current_area": "guild_hall", "current_location": "counter"})
    state.register(player)

    areas = AreaSlice()
    # No board entries at all
    areas.restore({"areas": {"guild_hall": {"board_bulletins": {}}}})
    state.register(areas)

    quests = QuestSlice()
    quests.restore(
        {
            "dynamic_quests": {
                "dq_available_only": {
                    "status": "available",
                    "title": "Available Quest (no board)",
                }
            }
        }
    )
    state.register(quests)

    world = WorldInstance("test_world")
    chars = CharacterRegistry()
    chars.load(
        {
            "receptionist_npc": {
                "id": "receptionist_npc",
                "name": "Guild Clerk",
                "current_area": "guild_hall",
                "current_location": "counter",
                "tags": ["receptionist"],
            }
        }
    )
    world.register(chars)

    result = ReceptionistHandler().compute(
        Command(
            type="receptionist_accept_quest",
            params={"npc_id": "receptionist_npc", "quest_id": "dq_available_only"},
        ),
        state,
        world,
    )

    assert result.executed is True
    # board_id should be None (not on board)
    assert result.metadata.get("board_id") is None
