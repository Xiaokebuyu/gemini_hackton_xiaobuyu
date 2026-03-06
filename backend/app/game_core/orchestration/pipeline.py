"""PipelineOrchestrator and PipelineHook skeleton."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import logging
from typing import Any, Awaitable, Callable

from app.game_core.orchestration.action_dispatcher import ActionDispatcher
from app.game_core.orchestration.event_engine import run_inline_event_check
from app.game_core.orchestration.models import PipelineResult, SSEEvent, StructuredAction
from app.game_core.orchestration.context_assembler import ContextAssembler
from app.game_core.orchestration.shared_context import SharedContext
from app.game_core.rules.models import Command, ExecuteResult
from app.game_core.state import StateChange, StateDelta

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PipelineContext:
    """Internal mutable state shared across one pipeline pass."""

    shared: SharedContext
    input_payload: Any
    assembled_context: dict[str, Any] = field(default_factory=dict)
    command: Command | None = None
    execute_result: ExecuteResult | None = None


AgentRoundRunner = Callable[
    [
        SharedContext,
        PipelineResult,
        Callable[[StateDelta | None], None],
        Callable[[SSEEvent], Awaitable[None]] | None,
    ],
    Awaitable[list[SSEEvent]],
]
AfterEngineCallback = Callable[[PipelineResult], Awaitable[None]]


class PipelineHook(ABC):
    """Pipeline extension point."""

    @property
    @abstractmethod
    def extension_point(self) -> str:
        """One of 'after_engine' or 'after_agents'."""

    @property
    def priority(self) -> int:
        return 50

    @abstractmethod
    async def execute(self, context: PipelineContext) -> None:
        """Run hook logic."""


class PipelineOrchestrator:
    """Three-stage pipeline skeleton."""

    def __init__(
        self,
        action_dispatcher: ActionDispatcher | None = None,
        context_assembler: ContextAssembler | None = None,
    ) -> None:
        self.action_dispatcher = action_dispatcher or ActionDispatcher()
        self.context_assembler = context_assembler or ContextAssembler()
        self._hooks: list[PipelineHook] = []
        self._stage_b_runner: AgentRoundRunner | None = None

    def register_hook(self, hook: PipelineHook) -> None:
        self._hooks.append(hook)
        self._hooks.sort(key=lambda item: (item.extension_point, item.priority))

    def set_stage_b_runner(self, runner: AgentRoundRunner | None) -> None:
        """Install or remove the Stage B agent runner."""
        self._stage_b_runner = runner

    async def process(
        self,
        input_payload: Any,
        shared: SharedContext,
        *,
        apply_delta: Callable[[StateDelta | None], None] | None = None,
        change_log: list[StateChange] | None = None,
        action_log: list[dict[str, Any]] | None = None,
        event_sink: Callable[[SSEEvent], Awaitable[None]] | None = None,
        after_engine: AfterEngineCallback | None = None,
    ) -> PipelineResult:
        del action_log
        ctx = PipelineContext(shared=shared, input_payload=input_payload)
        ctx.assembled_context = self.context_assembler.assemble(shared)

        if isinstance(input_payload, Command):
            ctx.command = input_payload
        elif isinstance(input_payload, StructuredAction):
            ctx.command = self.action_dispatcher.dispatch(input_payload)
        elif isinstance(input_payload, dict) and "action_type" in input_payload:
            structured = StructuredAction(
                action_type=str(input_payload["action_type"]),
                params=dict(input_payload.get("params", {})),
                source=str(input_payload.get("source", "player")),
                context=(
                    dict(input_payload["context"])
                    if isinstance(input_payload.get("context"), dict)
                    else None
                ),
            )
            ctx.command = self.action_dispatcher.dispatch(structured)

        if ctx.command is not None:
            ctx.execute_result = shared.rules_engine.execute(
                ctx.command,
                shared.state,
                shared.world,
            )

        # Fill L7 with engine result summary
        if ctx.execute_result is not None:
            ctx.assembled_context["l7_engine_result"] = {
                "success": ctx.execute_result.success,
                "narrative_hints": list(ctx.execute_result.narrative_hints),
                "rolls": [
                    {
                        "purpose": roll.purpose,
                        "dice": roll.dice,
                        "result": roll.result,
                        "modifiers": list(roll.modifiers),
                        "total": roll.total,
                        "critical": roll.critical,
                    }
                    for roll in ctx.execute_result.rolls
                ],
                "time_cost": ctx.execute_result.time_cost,
            }

        for hook in self._hooks_for("after_engine"):
            await hook.execute(ctx)

        if ctx.execute_result is None:
            return PipelineResult(
                success=True,
                metadata={"status": "stub"},
            )

        metadata = dict(ctx.execute_result.metadata)
        if ctx.command is not None and isinstance(ctx.command.context, dict) and ctx.command.context:
            metadata["action_context"] = dict(ctx.command.context)

        result = PipelineResult(
            success=ctx.execute_result.success,
            commands=[ctx.command] if ctx.command is not None else [],
            delta=ctx.execute_result.delta,
            time_cost=ctx.execute_result.time_cost,
            action_type=ctx.command.type if ctx.command is not None else "noop",
            errors=list(ctx.execute_result.errors),
            narrative_hints=list(ctx.execute_result.narrative_hints),
            rolls=list(ctx.execute_result.rolls),
            metadata=metadata,
        )

        if result.success and apply_delta is not None and result.delta is not None:
            apply_delta(result.delta)

        deferred_event_payloads: list[dict[str, Any]] = []
        if result.success:
            deferred_event_payloads.extend(await self._run_event_stage(
                shared,
                apply_delta=apply_delta,
                change_log=change_log,
                collected=result.sse_events,
                label="post_engine",
                emit_visible_events=False,
                event_sink=event_sink,
            ))

        if after_engine is not None:
            await after_engine(result)

        deferred_tail_events: list[SSEEvent] = []
        if result.success and self._stage_b_runner is not None and apply_delta is not None:
            immediate_events, deferred_tail_events = await self._run_stage_b(
                shared,
                result,
                apply_delta=apply_delta,
                event_sink=event_sink,
            )
            result.sse_events.extend(immediate_events)

        if result.success:
            deferred_event_payloads.extend(await self._run_event_stage(
                shared,
                apply_delta=apply_delta,
                change_log=change_log,
                collected=result.sse_events,
                label="post_agents",
                emit_visible_events=False,
                event_sink=event_sink,
            ))

        for hook in self._hooks_for("after_agents"):
            await hook.execute(ctx)

        stage_events = [
            SSEEvent(event_type="event_state_changed", payload=payload)
            for payload in deferred_event_payloads
        ]
        if stage_events:
            result.sse_events.extend(stage_events)
            if event_sink is not None:
                for event in stage_events:
                    await event_sink(event)

        result.sse_events.extend(deferred_tail_events)
        if deferred_tail_events and event_sink is not None:
            for event in deferred_tail_events:
                await event_sink(event)
        return result

    def _hooks_for(self, extension_point: str) -> list[PipelineHook]:
        return [
            hook
            for hook in self._hooks
            if hook.extension_point == extension_point
        ]

    async def _run_event_stage(
        self,
        shared: SharedContext,
        *,
        apply_delta: Callable[[StateDelta | None], None] | None,
        change_log: list[StateChange] | None,
        collected: list[SSEEvent],
        label: str,
        emit_visible_events: bool,
        event_sink: Callable[[SSEEvent], Awaitable[None]] | None = None,
    ) -> list[dict[str, Any]]:
        if apply_delta is None or change_log is None:
            return []
        payloads = run_inline_event_check(
            state=shared.state,
            world=shared.world,
            rules_engine=shared.rules_engine,
            apply_delta=apply_delta,
            change_log=change_log,
            scene_bus=shared.scene_bus,
            label=label,
        )
        if emit_visible_events:
            for payload in payloads:
                event = SSEEvent(
                    event_type="event_state_changed",
                    payload=payload,
                )
                collected.append(event)
                if event_sink is not None:
                    await event_sink(event)
        return payloads

    async def _run_stage_b(
        self,
        shared: SharedContext,
        result: PipelineResult,
        *,
        apply_delta: Callable[[StateDelta | None], None],
        event_sink: Callable[[SSEEvent], Awaitable[None]] | None,
    ) -> tuple[list[SSEEvent], list[SSEEvent]]:
        emitted_events: list[SSEEvent] = []

        async def _stage_b_sink(event: SSEEvent) -> None:
            emitted_events.append(event)
            if event.event_type in {"dialogue_options", "dialogue_options_unavailable"}:
                return
            if event_sink is not None:
                await event_sink(event)

        try:
            agent_events = await self._stage_b_runner(
                shared,
                result,
                apply_delta,
                _stage_b_sink,
            )
        except Exception as exc:
            logger.exception("stage b runner failed")
            error_event = SSEEvent(
                event_type="agent_hook_error",
                payload={
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                },
            )
            if event_sink is not None:
                await event_sink(error_event)
            return [error_event], []

        stage_b_events = list(agent_events) if agent_events else list(emitted_events)
        immediate_events, deferred_tail_events = self._split_tail_events(stage_b_events)
        if not emitted_events and event_sink is not None:
            for event in immediate_events:
                await event_sink(event)
        return immediate_events, deferred_tail_events

    @staticmethod
    def _split_tail_events(events: list[SSEEvent]) -> tuple[list[SSEEvent], list[SSEEvent]]:
        immediate: list[SSEEvent] = []
        deferred: list[SSEEvent] = []
        for event in events:
            if event.event_type in {"dialogue_options", "dialogue_options_unavailable"}:
                deferred.append(event)
            else:
                immediate.append(event)
        return immediate, deferred
