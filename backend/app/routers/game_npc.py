"""NPC interaction API routes (interact, passerby, private-chat)."""
import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.dependencies import get_coordinator
from app.models.admin_protocol import InteractRequest
from app.routers._common import CATCHABLE_EXCEPTIONS, map_exception_to_http
from app.services.admin.admin_coordinator import AdminCoordinator
from app.exceptions import MCPServiceUnavailableError

logger = logging.getLogger(__name__)

router = APIRouter(tags=["NPC"])


class PrivateChatRequest(BaseModel):
    """私聊请求"""

    target_character_id: str
    input: str


class PasserbyDialogueRequest(BaseModel):
    """路人对话请求"""

    instance_id: str
    message: str


@router.post("/{world_id}/sessions/{session_id}/interact/stream")
async def interact_stream(
    world_id: str,
    session_id: str,
    payload: InteractRequest,
    coordinator: AdminCoordinator = Depends(get_coordinator),
):
    """NPC 直接交互流式端点（SSE）"""

    async def event_generator():
        try:
            async for event in coordinator.process_interact_stream(
                world_id=world_id,
                session_id=session_id,
                npc_id=payload.npc_id,
                player_input=payload.input,
            ):
                yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
        except asyncio.CancelledError:
            logger.debug("[interact/stream] 客户端断开 world=%s session=%s", world_id, session_id)
            raise
        except CATCHABLE_EXCEPTIONS as exc:
            logger.exception("[interact/stream] 流式处理失败: %s", exc)
            http_exc = map_exception_to_http(exc)
            error_event = {"type": "error", "error": str(exc), "status_code": http_exc.status_code}
            yield f"data: {json.dumps(error_event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{world_id}/sessions/{session_id}/passersby")
async def get_passersby(
    world_id: str,
    session_id: str,
    coordinator: AdminCoordinator = Depends(get_coordinator),
):
    """获取当前位置的路人列表"""
    try:
        location = await coordinator.get_current_location(world_id, session_id)
        if "error" in location:
            raise HTTPException(status_code=404, detail=location["error"])

        map_id = location.get("location_id")
        sub_location_id = location.get("sub_location_id")

        passersby = await coordinator.passerby_service.get_active_passersby(
            world_id, map_id, sub_location_id
        )

        return {
            "location_id": map_id,
            "sub_location_id": sub_location_id,
            "passersby": passersby,
        }
    except HTTPException:
        raise
    except CATCHABLE_EXCEPTIONS as exc:
        raise map_exception_to_http(exc) from exc


@router.post("/{world_id}/sessions/{session_id}/passersby/spawn")
async def spawn_passerby(
    world_id: str,
    session_id: str,
    coordinator: AdminCoordinator = Depends(get_coordinator),
):
    """生成一个路人NPC"""
    try:
        location = await coordinator.get_current_location(world_id, session_id)
        if "error" in location:
            raise HTTPException(status_code=404, detail=location["error"])

        map_id = location.get("location_id")
        sub_location_id = location.get("sub_location_id")

        passerby = await coordinator.passerby_service.get_or_spawn_passerby(
            world_id, map_id, sub_location_id
        )

        return {
            "success": True,
            "passerby": {
                "instance_id": passerby.instance_id,
                "name": passerby.name,
                "appearance": passerby.appearance,
                "mood": passerby.mood,
            },
        }
    except HTTPException:
        raise
    except CATCHABLE_EXCEPTIONS as exc:
        raise map_exception_to_http(exc) from exc


@router.post("/{world_id}/sessions/{session_id}/passersby/dialogue")
async def passerby_dialogue(
    world_id: str,
    session_id: str,
    payload: PasserbyDialogueRequest,
    coordinator: AdminCoordinator = Depends(get_coordinator),
):
    """与路人对话"""
    try:
        location = await coordinator.get_current_location(world_id, session_id)
        if "error" in location:
            raise HTTPException(status_code=404, detail=location["error"])

        map_id = location.get("location_id")

        result = await coordinator.passerby_service.handle_passerby_dialogue(
            world_id, map_id, payload.instance_id, payload.message
        )

        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error", "对话失败"))

        return result
    except HTTPException:
        raise
    except CATCHABLE_EXCEPTIONS as exc:
        raise map_exception_to_http(exc) from exc


@router.post("/{world_id}/sessions/{session_id}/private-chat/stream")
async def private_chat_stream(
    world_id: str,
    session_id: str,
    payload: PrivateChatRequest,
    coordinator: AdminCoordinator = Depends(get_coordinator),
):
    """私聊流式端点 - 直接与角色对话，跳过GM叙述"""

    async def event_generator():
        try:
            async for event in coordinator.process_private_chat_stream(
                world_id=world_id,
                session_id=session_id,
                target_character_id=payload.target_character_id,
                player_input=payload.input,
            ):
                yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
        except asyncio.CancelledError:
            logger.debug("[private-chat/stream] 客户端断开，终止流 world=%s session=%s", world_id, session_id)
            raise
        except CATCHABLE_EXCEPTIONS as exc:
            logger.exception("[private-chat/stream] 私聊流式处理失败: %s", exc)
            http_exc = map_exception_to_http(exc)
            error_event = {
                "type": "error",
                "error": str(exc),
                "status_code": http_exc.status_code,
            }
            yield f"data: {json.dumps(error_event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
