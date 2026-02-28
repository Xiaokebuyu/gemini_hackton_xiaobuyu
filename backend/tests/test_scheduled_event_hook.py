"""Tests for ScheduledEventHook."""

from __future__ import annotations

import asyncio
from typing import Any

from app.game_core.content import WorldInstance
from app.game_core.orchestration.hooks import ScheduledEventHook
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules import RulesEngine
from app.game_core.state import StateContainer
from app.game_core.state.slices import EventSlice, SceneSlice, TimeSlice


def _make_context(*, day: int = 1, slot: int = 8) -> SettlementContext:
    state = StateContainer()

    time_slice = TimeSlice()
    time_slice.restore({"day": day, "slot": slot})
    state.register(time_slice)

    event_slice = EventSlice()
    event_slice.restore({})
    state.register(event_slice)

    scene_slice = SceneSlice()
    state.register(scene_slice)

    return SettlementContext(
        change_log=[],
        state=state,
        world=WorldInstance("test_world"),
        scene_bus=SceneBus(scene_slice),
        _rules_engine=RulesEngine(),
        _apply_delta=lambda delta: None,
    )


class TestScheduledEventHook:
    def test_due_event_moves_from_pending_to_active(self) -> None:
        context = _make_context()
        current_tick = context.state.time.absolute_tick()
        context.state.events.restore(
            {
                "pending_events": [
                    {
                        "event_id": "evt_1",
                        "event_type": "quest",
                        "trigger_tick": current_tick,
                        "payload": {"quest_id": "q1"},
                        "metadata": {"priority": "high"},
                        "source": "engine",
                    }
                ]
            }
        )

        result = asyncio.run(ScheduledEventHook().execute(context))

        assert context.state.events.pending_events == []
        assert "evt_1" in context.state.events.active_events
        assert context.state.events.active_events["evt_1"]["state"] == "triggered"
        assert context.state.events.active_events["evt_1"]["status"] == "triggered"
        assert len(result.sse_events) == 1
        assert result.sse_events[0].event_type == "event_triggered"
        assert result.metadata["triggered_count"] == 1

    def test_no_due_events_returns_noop(self) -> None:
        context = _make_context()
        current_tick = context.state.time.absolute_tick()
        context.state.events.restore(
            {
                "pending_events": [
                    {
                        "event_id": "evt_1",
                        "trigger_tick": current_tick + 1,
                    }
                ]
            }
        )

        result = asyncio.run(ScheduledEventHook().execute(context))

        assert result.metadata == {"status": "noop", "triggered_count": 0}
        assert context.state.events.active_events == {}
        assert len(context.state.events.pending_events) == 1

    def test_multiple_due_events_trigger_together(self) -> None:
        context = _make_context()
        current_tick = context.state.time.absolute_tick()
        context.state.events.restore(
            {
                "pending_events": [
                    {"event_id": "evt_1", "trigger_tick": current_tick},
                    {"event_id": "evt_2", "trigger_tick": current_tick - 1},
                ]
            }
        )

        result = asyncio.run(ScheduledEventHook().execute(context))

        assert result.metadata["triggered_count"] == 2
        assert result.metadata["event_ids"] == ["evt_1", "evt_2"]
        assert set(context.state.events.active_events) == {"evt_1", "evt_2"}

    def test_invalid_event_without_id_is_skipped(self) -> None:
        context = _make_context()
        current_tick = context.state.time.absolute_tick()
        context.state.events.restore(
            {
                "pending_events": [
                    {"trigger_tick": current_tick},
                    {"event_id": "evt_ok", "trigger_tick": current_tick},
                ]
            }
        )

        result = asyncio.run(ScheduledEventHook().execute(context))

        assert result.metadata["triggered_count"] == 1
        assert result.metadata["skipped_invalid_count"] == 1
        assert result.metadata["event_ids"] == ["evt_ok"]
        assert set(context.state.events.active_events) == {"evt_ok"}

    def test_missing_or_invalid_fields_fall_back_to_defaults(self) -> None:
        context = _make_context(day=2, slot=3)
        current_tick = context.state.time.absolute_tick()
        context.state.events.restore(
            {
                "pending_events": [
                    {
                        "event_id": "evt_defaulted",
                        "trigger_tick": current_tick,
                        "payload": ["bad"],
                        "metadata": "bad",
                    }
                ]
            }
        )

        result = asyncio.run(ScheduledEventHook().execute(context))
        active_event: dict[str, Any] = context.state.events.active_events["evt_defaulted"]

        assert active_event["event_type"] == "generic"
        assert active_event["payload"] == {}
        assert active_event["metadata"] == {}
        assert active_event["trigger_tick"] == current_tick
        assert active_event["triggered_at"] == {"day": 2, "slot": 3, "period": "night"}
        assert result.sse_events[0].payload["event_type"] == "generic"
