"""NpcScheduleHook skeleton."""

from app.game_core.orchestration.hooks.base import NoOpSettlementHook


class NpcScheduleHook(NoOpSettlementHook):
    HOOK_PRIORITY = 60
    HOOK_NAME = "npc_schedule"
