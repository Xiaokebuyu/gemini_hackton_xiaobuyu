"""
Tiered AI Service - 简化的 NPC 层级响应服务

规则：
- PASSERBY -> FAST (Flash 模型，无工具)
- SECONDARY -> SUBCONSCIOUS (Flash 模型 + thinking)
- MAIN -> DEEP (Pro 模型 + thinking)
"""
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional

from app.config import settings
from app.services.llm_service import LLMService
from app.services.pro_service import ProService
from app.tools.worldbook_graphizer.models import NPCTier


class AITier(str, Enum):
    """AI响应层级"""
    FAST = "fast"
    SUBCONSCIOUS = "subconscious"
    DEEP = "deep"


@dataclass
class TieredResponse:
    """分层响应结果"""
    content: str
    tier_used: AITier
    latency_ms: float
    recalled_memory: Optional[str] = None
    cache_hit: bool = False


TIER_BY_NPC = {
    NPCTier.PASSERBY: AITier.FAST,
    NPCTier.SECONDARY: AITier.SUBCONSCIOUS,
    NPCTier.MAIN: AITier.DEEP,
}


class TieredAIService:
    """简化的三层AI响应服务"""

    def __init__(
        self,
        llm_service: Optional[LLMService] = None,
        pro_service: Optional[ProService] = None,
    ) -> None:
        self._llm_service = llm_service or LLMService()
        self._pro_service = pro_service or ProService()

    def determine_tier(
        self,
        npc_tier: NPCTier,
        force_tier: Optional[AITier] = None,
    ) -> AITier:
        """根据NPC层级决定AI层"""
        if force_tier:
            return force_tier
        return TIER_BY_NPC.get(npc_tier, AITier.SUBCONSCIOUS)

    async def respond(
        self,
        world_id: str,
        npc_id: str,
        npc_tier: NPCTier,
        query: str,
        location_id: str,
        npc_profile: Dict[str, Any],
        conversation_history: Optional[list] = None,
        force_tier: Optional[AITier] = None,
        sub_location_id: Optional[str] = None,
    ) -> TieredResponse:
        """生成NPC响应"""
        start = time.time()
        tier = self.determine_tier(npc_tier, force_tier)

        try:
            if tier == AITier.FAST:
                response = await self._respond_fast(query, npc_profile, location_id, sub_location_id)
            elif tier == AITier.SUBCONSCIOUS:
                response = await self._respond_chat(
                    world_id=world_id,
                    npc_id=npc_id,
                    query=query,
                    location_id=location_id,
                    conversation_history=conversation_history,
                    model=settings.npc_tier_config.secondary_model,
                    thinking_level=settings.npc_tier_config.secondary_thinking,
                    history_limit=10,
                )
            else:
                response = await self._respond_chat(
                    world_id=world_id,
                    npc_id=npc_id,
                    query=query,
                    location_id=location_id,
                    conversation_history=conversation_history,
                    model=settings.npc_tier_config.main_model,
                    thinking_level=settings.npc_tier_config.main_thinking,
                    history_limit=20,
                )
        except Exception:
            response = await self._respond_fast(query, npc_profile, location_id, sub_location_id)

        latency = (time.time() - start) * 1000

        return TieredResponse(
            content=response.get("content", ""),
            tier_used=tier,
            latency_ms=latency,
            recalled_memory=response.get("recalled_memory"),
            cache_hit=response.get("cache_hit", False),
        )

    async def _respond_fast(
        self,
        query: str,
        npc_profile: Dict[str, Any],
        location_id: str,
        sub_location_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fast层响应 - 无工具，轻量prompt"""
        prompt = self._build_fast_prompt(query, npc_profile, location_id, sub_location_id)
        try:
            content = await self._llm_service.generate_simple(
                prompt,
                model_override=settings.npc_tier_config.passerby_model,
            )
        except Exception:
            content = f"（{npc_profile.get('name', 'NPC')}似乎在思考...）"
        return {
            "content": content,
            "cache_hit": False,
        }

    async def _respond_chat(
        self,
        world_id: str,
        npc_id: str,
        query: str,
        location_id: str,
        conversation_history: Optional[list],
        model: str,
        thinking_level: Optional[str],
        history_limit: int,
    ) -> Dict[str, Any]:
        if not self._pro_service:
            return await self._respond_fast(query, {"name": npc_id}, location_id)

        from app.models.pro import SceneContext, ChatMessage, ChatRequest

        scene = SceneContext(
            description=f"在{location_id}",
            location=location_id,
            present_characters=[npc_id, "player"],
        )

        history = []
        if conversation_history:
            for msg in conversation_history[-history_limit:]:
                history.append(
                    ChatMessage(
                        role=msg.get("role", "user"),
                        content=msg.get("content", ""),
                    )
                )

        request = ChatRequest(
            message=query,
            scene=scene,
            conversation_history=history,
        )

        result = await self._pro_service.chat(
            world_id=world_id,
            character_id=npc_id,
            request=request,
            model_override=model,
            thinking_level=thinking_level,
            enable_tools=True,
        )

        return {
            "content": result.response,
            "recalled_memory": result.recalled_memory,
        }

    def _build_fast_prompt(
        self,
        query: str,
        npc_profile: Dict[str, Any],
        location_id: str,
        sub_location_id: Optional[str] = None,
    ) -> str:
        name = npc_profile.get("name", "NPC")
        personality = npc_profile.get("personality", "")
        speech_pattern = npc_profile.get("speech_pattern", "")
        occupation = npc_profile.get("occupation", "")
        appearance = npc_profile.get("appearance", "")
        shared_context = npc_profile.get("shared_context", "")

        location_hint = location_id
        if sub_location_id:
            location_hint = f"{location_id}/{sub_location_id}"

        prompt_parts = [
            f"你是{name}。",
            f"职业: {occupation}",
            f"性格: {personality}",
            f"说话方式: {speech_pattern}",
            f"外貌: {appearance}",
            "",
            f"当前地点: {location_hint}",
        ]

        if shared_context:
            prompt_parts.extend([
                "",
                "你听说的最近消息:",
                shared_context,
            ])

        prompt_parts.extend([
            "",
            f"玩家说: {query}",
            "",
            "请简短回应（1-2句话），保持角色特点:",
        ])

        return "\n".join([p for p in prompt_parts if p is not None])
