"""PartySlice implementation."""

from __future__ import annotations

from typing import Any, Mapping

from app.game_core.state.base import StateSlice
from app.game_core.state.delta import StateChange


class PartySlice(StateSlice):
    """Party composition and companion sentiment."""

    def __init__(self) -> None:
        super().__init__("party")
        self.members: dict[str, dict[str, Any]] = {}
        self.companion_approval: dict[str, int] = {}
        self.shared_experiences: list[dict[str, Any]] = []

    def restore(self, payload: Mapping[str, Any]) -> None:
        self.members = {
            str(key): dict(value)
            for key, value in payload.get("members", {}).items()
            if isinstance(value, Mapping)
        }
        self.companion_approval = {
            str(key): int(value)
            for key, value in payload.get("companion_approval", {}).items()
        }
        self.shared_experiences = [
            dict(value) for value in payload.get("shared_experiences", [])
            if isinstance(value, Mapping)
        ]
        self.clear_dirty()

    def serialize(self) -> dict[str, Any]:
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        return {
            "members": {
                key: dict(value)
                for key, value in self.members.items()
            },
            "companion_approval": dict(self.companion_approval),
            "shared_experiences": [dict(value) for value in self.shared_experiences],
        }

    def add_member(self, character_id: str, member: dict[str, Any]) -> None:
        self.members[character_id] = dict(member)
        self._dirty = True

    def remove_member(self, character_id: str) -> None:
        if character_id in self.members:
            del self.members[character_id]
            self._dirty = True

    def modify_approval(self, character_id: str, delta: int) -> None:
        self.companion_approval[character_id] = self.companion_approval.get(character_id, 0) + delta
        self._dirty = True

    def record_experience(self, experience: dict[str, Any]) -> None:
        self.shared_experiences.append(dict(experience))
        self._dirty = True

    def apply_state_change(self, change: StateChange) -> None:
        if change.path.startswith("members.") and isinstance(change.value, Mapping):
            _, character_id = change.path.split(".", 1)
            if change.operation == "remove":
                self.remove_member(character_id)
            else:
                self.add_member(character_id, dict(change.value))
            return
        if change.path.startswith("companion_approval."):
            _, character_id = change.path.split(".", 1)
            if change.operation == "add":
                self.modify_approval(character_id, int(change.value))
            else:
                self.companion_approval[character_id] = int(change.value)
                self._dirty = True
            return
        if change.path == "shared_experiences" and isinstance(change.value, Mapping):
            self.record_experience(dict(change.value))
            return
        raise ValueError(
            f"unsupported party state change: {change.operation} {change.path}"
        )
