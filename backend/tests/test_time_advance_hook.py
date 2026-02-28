"""Tests for TimeAdvanceHook."""

from __future__ import annotations

import asyncio

from app.game_core.content import WorldInstance
from app.game_core.orchestration.hooks import TimeAdvanceHook
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules import RulesEngine
from app.game_core.state import StateContainer
from app.game_core.state.slices import SceneSlice, TimeSlice


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
        }

    def test_advance_reports_day_rollover(self) -> None:
        context = _make_context(day=1, slot=24, accumulated=1.0)

        result = asyncio.run(TimeAdvanceHook().execute(context))

        assert result.metadata["advanced"] is True
        assert result.metadata["from_tick"] == 24
        assert result.metadata["to_tick"] == 25
        assert result.metadata["crossed_day"] is True
        assert result.metadata["period_changed"] is False
        assert context.state.time.day == 2
        assert context.state.time.slot == 1
