"""Tests for TimeAdvanceHook."""

from __future__ import annotations

import asyncio

from app.game_core.content import WorldInstance
from app.game_core.content.registries.characters import CharacterRegistry
from app.game_core.orchestration.hooks import TimeAdvanceHook
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules import RulesEngine, register_default_rules_handlers
from app.game_core.state import StateContainer
from app.game_core.state.slices import SceneSlice, TimeSlice
from app.game_core.state.slices.area import AreaSlice
from app.game_core.state.slices.player import PlayerSlice
from app.game_core.state.slices.relations import RelationSlice


def _make_context(
    *,
    include_time: bool = True,
    day: int = 1,
    slot: int = 8,
    accumulated: float = 0.0,
) -> SettlementContext:
    state = StateContainer()
    if include_time:
        time_slice = TimeSlice()
        time_slice.restore({"day": day, "slot": slot, "accumulated": accumulated})
        state.register(time_slice)

    scene_slice = SceneSlice()
    scene_slice.restore({})
    state.register(scene_slice)

    return SettlementContext(
        change_log=[],
        state=state,
        world=WorldInstance("test_world"),
        scene_bus=SceneBus(scene_slice),
        _rules_engine=RulesEngine(),
        _apply_delta=lambda delta: None,
    )


class TestTimeAdvanceHook:
    def test_missing_time_returns_stable_noop_metadata(self) -> None:
        result = asyncio.run(TimeAdvanceHook().execute(_make_context(include_time=False)))

        assert result.metadata == {
            "status": "noop",
            "advanced": False,
            "from_tick": None,
            "to_tick": None,
            "crossed_day": False,
            "period_changed": False,
        }
        assert result.sse_events == []

    def test_insufficient_accumulated_time_is_noop(self) -> None:
        context = _make_context(day=1, slot=9, accumulated=0.5)

        result = asyncio.run(TimeAdvanceHook().execute(context))

        assert result.metadata == {
            "status": "noop",
            "advanced": False,
            "from_tick": 9,
            "to_tick": 9,
            "crossed_day": False,
            "period_changed": False,
        }
        assert context.state.time.slot == 9
        assert context.state.time.accumulated == 0.5
        assert result.sse_events == []

    def test_advance_reports_period_change(self) -> None:
        context = _make_context(day=1, slot=17, accumulated=1.0)

        result = asyncio.run(TimeAdvanceHook().execute(context))

        assert result.metadata == {
            "status": "advanced",
            "advanced": True,
            "from_tick": 17,
            "to_tick": 18,
            "crossed_day": False,
            "period_changed": True,
            "shops_refreshed": 0,
        }
        assert context.state.time.slot == 18
        assert context.state.time.accumulated == 0.0
        assert result.sse_events[0].payload == {
            "day": 1,
            "slot": 18,
            "period": "dusk",
            "absolute_tick": 18,
            "crossed_day": False,
            "period_changed": True,
            "shops_refreshed": 0,
        }

    def test_advance_reports_day_rollover(self) -> None:
        context = _make_context(day=1, slot=24, accumulated=1.0)

        result = asyncio.run(TimeAdvanceHook().execute(context))

        assert result.metadata["advanced"] is True
        assert result.metadata["from_tick"] == 24
        assert result.metadata["to_tick"] == 25
        assert result.metadata["crossed_day"] is True
        assert result.metadata["period_changed"] is False
        assert result.metadata["shops_refreshed"] == 0  # no characters registry
        assert context.state.time.day == 2
        assert context.state.time.slot == 1


# ---------------------------------------------------------------------------
# O-G01: TimeAdvanceHook daily merchant shop refresh
# ---------------------------------------------------------------------------


def _make_merchant_context(
    *,
    day: int = 1,
    slot: int = 24,
    accumulated: float = 1.0,
    refresh_on: str | list[str] | None = "daily",
    with_areas: bool = True,
) -> SettlementContext:
    """Create a SettlementContext with a merchant character for shop-refresh tests."""
    state = StateContainer()
    time_slice = TimeSlice()
    time_slice.restore({"day": day, "slot": slot, "accumulated": accumulated})
    state.register(time_slice)

    scene_slice = SceneSlice()
    scene_slice.restore({})
    state.register(scene_slice)

    relations_slice = RelationSlice()
    relations_slice.restore({})
    state.register(relations_slice)

    if with_areas:
        areas_slice = AreaSlice()
        areas_slice.restore({"areas": {"town": {"npc_locations": {"merchant_bob": None}}}})
        state.register(areas_slice)
        player_slice = PlayerSlice()
        player_slice.restore({"current_area": "town"})
        state.register(player_slice)

    world = WorldInstance("test_world")
    char_registry = CharacterRegistry()
    char_registry.load({
        "merchant_bob": {
            "id": "merchant_bob",
            "name": "Bob",
            "area_id": "town",
            "refresh_on": refresh_on,
        }
    })
    world.register(char_registry)

    rules_engine = RulesEngine()
    register_default_rules_handlers(rules_engine)

    return SettlementContext(
        change_log=[],
        state=state,
        world=world,
        scene_bus=SceneBus(scene_slice),
        _rules_engine=rules_engine,
        _apply_delta=lambda delta: None,
    )


class TestTimeAdvanceHookShopRefresh:
    def test_no_refresh_when_no_day_cross(self) -> None:
        # slot 17 → 18: period change but no day cross
        context = _make_merchant_context(day=1, slot=17, accumulated=1.0)
        result = asyncio.run(TimeAdvanceHook().execute(context))
        assert result.metadata["crossed_day"] is False
        assert result.metadata["shops_refreshed"] == 0

    def test_refresh_daily_merchant_on_day_cross(self) -> None:
        # slot 24 → day 2 slot 1: day cross
        context = _make_merchant_context(day=1, slot=24, accumulated=1.0, refresh_on="daily")
        result = asyncio.run(TimeAdvanceHook().execute(context))
        assert result.metadata["crossed_day"] is True
        assert result.metadata["shops_refreshed"] == 1
        assert result.sse_events[0].payload["shops_refreshed"] == 1

    def test_skip_non_daily_merchant(self) -> None:
        # merchant with refresh_on="long_rest" should not be refreshed on day cross
        context = _make_merchant_context(day=1, slot=24, accumulated=1.0, refresh_on="long_rest")
        result = asyncio.run(TimeAdvanceHook().execute(context))
        assert result.metadata["crossed_day"] is True
        assert result.metadata["shops_refreshed"] == 0

    def test_refresh_fallback_without_areas_slice(self) -> None:
        # no areas slice → fallback to global scan, daily merchant still refreshed
        context = _make_merchant_context(day=1, slot=24, accumulated=1.0, with_areas=False)
        result = asyncio.run(TimeAdvanceHook().execute(context))
        assert result.metadata["crossed_day"] is True
        assert result.metadata["shops_refreshed"] == 1
