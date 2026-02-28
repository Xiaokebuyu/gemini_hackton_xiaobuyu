"""PlayerSlice implementation."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, ClassVar, Mapping
from uuid import uuid4

from app.game_core.state.base import StateSlice
from app.game_core.state.delta import StateChange


EQUIPMENT_SLOTS = [
    "head",
    "chest",
    "gloves",
    "boots",
    "cloak",
    "amulet",
    "ring_l",
    "ring_r",
    "main_hand",
    "off_hand",
    "ranged",
    "ammo",
    "belt",
]

SKILL_TO_STAT = {
    "athletics": "str",
    "acrobatics": "dex",
    "sleight_of_hand": "dex",
    "stealth": "dex",
    "arcana": "int",
    "history": "int",
    "investigation": "int",
    "nature": "int",
    "religion": "int",
    "animal_handling": "wis",
    "insight": "wis",
    "medicine": "wis",
    "perception": "wis",
    "survival": "wis",
    "deception": "cha",
    "intimidation": "cha",
    "performance": "cha",
    "persuasion": "cha",
}


@dataclass(slots=True)
class ItemStack:
    item_id: str
    count: int = 1
    tags: list[str] = field(default_factory=list)

    def snapshot(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "count": self.count,
            "tags": list(self.tags),
        }


class PlayerSlice(StateSlice):
    """Player runtime state.

    This is intentionally a narrow first pass: core identity, economy, location,
    inventory, and equipment. Spell/effect systems can extend this slice later
    without changing its ownership boundary.
    """

    def __init__(self) -> None:
        super().__init__("player")
        self.character_id = ""
        self.character_name = ""
        self.level = 1
        self.xp = 0
        self.hp = 10
        self.max_hp = 10
        self.ac = 10
        self.stats = {
            "str": 10,
            "dex": 10,
            "con": 10,
            "int": 10,
            "wis": 10,
            "cha": 10,
        }
        self.proficiency_bonus = 2
        self.character_class = ""
        self.subclass: str | None = None
        self.class_features: list[str] = []
        self.gold = 0
        self.current_area = ""
        self.current_location: str | None = None
        self.inventory: list[ItemStack] = []
        self.equipment: dict[str, dict[str, Any] | None] = {
            slot: None for slot in EQUIPMENT_SLOTS
        }
        self.guild_rank = "porcelain"
        self.guild_reputation = 0
        self.spell_slots: dict[int, dict[str, int]] = {}
        self.known_spells: list[str] = []
        self.prepared_spells: list[str] = []
        self.concentration: dict[str, Any] | None = None
        self.active_effects: list[dict[str, Any]] = []
        self.class_resources: dict[str, dict[str, Any]] = {}
        self.save_proficiencies: list[str] = []

    def restore(self, payload: Mapping[str, Any]) -> None:
        self.character_id = str(payload.get("character_id", ""))
        self.character_name = str(payload.get("character_name", ""))
        self.level = int(payload.get("level", 1))
        self.xp = int(payload.get("xp", 0))
        self.hp = int(payload.get("hp", 10))
        self.max_hp = int(payload.get("max_hp", self.hp))
        self.ac = int(payload.get("ac", 10))
        self.stats = dict(payload.get("stats", self.stats))
        self.proficiency_bonus = int(payload.get("proficiency_bonus", 2))
        self.character_class = str(payload.get("character_class", ""))
        raw_subclass = payload.get("subclass")
        self.subclass = str(raw_subclass) if raw_subclass is not None else None
        self.class_features = [
            str(feature) for feature in payload.get("class_features", [])
        ]
        self.gold = int(payload.get("gold", 0))
        self.current_area = str(payload.get("current_area", ""))
        raw_location = payload.get("current_location")
        self.current_location = str(raw_location) if raw_location is not None else None
        self.inventory = [
            self._coerce_stack(item)
            for item in payload.get("inventory", [])
        ]
        restored_equipment = {
            slot: payload.get("equipment", {}).get(slot)
            for slot in EQUIPMENT_SLOTS
        }
        self.equipment = restored_equipment
        self.guild_rank = str(payload.get("guild_rank", "porcelain"))
        self.guild_reputation = int(payload.get("guild_reputation", 0))
        raw_spell_slots = payload.get("spell_slots", {})
        self.spell_slots = {}
        if isinstance(raw_spell_slots, Mapping):
            for key, value in raw_spell_slots.items():
                if isinstance(value, Mapping):
                    self.spell_slots[int(key)] = {
                        "current": int(value.get("current", 0)),
                        "max": int(value.get("max", 0)),
                    }
        self.known_spells = [str(spell_id) for spell_id in payload.get("known_spells", [])]
        self.prepared_spells = [
            str(spell_id) for spell_id in payload.get("prepared_spells", [])
        ]
        raw_concentration = payload.get("concentration")
        self.concentration = (
            dict(raw_concentration) if isinstance(raw_concentration, Mapping) else None
        )
        self.active_effects = [
            dict(effect) for effect in payload.get("active_effects", [])
            if isinstance(effect, Mapping)
        ]
        raw_resources = payload.get("class_resources", {})
        self.class_resources = {}
        if isinstance(raw_resources, Mapping):
            for key, value in raw_resources.items():
                if isinstance(value, Mapping):
                    self.class_resources[str(key)] = {
                        "current": int(value.get("current", 0)),
                        "max": int(value.get("max", 0)),
                        "recovery": str(value.get("recovery", "long_rest")),
                    }
        self.save_proficiencies = [
            str(s).strip()
            for s in payload.get("save_proficiencies", [])
            if str(s).strip()
        ]
        self.clear_dirty()

    def serialize(self) -> dict[str, Any]:
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        return {
            "character_id": self.character_id,
            "character_name": self.character_name,
            "level": self.level,
            "xp": self.xp,
            "hp": self.hp,
            "max_hp": self.max_hp,
            "ac": self.ac,
            "stats": dict(self.stats),
            "proficiency_bonus": self.proficiency_bonus,
            "character_class": self.character_class,
            "subclass": self.subclass,
            "class_features": list(self.class_features),
            "gold": self.gold,
            "current_area": self.current_area,
            "current_location": self.current_location,
            "inventory": [item.snapshot() for item in self.inventory],
            "equipment": deepcopy(self.equipment),
            "guild_rank": self.guild_rank,
            "guild_reputation": self.guild_reputation,
            "spell_slots": deepcopy(self.spell_slots),
            "known_spells": list(self.known_spells),
            "prepared_spells": list(self.prepared_spells),
            "concentration": deepcopy(self.concentration),
            "active_effects": deepcopy(self.active_effects),
            "class_resources": deepcopy(self.class_resources),
            "save_proficiencies": list(self.save_proficiencies),
        }

    def get_modifier(self, stat: str) -> int:
        if stat not in self.stats:
            raise KeyError(f"unknown stat: {stat}")
        return (int(self.stats[stat]) - 10) // 2

    def get_skill_bonus(self, skill: str) -> int:
        stat = SKILL_TO_STAT.get(skill, "int")
        return self.get_modifier(stat) + self.proficiency_bonus

    def get_item_count(self, item_id: str) -> int:
        for stack in self.inventory:
            if stack.item_id == item_id:
                return stack.count
        return 0

    def get_inventory_by_tag(self, tag: str) -> list[ItemStack]:
        return [
            ItemStack(stack.item_id, stack.count, list(stack.tags))
            for stack in self.inventory
            if tag in stack.tags
        ]

    def get_equipped(self, slot: str) -> dict[str, Any] | None:
        if slot not in self.equipment:
            raise KeyError(f"unknown equipment slot: {slot}")
        item = self.equipment[slot]
        return dict(item) if isinstance(item, dict) else item

    def get_spell_slots(self, level: int) -> dict[str, int] | None:
        slot_state = self.spell_slots.get(level)
        return dict(slot_state) if isinstance(slot_state, dict) else None

    def has_spell_slot(self, level: int) -> bool:
        slot_state = self.spell_slots.get(level)
        if not slot_state:
            return False
        return int(slot_state.get("current", 0)) > 0

    def get_lowest_available_slot(self, min_level: int) -> int | None:
        for level in sorted(self.spell_slots):
            if level >= min_level and self.has_spell_slot(level):
                return level
        return None

    def is_spell_prepared(self, spell_id: str) -> bool:
        return spell_id in self.prepared_spells

    def get_active_effects(self) -> list[dict[str, Any]]:
        return [dict(effect) for effect in self.active_effects]

    def has_effect(self, effect_id: str) -> bool:
        return any(effect.get("effect_id") == effect_id for effect in self.active_effects)

    def get_effect_modifiers(self) -> dict[str, int]:
        aggregated: dict[str, int] = {}
        for effect in self.active_effects:
            modifiers = effect.get("modifiers", {})
            if not isinstance(modifiers, Mapping):
                continue
            for key, value in modifiers.items():
                aggregated[str(key)] = aggregated.get(str(key), 0) + int(value)
        return aggregated

    def is_action_prevented(self) -> bool:
        return any(bool(effect.get("prevents_action")) for effect in self.active_effects)

    def get_disadvantage_checks(self) -> list[str]:
        checks: list[str] = []
        for effect in self.active_effects:
            raw_checks = effect.get("disadvantage_checks", [])
            if isinstance(raw_checks, list):
                checks.extend(str(check) for check in raw_checks)
        return sorted(set(checks))

    def get_advantage_on_attacks_against(self) -> bool:
        return any(
            bool(effect.get("advantage_on_attacks_against"))
            for effect in self.active_effects
        )

    def get_resource(self, key: str) -> dict[str, Any] | None:
        value = self.class_resources.get(key)
        return dict(value) if isinstance(value, dict) else None

    def has_resource(self, key: str, amount: int = 1) -> bool:
        resource = self.class_resources.get(key)
        if not resource:
            return False
        return int(resource.get("current", 0)) >= amount

    def modify_hp(self, delta: int) -> None:
        self.hp = max(0, min(self.max_hp, self.hp + delta))
        self._dirty = True

    def add_xp(self, amount: int) -> bool:
        if amount < 0:
            raise ValueError("amount must be >= 0")
        self.xp += amount
        leveled_up = False
        threshold = self.level * 1000
        while self.xp >= threshold:
            self.level += 1
            leveled_up = True
            threshold = self.level * 1000
        self._dirty = True
        return leveled_up

    def modify_gold(self, delta: int) -> None:
        self.gold = max(0, self.gold + delta)
        self._dirty = True

    def add_item(self, item_id: str, count: int = 1, tags: list[str] | None = None) -> None:
        if count <= 0:
            raise ValueError("count must be > 0")
        for stack in self.inventory:
            if stack.item_id == item_id:
                stack.count += count
                if tags:
                    stack.tags = sorted(set(stack.tags).union(tags))
                self._dirty = True
                return
        self.inventory.append(ItemStack(item_id=item_id, count=count, tags=tags or []))
        self._dirty = True

    def remove_item(self, item_id: str, count: int = 1) -> None:
        if count <= 0:
            raise ValueError("count must be > 0")
        for index, stack in enumerate(self.inventory):
            if stack.item_id != item_id:
                continue
            if stack.count < count:
                raise ValueError(f"not enough items: {item_id}")
            stack.count -= count
            if stack.count == 0:
                self.inventory.pop(index)
            self._dirty = True
            return
        raise ValueError(f"item not found: {item_id}")

    def equip(self, item_id: str, slot: str) -> None:
        if slot not in self.equipment:
            raise KeyError(f"unknown equipment slot: {slot}")
        if self.get_item_count(item_id) <= 0:
            raise ValueError(f"item not in inventory: {item_id}")
        self.equipment[slot] = {"item_id": item_id}
        self._dirty = True

    def unequip(self, slot: str) -> None:
        if slot not in self.equipment:
            raise KeyError(f"unknown equipment slot: {slot}")
        self.equipment[slot] = None
        self._dirty = True

    def consume_spell_slot(self, level: int) -> None:
        if not self.has_spell_slot(level):
            raise ValueError(f"no spell slot available at level {level}")
        self.spell_slots[level]["current"] -= 1
        self._dirty = True

    def restore_spell_slots(self, level: int | None = None) -> None:
        targets = [level] if level is not None else list(self.spell_slots.keys())
        for target in targets:
            slot_state = self.spell_slots.get(target)
            if slot_state is None:
                continue
            slot_state["current"] = int(slot_state.get("max", 0))
        self._dirty = True

    def set_prepared_spells(self, spell_ids: list[str]) -> None:
        self.prepared_spells = [str(spell_id) for spell_id in spell_ids]
        self._dirty = True

    def learn_spell(self, spell_id: str) -> None:
        if spell_id not in self.known_spells:
            self.known_spells.append(spell_id)
        if not self.prepared_spells:
            self.prepared_spells = list(self.known_spells)
        self._dirty = True

    def set_concentration(self, conc: dict[str, Any] | None) -> None:
        self.concentration = dict(conc) if isinstance(conc, Mapping) else None
        self._dirty = True

    def break_concentration(self) -> list[str]:
        if not self.concentration:
            return []
        effect_ids = self.concentration.get("applied_effects", [])
        removable = [str(effect_id) for effect_id in effect_ids] if isinstance(effect_ids, list) else []
        self.concentration = None
        self._dirty = True
        return removable

    def add_effect(self, effect: dict[str, Any]) -> str:
        normalized = dict(effect)
        if not normalized.get("instance_id"):
            normalized["instance_id"] = uuid4().hex
        effect_id = str(normalized.get("effect_id", ""))
        stackable = bool(normalized.get("stackable"))
        for existing in self.active_effects:
            if existing.get("effect_id") != effect_id:
                continue
            if not stackable:
                existing["remaining_duration"] = max(
                    int(existing.get("remaining_duration", -1)),
                    int(normalized.get("remaining_duration", -1)),
                )
                self._dirty = True
                return str(existing["instance_id"])
            existing["stack_count"] = min(3, int(existing.get("stack_count", 1)) + 1)
            self._dirty = True
            return str(existing.get("instance_id", normalized["instance_id"]))
        normalized.setdefault("stack_count", 1)
        self.active_effects.append(normalized)
        self._dirty = True
        return str(normalized["instance_id"])

    def remove_effect(self, instance_id: str) -> None:
        original_size = len(self.active_effects)
        self.active_effects = [
            effect
            for effect in self.active_effects
            if effect.get("instance_id") != instance_id
        ]
        if len(self.active_effects) != original_size:
            self._dirty = True

    def remove_effects_by_id(self, effect_id: str) -> None:
        original_size = len(self.active_effects)
        self.active_effects = [
            effect
            for effect in self.active_effects
            if effect.get("effect_id") != effect_id
        ]
        if len(self.active_effects) != original_size:
            self._dirty = True

    def tick_effect_durations(self) -> list[str]:
        expired: list[str] = []
        for effect in self.active_effects:
            duration = int(effect.get("remaining_duration", -1))
            if duration < 0:
                continue
            duration -= 1
            effect["remaining_duration"] = duration
            if duration <= 0:
                expired.append(str(effect.get("instance_id", "")))
        if expired:
            self.active_effects = [
                effect
                for effect in self.active_effects
                if str(effect.get("instance_id", "")) not in expired
            ]
        self._dirty = True
        return expired

    def consume_resource(self, key: str, amount: int = 1) -> None:
        if amount <= 0:
            raise ValueError("amount must be > 0")
        if not self.has_resource(key, amount):
            raise ValueError(f"insufficient resource: {key}")
        self.class_resources[key]["current"] -= amount
        self._dirty = True

    def restore_resource(self, key: str, amount: int | None = None) -> None:
        resource = self.class_resources.get(key)
        if not resource:
            return
        max_value = int(resource.get("max", 0))
        if amount is None:
            resource["current"] = max_value
        else:
            resource["current"] = min(max_value, int(resource.get("current", 0)) + amount)
        self._dirty = True

    def restore_resources_by_recovery(self, recovery_type: str) -> None:
        for key, resource in self.class_resources.items():
            if resource.get("recovery") == recovery_type:
                self.restore_resource(key)

    def validate(self) -> list[str]:
        issues: list[str] = []
        if not isinstance(self.level, int) or self.level < 1:
            issues.append("level must be >= 1")
        if not isinstance(self.max_hp, int) or self.max_hp < 1:
            issues.append("max_hp must be >= 1")
        if not isinstance(self.hp, int) or self.hp < 0 or self.hp > self.max_hp:
            issues.append("hp must be between 0 and max_hp")
        if not isinstance(self.gold, int) or self.gold < 0:
            issues.append("gold must be >= 0")

        if not isinstance(self.equipment, dict):
            issues.append("equipment must be a dict")
            return issues
        equipment_keys = set(self.equipment)
        expected_keys = set(EQUIPMENT_SLOTS)
        if equipment_keys != expected_keys:
            issues.append("equipment slots do not match EQUIPMENT_SLOTS")

        if not isinstance(self.spell_slots, dict):
            issues.append("spell_slots must be a dict")
        else:
            for level, slot_state in self.spell_slots.items():
                if not isinstance(slot_state, Mapping):
                    issues.append(f"spell_slots[{level}] must be a mapping")
                    continue
                current = int(slot_state.get("current", 0))
                max_value = int(slot_state.get("max", 0))
                if current < 0:
                    issues.append(f"spell_slots[{level}].current must be >= 0")
                if max_value < 0:
                    issues.append(f"spell_slots[{level}].max must be >= 0")
                if current > max_value:
                    issues.append(f"spell_slots[{level}].current must be <= max")

        if not isinstance(self.class_resources, dict):
            issues.append("class_resources must be a dict")
            return issues

        for key, resource in self.class_resources.items():
            if not isinstance(resource, Mapping):
                issues.append(f"class_resources[{key}] must be a mapping")
                continue
            current = int(resource.get("current", 0))
            max_value = int(resource.get("max", 0))
            if current < 0:
                issues.append(f"class_resources[{key}].current must be >= 0")
            if max_value < 0:
                issues.append(f"class_resources[{key}].max must be >= 0")
            if current > max_value:
                issues.append(f"class_resources[{key}].current must be <= max")

        return issues

    def apply_state_change(self, change: StateChange) -> None:
        if change.path == "gold":
            if change.operation == "add":
                self.modify_gold(int(change.value))
            else:
                self.gold = max(0, int(change.value))
                self._dirty = True
            return

        if change.path == "hp":
            if change.operation == "add":
                self.modify_hp(int(change.value))
            else:
                self.hp = max(0, min(self.max_hp, int(change.value)))
                self._dirty = True
            return

        if change.path == "current_area":
            self.current_area = str(change.value)
            self._dirty = True
            return

        if change.path == "current_location":
            self.current_location = (
                str(change.value) if change.value is not None else None
            )
            self._dirty = True
            return

        if change.path == "inventory":
            if not isinstance(change.value, list):
                raise ValueError("inventory change must be a list")
            self.inventory = [self._coerce_stack(item) for item in change.value]
            self._dirty = True
            return

        if "." in change.path:
            root, child = change.path.split(".", 1)
            if root == "stats":
                if change.operation == "add":
                    self.stats[child] = int(self.stats.get(child, 0)) + int(change.value)
                else:
                    self.stats[child] = int(change.value)
                self._dirty = True
                return
            if root == "spell_slots":
                level = int(child)
                slot_state = self.spell_slots.setdefault(level, {"current": 0, "max": 0})
                if isinstance(change.value, Mapping):
                    slot_state.update(
                        {
                            "current": int(change.value.get("current", slot_state["current"])),
                            "max": int(change.value.get("max", slot_state["max"])),
                        }
                    )
                elif change.operation == "add":
                    slot_state["current"] += int(change.value)
                else:
                    slot_state["current"] = int(change.value)
                self._dirty = True
                return
            if root == "class_resources":
                if isinstance(change.value, Mapping):
                    self.class_resources[child] = dict(change.value)
                self._dirty = True
                return

        if change.path == "stats" and isinstance(change.value, Mapping):
            self.stats = {str(k): int(v) for k, v in change.value.items()}
            self._dirty = True
            return

        if change.path == "class_features" and isinstance(change.value, list):
            self.class_features = [str(f) for f in change.value]
            self._dirty = True
            return

        if change.path == "equipment" and isinstance(change.value, Mapping):
            self.equipment = {str(k): v for k, v in change.value.items()}
            self._dirty = True
            return

        if change.path == "active_effects" and isinstance(change.value, list):
            self.active_effects = [
                dict(e) if isinstance(e, Mapping) else e for e in change.value
            ]
            self._dirty = True
            return

        if change.path == "concentration":
            self.concentration = (
                dict(change.value) if isinstance(change.value, Mapping) else change.value
            )
            self._dirty = True
            return

        if change.path == "known_spells" and isinstance(change.value, list):
            self.known_spells = [str(s) for s in change.value]
            self._dirty = True
            return

        if change.path == "prepared_spells" and isinstance(change.value, list):
            self.prepared_spells = [str(s) for s in change.value]
            self._dirty = True
            return

        if change.path == "save_proficiencies" and isinstance(change.value, list):
            self.save_proficiencies = [str(s) for s in change.value]
            self._dirty = True
            return

        if change.operation in {"set", "modify"} and change.path in self._SIMPLE_FIELDS:
            coerce = self._SIMPLE_FIELDS[change.path]
            setattr(self, change.path, coerce(change.value))
            self._dirty = True
            return

        raise ValueError(
            f"unsupported player state change: {change.operation} {change.path}"
        )

    _SIMPLE_FIELDS: ClassVar[dict[str, type]] = {
        "hp": int,
        "max_hp": int,
        "ac": int,
        "gold": int,
        "level": int,
        "xp": int,
        "proficiency_bonus": int,
        "character_class": str,
        "subclass": str,
        "current_area": str,
        "current_location": str,
        "guild_rank": str,
        "guild_reputation": int,
        "character_id": str,
        "character_name": str,
    }

    @staticmethod
    def _coerce_stack(item: Any) -> ItemStack:
        if isinstance(item, ItemStack):
            return ItemStack(item_id=item.item_id, count=item.count, tags=list(item.tags))
        if isinstance(item, Mapping):
            return ItemStack(
                item_id=str(item.get("item_id", "")),
                count=int(item.get("count", 1)),
                tags=[str(tag) for tag in item.get("tags", [])],
            )
        raise ValueError(f"invalid item stack: {item!r}")
