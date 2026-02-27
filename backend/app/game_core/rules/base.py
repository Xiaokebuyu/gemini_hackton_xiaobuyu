"""Rules-layer abstract handlers."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.game_core.content import WorldInstance
from app.game_core.rules.models import Command, ExecuteResult, ValidationResult
from app.game_core.state import StateContainer


class CommandHandler(ABC):
    """Pure command handler contract."""

    @property
    @abstractmethod
    def command_types(self) -> list[str]:
        """Command types handled by this handler."""

    @abstractmethod
    def validate(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        """Validate without mutating state."""

    @abstractmethod
    def compute(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ExecuteResult:
        """Compute result without mutating state."""


class StaticCommandHandler(CommandHandler):
    """Shared stub implementation for skeleton handlers."""

    COMMAND_TYPES: tuple[str, ...] = ()

    @property
    def command_types(self) -> list[str]:
        return list(self.COMMAND_TYPES)

    def validate(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        return ValidationResult(ok=False, reason=f"{cmd.type} not implemented")

    def compute(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ExecuteResult:
        return ExecuteResult.not_implemented(cmd.type or self.__class__.__name__)
