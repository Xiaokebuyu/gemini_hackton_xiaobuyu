"""PipelineOrchestrator and PipelineHook skeleton."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.game_core.orchestration.action_dispatcher import ActionDispatcher
from app.game_core.orchestration.context_assembler import ContextAssembler
from app.game_core.orchestration.models import PipelineResult, StructuredAction
from app.game_core.orchestration.shared_context import SharedContext
from app.game_core.rules.models import Command, ExecuteResult


@dataclass(slots=True)
class PipelineContext:
    """Internal mutable state shared across one pipeline pass."""

    shared: SharedContext
    input_payload: Any
    assembled_context: dict[str, Any] = field(default_factory=dict)
    command: Command | None = None
    execute_result: ExecuteResult | None = None


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

    def register_hook(self, hook: PipelineHook) -> None:
        self._hooks.append(hook)
        self._hooks.sort(key=lambda item: (item.extension_point, item.priority))

    async def process(
        self,
        input_payload: Any,
        shared: SharedContext,
    ) -> PipelineResult:
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

        for hook in self._hooks_for("after_agents"):
            await hook.execute(ctx)

        if ctx.execute_result is None:
            return PipelineResult(
                success=True,
                metadata={"status": "stub"},
            )

        return PipelineResult(
            success=ctx.execute_result.success,
            commands=[ctx.command] if ctx.command is not None else [],
            delta=ctx.execute_result.delta,
            time_cost=ctx.execute_result.time_cost,
            action_type=ctx.command.type if ctx.command is not None else "noop",
            errors=list(ctx.execute_result.errors),
            narrative_hints=list(ctx.execute_result.narrative_hints),
            rolls=list(ctx.execute_result.rolls),
            metadata={},
        )

    def _hooks_for(self, extension_point: str) -> list[PipelineHook]:
        return [
            hook
            for hook in self._hooks
            if hook.extension_point == extension_point
        ]
