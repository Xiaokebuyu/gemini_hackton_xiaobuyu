"""Persistence adapter protocols."""

from __future__ import annotations

from typing import Any, Protocol


class PersistencePort(Protocol):
    """Protocol for save/load boundaries."""

    async def load(self, key: str) -> dict[str, Any]:
        """Load serialized state."""

    async def save(self, key: str, payload: dict[str, Any]) -> None:
        """Persist serialized state."""


class NullPersistencePort:
    """In-memory stub persistence port."""

    def __init__(self) -> None:
        self._storage: dict[str, dict[str, Any]] = {}

    async def load(self, key: str) -> dict[str, Any]:
        return dict(self._storage.get(key, {}))

    async def save(self, key: str, payload: dict[str, Any]) -> None:
        self._storage[key] = dict(payload)
