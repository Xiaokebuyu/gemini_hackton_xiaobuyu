"""RulesEngine implementation."""

from __future__ import annotations

from app.game_core.content import WorldInstance
from app.game_core.rules.base import CommandHandler
from app.game_core.rules.models import Command, ExecuteResult, ValidationResult
from app.game_core.state import StateContainer


class RulesEngine:
    """Command router for pure handlers."""

    def __init__(self) -> None:
        self._handlers: dict[str, CommandHandler] = {}

    def register(self, handler: CommandHandler) -> None:
        for command_type in handler.command_types:
            self._handlers[command_type] = handler

    def register_if_missing(self, handler: CommandHandler) -> list[str]:
        added: list[str] = []
        for command_type in handler.command_types:
            if command_type in self._handlers:
                continue
            self._handlers[command_type] = handler
            added.append(command_type)
        return added

    def execute(
        self,
        command: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ExecuteResult:
        handler = self._handlers.get(command.type)
        if handler is None:
            return ExecuteResult.error(f"Unknown command type: {command.type}")

        validation = handler.validate(command, state, world)
        if not validation.ok:
            return ExecuteResult.error(validation.reason or "validation failed")

        return handler.compute(command, state, world)

    def validate(
        self,
        command: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        handler = self._handlers.get(command.type)
        if handler is None:
            return ValidationResult(ok=False, reason="Unknown command type")
        return handler.validate(command, state, world)

    def dry_run(
        self,
        command: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ExecuteResult:
        result = self.execute(command, state, world)
        result.is_dry_run = True
        return result

    def batch_execute(
        self,
        commands: list[Command],
        state: StateContainer,
        world: WorldInstance,
    ) -> list[ExecuteResult]:
        results: list[ExecuteResult] = []
        for command in commands:
            result = self.execute(command, state, world)
            if result.success and result.delta is not None:
                state.apply(result.delta)  # accumulate: each command sees prior changes
            results.append(result)
        return results
