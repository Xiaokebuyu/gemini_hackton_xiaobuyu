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

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def get_by_area(self, area_id: str) -> list[dict[str, Any]]:
        """Return characters whose area_id or current_area matches."""
        normalized = area_id.strip()
        results: list[dict[str, Any]] = []
        for item in self._items.values():
            for field_name in ("area_id", "current_area"):
                if str(item.get(field_name, "")).strip() == normalized:
                    results.append(dict(item))
                    break
        return results

    def get_merchants(self) -> list[dict[str, Any]]:
        """Return characters that have shop or shop_inventory."""
        return [
            dict(item) for item in self._items.values()
            if isinstance(item.get("shop"), Mapping)
            or isinstance(item.get("shop_inventory"), Mapping)
        ]

    def get_by_faction(self, faction_id: str) -> list[dict[str, Any]]:
        """Return characters matching the given faction/faction_id."""
        normalized = faction_id.strip()
        return [
            dict(item) for item in self._items.values()
            if str(item.get("faction", "")).strip() == normalized
            or str(item.get("faction_id", "")).strip() == normalized
        ]

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        issues: list[str] = []
        for item_id, item in self._items.items():
            if not item.get("id"):
                issues.append(f"character '{item_id}' missing id")

            # -- Consumer fields --
            if "name" in item and self._coerce_non_empty_string(item.get("name")) is None:
                issues.append(f"character '{item_id}' has invalid name")

            for field_name in ("area_id", "current_area"):
                if field_name in item and self._coerce_non_empty_string(item.get(field_name)) is None:
                    issues.append(f"character '{item_id}' has invalid {field_name}")

            if "tags" in item and not isinstance(item.get("tags"), list):
                issues.append(f"character '{item_id}' has invalid tags")

            for field_name in ("character_class", "class_id"):
                if field_name in item and self._coerce_non_empty_string(item.get(field_name)) is None:
                    issues.append(f"character '{item_id}' has invalid {field_name}")

            for field_name in ("faction", "faction_id"):
                if field_name in item and self._coerce_non_empty_string(item.get(field_name)) is None:
                    issues.append(f"character '{item_id}' has invalid {field_name}")

            # -- Inventory --
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

            # -- Simple shop format --
            shop = item.get("shop")
            if shop is not None:
                if not isinstance(shop, Mapping):
                    issues.append(f"character '{item_id}' has invalid shop")
                else:
                    shop_inv_list = shop.get("inventory")
                    if shop_inv_list is not None:
                        if not isinstance(shop_inv_list, list):
                            issues.append(
                                f"character '{item_id}' has invalid shop inventory"
                            )
                        else:
                            for index, entry in enumerate(shop_inv_list):
                                if not isinstance(entry, Mapping):
                                    issues.append(
                                        f"character '{item_id}' shop.inventory[{index}] must be a mapping"
                                    )
                                    continue
                                if self._coerce_non_empty_string(entry.get("item_id")) is None:
                                    issues.append(
                                        f"character '{item_id}' shop.inventory[{index}] missing item_id"
                                    )
                                if (
                                    "price" in entry
                                    and self._coerce_non_negative_int(entry.get("price")) is None
                                ):
                                    issues.append(
                                        f"character '{item_id}' shop.inventory[{index}] has invalid price"
                                    )

            # -- Economy shop_inventory format --
            shop_inventory = item.get("shop_inventory")
            if shop_inventory is not None:
                if not isinstance(shop_inventory, Mapping):
                    issues.append(f"character '{item_id}' has invalid shop_inventory")
                else:
                    self._validate_shop_inventory(item_id, shop_inventory, issues)
        return issues

    def _validate_shop_inventory(
        self, item_id: str, shop_inv: Mapping[str, Any], issues: list[str],
    ) -> None:
        if "sell_markup" in shop_inv:
            markup = self._coerce_float(shop_inv.get("sell_markup"))
            if markup is None or markup < 0:
                issues.append(f"character '{item_id}' shop_inventory has invalid sell_markup")
            elif markup > 2.0:
                issues.append(
                    f"character '{item_id}' balance warning: sell_markup {markup} exceeds 2.0"
                )

        if "buy_rate" in shop_inv:
            rate = self._coerce_float(shop_inv.get("buy_rate"))
            if rate is None or rate < 0.0 or rate > 1.0:
                issues.append(f"character '{item_id}' shop_inventory has invalid buy_rate")

        if "rotating_slots" in shop_inv:
            if self._coerce_non_negative_int(shop_inv.get("rotating_slots")) is None:
                issues.append(f"character '{item_id}' shop_inventory has invalid rotating_slots")

        for pool_name in ("base_pool", "rotating_pool"):
            pool = shop_inv.get(pool_name)
            if pool is None:
                continue
            if not isinstance(pool, list):
                issues.append(f"character '{item_id}' shop_inventory has invalid {pool_name}")
                continue
            for index, entry in enumerate(pool):
                if not isinstance(entry, Mapping):
                    issues.append(
                        f"character '{item_id}' shop_inventory.{pool_name}[{index}] must be a mapping"
                    )
                    continue
                if self._coerce_non_empty_string(entry.get("item_id")) is None:
                    issues.append(
                        f"character '{item_id}' shop_inventory.{pool_name}[{index}] missing item_id"
                    )

        refresh_on = shop_inv.get("refresh_on")
        if refresh_on is not None:
            if isinstance(refresh_on, str):
                if self._coerce_non_empty_string(refresh_on) is None:
                    issues.append(f"character '{item_id}' shop_inventory has invalid refresh_on")
            elif isinstance(refresh_on, list):
                for index, entry in enumerate(refresh_on):
                    if self._coerce_non_empty_string(entry) is None:
                        issues.append(
                            f"character '{item_id}' shop_inventory.refresh_on[{index}] must be a non-empty string"
                        )
            else:
                issues.append(f"character '{item_id}' shop_inventory has invalid refresh_on")
