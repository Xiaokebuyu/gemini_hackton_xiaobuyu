"""TickCoordinator skeleton."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

MAX_ACTION_LOG = 100  # 每个 session 保留的最近动作条数上限（防止无界增长污染 LLM 上下文）

from app.game_core.content import WorldInstance
from app.game_core.orchestration.models import PipelineResult, SSEEvent
from app.game_core.orchestration.pipeline import PipelineOrchestrator
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.orchestration.shared_context import SharedContext
from app.game_core.orchestration.hooks.base import SettlementHook
from app.game_core.rules import RulesEngine
from app.game_core.state import StateChange, StateContainer, StateDelta


class TickCoordinator:
    """Own the session tick lifecycle."""

    def __init__(
        self,
        world: WorldInstance,
        state: StateContainer,
        rules_engine: RulesEngine,
        scene_bus: SceneBus,
        pipeline: PipelineOrchestrator | None = None,
    ) -> None:
        self.world = world
        self.state = state
        self.rules_engine = rules_engine
        self.scene_bus = scene_bus
        self.pipeline = pipeline or PipelineOrchestrator()
        self.change_log: list[StateChange] = []
        self.action_log: list[dict[str, Any]] = []
        self.settlement_hooks: list[SettlementHook] = []

    def register_settlement_hook(self, hook: SettlementHook) -> None:
        self.settlement_hooks.append(hook)
        self.settlement_hooks.sort(key=lambda item: item.priority)

    async def process(
        self,
        input_payload: Any,
        event_sink: Callable[[SSEEvent], Awaitable[None]] | None = None,
    ) -> PipelineResult:
        shared = SharedContext(
            world=self.world,
            state=self.state,
            rules_engine=self.rules_engine,
            scene_bus=self.scene_bus,
        )
        result = await self.pipeline.process(input_payload, shared)
        if event_sink is not None:
            for event in result.sse_events:
                await event_sink(event)
        if result.success and result.delta is not None:
            self._apply_delta(result.delta)
        self._record_action(result)
        self.accumulate(result.time_cost)
        while self.check_settlement():
            before_accumulated = self.state.time.accumulated
            settlement_events = await self._tick_settlement(event_sink)
            result.sse_events.extend(settlement_events)
            if (
                self.state.has_slice("time")
                and self.state.time.accumulated >= 1.0
                and self.state.time.accumulated >= before_accumulated
            ):
                raise RuntimeError("settlement made no progress")
        return result

    def accumulate(self, time_cost: float) -> None:
        if time_cost <= 0:
            return
        if not self.state.has_slice("time"):
            return
        self.state.time.add_action(time_cost)

    def check_settlement(self) -> bool:
        if not self.state.has_slice("time"):
            return False
        return self.state.time.accumulated >= 1.0

    async def _tick_settlement(
        self,
        event_sink: Callable[[SSEEvent], Awaitable[None]] | None = None,
    ) -> list[SSEEvent]:
        """Run settlement hooks. Returns collected SSE events."""
        collected_events: list[SSEEvent] = []
        context = SettlementContext(
            change_log=self.change_log,
            state=self.state,
            world=self.world,
            scene_bus=self.scene_bus,
            _rules_engine=self.rules_engine,
            _apply_delta=self._apply_delta,
            action_log=list(self.action_log),
        )
        for hook in self.settlement_hooks:
            if hook.should_skip(self.change_log):
                continue
            hook_name = getattr(hook, "HOOK_NAME", type(hook).__name__)
            try:
                result = await hook.execute(context)
            except Exception as exc:
                logger.exception("settlement hook failed: %s", hook_name)
                error_event = SSEEvent(
                    event_type="hook_error",
                    payload={
                        "hook": hook_name,
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    },
                )
                collected_events.append(error_event)
                if event_sink is not None:
                    await event_sink(error_event)
                continue
            for command in result.commands:
                try:
                    context.execute_command(command)
                except Exception as exc:
                    logger.exception("hook command failed: %s", hook_name)
                    cmd_error = SSEEvent(
                        event_type="command_error",
                        payload={
                            "hook": hook_name,
                            "error_type": type(exc).__name__,
                            "message": str(exc),
                        },
                    )
                    collected_events.append(cmd_error)
                    if event_sink is not None:
                        await event_sink(cmd_error)
            for event in result.sse_events:
                collected_events.append(event)
                if event_sink is not None:
                    await event_sink(event)
        return collected_events

    def export_dirty(self) -> dict[str, dict[str, Any]]:
        """Return serialized dirty-slice data for the outer persistence boundary.

        This does NOT write to storage.  The caller (SaveStore) is
        responsible for actual persistence — see D-O22 in orchestration.md.
        """
        return self.state.export_dirty()

    def _record_action(self, result: PipelineResult) -> None:
        if result.action_type == "noop":
            return
        command = result.commands[0] if result.commands else None
        record: dict[str, Any] = {
            "type": result.action_type,
            "actor": command.source if command else "system",
            "params": dict(command.params) if command else {},
            "success": result.success,
            "time_cost": result.time_cost,
        }
        if result.narrative_hints:
            record["narrative_hints"] = list(result.narrative_hints)
        self.action_log.append(record)
        if len(self.action_log) > MAX_ACTION_LOG:
            self.action_log = self.action_log[-MAX_ACTION_LOG:]

    def _apply_delta(self, delta: StateDelta | None) -> None:
        if delta is None:
            return
        self.state.apply(delta)
        self.change_log.extend(delta.changes)
        for change in delta.changes:
            self.scene_bus.record_state_change(change)
