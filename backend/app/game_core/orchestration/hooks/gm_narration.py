"""GmNarrationHook skeleton."""

from app.game_core.orchestration.hooks.base import NoOpSettlementHook


class GmNarrationHook(NoOpSettlementHook):
    HOOK_PRIORITY = 80
    HOOK_NAME = "gm_narration"
