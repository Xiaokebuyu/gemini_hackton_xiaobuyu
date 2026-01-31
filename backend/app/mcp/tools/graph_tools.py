"""Graph tools for MCP server."""
import json
from typing import Any, Dict, Optional

from app.models.flash import RecallRequest
from app.models.graph import MemoryNode
from app.services.flash_service import FlashService
from app.services.graph_store import GraphStore

_graph_store = GraphStore()
_flash_service = FlashService(_graph_store)


def register(game_mcp) -> None:
    @game_mcp.tool()
    async def query_graph(
        world_id: str,
        graph_type: str,
        character_id: Optional[str] = None,
    ) -> str:
        graph = await _graph_store.load_graph(world_id, graph_type, character_id)
        return json.dumps(graph.model_dump(), ensure_ascii=False, indent=2, default=str)

    @game_mcp.tool()
    async def upsert_node(
        world_id: str,
        graph_type: str,
        node: Dict[str, Any],
        character_id: Optional[str] = None,
        index: bool = False,
    ) -> str:
        node_obj = MemoryNode(**node)
        await _graph_store.upsert_node(
            world_id=world_id,
            graph_type=graph_type,
            node=node_obj,
            character_id=character_id,
            index=index,
        )
        return json.dumps({"success": True, "node_id": node_obj.id}, ensure_ascii=False)

    @game_mcp.tool()
    async def recall_memory(
        world_id: str,
        character_id: str,
        request: Dict[str, Any],
    ) -> str:
        recall_req = request if isinstance(request, RecallRequest) else RecallRequest(**request)
        result = await _flash_service.recall_memory(world_id, character_id, recall_req)
        payload = result.model_dump() if hasattr(result, "model_dump") else result
        return json.dumps(payload, ensure_ascii=False, indent=2, default=str)
