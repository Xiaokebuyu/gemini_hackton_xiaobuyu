"""CharacterRegistry implementation."""

from __future__ import annotations

from typing import Any, Mapping

from app.game_core.content.base import ContentRegistry


class CharacterRegistry(ContentRegistry):
    """Registry for NPC templates."""

    def __init__(self) -> None:
        super().__init__("characters")
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
                issues.append(f"character '{item_id}' missing id")
            for field_name in ("area_id", "current_area"):
                if field_name not in item:
                    continue
                if self._coerce_non_empty_string(item.get(field_name)) is None:
                    issues.append(
                        f"character '{item_id}' has invalid {field_name}"
                    )

            inventory = item.get("inventory")
            if inventory is not None:
                if not isinstance(inventory, list):
                    issues.append(f"character '{item_id}' has invalid inventory")
                else:
                    for index, entry in enumerate(inventory):
                        if not isinstance(entry, Mapping):
                            issues.append(
                                f"character '{item_id}' inventory[{index}] must be a mapping"
                            )
                            continue
                        if self._coerce_non_empty_string(entry.get("item_id")) is None:
                            issues.append(
                                f"character '{item_id}' inventory[{index}] missing item_id"
                            )
                        if (
                            "count" in entry
                            and self._coerce_non_negative_int(entry.get("count")) is None
                        ):
                            issues.append(
                                f"character '{item_id}' inventory[{index}] has invalid count"
                            )

            shop = item.get("shop")
            if shop is not None:
                if not isinstance(shop, Mapping):
                    issues.append(f"character '{item_id}' has invalid shop")
                else:
                    shop_inventory = shop.get("inventory")
                    if shop_inventory is not None:
                        if not isinstance(shop_inventory, list):
                            issues.append(
                                f"character '{item_id}' has invalid shop inventory"
                            )
                        else:
                            for index, entry in enumerate(shop_inventory):
                                if not isinstance(entry, Mapping):
                                    issues.append(
                                        "character "
                                        f"'{item_id}' shop.inventory[{index}] must be a mapping"
                                    )
                                    continue
                                if (
                                    self._coerce_non_empty_string(entry.get("item_id"))
                                    is None
                                ):
                                    issues.append(
                                        "character "
                                        f"'{item_id}' shop.inventory[{index}] missing item_id"
                                    )
                                if (
                                    "price" in entry
                                    and self._coerce_non_negative_int(entry.get("price"))
                                    is None
                                ):
                                    issues.append(
                                        "character "
                                        f"'{item_id}' shop.inventory[{index}] has invalid price"
                                    )
        return issues
