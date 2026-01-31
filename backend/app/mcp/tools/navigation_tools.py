"""Navigation tools for MCP server."""
import json
from typing import Optional

from app.services.admin.admin_coordinator import AdminCoordinator

_admin = AdminCoordinator.get_instance()


def register(game_mcp) -> None:
    @game_mcp.tool()
    async def get_location(world_id: str, session_id: str) -> str:
        result = await _admin.get_current_location(world_id, session_id)
        return json.dumps(result, ensure_ascii=False, indent=2, default=str)

    @game_mcp.tool()
    async def navigate(
        world_id: str,
        session_id: str,
        destination: Optional[str] = None,
        direction: Optional[str] = None,
    ) -> str:
        result = await _admin.navigate(
            world_id=world_id,
            session_id=session_id,
            destination=destination,
            direction=direction,
        )
        return json.dumps(result, ensure_ascii=False, indent=2, default=str)

    @game_mcp.tool()
    async def enter_sublocation(world_id: str, session_id: str, sub_location_id: str) -> str:
        result = await _admin.enter_sub_location(
            world_id=world_id,
            session_id=session_id,
            sub_location_id=sub_location_id,
        )
        return json.dumps(result, ensure_ascii=False, indent=2, default=str)

    @game_mcp.tool()
    async def leave_sublocation(world_id: str, session_id: str) -> str:
        result = await _admin.leave_sub_location(
            world_id=world_id,
            session_id=session_id,
        )
        return json.dumps(result, ensure_ascii=False, indent=2, default=str)
