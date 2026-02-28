"""LoreRegistry implementation."""

from __future__ import annotations

from typing import Any

from app.game_core.content.base import ContentRegistry


class LoreRegistry(ContentRegistry):
    """Registry for lore and world-rule text blocks."""

    def __init__(self) -> None:
        super().__init__("lore")
        self._items: dict[str, dict[str, Any]] = {}

    def load(self, data: dict[str, Any]) -> None:
        self._items = self._coerce_dict_mapping(data)

    def get(self, content_id: str) -> Any | None:
        item = self._items.get(content_id)
        return dict(item) if isinstance(item, dict) else item

    def list_all(self) -> list[Any]:
        return [dict(value) for value in self._items.values()]

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def get_by_tag(self, tag: str) -> list[dict[str, Any]]:
        """Return lore entries that have the given tag in their tags list."""
        return [
            dict(item) for item in self._items.values()
            if isinstance(item.get("tags"), list) and tag in item["tags"]
        ]

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        issues: list[str] = []
        for item_id, item in self._items.items():
            if not item.get("id"):
                issues.append(f"lore entry '{item_id}' missing id")

            for field_name in ("name", "title"):
                if field_name in item and self._coerce_non_empty_string(item.get(field_name)) is None:
                    issues.append(f"lore entry '{item_id}' has invalid {field_name}")

            for field_name in ("content", "text"):
                if field_name in item and self._coerce_non_empty_string(item.get(field_name)) is None:
                    issues.append(f"lore entry '{item_id}' has invalid {field_name}")

            tags = item.get("tags")
            if tags is not None:
                if not isinstance(tags, list):
                    issues.append(f"lore entry '{item_id}' has invalid tags")
                else:
                    for index, tag in enumerate(tags):
                        if self._coerce_non_empty_string(tag) is None:
                            issues.append(
                                f"lore entry '{item_id}' tags[{index}] must be a non-empty string"
                            )
        return issues
