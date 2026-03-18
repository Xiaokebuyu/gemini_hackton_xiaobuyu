"""Tests for event condition checks in the tick pipeline lifecycle."""

from __future__ import annotations

import asyncio

from app.game_core.content import WorldInstance
from app.game_core.orchestration.event_engine import run_inline_event_check
from app.game_core.orchestration.models import SSEEvent
from app.game_core.orchestration.pipeline import PipelineOrchestrator
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.tick_coordinator import TickCoordinator
from app.game_core.rules import RulesEngine
from app.game_core.rules.handlers import WorldStateHandler
from app.game_core.rules.models import Command
from app.game_core.state import StateChange, StateContainer, StateDelta
from app.game_core.state.slices import EventSlice, FlagSlice, SceneSlice


def _build_rules_engine() -> RulesEngine:
    engine = RulesEngine()
    engine.register(WorldStateHandler())
    return engine


def _prepare_state(
    event_id: str,
    *,
    on_trigger: list[dict] | None = None,
) -> StateContainer:
    state = StateContainer()
    scene = SceneSlice()
    scene.restore({})
    state.register(EventSlice())
    state.register(FlagSlice())
    state.register(scene)
    state.events.restore(
        {
            "active_events": {
                event_id: {
                    "id": event_id,
                    "event_id": event_id,
                    "state": "locked",
                    "conditions": {"type": "flag_set", "key": "trigger_flag"},
                    **({"on_trigger": on_trigger} if on_trigger is not None else {}),
                },
            },
        },
    )
    state.flags.restore({"flags": {"trigger_flag": False}})
    return state


def _build_coordinator(state: StateContainer) -> tuple[TickCoordinator, SceneBus]:
    """Build a TickCoordinator with a real PipelineOrchestrator."""
    scene_bus = SceneBus(state.scene)
    pipeline = PipelineOrchestrator()
    pipeline.action_dispatcher.register("set_flag", "set_flag")
    coordinator = TickCoordinator(
        world=WorldInstance("test_world"),
        state=state,
        rules_engine=_build_rules_engine(),
        scene_bus=scene_bus,
        pipeline=pipeline,
    )
    return coordinator, scene_bus


# ---------------------------------------------------------------------------
# run_inline_event_check standalone tests
# ---------------------------------------------------------------------------

def test_run_inline_event_check_resolves_event_and_executes_on_trigger() -> None:
    state = _prepare_state(
        "evt_trigger",
        on_trigger=[{"type": "set_flag", "params": {"key": "reward_ready", "value": True}}],
    )
    state.flags.restore({"flags": {"got_key": True}})
    state.events.restore(
        {
            "active_events": {
                "evt_trigger": {
                    "id": "evt_trigger",
                    "event_id": "evt_trigger",
                    "state": "locked",
                    "conditions": {"type": "flag_set", "key": "got_key", "value": True},
                    "on_trigger": [{"type": "set_flag", "params": {"key": "reward_ready", "value": True}}],
                },
            },
        },
    )

    change_log: list[StateChange] = []
    scene_bus = SceneBus(state.scene)
    payloads = run_inline_event_check(
        state=state,
        world=WorldInstance("test_world"),
        rules_engine=_build_rules_engine(),
        apply_delta=state.apply,
        change_log=change_log,
        scene_bus=scene_bus,
        label="standalone",
    )

    assert len(payloads) == 1
    assert payloads[0]["event_id"] == "evt_trigger"
    assert payloads[0]["from_state"] == "locked"
    assert payloads[0]["to_state"] == "resolved"
    assert payloads[0]["source"] == "standalone"
    assert state.flags.get("reward_ready") is True
    assert state.events.get_event("evt_trigger")["state"] == "resolved"
    assert len(change_log) == 1
    assert change_log[0].slice == "events"


def test_no_events_slice_safe() -> None:
    """run_inline_event_check returns empty when state has no events slice."""
    state = StateContainer()
    scene = SceneSlice()
    scene.restore({})
    state.register(scene)
    state.register(FlagSlice())

    result = run_inline_event_check(
        state=state,
        world=WorldInstance("test_world"),
        rules_engine=_build_rules_engine(),
        apply_delta=state.apply,
        change_log=[],
        scene_bus=SceneBus(scene),
        label="test",
    )
    assert result == []


def test_run_inline_writes_to_scene_bus() -> None:
    """run_inline_event_check records state changes to scene_bus."""
    state = _prepare_state("evt_bus")
    state.flags.restore({"flags": {"trigger_flag": True}})
    scene_bus = SceneBus(state.scene)

    run_inline_event_check(
        state=state,
        world=WorldInstance("test_world"),
        rules_engine=_build_rules_engine(),
        apply_delta=state.apply,
        change_log=[],
        scene_bus=scene_bus,
        label="bus_test",
    )

    snapshot = scene_bus.snapshot()
    state_changes = snapshot.get("state_changes", [])
    assert any(
        sc.get("slice") == "events" and "evt_bus" in sc.get("path", "")
        for sc in state_changes
    )


# ---------------------------------------------------------------------------
# Pipeline-level event checks (real PipelineOrchestrator)
# ---------------------------------------------------------------------------

