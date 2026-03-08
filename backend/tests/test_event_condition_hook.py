"""Tests for EventConditionHook."""

from __future__ import annotations

import asyncio

from app.game_core.content import WorldInstance
from app.game_core.content.registries import QuestRegistry
from app.game_core.orchestration.hooks.event_condition import EventConditionHook
from app.game_core.orchestration.hooks.event_condition import EventConditionDecision
from app.game_core.orchestration.hooks.event_condition import EventTransition
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules import Command, RulesEngine
from app.game_core.rules.handlers import WorldStateHandler
from app.game_core.state import StateChange, StateContainer, StateDelta
from app.game_core.state.slices import (
    EventSlice,
    FlagSlice,
    PartySlice,
    PlayerSlice,
    QuestSlice,
    SceneSlice,
    TimeSlice,
)


class StaticEvaluator:
    def __init__(self, decision) -> None:
        self.decision = decision

    def evaluate(self, state, world):
        del state, world
        return self.decision


class ExplodingEvaluator:
    def evaluate(self, state, world):
        del state, world
        raise RuntimeError("boom")


def _make_context() -> SettlementContext:
    world = WorldInstance("test_world")

    state = StateContainer()

    time_slice = TimeSlice()
    time_slice.restore({"day": 2, "slot": 9})
    state.register(time_slice)

    player = PlayerSlice()
    player.restore({"current_area": "forest", "current_location": "camp"})
    state.register(player)

    flags = FlagSlice()
    flags.restore({"flags": {"quest_started": True}})
    state.register(flags)

    events = EventSlice()
    events.restore({})
    state.register(events)

    quests = QuestSlice()
    quests.restore(
        {
            "milestone_states": {"ms_1": {"state": "ACTIVE"}},
            "dynamic_quests": {"dq_1": {"status": "completed"}},
        }
    )
    state.register(quests)

    party = PartySlice()
    party.restore({"members": {"companion_1": {"id": "companion_1"}}})
    state.register(party)

    scene_slice = SceneSlice()
    scene_slice.restore({})
    state.register(scene_slice)
    scene_bus = SceneBus(scene_slice)

    rules_engine = RulesEngine()
    rules_engine.register(WorldStateHandler())

    change_log: list[StateChange] = []

    def _apply_delta(delta: StateDelta | None) -> None:
        if delta is None:
            return
        state.apply(delta)
        change_log.extend(delta.changes)
        for change in delta.changes:
            scene_bus.record_state_change(change)

    return SettlementContext(
        change_log=change_log,
        state=state,
        world=world,
        scene_bus=scene_bus,
        _rules_engine=rules_engine,
        _apply_delta=_apply_delta,
    )


