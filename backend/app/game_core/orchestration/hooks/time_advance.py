"""TimeAdvanceHook implementation."""

from __future__ import annotations

from app.game_core.orchestration.hooks.base import NoOpSettlementHook
from app.game_core.orchestration.models import HookResult, SSEEvent
from app.game_core.orchestration.settlement import SettlementContext


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
            },
        )
