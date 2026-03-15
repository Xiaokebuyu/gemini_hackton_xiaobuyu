"""Integration tests for MechanicalOsirisEngine + AIOsirisHook (2-A).

These tests verify:
- _ALLOWED_COMMAND_TYPES is restricted to set_flag + adjust_danger
- area_events are persisted into AreaSlice via append_area_event
- ai_osiris_applied SSE event fires when commands execute
- MAX_CONSEQUENCES truncation works
- has_witnesses detection (party members + co-located NPCs)
- _effect_to_command edge cases (missing params, wrong type)
- hook-level integration with the full SettlementContext pipeline
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.game_core.orchestration.hooks.ai_osiris import (
    AIOsirisHook,
    MechanicalOsirisEngine,
    _ALLOWED_COMMAND_TYPES,
)
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules import RulesEngine
from app.game_core.rules.handlers import WorldStateHandler
from app.game_core.state import StateChange, StateContainer, StateDelta
from app.game_core.state.slices import (
    AreaSlice,
    FlagSlice,
    NarrativePlanSlice,
    PartySlice,
    PlayerSlice,
    RelationSlice,
    SceneSlice,
    TimeSlice,
)
from app.game_core.content import WorldInstance


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_context(
    *,
    action_log: list[dict] | None = None,
    area_id: str = "frontier_town",
    area_tags: list[str] | None = None,
    area_npc_locations: dict[str, str | None] | None = None,
    party_members: dict | None = None,
) -> SettlementContext:
    world = WorldInstance("test_world")
    state = StateContainer()

    time_slice = TimeSlice()
    time_slice.restore({"day": 1, "slot": 4})
    state.register(time_slice)

    player = PlayerSlice()
    player.restore({"current_area": area_id, "current_location": "town_square"})
    state.register(player)

    flags = FlagSlice()
    flags.restore({"flags": {}})
    state.register(flags)

    relations = RelationSlice()
    relations.restore({})
    state.register(relations)

    areas = AreaSlice()
    area_state_dict: dict[str, Any] = {}
    if area_tags is not None:
        area_state_dict["tags"] = area_tags
    if area_npc_locations is not None:
        area_state_dict["npc_locations"] = area_npc_locations
    areas.restore({"areas": {area_id: area_state_dict}})
    state.register(areas)

    party = PartySlice()
    party.restore({"members": party_members or {}})
    state.register(party)

    narrative_plan = NarrativePlanSlice()
    narrative_plan.restore({"current_chapter": "chapter_1"})
    state.register(narrative_plan)

    scene_slice = SceneSlice()
    scene_slice.restore({})
    state.register(scene_slice)
    scene_bus = SceneBus(scene_slice)

    rules_engine = RulesEngine()
    rules_engine.register(WorldStateHandler())

    change_log: list[StateChange] = []

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
        action_log=list(action_log or []),
    )


def _non_processing_events(result) -> list:
    return [e for e in result.sse_events if e.event_type != "ai_processing"]


# ---------------------------------------------------------------------------
# 1. _ALLOWED_COMMAND_TYPES — only set_flag + adjust_danger
# ---------------------------------------------------------------------------

def test_allowed_command_types_contains_only_set_flag_and_adjust_danger():
    assert set(_ALLOWED_COMMAND_TYPES) == {"set_flag", "adjust_danger"}


def test_allowed_command_types_does_not_include_legacy_commands():
    forbidden = {
        "advance_quest", "modify_completion", "schedule_event",
        "create_rumor", "modify_location", "modify_disposition", "modify_approval",
    }
    for cmd in forbidden:
        assert cmd not in _ALLOWED_COMMAND_TYPES, f"{cmd!r} must not be in _ALLOWED_COMMAND_TYPES"


# ---------------------------------------------------------------------------
# 2. area_events persisted to AreaSlice
# ---------------------------------------------------------------------------

def test_navigation_action_writes_area_event_to_slice():
    """NAVIGATION rule fires an area_event that is persisted into AreaSlice."""
    context = _make_context(
        action_log=[{"type": "move_area", "time_cost": 1.0}],
    )

    asyncio.run(AIOsirisHook().execute(context))

    events = context.state.areas.get_area_events("frontier_town")
    assert len(events) >= 1
    assert any(e["source"] == "ai_osiris" for e in events)


def test_combat_end_writes_minor_area_event_to_slice():
    """COMBAT_END rule writes an area_event of severity 'minor'."""
    context = _make_context(
        action_log=[{"type": "attack", "time_cost": 0.1}],
    )
    # Inject COMBAT_END tag via SceneBus ENGINE entry
    context.scene_bus.add_entry({
        "source": "ENGINE",
        "content": "combat ended",
        "visibility": "system",
        "tags": ["COMBAT_END"],
    })

    asyncio.run(AIOsirisHook().execute(context))

    events = context.state.areas.get_area_events("frontier_town")
    minor_events = [e for e in events if e.get("severity") == "minor"]
    assert len(minor_events) >= 1


def test_area_event_has_tick_field():
    """area_events written by Osiris include a 'tick' int field."""
    context = _make_context(
        action_log=[{"type": "move_area", "time_cost": 1.0}],
    )

    asyncio.run(AIOsirisHook().execute(context))

    events = context.state.areas.get_area_events("frontier_town")
    assert len(events) >= 1
    assert all("tick" in e for e in events)
    assert all(isinstance(e["tick"], int) for e in events)


def test_quest_progress_writes_major_area_event_to_slice():
    """QUEST_PROGRESS rule writes area_event severity='major' into AreaSlice."""
    context = _make_context()
    context.scene_bus.add_entry({
        "source": "ENGINE",
        "content": "quest advanced",
        "visibility": "system",
        "tags": ["QUEST_PROGRESS"],
    })

    asyncio.run(AIOsirisHook().execute(context))

    events = context.state.areas.get_area_events("frontier_town")
    major_events = [e for e in events if e.get("severity") == "major"]
    assert len(major_events) >= 1


# ---------------------------------------------------------------------------
# 3. ai_osiris_applied SSE event
# ---------------------------------------------------------------------------

def test_combat_in_safe_zone_emits_ai_osiris_applied_sse():
    """When commands execute (COMBAT+safe_zone), ai_osiris_applied SSE fires."""
    context = _make_context(
        action_log=[{"type": "attack", "time_cost": 0.1}],
        area_tags=["safe_zone"],
    )

    result = asyncio.run(AIOsirisHook().execute(context))

    applied_events = [e for e in result.sse_events if e.event_type == "ai_osiris_applied"]
    assert len(applied_events) == 1
    assert applied_events[0].payload["executed_count"] >= 1


def test_no_commands_executed_no_ai_osiris_applied_sse():
    """When no commands execute (no matching rules), ai_osiris_applied is NOT emitted."""
    context = _make_context()

    result = asyncio.run(AIOsirisHook().execute(context))

    applied_events = [e for e in result.sse_events if e.event_type == "ai_osiris_applied"]
    assert applied_events == []


# ---------------------------------------------------------------------------
# 4. has_witnesses detection
# ---------------------------------------------------------------------------

def test_has_witnesses_true_when_party_member_present():
    """Party members count as witnesses."""
    context = _make_context(
        party_members={"companion_1": {"id": "companion_1"}},
    )
    assert AIOsirisHook._has_witnesses(context) is True


def test_has_witnesses_false_with_empty_party_and_no_npcs():
    """No party members + no co-located NPCs → no witnesses."""
    context = _make_context(party_members={})
    assert AIOsirisHook._has_witnesses(context) is False


def test_has_witnesses_true_when_npc_in_area():
    """NPC in area_npc_locations counts as witness."""
    context = _make_context(
        party_members={},
        area_npc_locations={"innkeeper": "main_hall"},
    )
    assert AIOsirisHook._has_witnesses(context) is True


# ---------------------------------------------------------------------------
# 5. MAX_CONSEQUENCES truncation
# ---------------------------------------------------------------------------

def test_max_consequences_caps_area_events():
    """area_event_dicts are capped at MAX_CONSEQUENCES."""
    engine = MechanicalOsirisEngine()
    # Inject more rules than MAX_CONSEQUENCES artificially
    # We do this by calling evaluate with multiple firing tags
    # Navigation + Quest + COMBAT_END simultaneously
    effects = engine.evaluate(
        {"NAVIGATION", "QUEST_PROGRESS", "COMBAT_END"},
        [],
        False,
        "area_x",
    )
    area_events = [e for e in effects if e["type"] == "area_event"]
    # Engine itself does not cap — hook does. Verify at hook level.
    assert len(area_events) >= 3

    context = _make_context()
    context.scene_bus.add_entry({
        "source": "ENGINE",
        "content": "multi events",
        "visibility": "system",
        "tags": ["NAVIGATION", "QUEST_PROGRESS", "COMBAT_END"],
    })
    result = asyncio.run(AIOsirisHook().execute(context))

    # Hook caps at MAX_CONSEQUENCES = 5
    assert result.metadata["area_events_written"] <= AIOsirisHook.MAX_CONSEQUENCES


# ---------------------------------------------------------------------------
# 6. _effect_to_command edge cases
# ---------------------------------------------------------------------------

def test_effect_to_command_adjust_danger_requires_area_id():
    """adjust_danger effect without area_id returns None."""
    result = AIOsirisHook._effect_to_command(
        {"type": "adjust_danger", "delta": 0.5, "area_id": ""},
        "",
    )
    assert result is None


def test_effect_to_command_adjust_danger_requires_numeric_delta():
    """adjust_danger effect with non-numeric delta returns None."""
    result = AIOsirisHook._effect_to_command(
        {"type": "adjust_danger", "delta": "a lot", "area_id": "zone_a"},
        "zone_a",
    )
    assert result is None


def test_effect_to_command_set_flag_requires_non_empty_flag():
    """set_flag effect with empty flag key returns None."""
    result = AIOsirisHook._effect_to_command(
        {"type": "set_flag", "flag": "", "area_id": "zone_a"},
        "zone_a",
    )
    assert result is None


def test_effect_to_command_unknown_type_returns_none():
    """Effect type not in _ALLOWED_COMMAND_TYPES returns None."""
    result = AIOsirisHook._effect_to_command(
        {"type": "advance_quest", "quest_id": "q1", "area_id": "zone_a"},
        "zone_a",
    )
    assert result is None


def test_effect_to_command_set_flag_success():
    """Valid set_flag effect produces a Command with type='set_flag'."""
    cmd = AIOsirisHook._effect_to_command(
        {"type": "set_flag", "flag": "disturbance_zone_a", "area_id": "zone_a"},
        "zone_a",
    )
    assert cmd is not None
    assert cmd.type == "set_flag"
    assert cmd.params["key"] == "disturbance_zone_a"
    assert cmd.params["value"] is True
    assert cmd.source == "ai_osiris"


def test_effect_to_command_adjust_danger_success():
    """Valid adjust_danger effect produces a Command with area_id."""
    cmd = AIOsirisHook._effect_to_command(
        {"type": "adjust_danger", "delta": -0.3, "area_id": "dungeon"},
        "dungeon",
    )
    assert cmd is not None
    assert cmd.type == "adjust_danger"
    assert cmd.params["area_id"] == "dungeon"
    assert cmd.params["delta"] == -0.3
