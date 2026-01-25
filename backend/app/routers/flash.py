"""
Flash API routes for character memory.

提供两种模式的API：
1. 结构化模式：/ingest, /recall - 直接操作结构化数据
2. 自然语言模式：/ingest-natural, /recall-natural - LLM增强
"""
from fastapi import APIRouter, HTTPException

from app.models.flash import (
    EventIngestRequest,
    EventIngestResponse,
    NaturalEventIngestRequest,
    NaturalEventIngestResponse,
    NaturalRecallRequest,
    NaturalRecallResponse,
    RecallRequest,
    RecallResponse,
)
from app.services.flash_service import FlashService


router = APIRouter()
flash_service = FlashService()


@router.post("/flash/{world_id}/characters/{character_id}/ingest")
async def ingest_event(
    world_id: str,
    character_id: str,
    payload: EventIngestRequest,
) -> EventIngestResponse:
    """Ingest a structured event into character memory."""
    try:
        return await flash_service.ingest_event(world_id, character_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/flash/{world_id}/characters/{character_id}/recall")
async def recall_memory(
    world_id: str,
    character_id: str,
    payload: RecallRequest,
) -> RecallResponse:
    """Recall memory for a character."""
    try:
        return await flash_service.recall_memory(world_id, character_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ==================== LLM增强的自然语言端点 ====================


@router.post("/flash/{world_id}/characters/{character_id}/ingest-natural")
async def ingest_event_natural(
    world_id: str,
    character_id: str,
    payload: NaturalEventIngestRequest,
) -> NaturalEventIngestResponse:
    """
    LLM增强的事件摄入：将自然语言事件描述编码为结构化记忆。

    这个端点会：
    1. 获取角色profile和已有节点
    2. 使用LLM将事件描述编码为节点和边
    3. 写入角色的记忆图谱

    适合场景：
    - GM描述发生的事件
    - 角色经历某事需要记住
    """
    try:
        return await flash_service.ingest_event_natural(world_id, character_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/flash/{world_id}/characters/{character_id}/recall-natural")
async def recall_memory_natural(
    world_id: str,
    character_id: str,
    payload: NaturalRecallRequest,
) -> NaturalRecallResponse:
    """
    LLM增强的记忆检索：用自然语言请求记忆，返回角色视角的回忆。

    这个端点会：
    1. 使用LLM理解查询，找到相关的种子节点
    2. 执行激活扩散，找到关联的记忆
    3. （可选）将子图翻译为角色视角的自然语言

    适合场景：
    - Pro需要回忆某件事
    - 查询角色对某人/某事的记忆
    """
    try:
        return await flash_service.recall_memory_natural(world_id, character_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
