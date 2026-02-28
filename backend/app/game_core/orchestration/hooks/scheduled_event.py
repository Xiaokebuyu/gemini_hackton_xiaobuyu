"""ScheduledEventHook implementation."""

from __future__ import annotations

from typing import Any, Mapping

from app.game_core.orchestration.hooks.base import NoOpSettlementHook
from app.game_core.orchestration.models import HookResult, SSEEvent
from app.game_core.orchestration.settlement import SettlementContext


class ScheduledEventHook(NoOpSettlementHook):
    HOOK_PRIORITY = 10
    HOOK_NAME = "scheduled_events"

    async def execute(self, context: SettlementContext) -> HookResult:
        if not context.state.has_slice("events"):
            return HookResult()
        if not context.state.has_slice("time"):
            return HookResult()

        current_tick = context.state.time.absolute_tick()
        due_events = context.state.events.pop_due_pending(current_tick)
        if not due_events:
            return HookResult(
                metadata={"status": "noop", "triggered_count": 0},
            )

        triggered_ids: list[str] = []
        sse_events: list[SSEEvent] = []
        skipped_invalid_count = 0
        triggered_at = context.state.time.get_current_time()
        for pending_event in due_events:
            event_id = self._get_event_id(pending_event)
            if event_id is None:
                skipped_invalid_count += 1
                continue

            event_type = self._normalize_string(
                pending_event.get("event_type"),
                default="generic",
            )
            payload = self._coerce_mapping(pending_event.get("payload"))
            metadata = self._coerce_mapping(pending_event.get("metadata"))
            source = self._normalize_string(
                pending_event.get("source"),
                default="system",
            )

            active_event = {
                "id": event_id,
                "event_id": event_id,
                "event_type": event_type,
                "state": "triggered",
                "status": "triggered",
                "payload": payload,
                "metadata": metadata,
                "source": source,
                "trigger_tick": current_tick,
                "triggered_at": dict(triggered_at),
            }
            context.state.events.activate(event_id, active_event)
            triggered_ids.append(event_id)
            sse_events.append(
                SSEEvent(
                    event_type="event_triggered",
                    payload={
                        "event_id": event_id,
                        "event_type": event_type,
                        "trigger_tick": current_tick,
                        "triggered_at": dict(triggered_at),
                    },
                )
            )

        status = "triggered" if triggered_ids else "noop"
        return HookResult(
            sse_events=sse_events,
            metadata={
                "status": status,
                "triggered_count": len(triggered_ids),
                "skipped_invalid_count": skipped_invalid_count,
                "event_ids": triggered_ids,
            },
        )

    @staticmethod
    def _get_event_id(pending_event: Mapping[str, Any]) -> str | None:
        value = pending_event.get("event_id")
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None

    @staticmethod
    def _normalize_string(value: Any, *, default: str) -> str:
        if not isinstance(value, str):
            return default
        normalized = value.strip()
        return normalized or default

    @staticmethod
    def _coerce_mapping(value: Any) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            return {}
        return {str(key): raw_value for key, raw_value in value.items()}
