"""
Pro API routes - 角色意识层API

提供：
1. 角色资料管理
2. 上下文组装
3. 对话接口（Phase 4核心功能）
"""
from fastapi import APIRouter, HTTPException

from app.models.pro import (
    CharacterProfile,
    ChatRequest,
    ChatResponse,
    ProContextRequest,
    ProContextResponse,
)
from app.services.pro_service import ProService


router = APIRouter()
pro_service = ProService()


@router.get("/pro/{world_id}/characters/{character_id}/profile")
async def get_profile(world_id: str, character_id: str) -> CharacterProfile:
    """Get character profile."""
    return await pro_service.get_profile(world_id, character_id)


@router.put("/pro/{world_id}/characters/{character_id}/profile")
async def set_profile(
    world_id: str,
    character_id: str,
    payload: CharacterProfile,
) -> CharacterProfile:
    """Set character profile."""
    try:
        return await pro_service.set_profile(world_id, character_id, payload)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/pro/{world_id}/characters/{character_id}/context")
async def build_context(
    world_id: str,
    character_id: str,
    payload: ProContextRequest,
) -> ProContextResponse:
    """Build Pro context."""
    try:
        return await pro_service.build_context(world_id, character_id, payload)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ==================== 对话接口（Phase 4核心功能）====================


@router.post("/pro/{world_id}/characters/{character_id}/chat")
async def chat(
    world_id: str,
    character_id: str,
    payload: ChatRequest,
) -> ChatResponse:
    """
    与角色对话

    这是Phase 4的核心功能：Pro（意识层）能在对话中按需调用Flash（潜意识）获取记忆。

    流程：
    1. 用户发送消息
    2. Pro分析是否需要记忆
    3. 如需要，自动调用recall_memory工具
    4. Flash返回记忆
    5. Pro生成最终回复

    请求体：
    - message: 用户消息
    - scene: 可选的场景上下文
    - conversation_history: 对话历史
    - injected_memory: 预注入的记忆（场景加载时）

    响应：
    - response: 角色的回复
    - tool_called: 是否调用了记忆工具
    - recalled_memory: 如果调用了工具，返回的记忆内容
    - recall_query: 记忆查询内容
    """
    try:
        return await pro_service.chat(world_id, character_id, payload)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
