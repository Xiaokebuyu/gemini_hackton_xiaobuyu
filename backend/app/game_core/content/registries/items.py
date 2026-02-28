"""ItemRegistry implementation."""

from __future__ import annotations

from typing import Any

from app.game_core.content.base import ContentRegistry


class ItemRegistry(ContentRegistry):
    """Registry for item templates."""

    def __init__(self) -> None:
        super().__init__("items")
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
                issues.append(f"item '{item_id}' missing id")

            if "price" in item and self._coerce_non_negative_int(item.get("price")) is None:
                issues.append(f"item '{item_id}' has invalid price")

            for field_name in ("heal_amount", "heal", "restore_hp"):
                if field_name not in item:
                    continue
                if self._coerce_non_negative_int(item.get(field_name)) is None:
                    issues.append(f"item '{item_id}' has invalid {field_name}")

            if "slot" in item and self._coerce_non_empty_string(item.get("slot")) is None:
                issues.append(f"item '{item_id}' has invalid slot")

            if "tags" in item and not isinstance(item.get("tags"), list):
                issues.append(f"item '{item_id}' has invalid tags")
        return issues
