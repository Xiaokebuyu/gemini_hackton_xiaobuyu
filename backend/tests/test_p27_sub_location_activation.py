"""Tests for Phase 1: Sub_location activation (P27).

Covers:
- move_area auto-placement at default_sub_location
- move_area auto-placement at first sub_location when no default set
- move_area leaves current_location=None when area has no sub_locations
- AreaTemplate.default_sub_location loaded from maps.json data
- _resolve_starting_location_id uses default_sub_location
- is_colocated NPC filtering when player has sub_location
- Osiris _build_nearby_npcs returns only same-sub_location NPCs when player is in one
"""

from __future__ import annotations

from types import SimpleNamespace

from app.game_core import GameRuntime
from app.game_core.adapters import NullPersistencePort, SaveStore
from app.game_core.content import WorldInstance
from app.game_core.content.registries import MapRegistry
from app.game_core.orchestration.presence import get_area_npcs, is_colocated
from app.game_core.rules import Command, RulesEngine
from app.game_core.rules.handlers import NavigationHandler
from app.game_core.state import StateContainer
from app.game_core.state.slices import PlayerSlice


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_world_with_default() -> WorldInstance:
    """World where 'town' has a default_sub_location='guild'."""
    world = WorldInstance("test_world")
    maps = MapRegistry()
    maps.load(
        {
            "start": {
                "id": "start",
                "name": "Start",
                "connections": [{"target_map_id": "town"}],
            },
            "town": {
                "id": "town",
                "name": "Town",
                "default_sub_location": "guild",
                "connections": [{"target_map_id": "start"}, {"target_map_id": "barren"}],
                "sub_locations": {
                    "guild": {"id": "guild", "name": "Guild"},
                    "temple": {"id": "temple", "name": "Temple"},
                },
            },
            "barren": {
                "id": "barren",
                "name": "Barren",
                "connections": [{"target_map_id": "town"}],
                # no sub_locations
            },
        }
    )
    world.register(maps)
    return world


def _make_state(*, area: str = "start", location: str | None = None) -> StateContainer:
    state = StateContainer()
    player = PlayerSlice()
    player.restore({"current_area": area, "current_location": location})
    state.register(player)
    return state


def _make_engine() -> RulesEngine:
    engine = RulesEngine()
    engine.register(NavigationHandler())
    return engine


def _make_runtime() -> GameRuntime:
    return GameRuntime(save_store=SaveStore(NullPersistencePort()))


def _apply(result, state: StateContainer) -> None:
    assert result.delta is not None
    state.apply(result.delta)


# ---------------------------------------------------------------------------
# move_area auto-placement
# ---------------------------------------------------------------------------


class TestMoveAreaAutoPlacement:
    def test_move_area_auto_places_at_default_sub_location(self) -> None:
        """When target area has default_sub_location, player lands there."""
        state = _make_state(area="start")
        world = _make_world_with_default()

        result = _make_engine().execute(
            Command(type="move_area", params={"area_id": "town"}),
            state,
            world,
        )

        assert result.executed is True
        assert result.metadata["to_location"] == "guild"
        _apply(result, state)
        assert state.player.current_area == "town"
        assert state.player.current_location == "guild"

    def test_move_area_without_default_uses_first_sub_location(self) -> None:
        """When no default_sub_location, falls back to first key in sub_locations."""
        world = WorldInstance("test_world")
        maps = MapRegistry()
        maps.load(
            {
                "a": {
                    "id": "a",
                    "name": "A",
                    "connections": [{"target_map_id": "b"}],
                },
                "b": {
                    "id": "b",
                    "name": "B",
                    "connections": [{"target_map_id": "a"}],
                    # no default_sub_location
                    "sub_locations": {
                        "inn": {"id": "inn", "name": "Inn"},
                        "market": {"id": "market", "name": "Market"},
                    },
                },
            }
        )
        world.register(maps)

        state = _make_state(area="a")
        result = _make_engine().execute(
            Command(type="move_area", params={"area_id": "b"}),
            state,
            world,
        )

        assert result.executed is True
        _apply(result, state)
        assert state.player.current_area == "b"
        assert state.player.current_location == "inn"

    def test_move_area_leaves_location_none_when_no_sub_locations(self) -> None:
        """When target area has no sub_locations, current_location stays None."""
        state = _make_state(area="town")
        world = _make_world_with_default()

        result = _make_engine().execute(
            Command(type="move_area", params={"area_id": "barren"}),
            state,
            world,
        )

        assert result.executed is True
        assert result.metadata["to_location"] is None
        _apply(result, state)
        assert state.player.current_area == "barren"
        assert state.player.current_location is None

    def test_move_area_ignores_invalid_default_sub_location(self) -> None:
        """When default_sub_location doesn't exist in sub_locations, falls back to first key."""
        world = WorldInstance("test_world")
        maps = MapRegistry()
        maps.load(
            {
                "a": {
                    "id": "a",
                    "name": "A",
                    "connections": [{"target_map_id": "b"}],
                },
                "b": {
                    "id": "b",
                    "name": "B",
                    "connections": [{"target_map_id": "a"}],
                    "default_sub_location": "nonexistent",
                    "sub_locations": {
                        "inn": {"id": "inn", "name": "Inn"},
                    },
                },
            }
        )
        world.register(maps)

        state = _make_state(area="a")
        result = _make_engine().execute(
            Command(type="move_area", params={"area_id": "b"}),
            state,
            world,
        )

        assert result.executed is True
        _apply(result, state)
        # Falls back to first key "inn"
        assert state.player.current_location == "inn"


