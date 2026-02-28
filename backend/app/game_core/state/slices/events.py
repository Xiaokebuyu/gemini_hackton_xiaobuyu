"""EventSlice implementation."""

from __future__ import annotations

from typing import Any, Mapping

from app.game_core.state.base import StateSlice
from app.game_core.state.delta import StateChange


class EventSlice(StateSlice):
    """Event queues and event state machine storage."""

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

    def pop_due_pending(self, current_tick: int) -> list[dict[str, Any]]:
        due: list[dict[str, Any]] = []
        remaining: list[dict[str, Any]] = []
        for event in self.pending_events:
            trigger_tick = int(event.get("trigger_tick", current_tick))
            if trigger_tick <= current_tick:
                due.append(dict(event))
            else:
                remaining.append(event)
        self.pending_events = remaining
        if due:
            self._dirty = True
        return due

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
        if not isinstance(self.pending_events, list):
            issues.append("pending_events must be a list")
        else:
            for i, event in enumerate(self.pending_events):
                if not isinstance(event, dict):
                    issues.append(f"pending_events[{i}] must be a dict")
                    continue
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
