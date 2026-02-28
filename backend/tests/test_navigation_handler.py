"""Tests for NavigationHandler."""

from __future__ import annotations

from app.game_core.content import WorldInstance
from app.game_core.content.registries import MapRegistry
from app.game_core.orchestration.defaults import build_default_action_dispatcher
from app.game_core.orchestration.models import StructuredAction
from app.game_core.rules import Command, RulesEngine
from app.game_core.rules.handlers import NavigationHandler
from app.game_core.state import StateContainer
from app.game_core.state.slices import PlayerSlice


def _make_world() -> WorldInstance:
    world = WorldInstance("test_world")
    maps = MapRegistry()
    maps.load(
        {
            "town": {"id": "town", "name": "Town"},
            "forest": {
                "id": "forest",
                "name": "Forest",
                "sub_locations": {
                    "camp": {"id": "camp", "name": "Camp"},
                },
            },
        }
    )
    world.register(maps)
    return world


def _make_state(*, area: str = "town", location: str | None = None) -> StateContainer:
    state = StateContainer()
    player = PlayerSlice()
    player.restore({"current_area": area, "current_location": location})
    state.register(player)
    return state


def _make_engine() -> RulesEngine:
    engine = RulesEngine()
    engine.register(NavigationHandler())
    return engine


def _apply(result, state: StateContainer) -> None:
    assert result.delta is not None
    state.apply(result.delta)


class TestNavigationHandler:
    def test_default_action_dispatcher_routes_move_area(self) -> None:
        dispatcher = build_default_action_dispatcher()

        command = dispatcher.dispatch(
            StructuredAction(action_type="move_area", params={"area_id": "forest"})
        )

        assert command is not None
        assert command.type == "move_area"

    def test_move_area_accepts_to_alias_and_clears_location(self) -> None:
        state = _make_state(area="town", location="inn")
        result = _make_engine().execute(
            Command(type="move_area", params={"to": "forest"}),
            state,
            _make_world(),
        )

        assert result.success is True
        assert result.time_cost == 1.0
        assert result.metadata["from_area"] == "town"
        assert result.metadata["to_area"] == "forest"
        _apply(result, state)
        assert state.player.current_area == "forest"
        assert state.player.current_location is None

    def test_move_area_rejects_from_mismatch(self) -> None:
        result = _make_engine().execute(
            Command(type="move_area", params={"area_id": "forest", "from": "cave"}),
            _make_state(area="town"),
            _make_world(),
        )

        assert result.success is False
        assert result.errors == ["from area mismatch: expected town, got cave"]

    def test_enter_sub_location_accepts_location_alias(self) -> None:
        state = _make_state(area="forest")
        result = _make_engine().execute(
            Command(type="enter_sub_location", params={"location": "camp"}),
            state,
            _make_world(),
        )

        assert result.success is True
        assert result.time_cost == 1.0 / 6.0
        assert result.metadata["area_id"] == "forest"
        _apply(result, state)
        assert state.player.current_location == "camp"

    def test_enter_sub_location_requires_current_area(self) -> None:
        result = _make_engine().execute(
            Command(type="enter_sub_location", params={"location_id": "camp"}),
            _make_state(area=""),
            _make_world(),
        )

        assert result.success is False
        assert result.errors == ["current area is required before entering a sub-location"]

    def test_leave_sub_location_succeeds_when_inside_one(self) -> None:
        state = _make_state(area="forest", location="camp")
        result = _make_engine().execute(
            Command(type="leave_sub_location"),
            state,
            _make_world(),
        )

        assert result.success is True
        assert result.time_cost == 1.0 / 12.0
        _apply(result, state)
        assert state.player.current_location is None

    def test_leave_sub_location_rejects_when_not_inside_one(self) -> None:
        result = _make_engine().execute(
            Command(type="leave_sub_location"),
            _make_state(area="forest", location=None),
            _make_world(),
        )

        assert result.success is False
        assert result.errors == ["player is not currently in a sub-location"]
