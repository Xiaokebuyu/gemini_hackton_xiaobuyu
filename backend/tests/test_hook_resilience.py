"""Tests for TickCoordinator settlement hook resilience."""

from __future__ import annotations

import asyncio
import pytest

from app.game_core.content import WorldInstance
from app.game_core.orchestration.hooks.base import NoOpSettlementHook
from app.game_core.orchestration.models import HookResult, PipelineResult, SSEEvent
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.orchestration.tick_coordinator import TickCoordinator
from app.game_core.rules import RulesEngine
from app.game_core.rules.base import CommandHandler
from app.game_core.rules.models import Command, ExecuteResult, ValidationResult
from app.game_core.state.base import StateContainer
from app.game_core.state.slices.player import PlayerSlice
from app.game_core.state.slices.scene import SceneSlice
from app.game_core.state.slices.time import TimeSlice


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


class _ExplodingHook(NoOpSettlementHook):
    HOOK_PRIORITY = 10
    HOOK_NAME = "exploding"

    async def execute(self, context: SettlementContext) -> HookResult:
        raise RuntimeError("boom")


class _TrackingHook(NoOpSettlementHook):
    HOOK_PRIORITY = 20
    HOOK_NAME = "tracking"

    def __init__(self) -> None:
        super().__init__()
        self.executed = False

    async def execute(self, context: SettlementContext) -> HookResult:
        self.executed = True
        return HookResult(
            sse_events=[SSEEvent(event_type="tracking_ran", payload={})],
            metadata={"status": "ok"},
        )


class _ExplodingCommandHook(NoOpSettlementHook):
    """Hook that returns a command which does NOT throw (unknown type → error result)."""

    HOOK_PRIORITY = 15
    HOOK_NAME = "exploding_command"

    async def execute(self, context: SettlementContext) -> HookResult:
        return HookResult(
            commands=[Command(type="nonexistent_command", source="test")],
            sse_events=[SSEEvent(event_type="cmd_hook_ran", payload={})],
            metadata={"status": "ok"},
        )


class _CrashingHandler(CommandHandler):
    """Handler that throws during compute."""

    @property
    def command_types(self) -> list[str]:
        return ["crash_cmd"]

    def validate(self, cmd, state, world) -> ValidationResult:
        return ValidationResult(ok=True)

    def compute(self, cmd, state, world) -> ExecuteResult:
        raise RuntimeError("handler crash")


class _CommandCrashHook(NoOpSettlementHook):
    """Hook that returns a command whose handler throws during execution."""

    HOOK_PRIORITY = 15
    HOOK_NAME = "command_crash"

    async def execute(self, context: SettlementContext) -> HookResult:
        return HookResult(
            commands=[Command(type="crash_cmd", source="test")],
            sse_events=[SSEEvent(event_type="cmd_crash_ran", payload={})],
            metadata={"status": "ok"},
        )


def _make_coordinator(extra_handlers: list | None = None) -> TickCoordinator:
    state = StateContainer()
    player = PlayerSlice()
    player.restore({"hp": 20, "max_hp": 20})
    state.register(player)
    time_slice = TimeSlice()
    state.register(time_slice)
    scene_slice = SceneSlice()
    state.register(scene_slice)
    world = WorldInstance("test")
    engine = RulesEngine()
    for handler in (extra_handlers or []):
        engine.register(handler)
    scene_bus = SceneBus(scene_slice)
    return TickCoordinator(world, state, engine, scene_bus)


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


def test_hook_exception_does_not_block_subsequent_hooks() -> None:
    """An exploding hook should not prevent later hooks from running."""
    coordinator = _make_coordinator()
    tracking = _TrackingHook()
    coordinator.register_settlement_hook(_ExplodingHook())
    coordinator.register_settlement_hook(tracking)

    events = asyncio.run(coordinator._tick_settlement())

    assert tracking.executed is True
    event_types = [e.event_type for e in events]
    assert "tracking_ran" in event_types


def test_hook_exception_produces_hook_error_sse() -> None:
    """An exploding hook should emit a hook_error SSE event."""
    coordinator = _make_coordinator()
    coordinator.register_settlement_hook(_ExplodingHook())

    events = asyncio.run(coordinator._tick_settlement())

    error_events = [e for e in events if e.event_type == "hook_error"]
    assert len(error_events) == 1
    assert error_events[0].payload["hook"] == "exploding"
    assert error_events[0].payload["error_type"] == "RuntimeError"
    assert error_events[0].payload["message"] == "boom"


def test_hook_command_exception_emits_command_error_sse() -> None:
    """A command that throws during execution should emit a command_error SSE event."""
    coordinator = _make_coordinator(extra_handlers=[_CrashingHandler()])
    coordinator.register_settlement_hook(_CommandCrashHook())

    events = asyncio.run(coordinator._tick_settlement())

    cmd_errors = [e for e in events if e.event_type == "command_error"]
    assert len(cmd_errors) == 1
    assert cmd_errors[0].payload["hook"] == "command_crash"
    assert "error_type" in cmd_errors[0].payload
    assert "message" in cmd_errors[0].payload
    # The hook's own SSE events should still be collected
    assert any(e.event_type == "cmd_crash_ran" for e in events)


