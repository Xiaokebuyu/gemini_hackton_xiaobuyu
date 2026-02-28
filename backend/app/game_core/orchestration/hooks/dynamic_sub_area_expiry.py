"""DynamicSubAreaExpiryHook implementation."""

from __future__ import annotations

from app.game_core.orchestration.hooks.base import NoOpSettlementHook
from app.game_core.orchestration.models import HookResult, SSEEvent
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules.models import Command


class DynamicSubAreaExpiryHook(NoOpSettlementHook):
    """Tick down dynamic sub-area expiry counters and remove expired ones.

    Runs after TimeAdvanceHook (P70) so the clock has already advanced.
    Only processes the player's current area.
    """

    HOOK_PRIORITY = 75
    HOOK_NAME = "dynamic_sub_area_expiry"

    async def execute(self, context: SettlementContext) -> HookResult:
        if not context.state.has_slice("areas"):
            return HookResult(metadata={"status": "noop", "reason": "missing_areas"})
        if not context.state.has_slice("player"):
            return HookResult(metadata={"status": "noop", "reason": "missing_player"})

        area_id = context.state.player.current_area
        if not area_id or area_id not in context.state.areas.areas:
            return HookResult(
                metadata={"status": "noop", "reason": "no_current_area"}
            )

        removed_ids = context.state.areas.tick_expiry(area_id)
        if not removed_ids:
            return HookResult(
                metadata={
                    "status": "noop",
                    "area_id": area_id,
                    "removed_count": 0,
                }
            )

        # If player is inside an expired sub-area, eject them
        player_ejected = False
        current_location = context.state.player.current_location
        if current_location and current_location in removed_ids:
            context.execute_command(
                Command(type="leave_sub_location", source="system")
            )
            player_ejected = True

        sse_events: list[SSEEvent] = [
            SSEEvent(
                event_type="dynamic_sub_areas_expired",
                payload={
                    "area_id": area_id,
                    "removed_ids": list(removed_ids),
                    "removed_count": len(removed_ids),
                    "player_ejected": player_ejected,
                },
            )
        ]

        return HookResult(
            sse_events=sse_events,
            metadata={
                "status": "expired",
                "area_id": area_id,
                "removed_count": len(removed_ids),
                "removed_ids": list(removed_ids),
                "player_ejected": player_ejected,
            },
        )
