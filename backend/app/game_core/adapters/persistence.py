"""Persistence adapter protocols."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class PersistencePort(Protocol):
    """Protocol for save/load boundaries."""

    async def load(self, key: str) -> dict[str, Any]:
        """Load serialized state."""

    async def save(self, key: str, payload: dict[str, Any]) -> None:
        """Persist serialized state."""


@runtime_checkable
class SessionCatalogPort(PersistencePort, Protocol):
    """Extended persistence contract for session catalogs."""

    async def list_keys(self) -> list[str]:
        """List stored session keys."""

    async def delete(self, key: str) -> bool:
        """Delete one stored session key."""


class NullPersistencePort:
    """In-memory stub persistence port."""

    def __init__(self) -> None:
        self._storage: dict[str, dict[str, Any]] = {}

    async def load(self, key: str) -> dict[str, Any]:
        return dict(self._storage.get(key, {}))

    async def save(self, key: str, payload: dict[str, Any]) -> None:
        self._storage[key] = dict(payload)

    async def list_keys(self) -> list[str]:
        return sorted(self._storage.keys())

    async def delete(self, key: str) -> bool:
        if key not in self._storage:
            return False
        del self._storage[key]
        return True
