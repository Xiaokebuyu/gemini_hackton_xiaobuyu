"""NPC instance tools for MCP server."""
import json
from typing import Any, Dict, List, Optional

from app.config import settings
from app.models.pro import ChatMessage, ChatRequest, SceneContext
from app.services.instance_manager import InstanceManager
from app.services.pro_service import ProService

_instance_manager = InstanceManager(
    max_instances=settings.instance_pool_max_instances,
    context_window_size=settings.instance_pool_context_window_size,
    graphize_threshold=settings.instance_pool_graphize_threshold,
    keep_recent_tokens=settings.instance_pool_keep_recent_tokens,
)
_pro_service = ProService()


def _tier_settings(tier: str) -> tuple[str, Optional[str]]:
    tier = tier.lower()
    cfg = settings.npc_tier_config
    if tier == "passerby":
        return cfg.passerby_model, cfg.passerby_thinking
    if tier == "secondary":
        return cfg.secondary_model, cfg.secondary_thinking
    return cfg.main_model, cfg.main_thinking


def _messages_from_dicts(items: Optional[List[Dict[str, Any]]]) -> List[ChatMessage]:
    if not items:
        return []
    messages: List[ChatMessage] = []
    for item in items:
        role = item.get("role", "user")
        content = item.get("content", "")
        messages.append(ChatMessage(role=role, content=content))
    return messages


def register(game_mcp) -> None:
    @game_mcp.tool()
    async def get_instance(world_id: str, npc_id: str, preload_memory: bool = True) -> str:
        instance = await _instance_manager.get_or_create(
            npc_id=npc_id,
            world_id=world_id,
            preload_memory=preload_memory,
        )
        info = instance.get_info().model_dump()
        return json.dumps(info, ensure_ascii=False, indent=2, default=str)

    @game_mcp.tool()
    async def npc_respond(
        world_id: str,
        npc_id: str,
        message: str,
        tier: str = "main",
        scene: Optional[Dict[str, Any]] = None,
        conversation_history: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        model, thinking_level = _tier_settings(tier)
        scene_ctx = SceneContext(**scene) if scene else None
        history = _messages_from_dicts(conversation_history)
        # main 档位保留工具调用能力；其他档位走纯对话以降低延迟。
        enable_tools = tier.lower() == "main"

        request = ChatRequest(
            message=message,
            scene=scene_ctx,
            conversation_history=history,
        )

        response = await _pro_service.chat(
            world_id=world_id,
            character_id=npc_id,
            request=request,
            model_override=model,
            thinking_level=thinking_level,
            enable_tools=enable_tools,
        )

        payload = {
            "npc_id": npc_id,
            "tier": tier,
            "response": response.response,
            "tool_called": response.tool_called,
            "recalled_memory": response.recalled_memory,
            "recall_query": response.recall_query,
            "thinking": response.thinking,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    @game_mcp.tool()
    async def persist_instance(world_id: str, npc_id: str) -> str:
        instance = await _instance_manager.get_or_create(
            npc_id=npc_id,
            world_id=world_id,
            preload_memory=False,
        )
        await instance.persist(_instance_manager.graph_store)
        return json.dumps({"success": True, "npc_id": npc_id}, ensure_ascii=False)
