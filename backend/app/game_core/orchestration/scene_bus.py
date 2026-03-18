"""SceneBus implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from app.game_core.state.delta import StateChange
from app.game_core.state.slices import SceneEntry, SceneSlice

if TYPE_CHECKING:
    from app.game_core.state import StateContainer


class SceneBus:
    """Runtime view over SceneSlice."""

    def __init__(self, scene_slice: SceneSlice) -> None:
        self._scene = scene_slice
        self._location_provider: Callable[[], dict[str, str | None]] | None = None

    def set_location_provider(
        self, provider: Callable[[], dict[str, str | None]]
    ) -> None:
        """Inject a callback that returns current player location.

        The callback should return {"area_id": ..., "location_id": ..., "room_id": ...}.
        """
        self._location_provider = provider

    @property
    def slice(self) -> SceneSlice:
        return self._scene

    def add_entry(self, entry: SceneEntry | dict[str, Any]) -> None:
        if isinstance(entry, SceneEntry):
            self._stamp_location(entry)
            self._scene.add_entry(entry)
            return
        scene_entry = SceneEntry(**entry)
        self._stamp_location(scene_entry)
        self._scene.add_entry(scene_entry)

    def _stamp_location(self, entry: SceneEntry) -> None:
        """Auto-fill location metadata if not already set."""
        if self._location_provider is None:
            return
        meta = entry.metadata
        if "area_id" in meta:
            return  # already set explicitly
        loc = self._location_provider()
        if loc.get("area_id"):
            meta["area_id"] = loc["area_id"]
        if loc.get("location_id"):
            meta["location_id"] = loc["location_id"]
        if loc.get("room_id"):
            meta["room_id"] = loc["room_id"]

    def get_for_character(self, character_id: str) -> list[SceneEntry]:
        return self._scene.get_for_character(character_id)

    def get_for_role(
        self,
        role: str,
        character_id: str | None = None,
    ) -> list[SceneEntry]:
        return self._scene.get_for_role(role, character_id)

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
