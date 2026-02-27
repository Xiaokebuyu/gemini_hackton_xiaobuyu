"""EncounterHook skeleton."""

from app.game_core.orchestration.hooks.base import NoOpSettlementHook


class EncounterHook(NoOpSettlementHook):
    HOOK_PRIORITY = 40
    HOOK_NAME = "encounter"
