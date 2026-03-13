"""XpAdvancementHook — check XP threshold and fire level_up + SSE notification."""

from __future__ import annotations

from app.game_core.orchestration.hooks.base import NoOpSettlementHook
from app.game_core.orchestration.models import HookResult, SSEEvent
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules.models import Command

class XpAdvancementHook(NoOpSettlementHook):
    """Check XP threshold and auto-trigger level_up + player_level_up SSE.

    Priority 58 — after MilestoneCompletionHook (54) / MilestoneUnlockHook (55)
    but before SharedExperienceHook (62), so XP granted by quest rewards this
    tick is also processed.
    """

    HOOK_PRIORITY = 58
    HOOK_NAME = "xp_advancement"

    async def execute(self, context: SettlementContext) -> HookResult:
        if not context.state.has_slice("player"):
            return HookResult(metadata={"status": "noop", "reason": "missing_player_slice"})

        player = context.state.player
        threshold = player.level * 1000  # consistent with GrowthHandler
        if player.xp < threshold:
            return HookResult(metadata={"status": "noop", "reason": "xp_below_threshold"})

        result = context.execute_command(
            Command(type="level_up", params={}, source="system")
        )
        if not result.executed:
            return HookResult(
                metadata={"status": "noop", "reason": "level_up_rejected", "errors": result.errors}
            )

        meta = result.metadata or {}
        to_level = meta.get("to_level", player.level)
        sse = SSEEvent(
            event_type="player_level_up",
            payload={
                "from_level": meta.get("from_level"),
                "to_level": to_level,
                "hp_gain": meta.get("hp_gain"),
                "features": meta.get("added_features", []),
                "asi_available": bool(meta.get("asi_available", player.asi_available)),
            },
        )
        return HookResult(
            sse_events=[sse],
            metadata={"status": "applied", "to_level": to_level},
        )
