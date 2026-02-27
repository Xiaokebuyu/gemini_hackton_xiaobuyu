"""Base abstractions for static content registries."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ContentRegistry(ABC):
    """Read-only registry contract for world content."""

    def __init__(self, name: str) -> None:
        if not name:
            raise ValueError("name must not be empty")
        self._name = name

    @property
    def name(self) -> str:
        """Canonical registry name used by WorldInstance."""
        return self._name

    @abstractmethod
    def load(self, data: dict[str, Any]) -> None:
        """Load structured world data once during world bootstrap."""

    @abstractmethod
    def get(self, content_id: str) -> Any | None:
        """Return one content item by id."""

    def list_all(self) -> list[Any]:
        """Return all items in this registry."""
        raise NotImplementedError

    def snapshot(self) -> dict[str, Any]:
        """Return a lightweight summary for diagnostics and persistence hooks."""
        return {
            "name": self.name,
            "size": len(self.list_all()),
        }

    def validate(self) -> list[str]:
        """Return registry validation issues. Empty means valid."""
        return []

    def query_by_tags(
        self,
        tags: list[str],
        match_all: bool = True,
    ) -> list[Any]:
        """Default tag query for dict-like entries with a 'tags' field."""
        if not tags:
            return self.list_all()

        required = {str(tag) for tag in tags}
        matches: list[Any] = []
        for item in self.list_all():
            item_tags = self._extract_tags(item)
            if match_all and required.issubset(item_tags):
                matches.append(item)
            elif not match_all and required.intersection(item_tags):
                matches.append(item)
        return matches

    @staticmethod
    def _extract_tags(item: Any) -> set[str]:
        if not isinstance(item, dict):
            return set()
        raw_tags = item.get("tags", [])
        if not isinstance(raw_tags, list):
            return set()
        return {str(tag) for tag in raw_tags}
