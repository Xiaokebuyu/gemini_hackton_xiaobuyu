"""
Game loop API routes.
"""
from typing import Optional
from fastapi import APIRouter, HTTPException

from app.models.game import (
    CombatResolveRequest,
    CombatResolveResponse,
    CombatStartRequest,
    CombatStartResponse,
    CreateSessionRequest,
    CreateSessionResponse,
    GameSessionState,
    UpdateSceneRequest,
    # Phase 7: Navigation models
    NavigateRequest,
    NavigateResponse,
    LocationResponse,
    GameTimeResponse,
)
from app.services.admin.admin_coordinator import AdminCoordinator


router = APIRouter()
admin = AdminCoordinator.get_instance()


@router.post("/game/{world_id}/sessions")
async def create_session(world_id: str, payload: CreateSessionRequest) -> CreateSessionResponse:
    """Create game session."""
    try:
        return await admin.create_session(world_id, payload)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/game/{world_id}/sessions/{session_id}")
async def get_session(world_id: str, session_id: str) -> GameSessionState:
    """Get game session."""
    session = await admin.get_session(world_id, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    return session


@router.post("/game/{world_id}/sessions/{session_id}/scene")
async def update_scene(
    world_id: str,
    session_id: str,
    payload: UpdateSceneRequest,
) -> GameSessionState:
    """Update scene state."""
    try:
        return await admin.update_scene(world_id, session_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/game/{world_id}/sessions/{session_id}/combat/start")
async def start_combat(
    world_id: str,
    session_id: str,
    payload: CombatStartRequest,
) -> CombatStartResponse:
    """Start combat."""
    try:
        return await admin.start_combat(world_id, session_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/game/{world_id}/sessions/{session_id}/combat/resolve")
async def resolve_combat(
    world_id: str,
    session_id: str,
    payload: CombatResolveRequest,
) -> CombatResolveResponse:
    """Resolve combat and dispatch event."""
    try:
        return await admin.resolve_combat(world_id, session_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ==================== Phase 7: 区域导航 API ====================


@router.get("/game/{world_id}/sessions/{session_id}/location")
async def get_location(world_id: str, session_id: str):
    """
    获取当前位置信息

    返回当前位置、可用目的地、在场NPC和时间信息。
    """
    try:
        result = await admin.get_current_location(world_id, session_id)
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/game/{world_id}/sessions/{session_id}/navigate")
async def navigate(
    world_id: str,
    session_id: str,
    payload: NavigateRequest,
):
    """
    导航到目标位置

    根据目的地名称或ID进行导航，返回旅途叙述和随机事件。
    时间会随旅途自动推进。
    """
    try:
        result = await admin.navigate(
            world_id=world_id,
            session_id=session_id,
            destination=payload.destination,
            direction=payload.direction,
        )
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error", "导航失败"))
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/game/{world_id}/sessions/{session_id}/time")
async def get_time(world_id: str, session_id: str):
    """获取当前游戏时间"""
    try:
        result = await admin.get_game_time(world_id, session_id)
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/game/{world_id}/sessions/{session_id}/time/advance")
async def advance_time(
    world_id: str,
    session_id: str,
    minutes: int = 30,
):
    """
    手动推进游戏时间

    Args:
        minutes: 推进的分钟数（默认30分钟）
    """
    try:
        result = await admin.advance_time(world_id, session_id, minutes)
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ==================== Module 2: 子地点导航 API ====================


from pydantic import BaseModel, Field
from typing import Dict, List


class EnterSubLocationRequest(BaseModel):
    """进入子地点请求"""
    sub_location_id: str


@router.post("/game/{world_id}/sessions/{session_id}/sub-location/enter")
async def enter_sub_location(
    world_id: str,
    session_id: str,
    payload: EnterSubLocationRequest,
):
    """
    进入当前地图的子地点

    进入一个子地点（如酒馆、公会、铁匠铺等），获取该子地点的详细描述和可交互选项。
    """
    try:
        result = await admin.enter_sub_location(
            world_id=world_id,
            session_id=session_id,
            sub_location_id=payload.sub_location_id,
        )
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error", "进入子地点失败"))
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/game/{world_id}/sessions/{session_id}/sub-location/leave")
async def leave_sub_location(
    world_id: str,
    session_id: str,
):
    """
    离开当前子地点

    离开子地点，回到地图层级。
    """
    try:
        result = await admin.leave_sub_location(
            world_id=world_id,
            session_id=session_id,
        )
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error", "离开子地点失败"))
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/game/{world_id}/sessions/{session_id}/sub-locations")
async def get_sub_locations(
    world_id: str,
    session_id: str,
):
    """
    获取当前地图的所有子地点

    返回当前位置可以进入的子地点列表。
    """
    try:
        location = await admin.get_current_location(world_id, session_id)
        if "error" in location:
            raise HTTPException(status_code=404, detail=location["error"])
        return {
            "location_id": location.get("location_id"),
            "location_name": location.get("location_name"),
            "current_sub_location": location.get("sub_location_id"),
            "available_sub_locations": location.get("available_sub_locations", []),
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ==================== Module 1: 主线章节系统 API ====================


@router.get("/game/{world_id}/sessions/{session_id}/narrative/progress")
async def get_narrative_progress(
    world_id: str,
    session_id: str,
):
    """
    获取当前叙事进度

    返回当前主线、章节、已完成的事件等信息。
    """
    try:
        progress = await admin.narrative_service.get_progress(world_id, session_id)
        chapter_info = admin.narrative_service.get_chapter_info(progress.current_chapter)

        return {
            "current_mainline": progress.current_mainline,
            "current_chapter": progress.current_chapter,
            "chapter_info": chapter_info,
            "objectives_completed": progress.objectives_completed,
            "events_triggered": progress.events_triggered,
            "chapters_completed": progress.chapters_completed,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/game/{world_id}/sessions/{session_id}/narrative/available-maps")
async def get_available_maps(
    world_id: str,
    session_id: str,
):
    """
    获取当前章节可用的地图

    返回已解锁可以前往的地图列表。
    """
    try:
        available_maps = await admin.narrative_service.get_available_maps(
            world_id, session_id
        )
        return {
            "available_maps": available_maps,
            "all_unlocked": "*" in available_maps,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


class TriggerEventRequest(BaseModel):
    """触发叙事事件请求"""
    event_id: str


@router.post("/game/{world_id}/sessions/{session_id}/narrative/trigger-event")
async def trigger_narrative_event(
    world_id: str,
    session_id: str,
    payload: TriggerEventRequest,
):
    """
    触发叙事事件

    可能导致章节完成和新地图解锁。
    """
    try:
        result = await admin.narrative_service.trigger_event(
            world_id, session_id, payload.event_id
        )
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ==================== Module 4: 路人NPC API ====================


@router.get("/game/{world_id}/sessions/{session_id}/passersby")
async def get_passersby(
    world_id: str,
    session_id: str,
):
    """
    获取当前位置的路人列表

    返回当前地图/子地点中的活跃路人NPC。
    """
    try:
        location = await admin.get_current_location(world_id, session_id)
        if "error" in location:
            raise HTTPException(status_code=404, detail=location["error"])

        map_id = location.get("location_id")
        sub_location_id = location.get("sub_location_id")

        passersby = await admin.passerby_service.get_active_passersby(
            world_id, map_id, sub_location_id
        )

        return {
            "location_id": map_id,
            "sub_location_id": sub_location_id,
            "passersby": passersby,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/game/{world_id}/sessions/{session_id}/passersby/spawn")
async def spawn_passerby(
    world_id: str,
    session_id: str,
):
    """
    生成一个路人NPC

    在当前位置生成一个路人NPC，可以进行对话。
    """
    try:
        location = await admin.get_current_location(world_id, session_id)
        if "error" in location:
            raise HTTPException(status_code=404, detail=location["error"])

        map_id = location.get("location_id")
        sub_location_id = location.get("sub_location_id")

        passerby = await admin.passerby_service.get_or_spawn_passerby(
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
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


class PasserbyDialogueRequest(BaseModel):
    """路人对话请求"""
    instance_id: str
    message: str


@router.post("/game/{world_id}/sessions/{session_id}/passersby/dialogue")
async def passerby_dialogue(
    world_id: str,
    session_id: str,
    payload: PasserbyDialogueRequest,
):
    """
    与路人对话

    使用FAST层AI进行快速响应，目标延迟<100ms。
    """
    try:
        location = await admin.get_current_location(world_id, session_id)
        if "error" in location:
            raise HTTPException(status_code=404, detail=location["error"])

        map_id = location.get("location_id")

        result = await admin.passerby_service.handle_passerby_dialogue(
            world_id, map_id, payload.instance_id, payload.message
        )

        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error", "对话失败"))

        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ==================== Phase 7: 增强的会话创建 ====================


class CreateGameSessionRequest(BaseModel):
    """创建游戏会话请求（增强版）"""
    user_id: str
    session_id: Optional[str] = None
    starting_location: Optional[str] = None
    starting_time: Optional[Dict[str, int]] = None  # {"day": 1, "hour": 8, "minute": 0}
    known_characters: List[str] = Field(default_factory=list)


@router.post("/game/{world_id}/sessions/start")
async def start_game_session(
    world_id: str,
    payload: CreateGameSessionRequest,
):
    """
    创建并启动游戏会话（增强版）

    自动初始化导航系统、时间系统，并返回起始位置信息。
    """
    try:
        # 使用 Admin 创建会话
        state = await admin.start_session(
            world_id=world_id,
            session_id=payload.session_id,
            participants=[payload.user_id],
            known_characters=payload.known_characters,
            starting_location=payload.starting_location,
            starting_time=payload.starting_time,
        )

        # 获取起始位置信息
        location_info = await admin.get_current_location(world_id, state.session_id)

        return {
            "session_id": state.session_id,
            "world_id": world_id,
            "phase": "idle",
            "location": location_info,
            "time": state.game_time.model_dump() if state.game_time else None,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
