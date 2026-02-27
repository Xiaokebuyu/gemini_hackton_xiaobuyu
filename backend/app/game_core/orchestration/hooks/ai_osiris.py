"""AIOsirisHook skeleton."""

from app.game_core.orchestration.hooks.base import NoOpSettlementHook


class AIOsirisHook(NoOpSettlementHook):
    HOOK_PRIORITY = 30
    HOOK_NAME = "ai_osiris"
