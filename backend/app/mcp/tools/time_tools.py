"""Time tools for MCP server."""
import json

from app.services.admin.admin_coordinator import AdminCoordinator

_admin = AdminCoordinator.get_instance()


def register(game_mcp) -> None:
    @game_mcp.tool()
    async def get_time(world_id: str, session_id: str) -> str:
        result = await _admin.get_game_time(world_id, session_id)
        return json.dumps(result, ensure_ascii=False, indent=2, default=str)

    @game_mcp.tool()
    async def advance_time(world_id: str, session_id: str, minutes: int = 30) -> str:
        result = await _admin.advance_time(world_id, session_id, minutes)
        return json.dumps(result, ensure_ascii=False, indent=2, default=str)
