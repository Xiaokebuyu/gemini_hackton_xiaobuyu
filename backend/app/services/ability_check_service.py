"""Ability check service - d20 + modifier + proficiency vs DC."""
import random
from typing import Any, Dict, Optional

from app.models.player_character import PlayerCharacter
from app.services.character_store import CharacterStore

# Skill -> ability mapping (D&D 5e)
SKILL_ABILITY_MAP = {
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


class AbilityCheckService:
    """Performs D&D-style ability checks."""

    def __init__(self, store: Optional[CharacterStore] = None) -> None:
        self.store = store or CharacterStore()

    async def perform_check(
        self,
        world_id: str,
        session_id: str,
        ability: Optional[str] = None,
        skill: Optional[str] = None,
        dc: int = 10,
    ) -> Dict[str, Any]:
        """
        Roll d20 + ability modifier + proficiency (if proficient) vs DC.

        Args:
            world_id: World ID
            session_id: Session ID
            ability: Ability name (str/dex/con/int/wis/cha). Auto-derived from skill if omitted.
            skill: Skill name (e.g. "stealth", "persuasion"). Optional.
            dc: Difficulty Class (default 10)

        Returns:
            Dict with roll, modifier, total, dc, success, description
        """
        character = await self.store.get_character(world_id, session_id)
        if not character:
            return {"error": "no player character", "success": False}

        # Determine ability from skill if not provided
        if skill and not ability:
            ability = SKILL_ABILITY_MAP.get(skill.lower(), "str")
        if not ability:
            ability = "str"

        ability = ability.lower()
        modifier = character.ability_modifier(ability)

        # Add proficiency if character is proficient in the skill
        proficiency = 0
        if skill and skill.lower() in [s.lower() for s in character.skill_proficiencies]:
            proficiency = character.proficiency_bonus

        # Roll
        roll = random.randint(1, 20)
        total = roll + modifier + proficiency
        success = total >= dc

        # Natural 20/1 flavor
        is_critical = roll == 20
        is_fumble = roll == 1

        result: Dict[str, Any] = {
            "roll": roll,
            "ability": ability,
            "modifier": modifier,
            "proficiency": proficiency,
            "total": total,
            "dc": dc,
            "success": success,
            "is_critical": is_critical,
            "is_fumble": is_fumble,
        }

        if skill:
            result["skill"] = skill

        # Generate description
        parts = [f"d20={roll}"]
        if modifier:
            parts.append(f"{ability.upper()}{modifier:+d}")
        if proficiency:
            parts.append(f"熟练{proficiency:+d}")
        roll_desc = " + ".join(parts) if len(parts) > 1 else parts[0]

        if is_critical:
            result["description"] = f"大成功！{roll_desc} = {total} vs DC{dc}"
        elif is_fumble:
            result["description"] = f"大失败！{roll_desc} = {total} vs DC{dc}"
        elif success:
            result["description"] = f"成功！{roll_desc} = {total} vs DC{dc}"
        else:
            result["description"] = f"失败。{roll_desc} = {total} vs DC{dc}"

        return result