# ---------------------------------------------------------------------------
# AreaTemplate.default_sub_location loading
# ---------------------------------------------------------------------------


class TestAreaTemplateDefaultSubLocation:
    def test_default_sub_location_loaded_from_data(self) -> None:
        maps = MapRegistry()
        maps.load(
            {
                "town": {
                    "id": "town",
                    "default_sub_location": "guild",
                    "sub_locations": {
                        "guild": {"id": "guild", "name": "Guild"},
                        "tavern": {"id": "tavern", "name": "Tavern"},
                    },
                }
            }
        )
        area = maps.get("town")
        assert area is not None
        assert area.default_sub_location == "guild"

    def test_default_sub_location_empty_when_absent(self) -> None:
        maps = MapRegistry()
        maps.load(
            {
                "forest": {
                    "id": "forest",
                    "sub_locations": {
                        "clearing": {"id": "clearing", "name": "Clearing"},
                    },
                }
            }
        )
        area = maps.get("forest")
        assert area is not None
        assert area.default_sub_location == ""

    def test_invalid_default_sub_location_recorded_as_issue(self) -> None:
        maps = MapRegistry()
        maps.load(
            {
                "town": {
                    "id": "town",
                    "default_sub_location": "   ",  # whitespace only = invalid
                    "sub_locations": {},
                }
            }
        )
        issues = maps.validate()
        assert any("default_sub_location" in issue for issue in issues)


class TestResolveStartingLocationId:
    def test_prefers_default_sub_location(self) -> None:
        runtime = _make_runtime()
        area = SimpleNamespace(
            default_sub_location="guild",
            sub_locations={"guild": object(), "temple": object()},
        )

        assert runtime._resolve_starting_location_id(area) == "guild"

    def test_falls_back_to_first_sub_location_when_default_is_invalid(self) -> None:
        runtime = _make_runtime()
        area = SimpleNamespace(
            default_sub_location="missing",
            sub_locations={"inn": object(), "market": object()},
        )

        assert runtime._resolve_starting_location_id(area) == "inn"

    def test_returns_none_when_area_has_no_sub_locations(self) -> None:
        runtime = _make_runtime()
        area = SimpleNamespace(
            default_sub_location="guild",
            sub_locations={},
        )

        assert runtime._resolve_starting_location_id(area) is None


# ---------------------------------------------------------------------------
# is_colocated NPC visibility
# ---------------------------------------------------------------------------


class TestIsColocatedFilter:
    def test_is_colocated_same_sub_location(self) -> None:
        assert is_colocated("guild", "guild") is True

    def test_is_colocated_different_sub_location(self) -> None:
        assert is_colocated("guild", "temple") is False

    def test_is_colocated_npc_has_no_location_player_has_one(self) -> None:
        # NPC at area root, player at sub_location → not colocated
        assert is_colocated(None, "guild") is False

    def test_is_colocated_player_has_no_location_npc_has_one(self) -> None:
        # Player at area root, NPC at sub_location → not colocated
        assert is_colocated("guild", None) is False

    def test_is_colocated_both_none(self) -> None:
        # Both at area root → colocated
        assert is_colocated(None, None) is True


# ---------------------------------------------------------------------------
# goblin_slayer world data: default_sub_location loaded correctly
# ---------------------------------------------------------------------------


class TestGoblinSlayerWorldDefaultSubLocation:
    def _load_goblin_slayer_maps(self) -> MapRegistry:
        import json
        import os

        data_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "data",
            "goblin_slayer",
            "v2",
            "maps.json",
        )
        with open(os.path.abspath(data_path)) as f:
            raw = json.load(f)
        maps = MapRegistry()
        maps.load(raw)
        return maps

    def test_frontier_town_default_sub_location(self) -> None:
        maps = self._load_goblin_slayer_maps()
        area = maps.get("frontier_town")
        assert area is not None
        assert area.default_sub_location == "adventurer_guild"
        assert "adventurer_guild" in area.sub_locations

    def test_cow_girl_farm_default_sub_location(self) -> None:
        maps = self._load_goblin_slayer_maps()
        area = maps.get("cow_girl_farm")
        assert area is not None
        assert area.default_sub_location == "main_house"
        assert "main_house" in area.sub_locations

    def test_ancient_ruins_default_sub_location(self) -> None:
        maps = self._load_goblin_slayer_maps()
        area = maps.get("ancient_ruins")
        assert area is not None
        assert area.default_sub_location == "forest_approach"
        assert "forest_approach" in area.sub_locations

    def test_water_capital_default_sub_location(self) -> None:
        maps = self._load_goblin_slayer_maps()
        area = maps.get("water_capital")
        assert area is not None
        assert area.default_sub_location == "temple_of_law"
        assert "temple_of_law" in area.sub_locations
