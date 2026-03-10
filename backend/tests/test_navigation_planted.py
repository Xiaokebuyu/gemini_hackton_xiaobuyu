"""Tests for NavigationHandler Phase 8 planted encounter detection.

Decision record: D-R41 (rules_engine.md)
"""
from __future__ import annotations

from app.game_core.content import WorldInstance
from app.game_core.content.registries import MapRegistry
from app.game_core.rules import Command, RulesEngine
from app.game_core.rules.handlers import NavigationHandler
from app.game_core.state import StateContainer
from app.game_core.state.slices import AreaSlice, PlayerSlice, TimeSlice


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_world() -> WorldInstance:
    world = WorldInstance("test_world")
    maps = MapRegistry()
    maps.load(
        {
            "forest": {
                "id": "forest",
                "name": "Forest",
                "connections": [],
                "sub_locations": {
                    "dark_grove": {"id": "dark_grove", "name": "Dark Grove"},
                    "stream": {"id": "stream", "name": "Stream"},
                },
            },
        }
    )
    world.register(maps)
    return world


def _make_state(
    *,
    area: str = "forest",
    planted_sub: str | None = None,
    planted_entry: dict | None = None,
    absolute_tick: int = 10,
) -> StateContainer:
    state = StateContainer()

    time_slice = TimeSlice()
    # absolute_tick = (day-1)*24 + slot; set day=1, slot=absolute_tick
    time_slice.restore({"day": 1, "slot": absolute_tick})
    state.register(time_slice)

    player = PlayerSlice()
    player.restore({"current_area": area, "current_location": None})
    state.register(player)

    areas = AreaSlice()
    area_data: dict = {"areas": {area: {}}}
    areas.restore(area_data)
    if planted_sub is not None and planted_entry is not None:
        areas.upsert_hostile(planted_sub, planted_entry)
    state.register(areas)

    return state


def _make_engine() -> RulesEngine:
    engine = RulesEngine()
    engine.register(NavigationHandler())
    return engine


def _apply(result, state: StateContainer) -> None:
    assert result.delta is not None
    state.apply(result.delta)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestNavigationPlantedEncounter:
    def test_enter_sub_location_detects_planted(self) -> None:
        """Entering a sub-location with a planted entry activates it to 'spotted'
        and populates encounter_spotted in result metadata."""
        planted_entry = {
            "area_id": "forest",
            "status": "planted",
            "combat_active": False,
            "cleared": False,
            "blocking": True,
            "monster_ids": ["goblin", "goblin"],
            "threat_level": "moderate",
            "surprise_modifier": 0,
            "map_category": "woodland",
            "map_tags": ["trees"],
            "description": "Goblins hiding in the dark grove.",
            "one_shot": True,
            "created_at_tick": 5,
            "expiry_ticks": -1,
            "source": "narrative_planner",
        }
        state = _make_state(
            area="forest",
            planted_sub="dark_grove",
            planted_entry=planted_entry,
            absolute_tick=10,
        )
        engine = _make_engine()

        result = engine.execute(
            Command(type="enter_sub_location", params={"location_id": "dark_grove"}),
            state,
            _make_world(),
        )

        assert result.executed is True
        assert "encounter_spotted" in result.metadata
        spotted = result.metadata["encounter_spotted"]
        assert spotted["sub_area_id"] == "dark_grove"
        assert spotted["area_id"] == "forest"
        assert spotted["monster_ids"] == ["goblin", "goblin"]
        assert spotted["threat_level"] == "moderate"
        assert spotted["blocking"] is True
        assert spotted["map_category"] == "woodland"
        assert spotted["source"] == "plant_encounter"

        # The state change should update status to "spotted"
        _apply(result, state)
        hostile_after = state.areas.get_hostile_state("dark_grove")
        assert hostile_after is not None
        assert hostile_after["status"] == "spotted"

    def test_enter_sub_location_no_planted(self) -> None:
        """Entering a sub-location without a planted entry results in normal navigation."""
        state = _make_state(area="forest", absolute_tick=5)
        engine = _make_engine()

        result = engine.execute(
            Command(type="enter_sub_location", params={"location_id": "stream"}),
            state,
            _make_world(),
        )

        assert result.executed is True
        assert "encounter_spotted" not in result.metadata
        assert result.metadata["to_location"] == "stream"

    def test_enter_sub_location_planted_expired(self) -> None:
        """An expired planted entry should be marked expired/cleared, not activated."""
        planted_entry = {
            "area_id": "forest",
            "status": "planted",
            "combat_active": False,
            "cleared": False,
            "blocking": True,
            "monster_ids": ["wolf"],
            "threat_level": "low",
            "surprise_modifier": 0,
            "map_category": None,
            "map_tags": [],
            "description": "",
            "one_shot": True,
            "created_at_tick": 0,    # created at tick 0
            "expiry_ticks": 5,       # expires after 5 ticks
            "source": "narrative_planner",
        }
        # current tick = 10, created = 0, expiry = 5 → (10 - 0) = 10 >= 5 → expired
        state = _make_state(
            area="forest",
            planted_sub="dark_grove",
            planted_entry=planted_entry,
            absolute_tick=10,
        )
        engine = _make_engine()

        result = engine.execute(
            Command(type="enter_sub_location", params={"location_id": "dark_grove"}),
            state,
            _make_world(),
        )

        assert result.executed is True
        assert "encounter_spotted" not in result.metadata, "Expired encounter must not activate"

        # After applying, status should be "expired" and cleared=True
        _apply(result, state)
        hostile_after = state.areas.get_hostile_state("dark_grove")
        assert hostile_after is not None
        assert hostile_after["status"] == "expired"
        assert hostile_after["cleared"] is True

    def test_enter_sub_location_planted_cleared_skipped(self) -> None:
        """A planted entry with cleared=True should not activate."""
        planted_entry = {
            "area_id": "forest",
            "status": "planted",
            "combat_active": False,
            "cleared": True,          # already cleared
            "blocking": True,
            "monster_ids": ["troll"],
            "threat_level": "high",
            "surprise_modifier": 0,
            "map_category": None,
            "map_tags": [],
            "description": "",
            "one_shot": True,
            "created_at_tick": 5,
            "expiry_ticks": -1,
            "source": "narrative_planner",
        }
        state = _make_state(
            area="forest",
            planted_sub="dark_grove",
            planted_entry=planted_entry,
            absolute_tick=10,
        )
        engine = _make_engine()

        result = engine.execute(
            Command(type="enter_sub_location", params={"location_id": "dark_grove"}),
            state,
            _make_world(),
        )

        assert result.executed is True
        assert "encounter_spotted" not in result.metadata, "Cleared encounter must not re-activate"
