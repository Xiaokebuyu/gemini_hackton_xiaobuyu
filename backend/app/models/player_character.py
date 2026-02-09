"""Player character model for BG3-style character system."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class CharacterRace(str, Enum):
    HUMAN = "human"
    ELF = "elf"
    DWARF = "dwarf"
    RHEA = "rhea"
    LIZARDMAN = "lizardman"
    PADFOOT = "padfoot"


class CharacterClass(str, Enum):
    FIGHTER = "fighter"
    PRIEST = "priest"
    MAGE = "mage"
    RANGER = "ranger"
    SCOUT = "scout"
    MONK = "monk"
    SWORDSMAN = "swordsman"
    SHAMAN = "shaman"


class PlayerCharacter(BaseModel):
    """Persistent player character data."""

    character_id: str = "player"
    name: str
    race: CharacterRace
    character_class: CharacterClass
    background: str = ""
    backstory: str = ""
    level: int = 1
    xp: int = 0
    xp_to_next_level: int = 300

    # Combat stats
    abilities: Dict[str, int]  # str/dex/con/int/wis/cha (post-racial)
    max_hp: int
    current_hp: int
    ac: int
    initiative_bonus: int
    proficiency_bonus: int = 2
    speed: int = 30

    # Proficiencies
    skill_proficiencies: List[str] = Field(default_factory=list)
    saving_throw_proficiencies: List[str] = Field(default_factory=list)
    weapon_proficiencies: List[str] = Field(default_factory=list)
    armor_proficiencies: List[str] = Field(default_factory=list)

    # Features
    feats: List[str] = Field(default_factory=list)
    class_features: List[str] = Field(default_factory=list)
    racial_traits: List[str] = Field(default_factory=list)

    # Equipment & Inventory
    equipment: Dict[str, Optional[str]] = Field(default_factory=dict)  # slot -> item_id
    inventory: List[Dict[str, Any]] = Field(default_factory=list)
    gold: int = 0

    # Conditions
    conditions: List[Dict[str, Any]] = Field(default_factory=list)

    # Spellcasting
    spell_slots: Dict[int, int] = Field(default_factory=dict)
    spell_slots_used: Dict[int, int] = Field(default_factory=dict)
    spells_known: List[str] = Field(default_factory=list)

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    def ability_modifier(self, ability: str) -> int:
        """Calculate ability modifier: (score - 10) // 2"""
        score = self.abilities.get(ability, 10)
        return (score - 10) // 2

    def add_item(
        self,
        item_id: str,
        item_name: str,
        quantity: int = 1,
        properties: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Add item to inventory. Stacks if same item_id exists."""
        for item in self.inventory:
            if item.get("item_id") == item_id:
                item["quantity"] = item.get("quantity", 1) + quantity
                return item
        new_item: Dict[str, Any] = {
            "item_id": item_id,
            "name": item_name,
            "quantity": quantity,
        }
        if properties:
            new_item["properties"] = properties
        self.inventory.append(new_item)
        return new_item

    def remove_item(self, item_id: str, quantity: int = 1) -> bool:
        """Remove item from inventory. Returns True if successful."""
        for i, item in enumerate(self.inventory):
            if item.get("item_id") == item_id:
                current_qty = item.get("quantity", 1)
                if current_qty <= quantity:
                    self.inventory.pop(i)
                else:
                    item["quantity"] = current_qty - quantity
                return True
        return False

    def has_item(self, item_id: str) -> bool:
        """Check if item exists in inventory."""
        return any(item.get("item_id") == item_id for item in self.inventory)

    def to_summary_text(self) -> str:
        """Generate ~100 token summary for LLM context injection."""
        mods = {k: self.ability_modifier(k) for k in self.abilities}
        mod_str = " ".join(f"{k[:3].upper()}:{v:+d}" for k, v in mods.items())
        equip_parts = []
        for slot, item_id in self.equipment.items():
            if item_id:
                equip_parts.append(f"{slot}={item_id}")
        equip_str = ", ".join(equip_parts) if equip_parts else "无装备"

        conditions_str = ""
        if self.conditions:
            conditions_str = f" | 状态: {', '.join(c.get('name', '?') for c in self.conditions)}"

        return (
            f"[玩家角色] {self.name} | {self.race.value} {self.character_class.value} Lv{self.level} | "
            f"HP:{self.current_hp}/{self.max_hp} AC:{self.ac} | "
            f"{mod_str} | 装备: {equip_str}{conditions_str}"
        )

    def _get_equipped_weapon_stats(self) -> Optional[Dict[str, Any]]:
        """Look up equipped weapon from item registry for combat stats."""
        weapon_id = self.equipment.get("main_hand")
        if not weapon_id:
            return None
        try:
            from app.services.item_registry import get_item
            item = get_item(weapon_id)
            if item and item.get("type") == "weapon":
                return item
        except Exception:
            pass
        return None

    def to_combat_player_state(self) -> Dict[str, Any]:
        """Bridge to combat system's player_state format."""
        str_mod = self.ability_modifier("str")
        dex_mod = self.ability_modifier("dex")
        best_attack_mod = max(str_mod, dex_mod)
        attack_bonus = best_attack_mod + self.proficiency_bonus

        # Default weapon stats
        damage_dice = "1d6"
        damage_bonus = best_attack_mod
        damage_type = "slashing"

        # Override from equipped weapon if available
        weapon = self._get_equipped_weapon_stats()
        if weapon:
            props = weapon.get("properties", {})
            if props.get("damage"):
                damage_dice = props["damage"]
            subtype = weapon.get("subtype", "")
            # Ranged weapons use DEX, melee use STR (or DEX if finesse/light)
            if "ranged" in subtype:
                attack_bonus = dex_mod + self.proficiency_bonus
                damage_bonus = dex_mod
            else:
                attack_bonus = str_mod + self.proficiency_bonus
                damage_bonus = str_mod
            # Determine damage type from weapon name/subtype heuristics
            weapon_name = weapon.get("name", "")
            if any(k in weapon_name for k in ("弓", "弩", "投")):
                damage_type = "piercing"
            elif any(k in weapon_name for k in ("锤", "棍", "铳")):
                damage_type = "bludgeoning"
            else:
                damage_type = "slashing"

        return {
            "name": self.name,
            "hp": self.current_hp,
            "max_hp": self.max_hp,
            "ac": self.ac,
            "level": self.level,
            "abilities": dict(self.abilities),
            "proficiency_bonus": self.proficiency_bonus,
            "initiative_bonus": self.initiative_bonus,
            "attack_bonus": attack_bonus,
            "damage_dice": damage_dice,
            "damage_bonus": damage_bonus,
            "damage_type": damage_type,
            "equipment": dict(self.equipment),
            "spell_slots": dict(self.spell_slots),
            "spell_slots_used": dict(self.spell_slots_used),
            "spells_known": list(self.spells_known),
            "class": self.character_class.value,
            "race": self.race.value,
        }
