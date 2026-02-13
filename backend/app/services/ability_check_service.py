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

VALID_ABILITIES = {"str", "dex", "con", "int", "wis", "cha"}


class RollTracker:
    """Per-turn anti-abuse tracker for player-initiated rolls (in-memory)."""

    MAX_ROLLS_PER_TURN = 3

    def __init__(self) -> None:
        self._turn_key: str = ""
        self._skills_used: set = set()
        self._roll_count: int = 0

    def reset(self, turn_key: str) -> None:
        if turn_key != self._turn_key:
            self._turn_key = turn_key
            self._skills_used = set()
            self._roll_count = 0

    def check(self, skill_or_ability: str, turn_key: str) -> Optional[str]:
        """Return error message if roll should be blocked, else None."""
        self.reset(turn_key)
        if self._roll_count >= self.MAX_ROLLS_PER_TURN:
            return f"本回合已达到掷骰上限 ({self.MAX_ROLLS_PER_TURN} 次)，请继续冒险。"
        if skill_or_ability in self._skills_used:
            return f"本回合已对 {skill_or_ability} 进行过检定，结果不可推翻。"
        return None

    def record(self, skill_or_ability: str) -> None:
        self._skills_used.add(skill_or_ability)
        self._roll_count += 1


# Module-level singleton tracker
_roll_tracker = RollTracker()


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
        source: str = "ai",
        turn_key: str = "",
    ) -> Dict[str, Any]:
        """
        Roll d20 + ability modifier + proficiency (if proficient) vs DC.

        Args:
            world_id: World ID
            session_id: Session ID
            ability: Ability name (str/dex/con/int/wis/cha). Auto-derived from skill if omitted.
            skill: Skill name (e.g. "stealth", "persuasion"). Optional.
            dc: Difficulty Class (default 10)
            source: "ai" or "player" — only player rolls are rate-limited.
            turn_key: Unique turn identifier for anti-abuse tracking.

        Returns:
            Dict with roll, modifier, total, dc, success, description
        """
        # Normalize inputs
        if skill:
            skill = skill.lower().replace(" ", "_")
        if ability:
            ability = ability.lower()

        # Validate skill
        if skill and skill not in SKILL_ABILITY_MAP:
            return {
                "error": f"未知技能: {skill}",
                "valid_skills": sorted(SKILL_ABILITY_MAP.keys()),
                "success": False,
            }

        # Validate ability
        if ability and ability not in VALID_ABILITIES:
            return {
                "error": f"未知属性: {ability}",
                "valid_abilities": sorted(VALID_ABILITIES),
                "success": False,
            }

        # Clamp DC to [1, 30]
        dc = max(1, min(dc, 30))

        # Anti-abuse for player-initiated rolls
        if source == "player" and turn_key:
            check_key = skill or ability or "raw"
            block_msg = _roll_tracker.check(check_key, turn_key)
            if block_msg:
                return {"error": block_msg, "success": False}

        character = await self.store.get_character(world_id, session_id)
        if not character:
            return {"error": "no player character", "success": False}

        # Determine ability from skill if not provided
        if skill and not ability:
            ability = SKILL_ABILITY_MAP.get(skill, "str")
        if not ability:
            ability = "str"

        ability = ability.lower()
        modifier = character.ability_modifier(ability)

        # Add proficiency if character is proficient in the skill
        proficiency = 0
        if skill and skill in [s.lower() for s in character.skill_proficiencies]:
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

        # Record for anti-abuse tracking
        if source == "player" and turn_key:
            _roll_tracker.record(skill or ability or "raw")

        return result
