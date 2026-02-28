"""FactionRegistry implementation."""

from __future__ import annotations

from typing import Any

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

    def validate(self) -> list[str]:
        issues: list[str] = []
        for item_id, item in self._items.items():
            if not item.get("id"):
                issues.append(f"faction '{item_id}' missing id")
            if "tags" in item and not isinstance(item.get("tags"), list):
                issues.append(f"faction '{item_id}' has invalid tags")
        return issues
