"""Tests for Dynamic Sub-Area Lifecycle closure."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

from app.game_core.content.registries.maps import MapRegistry
from app.game_core.content.world import WorldInstance
from app.game_core.orchestration.context_assembler import ContextAssembler
from app.game_core.orchestration.hooks.dynamic_sub_area_expiry import (
    DynamicSubAreaExpiryHook,
)
from app.game_core.rules.handlers.navigation import NavigationHandler
from app.game_core.rules.models import Command
from app.game_core.state.base import StateContainer
from app.game_core.state.slices.area import AreaSlice, AreaState
from app.game_core.state.slices.player import PlayerSlice


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_state(
    area_id: str = "forest",
    current_location: str | None = None,
    temporary_sub_areas: list[dict[str, Any]] | None = None,
) -> StateContainer:
    state = StateContainer()
    player = PlayerSlice()
    player.restore({"current_area": area_id, "current_location": current_location})
    state.register(player)

    areas = AreaSlice()
    area_state = AreaState()
    if temporary_sub_areas:
        area_state.temporary_sub_areas = list(temporary_sub_areas)
    areas.areas[area_id] = area_state
    state.register(areas)
    return state


def _make_world(
    area_id: str = "forest",
    sub_locations: dict[str, Any] | None = None,
) -> WorldInstance:
    world = WorldInstance("test")
    maps = MapRegistry()
    area_data: dict[str, Any] = {"id": area_id}
    if sub_locations is not None:
        area_data["sub_locations"] = sub_locations
    maps.load({area_id: area_data})
    world.register(maps)
    return world


# ------------------------------------------------------------------
# NavigationHandler — dynamic sub-area fallback
# ------------------------------------------------------------------

handler = NavigationHandler()


def test_navigation_accepts_dynamic_sub_area():
    """Player can enter a dynamic sub-area that exists in temporary_sub_areas."""
    state = _make_state(
        temporary_sub_areas=[{"id": "camp_dynamic", "label": "Camp", "expiry": -1}]
    )
    world = _make_world()  # no static sub_locations

    cmd = Command(type="enter_sub_location", params={"location_id": "camp_dynamic"})
    result = handler.compute(cmd, state, world)

    assert result.executed
    assert result.metadata.get("to_location") == "camp_dynamic"


def test_navigation_rejects_unknown_sub_area():
    """Neither static nor dynamic sub-area exists -> rejection."""
    state = _make_state()
    world = _make_world()

    cmd = Command(type="enter_sub_location", params={"location_id": "nonexistent"})
    result = handler.compute(cmd, state, world)

    assert not result.executed
    assert "unknown sub-location" in (result.errors[0] if result.errors else "")


def test_navigation_static_takes_priority_over_dynamic():
    """Static sub_locations are checked before dynamic ones."""
    state = _make_state(
        temporary_sub_areas=[{"id": "inn", "label": "Dynamic Inn", "expiry": 5}]
    )
    world = _make_world(sub_locations={"inn": {"id": "inn", "label": "Static Inn"}})

    cmd = Command(type="enter_sub_location", params={"location_id": "inn"})
    result = handler.compute(cmd, state, world)

    assert result.executed


# ------------------------------------------------------------------
# AreaSlice.tick_expiry
# ------------------------------------------------------------------


def test_tick_expiry_decrements_and_removes():
    """Permanent stays, timed decrements, temporary at 1 gets removed."""
    areas = AreaSlice()
    area = AreaState()
    area.temporary_sub_areas = [
        {"id": "perm", "expiry": -1},
        {"id": "timed", "expiry": 3},
        {"id": "temp", "expiry": 1},
    ]
    areas.areas["forest"] = area

    removed = areas.tick_expiry("forest")

    assert removed == ["temp"]
    remaining = areas.list_temporary_sub_areas("forest")
    assert len(remaining) == 2
    ids = {item["id"] for item in remaining}
    assert ids == {"perm", "timed"}
    # timed should have decremented from 3 to 2
    timed_item = next(item for item in remaining if item["id"] == "timed")
    assert timed_item["expiry"] == 2


def test_tick_expiry_elapsed_zero_is_noop():
    """elapsed=0 should not change anything."""
    areas = AreaSlice()
    area = AreaState()
    area.temporary_sub_areas = [
        {"id": "temp", "expiry": 1},
    ]
    areas.areas["forest"] = area

    removed = areas.tick_expiry("forest", elapsed=0)

    assert removed == []
    remaining = areas.list_temporary_sub_areas("forest")
    assert len(remaining) == 1
    assert remaining[0]["expiry"] == 1


def test_tick_expiry_removes_zero_expiry():
    """Sub-area with expiry=0 should be removed immediately."""
    areas = AreaSlice()
    area = AreaState()
    area.temporary_sub_areas = [
        {"id": "already_expired", "expiry": 0},
    ]
    areas.areas["forest"] = area

    removed = areas.tick_expiry("forest")

    assert removed == ["already_expired"]
    assert areas.list_temporary_sub_areas("forest") == []


# ------------------------------------------------------------------
# DynamicSubAreaExpiryHook
# ------------------------------------------------------------------


def _make_settlement_context(
    state: StateContainer,
) -> MagicMock:
    ctx = MagicMock()
    ctx.state = state
    ctx.execute_command = MagicMock()
    return ctx


def test_expiry_hook_noop_without_areas_slice():
    state = StateContainer()
    player = PlayerSlice()
    state.register(player)
    ctx = _make_settlement_context(state)

    result = asyncio.run(DynamicSubAreaExpiryHook().execute(ctx))

    assert result.metadata["status"] == "noop"
    assert result.metadata["reason"] == "missing_areas"


def test_expiry_hook_noop_no_expired():
    """Only permanent sub-areas -> noop."""
    state = _make_state(
        temporary_sub_areas=[{"id": "perm", "expiry": -1}]
    )
    ctx = _make_settlement_context(state)

    result = asyncio.run(DynamicSubAreaExpiryHook().execute(ctx))

    assert result.metadata["status"] == "noop"
    assert result.metadata.get("removed_count", 0) == 0


def test_expiry_hook_expires_and_emits_sse():
    """Sub-area with expiry=1 gets removed and SSE is emitted."""
    state = _make_state(
        temporary_sub_areas=[
            {"id": "temp_camp", "expiry": 1},
            {"id": "perm_camp", "expiry": -1},
        ]
    )
    ctx = _make_settlement_context(state)

    result = asyncio.run(DynamicSubAreaExpiryHook().execute(ctx))

    assert result.metadata["status"] == "expired"
    assert result.metadata["removed_count"] == 1
    assert "temp_camp" in result.metadata["removed_ids"]
    assert len(result.sse_events) == 1
    assert result.sse_events[0].event_type == "dynamic_sub_areas_expired"
    assert result.sse_events[0].payload["removed_count"] == 1
    assert not result.sse_events[0].payload["player_ejected"]


def test_expiry_hook_ejects_player_from_expired_location():
    """Player in an expired sub-area triggers leave_sub_location."""
    state = _make_state(
        current_location="temp_camp",
        temporary_sub_areas=[{"id": "temp_camp", "expiry": 1}],
    )
    ctx = _make_settlement_context(state)

    result = asyncio.run(DynamicSubAreaExpiryHook().execute(ctx))

    assert result.metadata["player_ejected"] is True
    ctx.execute_command.assert_called_once()
    cmd = ctx.execute_command.call_args[0][0]
    assert cmd.type == "leave_sub_location"
    assert cmd.source == "system"


# ------------------------------------------------------------------
# ContextAssembler L3 — dynamic sub-area context
# ------------------------------------------------------------------


def test_l3_includes_dynamic_template():
    """L3 finds dynamic sub-area template when player is inside one."""
    world = _make_world()
    area_state = {
        "temporary_sub_areas": [
            {"id": "camp", "label": "Bandit Camp", "expiry": 5},
        ],
    }

    l3 = ContextAssembler._build_location_details(
        world=world,
        current_area="forest",
        current_location="camp",
        current_room=None,
        current_area_state=area_state,
    )

    assert l3["template"] is not None
    assert l3["template"]["label"] == "Bandit Camp"
    assert l3["is_dynamic"] is True


def test_l3_lists_all_dynamic_sub_areas():
    """L3 dynamic_sub_areas lists all dynamic sub-areas even when player is not in one."""
    world = _make_world()
    area_state = {
        "temporary_sub_areas": [
            {"id": "camp_a", "label": "Camp A", "expiry": -1},
            {"id": "camp_b", "label": "Camp B", "expiry": 10},
        ],
    }

    l3 = ContextAssembler._build_location_details(
        world=world,
        current_area="forest",
        current_location=None,
        current_room=None,
        current_area_state=area_state,
    )

    assert l3["template"] is None
    assert l3["is_dynamic"] is False
    assert len(l3["dynamic_sub_areas"]) == 2
    ids = {item["id"] for item in l3["dynamic_sub_areas"]}
    assert ids == {"camp_a", "camp_b"}