def test_hook_command_exception_does_not_block_subsequent_hooks() -> None:
    """A failing command from a hook should not block later hooks."""
    coordinator = _make_coordinator()
    tracking = _TrackingHook()
    coordinator.register_settlement_hook(_ExplodingCommandHook())
    coordinator.register_settlement_hook(tracking)

    events = asyncio.run(coordinator._tick_settlement())

    assert tracking.executed is True
    event_types = [e.event_type for e in events]
    # The command hook's own SSE events should still be collected
    assert "cmd_hook_ran" in event_types
    assert "tracking_ran" in event_types


def test_normal_hooks_unaffected_by_resilience_wrapper() -> None:
    """Normal hooks produce the same results with the resilience wrapper."""
    coordinator = _make_coordinator()
    tracking = _TrackingHook()
    coordinator.register_settlement_hook(tracking)

    events = asyncio.run(coordinator._tick_settlement())

    assert tracking.executed is True
    event_types = [e.event_type for e in events]
    assert "tracking_ran" in event_types
    assert "hook_error" not in event_types


# ------------------------------------------------------------------
# Event sink tests
# ------------------------------------------------------------------


def test_event_sink_receives_hook_events() -> None:
    """When event_sink is provided, hook SSE events are pushed to it."""
    coordinator = _make_coordinator()
    coordinator.register_settlement_hook(_TrackingHook())
    sink_events: list[SSEEvent] = []

    async def _sink(event: SSEEvent) -> None:
        sink_events.append(event)

    events = asyncio.run(coordinator._tick_settlement(event_sink=_sink))

    assert len(sink_events) == 1
    assert sink_events[0].event_type == "tracking_ran"
    # Events should also be in the returned list (backward compat)
    assert len(events) == 1
    assert events[0].event_type == "tracking_ran"


def test_event_sink_receives_hook_error_events() -> None:
    """When a hook explodes, the error event is pushed to event_sink."""
    coordinator = _make_coordinator()
    coordinator.register_settlement_hook(_ExplodingHook())
    coordinator.register_settlement_hook(_TrackingHook())
    sink_events: list[SSEEvent] = []

    async def _sink(event: SSEEvent) -> None:
        sink_events.append(event)

    asyncio.run(coordinator._tick_settlement(event_sink=_sink))

    sink_types = [e.event_type for e in sink_events]
    assert "hook_error" in sink_types
    assert "tracking_ran" in sink_types


def test_event_sink_none_preserves_original_behavior() -> None:
    """When event_sink is None, behavior is identical to no-sink path."""
    coordinator = _make_coordinator()
    coordinator.register_settlement_hook(_TrackingHook())

    events = asyncio.run(coordinator._tick_settlement(event_sink=None))

    assert len(events) == 1
    assert events[0].event_type == "tracking_ran"


# ------------------------------------------------------------------
# action_log 结算窗口测试
# ------------------------------------------------------------------


def test_action_log_consumes_one_tick_and_preserves_spillover() -> None:
    coordinator = _make_coordinator()
    coordinator._append_pipeline_action(
        PipelineResult(success=True, action_type="navigate", time_cost=0.75)
    )
    coordinator._append_pipeline_action(
        PipelineResult(success=True, action_type="rest_long", time_cost=0.75)
    )

    first_window = coordinator.action_log
    assert [(entry["type"], entry["time_cost"]) for entry in first_window] == [
        ("navigate", 0.75),
        ("rest_long", 0.25),
    ]

    coordinator._consume_pending_action_window()

    second_window = coordinator.action_log
    assert [(entry["type"], entry["time_cost"]) for entry in second_window] == [
        ("rest_long", 0.5),
    ]


def test_consumed_action_log_does_not_leak_into_next_window() -> None:
    coordinator = _make_coordinator()
    coordinator._append_pipeline_action(
        PipelineResult(success=True, action_type="navigate", time_cost=1.0)
    )
    coordinator._consume_pending_action_window()

    coordinator._append_pipeline_action(
        PipelineResult(success=True, action_type="skill_check", time_cost=1.0 / 6.0)
    )

    assert coordinator.action_log == [
        {
            "type": "skill_check",
            "actor": "system",
            "params": {},
            "success": True,
            "time_cost": 1.0 / 6.0,
        }
    ]


def test_multi_tick_action_reuses_same_high_level_semantics_until_fully_consumed() -> None:
    coordinator = _make_coordinator()
    coordinator._append_pipeline_action(
        PipelineResult(success=True, action_type="rest_long", time_cost=8.0 / 6.0)
    )

    first_window = coordinator.action_log
    assert first_window == [
        {
            "type": "rest_long",
            "actor": "system",
            "params": {},
            "success": True,
            "time_cost": 1.0,
        }
    ]

    coordinator._consume_pending_action_window()
    second_window = coordinator.action_log
    assert len(second_window) == 1
    assert second_window[0]["type"] == "rest_long"
    assert second_window[0]["actor"] == "system"
    assert second_window[0]["params"] == {}
    assert second_window[0]["success"] is True
    assert second_window[0]["time_cost"] == pytest.approx(1.0 / 3.0)
