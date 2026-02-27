"""SceneBusResetHook skeleton."""

from __future__ import annotations

from app.game_core.orchestration.hooks.base import NoOpSettlementHook
from app.game_core.orchestration.models import HookResult
from app.game_core.orchestration.settlement import SettlementContext


class SceneBusResetHook(NoOpSettlementHook):
    HOOK_PRIORITY = 90
    HOOK_NAME = "scene_reset"

    async def execute(self, context: SettlementContext) -> HookResult:
        context.scene_bus.reset()
        context.change_log.clear()
        return HookResult(metadata={"status": "reset"})
