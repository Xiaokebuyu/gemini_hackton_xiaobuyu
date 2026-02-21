"""Character tools for MCP server."""
import json

from app.services.character_store import CharacterStore

_store = CharacterStore()


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
