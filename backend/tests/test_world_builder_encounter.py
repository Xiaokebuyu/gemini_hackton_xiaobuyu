"""Tests for WorldBuilderSubSystem plant_encounter directive.

Decision record: D-R41 (rules_engine.md)
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from app.game_core.content import WorldInstance
from app.game_core.orchestration.models import SSEEvent
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.planning.world_builder import WorldBuilderSubSystem
from app.game_core.rules import RulesEngine, register_default_rules_handlers
from app.game_core.state import StateChange, StateContainer, StateDelta
from app.game_core.state.slices import (
    AreaSlice,
    FlagSlice,
    NarrativePlanSlice,
    PlayerSlice,
    QuestSlice,
    SceneSlice,
    TimeSlice,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_context(
    *,
    player_area: str = "frontier_town",
    area_payload: dict[str, Any] | None = None,
) -> SettlementContext:
    """Build a minimal SettlementContext for WorldBuilder directive testing."""
    world = WorldInstance("test_world")
    state = StateContainer()

    time_slice = TimeSlice()
    time_slice.restore({"day": 1, "slot": 9})
    state.register(time_slice)

    player = PlayerSlice()
    player.restore({"current_area": player_area, "current_location": None})
    state.register(player)

    quests = QuestSlice()
    quests.restore({"milestone_states": {}, "dynamic_quests": {}})
    state.register(quests)

    narrative_plan = NarrativePlanSlice()
    narrative_plan.restore({})
    state.register(narrative_plan)

    if area_payload is None:
        area_payload = {"areas": {player_area: {}}}
    areas = AreaSlice()
    areas.restore(area_payload)
    state.register(areas)

    flag_slice = FlagSlice()
    flag_slice.restore({})
    state.register(flag_slice)

    scene_slice = SceneSlice()
    scene_slice.restore({})
    state.register(scene_slice)
    scene_bus = SceneBus(scene_slice)

    change_log: list[StateChange] = []
    rules_engine = RulesEngine()
    register_default_rules_handlers(rules_engine)

    def _apply_delta(delta: StateDelta | None) -> None:
        if delta is None:
            return
        state.apply(delta)
        change_log.extend(delta.changes)
        for change in delta.changes:
            scene_bus.record_state_change(change)

    return SettlementContext(
        change_log=change_log,
        state=state,
        world=world,
        scene_bus=scene_bus,
        _rules_engine=rules_engine,
        _apply_delta=_apply_delta,
    )


def _make_builder(sse_collector: list[SSEEvent] | None = None) -> WorldBuilderSubSystem:
    return WorldBuilderSubSystem(sse_collector=sse_collector)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPlantEncounter:
    def test_plant_encounter_creates_hostile_tracking(self) -> None:
        """A valid plant_encounter payload should create a planted entry in hostile_tracking."""
        context = _make_context(player_area="frontier_town")
        builder = _make_builder()

        ok = builder.apply_directive(
            "plant_encounter",
            {
                "area_id": "frontier_town",
                "sub_area_id": "dark_alley",
                "monster_ids": ["goblin", "goblin", "hobgoblin"],
                "threat_level": "moderate",
                "description": "Goblins lurking in the shadows.",
            },
            context,
            current_tick=5,
        )

        assert ok is True
        hostile = context.state.areas.get_hostile_state("dark_alley")
        assert hostile is not None
        assert hostile["status"] == "planted"
        assert hostile["area_id"] == "frontier_town"
        assert hostile["monster_ids"] == ["goblin", "goblin", "hobgoblin"]
        assert hostile["combat_active"] is False
        assert hostile["cleared"] is False
        assert hostile["created_at_tick"] == 5

    def test_plant_encounter_missing_area_fails(self) -> None:
        """plant_encounter returns a rejection reason when area_id does not exist in state."""
        context = _make_context(player_area="frontier_town")
        builder = _make_builder()

        ok = builder.apply_directive(
            "plant_encounter",
            {
                "area_id": "nonexistent_area",
                "sub_area_id": "dark_alley",
                "monster_ids": ["goblin"],
            },
            context,
            current_tick=1,
        )

        assert ok is not True  # Returns a rejection reason string

    def test_plant_encounter_missing_monsters_fails(self) -> None:
        """plant_encounter returns a rejection reason for empty or missing monster_ids."""
        context = _make_context(player_area="frontier_town")
        builder = _make_builder()

        # Empty list
        ok_empty = builder.apply_directive(
            "plant_encounter",
            {
                "area_id": "frontier_town",
                "sub_area_id": "dark_alley",
                "monster_ids": [],
            },
            context,
            current_tick=1,
        )
        assert ok_empty is not True  # Returns a rejection reason string

        # Missing key
        ok_missing = builder.apply_directive(
            "plant_encounter",
            {
                "area_id": "frontier_town",
                "sub_area_id": "dark_alley",
            },
            context,
            current_tick=1,
        )
        assert ok_missing is not True  # Returns a rejection reason string

    def test_plant_encounter_no_sse_emitted(self) -> None:
        """plant_encounter should NOT emit any SSE events (encounter activates on arrival)."""
        sse: list[SSEEvent] = []
        context = _make_context(player_area="frontier_town")
        builder = _make_builder(sse_collector=sse)

        ok = builder.apply_directive(
            "plant_encounter",
            {
                "area_id": "frontier_town",
                "sub_area_id": "dark_alley",
                "monster_ids": ["wolf"],
            },
            context,
            current_tick=3,
        )

        assert ok is True
        assert sse == [], "plant_encounter must not emit SSE; encounter fires on player arrival"

    def test_plant_encounter_metadata_stored(self) -> None:
        """threat_level, surprise_modifier, map_tags, map_category and expiry_ticks are stored."""
        context = _make_context(player_area="frontier_town")
        builder = _make_builder()

        ok = builder.apply_directive(
            "plant_encounter",
            {
                "area_id": "frontier_town",
                "sub_area_id": "cave_entrance",
                "monster_ids": ["orc", "orc_shaman"],
                "threat_level": "deadly",
                "surprise_modifier": 2,
                "map_category": "cave",
                "map_tags": ["dark", "cramped"],
                "expiry_ticks": 24,
                "one_shot": False,
                "blocking": False,
                "description": "Orcs guard the cave mouth.",
            },
            context,
            current_tick=10,
        )

        assert ok is True
        hostile = context.state.areas.get_hostile_state("cave_entrance")
        assert hostile is not None
        assert hostile["threat_level"] == "deadly"
        assert hostile["surprise_modifier"] == 2
        assert hostile["map_category"] == "cave"
        assert hostile["map_tags"] == ["dark", "cramped"]
        assert hostile["expiry_ticks"] == 24
        assert hostile["one_shot"] is False
        assert hostile["blocking"] is False
        assert hostile["description"] == "Orcs guard the cave mouth."
        assert hostile["source"] == "narrative_planner"
