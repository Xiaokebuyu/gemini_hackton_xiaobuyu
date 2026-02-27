"""SceneBus implementation."""

from __future__ import annotations

from typing import Any

from app.game_core.state.delta import StateChange
from app.game_core.state.slices import SceneEntry, SceneSlice


class SceneBus:
    """Runtime view over SceneSlice."""

    def __init__(self, scene_slice: SceneSlice) -> None:
        self._scene = scene_slice

    @property
    def slice(self) -> SceneSlice:
        return self._scene

    def add_entry(self, entry: SceneEntry | dict[str, Any]) -> None:
        if isinstance(entry, SceneEntry):
            self._scene.add_entry(entry)
            return
        self._scene.add_entry(SceneEntry(**entry))

    def get_for_character(self, character_id: str) -> list[SceneEntry]:
        return self._scene.get_for_character(character_id)

    def record_state_change(self, change: StateChange | dict[str, Any]) -> None:
        if isinstance(change, StateChange):
            payload = {
                "slice": change.slice,
                "operation": change.operation,
                "path": change.path,
                "value": change.value,
            }
            self._scene.record_state_change(payload)
            return
        self._scene.record_state_change(change)

    def reset(self) -> None:
        self._scene.reset()

    def snapshot(self) -> dict[str, Any]:
        return self._scene.snapshot()
