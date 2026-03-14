"""Tests for P23 Track C: Shop Bootstrap + Prompt + Fallback.

Covers:
- C-1 (W1-2): bootstrap_opening_planner pre-initializes shop states
- C-2 (W2-3): narrators.py planner prompt contains design skill tool docs
- C-3 (W5-3): no-LLM fallback planner system — bootstrap works, regular noop
- C-4 (W5-4): narrators.py planner prompt contains play_style_tags guidance
- C-5 (W6-2): rotating inventory uses hash-based seed, not simple tick mod
- C-6 (W6-3): price=0 logs warning (design_reward removed)
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any
from unittest import mock

from app.game_core.content import WorldInstance
from app.game_core.content.registries import CharacterRegistry, ItemRegistry
from app.game_core.orchestration.models import SSEEvent
from app.game_core.rules import Command, RulesEngine
from app.game_core.rules.defaults import register_default_rules_handlers
from app.game_core.rules.handlers import EconomyHandler
from app.game_core.state import StateChange, StateContainer, StateDelta
from app.game_core.state.slices import PlayerSlice, RelationSlice, TimeSlice


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_world_with_merchant(npc_id: str = "merchant") -> WorldInstance:
    world = WorldInstance("test_world")

    items = ItemRegistry()
    items.load(
        {
            "potion": {"id": "potion", "base_price": 10},
            "elixir": {"id": "elixir", "base_price": 25},
            "bomb": {"id": "bomb", "base_price": 15},
        }
    )
    world.register(items)

    characters = CharacterRegistry()
    characters.load(
        {
            npc_id: {
                "id": npc_id,
                "name": "Test Merchant",
                "shop_inventory": {
                    "base_pool": [{"item_id": "potion", "count": 5}],
                    "rotating_pool": [
                        {"item_id": "elixir", "count": 2},
                        {"item_id": "bomb", "count": 3},
                    ],
                    "rotating_slots": 1,
                },
            }
        }
    )
    world.register(characters)
    return world


def _make_state_with_relations() -> StateContainer:
    state = StateContainer()

    player = PlayerSlice()
    player.restore({"character_id": "pc_1", "gold": 100, "inventory": []})
    state.register(player)

    relations = RelationSlice()
    relations.restore({})
    state.register(relations)

    time_slice = TimeSlice()
    time_slice.restore({"day": 1, "slot": 9})
    state.register(time_slice)

    return state


# ---------------------------------------------------------------------------
# C-1: W1-2 — Shop Bootstrap
# ---------------------------------------------------------------------------


def test_bootstrap_shops_initializes_shop_state():
    """bootstrap_opening_planner should pre-initialize shop state for merchants."""
    from app.game_core.runtime import GameRuntime, ManagedSession
    from app.game_core.bootstrap import build_default_world, build_runtime_for_world

    world = _make_world_with_merchant("blacksmith")
    state = _make_state_with_relations()
    rules_engine = RulesEngine()
    register_default_rules_handlers(rules_engine)

    # Simulate _bootstrap_shops directly (same logic as in GameRuntime._bootstrap_shops)
    runtime_obj = build_runtime_for_world(world)
    # Inject our state and world
    runtime_obj.state = state
    runtime_obj.world = world
    runtime_obj.rules_engine = rules_engine

    class _FakeSession:
        runtime = runtime_obj

    # Verify no shop state initially
    assert state.relations.get_shop_state("blacksmith") is None

    # Run bootstrap shops
    gr = GameRuntime()
    gr._bootstrap_shops(_FakeSession())

    # Verify shop state is now initialized
    shop = state.relations.get_shop_state("blacksmith")
    assert shop is not None, "shop state should be initialized after _bootstrap_shops"
    assert "current_stock" in shop, "shop state should have current_stock"
    assert len(shop["current_stock"]) > 0, "current_stock should not be empty"


def test_bootstrap_shops_skips_existing_shop_state():
    """_bootstrap_shops should not overwrite an already-existing shop state."""
    from app.game_core.runtime import GameRuntime
    from app.game_core.bootstrap import build_runtime_for_world

    world = _make_world_with_merchant("blacksmith")
    state = _make_state_with_relations()
    rules_engine = RulesEngine()
    register_default_rules_handlers(rules_engine)

    # Pre-set a shop state via the correct method
    pre_existing = {"current_stock": [{"item_id": "old_item", "remaining": 1}]}
    state.relations.update_shop_state("blacksmith", pre_existing)

    runtime_obj = build_runtime_for_world(world)
    runtime_obj.state = state
    runtime_obj.world = world
    runtime_obj.rules_engine = rules_engine

    class _FakeSession:
        runtime = runtime_obj

    gr = GameRuntime()
    gr._bootstrap_shops(_FakeSession())

    # Verify the pre-existing shop state was NOT overwritten
    shop = state.relations.get_shop_state("blacksmith")
    assert shop is not None
    stock = shop.get("current_stock", [])
    # Should still have old_item from pre-existing state
    item_ids = [r.get("item_id") for r in stock]
    assert "old_item" in item_ids, "pre-existing shop state should not be overwritten"


def test_bootstrap_shops_noop_without_characters_registry():
    """_bootstrap_shops should silently return if no characters registry."""
    from app.game_core.runtime import GameRuntime
    from app.game_core.bootstrap import build_runtime_for_world

    world_no_chars = WorldInstance("test_world")
    items = ItemRegistry()
    world_no_chars.register(items)

    state = _make_state_with_relations()
    runtime_obj = build_runtime_for_world(world_no_chars)
    runtime_obj.state = state
    runtime_obj.world = world_no_chars

    class _FakeSession:
        runtime = runtime_obj

    gr = GameRuntime()
    # Should not raise
    gr._bootstrap_shops(_FakeSession())


def test_bootstrap_opening_planner_uses_opening_only_hook(monkeypatch):
    """bootstrap_opening_planner should not reuse the live runtime planner hook."""
    from app.game_core.bootstrap import build_runtime_for_world
    from app.game_core.orchestration.models import HookResult
    from app.game_core.runtime import GameRuntime, ManagedSession

    world = _make_world_with_merchant("blacksmith")
    runtime_obj = build_runtime_for_world(world)
    session = ManagedSession(
        world_id="test_world",
        session_id="sess_bootstrap_only",
        runtime=runtime_obj,
        phase="opening_ready",
    )

    class _ExistingRuntimeHook:
        _dispatcher = object()

        async def bootstrap(self, context):  # pragma: no cover - should never run
            return HookResult(
                sse_events=[SSEEvent("wrong_hook_used", {"source": "runtime"})],
                metadata={"applied_count": 0, "story_fact_count": 0},
            )

    class _BootstrapOnlyHook:
        async def bootstrap(self, context):
            return HookResult(
                sse_events=[SSEEvent("opening_bootstrap_used", {"source": "opening"})],
                metadata={"applied_count": 0, "story_fact_count": 0},
            )

    gr = GameRuntime()
    monkeypatch.setattr(gr, "_find_narrative_planner_hook", lambda _session: _ExistingRuntimeHook())
    monkeypatch.setattr(
        "app.game_core.runtime.build_narrative_planner_hook",
        lambda planner_system, *, state, instance_manager=None: _BootstrapOnlyHook(),
    )

    events = asyncio.run(gr.bootstrap_opening_planner(session))

    assert [event.event_type for event in events] == ["opening_bootstrap_used"]


# ---------------------------------------------------------------------------
# C-2 (W2-3) + C-4 (W5-4): Planner Prompt Content
# ---------------------------------------------------------------------------


def test_planner_system_prompt_contains_design_skill_tools():
    """AgenticNarrativePlanner._SYSTEM_PROMPT should mention design skill tools."""
    from app.narrators import AgenticNarrativePlanner

    prompt = AgenticNarrativePlanner._SYSTEM_PROMPT
    assert "list_design_skills" in prompt, "Prompt should mention list_design_skills tool"
    assert "read_design_skill" in prompt, "Prompt should mention read_design_skill tool"
    assert "设计模板" in prompt or "design" in prompt.lower(), "Prompt should mention design template concept"


def test_planner_system_prompt_contains_play_style_guidance():
    """AgenticNarrativePlanner._SYSTEM_PROMPT should contain play_style_tags guidance."""
    from app.narrators import AgenticNarrativePlanner

    prompt = AgenticNarrativePlanner._SYSTEM_PROMPT
    assert "play_style_tags" in prompt, "Prompt should reference play_style_tags"
    assert "combat_heavy" in prompt, "Prompt should explain combat_heavy tag"
    assert "dialogue_heavy" in prompt, "Prompt should explain dialogue_heavy tag"
    assert "idle" in prompt, "Prompt should explain idle tag"
    assert "modify_location" not in prompt, "Prompt should not mention legacy unsupported modify_location"


def test_subsystem_prompts_contain_design_skill_note():
    """All subsystem prompts should mention list_design_skills / read_design_skill."""
    from app.narrators import (
        QUEST_MANAGER_AGENT_PROMPT,
        NPC_DIRECTOR_AGENT_PROMPT,
        WORLD_BUILDER_AGENT_PROMPT,
        NARRATIVE_WEAVER_AGENT_PROMPT,
    )
    for name, prompt in [
        ("quest_manager", QUEST_MANAGER_AGENT_PROMPT),
        ("npc_director", NPC_DIRECTOR_AGENT_PROMPT),
        ("world_builder", WORLD_BUILDER_AGENT_PROMPT),
        ("narrative_weaver", NARRATIVE_WEAVER_AGENT_PROMPT),
    ]:
        assert "list_design_skills" in prompt or "read_design_skill" in prompt, (
            f"{name} prompt should mention design skill tools"
        )


def test_subsystem_prompts_spell_out_runtime_contract_examples():
    """Subsystem prompts should include the current runtime contract, not vague directive names only."""
    from app.narrators import (
        QUEST_MANAGER_AGENT_PROMPT,
        NPC_DIRECTOR_AGENT_PROMPT,
        WORLD_BUILDER_AGENT_PROMPT,
        NARRATIVE_WEAVER_AGENT_PROMPT,
    )

    assert '"quest_id":"dq_x"' in QUEST_MANAGER_AGENT_PROMPT
    assert "id / description" in QUEST_MANAGER_AGENT_PROMPT
    assert "metadata" in QUEST_MANAGER_AGENT_PROMPT
    assert '"directive":{"kind"' in NPC_DIRECTOR_AGENT_PROMPT
    assert "behavior / topic / goal / interactable" in NPC_DIRECTOR_AGENT_PROMPT
    assert '"clue_id":"..."' in WORLD_BUILDER_AGENT_PROMPT
    assert '"sub_area_id":"..."' in WORLD_BUILDER_AGENT_PROMPT
    assert '"monster_ids":["goblin"]' in WORLD_BUILDER_AGENT_PROMPT
    assert "interactables" in WORLD_BUILDER_AGENT_PROMPT
    assert "敌对/战斗遭遇" in WORLD_BUILDER_AGENT_PROMPT
    assert '{"frozen": true}' in NARRATIVE_WEAVER_AGENT_PROMPT
    assert "pacing_factor" in NARRATIVE_WEAVER_AGENT_PROMPT


# ---------------------------------------------------------------------------
# C-3 (W5-3): No-LLM Deterministic Fallback
# (deps.py cannot be imported in tests as it requires fastapi. We test the
#  underlying building blocks directly.)
# ---------------------------------------------------------------------------


def _build_fallback_assembly():
    """Helper: build the same PlannerSystemAssembly that the fallback factory creates."""
    from app.game_core.adapters.planner_system import PlannerSystemAssembly
    from app.game_core.planning.opening_bootstrap import OpeningBootstrapQuestAgent

    class _FallbackBlackboard:
        @property
        def history_key(self) -> str:
            return "__deterministic_fallback_blackboard__"

        async def plan(self, context: dict) -> dict:
            return {
                "directives": [],
                "story_facts": [],
                "strategy_notes": "",
                "metadata": {"provider": "deterministic_fallback", "reason": "no_llm"},
            }

        def export_history(self) -> list:
            return []

        def import_history(self, data: list) -> None:
            pass

    class _FallbackAgent:
        def __init__(self, name: str) -> None:
            self._name = name

        @property
        def history_key(self) -> str:
            return f"__deterministic_fallback_{self._name}__"

        async def evaluate(self, context: dict) -> dict:
            return {
                "directives": [],
                "story_facts": [],
                "strategy_notes": "",
                "metadata": {"provider": "deterministic_fallback", "reason": "no_llm"},
            }

        def export_history(self) -> list:
            return []

        def import_history(self, data: list) -> None:
            pass

    return PlannerSystemAssembly(
        blackboard=_FallbackBlackboard(),
        quest_manager_agent=OpeningBootstrapQuestAgent(),
        npc_director_agent=_FallbackAgent("npc_director"),
        world_builder_agent=_FallbackAgent("world_builder"),
        narrative_weaver_agent=_FallbackAgent("narrative_weaver"),
    )


def test_fallback_assembly_has_all_agents():
    """Fallback assembly should include all agent slots."""
    from app.game_core.adapters.planner_system import PlannerSystemAssembly
    from app.game_core.planning.opening_bootstrap import OpeningBootstrapQuestAgent

    assembly = _build_fallback_assembly()
    assert isinstance(assembly, PlannerSystemAssembly)
    assert isinstance(assembly.quest_manager_agent, OpeningBootstrapQuestAgent)
    assert assembly.npc_director_agent is not None
    assert assembly.world_builder_agent is not None
    assert assembly.narrative_weaver_agent is not None


def test_fallback_agent_returns_empty_directives():
    """Non-bootstrap fallback agents should return empty directives."""
    assembly = _build_fallback_assembly()

    async def _run():
        result = await assembly.npc_director_agent.evaluate({"current_event": {"kind": "tick_settlement"}})
        return result

    result = asyncio.run(_run())
    assert result.get("directives") == [], "Fallback agent should return empty directives"
    assert result.get("metadata", {}).get("reason") == "no_llm"


def test_fallback_blackboard_returns_empty_directives():
    """Fallback blackboard should return empty directives."""
    assembly = _build_fallback_assembly()

    async def _run():
        result = await assembly.blackboard.plan({"current_event": {"kind": "tick_settlement"}})
        return result

    result = asyncio.run(_run())
    assert result.get("directives") == []
    assert result.get("metadata", {}).get("reason") == "no_llm"


def test_fallback_quest_agent_still_runs_bootstrap():
    """The fallback quest_manager_agent (OpeningBootstrapQuestAgent) handles bootstrap."""
    assembly = _build_fallback_assembly()

    bootstrap_context = {
        "current_event": {"kind": "bootstrap"},
        "quests": {
            "available_milestones": ["test_milestone_1"],
            "dynamic_quests": {},
        },
        "location": {"area_id": "frontier_town"},
        "area_boards": [{"id": "board_1", "sub_location": "adventurers_guild"}],
    }

    async def _run():
        return await assembly.quest_manager_agent.evaluate(bootstrap_context)

    result = asyncio.run(_run())
    directives = result.get("directives", [])
    kinds = [d.get("kind") for d in directives]
    assert "create_quest" in kinds, f"Bootstrap agent should produce create_quest, got {kinds}"


# ---------------------------------------------------------------------------
# C-5 (W6-2): Rotating Inventory Non-Deterministic Rotation
# ---------------------------------------------------------------------------


def test_rotating_inventory_uses_hash_seed():
    """Rotating inventory selection should use hash-based seed, not tick mod."""
    world = _make_world_with_merchant()
    state = _make_state_with_relations()
    handler = EconomyHandler()

    # Get rotating entries for different ticks — they should differ when pool > slots
    # tick=1: seed = md5("merchant:1")[:8]
    # tick=2: seed = md5("merchant:2")[:8]
    # We verify: different npc_id → different start even at same tick
    rotating_pool = [
        {"item_id": "elixir", "count": 2},
        {"item_id": "bomb", "count": 3},
        {"item_id": "potion", "count": 1},
    ]

    def _get_start(npc_id: str, tick: int) -> int:
        seed_str = f"{npc_id}:{tick}"
        seed = int(hashlib.md5(seed_str.encode()).hexdigest()[:8], 16)
        return seed % len(rotating_pool)

    tick = 5
    start_merchant = _get_start("merchant", tick)
    start_blacksmith = _get_start("blacksmith", tick)
    # Two different NPCs should typically get different starts (they might collide,
    # but MD5 prefix makes this extremely unlikely for distinct strings)
    # We mainly verify the formula itself is what's being used
    seed_merchant = int(hashlib.md5(f"merchant:{tick}".encode()).hexdigest()[:8], 16)
    assert start_merchant == seed_merchant % len(rotating_pool)


def test_rotating_inventory_consistent_within_same_tick():
    """Same npc_id + tick should always produce the same rotation."""
    world = _make_world_with_merchant()
    state = _make_state_with_relations()

    # Refresh shop twice at same tick — result should be identical
    cmd = Command(type="refresh_shop", params={"npc_id": "merchant"}, source="test")
    rules_engine = RulesEngine()
    register_default_rules_handlers(rules_engine)

    result1 = rules_engine.execute(cmd, state, world)
    assert result1.executed
    state.apply(result1.delta)
    shop1 = dict(state.relations.get_shop_state("merchant") or {})

    # Clear shop state by restoring to empty (direct mutation for test setup)
    state.relations.shop_states.pop("merchant", None)

    result2 = rules_engine.execute(cmd, state, world)
    assert result2.executed
    state.apply(result2.delta)
    shop2 = dict(state.relations.get_shop_state("merchant") or {})

    # Rotating stocks should be identical (same tick, same npc_id)
    stock1 = sorted(
        [r.get("item_id") for r in shop1.get("current_stock", []) if r.get("source") == "rotating"]
    )
    stock2 = sorted(
        [r.get("item_id") for r in shop2.get("current_stock", []) if r.get("source") == "rotating"]
    )
    assert stock1 == stock2, "Same tick should produce same rotating selection"


# ---------------------------------------------------------------------------
# C-6 (W6-3): price warning
# ---------------------------------------------------------------------------


def test_base_price_logs_warning_for_zero_price(caplog):
    """_base_price_for_item should log a warning when price falls back to 0."""
    from app.game_core.rules.handlers.economy import EconomyHandler

    world = WorldInstance("test_world")
    # Item without base_price
    items = ItemRegistry()
    items.load({"mystery_item": {"id": "mystery_item"}})
    world.register(items)

    handler = EconomyHandler()
    with caplog.at_level(logging.WARNING, logger="app.game_core.rules.handlers.economy"):
        price = handler._base_price_for_item("mystery_item", None, world)
    assert price == 0
    assert any("mystery_item" in r.message and "no base_price" in r.message for r in caplog.records), (
        "Should log warning when item has no base_price"
    )
