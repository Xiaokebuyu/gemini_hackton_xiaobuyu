"""Tests for RestHandler."""

from __future__ import annotations

from app.game_core.content import WorldInstance
from app.game_core.orchestration.defaults import build_default_action_dispatcher
from app.game_core.orchestration.models import StructuredAction
from app.game_core.rules import Command, RulesEngine
from app.game_core.rules.handlers import RestHandler
from app.game_core.state import StateContainer
from app.game_core.state.slices import AreaSlice, PlayerSlice, TimeSlice


def _make_world() -> WorldInstance:
    return WorldInstance("test_world")


def _make_state(
    *,
    hp: int = 6,
    max_hp: int = 20,
    area_properties: dict | None = None,
    danger_level: float = 1.2,
    slot: int = 21,
    stats: dict[str, int] | None = None,
    active_effects: list[dict] | None = None,
) -> StateContainer:
    state = StateContainer()

    player = PlayerSlice()
    player.restore(
        {
            "character_id": "pc_1",
            "hp": hp,
            "max_hp": max_hp,
            "current_area": "forest",
            "current_location": "camp",
            "stats": stats
            or {
                "str": 10,
                "dex": 12,
                "con": 12,
                "int": 10,
                "wis": 12,
                "cha": 10,
            },
            "spell_slots": {"1": {"current": 0, "max": 2}},
            "class_resources": {
                "ki": {"current": 0, "max": 3, "recovery": "short_rest"},
                "rage": {"current": 0, "max": 2, "recovery": "long_rest"},
            },
            "concentration": {"spell_id": "hex"},
            "active_effects": active_effects
            or [
                {"effect_id": "fatigue", "cure_conditions": ["short_rest"]},
                {"effect_id": "curse", "cure_conditions": ["long_rest"]},
                {"effect_id": "bless"},
            ],
        }
    )
    state.register(player)

    areas = AreaSlice()
    areas.restore(
        {
            "areas": {
                "forest": {
                    "danger_level": danger_level,
                    "properties": area_properties or {},
                }
            }
        }
    )
    state.register(areas)

    time_slice = TimeSlice()
    time_slice.restore({"day": 1, "slot": slot})
    state.register(time_slice)

    return state


def _make_engine() -> RulesEngine:
    engine = RulesEngine()
    engine.register(RestHandler())
    return engine


def _apply(result, state: StateContainer) -> None:
    if result.delta is None:
        return
    state.apply(result.delta)


class TestRestHandler:
    def test_default_action_dispatcher_routes_rest_short(self) -> None:
        dispatcher = build_default_action_dispatcher()

        command = dispatcher.dispatch(StructuredAction(action_type="rest_short"))

        assert command is not None
        assert command.type == "rest_short"

    def test_rest_short_heals_and_restores_short_rest_resources(self) -> None:
        state = _make_state()
        result = _make_engine().execute(
            Command(type="rest_short"),
            state,
            _make_world(),
        )

        assert result.executed is True
        assert result.time_cost == 1.0
        assert result.metadata["status"] == "short_rest"
        assert result.metadata["healed"] == 5
        assert result.metadata["restored_resource_keys"] == ["ki"]
        assert result.metadata["removed_effect_count"] == 1
        _apply(result, state)
        assert state.player.hp == 11
        assert state.player.class_resources["ki"]["current"] == 3
        assert state.player.class_resources["rage"]["current"] == 0
        remaining_ids = {effect["effect_id"] for effect in state.player.active_effects}
        assert remaining_ids == {"curse", "bless"}

    def test_rest_long_restores_full_resources_and_uses_active_camp(self) -> None:
        state = _make_state(
            area_properties={"active_camp": {"camp_type": "safe"}},
            active_effects=[
                {"effect_id": "curse", "cure_conditions": ["long_rest"]},
                {"effect_id": "bless"},
            ],
        )
        result = _make_engine().execute(
            Command(type="rest_long"),
            state,
            _make_world(),
        )

        assert result.executed is True
        assert result.metadata["status"] == "long_rest"
        assert result.metadata["camp_type"] == "safe"
        assert result.metadata["night_watch_required"] is False
        assert result.metadata["broke_concentration"] is True
        assert result.metadata["removed_effect_count"] == 1
        assert result.time_cost == 8.0
        _apply(result, state)
        assert state.player.hp == 20
        assert state.player.spell_slots[1]["current"] == 2
        assert state.player.class_resources["ki"]["current"] == 3
        assert state.player.class_resources["rage"]["current"] == 2
        assert state.player.concentration is None
        remaining_ids = {effect["effect_id"] for effect in state.player.active_effects}
        assert remaining_ids == {"bless"}

    def test_night_watch_skips_safe_camp(self) -> None:
        state = _make_state(area_properties={"active_camp": {"camp_type": "safe"}})
        result = _make_engine().execute(
            Command(type="night_watch", params={"area_id": "forest"}),
            state,
            _make_world(),
        )

        assert result.executed is True
        assert result.delta is None
        assert result.metadata["status"] == "skipped_safe_camp"
        assert result.metadata["ambush"] is False

    def test_night_watch_uses_deterministic_passive_perception(self) -> None:
        state = _make_state(
            danger_level=1.2,
            stats={
                "str": 10,
                "dex": 12,
                "con": 12,
                "int": 10,
                "wis": 8,
                "cha": 10,
            },
        )
        result = _make_engine().execute(
            Command(type="night_watch", params={"area_id": "forest", "camp_type": "wilderness"}),
            state,
            _make_world(),
        )

        assert result.executed is True
        assert result.metadata["status"] == "checked"
        assert result.metadata["dc"] == 15
        assert result.metadata["passive_total"] == 11
        assert result.metadata["passed"] is False
        assert result.metadata["ambush"] is True
        assert result.metadata["recovery_multiplier"] == 0.75

    def test_set_camp_writes_active_camp_payload(self) -> None:
        state = _make_state(area_properties={})
        result = _make_engine().execute(
            Command(type="set_camp", params={"area_id": "forest", "camp_type": "safe"}),
            state,
            _make_world(),
        )

        assert result.executed is True
        assert result.time_cost == 1.0 / 6.0
        assert result.metadata["status"] == "camp_set"
        _apply(result, state)
        active_camp = state.areas.get_area("forest").properties["active_camp"]
        assert active_camp["camp_type"] == "safe"
        assert active_camp["location_id"] == "camp"
        assert active_camp["created_at_tick"] == 21
