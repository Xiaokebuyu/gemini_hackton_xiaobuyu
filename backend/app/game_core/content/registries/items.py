"""ItemRegistry implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.game_core.content.base import ContentRegistry

_ITEM_TYPES = frozenset({
    "weapon", "armor", "shield", "potion", "scroll",
    "wand", "ring", "amulet", "consumable", "misc",
})

_RARITIES = frozenset({
    "common", "uncommon", "rare", "very_rare", "legendary",
})


@dataclass(slots=True)
class ItemTemplate:
    """Typed item definition."""

    id: str
    name: str = ""
    type: str = ""
    rarity: str = ""
    base_price: int | None = None
    price: int | None = None
    heal_amount: int | None = None
    heal: int | None = None
    restore_hp: int | None = None
    slot: str = ""
    tags: list[str] = field(default_factory=list)
    weight: float | None = None
    requires_attunement: bool = False
    damage_dice: str = ""
    damage_type: str = ""
    ac_bonus: int | None = None


class ItemRegistry(ContentRegistry):
    """Registry for item templates."""

    def __init__(self) -> None:
        super().__init__("items")
        self._items: dict[str, ItemTemplate] = {}
        self._load_issues: list[str] = []

    def load(self, data: dict[str, Any]) -> None:
        self._items = {}
        self._load_issues = []
        coerced = self._coerce_dict_mapping(data)
        for iid, raw in coerced.items():
            # -- Format validation → _load_issues --
            if not raw.get("id"):
                self._load_issues.append(f"item '{iid}' missing id")

            raw_name = raw.get("name")
            if raw_name is not None and self._coerce_non_empty_string(raw_name) is None:
                self._load_issues.append(f"item '{iid}' has invalid name")

            for fn in ("price", "base_price"):
                if fn in raw and self._coerce_non_negative_int(raw.get(fn)) is None:
                    self._load_issues.append(f"item '{iid}' has invalid {fn}")

            for fn in ("heal_amount", "heal", "restore_hp"):
                if fn in raw and self._coerce_non_negative_int(raw.get(fn)) is None:
                    self._load_issues.append(f"item '{iid}' has invalid {fn}")

            raw_slot = raw.get("slot")
            if raw_slot is not None and self._coerce_non_empty_string(raw_slot) is None:
                self._load_issues.append(f"item '{iid}' has invalid slot")

            raw_tags = raw.get("tags")
            if raw_tags is not None and not isinstance(raw_tags, list):
                self._load_issues.append(f"item '{iid}' has invalid tags")

            if "type" in raw:
                it = self._coerce_non_empty_string(raw.get("type"))
                if it is None or it.lower() not in _ITEM_TYPES:
                    self._load_issues.append(f"item '{iid}' has invalid type")

            if "rarity" in raw:
                r = self._coerce_non_empty_string(raw.get("rarity"))
                if r is None or r.lower() not in _RARITIES:
                    self._load_issues.append(f"item '{iid}' has invalid rarity")

            for fn in ("damage_dice", "damage_type"):
                if fn in raw and self._coerce_non_empty_string(raw.get(fn)) is None:
                    self._load_issues.append(f"item '{iid}' has invalid {fn}")

            if "ac_bonus" in raw and self._coerce_non_negative_int(raw.get("ac_bonus")) is None:
                self._load_issues.append(f"item '{iid}' has invalid ac_bonus")

            if "weight" in raw:
                w = self._coerce_float(raw.get("weight"))
                if w is None or w < 0:
                    self._load_issues.append(f"item '{iid}' has invalid weight")

            requires_attunement = False
            if "requires_attunement" in raw:
                if self._is_bool_like(raw.get("requires_attunement")):
                    requires_attunement = bool(raw["requires_attunement"])
                else:
                    self._load_issues.append(f"item '{iid}' has invalid requires_attunement")

            tags: list[str] = []
            if isinstance(raw_tags, list):
                tags = [str(t) for t in raw_tags if isinstance(t, str) and str(t).strip()]

            self._items[iid] = ItemTemplate(
                id=str(raw.get("id", iid)),
                name=str(raw.get("name") or ""),
                type=str(raw.get("type") or "").strip().lower(),
                rarity=str(raw.get("rarity") or "").strip().lower(),
                base_price=self._coerce_non_negative_int(raw.get("base_price")),
                price=self._coerce_non_negative_int(raw.get("price")),
                heal_amount=self._coerce_non_negative_int(raw.get("heal_amount")),
                heal=self._coerce_non_negative_int(raw.get("heal")),
                restore_hp=self._coerce_non_negative_int(raw.get("restore_hp")),
                slot=str(raw.get("slot") or "").strip(),
                tags=tags,
                weight=self._coerce_float(raw.get("weight")),
                requires_attunement=requires_attunement,
                damage_dice=str(raw.get("damage_dice") or "").strip(),
                damage_type=str(raw.get("damage_type") or "").strip(),
                ac_bonus=self._coerce_non_negative_int(raw.get("ac_bonus")),
            )

    def get(self, content_id: str) -> ItemTemplate | None:
        return self._items.get(content_id)

    def list_all(self) -> list[ItemTemplate]:
        return list(self._items.values())

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def get_equippable(self) -> list[ItemTemplate]:
        """Return items that have an equipment slot."""
        return [item for item in self._items.values() if item.slot]

    def get_by_type(self, item_type: str) -> list[ItemTemplate]:
        """Return items matching the given type."""
        normalized = item_type.strip().lower()
        return [
            item for item in self._items.values()
            if item.type == normalized
        ]

    def get_by_rarity(self, rarity: str) -> list[ItemTemplate]:
        """Return items matching the given rarity."""
        normalized = rarity.strip().lower()
        return [
            item for item in self._items.values()
            if item.rarity == normalized
        ]

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        return list(self._load_issues)
