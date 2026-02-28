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

    def validate(self) -> list[str]:
        issues: list[str] = []
        for key, value in self._dimensions.items():
            if not value.get("id"):
                issues.append(f"tag dimension '{key}' missing id")
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

    def has_tag(self, tag: str) -> bool:
        for dimension in self._dimensions.values():
            tags = dimension.get("tags", [])
            if isinstance(tags, list) and tag in tags:
                return True
        return False
