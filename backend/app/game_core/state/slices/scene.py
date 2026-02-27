"""SceneSlice implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from app.game_core.state.base import StateSlice
from app.game_core.state.delta import StateChange


@dataclass(slots=True)
class SceneEntry:
    source: str
    content: str
    visibility: str = "public"
    audience: list[str] | None = None
    tags: list[str] = field(default_factory=list)
    timestamp: float = 0.0

    def snapshot(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "content": self.content,
            "visibility": self.visibility,
            "audience": list(self.audience) if self.audience is not None else None,
            "tags": list(self.tags),
            "timestamp": self.timestamp,
        }


class SceneSlice(StateSlice):
    """Per-tick scene buffer."""

    def __init__(self) -> None:
        super().__init__("scene")
        self.entries: list[SceneEntry] = []
        self.state_changes: list[dict[str, Any]] = []

    def restore(self, payload: Mapping[str, Any]) -> None:
        self.entries = [
            self._coerce_entry(entry)
            for entry in payload.get("entries", [])
        ]
        self.state_changes = [
            dict(change) for change in payload.get("state_changes", [])
        ]
        self.clear_dirty()

    def serialize(self) -> dict[str, Any]:
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        return {
            "entries": [entry.snapshot() for entry in self.entries],
            "state_changes": [dict(change) for change in self.state_changes],
        }

    def get_entries(self, visibility: str | None = None) -> list[SceneEntry]:
        if visibility is None:
            return [self._copy_entry(entry) for entry in self.entries]
        return [
            self._copy_entry(entry)
            for entry in self.entries
            if entry.visibility == visibility
        ]

    def get_for_character(self, character_id: str) -> list[SceneEntry]:
        visible: list[SceneEntry] = []
        for entry in self.entries:
            if entry.visibility == "system":
                continue
            if entry.visibility == "private":
                if entry.audience is None or character_id not in entry.audience:
                    continue
            visible.append(self._copy_entry(entry))
        return visible

    def get_state_changes(self) -> list[dict[str, Any]]:
        return [dict(change) for change in self.state_changes]

    def add_entry(self, entry: SceneEntry) -> None:
        self.entries.append(self._copy_entry(entry))
        self._dirty = True

    def record_state_change(self, change: dict[str, Any]) -> None:
        self.state_changes.append(dict(change))
        self._dirty = True

    def reset(self) -> None:
        self.entries = []
        self.state_changes = []
        self._dirty = True

    def apply_state_change(self, change: StateChange) -> None:
        if change.path == "entries" and change.operation == "add":
            self.add_entry(self._coerce_entry(change.value))
            return
        if change.path == "state_changes" and change.operation == "add":
            if not isinstance(change.value, Mapping):
                raise ValueError("scene state change payload must be a mapping")
            self.record_state_change(dict(change.value))
            return
        if change.path == "reset" and change.operation == "set":
            self.reset()
            return
        raise ValueError(
            f"unsupported scene state change: {change.operation} {change.path}"
        )

    @staticmethod
    def _coerce_entry(raw: Any) -> SceneEntry:
        if isinstance(raw, SceneEntry):
            return SceneEntry(
                source=raw.source,
                content=raw.content,
                visibility=raw.visibility,
                audience=list(raw.audience) if raw.audience is not None else None,
                tags=list(raw.tags),
                timestamp=raw.timestamp,
            )
        if not isinstance(raw, Mapping):
            raise ValueError(f"invalid scene entry: {raw!r}")
        audience = raw.get("audience")
        parsed_audience = None
        if audience is not None:
            parsed_audience = [str(item) for item in audience]
        return SceneEntry(
            source=str(raw.get("source", "")),
            content=str(raw.get("content", "")),
            visibility=str(raw.get("visibility", "public")),
            audience=parsed_audience,
            tags=[str(tag) for tag in raw.get("tags", [])],
            timestamp=float(raw.get("timestamp", 0.0)),
        )

    @classmethod
    def _copy_entry(cls, entry: SceneEntry) -> SceneEntry:
        return cls._coerce_entry(entry)
