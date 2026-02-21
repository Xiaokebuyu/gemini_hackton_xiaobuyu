"""Narrative tools for MCP server."""
import json

from app.services.narrative_service import NarrativeService

_narrative_service = NarrativeService()


def register(game_mcp) -> None:
    @game_mcp.tool()
    async def get_progress(world_id: str, session_id: str) -> str:
        progress = await _narrative_service.get_progress(world_id, session_id)
        return json.dumps(progress.to_dict(), ensure_ascii=False, indent=2, default=str)

    @game_mcp.tool()
    async def get_available_maps(world_id: str, session_id: str) -> str:
        maps = await _narrative_service.get_available_maps(world_id, session_id)
        return json.dumps({"available_maps": maps, "all_unlocked": "*" in maps}, ensure_ascii=False)

