"""Tests for 2-C: Osiris 7-command migration to Planner subsystems.

Verifies that:
- NarrativeWeaverSubSystem handles "schedule_event" directive kind.
- NpcDirectorSubSystem handles "create_rumor" and "modify_location" directive kinds.
- directive_contracts.py SUPPORTED_PLANNER_DIRECTIVE_KINDS includes all three new kinds.
- validate_planner_directive accepts valid payloads for each new kind.

All async tests use asyncio.run() wrappers — pytest-asyncio is not installed.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.game_core.content import WorldInstance
from app.game_core.orchestration.models import SSEEvent
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.planning.directive_contracts import (
    SUPPORTED_PLANNER_DIRECTIVE_KINDS,
    validate_planner_directive,
)
from app.game_core.planning.narrative_weaver import NarrativeWeaverSubSystem
from app.game_core.planning.npc_director import NpcDirectorSubSystem
from app.game_core.planning.subsystem import PlannerDispatcher
from app.game_core.rules import RulesEngine, register_default_rules_handlers
from app.game_core.state import StateChange, StateContainer, StateDelta
from app.game_core.state.slices import (
    AreaSlice,
    EventSlice,
    FlagSlice,
    NarrativePlanSlice,
    PlayerSlice,
    SceneSlice,
    TimeSlice,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_context(
    *,
    player_area: str = "frontier_town",
    with_events: bool = True,
    with_areas: bool = True,
    world: WorldInstance | None = None,
) -> SettlementContext:
    """Build a minimal SettlementContext for directive testing."""
    if world is None:
        world = WorldInstance("test_world")

    state = StateContainer()

    time_slice = TimeSlice()
    time_slice.restore({"day": 1, "slot": 9})
    state.register(time_slice)

    player = PlayerSlice()
    player.restore({"current_area": player_area, "current_location": None})
    state.register(player)

    narrative_plan = NarrativePlanSlice()
    narrative_plan.restore({})
    state.register(narrative_plan)

    if with_areas:
        areas = AreaSlice()
        areas.restore({"areas": {player_area: {}}})
        state.register(areas)

    if with_events:
        events = EventSlice()
        events.restore({})
        state.register(events)

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


def _make_dispatcher_with_weaver_and_director(
    *,
    sse_collector: list[SSEEvent] | None = None,
) -> PlannerDispatcher:
    """Build a PlannerDispatcher with NarrativeWeaver and NpcDirector registered."""
    pending_sse: list[SSEEvent] = sse_collector if sse_collector is not None else []
    dispatcher = PlannerDispatcher()
    dispatcher.register(NarrativeWeaverSubSystem(sse_collector=pending_sse))
    dispatcher.register(NpcDirectorSubSystem())
    return dispatcher


# ---------------------------------------------------------------------------
# SUPPORTED_PLANNER_DIRECTIVE_KINDS membership
# ---------------------------------------------------------------------------


def test_schedule_event_is_supported_kind():
    """schedule_event is listed in SUPPORTED_PLANNER_DIRECTIVE_KINDS."""
    assert "schedule_event" in SUPPORTED_PLANNER_DIRECTIVE_KINDS


def test_create_rumor_is_supported_kind():
    """create_rumor is listed in SUPPORTED_PLANNER_DIRECTIVE_KINDS."""
    assert "create_rumor" in SUPPORTED_PLANNER_DIRECTIVE_KINDS


def test_modify_location_is_supported_kind():
    """modify_location is listed in SUPPORTED_PLANNER_DIRECTIVE_KINDS."""
    assert "modify_location" in SUPPORTED_PLANNER_DIRECTIVE_KINDS


# ---------------------------------------------------------------------------
# validate_planner_directive contract checks
# ---------------------------------------------------------------------------


def test_validate_schedule_event_valid():
    """schedule_event with a valid event_id passes contract validation."""
    result = validate_planner_directive({
        "kind": "schedule_event",
        "payload": {
            "event_id": "evt_goblin_raid",
            "event_type": "combat",
        },
    })
    assert result.ok is True
    assert result.kind == "schedule_event"
    assert result.reason_code is None


def test_validate_schedule_event_missing_event_id():
    """schedule_event without event_id fails validation with missing_event_id."""
    result = validate_planner_directive({
        "kind": "schedule_event",
        "payload": {
            "event_type": "combat",
        },
    })
    assert result.ok is False
    assert result.reason_code == "missing_event_id"


def test_validate_create_rumor_valid():
    """create_rumor with text passes contract validation."""
    result = validate_planner_directive({
        "kind": "create_rumor",
        "payload": {
            "text": "Goblins have been spotted near the north gate.",
        },
    })
    assert result.ok is True
    assert result.kind == "create_rumor"
    assert result.reason_code is None


def test_validate_create_rumor_missing_text():
    """create_rumor without text or content fails validation."""
    result = validate_planner_directive({
        "kind": "create_rumor",
        "payload": {
            "area_id": "frontier_town",
        },
    })
    assert result.ok is False
    assert result.reason_code == "missing_rumor_text"


def test_validate_modify_location_valid():
    """modify_location with area_id passes contract validation."""
    result = validate_planner_directive({
        "kind": "modify_location",
        "payload": {
            "area_id": "frontier_town",
            "location_id": "tavern",
        },
    })
    assert result.ok is True
    assert result.kind == "modify_location"
    assert result.reason_code is None


def test_validate_modify_location_missing_area_id():
    """modify_location without area_id fails validation."""
    result = validate_planner_directive({
        "kind": "modify_location",
        "payload": {
            "location_id": "tavern",
        },
    })
    assert result.ok is False
    assert result.reason_code == "missing_area_id"


# ---------------------------------------------------------------------------
# NarrativeWeaverSubSystem.apply_directive — schedule_event routing
# ---------------------------------------------------------------------------


def test_narrative_weaver_handles_schedule_event():
    """NarrativeWeaver._HANDLES contains 'schedule_event'."""
    weaver = NarrativeWeaverSubSystem()
    assert "schedule_event" in weaver.handles


def test_narrative_weaver_apply_schedule_event_writes_pending_event():
    """Applying a schedule_event directive stores the event in EventSlice.pending_events."""
    context = _make_context()
    weaver = NarrativeWeaverSubSystem()

    ok = weaver.apply_directive(
        "schedule_event",
        {
            "event_id": "evt_test_001",
            "event_type": "narrative",
            "trigger_condition": {"type": "absolute_tick", "tick": 10},
        },
        context,
        current_tick=1,
    )

    assert ok is True
    pending = context.state.events.pending_events
    assert any(e["event_id"] == "evt_test_001" for e in pending), (
        f"expected evt_test_001 in pending_events, got {pending}"
    )


def test_narrative_weaver_rejects_unsupported_kind():
    """NarrativeWeaver returns False for unknown directive kinds."""
    weaver = NarrativeWeaverSubSystem()
    context = _make_context()

    result = weaver.apply_directive("create_quest", {}, context, current_tick=1)
    assert result is False


# ---------------------------------------------------------------------------
# NpcDirectorSubSystem.apply_directive — create_rumor and modify_location
# ---------------------------------------------------------------------------


def test_npc_director_handles_create_rumor():
    """NpcDirector._HANDLES contains 'create_rumor'."""
    director = NpcDirectorSubSystem()
    assert "create_rumor" in director.handles


def test_npc_director_handles_modify_location():
    """NpcDirector._HANDLES contains 'modify_location'."""
    director = NpcDirectorSubSystem()
    assert "modify_location" in director.handles


def test_npc_director_apply_create_rumor_writes_to_events():
    """Applying a create_rumor directive adds a rumor to EventSlice.rumors."""
    context = _make_context()
    director = NpcDirectorSubSystem()

    ok = director.apply_directive(
        "create_rumor",
        {
            "text": "A dragon has been seen near the mountains.",
            "tags": ["dragon", "danger"],
        },
        context,
        current_tick=5,
    )

    assert ok is True
    rumors = context.state.events.rumors
    assert any("dragon" in r.get("text", "") for r in rumors), (
        f"expected a dragon rumor in {rumors}"
    )


def test_npc_director_apply_modify_location_key_value_writes_area_property():
    """modify_location with key/value writes to the area's properties."""
    context = _make_context()
    director = NpcDirectorSubSystem()

    ok = director.apply_directive(
        "modify_location",
        {
            "area_id": "frontier_town",
            "key": "threat_level",
            "value": "high",
        },
        context,
        current_tick=3,
    )

    assert ok is True
    area = context.state.areas.get_area("frontier_town")
    assert area.properties.get("threat_level") == "high"


