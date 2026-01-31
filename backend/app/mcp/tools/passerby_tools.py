"""Passerby tools for MCP server."""
import json
from typing import Optional

from app.services.passerby_service import PasserbyService

_passerby_service = PasserbyService()


def register(game_mcp) -> None:
    @game_mcp.tool()
    async def spawn_passerby(
        world_id: str,
        map_id: str,
        sub_location_id: Optional[str] = None,
        spawn_hint: Optional[str] = None,
    ) -> str:
        passerby = await _passerby_service.get_or_spawn_passerby(
            world_id=world_id,
            map_id=map_id,
            sub_location_id=sub_location_id,
            spawn_hint=spawn_hint,
        )
        return json.dumps(passerby.model_dump(), ensure_ascii=False, indent=2, default=str)

    @game_mcp.tool()
    async def passerby_respond(
        world_id: str,
        map_id: str,
        instance_id: str,
        message: str,
    ) -> str:
        result = await _passerby_service.handle_passerby_dialogue(
            world_id=world_id,
            map_id=map_id,
            instance_id=instance_id,
            player_message=message,
        )
        return json.dumps(result, ensure_ascii=False, indent=2, default=str)

    @game_mcp.tool()
    async def despawn_passerby(
        world_id: str,
        map_id: str,
        instance_id: str,
        persist_memory: bool = True,
    ) -> str:
        await _passerby_service.despawn_passerby(
            world_id=world_id,
            map_id=map_id,
            instance_id=instance_id,
            persist_memory=persist_memory,
        )
        return json.dumps({"success": True, "instance_id": instance_id}, ensure_ascii=False)
