"""Character tools for MCP server."""
import json
from typing import Optional

from app.services.character_service import CharacterService
from app.services.character_store import CharacterStore

_store = CharacterStore()
_service = CharacterService(store=_store)


def register(game_mcp) -> None:
    @game_mcp.tool()
    async def get_player_character(world_id: str, session_id: str) -> str:
        """Get the player character sheet.

        Returns JSON with full character data including name, race, class,
        level, HP, AC, abilities, equipment, skills, etc.
        Returns {"character": null} if no character exists.
        """
        character = await _store.get_character(world_id, session_id)
        if not character:
            return json.dumps({"character": None}, ensure_ascii=False)
        return json.dumps(
            {"character": character.model_dump(mode="json")},
            ensure_ascii=False,
            indent=2,
            default=str,
        )

    @game_mcp.tool()
    async def get_player_character_summary(world_id: str, session_id: str) -> str:
        """Get a brief text summary of the player character for LLM context.

        Returns a ~100 token text summary suitable for prompt injection.
        """
        character = await _store.get_character(world_id, session_id)
        if not character:
            return json.dumps({"summary": "无玩家角色"}, ensure_ascii=False)
        return json.dumps(
            {"summary": character.to_summary_text()},
            ensure_ascii=False,
        )

    @game_mcp.tool()
    async def heal_player(
        world_id: str, session_id: str, amount: int
    ) -> str:
        """Heal the player by a given amount (capped at max_hp).

        Args:
            world_id: World ID
            session_id: Session ID
            amount: HP to heal (positive integer)
        """
        character = await _store.get_character(world_id, session_id)
        if not character:
            return json.dumps({"error": "no player character"}, ensure_ascii=False)
        new_hp = min(character.current_hp + amount, character.max_hp)
        await _service.set_hp(world_id, session_id, new_hp)
        return json.dumps(
            {"success": True, "hp": new_hp, "max_hp": character.max_hp},
            ensure_ascii=False,
        )

    @game_mcp.tool()
    async def damage_player(
        world_id: str, session_id: str, amount: int
    ) -> str:
        """Apply damage to the player (min 0 HP).

        Args:
            world_id: World ID
            session_id: Session ID
            amount: damage to apply (positive integer)
        """
        character = await _store.get_character(world_id, session_id)
        if not character:
            return json.dumps({"error": "no player character"}, ensure_ascii=False)
        new_hp = max(character.current_hp - amount, 0)
        await _service.set_hp(world_id, session_id, new_hp)
        return json.dumps(
            {"success": True, "hp": new_hp, "max_hp": character.max_hp},
            ensure_ascii=False,
        )

    @game_mcp.tool()
    async def add_player_xp(
        world_id: str, session_id: str, amount: int
    ) -> str:
        """Award XP to the player. Handles level-up automatically.

        Args:
            world_id: World ID
            session_id: Session ID
            amount: XP to award (positive integer)
        """
        result = await _service.add_xp(world_id, session_id, amount)
        return json.dumps(result, ensure_ascii=False)

    @game_mcp.tool()
    async def set_player_hp(
        world_id: str, session_id: str, hp: int
    ) -> str:
        """Set the player's HP to an exact value.

        Args:
            world_id: World ID
            session_id: Session ID
            hp: exact HP value to set
        """
        character = await _store.get_character(world_id, session_id)
        if not character:
            return json.dumps({"error": "no player character"}, ensure_ascii=False)
        clamped = max(0, min(hp, character.max_hp))
        await _service.set_hp(world_id, session_id, clamped)
        return json.dumps(
            {"success": True, "hp": clamped, "max_hp": character.max_hp},
            ensure_ascii=False,
        )
