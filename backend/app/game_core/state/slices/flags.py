"""FlagSlice implementation."""

from __future__ import annotations

from typing import Any, Mapping

from app.game_core.state.base import StateSlice
from app.game_core.state.delta import StateChange


class FlagSlice(StateSlice):
    """Generic world/session key-value flags."""

    def __init__(self) -> None:
        super().__init__("flags")
        self.flags: dict[str, Any] = {}

    def restore(self, payload: Mapping[str, Any]) -> None:
        self.flags = dict(payload.get("flags", {}))
        self.clear_dirty()

    def serialize(self) -> dict[str, Any]:
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        return {"flags": dict(self.flags)}

    def get(self, key: str, default: Any = None) -> Any:
        return self.flags.get(key, default)

    def has(self, key: str) -> bool:
        return key in self.flags

    def set(self, key: str, value: Any) -> None:
        self.flags[key] = value
        self._dirty = True

    def remove(self, key: str) -> None:
        if key in self.flags:
            del self.flags[key]
            self._dirty = True

    def validate(self) -> list[str]:
        issues: list[str] = []
        if not isinstance(self.flags, dict):
            issues.append("flags must be a dict")
        else:
            for key in self.flags:
                if not isinstance(key, str) or not key:
                    issues.append("flag keys must be non-empty strings")
                    break
        return issues

    def apply_state_change(self, change: StateChange) -> None:
        if change.path.startswith("flags."):
            _, key = change.path.split(".", 1)
            if change.operation == "remove":
                self.remove(key)
            else:
                self.set(key, change.value)
            return
        raise ValueError(f"unsupported flag state change: {change.operation} {change.path}")
