"""TagRegistry implementation."""

from __future__ import annotations

from typing import Any, Mapping

from app.game_core.content.base import ContentRegistry


class TagRegistry(ContentRegistry):
    """Registry for tag definitions and dimensions."""

    def __init__(self) -> None:
        super().__init__("tags")
        self._dimensions: dict[str, dict[str, Any]] = {}

    def load(self, data: dict[str, Any]) -> None:
        self._dimensions = {}
        if not isinstance(data, Mapping):
            return
        for dimension, payload in data.items():
            if isinstance(payload, Mapping):
                normalized = dict(payload)
                normalized.setdefault("id", str(dimension))
                normalized.setdefault("tags", normalized.get("tags", []))
                self._dimensions[str(dimension)] = normalized

    def get(self, content_id: str) -> Any | None:
        return self._dimensions.get(content_id)

    def list_all(self) -> list[Any]:
        return [dict(value) for value in self._dimensions.values()]

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def all_tags(self) -> set[str]:
        """Return all tag values across all dimensions."""
        result: set[str] = set()
        for dimension in self._dimensions.values():
            tags = dimension.get("tags", [])
            if isinstance(tags, list):
                for tag in tags:
                    s = self._coerce_non_empty_string(tag)
                    if s is not None:
                        result.add(s)
        return result

    def get_dimension_for_tag(self, tag: str) -> str | None:
        """Return the dimension name that contains the given tag, or None."""
        for dim_key, dimension in self._dimensions.items():
            tags = dimension.get("tags", [])
            if isinstance(tags, list) and tag in tags:
                return dim_key
        return None

    def has_tag(self, tag: str) -> bool:
        for dimension in self._dimensions.values():
            tags = dimension.get("tags", [])
            if isinstance(tags, list) and tag in tags:
                return True
        return False

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        issues: list[str] = []
        for key, value in self._dimensions.items():
            if not value.get("id"):
                issues.append(f"tag dimension '{key}' missing id")

            if "description" in value and self._coerce_non_empty_string(value.get("description")) is None:
                issues.append(f"tag dimension '{key}' has invalid description")

            tags = value.get("tags")
            if tags is not None and not isinstance(tags, list):
                issues.append(f"tag dimension '{key}' has invalid tags")
                continue
            if not isinstance(tags, list):
                continue
            for index, tag in enumerate(tags):
                if self._coerce_non_empty_string(tag) is None:
                    issues.append(
                        f"tag dimension '{key}' tags[{index}] must be a non-empty string"
                    )
        return issues
