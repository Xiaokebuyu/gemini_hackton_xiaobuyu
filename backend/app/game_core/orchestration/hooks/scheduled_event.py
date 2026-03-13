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

        current_time = context.state.time.get_current_time()
        current_flags = None
        if context.state.has_slice("flags"):
            flags_snapshot = context.state.flags.snapshot()
            raw_flags = flags_snapshot.get("flags")
            if isinstance(raw_flags, Mapping):
                current_flags = {str(key): value for key, value in raw_flags.items()}
        current_area = None
        current_location = None
        current_room = None
        if context.state.has_slice("player"):
            current_area = context.state.player.current_area
            current_location = context.state.player.current_location
            current_room = getattr(context.state.player, "current_room", None)
        due_events = context.state.events.check_triggers(
            current_time,
            current_flags=current_flags,
            current_area=current_area,
            current_location=current_location,
            current_room=current_room,
        )
        if not due_events:
            return HookResult(
                metadata={"status": "noop", "triggered_count": 0},
            )

        triggered_ids: list[str] = []
        sse_events: list[SSEEvent] = []
        skipped_invalid_count = 0
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
            raw_tc = pending_event.get("trigger_condition")
            if isinstance(raw_tc, dict):
                trigger_condition = raw_tc
            else:
                # Legacy fallback: reconstruct from trigger_tick if present
                tt = pending_event.get("trigger_tick")
                trigger_condition = (
                    {"type": "absolute_tick", "tick": int(tt)}
                    if isinstance(tt, int)
                    else {}
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
                "trigger_condition": trigger_condition,
                "triggered_at": dict(current_time),
            }
            context.state.events.activate(event_id, active_event)
            triggered_ids.append(event_id)
            sse_events.append(
                SSEEvent(
                    event_type="event_triggered",
                    payload={
                        "event_id": event_id,
                        "event_type": event_type,
                        "trigger_condition": trigger_condition,
                        "triggered_at": dict(current_time),
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
