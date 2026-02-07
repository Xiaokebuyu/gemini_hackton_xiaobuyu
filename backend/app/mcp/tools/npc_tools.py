"""NPC instance tools for MCP server."""
import json
from typing import Any, Dict, List, Optional

from app.config import settings
from app.services.instance_manager import InstanceManager
from app.services.llm_service import LLMService

_instance_manager = InstanceManager(
    max_instances=settings.instance_pool_max_instances,
    context_window_size=settings.instance_pool_context_window_size,
    graphize_threshold=settings.instance_pool_graphize_threshold,
    keep_recent_tokens=settings.instance_pool_keep_recent_tokens,
)
_llm_service = LLMService()


def _tier_settings(tier: str) -> tuple[str, Optional[str]]:
    tier = tier.lower()
    cfg = settings.npc_tier_config
    if tier == "passerby":
        return cfg.passerby_model, cfg.passerby_thinking
    if tier == "secondary":
        return cfg.secondary_model, cfg.secondary_thinking
    return cfg.main_model, cfg.main_thinking


def _format_scene(scene: Optional[Dict[str, Any]]) -> str:
    if not scene:
        return "无"
    lines = []
    location = scene.get("location")
    if location:
        lines.append(f"- 地点: {location}")
    description = scene.get("description")
    if description:
        lines.append(f"- 描述: {description}")
    environment = scene.get("environment")
    if environment:
        lines.append(f"- 氛围: {environment}")
    present_characters = scene.get("present_characters")
    if present_characters:
        lines.append(f"- 在场角色: {', '.join(str(c) for c in present_characters)}")
    return "\n".join(lines) if lines else "无"


def _append_external_history(
    instance,
    conversation_history: Optional[List[Dict[str, Any]]],
) -> None:
    if not conversation_history:
        return
    for item in conversation_history[-12:]:
        role = str(item.get("role", "user")).strip().lower()
        content = str(item.get("content", "")).strip()
        if not content:
            continue
        if role not in {"user", "assistant", "system"}:
            role = "user"
        instance.context_window.add_message(role, content)


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
        instance = await _instance_manager.get_or_create(
            npc_id=npc_id,
            world_id=world_id,
            preload_memory=True,
        )

        _append_external_history(instance, conversation_history)
        instance.context_window.add_message("user", message)

        recent = instance.context_window.get_recent_messages(count=24)
        history_lines = [f"{msg.role}: {msg.content}" for msg in recent[:-1]]
        history_text = "\n".join(history_lines) if history_lines else "无"
        system_prompt = (
            instance.context_window.get_system_prompt()
            or f"你是 {npc_id}。请保持角色一致性。"
        )
        scene_text = _format_scene(scene)

        prompt = (
            f"{system_prompt}\n\n"
            f"## 当前场景\n{scene_text}\n\n"
            f"## 近期对话\n{history_text}\n\n"
            f"## 玩家输入\n{message}\n\n"
            "请用中文以角色身份回复1-3句，保持上下文连续。"
        )

        response_text = await _llm_service.generate_simple(
            prompt,
            model_override=model,
            thinking_level=thinking_level,
        )
        response_text = (response_text or "").strip()
        if not response_text:
            payload = {
                "npc_id": npc_id,
                "tier": tier,
                "error": "empty_response",
                "response": "",
            }
            return json.dumps(payload, ensure_ascii=False, indent=2)

        instance.context_window.add_message("assistant", response_text)
        instance.state.conversation_turn_count += 1

        payload = {
            "npc_id": npc_id,
            "tier": tier,
            "response": response_text,
            "tool_called": False,
            "recalled_memory": None,
            "recall_query": None,
            "thinking": None,
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
