"""EventSlice implementation."""

from __future__ import annotations

from typing import Any, ClassVar, Mapping

from app.game_core.state.base import StateSlice
from app.game_core.state.delta import StateChange


def _absolute_tick(time_dict: Mapping[str, Any]) -> int:
    """Compute monotonic absolute tick from a time snapshot {day, slot}."""
    return (int(time_dict.get("day", 1)) - 1) * 24 + int(time_dict.get("slot", 0))


class EventSlice(StateSlice):
    """Event queues and event state machine storage."""

    _VALID_STATES: ClassVar[frozenset[str]] = frozenset(
        {"dormant", "available", "triggered", "active", "resolved", "expired", "cancelled"}
    )

    def __init__(self) -> None:
        super().__init__("events")
        self.active_events: dict[str, dict[str, Any]] = {}
        self.pending_events: list[dict[str, Any]] = []
        self.rumors: list[dict[str, Any]] = []

    def restore(self, payload: Mapping[str, Any]) -> None:
        self.active_events = {
            str(key): self._canonicalize_active_event(str(key), value)
            for key, value in payload.get("active_events", {}).items()
            if isinstance(value, Mapping)
        }
        self.pending_events = [
            dict(value) for value in payload.get("pending_events", [])
            if isinstance(value, Mapping)
        ]
        self.rumors = [
            dict(value) for value in payload.get("rumors", [])
            if isinstance(value, Mapping)
        ]
        self.clear_dirty()

    def serialize(self) -> dict[str, Any]:
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        return {
            "active_events": self.list_active_events(),
            "pending_events": [dict(value) for value in self.pending_events],
            "rumors": [dict(value) for value in self.rumors],
        }

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        event = self.active_events.get(event_id)
        return dict(event) if isinstance(event, dict) else None

    def list_active_events(self) -> dict[str, dict[str, Any]]:
        return {
            key: dict(value)
            for key, value in self.active_events.items()
        }

    def schedule(self, pending_event: dict[str, Any]) -> None:
        self.pending_events.append(dict(pending_event))
        self._dirty = True

    def activate(self, event_id: str, event: dict[str, Any]) -> None:
        self.active_events[event_id] = self._canonicalize_active_event(event_id, event)
        self._dirty = True

    def update_status(self, event_id: str, status: str) -> None:
        self.set_state(event_id, status)

    def set_state(
        self,
        event_id: str,
        state: str,
        *,
        patch: Mapping[str, Any] | None = None,
    ) -> None:
        payload = dict(self.active_events.get(event_id, {}))
        if isinstance(patch, Mapping):
            payload.update({str(key): value for key, value in patch.items()})
        payload["state"] = state
        payload["status"] = state
        self.active_events[event_id] = self._canonicalize_active_event(event_id, payload)
        self._dirty = True

    def add_rumor(self, rumor: dict[str, Any]) -> None:
        self.rumors.append(dict(rumor))
        self._dirty = True

    def spread_rumor(self, rumor_id: str, npc_id: str) -> bool:
        """Add npc_id to a rumor's known_by list.

        Returns True if added, False if already known or rumor not found.
        """
        for rumor in self.rumors:
            rid = rumor.get("rumor_id") or rumor.get("id") or rumor.get("event_id")
            if rid == rumor_id:
                known_by = rumor.get("known_by")
                if not isinstance(known_by, list):
                    known_by = []
                    rumor["known_by"] = known_by
                if npc_id in known_by:
                    return False
                known_by.append(npc_id)
                self._dirty = True
                return True
        return False

    def check_triggers(
        self,
        current_time: Mapping[str, Any],
        current_flags: dict[str, Any] | None = None,
        current_area: str | None = None,
        current_location: str | None = None,
    ) -> list[dict[str, Any]]:
        """Evaluate pending events and return those whose trigger conditions are met.

        Removes due events from pending_events and returns them as copies.
        """
        current_abs = _absolute_tick(current_time)
        due: list[dict[str, Any]] = []
        remaining: list[dict[str, Any]] = []
        for event in self.pending_events:
            if self._is_condition_met(
                event,
                current_abs,
                current_time,
                current_flags,
                current_area,
                current_location,
            ):
                due.append(dict(event))
            else:
                remaining.append(event)
        self.pending_events = remaining
        if due:
            self._dirty = True
        return due

    @staticmethod
    def _is_condition_met(
        event: Mapping[str, Any],
        current_abs: int,
        current_time: Mapping[str, Any],
        current_flags: dict[str, Any] | None,
        current_area: str | None,
        current_location: str | None,
    ) -> bool:
        condition = event.get("trigger_condition")
        if not isinstance(condition, Mapping):
            # Legacy fallback: honour trigger_tick if present
            tt = event.get("trigger_tick")
            return isinstance(tt, int) and tt <= current_abs
        condition_type = condition.get("type", "")
        if condition_type == "absolute_tick":
            tick = condition.get("tick")
            return isinstance(tick, int) and tick <= current_abs
        if condition_type == "time_slots_elapsed":
            count = condition.get("count")
            if not isinstance(count, int):
                return False
            created_at = event.get("created_at", {})
            if not isinstance(created_at, Mapping):
                return False
            created_abs = _absolute_tick(created_at)
            return created_abs + count <= current_abs
        if condition_type == "period_reached":
            period = condition.get("period")
            return isinstance(period, str) and period.strip() == str(current_time.get("period", "")).strip()
        if condition_type == "location_entered":
            area_id = condition.get("area_id")
            location_id = condition.get("location_id")
            if area_id is None and location_id is None:
                return False
            area_ok = True
            if area_id is not None:
                area_ok = isinstance(area_id, str) and area_id.strip() == (current_area or "")
            location_ok = True
            if location_id is not None:
                location_ok = isinstance(location_id, str) and location_id.strip() == (current_location or "")
            return area_ok and location_ok
        if condition_type == "flag_set":
            if current_flags is None:
                return False
            key = condition.get("key") or condition.get("flag_key")
            if not isinstance(key, str) or not key.strip():
                return False
            expected = condition.get("value", True)
            return current_flags.get(key.strip()) == expected
        return False

    def trigger(self, event_id: str) -> None:
        """Transition an active event to the triggered state."""
        self.set_state(event_id, "triggered")

    def resolve(self, event_id: str) -> None:
        """Transition an active event to the resolved state."""
        self.set_state(event_id, "resolved")

    def validate(self) -> list[str]:
        issues: list[str] = []
        if not isinstance(self.active_events, dict):
            issues.append("active_events must be a dict")
        else:
            for key, event in self.active_events.items():
                if not isinstance(event, dict):
                    issues.append(f"active_events[{key}] must be a dict")
                    continue
                if event.get("id") != key or event.get("event_id") != key:
                    issues.append(f"active_events[{key}] id/event_id must match the key")
                state = event.get("state")
                status = event.get("status")
                if state != status:
                    issues.append(f"active_events[{key}] state and status must match")
                if state is not None and state not in self._VALID_STATES:
                    issues.append(
                        f"active_events[{key}] state '{state}' is not a valid event state"
                    )
        if not isinstance(self.pending_events, list):
            issues.append("pending_events must be a list")
        else:
            for i, event in enumerate(self.pending_events):
                if not isinstance(event, dict):
                    issues.append(f"pending_events[{i}] must be a dict")
                    continue
                tc = event.get("trigger_condition")
                if tc is not None and not isinstance(tc, dict):
                    issues.append(f"pending_events[{i}] trigger_condition must be a dict")
                # Legacy field — kept for forward-compatibility with old saves
                tt = event.get("trigger_tick")
                if tt is not None and not isinstance(tt, int):
                    issues.append(f"pending_events[{i}] trigger_tick must be an integer")
        if not isinstance(self.rumors, list):
            issues.append("rumors must be a list")
        else:
            for i, rumor in enumerate(self.rumors):
                if not isinstance(rumor, dict):
                    issues.append(f"rumors[{i}] must be a dict")
        return issues

    def apply_state_change(self, change: StateChange) -> None:
        if change.path == "pending_events" and isinstance(change.value, Mapping):
            if change.operation == "add":
                self.schedule(dict(change.value))
                return
        if change.path.startswith("active_events.") and isinstance(change.value, Mapping):
            _, event_id = change.path.split(".", 1)
            self.activate(event_id, dict(change.value))
            return
        if change.path == "rumors" and isinstance(change.value, Mapping):
            self.add_rumor(dict(change.value))
            return
        if change.path == "rumors.spread" and isinstance(change.value, Mapping):
            rumor_id = str(change.value.get("rumor_id", ""))
            npc_id = str(change.value.get("npc_id", ""))
            if rumor_id and npc_id:
                self.spread_rumor(rumor_id, npc_id)
            return
        raise ValueError(
            f"unsupported event state change: {change.operation} {change.path}"
        )

    @staticmethod
    def _canonicalize_active_event(
        event_id: str,
        event: Mapping[str, Any],
    ) -> dict[str, Any]:
        payload = {str(key): value for key, value in event.items()}
        normalized_id = str(event_id).strip() or str(
            payload.get("event_id", payload.get("id", ""))
        ).strip()
        payload["id"] = normalized_id
        payload["event_id"] = normalized_id

        state = str(payload.get("state", "")).strip()
        status = str(payload.get("status", "")).strip()
        canonical_state = state or status or "triggered"
        payload["state"] = canonical_state
        payload["status"] = canonical_state
        return payload
