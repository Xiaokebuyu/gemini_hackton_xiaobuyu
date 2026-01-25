"""
Pro service - 角色意识层服务

支持：
1. 角色资料管理
2. 上下文组装
3. LLM对话（带记忆工具调用）
"""
from typing import Any, Dict, List, Optional

from app.models.pro import (
    CharacterProfile,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ProContextRequest,
    ProContextResponse,
    SceneContext,
)
from app.services.flash_service import FlashService
from app.services.graph_store import GraphStore
from app.services.pro_context_builder import ProContextBuilder


class ProService:
    """Pro service for character consciousness layer."""

    def __init__(
        self,
        graph_store: Optional[GraphStore] = None,
        flash_service: Optional[FlashService] = None,
        context_builder: Optional[ProContextBuilder] = None,
    ) -> None:
        self.graph_store = graph_store or GraphStore()
        self.flash_service = flash_service or FlashService(self.graph_store)
        self.context_builder = context_builder or ProContextBuilder()
        self._llm_service: Optional["ProLLMService"] = None

    @property
    def llm_service(self) -> "ProLLMService":
        """懒加载LLM服务"""
        if self._llm_service is None:
            from app.services.pro_llm_service import ProLLMService
            self._llm_service = ProLLMService(flash_service=self.flash_service)
        return self._llm_service

    async def get_profile(self, world_id: str, character_id: str) -> CharacterProfile:
        data = await self.graph_store.get_character_profile(world_id, character_id)
        return CharacterProfile(**data) if data else CharacterProfile()

    async def set_profile(
        self,
        world_id: str,
        character_id: str,
        profile: CharacterProfile,
        merge: bool = True,
    ) -> CharacterProfile:
        await self.graph_store.set_character_profile(
            world_id,
            character_id,
            profile.model_dump(),
            merge=merge,
        )
        return profile

    async def build_context(
        self,
        world_id: str,
        character_id: str,
        request: ProContextRequest,
    ) -> ProContextResponse:
        profile = await self.get_profile(world_id, character_id)
        state = await self.graph_store.get_character_state(world_id, character_id)

        memory = None
        if request.recall:
            memory = await self.flash_service.recall_memory(world_id, character_id, request.recall)

        prompt = None
        if request.include_prompt:
            prompt = self.context_builder.build_prompt(
                profile=profile,
                state=state,
                scene=request.scene,
                memory=memory,
                recent_conversation=request.recent_conversation,
            )

        return ProContextResponse(
            profile=profile,
            state=state,
            scene=request.scene,
            memory=memory,
            assembled_prompt=prompt,
        )

    # ==================== LLM对话方法 ====================

    async def chat(
        self,
        world_id: str,
        character_id: str,
        request: ChatRequest,
    ) -> ChatResponse:
        """
        与角色对话

        这是Phase 4的核心功能：Pro能在对话中按需调用Flash获取记忆

        Args:
            world_id: 世界ID
            character_id: 角色ID
            request: 对话请求

        Returns:
            对话响应，包含角色回复和可能的记忆检索信息
        """
        # 获取角色资料和状态
        profile = await self.get_profile(world_id, character_id)
        state = await self.graph_store.get_character_state(world_id, character_id) or {}

        # 调用LLM服务进行对话
        result = await self.llm_service.chat(
            world_id=world_id,
            character_id=character_id,
            user_message=request.message,
            profile=profile,
            state=state,
            scene=request.scene,
            conversation_history=request.conversation_history,
            injected_memory=request.injected_memory,
        )

        return ChatResponse(
            response=result["response"],
            tool_called=result.get("tool_called", False),
            recalled_memory=result.get("recalled_memory"),
            recall_query=result.get("recall_query"),
            thinking=result.get("thinking"),
        )

    async def chat_simple(
        self,
        world_id: str,
        character_id: str,
        message: str,
        scene: Optional[SceneContext] = None,
    ) -> str:
        """
        简化的对话接口

        Args:
            world_id: 世界ID
            character_id: 角色ID
            message: 用户消息
            scene: 可选的场景上下文

        Returns:
            角色的回复文本
        """
        request = ChatRequest(message=message, scene=scene)
        response = await self.chat(world_id, character_id, request)
        return response.response
