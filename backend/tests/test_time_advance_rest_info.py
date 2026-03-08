"""Tests for TimeAdvanceHook.rest_info SSE payload field (Block E)."""

from __future__ import annotations

import asyncio

from app.game_core.content import WorldInstance
from app.game_core.orchestration.hooks import TimeAdvanceHook
from app.game_core.orchestration.hooks.rest_phase import RestPhaseInfo
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules import RulesEngine
from app.game_core.state import StateContainer
from app.game_core.state.slices import SceneSlice, TimeSlice


def _make_context(
    *,
    slot: int = 8,
    accumulated: float = 1.0,
    rest_phase: RestPhaseInfo | None = None,
) -> SettlementContext:
    state = StateContainer()
    time_slice = TimeSlice()
    time_slice.restore({"day": 1, "slot": slot, "accumulated": accumulated})
    state.register(time_slice)

    scene_slice = SceneSlice()
    scene_slice.restore({})
    state.register(scene_slice)

    ctx = SettlementContext(
        change_log=[],
        state=state,
        world=WorldInstance("test_world"),
        scene_bus=SceneBus(scene_slice),
        _rules_engine=RulesEngine(),
        _apply_delta=lambda delta: None,
    )
    ctx.rest_phase = rest_phase
    return ctx


def _make_rest_phase(
    *,
    slot_index: int = 1,
    total_slots: int = 8,
    is_final: bool = False,
    rest_action_type: str = "rest_long",
) -> RestPhaseInfo:
    slots_remaining = total_slots - slot_index + 1
    return RestPhaseInfo(
        tick_kind="rest",
        is_rest_tick=True,
        rest_action_type=rest_action_type,
        rest_total_slots=total_slots,
        rest_slots_remaining=slots_remaining,
        rest_slot_index=slot_index,
        is_final_rest_slot=is_final,
        camp_type=None,
        night_watch_required=None,
        crossed_day=False,
    )


class TestTimeAdvanceHookRestInfo:
    def test_normal_tick_rest_info_is_none(self) -> None:
        """Non-rest tick: rest_info absent (None) in SSE payload."""
        context = _make_context(slot=8, accumulated=1.0, rest_phase=None)

        result = asyncio.run(TimeAdvanceHook().execute(context))

        assert result.sse_events, "Expected at least one SSE event"
        payload = result.sse_events[0].payload
        assert payload["rest_info"] is None

    def test_rest_tick_payload_contains_rest_info(self) -> None:
        """Long rest tick: SSE payload has rest_info with correct fields."""
        rest_phase = _make_rest_phase(slot_index=3, total_slots=8, is_final=False)
        context = _make_context(slot=10, accumulated=1.0, rest_phase=rest_phase)

        result = asyncio.run(TimeAdvanceHook().execute(context))

        assert result.sse_events, "Expected at least one SSE event"
        payload = result.sse_events[0].payload
        rest_info = payload["rest_info"]
        assert rest_info is not None
        assert rest_info["rest_type"] == "rest_long"
        assert rest_info["slot_index"] == 3
        assert rest_info["total_slots"] == 8
        assert isinstance(rest_info["is_quiet"], bool)
        assert isinstance(rest_info["is_final"], bool)
        assert rest_info["is_final"] is False

    def test_final_rest_slot_is_final_true(self) -> None:
        """Final rest slot: is_final=True in rest_info."""
        rest_phase = _make_rest_phase(slot_index=8, total_slots=8, is_final=True)
        context = _make_context(slot=15, accumulated=1.0, rest_phase=rest_phase)

        result = asyncio.run(TimeAdvanceHook().execute(context))

        assert result.sse_events
        rest_info = result.sse_events[0].payload["rest_info"]
        assert rest_info is not None
        assert rest_info["is_final"] is True
        assert rest_info["slot_index"] == 8
        assert rest_info["total_slots"] == 8
