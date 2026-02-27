"""EventConditionHook skeleton."""

from app.game_core.orchestration.hooks.base import NoOpSettlementHook


class EventConditionHook(NoOpSettlementHook):
    HOOK_PRIORITY = 50
    HOOK_NAME = "event_conditions"