# ---------------------------------------------------------------------------
# PlannerDispatcher routing — all three new kinds dispatched correctly
# ---------------------------------------------------------------------------


def test_dispatcher_routes_schedule_event_via_weaver():
    """PlannerDispatcher routes schedule_event to NarrativeWeaverSubSystem."""
    context = _make_context()
    dispatcher = _make_dispatcher_with_weaver_and_director()

    ok = dispatcher.apply_directive(
        "schedule_event",
        {
            "event_id": "evt_dispatch_test",
            "event_type": "combat",
            "trigger_tick": 10,
        },
        context,
        current_tick=1,
    )

    assert ok is True
    assert any(
        e["event_id"] == "evt_dispatch_test"
        for e in context.state.events.pending_events
    )


def test_dispatcher_routes_create_rumor_via_director():
    """PlannerDispatcher routes create_rumor to NpcDirectorSubSystem."""
    context = _make_context()
    dispatcher = _make_dispatcher_with_weaver_and_director()

    ok = dispatcher.apply_directive(
        "create_rumor",
        {"text": "The innkeeper knows something suspicious."},
        context,
        current_tick=2,
    )

    assert ok is True
    assert any(
        "innkeeper" in r.get("text", "")
        for r in context.state.events.rumors
    )


def test_dispatcher_routes_modify_location_via_director():
    """PlannerDispatcher routes modify_location to NpcDirectorSubSystem."""
    context = _make_context()
    dispatcher = _make_dispatcher_with_weaver_and_director()

    ok = dispatcher.apply_directive(
        "modify_location",
        {
            "area_id": "frontier_town",
            "key": "patrol_active",
            "value": True,
        },
        context,
        current_tick=7,
    )

    assert ok is True
    area = context.state.areas.get_area("frontier_town")
    assert area.properties.get("patrol_active") is True
