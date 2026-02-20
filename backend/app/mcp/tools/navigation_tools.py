"""Navigation tools for MCP server."""
import json

_admin = None


def _get_admin():
    global _admin
    if _admin is None:
        from app.services.admin.admin_coordinator import AdminCoordinator
        _admin = AdminCoordinator.get_instance()
    return _admin


def register(game_mcp) -> None:
    @game_mcp.tool()
    async def get_location(world_id: str, session_id: str) -> str:
        result = await _get_admin().get_current_location(world_id, session_id)
        return json.dumps(result, ensure_ascii=False, indent=2, default=str)

    @game_mcp.tool()
    async def enter_sublocation(world_id: str, session_id: str, sub_location_id: str) -> str:
        result = await _get_admin().enter_sub_location(
            world_id=world_id,
            session_id=session_id,
            sub_location_id=sub_location_id,
        )
        return json.dumps(result, ensure_ascii=False, indent=2, default=str)

    @game_mcp.tool()
    async def leave_sublocation(world_id: str, session_id: str) -> str:
        result = await _get_admin().leave_sub_location(
            world_id=world_id,
            session_id=session_id,
        )
        return json.dumps(result, ensure_ascii=False, indent=2, default=str)
