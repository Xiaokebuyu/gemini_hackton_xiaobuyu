"""Settlement hook base classes."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.game_core.orchestration.models import HookResult
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.state import StateChange


class SettlementHook(ABC):
    """Settlement extension point."""

    @property
    @abstractmethod
    def priority(self) -> int:
        """Execution priority."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Hook name."""

    @abstractmethod
    async def execute(self, context: SettlementContext) -> HookResult:
        """Run hook logic."""

    def should_skip(self, change_log: list[StateChange]) -> bool:
        del change_log
        return False


class NoOpSettlementHook(SettlementHook):
    """Base class for inert hook skeletons."""

    HOOK_PRIORITY = 50
    HOOK_NAME = "noop"

    @property
    def priority(self) -> int:
        return self.HOOK_PRIORITY

    @property
    def name(self) -> str:
        return self.HOOK_NAME

    async def execute(self, context: SettlementContext) -> HookResult:
        del context
        return HookResult(metadata={"status": "stub", "hook": self.name})
