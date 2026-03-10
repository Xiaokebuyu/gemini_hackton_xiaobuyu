"""Tests for ItemDesigner runtime integration and directive application."""

from __future__ import annotations

import asyncio
from typing import Any

from app.game_core.adapters.planner_system import PlannerSystemAssembly
from app.game_core.bootstrap import build_narrative_planner_hook
from app.game_core.content import WorldInstance
from app.game_core.content.registries.characters import CharacterRegistry
from app.game_core.content.registries.items import ItemRegistry
from app.game_core.orchestration.hooks.narrative_planner import NarrativePlannerHook
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.planning.item_designer import ItemDesignerSubSystem
from app.game_core.planning.subsystem import PlannerEvent
from app.game_core.rules import RulesEngine
from app.game_core.rules.defaults import register_default_rules_handlers
from app.game_core.state import StateChange, StateContainer, StateDelta
from app.game_core.state.slices import (
    AreaSlice,
    EventSlice,
    FlagSlice,
    NarrativePlanSlice,
    PartySlice,
    PlayerSlice,
    QuestSlice,
    RelationSlice,
    SceneSlice,
    TimeSlice,
)
from app.interaction_service import build_interaction_view_context
from app.interaction_views import build_quest_reward_payload


class RecordingAgent:
    def __init__(self, directives: list[dict[str, Any]] | None = None) -> None:
        self.directives = list(directives or [])
        self.calls: list[dict[str, Any]] = []

    async def evaluate(self, context: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(dict(context))
        return {
            "directives": list(self.directives),
            "story_facts": [],
            "strategy_notes": "",
            "metadata": {},
        }


def _make_world() -> WorldInstance:
    world = WorldInstance("test_world")
    characters = CharacterRegistry()
    characters.load(
        {
            "blacksmith": {
                "id": "blacksmith",
                "name": "碎盾铁匠",
                "tags": ["merchant", "craftsman"],
                "shop_inventory": {
                    "base_pool": [
                        {"item_id": "cheap_shortsword", "count": "unlimited"},
                        {"item_id": "standard_longsword", "count": "3"},
                        {"item_id": "scouts_hand_axe", "count": "5"},
                        {"item_id": "heavy_war_hammer", "count": "2"},
                        {"item_id": "throwing_dagger", "count": "unlimited"},
                        {"item_id": "sturdy_spear", "count": "3"},
                        {"item_id": "leather_armor", "count": "unlimited"},
                        {"item_id": "dirty_chain_mail", "count": "2"},
                        {"item_id": "round_shield", "count": "unlimited"},
                        {"item_id": "hempen_rope", "count": "unlimited"},
                    ],
                    "rotating_pool": [
                        {"item_id": "battered_plate_armor", "count": "1", "min_player_level": 3},
                        {"item_id": "healing_potion", "count": "3"},
                        {"item_id": "basic_antidote", "count": "2"},
                        {"item_id": "holy_water_flask", "count": "1"},
                    ],
                    "rotating_slots": 2,
                },
            }
        }
    )
    items = ItemRegistry()
    items.load(
        {
            "cheap_shortsword": {
                "id": "cheap_shortsword",
                "name": "Cheap Shortsword",
                "type": "weapon",
                "rarity": "common",
                "base_price": 10,
                "slot": "main_hand",
                "damage_dice": "1d6",
                "damage_type": "piercing",
            },
            "throwing_dagger": {
                "id": "throwing_dagger",
                "name": "Throwing Dagger",
                "type": "weapon",
                "rarity": "common",
                "base_price": 2,
                "slot": "main_hand",
                "damage_dice": "1d4",
                "damage_type": "piercing",
            },
            "sturdy_spear": {
                "id": "sturdy_spear",
                "name": "Sturdy Spear",
                "type": "weapon",
                "rarity": "common",
                "base_price": 4,
                "slot": "main_hand",
                "damage_dice": "1d6",
                "damage_type": "piercing",
            },
            "standard_longsword": {
                "id": "standard_longsword",
                "name": "Standard Longsword",
                "type": "weapon",
                "rarity": "common",
                "base_price": 15,
                "slot": "main_hand",
                "damage_dice": "1d8",
                "damage_type": "slashing",
            },
            "scouts_hand_axe": {
                "id": "scouts_hand_axe",
                "name": "Scout's Hand Axe",
                "type": "weapon",
                "rarity": "common",
                "base_price": 5,
                "slot": "main_hand",
                "damage_dice": "1d6",
                "damage_type": "slashing",
            },
            "heavy_war_hammer": {
                "id": "heavy_war_hammer",
                "name": "Heavy War Hammer",
                "type": "weapon",
                "rarity": "uncommon",
                "base_price": 20,
                "slot": "main_hand",
                "damage_dice": "1d8",
                "damage_type": "bludgeoning",
            },
            "leather_armor": {
                "id": "leather_armor",
                "name": "Leather Armor",
                "type": "armor",
                "rarity": "common",
                "base_price": 10,
                "slot": "body",
                "subtype": "light",
                "ac_bonus": 1,
            },
            "dirty_chain_mail": {
                "id": "dirty_chain_mail",
                "name": "Dirty Chain Mail",
                "type": "armor",
                "rarity": "common",
                "base_price": 75,
                "slot": "body",
                "subtype": "medium",
                "ac_bonus": 6,
            },
            "healing_potion": {
                "id": "healing_potion",
                "name": "Healing Potion",
                "type": "consumable",
                "rarity": "common",
                "base_price": 50,
            },
            "basic_antidote": {
                "id": "basic_antidote",
                "name": "Basic Antidote",
                "type": "consumable",
                "rarity": "common",
                "base_price": 25,
            },
            "sulfur_smoke_ball": {
                "id": "sulfur_smoke_ball",
                "name": "Sulfur Smoke Ball",
                "type": "consumable",
                "rarity": "uncommon",
                "base_price": 30,
            },
            "holy_water_flask": {
                "id": "holy_water_flask",
                "name": "Holy Water Flask",
                "type": "consumable",
                "rarity": "uncommon",
                "base_price": 25,
            },
            "round_shield": {
                "id": "round_shield",
                "name": "Round Shield",
                "type": "armor",
                "rarity": "common",
                "base_price": 40,
                "subtype": "shield",
                "ac_bonus": 2,
            },
            "battered_plate_armor": {
                "id": "battered_plate_armor",
                "name": "Battered Plate Armor",
                "type": "armor",
                "rarity": "uncommon",
                "base_price": 200,
                "slot": "body",
                "subtype": "heavy",
                "ac_bonus": 8,
            },
            "hempen_rope": {
                "id": "hempen_rope",
                "name": "Hempen Rope",
                "type": "misc",
                "rarity": "common",
                "base_price": 2,
            },
            "iron_spikes": {
                "id": "iron_spikes",
                "name": "Iron Spikes",
                "type": "misc",
                "rarity": "common",
                "base_price": 1,
            },
        }
    )
    world.register(characters)
    world.register(items)
    return world


def _make_context(
    *,
    dynamic_quests: dict[str, dict[str, Any]] | None = None,
    shop_states: dict[str, dict[str, Any]] | None = None,
    change_log: list[StateChange] | None = None,
    player_payload: dict[str, Any] | None = None,
) -> SettlementContext:
    world = _make_world()
    state = StateContainer()

    time_slice = TimeSlice()
    time_slice.restore({"day": 1, "slot": 9})
    state.register(time_slice)

    player = PlayerSlice()
    player.restore(
        {
            "current_area": "forest",
            "current_location": None,
            "level": 1,
            "hp": 10,
            "max_hp": 10,
            "ac": 10,
            "character_class": "fighter",
            "stats": {"str": 12, "dex": 12, "con": 12},
            **(player_payload or {}),
        }
    )
    state.register(player)

    quests = QuestSlice()
    quests.restore(
        {
            "milestone_states": {},
            "dynamic_quests": dynamic_quests or {},
            "chapter_completion": {},
        }
    )
    state.register(quests)

    relations = RelationSlice()
    relations.restore({"shop_states": shop_states or {}})
    state.register(relations)

    narrative_plan = NarrativePlanSlice()
    narrative_plan.restore({"current_chapter": "chapter_1", "last_run_tick": 0})
    state.register(narrative_plan)

    areas = AreaSlice()
    areas.restore(
        {
            "areas": {
                "forest": {
                    "danger_level": 0.2,
                    "npc_locations": {"blacksmith": "forge"},
                    "board_bulletins": {},
                }
            }
        }
    )
    state.register(areas)

    flags = FlagSlice()
    flags.restore({})
    state.register(flags)

    events = EventSlice()
    events.restore({})
    state.register(events)

    party = PartySlice()
    party.restore({})
    state.register(party)

    scene = SceneSlice()
    scene.restore({})
    state.register(scene)
    scene_bus = SceneBus(scene)

    active_change_log = list(change_log or [])

    def _apply_delta(delta: StateDelta | None) -> None:
        if delta is None:
            return
        state.apply(delta)
        active_change_log.extend(delta.changes)
        for change in delta.changes:
            scene_bus.record_state_change(change)

    rules_engine = RulesEngine()
    register_default_rules_handlers(rules_engine)

    return SettlementContext(
        change_log=active_change_log,
        state=state,
        world=world,
        scene_bus=scene_bus,
        _rules_engine=rules_engine,
        _apply_delta=_apply_delta,
    )


def test_accepts_item_designer_events_only() -> None:
    subsystem = ItemDesignerSubSystem()

    assert subsystem.accepts_event(PlannerEvent(kind="quest_created", tick=1)) is True
    assert subsystem.accepts_event(PlannerEvent(kind="quest_accepted", tick=1)) is True
    assert subsystem.accepts_event(PlannerEvent(kind="shop_refreshed", tick=1)) is True
    assert subsystem.accepts_event(PlannerEvent(kind="quest_completed", tick=1)) is False
    assert subsystem.accepts_event(PlannerEvent(kind="shop_inventory_changed", tick=1)) is False


def test_design_reward_writes_canonical_reward_fields_only() -> None:
    context = _make_context(
        dynamic_quests={
            "dq_reward": {
                "quest_id": "dq_reward",
                "status": "available",
                "title": "Resupply",
                "summary": "Fetch supplies.",
                "rewards": {"gold": 25},
            }
        }
    )
    subsystem = ItemDesignerSubSystem()

    result = subsystem.apply_directive(
        "design_reward",
        {
            "linked_quest_id": "dq_reward",
            "item_id": "healing_potion",
            "quantity": 2,
            "title": "Field Supplies",
        },
        context,
        current_tick=9,
    )

    assert result is True
    quest = context.state.quests.dynamic_quests["dq_reward"]
    assert quest["rewards"]["gold"] == 25
    assert quest["rewards"]["items"] == [{"item_id": "healing_potion", "count": 2}]
    assert "reward_items" not in quest
    assert "reward_summary" not in quest
    assert context.state.narrative_plan.quest_history[-1]["kind"] == "design_reward"
    assert {
        change.path for change in context.change_log if change.slice == "quests"
    } >= {"dynamic_quests.dq_reward.rewards"}


def test_design_reward_merges_same_item_without_duplicate_entries() -> None:
    context = _make_context(
        dynamic_quests={
            "dq_reward": {
                "quest_id": "dq_reward",
                "status": "active",
                "title": "Resupply",
                "summary": "Fetch supplies.",
                "rewards": {"items": [{"item_id": "healing_potion", "count": 1}]},
            }
        }
    )
    subsystem = ItemDesignerSubSystem()

    result = subsystem.apply_directive(
        "design_reward",
        {
            "linked_quest_id": "dq_reward",
            "item_id": "healing_potion",
            "quantity": 2,
        },
        context,
        current_tick=9,
    )

    assert result is True
    reward_items = context.state.quests.dynamic_quests["dq_reward"]["rewards"]["items"]
    assert reward_items == [{"item_id": "healing_potion", "count": 3}]


def test_design_reward_rejects_closed_quest_and_invalid_payload() -> None:
    context = _make_context(
        dynamic_quests={
            "dq_closed": {
                "quest_id": "dq_closed",
                "status": "completed",
                "title": "Done",
                "summary": "Closed quest.",
            }
        }
    )
    subsystem = ItemDesignerSubSystem()

    assert subsystem.apply_directive(
        "design_reward",
        {
            "linked_quest_id": "dq_closed",
            "item_id": "healing_potion",
            "quantity": 1,
        },
        context,
        current_tick=9,
    ) is False
    assert subsystem.apply_directive(
        "design_reward",
        {
            "linked_quest_id": "dq_missing",
            "item_id": "healing_potion",
            "quantity": 1,
        },
        context,
        current_tick=9,
    ) is False
    assert subsystem.apply_directive(
        "design_reward",
        {
            "linked_quest_id": "dq_closed",
            "item_id": "unknown_item",
            "quantity": 0,
        },
        context,
        current_tick=9,
    ) is False


def test_reward_candidates_respect_level_and_class_guardrails() -> None:
    context = _make_context(
        dynamic_quests={
            "dq_hunt": {
                "quest_id": "dq_hunt",
                "status": "available",
                "title": "Goblin Hunt",
                "summary": "Slay the goblins in the ruined watchtower.",
            }
        },
        player_payload={
            "level": 2,
            "character_class": "rogue",
            "stats": {"str": 12, "dex": 14, "con": 12},
            "equipment": {
                "main_hand": {"item_id": "cheap_shortsword"},
                "off_hand": {"item_id": "round_shield"},
            },
        },
    )
    subsystem = ItemDesignerSubSystem()
    player_snapshot = subsystem._build_player_snapshot(context)

    candidates = subsystem._build_reward_candidates(
        context,
        context.state.quests.dynamic_quests["dq_hunt"],
        player_snapshot=player_snapshot,
    )

    candidate_ids = {entry["item_id"] for entry in candidates}
    assert "standard_longsword" not in candidate_ids
    assert "scouts_hand_axe" not in candidate_ids
    assert "dirty_chain_mail" not in candidate_ids
    assert "heavy_war_hammer" not in candidate_ids
    assert "round_shield" not in candidate_ids
    assert "hempen_rope" not in candidate_ids


def test_reward_candidates_reflect_player_state_and_quest_semantics() -> None:
    context = _make_context(
        dynamic_quests={
            "dq_poison": {
                "quest_id": "dq_poison",
                "status": "available",
                "title": "Poisoned Shrine",
                "summary": "Survive the toxic shrine and purge the cursed venom.",
            }
        },
        player_payload={
            "level": 3,
            "hp": 4,
            "max_hp": 10,
            "inventory": [],
            "equipment": {
                "main_hand": {"item_id": "cheap_shortsword"},
            },
        },
    )
    subsystem = ItemDesignerSubSystem()
    player_snapshot = subsystem._build_player_snapshot(context)

    candidates = subsystem._build_reward_candidates(
        context,
        context.state.quests.dynamic_quests["dq_poison"],
        player_snapshot=player_snapshot,
    )

    by_id = {entry["item_id"]: entry for entry in candidates}
    assert "healing_potion" in by_id
    assert "basic_antidote" in by_id
    assert by_id["basic_antidote"]["quantity"] == 2


def test_reward_progression_guardrails_use_rarity_and_price_caps() -> None:
    subsystem = ItemDesignerSubSystem()

    assert subsystem._reward_progression_guardrail_reason(
        item_type="weapon",
        rarity="uncommon",
        base_price=20,
        level=2,
    ) == "item_rarity_disallowed"
    assert subsystem._reward_progression_guardrail_reason(
        item_type="armor",
        rarity="common",
        base_price=90,
        level=4,
    ) == "item_price_too_high"
    assert subsystem._reward_progression_guardrail_reason(
        item_type="consumable",
        rarity="rare",
        base_price=999,
        level=1,
    ) is None


def test_reward_candidates_empty_when_quest_already_has_item_reward() -> None:
    context = _make_context(
        dynamic_quests={
            "dq_rewarded": {
                "quest_id": "dq_rewarded",
                "status": "available",
                "title": "Resupply",
                "summary": "Supplies are already assigned.",
                "rewards": {"items": [{"item_id": "healing_potion", "count": 1}]},
            }
        }
    )
    subsystem = ItemDesignerSubSystem()
    player_snapshot = subsystem._build_player_snapshot(context)

    candidates = subsystem._build_reward_candidates(
        context,
        context.state.quests.dynamic_quests["dq_rewarded"],
        player_snapshot=player_snapshot,
    )

    assert candidates == []


def test_curate_shop_reuses_existing_stock_row_and_validates_item_ids() -> None:
    context = _make_context(
        shop_states={
            "blacksmith": {
                "npc_id": "blacksmith",
                "current_stock": [
                    {
                        "item_id": "healing_potion",
                        "base_price": 50,
                        "remaining": 1,
                        "source": "base",
                    }
                ],
                "last_refresh_tick": 0,
            }
        }
    )
    subsystem = ItemDesignerSubSystem()

    result = subsystem.apply_directive(
        "curate_shop",
        {
            "npc_id": "blacksmith",
            "add_items": [
                {"item_id": "healing_potion", "count": 2, "price_override": 99}
            ],
        },
        context,
        current_tick=9,
    )

    assert result is True
    stock = context.state.relations.shop_states["blacksmith"]["current_stock"]
    assert len(stock) == 1
    assert stock[0]["item_id"] == "healing_potion"
    assert stock[0]["remaining"] == 3
    assert stock[0]["base_price"] == 99
    assert subsystem.apply_directive(
        "curate_shop",
        {
            "npc_id": "blacksmith",
            "add_items": [{"item_id": "unknown_item", "count": 1}],
        },
        context,
        current_tick=10,
    ) is False


def test_reward_view_reads_canonical_rewards_when_legacy_alias_missing() -> None:
    context = _make_context(
        dynamic_quests={
            "dq_reward": {
                "quest_id": "dq_reward",
                "status": "available",
                "title": "Resupply",
                "summary": "Fetch supplies.",
                "rewards": {"items": [{"item_id": "healing_potion", "count": 2}]},
            }
        }
    )
    view_context = build_interaction_view_context(context.state, context.world)

    payload = build_quest_reward_payload(view_context, "blacksmith", "dq_reward")

    assert payload["quest"]["reward_known"] is True
    assert payload["quest"]["items"] == [
        {
            "item_id": "healing_potion",
            "count": 2,
            "name": "Healing Potion",
            "type": "consumable",
            "rarity": "common",
        }
    ]


def test_reward_view_falls_back_when_item_catalog_entry_is_missing() -> None:
    context = _make_context(
        dynamic_quests={
            "dq_reward": {
                "quest_id": "dq_reward",
                "status": "available",
                "title": "Resupply",
                "summary": "Fetch supplies.",
                "rewards": {"items": [{"item_id": "mystery_token", "count": 1}]},
            }
        }
    )
    view_context = build_interaction_view_context(context.state, context.world)

    payload = build_quest_reward_payload(view_context, "blacksmith", "dq_reward")

    assert payload["quest"]["items"] == [
        {
            "item_id": "mystery_token",
            "count": 1,
            "name": "mystery_token",
            "type": "",
            "rarity": "",
        }
    ]


def test_build_hook_wires_item_designer_agent() -> None:
    agent = RecordingAgent()
    context = _make_context()
    hook = build_narrative_planner_hook(
        PlannerSystemAssembly(item_designer_agent=agent),
        state=context.state,
    )

    subsystem = next(
        sub for sub in hook._dispatcher.subsystems if sub.name == "item_designer"
    )

    assert isinstance(subsystem, ItemDesignerSubSystem)
    assert subsystem._agent is agent


def test_hook_applies_design_reward_from_item_agent_on_quest_created() -> None:
    quest_payload = {
        "quest_id": "dq_reward",
        "status": "available",
        "title": "Resupply",
        "summary": "Fetch supplies.",
        "created_at_tick": 9,
    }
    context = _make_context(
        dynamic_quests={"dq_reward": quest_payload},
        change_log=[
            StateChange("quests", "set", "dynamic_quests.dq_reward", quest_payload),
        ],
    )
    agent = RecordingAgent(
        directives=[
            {
                "kind": "design_reward",
                "payload": {
                    "linked_quest_id": "dq_reward",
                    "item_id": "healing_potion",
                    "quantity": 1,
                },
            }
        ]
    )
    hook = build_narrative_planner_hook(
        PlannerSystemAssembly(item_designer_agent=agent),
        state=context.state,
    )

    result = asyncio.run(hook.execute(context))

    assert result.metadata["status"] == "updated"
    assert "design_reward" in result.metadata["applied_kinds"]
    assert context.state.quests.dynamic_quests["dq_reward"]["rewards"]["items"] == [
        {"item_id": "healing_potion", "count": 1}
    ]
    assert agent.calls[0]["current_event"]["kind"] == "quest_created"
    assert agent.calls[0]["quest_snapshot"]["quest_id"] == "dq_reward"
    assert agent.calls[0]["player_snapshot"]["level"] == 1
    assert agent.calls[0]["reward_candidates"]
    assert {
        entry["item_id"] for entry in agent.calls[0]["available_items"]
    } == {
        entry["item_id"] for entry in agent.calls[0]["reward_candidates"]
    }


def test_hook_rejects_design_reward_outside_reward_candidates() -> None:
    quest_payload = {
        "quest_id": "dq_reward",
        "status": "available",
        "title": "Resupply",
        "summary": "Fetch supplies.",
        "created_at_tick": 9,
    }
    context = _make_context(
        dynamic_quests={"dq_reward": quest_payload},
        change_log=[
            StateChange("quests", "set", "dynamic_quests.dq_reward", quest_payload),
        ],
    )
    agent = RecordingAgent(
        directives=[
            {
                "kind": "design_reward",
                "payload": {
                    "linked_quest_id": "dq_reward",
                    "item_id": "heavy_war_hammer",
                    "quantity": 1,
                },
            }
        ]
    )
    hook = build_narrative_planner_hook(
        PlannerSystemAssembly(item_designer_agent=agent),
        state=context.state,
    )

    result = asyncio.run(hook.execute(context))

    assert result.metadata["status"] == "updated"
    assert "design_reward" not in result.metadata["applied_kinds"]
    assert "rewards" not in context.state.quests.dynamic_quests["dq_reward"]
    trace = context.state.narrative_plan.last_planner_replay_trace
    item_summary = next(
        summary
        for summary in trace["rounds"][0]["subsystems"]
        if summary["name"] == "item_designer"
    )
    assert item_summary["metadata"]["filtered_directive_count"] == 1
    assert item_summary["metadata"]["rejected_directives"][0]["reason_code"] == "item_rarity_disallowed"


def test_hook_records_noop_reason_when_quest_already_has_item_reward() -> None:
    quest_payload = {
        "quest_id": "dq_rewarded",
        "status": "available",
        "title": "Resupply",
        "summary": "Supplies are already assigned.",
        "rewards": {"items": [{"item_id": "healing_potion", "count": 1}]},
        "created_at_tick": 9,
    }
    context = _make_context(
        dynamic_quests={"dq_rewarded": quest_payload},
        change_log=[
            StateChange("quests", "set", "dynamic_quests.dq_rewarded", quest_payload),
        ],
    )
    agent = RecordingAgent(directives=[])
    hook = build_narrative_planner_hook(
        PlannerSystemAssembly(item_designer_agent=agent),
        state=context.state,
    )

    result = asyncio.run(hook.execute(context))

    assert "design_reward" not in result.metadata["applied_kinds"]
    trace = context.state.narrative_plan.last_planner_replay_trace
    item_summary = next(
        summary
        for summary in trace["rounds"][0]["subsystems"]
        if summary["name"] == "item_designer"
    )
    assert item_summary["metadata"]["candidate_summary"] == {
        "reward_candidate_count": 0,
        "shop_candidate_count": 0,
    }
    assert item_summary["metadata"]["noop_reason"] == "quest_already_has_item_reward"


def test_hook_applies_curate_shop_from_item_agent_on_shop_refresh() -> None:
    context = _make_context(
        shop_states={
            "blacksmith": {
                "npc_id": "blacksmith",
                "current_stock": [],
                "last_refresh_tick": 0,
            }
        }
    )
    context.action_log = [
        {
            "type": "refresh_shop",
            "params": {"npc_id": "blacksmith"},
            "executed": True,
        }
    ]
    agent = RecordingAgent(
        directives=[
            {
                "kind": "curate_shop",
                "payload": {
                    "npc_id": "blacksmith",
                    "add_items": [{"item_id": "healing_potion", "count": 3}],
                },
            }
        ]
    )
    hook = build_narrative_planner_hook(
        PlannerSystemAssembly(item_designer_agent=agent),
        state=context.state,
    )

    result = asyncio.run(hook.execute(context))

    stock = context.state.relations.shop_states["blacksmith"]["current_stock"]
    assert result.metadata["status"] == "updated"
    assert "curate_shop" in result.metadata["applied_kinds"]
    assert stock[0]["item_id"] == "healing_potion"
    assert agent.calls[0]["current_event"]["kind"] == "shop_refreshed"
    assert agent.calls[0]["shop_snapshot"]["npc_id"] == "blacksmith"
    assert agent.calls[0]["merchant_profile"]["merchant_archetype"] == "blacksmith_like"
    assert agent.calls[0]["shop_candidates"]
    available_item_ids = {entry["item_id"] for entry in agent.calls[0]["available_items"]}
    assert "cheap_shortsword" in available_item_ids
    assert "sulfur_smoke_ball" not in available_item_ids


def test_shop_candidates_only_use_rotating_pool_and_skip_blacksmith_outliers() -> None:
    context = _make_context(
        shop_states={
            "blacksmith": {
                "npc_id": "blacksmith",
                "current_stock": [],
                "last_refresh_tick": 0,
            }
        },
        player_payload={
            "level": 3,
            "hp": 6,
            "max_hp": 10,
            "inventory": [],
            "equipment": {
                "main_hand": {"item_id": "cheap_shortsword"},
            },
        },
    )
    subsystem = ItemDesignerSubSystem()
    player_snapshot = subsystem._build_player_snapshot(context)
    merchant_profile = subsystem._build_merchant_profile(
        context,
        "blacksmith",
        player_snapshot=player_snapshot,
    )

    candidates = subsystem._build_shop_candidates(
        context,
        "blacksmith",
        context.state.relations.shop_states["blacksmith"],
        player_snapshot=player_snapshot,
        merchant_profile=merchant_profile,
    )

    candidate_ids = {entry["item_id"] for entry in candidates}
    assert candidate_ids <= {"healing_potion", "basic_antidote"}
    assert "battered_plate_armor" not in candidate_ids
    assert "cheap_shortsword" not in candidate_ids
    assert "holy_water_flask" not in candidate_ids


def test_hook_rejects_curate_shop_outside_shop_candidates() -> None:
    context = _make_context(
        shop_states={
            "blacksmith": {
                "npc_id": "blacksmith",
                "current_stock": [],
                "last_refresh_tick": 0,
            }
        }
    )
    context.action_log = [
        {
            "type": "refresh_shop",
            "params": {"npc_id": "blacksmith"},
            "executed": True,
        }
    ]
    agent = RecordingAgent(
        directives=[
            {
                "kind": "curate_shop",
                "payload": {
                    "npc_id": "blacksmith",
                    "add_items": [{"item_id": "round_shield", "count": 1}],
                },
            }
        ]
    )
    hook = build_narrative_planner_hook(
        PlannerSystemAssembly(item_designer_agent=agent),
        state=context.state,
    )

    result = asyncio.run(hook.execute(context))

    assert result.metadata["status"] == "updated"
    assert "curate_shop" not in result.metadata["applied_kinds"]
    assert context.state.relations.shop_states["blacksmith"]["current_stock"] == []
    trace = context.state.narrative_plan.last_planner_replay_trace
    item_summary = next(
        summary
        for summary in trace["rounds"][0]["subsystems"]
        if summary["name"] == "item_designer"
    )
    assert item_summary["metadata"]["filtered_directive_count"] == 1
    assert item_summary["metadata"]["rejected_directives"][0]["reason_code"] == "candidate_not_allowed"


def test_supported_directives_include_design_reward() -> None:
    assert "design_reward" in NarrativePlannerHook._SUPPORTED_DIRECTIVES
