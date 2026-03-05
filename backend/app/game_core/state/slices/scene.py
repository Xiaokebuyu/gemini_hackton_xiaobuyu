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

    _VALID_VISIBILITY = frozenset({"public", "private", "system"})

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
        audience_tokens = {character_id}
        if ":" not in character_id:
            audience_tokens.add(f"npc:{character_id}")
            audience_tokens.add(f"teammate:{character_id}")
        for entry in self.entries:
            if entry.visibility == "system":
                continue
            if entry.visibility == "private":
                if entry.audience is None:
                    continue
                normalized = {str(item) for item in entry.audience}
                if not normalized.intersection(audience_tokens):
                    continue
            visible.append(self._copy_entry(entry))
        return visible

    def get_for_role(
        self,
        role: str,
        character_id: str | None = None,
    ) -> list[SceneEntry]:
        """Return entries visible to one viewer role.

        Visibility rules match the design spec:
        - gm: public + private, but not system
        - npc/teammate: public + private entries whose audience contains
          ``npc:{id}`` / ``teammate:{id}``
        """
        if role == "gm":
            return [
                self._copy_entry(entry)
                for entry in self.entries
                if entry.visibility != "system"
            ]
        if role not in {"npc", "teammate"}:
            raise ValueError(f"unsupported scene viewer role: {role}")
        if not character_id:
            raise ValueError(f"{role} scene view requires character_id")
        audience_token = f"{role}:{character_id}"
        visible: list[SceneEntry] = []
        for entry in self.entries:
            if entry.visibility == "system":
                continue
            if entry.visibility == "private":
                if entry.audience is None:
                    continue
                normalized = {str(item) for item in entry.audience}
                if audience_token not in normalized:
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

    def validate(self) -> list[str]:
        issues: list[str] = []
        if not isinstance(self.entries, list):
            issues.append("entries must be a list")
        else:
            for i, entry in enumerate(self.entries):
                if not isinstance(entry, SceneEntry):
                    issues.append(f"entries[{i}] must be a SceneEntry")
                    continue
                if not entry.source:
                    issues.append(f"entries[{i}] source must not be empty")
                if entry.visibility not in self._VALID_VISIBILITY:
                    issues.append(f"entries[{i}] visibility must be public/private/system")
        if not isinstance(self.state_changes, list):
            issues.append("state_changes must be a list")
        else:
            for i, change in enumerate(self.state_changes):
                if not isinstance(change, dict):
                    issues.append(f"state_changes[{i}] must be a dict")
        return issues

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
