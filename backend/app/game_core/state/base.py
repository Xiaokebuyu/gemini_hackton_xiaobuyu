"""Base abstractions for session state."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Mapping, cast

from app.game_core.state.delta import StateChange, StateDelta
from app.game_core.state.internal import SupportsStateChange


class StateSlice(ABC):
    """Contract for one cohesive state slice."""

    def __init__(self, name: str) -> None:
        if not name:
            raise ValueError("name must not be empty")
        self._name = name
        self._dirty = False

    @property
    def name(self) -> str:
        """Canonical slice name used by StateContainer."""
        return self._name

    @abstractmethod
    def restore(self, payload: Mapping[str, Any]) -> None:
        """Restore the slice from persisted payload."""

    @abstractmethod
    def serialize(self) -> dict[str, Any]:
        """Return the slice payload for persistence."""

    @abstractmethod
    def snapshot(self) -> dict[str, Any]:
        """Return the slice payload for inspection."""

    @property
    def dirty(self) -> bool:
        """Whether this slice has unpersisted changes."""
        return self._dirty

    def clear_dirty(self) -> None:
        """Reset dirty tracking after persistence."""
        self._dirty = False

    def validate(self) -> list[str]:
        """Return slice validation issues. Empty means valid."""
        return []


class StateContainer:
    """Container that owns all session state slices."""

    def __init__(self) -> None:
        self._slices: dict[str, StateSlice] = {}

    def register(self, slice_obj: StateSlice) -> None:
        """Register a state slice by its canonical name."""
        name = slice_obj.name
        if name in self._slices:
            raise ValueError(f"slice already registered: {name}")
        self._slices[name] = slice_obj

    def get_slice(self, name: str) -> StateSlice:
        """Return one registered slice."""
        try:
            return self._slices[name]
        except KeyError as exc:
            raise KeyError(f"unknown slice: {name}") from exc

    def has_slice(self, name: str) -> bool:
        """Whether a slice is registered."""
        return name in self._slices

    def all_slices(self) -> list[tuple[str, StateSlice]]:
        """Return all registered slices in insertion order."""
        return list(self._slices.items())

    @property
    def time(self) -> StateSlice:
        return self.get_slice("time")

    @property
    def player(self) -> StateSlice:
        return self.get_slice("player")

    @property
    def areas(self) -> StateSlice:
        return self.get_slice("areas")

    @property
    def relations(self) -> StateSlice:
        return self.get_slice("relations")

    @property
    def quests(self) -> StateSlice:
        return self.get_slice("quests")

    @property
    def flags(self) -> StateSlice:
        return self.get_slice("flags")

    @property
    def events(self) -> StateSlice:
        return self.get_slice("events")

    @property
    def party(self) -> StateSlice:
        return self.get_slice("party")

    @property
    def narrative_plan(self) -> StateSlice:
        return self.get_slice("narrative_plan")

    @property
    def scene(self) -> StateSlice:
        return self.get_slice("scene")

    def snapshot(self) -> dict[str, dict[str, Any]]:
        """Return a complete state snapshot."""
        return {
            name: slice_obj.snapshot()
            for name, slice_obj in self._slices.items()
        }

    def restore(self, payload: Mapping[str, Mapping[str, Any]]) -> None:
        """Restore all known slices from a persisted payload."""
        for name, slice_payload in payload.items():
            if name not in self._slices:
                continue
            self._slices[name].restore(slice_payload)

    def persist(self) -> dict[str, dict[str, Any]]:
        """Persist only dirty slices, per the design spec."""
        return {
            name: slice_obj.serialize()
            for name, slice_obj in self._slices.items()
            if slice_obj.dirty
        }

    def apply(self, delta: StateDelta) -> None:
        """Dispatch each StateChange to its target slice.

        Concrete slices can expose an internal `apply_state_change(change)` hook.
        This keeps the public StateSlice ABC aligned with the docs while still
        letting the container consume StateDelta in tests and early scaffolding.
        """
        for change in delta.changes:
            self._apply_change(change)

    def _apply_change(self, change: StateChange) -> None:
        slice_obj = self.get_slice(change.slice)
        if not isinstance(slice_obj, SupportsStateChange):
            raise NotImplementedError(
                f"slice '{change.slice}' does not implement apply_state_change()"
            )
        handler = cast(SupportsStateChange, slice_obj)
        handler.apply_state_change(change)

    def validate(self) -> dict[str, list[str]]:
        """Collect validation issues from all slices."""
        return {
            name: issues
            for name, slice_obj in self._slices.items()
            if (issues := slice_obj.validate())
        }
