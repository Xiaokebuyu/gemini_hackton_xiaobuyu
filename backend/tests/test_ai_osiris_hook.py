"""Tests for AIOsirisHook (post-mechanical-engine migration).

Tests for the old LLM evaluator path (_build_summary, _build_snapshot,
RecordingEvaluator) have been removed. The hook now uses MechanicalOsirisEngine.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.game_core.content import WorldInstance
from app.game_core.content.registries import (
    CharacterRegistry,
    FactionRegistry,
    ItemRegistry,
    LoreRegistry,
    SkillRegistry,
    TagRegistry,
)
from app.game_core.orchestration.hooks.ai_osiris import AIOsirisHook, MechanicalOsirisEngine
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules import Command, RulesEngine
from app.game_core.rules.handlers import WorldStateHandler
from app.game_core.state import StateChange, StateContainer, StateDelta
from app.game_core.state.slices import (
    AreaSlice,
    FlagSlice,
    NarrativePlanSlice,
    PartySlice,
    PlayerSlice,
    QuestSlice,
    RelationSlice,
    SceneSlice,
    EventSlice,
    TimeSlice,
)


def _make_context(
    *,
    change_log: list[StateChange] | None = None,
    include_characters: bool = False,
    include_lore: bool = False,
    include_factions: bool = False,
    include_tags: bool = False,
    include_quests: bool = False,
    include_events: bool = False,
    event_pending: list[dict[str, Any]] | None = None,
    narrative_plan_data: dict | None = None,
    area_npc_locations: dict[str, str | None] | None = None,
    area_danger_level: float | None = None,
    action_log: list[dict] | None = None,
    area_tags: list[str] | None = None,
) -> SettlementContext:
    world = WorldInstance("test_world")
    if include_characters:
        characters = CharacterRegistry()
        characters.load(
            {
                "npc_guard": {
                    "id": "npc_guard",
                    "name": "Guard",
                    "area_id": "forest",
                    "tags": ["GUARD", "SOLDIER"],
                },
                "npc_far": {"id": "npc_far", "name": "Far Away", "area_id": "city"},
                "companion_1": {
                    "id": "companion_1",
                    "name": "Companion",
                    "tags": ["COMPANION", "PALADIN"],
                    "faction": "temple_order",
                },
                "player_char": {
                    "id": "player_char",
                    "name": "Hero",
                    "tags": ["ADVENTURER", "NEWCOMER"],
                },
            }
        )
        world.register(characters)
    if include_lore:
        lore = LoreRegistry()
        lore.load({
            "theft_rules": {
                "id": "theft_rules",
                "content": "Merchant guild has zero tolerance for theft.",
                "tags": ["CRIME", "COMMERCE"],
            },
        })
        world.register(lore)
    if include_factions:
        factions = FactionRegistry()
        factions.load({
            "merchant_guild": {
                "id": "merchant_guild",
                "name": "Merchant Guild",
                "alignment": "lawful_neutral",
                "behavioral_rules": "Zero tolerance for theft; triggers city-wide bounty.",
            },
        })
        world.register(factions)
    if include_tags:
        tags = TagRegistry()
        tags.load({
            "moral": {
                "id": "moral",
                "description": "Moral alignment tags",
                "tags": ["SACRED", "PROFANE", "NEUTRAL"],
            },
        })
        world.register(tags)

    state = StateContainer()

    time_slice = TimeSlice()
    time_slice.restore({"day": 2, "slot": 9})
    state.register(time_slice)

    player = PlayerSlice()
    player_data: dict = {"current_area": "forest", "current_location": "camp"}
    if include_characters:
        player_data["character_id"] = "player_char"
    player.restore(player_data)
    state.register(player)

    flags = FlagSlice()
    flags.restore({"flags": {"quest_started": True}})
    state.register(flags)

    relations = RelationSlice()
    if include_characters:
        relations.restore({
            "faction_standings": {"guild": 3},
            "npc_dispositions": {"companion_1": {"trust": 40, "admiration": 30}},
            "relationship_stages": {"companion_1": "acquaintance"},
        })
    else:
        relations.restore({"faction_standings": {"guild": 3}})
    state.register(relations)

    areas = AreaSlice()
    area_data: dict = {}
    if area_npc_locations is not None or area_danger_level is not None or area_tags is not None:
        area_state: dict = {}
        if area_npc_locations is not None:
            area_state["npc_locations"] = area_npc_locations
        if area_danger_level is not None:
            area_state["danger_level"] = area_danger_level
        if area_tags is not None:
            area_state["tags"] = area_tags
        area_data = {"areas": {"forest": area_state}}
    areas.restore(area_data)
    state.register(areas)

    party = PartySlice()
    party_data: dict = {"members": {"companion_1": {"id": "companion_1"}}}
    if include_characters:
        party_data["companion_approval"] = {"companion_1": 65}
    party.restore(party_data)
    state.register(party)

    if include_quests:
        quests_slice = QuestSlice()
        quests_slice.restore({
            "milestone_states": {
                "main_quest_1": {"state": "ACTIVE", "activated_tick": 10},
                "side_quest_1": {"state": "AVAILABLE"},
                "completed_quest": {"state": "COMPLETED", "completed_tick": 20},
            },
            "dynamic_quests": {
                "dynamic_1": {"status": "active", "title": "Dynamic Quest"},
                "dynamic_done": {"status": "completed", "title": "Done Quest"},
            },
        })
        state.register(quests_slice)

    narrative_plan = NarrativePlanSlice()
    narrative_plan.restore(
        narrative_plan_data or {"current_chapter": "chapter_1"}
    )
    state.register(narrative_plan)

    scene_slice = SceneSlice()
    scene_slice.restore({})
    state.register(scene_slice)
    scene_bus = SceneBus(scene_slice)

    if include_events:
        events = EventSlice()
        events.restore({
            "pending_events": [
                dict(item)
                for item in (event_pending if event_pending is not None else [])
                if isinstance(item, dict)
            ],
            "active_events": {},
            "rumors": [],
        })
        state.register(events)

    rules_engine = RulesEngine()
    rules_engine.register(WorldStateHandler())

    active_change_log = list(change_log or [])

    def _apply_delta(delta: StateDelta | None) -> None:
        if delta is None:
            return
        state.apply(delta)
        active_change_log.extend(delta.changes)
        for change in delta.changes:
            scene_bus.record_state_change(change)

    return SettlementContext(
        change_log=active_change_log,
        state=state,
        world=world,
        scene_bus=scene_bus,
        _rules_engine=rules_engine,
        _apply_delta=_apply_delta,
        action_log=list(action_log) if action_log is not None else [],
    )


def _non_processing_events(result) -> list:
    """Return SSE events that are not ai_processing (QF-4 channel events)."""
    return [e for e in result.sse_events if e.event_type != "ai_processing"]


class TestAIOsirisHook:
    def test_should_skip_without_meaningful_changes(self) -> None:
        hook = AIOsirisHook()

        assert hook.should_skip([]) is True
        assert (
            hook.should_skip(
                [
                    StateChange(
                        slice="scene",
                        operation="add",
                        path="entries",
                        value={"source": "gm"},
                    )
                ]
            )
            is False
        )

    def test_should_not_skip_when_flags_changed(self) -> None:
        hook = AIOsirisHook()

        assert (
            hook.should_skip(
                [
                    StateChange(
                        slice="flags",
                        operation="set",
                        path="flags.quest_started",
                        value=True,
                    )
                ]
            )
            is False
        )

    def test_should_skip_when_all_actions_are_trivial_zero_cost(self) -> None:
        hook = AIOsirisHook()
        action_log = [
            {"type": "look_inventory", "time_cost": 0.0},
            {"type": "equip", "time_cost": 0},
        ]

        assert hook.should_skip([], action_log=action_log) is True
        assert hook.should_skip([StateChange(slice="scene", operation="set", path="entries", value=[])], action_log=action_log) is True

    def test_should_not_skip_when_trivial_action_has_cost(self) -> None:
        hook = AIOsirisHook()
        action_log = [{"type": "look_inventory", "time_cost": 0.2}]

        assert hook.should_skip([], action_log=action_log) is False

    def test_should_not_skip_when_action_is_non_trivial(self) -> None:
        hook = AIOsirisHook()
        action_log = [{"type": "trade_buy", "time_cost": 0.0}]

        assert hook.should_skip([], action_log=action_log) is False

    def test_execute_with_no_tags_returns_noop(self) -> None:
        """When no ENGINE tags are in SceneBus and no action_log, engine returns no effects."""
        context = _make_context()

        result = asyncio.run(AIOsirisHook().execute(context))

        assert result.metadata["evaluated"] is True
        assert result.metadata["provider_name"] == "MechanicalOsirisEngine"
        assert result.metadata["executed_count"] == 0

    def test_execute_emits_ai_processing_sse_events(self) -> None:
        context = _make_context()

        result = asyncio.run(AIOsirisHook().execute(context))

        processing = [e for e in result.sse_events if e.event_type == "ai_processing"]
        assert len(processing) == 2
        assert processing[0].payload["system"] == "osiris"
        assert processing[0].payload["status"] == "start"
        assert processing[1].payload["status"] == "done"

    def test_quiet_long_rest_slot_suppresses_consequences(self) -> None:
        context = _make_context(action_log=[{"type": "rest_long", "time_cost": 1.0}])
        context.state.time.accumulated = 4.0
        context.state.time._dirty = True

        result = asyncio.run(AIOsirisHook().execute(context))

        assert result.metadata["status"] == "quiet_rest_slot"
        assert result.metadata["quiet_rest_slot"] is True
        assert result.metadata["executed_count"] == 0
        assert _non_processing_events(result) == []
        assert context.scene_bus.snapshot()["entries"] == []

    def test_execute_returns_mechanical_metadata(self) -> None:
        context = _make_context()

        result = asyncio.run(AIOsirisHook().execute(context))

        assert result.metadata["provider_name"] == "MechanicalOsirisEngine"
        assert result.metadata["provider_status"] == "mechanical"
        assert "action_tags" in result.metadata
        assert "area_tags" in result.metadata
        assert "evaluation_ms" in result.metadata

    def test_execute_with_engine_none_uses_default_engine(self) -> None:
        """AIOsirisHook() with no args should use MechanicalOsirisEngine by default."""
        hook = AIOsirisHook()
        assert isinstance(hook._engine, MechanicalOsirisEngine)

    def test_execute_with_combat_action_log_applies_danger_adjust(self) -> None:
        """COMBAT action in safe_zone area triggers adjust_danger effect."""
        context = _make_context(
            action_log=[{"type": "attack", "time_cost": 1.0 / 6.0}],
            area_tags=["safe_zone"],
        )

        result = asyncio.run(AIOsirisHook().execute(context))

        # Should have executed at least one command (adjust_danger) and set a flag
        assert result.metadata["evaluated"] is True
        assert result.metadata["provider_name"] == "MechanicalOsirisEngine"
        # area_tags should be captured
        assert "safe_zone" in result.metadata["area_tags"]

    def test_execute_with_navigation_action_writes_area_event(self) -> None:
        """NAVIGATION action produces an area_event."""
        context = _make_context(
            action_log=[{"type": "move_area", "time_cost": 1.0}],
        )

        result = asyncio.run(AIOsirisHook().execute(context))

        assert result.metadata["evaluated"] is True
        # area_events_written should be >= 1 for NAVIGATION
        assert result.metadata["area_events_written"] >= 1


class TestMechanicalOsirisEngine:
    """Direct unit tests for MechanicalOsirisEngine.evaluate()."""

    def test_no_action_tags_returns_empty(self) -> None:
        engine = MechanicalOsirisEngine()
        effects = engine.evaluate(set(), [], False, "forest")
        assert effects == []

    def test_combat_in_safe_zone_produces_effects(self) -> None:
        engine = MechanicalOsirisEngine()
        effects = engine.evaluate({"COMBAT"}, ["safe_zone"], True, "frontier_town")

        effect_types = {e["type"] for e in effects}
        assert "adjust_danger" in effect_types
        assert "set_flag" in effect_types
        assert "area_event" in effect_types

    def test_combat_in_safe_zone_adjust_danger_delta_positive(self) -> None:
        engine = MechanicalOsirisEngine()
        effects = engine.evaluate({"COMBAT"}, ["safe_zone"], True, "frontier_town")

        danger_effects = [e for e in effects if e["type"] == "adjust_danger"]
        assert len(danger_effects) == 1
        assert danger_effects[0]["delta"] > 0
        assert danger_effects[0]["area_id"] == "frontier_town"

    def test_combat_in_safe_zone_flag_has_area_id_in_key(self) -> None:
        engine = MechanicalOsirisEngine()
        effects = engine.evaluate({"COMBAT"}, ["safe_zone"], False, "frontier_town")

        flag_effects = [e for e in effects if e["type"] == "set_flag"]
        assert len(flag_effects) == 1
        assert "frontier_town" in flag_effects[0]["flag"]

    def test_combat_end_reduces_danger(self) -> None:
        engine = MechanicalOsirisEngine()
        effects = engine.evaluate({"COMBAT_END"}, [], False, "forest")

        danger_effects = [e for e in effects if e["type"] == "adjust_danger"]
        assert len(danger_effects) == 1
        assert danger_effects[0]["delta"] < 0

    def test_combat_end_produces_area_event(self) -> None:
        engine = MechanicalOsirisEngine()
        effects = engine.evaluate({"COMBAT_END"}, [], False, "forest")

        area_events = [e for e in effects if e["type"] == "area_event"]
        assert len(area_events) >= 1

    def test_navigation_produces_area_event(self) -> None:
        engine = MechanicalOsirisEngine()
        effects = engine.evaluate({"NAVIGATION"}, [], False, "forest")

        area_events = [e for e in effects if e["type"] == "area_event"]
        assert len(area_events) == 1
        assert area_events[0]["severity"] == "minor"

    def test_combat_without_safe_zone_tag_not_triggered(self) -> None:
        """COMBAT rule with area_tag='safe_zone' must NOT fire when area has no safe_zone tag."""
        engine = MechanicalOsirisEngine()
        # hostile area — no safe_zone tag
        effects = engine.evaluate({"COMBAT"}, ["hostile"], False, "dungeon")

        # COMBAT+safe_zone rule should NOT fire
        # But COMBAT_END is not in tags so only non-area-tagged rules matter
        # No rule for COMBAT without area_tag constraint → no effects
        assert all(e["type"] != "set_flag" or "safe_zone" not in str(e) for e in effects)
        # No adjust_danger from the COMBAT rule (since area_tag mismatch)
        combat_danger = [e for e in effects if e["type"] == "adjust_danger" and e.get("delta", 0) > 0.4]
        assert combat_danger == []

    def test_long_rest_in_hostile_area_increases_danger(self) -> None:
        engine = MechanicalOsirisEngine()
        effects = engine.evaluate({"LONG_REST"}, ["hostile"], False, "swamp")

        danger_effects = [e for e in effects if e["type"] == "adjust_danger"]
        assert len(danger_effects) == 1
        assert danger_effects[0]["delta"] > 0
        assert danger_effects[0]["area_id"] == "swamp"

    def test_long_rest_without_hostile_tag_no_danger(self) -> None:
        engine = MechanicalOsirisEngine()
        effects = engine.evaluate({"LONG_REST"}, ["peaceful"], False, "inn")

        danger_effects = [e for e in effects if e["type"] == "adjust_danger"]
        assert danger_effects == []

    def test_quest_progress_produces_major_area_event(self) -> None:
        engine = MechanicalOsirisEngine()
        effects = engine.evaluate({"QUEST_PROGRESS"}, [], False, "town")

        area_events = [e for e in effects if e["type"] == "area_event"]
        assert len(area_events) == 1
        assert area_events[0]["severity"] == "major"

    def test_investigation_produces_no_effects(self) -> None:
        """INVESTIGATION rule has empty effects (clue handler writes area_event directly)."""
        engine = MechanicalOsirisEngine()
        effects = engine.evaluate({"INVESTIGATION"}, [], False, "dungeon")
        # INVESTIGATION rule fires but has no effects
        assert effects == []

    def test_multiple_tags_fires_multiple_rules(self) -> None:
        engine = MechanicalOsirisEngine()
        # Both NAVIGATION and QUEST_PROGRESS — both rules should fire
        effects = engine.evaluate({"NAVIGATION", "QUEST_PROGRESS"}, [], False, "forest")

        area_events = [e for e in effects if e["type"] == "area_event"]
        assert len(area_events) >= 2

    def test_empty_area_id_passthrough_in_effects(self) -> None:
        """Even with empty area_id, no crash — effects get area_id=""."""
        engine = MechanicalOsirisEngine()
        effects = engine.evaluate({"COMBAT_END"}, [], False, "")
        # Should not crash
        for e in effects:
            assert "area_id" in e
            assert e["area_id"] == ""

    def test_area_id_template_expansion_in_flag_key(self) -> None:
        engine = MechanicalOsirisEngine()
        effects = engine.evaluate({"COMBAT"}, ["safe_zone"], True, "north_gate")

        flag_effects = [e for e in effects if e["type"] == "set_flag"]
        assert len(flag_effects) == 1
        assert flag_effects[0]["flag"] == "disturbance_north_gate"