def test_pipeline_event_check_fires_after_engine_delta() -> None:
    """Pipeline's internal event stage detects transitions after delta is applied."""
    state = _prepare_state("evt_a6")
    coordinator, _ = _build_coordinator(state)

    result = asyncio.run(coordinator.process(
        {"action_type": "set_flag", "params": {"key": "trigger_flag", "value": True}},
    ))

    assert result.executed is True
    assert any(e.event_type == "event_state_changed" for e in result.sse_events)
    assert state.events.get_event("evt_a6")["state"] == "available"


def test_pipeline_event_check_fires_after_agent_round() -> None:
    """Pipeline's post-agent event stage detects transitions from agent state changes."""
    state = _prepare_state("evt_c1")
    coordinator, _ = _build_coordinator(state)

    async def _agent_round(shared, process_result, apply_delta, event_sink=None):
        del shared, process_result
        apply_delta(
            StateDelta(changes=[
                StateChange(slice="flags", operation="set", path="flags.trigger_flag", value=True),
            ]),
        )
        return []

    coordinator.pipeline.set_stage_b_runner(_agent_round)

    # Engine sets an unrelated flag; agent sets the trigger_flag
    result = asyncio.run(coordinator.process(
        Command(type="set_flag", params={"key": "unrelated", "value": True}, source="player"),
    ))

    assert any(e.event_type == "event_state_changed" for e in result.sse_events)
    assert state.events.get_event("evt_c1")["state"] == "available"


def test_pipeline_after_engine_callback_runs_before_stage_b_and_defers_a6_visibility() -> None:
    state = _prepare_state("evt_boundary")
    coordinator, _ = _build_coordinator(state)
    order: list[str] = []
    stage_b_started = asyncio.Event()
    allow_stage_b_finish = asyncio.Event()

    async def _agent_round(shared, process_result, apply_delta, event_sink=None):
        del shared, process_result, apply_delta
        stage_b_started.set()
        order.append("stage_b_started")
        await allow_stage_b_finish.wait()
        if event_sink is not None:
            await event_sink(SSEEvent("gm_narration", {"content": "The room reacts."}))
        return [SSEEvent("gm_narration", {"content": "The room reacts."})]

    async def _after_engine(_result):
        order.append("action_result")
        assert not stage_b_started.is_set()
        allow_stage_b_finish.set()

    async def _event_sink(event: SSEEvent):
        order.append(event.event_type)

    coordinator.pipeline.set_stage_b_runner(_agent_round)

    result = asyncio.run(coordinator.process(
        {"action_type": "set_flag", "params": {"key": "trigger_flag", "value": True}},
        event_sink=_event_sink,
        after_engine=_after_engine,
    ))

    assert result.executed is True
    assert state.events.get_event("evt_boundary")["state"] == "available"
    assert order.index("action_result") < order.index("stage_b_started")
    assert order.index("gm_narration") < order.index("event_state_changed")


def test_pipeline_event_check_runs_without_agent_hooks() -> None:
    """Pipeline's event stage fires even when no agent hooks are registered."""
    state = _prepare_state("evt_no_agent")
    coordinator, _ = _build_coordinator(state)

    result = asyncio.run(coordinator.process(
        {"action_type": "set_flag", "params": {"key": "trigger_flag", "value": True}},
    ))

    assert any(e.event_type == "event_state_changed" for e in result.sse_events)
    assert state.events.get_event("evt_no_agent")["state"] == "available"


def test_no_event_when_action_failed() -> None:
    """Failed action should not trigger event transitions."""
    state = _prepare_state("evt_failed")
    coordinator, _ = _build_coordinator(state)

    # Empty params → validation failure → executed=False → no event stage
    result = asyncio.run(coordinator.process(
        Command(type="set_flag", params={}, source="player"),
    ))

    assert not any(e.event_type == "event_state_changed" for e in result.sse_events)
    assert state.events.get_event("evt_failed")["state"] == "locked"


def test_pipeline_no_double_trigger() -> None:
    """Pipeline's two event stages do not double-trigger the same event.

    The pre-agent stage transitions the event. The post-agent stage
    should find nothing new (idempotent). Exactly one SSE is emitted.
    """
    state = _prepare_state("evt_once")
    coordinator, _ = _build_coordinator(state)

    result = asyncio.run(coordinator.process(
        {"action_type": "set_flag", "params": {"key": "trigger_flag", "value": True}},
    ))

    event_changed = [
        e for e in result.sse_events
        if e.event_type == "event_state_changed"
        and e.payload.get("event_id") == "evt_once"
    ]
    assert len(event_changed) == 1
    assert state.events.get_event("evt_once")["state"] == "available"


# ---------------------------------------------------------------------------
# finalize_external_turn (interact pipeline)
# ---------------------------------------------------------------------------

def test_finalize_external_turn_checks_post_external() -> None:
    state = _prepare_state("evt_external")
    coordinator, _ = _build_coordinator(state)
    command = Command(type="set_flag", params={"key": "trigger_flag", "value": True}, source="npc")
    execute_result = coordinator.rules_engine.execute(command, state, coordinator.world)
    coordinator.apply_external_result(execute_result)
    events = asyncio.run(coordinator.finalize_external_turn(time_cost=1 / 6))
    assert any(
        e.event_type == "event_state_changed" and e.payload.get("source") == "post_external"
        for e in events
    )
    assert state.events.get_event("evt_external")["state"] == "available"
