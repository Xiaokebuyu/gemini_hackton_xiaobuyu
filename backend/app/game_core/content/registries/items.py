"""ItemRegistry implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from app.game_core.content.base import ContentRegistry
from app.game_core.content.registries.shared_types import Effect

_ITEM_TYPES = frozenset({
    "weapon", "armor", "shield", "potion", "scroll",
    "wand", "ring", "amulet", "consumable", "misc", "accessory",
})

_RARITIES = frozenset({
    "common", "uncommon", "rare", "very_rare", "legendary",
})

_ARMOR_SUBTYPES = frozenset({"light", "medium", "heavy", "shield"})

# dex_cap and stealth_disadvantage inferred from armor_type
_ARMOR_DEX_CAP: dict[str, int | None] = {
    "light": None,
    "medium": 2,
    "heavy": 0,
    "shield": None,
}
_ARMOR_STEALTH_DISADVANTAGE: dict[str, bool] = {
    "light": False,
    "medium": False,
    "heavy": True,
    "shield": False,
}


@dataclass(slots=True)
class WeaponData:
    """Typed weapon attributes (type == 'weapon')."""

    damage_dice: str                                # "1d6" / "2d6" / "1d8"
    damage_type: str                                # slashing / piercing / bludgeoning
    properties: list[str] = field(default_factory=list)  # FINESSE / LIGHT / TWO_HANDED …
    range: int = 1                                  # 1=近战, 2=长柄, 3=短程, 5=远程
    proficiency: str = ""                           # simple / martial
    slot: str = "main_hand"                         # main_hand / off_hand / ranged / two_handed
    versatile_dice: str = ""                        # VERSATILE 双手持握骰


@dataclass(slots=True)
class ArmorData:
    """Typed armor / shield attributes (type == 'armor')."""

    armor_type: str                                 # light / medium / heavy / shield
    base_ac: int                                    # 绝对基础 AC（轻/中/重甲，如 12/13/18）；盾牌为加成值（如 2）
    dex_cap: int | None = None                      # None=无上限, 2=medium, 0=heavy
    str_requirement: int | None = None              # 重甲力量需求
    stealth_disadvantage: bool = False
    proficiency: str = ""                           # light / medium / heavy / shield


@dataclass(slots=True)
class AccessoryData:
    """Typed accessory attributes (type == 'accessory')."""

    slot: str = ""    # head / neck / cloak / hands / finger / feet
    effects: list[Effect] = field(default_factory=list)


@dataclass(slots=True)
class ConsumableData:
    """Typed consumable attributes."""

    trigger: str = "on_use"                         # on_use / on_hit / on_receive
    charges: int = 1
    effect: Effect | None = None


@dataclass(slots=True)
class ItemTemplate:
    """Typed item definition."""

    id: str
    name: str = ""
    description: str = ""
    type: str = ""
    rarity: str = ""
    base_price: int | None = None
    heal_amount: int | None = None
    slot: str = ""
    tags: list[str] = field(default_factory=list)
    weight: float | None = None
    requires_attunement: bool = False
    # === typed sub-structs ===
    weapon_data: WeaponData | None = None
    armor_data: ArmorData | None = None
    consumable_data: ConsumableData | None = None
    accessory_data: AccessoryData | None = None


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

            # Merge price alias into base_price; merge heal/restore_hp aliases into heal_amount
            _base_price: int | None = self._coerce_non_negative_int(raw.get("base_price"))
            if _base_price is None:
                _base_price = self._coerce_non_negative_int(raw.get("price"))
            _heal_amount: int | None = None
            for fn in ("heal_amount", "heal", "restore_hp"):
                val = self._coerce_non_negative_int(raw.get(fn))
                if val is not None:
                    _heal_amount = val
                    break

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

            # Weapon flat fields — validated before building WeaponData
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

            item_type = str(raw.get("type") or "").strip().lower()

            # -- Build typed sub-structs --
            weapon_data = self._build_weapon_data(raw)
            armor_data = self._build_armor_data(raw, item_type)
            consumable_data = self._build_consumable_data(raw, _heal_amount)
            accessory_data = self._build_accessory_data(raw, item_type)

            self._items[iid] = ItemTemplate(
                id=str(raw.get("id", iid)),
                name=str(raw.get("name") or ""),
                description=str(raw.get("description") or ""),
                type=item_type,
                rarity=str(raw.get("rarity") or "").strip().lower(),
                base_price=_base_price,
                heal_amount=_heal_amount,
                slot=str(raw.get("slot") or "").strip(),
                tags=tags,
                weight=self._coerce_float(raw.get("weight")),
                requires_attunement=requires_attunement,
                weapon_data=weapon_data,
                armor_data=armor_data,
                consumable_data=consumable_data,
                accessory_data=accessory_data,
            )

    # ------------------------------------------------------------------
    # Sub-struct builders (private)
    # ------------------------------------------------------------------

    def _build_weapon_data(self, raw: dict[str, Any]) -> WeaponData | None:
        """Build WeaponData when damage_dice is present and valid."""
        damage_dice = self._coerce_non_empty_string(raw.get("damage_dice"))
        if damage_dice is None:
            return None
        damage_type = str(raw.get("damage_type") or "").strip()
        properties: list[str] = []
        raw_props = raw.get("weapon_properties") or raw.get("properties")
        if isinstance(raw_props, list):
            properties = [str(p) for p in raw_props if isinstance(p, str) and str(p).strip()]
        weapon_slot = self._coerce_non_empty_string(raw.get("weapon_slot")) or "main_hand"
        return WeaponData(
            damage_dice=damage_dice,
            damage_type=damage_type,
            properties=properties,
            range=int(raw.get("range") or 1),
            proficiency=str(raw.get("weapon_proficiency") or "").strip(),
            slot=weapon_slot,
            versatile_dice=str(raw.get("versatile_dice") or "").strip(),
        )

    def _build_armor_data(self, raw: dict[str, Any], item_type: str) -> ArmorData | None:
        """Build ArmorData when item_type == 'armor'."""
        if item_type != "armor":
            return None
        ac_bonus = self._coerce_non_negative_int(raw.get("ac_bonus")) or 0
        subtype = self._coerce_non_empty_string(raw.get("subtype"))
        armor_type = subtype if subtype in _ARMOR_SUBTYPES else "light"
        dex_cap = _ARMOR_DEX_CAP.get(armor_type)
        stealth_disadvantage = _ARMOR_STEALTH_DISADVANTAGE.get(armor_type, False)
        str_req = self._coerce_non_negative_int(raw.get("str_requirement"))
        proficiency = armor_type if armor_type != "shield" else "shield"
        # 盾牌存加成值；其他护甲存绝对基础 AC（对齐设计规范 §6.3 公式 base_ac + DEX_mod）
        base_ac = ac_bonus if armor_type == "shield" else 10 + ac_bonus
        return ArmorData(
            armor_type=armor_type,
            base_ac=base_ac,
            dex_cap=dex_cap,
            str_requirement=str_req,
            stealth_disadvantage=stealth_disadvantage,
            proficiency=proficiency,
        )

    @staticmethod
    def _build_consumable_data(raw: dict[str, Any], heal_amount: int | None) -> ConsumableData | None:
        """Build ConsumableData from raw item dict."""
        # 优先读 consumable_data 子 Mapping
        cd_raw = raw.get("consumable_data")
        if isinstance(cd_raw, Mapping):
            trigger = str(cd_raw.get("trigger", "on_use")).strip() or "on_use"
            charges = 1
            raw_charges = cd_raw.get("charges")
            if raw_charges is not None:
                try:
                    charges = max(1, int(raw_charges))
                except (ValueError, TypeError):
                    pass
            effect: Effect | None = None
            eff_raw = cd_raw.get("effect")
            if isinstance(eff_raw, Mapping):
                effect = Effect(
                    type=str(eff_raw.get("type", "")).strip(),
                    params=dict(eff_raw.get("params", {})) if isinstance(eff_raw.get("params"), Mapping) else {},
                    target=str(eff_raw.get("target", "self")).strip(),
                    tags=[str(t) for t in eff_raw.get("tags", []) if isinstance(t, str)] if isinstance(eff_raw.get("tags"), list) else [],
                )
            return ConsumableData(trigger=trigger, charges=charges, effect=effect)

        # 兼容旧格式：heal_amount → heal Effect
        if heal_amount is not None and heal_amount > 0:
            return ConsumableData(
                trigger="on_use",
                charges=1,
                effect=Effect(type="heal", params={"amount": heal_amount}, target="self"),
            )

        # 顶层 effect 字段
        eff_raw = raw.get("effect")
        if isinstance(eff_raw, Mapping):
            effect = Effect(
                type=str(eff_raw.get("type", "")).strip(),
                params=dict(eff_raw.get("params", {})) if isinstance(eff_raw.get("params"), Mapping) else {},
                target=str(eff_raw.get("target", "self")).strip(),
                tags=[str(t) for t in eff_raw.get("tags", []) if isinstance(t, str)] if isinstance(eff_raw.get("tags"), list) else [],
            )
            return ConsumableData(trigger="on_use", charges=1, effect=effect)

        return None

    @staticmethod
    def _build_accessory_data(raw: dict[str, Any], item_type: str) -> AccessoryData | None:
        """Build AccessoryData when item_type == 'accessory'."""
        if item_type != "accessory":
            return None
        slot = str(raw.get("accessory_slot") or raw.get("slot") or "").strip()
        effects: list[Effect] = []
        raw_effects = raw.get("effects", [])
        if isinstance(raw_effects, list):
            for eff_raw in raw_effects:
                if isinstance(eff_raw, Mapping):
                    effects.append(Effect(
                        type=str(eff_raw.get("type", "")).strip(),
                        params=(
                            dict(eff_raw.get("params", {}))
                            if isinstance(eff_raw.get("params"), Mapping) else {}
                        ),
                        target=str(eff_raw.get("target", "self")).strip(),
                        tags=(
                            [str(t) for t in eff_raw.get("tags", []) if isinstance(t, str)]
                            if isinstance(eff_raw.get("tags"), list) else []
                        ),
                    ))
        if not slot and not effects:
            return None
        return AccessoryData(slot=slot, effects=effects)

    # ------------------------------------------------------------------
    # Public accessors
    # ------------------------------------------------------------------

    def get(self, content_id: str) -> ItemTemplate | None:
        return self._items.get(content_id)

    def list_all(self) -> list[ItemTemplate]:
        return list(self._items.values())

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def get_equippable(self) -> list[ItemTemplate]:
        """Return items that can be equipped (have slot, weapon_data, or armor_data)."""
        return [
            item for item in self._items.values()
            if item.slot or item.weapon_data is not None or item.armor_data is not None
        ]

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

    def get_weapons(self, properties: list[str] | None = None) -> list[ItemTemplate]:
        """Return weapons, optionally filtered by required properties."""
        weapons = [item for item in self._items.values() if item.weapon_data is not None]
        if not properties:
            return weapons
        return [
            item for item in weapons
            if all(p in item.weapon_data.properties for p in properties)  # type: ignore[union-attr]
        ]

    def get_armors(self, armor_type: str | None = None) -> list[ItemTemplate]:
        """Return armor items, optionally filtered by armor_type."""
        armors = [item for item in self._items.values() if item.armor_data is not None]
        if not armor_type:
            return armors
        normalized = armor_type.strip().lower()
        return [item for item in armors if item.armor_data.armor_type == normalized]  # type: ignore[union-attr]

    def get_consumables(self) -> list[ItemTemplate]:
        """Return items with ConsumableData."""
        return [item for item in self._items.values() if item.consumable_data is not None]

    def get_by_price_range(self, min_gp: int, max_gp: int) -> list[ItemTemplate]:
        """Return items whose base_price is within [min_gp, max_gp]."""
        return [
            item for item in self._items.values()
            if item.base_price is not None and min_gp <= item.base_price <= max_gp
        ]

    def get_by_tags(self, tags: list[str]) -> list[ItemTemplate]:
        """Return items that carry all of the specified tags."""
        if not tags:
            return list(self._items.values())
        return [
            item for item in self._items.values()
            if all(t in item.tags for t in tags)
        ]

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        return list(self._load_issues)