class TestEventConditionHook:
    def test_initial_quest_events_use_stable_state_fields(self) -> None:
        world = WorldInstance("test_world")
        quests = QuestRegistry()
        quests.load(
            {
                "initial_events": [
                    {
                        "id": "evt_intro",
                        "event_type": "quest",
                        "preconditions": {"type": "flag_set", "key": "quest_started"},
                    }
                ]
            }
        )
        world.register(quests)

        state = StateContainer.create_new(world)
        event = state.events.get_event("evt_intro")

        assert event is not None
        assert event["id"] == "evt_intro"
        assert event["event_id"] == "evt_intro"
        assert event["state"] == "locked"
        assert event["status"] == "locked"
        assert event["source"] == "quest"

    def test_should_not_skip(self) -> None:
        assert EventConditionHook().should_skip([]) is False

    def test_missing_events_slice_returns_noop(self) -> None:
        context = _make_context()
        del context.state._slices["events"]

        result = asyncio.run(EventConditionHook().execute(context))

        assert result.metadata["status"] == "noop"
        assert result.metadata["checked_event_count"] == 0

    def test_locked_event_transitions_to_available_when_flag_matches(self) -> None:
        context = _make_context()
        context.state.events.restore(
            {
                "active_events": {
                    "evt_flag": {
                        "status": "locked",
                        "title": "启程信号",
                        "conditions": {"type": "flag_set", "key": "quest_started"},
                    }
                }
            }
        )

        result = asyncio.run(EventConditionHook().execute(context))
        event = context.state.events.get_event("evt_flag")

        assert event is not None
        assert event["state"] == "available"
        assert event["status"] == "available"
        assert result.metadata["status"] == "applied"
        assert result.metadata["transitioned_count"] == 1
        assert result.sse_events[0].event_type == "event_state_changed"
        assert result.sse_events[0].payload["to_state"] == "available"
        assert result.sse_events[0].payload["title"] == "启程信号"

    def test_triggered_event_without_conditions_transitions_to_active(self) -> None:
        context = _make_context()
        context.state.events.restore(
            {
                "active_events": {
                    "evt_due": {
                        "state": "triggered",
                        "event_type": "generic",
                    }
                }
            }
        )

        result = asyncio.run(EventConditionHook().execute(context))
        event = context.state.events.get_event("evt_due")

        assert event is not None
        assert event["state"] == "active"
        assert event["status"] == "active"
        assert result.metadata["transitioned_count"] == 1

    def test_unsupported_conditions_are_counted_without_crashing(self) -> None:
        context = _make_context()
        context.state.events.restore(
            {
                "active_events": {
                    "evt_bad": {
                        "state": "locked",
                        "conditions": [{"type": "unknown_future_condition", "count": 3}],
                    }
                }
            }
        )

        result = asyncio.run(EventConditionHook().execute(context))

        assert result.metadata["status"] == "noop"
        assert result.metadata["unsupported_condition_count"] == 1
        assert context.state.events.get_event("evt_bad")["state"] == "locked"

    def test_supported_condition_types_are_checked(self) -> None:
        context = _make_context()
        context.state.events.restore(
            {
                "active_events": {
                    "evt_area": {
                        "state": "locked",
                        "conditions": {"type": "location_entered", "area_id": "forest"},
                    },
                    "evt_period": {
                        "state": "locked",
                        "conditions": {"type": "period_reached", "period": "day"},
                    },
                    "evt_time": {
                        "state": "locked",
                        "conditions": {"type": "time_reached", "day": 2, "slot": 9},
                    },
                    "evt_ms": {
                        "state": "locked",
                        "conditions": {
                            "type": "quest_state",
                            "quest_id": "ms_1",
                            "state": "active",
                            "kind": "milestone",
                        },
                    },
                    "evt_dq": {
                        "state": "locked",
                        "conditions": {
                            "type": "quest_state",
                            "quest_id": "dq_1",
                            "state": "completed",
                        },
                    },
                }
            }
        )

        result = asyncio.run(EventConditionHook().execute(context))

        assert result.metadata["transitioned_count"] == 5
        for event_id in ("evt_area", "evt_period", "evt_time", "evt_ms", "evt_dq"):
            event = context.state.events.get_event(event_id)
            assert event is not None
            assert event["state"] == "available"

    def test_commands_are_forced_to_system_and_invalid_types_are_skipped(self) -> None:
        context = _make_context()
        context.state.events.restore({"active_events": {"evt_hook": {"state": "active"}}})
        evaluator = StaticEvaluator(
            EventConditionDecision(
                commands=[
                    Command(
                        type="modify_location",
                        params={"area_id": "forest"},
                        source="ai_osiris",
                    ),
                    {"type": "unknown", "params": {}},
                ]
            )
        )

        result = asyncio.run(EventConditionHook(evaluator=evaluator).execute(context))

        assert context.state.player.current_area == "forest"
        assert result.metadata["status"] == "applied"
        assert result.metadata["executed_count"] == 1
        assert result.metadata["skipped_invalid_command_count"] == 1
        assert result.metadata["command_results"][0]["executed"] is True

    def test_failed_command_does_not_stop_later_commands(self) -> None:
        context = _make_context()
        context.state.events.restore({"active_events": {"evt_hook": {"state": "active"}}})
        evaluator = StaticEvaluator(
            {
                "commands": [
                    {
                        "type": "modify_approval",
                        "params": {"character_id": "outsider", "delta": 1},
                    },
                    {
                        "type": "set_flag",
                        "params": {"key": "after_failure", "value": True},
                    },
                ]
            }
        )

        result = asyncio.run(EventConditionHook(evaluator=evaluator).execute(context))

        assert result.metadata["status"] == "partial_failure"
        assert result.metadata["executed_count"] == 2
        assert result.metadata["failed_count"] == 1
        assert result.metadata["command_results"][0]["executed"] is False
        assert result.metadata["command_results"][1]["executed"] is True
        assert context.state.flags.get("after_failure") is True

    def test_invalid_transition_is_skipped(self) -> None:
        context = _make_context()
        context.state.events.restore({"active_events": {"evt_hook": {"state": "active"}}})
        evaluator = StaticEvaluator(
            EventConditionDecision(
                transitions=[
                    EventTransition(
                        event_id="missing",
                        from_state="active",
                        to_state="resolved",
                        reason="bad",
                    ),
                    EventTransition(
                        event_id="evt_hook",
                        from_state="active",
                        to_state="not_a_state",
                        reason="bad",
                    ),
                ]
            )
        )

        result = asyncio.run(EventConditionHook(evaluator=evaluator).execute(context))

        assert result.metadata["status"] == "noop"
        assert result.metadata["skipped_invalid_transition_count"] == 2
        assert context.state.events.get_event("evt_hook")["state"] == "active"

    def test_evaluator_exception_returns_error_sse(self) -> None:
        context = _make_context()
        context.state.events.restore({"active_events": {"evt_hook": {"state": "active"}}})

        result = asyncio.run(EventConditionHook(evaluator=ExplodingEvaluator()).execute(context))

        assert result.metadata["status"] == "evaluator_error"
        assert result.sse_events[0].event_type == "event_condition_error"
        assert context.scene_bus.snapshot()["entries"] == []
