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
            str(key): dict(value)
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
            "active_events": {
                key: dict(value)
                for key, value in self.active_events.items()
            },
            "pending_events": [dict(value) for value in self.pending_events],
            "rumors": [dict(value) for value in self.rumors],
        }

    def schedule(self, pending_event: dict[str, Any]) -> None:
        self.pending_events.append(dict(pending_event))
        self._dirty = True

    def activate(self, event_id: str, event: dict[str, Any]) -> None:
        self.active_events[event_id] = dict(event)
        self._dirty = True

    def update_status(self, event_id: str, status: str) -> None:
        self.active_events.setdefault(event_id, {})["status"] = status
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
