"""TickCoordinator skeleton."""

from __future__ import annotations

from typing import Any

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
        self.settlement_hooks: list[SettlementHook] = []

    def register_settlement_hook(self, hook: SettlementHook) -> None:
        self.settlement_hooks.append(hook)
        self.settlement_hooks.sort(key=lambda item: item.priority)

    async def process(self, input_payload: Any) -> PipelineResult:
        shared = SharedContext(
            world=self.world,
            state=self.state,
            rules_engine=self.rules_engine,
            scene_bus=self.scene_bus,
        )
        result = await self.pipeline.process(input_payload, shared)
        if result.success and result.delta is not None:
            self._apply_delta(result.delta)
        self.accumulate(result.time_cost)
        while self.check_settlement():
            before_accumulated = self.state.time.accumulated
            settlement_events = await self._tick_settlement()
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

    async def _tick_settlement(self) -> list[SSEEvent]:
        """Run settlement hooks. Returns collected SSE events."""
        collected_events: list[SSEEvent] = []
        context = SettlementContext(
            change_log=self.change_log,
            state=self.state,
            world=self.world,
            scene_bus=self.scene_bus,
            _rules_engine=self.rules_engine,
            _apply_delta=self._apply_delta,
        )
        for hook in self.settlement_hooks:
            if hook.should_skip(self.change_log):
                continue
            result = await hook.execute(context)
            for command in result.commands:
                context.execute_command(command)
            collected_events.extend(result.sse_events)
        return collected_events

    def persist(self) -> dict[str, dict[str, Any]]:
        return self.state.persist()

    def _apply_delta(self, delta: StateDelta | None) -> None:
        if delta is None:
            return
        self.state.apply(delta)
        self.change_log.extend(delta.changes)
        for change in delta.changes:
            self.scene_bus.record_state_change(change)
