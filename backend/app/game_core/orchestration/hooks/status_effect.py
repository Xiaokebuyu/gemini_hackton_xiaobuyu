"""StatusEffectHook implementation."""

from __future__ import annotations

from app.game_core.orchestration.hooks.base import NoOpSettlementHook
from app.game_core.orchestration.models import HookResult, SSEEvent
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules.models import Command


class StatusEffectHook(NoOpSettlementHook):
    HOOK_PRIORITY = 20
    HOOK_NAME = "status_effects"

    async def execute(self, context: SettlementContext) -> HookResult:
        if not context.state.has_slice("player"):
            return HookResult(
                metadata={
                    "status": "noop",
                    "reason": "missing_player",
                }
            )

        result = context.execute_command(Command(type="tick_effects", source="system"))
        if not result.executed:
            return HookResult(
                metadata={
                    "status": "error",
                    "errors": list(result.errors),
                    "effect_result": dict(result.metadata),
                }
            )

        effect_result = dict(result.metadata)
        changed = self._has_effect_changes(effect_result)
        sse_events: list[SSEEvent] = []
        if changed:
            sse_events.append(
                SSEEvent(
                    event_type="status_effects_ticked",
                    payload={
                        "applied_count": int(effect_result.get("applied_count", 0)),
                        "removed_count": int(effect_result.get("removed_count", 0)),
                        "expired_count": int(effect_result.get("expired_count", 0)),
                        "hp_delta": int(effect_result.get("hp_delta", 0)),
                    },
                )
            )

        # Tick combat participant effects
        if context.state.has_slice("areas"):
            combat_result = context.execute_command(
                Command(type="tick_combat_effects", source="system")
            )
            if combat_result.executed:
                combat_meta = dict(combat_result.metadata)
                if self._has_combat_changes(combat_meta):
                    changed = True
                    sse_events.append(
                        SSEEvent(
                            event_type="combat_effects_ticked",
                            payload={
                                "combats_processed": int(combat_meta.get("combats_processed", 0)),
                                "participants_ticked": int(combat_meta.get("participants_ticked", 0)),
                                "effects_expired": int(combat_meta.get("effects_expired", 0)),
                                "total_hp_delta": int(combat_meta.get("total_hp_delta", 0)),
                            },
                        )
                    )

        return HookResult(
            sse_events=sse_events,
            metadata={
                "status": "ticked" if changed else "noop",
                "effect_result": effect_result,
            },
        )

    @staticmethod
    def _has_combat_changes(meta: dict[str, object]) -> bool:
        return (
            int(meta.get("participants_ticked", 0)) > 0
            or int(meta.get("effects_expired", 0)) > 0
        )

    @staticmethod
    def _has_effect_changes(effect_result: dict[str, object]) -> bool:
        return (
            int(effect_result.get("applied_count", 0)) > 0
            or int(effect_result.get("removed_count", 0)) > 0
            or int(effect_result.get("expired_count", 0)) > 0
            or int(effect_result.get("hp_delta", 0)) != 0
        )
