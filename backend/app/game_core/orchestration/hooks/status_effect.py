"""StatusEffectHook skeleton."""

from app.game_core.orchestration.hooks.base import NoOpSettlementHook


class StatusEffectHook(NoOpSettlementHook):
    HOOK_PRIORITY = 20
    HOOK_NAME = "status_effects"
