"""TimeAdvanceHook implementation."""

from __future__ import annotations

from typing import Any

from app.game_core.orchestration.hooks.base import NoOpSettlementHook
from app.game_core.orchestration.hooks.rest_phase import RestPhaseInfo, is_quiet_rest_slot
from app.game_core.orchestration.models import HookResult, SSEEvent
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules.models import Command


class TimeAdvanceHook(NoOpSettlementHook):
    HOOK_PRIORITY = 70
    HOOK_NAME = "time_advance"

    async def execute(self, context: SettlementContext) -> HookResult:
        if not context.state.has_slice("time"):
            return HookResult(
                metadata={
                    "status": "noop",
                    "advanced": False,
                    "from_tick": None,
                    "to_tick": None,
                    "crossed_day": False,
                    "period_changed": False,
                }
            )
        before_time = context.state.time.get_current_time()
        from_tick = context.state.time.absolute_tick()
        if context.state.time.accumulated < 1.0:
            return HookResult(
                metadata={
                    "status": "noop",
                    "advanced": False,
                    "from_tick": from_tick,
                    "to_tick": from_tick,
                    "crossed_day": False,
                    "period_changed": False,
                }
            )
        context.state.time.consume_tick(1.0)
        context.state.time.advance(1)
        after_time = context.state.time.get_current_time()
        to_tick = context.state.time.absolute_tick()
        crossed_day = after_time["day"] != before_time["day"]
        period_changed = after_time["period"] != before_time["period"]
        shops_refreshed = 0
        if crossed_day:
            shops_refreshed = self._refresh_daily_merchants(context)
        rest_info = None
        rest_phase = getattr(context, "rest_phase", None)
        if isinstance(rest_phase, RestPhaseInfo):
            rest_info = {
                "rest_type": rest_phase.rest_action_type,
                "slot_index": rest_phase.rest_slot_index,
                "total_slots": rest_phase.rest_total_slots,
                "is_quiet": is_quiet_rest_slot(context, rest_phase),
                "is_final": rest_phase.is_final_rest_slot,
            }
        return HookResult(
            sse_events=[
                SSEEvent(
                    event_type="time_advanced",
                    payload={
                        "day": after_time["day"],
                        "slot": after_time["slot"],
                        "period": after_time["period"],
                        "absolute_tick": to_tick,
                        "crossed_day": crossed_day,
                        "period_changed": period_changed,
                        "shops_refreshed": shops_refreshed,
                        "rest_info": rest_info,
                    },
                )
            ],
            metadata={
                "status": "advanced",
                "advanced": True,
                "from_tick": from_tick,
                "to_tick": to_tick,
                "crossed_day": crossed_day,
                "period_changed": period_changed,
                "shops_refreshed": shops_refreshed,
            },
        )

    def _refresh_daily_merchants(self, context: SettlementContext) -> int:
        """Execute refresh_shop for daily merchants in the player's current area.

        Falls back to all characters when areas/player slice is unavailable.
        Returns the count of successfully refreshed shops.
        """
        if not context.world.has_registry("characters"):
            return 0
        npc_ids = self._daily_merchant_ids(context)
        count = 0
        for npc_id in npc_ids:
            result = context.execute_command(
                Command(type="refresh_shop", params={"npc_id": npc_id})
            )
            if result.executed:
                count += 1
        return count

    @staticmethod
    def _daily_merchant_ids(context: SettlementContext) -> list[str]:
        """Return IDs of characters with refresh_on='daily' in the current area.

        If areas or player slice is missing, scans all registered characters.
        """
        candidates: set[str] | None = None
        if context.state.has_slice("areas") and context.state.has_slice("player"):
            area_id = context.state.player.current_area
            if area_id and area_id in context.state.areas.areas:
                candidates = set(
                    context.state.areas.areas[area_id].npc_locations.keys()
                )

        result: list[str] = []
        for char in context.world.characters.list_all():
            if candidates is not None and char.id not in candidates:
                continue
            refresh_on: Any = getattr(char, "refresh_on", None)
            if refresh_on is None:
                continue
            if isinstance(refresh_on, str) and refresh_on == "daily":
                result.append(char.id)
            elif isinstance(refresh_on, list) and "daily" in refresh_on:
                result.append(char.id)
        return result
