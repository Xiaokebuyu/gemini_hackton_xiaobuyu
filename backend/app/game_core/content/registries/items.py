"""ItemRegistry implementation."""

from __future__ import annotations

from typing import Any

from app.game_core.content.base import ContentRegistry

_ITEM_TYPES = frozenset({
    "weapon", "armor", "shield", "potion", "scroll",
    "wand", "ring", "amulet", "consumable", "misc",
})

_RARITIES = frozenset({
    "common", "uncommon", "rare", "very_rare", "legendary",
})


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

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def get_equippable(self) -> list[dict[str, Any]]:
        """Return items that have an equipment slot."""
        return [dict(item) for item in self._items.values() if item.get("slot")]

    def get_by_type(self, item_type: str) -> list[dict[str, Any]]:
        """Return items matching the given type."""
        normalized = item_type.strip().lower()
        return [
            dict(item)
            for item in self._items.values()
            if str(item.get("type", "")).strip().lower() == normalized
        ]

    def get_by_rarity(self, rarity: str) -> list[dict[str, Any]]:
        """Return items matching the given rarity."""
        normalized = rarity.strip().lower()
        return [
            dict(item)
            for item in self._items.values()
            if str(item.get("rarity", "")).strip().lower() == normalized
        ]

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        issues: list[str] = []
        for item_id, item in self._items.items():
            if not item.get("id"):
                issues.append(f"item '{item_id}' missing id")

            # -- Consumer fields (economy.py, encounter.py, combat.py) --
            if "name" in item and self._coerce_non_empty_string(item.get("name")) is None:
                issues.append(f"item '{item_id}' has invalid name")

            for field_name in ("price", "base_price"):
                if field_name in item and self._coerce_non_negative_int(item.get(field_name)) is None:
                    issues.append(f"item '{item_id}' has invalid {field_name}")

            for field_name in ("heal_amount", "heal", "restore_hp"):
                if field_name not in item:
                    continue
                if self._coerce_non_negative_int(item.get(field_name)) is None:
                    issues.append(f"item '{item_id}' has invalid {field_name}")

            if "slot" in item and self._coerce_non_empty_string(item.get("slot")) is None:
                issues.append(f"item '{item_id}' has invalid slot")

            if "tags" in item and not isinstance(item.get("tags"), list):
                issues.append(f"item '{item_id}' has invalid tags")

            # -- Game mechanic fields --
            if "type" in item:
                it = self._coerce_non_empty_string(item.get("type"))
                if it is None or it.lower() not in _ITEM_TYPES:
                    issues.append(f"item '{item_id}' has invalid type")

            if "rarity" in item:
                r = self._coerce_non_empty_string(item.get("rarity"))
                if r is None or r.lower() not in _RARITIES:
                    issues.append(f"item '{item_id}' has invalid rarity")

            for field_name in ("damage_dice", "damage_type"):
                if field_name in item and self._coerce_non_empty_string(item.get(field_name)) is None:
                    issues.append(f"item '{item_id}' has invalid {field_name}")

            if "ac_bonus" in item and self._coerce_non_negative_int(item.get("ac_bonus")) is None:
                issues.append(f"item '{item_id}' has invalid ac_bonus")

            if "weight" in item:
                w = self._coerce_float(item.get("weight"))
                if w is None or w < 0:
                    issues.append(f"item '{item_id}' has invalid weight")

            if "requires_attunement" in item and not self._is_bool_like(item.get("requires_attunement")):
                issues.append(f"item '{item_id}' has invalid requires_attunement")
        return issues
