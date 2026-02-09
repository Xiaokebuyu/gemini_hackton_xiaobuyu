"""
Character creation and management business logic.

Validates input, applies racial bonuses, calculates derived stats,
and persists via CharacterStore.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.models.character_creation import CharacterCreationRequest
from app.models.player_character import CharacterClass, CharacterRace, PlayerCharacter
from app.services.character_store import CharacterStore

logger = logging.getLogger(__name__)


class CharacterService:
    """Business logic for player character lifecycle."""

    def __init__(self, store: Optional[CharacterStore] = None) -> None:
        self.store = store or CharacterStore()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def create_character(
        self,
        world_id: str,
        session_id: str,
        request: CharacterCreationRequest,
    ) -> PlayerCharacter:
        """
        Validate request, apply racial bonuses, calculate derived stats,
        persist and return the new PlayerCharacter.
        """
        config = await self.store.get_creation_config(world_id)

        # --- Validate enums ---
        race = CharacterRace(request.race)
        char_class = CharacterClass(request.character_class)

        race_cfg = config["races"].get(race.value)
        if not race_cfg:
            raise ValueError(f"Unknown race: {race.value}")
        class_cfg = config["classes"].get(char_class.value)
        if not class_cfg:
            raise ValueError(f"Unknown class: {char_class.value}")

        # --- Validate point buy ---
        if not self.validate_point_buy(request.ability_scores, config["point_buy"]):
            raise ValueError("Invalid point-buy allocation")

        # --- Apply racial bonuses ---
        abilities = dict(request.ability_scores)
        for ability, bonus in race_cfg.get("ability_bonuses", {}).items():
            abilities[ability] = abilities.get(ability, 10) + bonus

        # --- Derived stats ---
        con_mod = (abilities.get("con", 10) - 10) // 2
        dex_mod = (abilities.get("dex", 10) - 10) // 2
        hit_die = class_cfg["hit_die"]
        max_hp = hit_die + con_mod
        ac = self._calculate_ac(char_class.value, dex_mod, abilities, race_cfg, class_cfg)
        initiative_bonus = dex_mod
        speed = race_cfg.get("speed", 30)

        # --- Proficiencies ---
        skill_profs = list(request.skill_proficiencies)
        # Add background skills
        bg_cfg = config["backgrounds"].get(request.background, {})
        for sk in bg_cfg.get("skill_proficiencies", []):
            if sk not in skill_profs:
                skill_profs.append(sk)
        # Add racial free proficiencies
        for sk in race_cfg.get("free_proficiencies", []):
            if sk not in skill_profs:
                skill_profs.append(sk)

        saving_throw_profs = list(class_cfg.get("saving_throws", []))
        weapon_profs = list(class_cfg.get("weapon_proficiencies", []))
        armor_profs = list(class_cfg.get("armor_proficiencies", []))

        # --- Features ---
        class_features = list(class_cfg.get("class_features", []))
        racial_traits = list(race_cfg.get("racial_traits", []))

        # --- Equipment ---
        equipment: Dict[str, Optional[str]] = {}
        for item in class_cfg.get("starting_equipment", []):
            equipment[item["slot"]] = item["item_id"]

        # --- Gold ---
        gold = class_cfg.get("starting_gold", 0) + bg_cfg.get("starting_gold_bonus", 0)

        # --- Spellcasting ---
        spell_slots: Dict[int, int] = {}
        if class_cfg.get("spellcasting"):
            raw_slots = class_cfg.get("spell_slots_at_level_1", {})
            spell_slots = {int(k): v for k, v in raw_slots.items()}

        # --- XP thresholds ---
        leveling = config.get("leveling", {})
        xp_thresholds = leveling.get("xp_thresholds", {})
        xp_to_next = xp_thresholds.get("2", 300)

        character = PlayerCharacter(
            name=request.name,
            race=race,
            character_class=char_class,
            background=request.background,
            backstory=request.backstory,
            abilities=abilities,
            max_hp=max_hp,
            current_hp=max_hp,
            ac=ac,
            initiative_bonus=initiative_bonus,
            speed=speed,
            skill_proficiencies=skill_profs,
            saving_throw_proficiencies=saving_throw_profs,
            weapon_proficiencies=weapon_profs,
            armor_proficiencies=armor_profs,
            class_features=class_features,
            racial_traits=racial_traits,
            equipment=equipment,
            gold=gold,
            spell_slots=spell_slots,
            spell_slots_used={k: 0 for k in spell_slots},
            xp_to_next_level=xp_to_next,
        )

        await self.store.save_character(world_id, session_id, character)
        logger.info(
            "Created character: %s (%s %s) for %s/%s",
            character.name, race.value, char_class.value, world_id, session_id,
        )
        return character

    async def get_character(
        self,
        world_id: str,
        session_id: str,
    ) -> Optional[PlayerCharacter]:
        """Retrieve saved player character."""
        return await self.store.get_character(world_id, session_id)

    async def set_hp(
        self,
        world_id: str,
        session_id: str,
        hp: int,
    ) -> None:
        """Atomic HP update (clamps to [0, max_hp])."""
        character = await self.store.get_character(world_id, session_id)
        if not character:
            raise ValueError("No player character found")
        character.current_hp = max(0, min(hp, character.max_hp))
        character.updated_at = datetime.now()
        await self.store.save_character(world_id, session_id, character)

    async def add_xp(
        self,
        world_id: str,
        session_id: str,
        amount: int,
    ) -> Dict[str, Any]:
        """
        Add XP and check for level-up.

        Returns dict with keys:
          - xp_gained: int
          - new_xp: int
          - leveled_up: bool
          - new_level: int (if leveled up)
          - hp_gained: int (if leveled up)
        """
        character = await self.store.get_character(world_id, session_id)
        if not character:
            raise ValueError("No player character found")

        config = await self.store.get_creation_config(world_id)
        leveling = config.get("leveling", {})
        xp_thresholds = leveling.get("xp_thresholds", {})
        proficiency_by_level = leveling.get("proficiency_by_level", {})

        character.xp += amount
        result: Dict[str, Any] = {
            "xp_gained": amount,
            "new_xp": character.xp,
            "leveled_up": False,
            "new_level": character.level,
            "hp_gained": 0,
        }

        # Check level-up (supports multi-level jumps)
        while True:
            next_level = character.level + 1
            threshold = xp_thresholds.get(str(next_level))
            if threshold is None or character.xp < threshold:
                break

            character.level = next_level
            result["leveled_up"] = True
            result["new_level"] = next_level

            # Update proficiency bonus
            prof = proficiency_by_level.get(str(next_level))
            if prof:
                character.proficiency_bonus = prof

            # HP gain on level-up: average hit die roll + CON modifier
            class_cfg = config["classes"].get(character.character_class.value, {})
            hit_die = class_cfg.get("hit_die", 8)
            con_mod = character.ability_modifier("con")
            hp_gain = max(1, math.ceil(hit_die / 2) + 1 + con_mod)
            character.max_hp += hp_gain
            character.current_hp += hp_gain
            result["hp_gained"] += hp_gain

            # Update xp_to_next_level
            next_next = str(next_level + 1)
            if next_next in xp_thresholds:
                character.xp_to_next_level = xp_thresholds[next_next]

            # Update spell slots for casters
            if class_cfg.get("spellcasting"):
                self._update_spell_slots_for_level(character, next_level)

        character.updated_at = datetime.now()
        await self.store.save_character(world_id, session_id, character)
        return result

    @staticmethod
    def validate_point_buy(scores: Dict[str, int], config: Dict[str, Any]) -> bool:
        """Verify that ability scores follow 27-point buy rules."""
        total_points = config.get("total_points", 27)
        min_score = config.get("min_score", 8)
        max_score = config.get("max_score", 15)
        cost_table = config.get("cost_table", {})
        required_abilities = config.get("abilities", ["str", "dex", "con", "int", "wis", "cha"])

        # Must have exactly the required abilities
        if set(scores.keys()) != set(required_abilities):
            return False

        spent = 0
        for ability, score in scores.items():
            if score < min_score or score > max_score:
                return False
            cost = cost_table.get(str(score))
            if cost is None:
                return False
            spent += cost

        return spent == total_points

    async def equip_item(
        self,
        world_id: str,
        session_id: str,
        item_id: str,
        slot: str,
    ) -> Dict[str, Any]:
        """Equip an item from inventory to a slot."""
        character = await self.store.get_character(world_id, session_id)
        if not character:
            raise ValueError("No player character found")
        if not character.has_item(item_id):
            raise ValueError(f"Item {item_id} not in inventory")

        # Unequip current item in slot (put back in inventory)
        old_item_id = character.equipment.get(slot)
        if old_item_id:
            character.add_item(old_item_id, old_item_id, 1)

        # Equip new item (remove from inventory)
        character.remove_item(item_id, 1)
        character.equipment[slot] = item_id

        # Recalculate AC if armor/shield changed
        if slot in ("armor", "off_hand"):
            self._recalculate_ac(character)

        character.updated_at = datetime.now()
        await self.store.save_character(world_id, session_id, character)
        return {"slot": slot, "item_id": item_id, "previous": old_item_id}

    async def unequip_item(
        self,
        world_id: str,
        session_id: str,
        slot: str,
    ) -> Dict[str, Any]:
        """Unequip item from a slot back to inventory."""
        character = await self.store.get_character(world_id, session_id)
        if not character:
            raise ValueError("No player character found")
        item_id = character.equipment.get(slot)
        if not item_id:
            raise ValueError(f"No item equipped in slot {slot}")

        character.equipment[slot] = None
        character.add_item(item_id, item_id, 1)

        if slot in ("armor", "off_hand"):
            self._recalculate_ac(character)

        character.updated_at = datetime.now()
        await self.store.save_character(world_id, session_id, character)
        return {"slot": slot, "unequipped": item_id}

    @staticmethod
    def _recalculate_ac(character: PlayerCharacter) -> None:
        """Recalculate AC based on currently equipped armor/shield."""
        from app.services.item_registry import get_item

        dex_mod = character.ability_modifier("dex")
        armor_id = character.equipment.get("armor")
        shield_id = character.equipment.get("off_hand")

        # Base AC from armor
        base_ac = 10 + dex_mod  # unarmored default
        if armor_id:
            armor_data = get_item(armor_id)
            if armor_data:
                subtype = armor_data.get("subtype", "")
                ac_bonus = armor_data.get("properties", {}).get("ac_bonus", 0)
                if subtype == "heavy":
                    base_ac = 10 + ac_bonus  # heavy armor: no DEX
                elif subtype in ("light", "clothing"):
                    base_ac = 10 + ac_bonus + dex_mod  # light: full DEX
                else:
                    base_ac = 10 + ac_bonus + min(dex_mod, 2)  # medium: DEX cap 2

        # Shield bonus
        shield_bonus = 0
        if shield_id:
            shield_data = get_item(shield_id)
            if shield_data and shield_data.get("subtype") == "shield":
                shield_bonus = shield_data.get("properties", {}).get("ac_bonus", 0)

        # Monk unarmored defense
        if character.character_class.value == "monk" and not armor_id:
            wis_mod = character.ability_modifier("wis")
            base_ac = max(base_ac, 10 + dex_mod + wis_mod)

        # Natural armor (lizardman)
        if "natural_armor" in character.racial_traits:
            base_ac = max(base_ac, 13 + dex_mod)

        character.ac = base_ac + shield_bonus

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _calculate_ac(
        class_name: str,
        dex_mod: int,
        abilities: Dict[str, int],
        race_cfg: Dict[str, Any],
        class_cfg: Dict[str, Any],
    ) -> int:
        """Calculate starting AC based on class equipment and racial traits."""
        # Monk: 10 + DEX + WIS
        if class_name == "monk":
            wis_mod = (abilities.get("wis", 10) - 10) // 2
            return 10 + dex_mod + wis_mod

        # Lizardman natural armor: 13 + DEX (if better than equipped armor)
        has_natural_armor = "natural_armor" in race_cfg.get("racial_traits", [])

        # Determine AC from starting equipment
        equipment = class_cfg.get("starting_equipment", [])
        armor_id = None
        has_shield = False
        for item in equipment:
            if item["slot"] == "armor":
                armor_id = item["item_id"]
            if item["slot"] == "off_hand" and "shield" in item.get("item_id", ""):
                has_shield = True

        shield_bonus = 2 if has_shield else 0

        # Base AC by armor type
        if armor_id == "armor_chain_mail":
            base_ac = 16  # Heavy, no DEX bonus
        elif armor_id == "armor_chain_shirt":
            base_ac = 13 + min(dex_mod, 2)  # Medium
        elif armor_id == "armor_leather":
            base_ac = 11 + dex_mod  # Light
        elif armor_id == "armor_basic_clothing":
            base_ac = 10 + dex_mod  # Unarmored
        else:
            base_ac = 10 + dex_mod

        equipped_ac = base_ac + shield_bonus

        # Natural armor comparison
        if has_natural_armor:
            natural_ac = 13 + dex_mod
            return max(equipped_ac, natural_ac)

        return equipped_ac

    @staticmethod
    def _update_spell_slots_for_level(character: PlayerCharacter, level: int) -> None:
        """Update spell slots when leveling up (simplified progression)."""
        # Simplified full-caster slot progression
        slot_progression = {
            2: {1: 3},
            3: {1: 4, 2: 2},
            4: {1: 4, 2: 3},
            5: {1: 4, 2: 3, 3: 2},
        }
        if level in slot_progression:
            for slot_level, count in slot_progression[level].items():
                character.spell_slots[slot_level] = count
                if slot_level not in character.spell_slots_used:
                    character.spell_slots_used[slot_level] = 0
