"""ScheduledEventHook skeleton."""

from app.game_core.orchestration.hooks.base import NoOpSettlementHook


class ScheduledEventHook(NoOpSettlementHook):
    HOOK_PRIORITY = 10
    HOOK_NAME = "scheduled_events"
