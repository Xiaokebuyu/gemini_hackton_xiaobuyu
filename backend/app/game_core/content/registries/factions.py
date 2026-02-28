"""FactionRegistry implementation."""

from __future__ import annotations

from typing import Any, Mapping

from app.game_core.content.base import ContentRegistry


class FactionRegistry(ContentRegistry):
    """Registry for faction definitions."""

    def __init__(self) -> None:
        super().__init__("factions")
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
        """Return factions that have the given tag in their tags list."""
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
                issues.append(f"faction '{item_id}' missing id")

            if "name" in item and self._coerce_non_empty_string(item.get("name")) is None:
                issues.append(f"faction '{item_id}' has invalid name")

            if "description" in item and self._coerce_non_empty_string(item.get("description")) is None:
                issues.append(f"faction '{item_id}' has invalid description")

            if "alignment" in item and self._coerce_non_empty_string(item.get("alignment")) is None:
                issues.append(f"faction '{item_id}' has invalid alignment")

            if "relations" in item and not isinstance(item.get("relations"), Mapping):
                issues.append(f"faction '{item_id}' has invalid relations")

            tags = item.get("tags")
            if tags is not None:
                if not isinstance(tags, list):
                    issues.append(f"faction '{item_id}' has invalid tags")
                else:
                    for index, tag in enumerate(tags):
                        if self._coerce_non_empty_string(tag) is None:
                            issues.append(
                                f"faction '{item_id}' tags[{index}] must be a non-empty string"
                            )
        return issues
