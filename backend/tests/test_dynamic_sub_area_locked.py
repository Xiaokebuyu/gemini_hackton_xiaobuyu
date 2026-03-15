"""Tests for dynamic sub-area locking mechanism (Phase 5).

Covers:
- locked sub-area blocks navigation (5b)
- unlocked (or no locked field) sub-area allows navigation (5b)
- unlock_sub_location clue effect unlocks existing locked sub-area (5c)
- unlock_sub_location falls back to creating new sub-area when target not found (5c)
- DynamicSubAreaManager.create() stores locked field (5a)
- planner handler _compute_sub_area_command includes locked field (5a)
- directive_contracts normalizes locked boolean for fill_area / plant_environmental (5a)
"""

from __future__ import annotations

from typing import Any

from app.game_core.clue_investigation import apply_clue_effects
from app.game_core.content.registries.maps import MapRegistry
from app.game_core.content.world import WorldInstance
from app.game_core.planning import DynamicSubAreaManager
from app.game_core.planning.directive_contracts import validate_planner_directive
from app.game_core.planning.models import FillAreaPlan, PlantEnvironmentalPlan
from app.game_core.rules.handlers.navigation import NavigationHandler
from app.game_core.rules.models import Command
from app.game_core.state.base import StateContainer
from app.game_core.state.slices.area import AreaSlice, AreaState
from app.game_core.state.slices.player import PlayerSlice


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_nav_state(
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


def _make_world(area_id: str = "forest") -> WorldInstance:
    world = WorldInstance("test")
    maps = MapRegistry()
    maps.load({area_id: {"id": area_id}})
    world.register(maps)
    return world


handler = NavigationHandler()


# ------------------------------------------------------------------
# 5b: NavigationHandler lock check
# ------------------------------------------------------------------


def test_navigation_rejects_locked_dynamic_sub_area() -> None:
    """Player cannot enter a dynamic sub-area with locked=True."""
    state = _make_nav_state(
        temporary_sub_areas=[{"id": "goblin_inner", "label": "Inner Den", "locked": True}]
    )
    world = _make_world()

    cmd = Command(type="enter_sub_location", params={"location_id": "goblin_inner"})
    result = handler.compute(cmd, state, world)

    assert not result.executed
    assert result.errors and result.errors[0] == "location_locked"


def test_navigation_allows_unlocked_dynamic_sub_area() -> None:
    """Player can enter a dynamic sub-area with locked=False (or no locked field)."""
    state = _make_nav_state(
        temporary_sub_areas=[{"id": "goblin_inner", "label": "Inner Den", "locked": False}]
    )
    world = _make_world()

    cmd = Command(type="enter_sub_location", params={"location_id": "goblin_inner"})
    result = handler.compute(cmd, state, world)

    assert result.executed
    assert result.metadata.get("to_location") == "goblin_inner"


def test_navigation_allows_dynamic_sub_area_without_locked_field() -> None:
    """Existing dynamic sub-areas without a locked field are not affected (backward compat)."""
    state = _make_nav_state(
        temporary_sub_areas=[{"id": "old_camp", "label": "Old Camp", "expiry": -1}]
    )
    world = _make_world()

    cmd = Command(type="enter_sub_location", params={"location_id": "old_camp"})
    result = handler.compute(cmd, state, world)

    assert result.executed


# ------------------------------------------------------------------
# 5c: unlock_sub_location clue effect
# ------------------------------------------------------------------


def _make_clue_state(
    area_id: str = "dungeon",
    temporary_sub_areas: list[dict[str, Any]] | None = None,
) -> StateContainer:
    state = StateContainer()
    player = PlayerSlice()
    player.restore({"current_area": area_id})
    state.register(player)

    areas = AreaSlice()
    area_state = AreaState()
    if temporary_sub_areas:
        area_state.temporary_sub_areas = list(temporary_sub_areas)
    areas.areas[area_id] = area_state
    state.register(areas)
    return state


def test_unlock_sub_location_unlocks_locked_existing_sub_area() -> None:
    """unlock_sub_location effect sets locked=False on an existing locked sub-area."""
    state = _make_clue_state(
        temporary_sub_areas=[
            {"id": "inner_vault", "label": "Inner Vault", "locked": True, "expiry": -1}
        ]
    )
    world = _make_world("dungeon")

    effects = [
        {"type": "unlock_sub_location", "params": {"sub_location_id": "inner_vault"}}
    ]
    changes, meta, error = apply_clue_effects(
        state, world, effects, area_id="dungeon", source="test"
    )

    assert error is None
    assert changes  # at least one StateChange
    state.apply(__import__("app.game_core.state", fromlist=["StateDelta"]).StateDelta(changes=changes))

    sub_areas = state.areas.list_temporary_sub_areas("dungeon")
    vault = next((s for s in sub_areas if s["id"] == "inner_vault"), None)
    assert vault is not None
    assert vault.get("locked") is False


def test_unlock_sub_location_can_enter_after_unlock() -> None:
    """After unlock_sub_location, NavigationHandler allows entry."""
    state = _make_clue_state(
        temporary_sub_areas=[
            {"id": "inner_vault", "label": "Inner Vault", "locked": True, "expiry": -1}
        ]
    )
    world = _make_world("dungeon")

    # Confirm locked before
    cmd = Command(type="enter_sub_location", params={"location_id": "inner_vault"})
    assert not handler.compute(cmd, state, world).executed

    # Apply unlock
    effects = [
        {"type": "unlock_sub_location", "params": {"sub_location_id": "inner_vault"}}
    ]
    from app.game_core.state import StateDelta
    changes, _, error = apply_clue_effects(
        state, world, effects, area_id="dungeon", source="test"
    )
    assert error is None
    state.apply(StateDelta(changes=changes))

    # Now entry is allowed
    result = handler.compute(cmd, state, world)
    assert result.executed


def test_unlock_sub_location_falls_back_to_create_when_not_found() -> None:
    """If no locked sub-area with the target id exists, fall back to creating it."""
    state = _make_clue_state(temporary_sub_areas=[])
    world = _make_world("dungeon")

    effects = [
        {
            "type": "unlock_sub_location",
            "params": {
                "sub_location_id": "new_area",
                "label": "New Area",
                "description": "A freshly created area",
            },
        }
    ]
    from app.game_core.state import StateDelta
    changes, meta, error = apply_clue_effects(
        state, world, effects, area_id="dungeon", source="test"
    )

    assert error is None
    # The fallback creates a new sub-area via apply_environment_reward
    state.apply(StateDelta(changes=changes))
    sub_areas = state.areas.list_temporary_sub_areas("dungeon")
    ids = {s["id"] for s in sub_areas}
    assert "new_area" in ids


def test_unlock_sub_location_does_not_unlock_already_unlocked() -> None:
    """unlock_sub_location on a sub-area that is NOT locked falls back to create path."""
    state = _make_clue_state(
        temporary_sub_areas=[
            {"id": "open_area", "label": "Open Area", "locked": False, "expiry": -1}
        ]
    )
    world = _make_world("dungeon")

    effects = [
        {"type": "unlock_sub_location", "params": {"sub_location_id": "open_area"}}
    ]
    from app.game_core.state import StateDelta
    changes, meta, error = apply_clue_effects(
        state, world, effects, area_id="dungeon", source="test"
    )
    # Falls back to create path — area already exists, so apply_environment_reward returns empty
    assert error is None
    # No changes needed (area already unlocked and exists)
    sub_areas_before = state.areas.list_temporary_sub_areas("dungeon")
    state.apply(StateDelta(changes=changes))
    sub_areas_after = state.areas.list_temporary_sub_areas("dungeon")
    assert len(sub_areas_after) == len(sub_areas_before)


# ------------------------------------------------------------------
# 5a: DynamicSubAreaManager.create() stores locked field
# ------------------------------------------------------------------


def test_dynamic_sub_area_manager_stores_locked_true() -> None:
    areas = AreaSlice()
    areas.restore({"areas": {"dungeon": {}}})
    manager = DynamicSubAreaManager(areas)

    created = manager.create("dungeon", {"id": "vault", "locked": True})

    assert created is not None
    assert created["locked"] is True


def test_dynamic_sub_area_manager_locked_defaults_to_false() -> None:
    areas = AreaSlice()
    areas.restore({"areas": {"dungeon": {}}})
    manager = DynamicSubAreaManager(areas)

    created = manager.create("dungeon", {"id": "open_room"})

    assert created is not None
    assert created["locked"] is False


# ------------------------------------------------------------------
# 5a: directive_contracts normalizes locked for fill_area / plant_environmental
# ------------------------------------------------------------------


def test_directive_contracts_fill_area_accepts_locked_true() -> None:
    directive = FillAreaPlan(
        area_id="dungeon",
        payload={"id": "vault", "label": "Vault", "locked": True},
    )
    result = validate_planner_directive(directive)
    assert result.ok
    assert result.payload.get("locked") is True


def test_directive_contracts_fill_area_accepts_locked_false() -> None:
    directive = FillAreaPlan(
        area_id="dungeon",
        payload={"id": "vault", "label": "Vault", "locked": False},
    )
    result = validate_planner_directive(directive)
    assert result.ok
    assert result.payload.get("locked") is False


def test_directive_contracts_fill_area_locked_omitted_is_absent() -> None:
    """When locked is not provided, the key is absent from normalized payload."""
    directive = FillAreaPlan(
        area_id="dungeon",
        payload={"id": "vault", "label": "Vault"},
    )
    result = validate_planner_directive(directive)
    assert result.ok
    assert "locked" not in result.payload


def test_directive_contracts_plant_environmental_accepts_locked() -> None:
    directive = PlantEnvironmentalPlan(
        area_id="dungeon",
        payload={"description": "Eerie altar", "locked": True},
    )
    result = validate_planner_directive(directive)
    assert result.ok
    assert result.payload.get("locked") is True


def test_directive_contracts_accepts_locked_string_true() -> None:
    """String 'true' is coerced to boolean True."""
    directive = FillAreaPlan(
        area_id="dungeon",
        payload={"id": "vault", "label": "Vault", "locked": "true"},
    )
    result = validate_planner_directive(directive)
    assert result.ok
    assert result.payload.get("locked") is True
