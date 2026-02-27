"""TimeAdvanceHook skeleton."""

from app.game_core.orchestration.hooks.base import NoOpSettlementHook


class TimeAdvanceHook(NoOpSettlementHook):
    HOOK_PRIORITY = 70
    HOOK_NAME = "time_advance"
