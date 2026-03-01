"""Base abstractions for static content registries."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Mapping


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

    @abstractmethod
    def list_all(self) -> list[Any]:
        """Return all items in this registry."""

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

    # ------------------------------------------------------------------
    # Shared helpers for concrete registries
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_tags(item: Any) -> set[str]:
        if isinstance(item, dict):
            raw_tags = item.get("tags", [])
        else:
            raw_tags = getattr(item, "tags", [])
        if not isinstance(raw_tags, list):
            return set()
        return {str(tag) for tag in raw_tags}

    @staticmethod
    def _coerce_dict_mapping(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """Normalize list-or-dict input into {id: dict} mapping.

        Shared by most concrete registries (maps, characters, items, etc.).
        """
        result: dict[str, dict[str, Any]] = {}
        if isinstance(data, list):
            for raw in data:
                if isinstance(raw, Mapping):
                    item_id = str(raw.get("id", "")).strip()
                    if item_id:
                        result[item_id] = dict(raw)
            return result
        if isinstance(data, Mapping):
            for key, raw in data.items():
                if isinstance(raw, Mapping):
                    payload = dict(raw)
                    payload.setdefault("id", str(key))
                    result[str(key)] = payload
        return result

    @staticmethod
    def _coerce_non_empty_string(value: Any) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        if not normalized:
            return None
        return normalized

    @staticmethod
    def _coerce_non_negative_int(value: Any) -> int | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            normalized = int(value)
        except (TypeError, ValueError):
            return None
        if normalized < 0:
            return None
        return normalized

    @staticmethod
    def _coerce_positive_int(value: Any) -> int | None:
        normalized = ContentRegistry._coerce_non_negative_int(value)
        if normalized is None or normalized < 1:
            return None
        return normalized

    @staticmethod
    def _coerce_float(value: Any) -> float | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _is_bool_like(value: Any) -> bool:
        if isinstance(value, bool):
            return True
        if isinstance(value, int):
            return value in {0, 1}
        if isinstance(value, str):
            return value.strip().lower() in {"true", "false", "1", "0", "yes", "no"}
        return False
