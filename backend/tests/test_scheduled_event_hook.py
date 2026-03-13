"""Tests for ScheduledEventHook."""

from __future__ import annotations

import asyncio

from app.game_core.content import WorldInstance
from app.game_core.orchestration.hooks.scheduled_event import ScheduledEventHook
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules import RulesEngine
from app.game_core.state import StateContainer, StateDelta
from app.game_core.state.slices import EventSlice, FlagSlice, PlayerSlice, SceneSlice, TimeSlice


def _make_context() -> SettlementContext:
    state = StateContainer()

    time_slice = TimeSlice()
    time_slice.restore({"day": 2, "slot": 9})
    state.register(time_slice)

    player = PlayerSlice()
    player.restore({"current_area": "forest", "current_location": "camp", "current_room": "tent"})
    state.register(player)

    flags = FlagSlice()
    flags.restore({"flags": {"quest_started": True}})
    state.register(flags)

    events = EventSlice()
    events.restore({})
    state.register(events)

    scene = SceneSlice()
    scene.restore({})
    state.register(scene)
    scene_bus = SceneBus(scene)

    def _apply_delta(delta: StateDelta | None) -> None:
        del delta

    return SettlementContext(
        change_log=[],
        state=state,
        world=WorldInstance("test_world"),
        scene_bus=scene_bus,
        _rules_engine=RulesEngine(),
        _apply_delta=_apply_delta,
    )


class TestScheduledEventHook:
    def test_period_reached_pending_event_triggers(self) -> None:
        context = _make_context()
        context.state.events.restore(
            {
                "pending_events": [
                    {
                        "event_id": "evt_period",
                        "event_type": "story",
                        "trigger_condition": {"type": "period_reached", "period": "day"},
                    }
                ]
            }
        )

        result = asyncio.run(ScheduledEventHook().execute(context))

        assert result.metadata["triggered_count"] == 1
        assert context.state.events.get_event("evt_period")["state"] == "triggered"

    def test_location_entered_pending_event_triggers(self) -> None:
        context = _make_context()
        context.state.events.restore(
            {
                "pending_events": [
                    {
                        "event_id": "evt_location",
                        "event_type": "story",
                        "trigger_condition": {"type": "location_entered", "area_id": "forest"},
                    }
                ]
            }
        )

        result = asyncio.run(ScheduledEventHook().execute(context))

        assert result.metadata["triggered_count"] == 1
        assert context.state.events.get_event("evt_location")["state"] == "triggered"

    def test_room_location_entered_pending_event_triggers(self) -> None:
        context = _make_context()
        context.state.events.restore(
            {
                "pending_events": [
                    {
                        "event_id": "evt_room",
                        "event_type": "story",
                        "trigger_condition": {
                            "type": "location_entered",
                            "area_id": "forest",
                            "location_id": "camp",
                            "room_id": "tent",
                        },
                    }
                ]
            }
        )

        result = asyncio.run(ScheduledEventHook().execute(context))

        assert result.metadata["triggered_count"] == 1
        assert context.state.events.get_event("evt_room")["state"] == "triggered"

    def test_flag_set_pending_event_triggers(self) -> None:
        context = _make_context()
        context.state.events.restore(
            {
                "pending_events": [
                    {
                        "event_id": "evt_flag",
                        "event_type": "story",
                        "trigger_condition": {"type": "flag_set", "key": "quest_started"},
                    }
                ]
            }
        )

        result = asyncio.run(ScheduledEventHook().execute(context))

        assert result.metadata["triggered_count"] == 1
        assert context.state.events.get_event("evt_flag")["state"] == "triggered"
