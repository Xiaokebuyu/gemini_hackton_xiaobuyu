"""
Unified Game V2 API routes (Flash-Only).
"""
import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.dependencies import get_coordinator
from app.models.admin_protocol import CoordinatorResponse
from app.models.event import (
    GMEventIngestRequest,
    GMEventIngestResponse,
    NaturalEventIngestRequest,
    NaturalEventIngestResponse,
)
from app.models.game import (
    CombatActionRequest,
    CombatActionResponse,
    CombatResolveRequest,
    CombatResolveResponse,
    CombatStartRequest,
    CombatStartResponse,
    EnterSceneRequest,
    EnterSceneResponse,
    GameContextResponse,
    GamePhase,
    GameSessionState,
    NavigateRequest,
    PlayerInputRequest,
    PlayerInputResponse,
    StartDialogueRequest,
    StartDialogueResponse,
    TriggerCombatRequest,
    TriggerCombatResponse,
)
from app.services.admin.admin_coordinator import AdminCoordinator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/game", tags=["Game V2"])


@router.get("/worlds")
async def list_worlds(coordinator=Depends(get_coordinator)):
    """列出所有可用世界"""
    worlds = await coordinator.list_worlds()
    return {"worlds": worlds}


class CreateGameSessionRequest(BaseModel):
    """创建游戏会话请求（统一版）"""

    user_id: str
    session_id: Optional[str] = None
    participants: Optional[List[str]] = None
    starting_location: Optional[str] = None
    starting_time: Optional[Dict[str, int]] = None  # {"day": 1, "hour": 8, "minute": 0}
    known_characters: List[str] = Field(default_factory=list)
    character_locations: Dict[str, str] = Field(default_factory=dict)


class EnterSubLocationRequest(BaseModel):
    """进入子地点请求"""

    sub_location_id: str


class CreatePartyRequest(BaseModel):
    """创建队伍请求"""

    leader_id: str = "player"


class AddTeammateRequest(BaseModel):
    """添加队友请求"""

    character_id: str
    name: str
    role: str = "support"
    personality: str = ""
    response_tendency: float = 0.5


class LoadTeammatesRequest(BaseModel):
    """加载预设队友请求"""

    teammates: List[Dict[str, Any]] = Field(default_factory=list)


class RecoverableSessionItem(BaseModel):
    """可恢复会话摘要"""

    session_id: str
    world_id: str
    status: str
    updated_at: datetime
    participants: List[str] = Field(default_factory=list)
    player_location: Optional[str] = None
    chapter_id: Optional[str] = None
    sub_location: Optional[str] = None
    party_member_count: int = 0
    party_members: List[str] = Field(default_factory=list)


class RecoverableSessionsResponse(BaseModel):
    """可恢复会话列表响应"""

    world_id: str
    user_id: str
    sessions: List[RecoverableSessionItem] = Field(default_factory=list)


@router.post("/{world_id}/sessions")
async def start_session(
    world_id: str,
    payload: CreateGameSessionRequest,
    coordinator: AdminCoordinator = Depends(get_coordinator),
):
    """
    创建并启动游戏会话（统一入口）
    """
    try:
        participants = payload.participants or [payload.user_id]
        if payload.user_id not in participants:
            participants.append(payload.user_id)

        state = await coordinator.start_session(
            world_id=world_id,
            session_id=payload.session_id,
            participants=participants,
            known_characters=payload.known_characters,
            character_locations=payload.character_locations,
            starting_location=payload.starting_location,
            starting_time=payload.starting_time,
        )
        location_info = await coordinator.get_current_location(world_id, state.session_id)

        # 生成开场叙述
        opening_narration = ""
        try:
            opening_narration = await coordinator.generate_opening_narration(
                world_id, state.session_id,
            )
        except Exception as exc:
            logger.warning("开场叙述生成失败: %s", exc)

        return {
            "session_id": state.session_id,
            "world_id": world_id,
            "phase": "idle",
            "location": location_info,
            "time": state.game_time.model_dump() if state.game_time else None,
            "opening_narration": opening_narration,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/{world_id}/sessions")
async def list_sessions(
    world_id: str,
    user_id: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=100),
    coordinator: AdminCoordinator = Depends(get_coordinator),
) -> RecoverableSessionsResponse:
    """列出用户在该世界可恢复的会话"""
    try:
        sessions = await coordinator.list_recoverable_sessions(
            world_id=world_id,
            user_id=user_id,
            limit=limit,
        )
        return RecoverableSessionsResponse(
            world_id=world_id,
            user_id=user_id,
            sessions=[RecoverableSessionItem(**item) for item in sessions],
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/{world_id}/sessions/{session_id}")
async def get_session(
    world_id: str,
    session_id: str,
    coordinator: AdminCoordinator = Depends(get_coordinator),
) -> GameSessionState:
    """获取会话"""
    session = await coordinator.get_session(world_id, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    return session


class ResumeSessionRequest(BaseModel):
    """恢复会话请求"""

    generate_narration: bool = True


@router.post("/{world_id}/sessions/{session_id}/resume")
async def resume_session(
    world_id: str,
    session_id: str,
    payload: Optional[ResumeSessionRequest] = None,
    coordinator: AdminCoordinator = Depends(get_coordinator),
):
    """恢复游戏会话状态"""
    try:
        generate_narration = payload.generate_narration if payload else True
        result = await coordinator.resume_session(
            world_id=world_id,
            session_id=session_id,
            generate_narration=generate_narration,
        )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/{world_id}/sessions/{session_id}/context")
async def get_context(
    world_id: str,
    session_id: str,
    coordinator: AdminCoordinator = Depends(get_coordinator),
) -> GameContextResponse:
    """获取游戏上下文"""
    context = await coordinator.get_context_async(world_id, session_id)
    if not context:
        raise HTTPException(status_code=404, detail="Session not found")
    return GameContextResponse(
        world_id=context.world_id,
        session_id=context.session_id,
        phase=GamePhase(context.phase.value),
        game_day=context.game_day,
        current_scene=context.current_scene,
        current_npc=context.current_npc,
        known_characters=context.known_characters,
    )


@router.post("/{world_id}/sessions/{session_id}/scene")
async def enter_scene(
    world_id: str,
    session_id: str,
    payload: EnterSceneRequest,
    coordinator: AdminCoordinator = Depends(get_coordinator),
) -> EnterSceneResponse:
    """进入场景（统一入口）"""
    try:
        result = await coordinator.enter_scene(
            world_id=world_id,
            session_id=session_id,
            scene=payload.scene,
            generate_description=payload.generate_description,
        )
        return EnterSceneResponse(
            scene=result["scene"],
            description=result.get("description", ""),
            npc_memories=result.get("npc_memories", {}),
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/{world_id}/sessions/{session_id}/input")
async def process_input_v2(
    world_id: str,
    session_id: str,
    payload: PlayerInputRequest,
    coordinator: AdminCoordinator = Depends(get_coordinator),
) -> CoordinatorResponse:
    """处理玩家输入（Flash-Only v2）"""
    try:
        return await asyncio.wait_for(
            coordinator.process_player_input_v2(
                world_id=world_id,
                session_id=session_id,
                player_input=payload.input,
            ),
            timeout=110.0,
        )
    except asyncio.TimeoutError:
        logger.error("[input] 请求处理超时(110s)")
        raise HTTPException(status_code=504, detail="请求处理超时，请稍后再试")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("[input] 处理失败: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/{world_id}/sessions/{session_id}/location")
async def get_location(
    world_id: str,
    session_id: str,
    coordinator: AdminCoordinator = Depends(get_coordinator),
):
    """获取当前位置信息"""
    try:
        result = await coordinator.get_current_location(world_id, session_id)
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/{world_id}/sessions/{session_id}/navigate")
async def navigate(
    world_id: str,
    session_id: str,
    payload: NavigateRequest,
    coordinator: AdminCoordinator = Depends(get_coordinator),
):
    """导航到目标位置"""
    try:
        result = await coordinator.navigate(
            world_id=world_id,
            session_id=session_id,
            destination=payload.destination,
            direction=payload.direction,
        )
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error", "导航失败"))
        narration = await coordinator.narrate_state_change(
            world_id,
            session_id,
            change_type="navigation",
            change_details={
                "to": (result.get("new_location") or {}).get("location_name"),
                "segments": result.get("segments", []),
            },
        )
        result["narration"] = narration
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/{world_id}/sessions/{session_id}/time")
async def get_time(
    world_id: str,
    session_id: str,
    coordinator: AdminCoordinator = Depends(get_coordinator),
):
    """获取当前游戏时间"""
    try:
        result = await coordinator.get_game_time(world_id, session_id)
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


class AdvanceTimeRequest(BaseModel):
    """推进时间请求"""

    minutes: int = 30


@router.post("/{world_id}/sessions/{session_id}/time/advance")
async def advance_time(
    world_id: str,
    session_id: str,
    payload: AdvanceTimeRequest,
    coordinator: AdminCoordinator = Depends(get_coordinator),
):
    """手动推进游戏时间"""
    try:
        result = await coordinator.advance_time(world_id, session_id, payload.minutes)
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        narration = await coordinator.narrate_state_change(
            world_id,
            session_id,
            change_type="time_advance",
            change_details={"minutes": payload.minutes, "events": result.get("events", [])},
        )
        result["narration"] = narration
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/{world_id}/sessions/{session_id}/sub-location/enter")
async def enter_sub_location(
    world_id: str,
    session_id: str,
    payload: EnterSubLocationRequest,
    coordinator: AdminCoordinator = Depends(get_coordinator),
):
    """进入子地点"""
    try:
        result = await coordinator.enter_sub_location(
            world_id=world_id,
            session_id=session_id,
            sub_location_id=payload.sub_location_id,
        )
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error", "进入子地点失败"))
        narration = await coordinator.narrate_state_change(
            world_id,
            session_id,
            change_type="sub_location_enter",
            change_details={
                "name": (result.get("sub_location") or {}).get("name", payload.sub_location_id),
            },
        )
        result["narration"] = narration
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/{world_id}/sessions/{session_id}/sub-location/leave")
async def leave_sub_location(
    world_id: str,
    session_id: str,
    coordinator: AdminCoordinator = Depends(get_coordinator),
):
    """离开子地点"""
    try:
        result = await coordinator.leave_sub_location(
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


@router.get("/{world_id}/sessions/{session_id}/sub-locations")
async def get_sub_locations(
    world_id: str,
    session_id: str,
    coordinator: AdminCoordinator = Depends(get_coordinator),
):
    """获取当前地图的所有子地点"""
    try:
        location = await coordinator.get_current_location(world_id, session_id)
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


@router.post("/{world_id}/sessions/{session_id}/dialogue/start")
async def start_dialogue(
    world_id: str,
    session_id: str,
    payload: StartDialogueRequest,
    coordinator: AdminCoordinator = Depends(get_coordinator),
) -> StartDialogueResponse:
    """开始与NPC对话"""
    try:
        result = await coordinator.start_dialogue(
            world_id=world_id,
            session_id=session_id,
            npc_id=payload.npc_id,
        )
        if result.get("type") == "error":
            raise HTTPException(status_code=400, detail=result.get("response"))
        narration = await coordinator.narrate_state_change(
            world_id,
            session_id,
            change_type="dialogue_start",
            change_details={"npc_name": result.get("speaker", payload.npc_id)},
        )
        return StartDialogueResponse(
            npc_id=payload.npc_id,
            npc_name=result.get("speaker", payload.npc_id),
            greeting=result.get("response", ""),
            narration=narration,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/{world_id}/sessions/{session_id}/dialogue/end")
async def end_dialogue(
    world_id: str,
    session_id: str,
    coordinator: AdminCoordinator = Depends(get_coordinator),
) -> PlayerInputResponse:
    """结束当前对话"""
    try:
        result = await coordinator.end_dialogue(world_id, session_id)
        return PlayerInputResponse(
            type=result.get("type", "system"),
            response=result.get("response", ""),
            speaker=result.get("speaker", "系统"),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/{world_id}/sessions/{session_id}/combat/trigger")
async def trigger_combat(
    world_id: str,
    session_id: str,
    payload: TriggerCombatRequest,
    coordinator: AdminCoordinator = Depends(get_coordinator),
) -> TriggerCombatResponse:
    """触发战斗"""
    try:
        result = await coordinator.trigger_combat(
            world_id=world_id,
            session_id=session_id,
            enemies=payload.enemies,
            player_state=payload.player_state,
            combat_description=payload.combat_description,
            environment=payload.environment,
        )
        if result.get("type") == "error":
            raise HTTPException(status_code=400, detail=result.get("response"))
        return TriggerCombatResponse(
            combat_id=result.get("combat_id", ""),
            narration=result.get("narration", ""),
            combat_state=result.get("combat_state", {}),
            available_actions=[
                a.model_dump() if hasattr(a, "model_dump") else a
                for a in result.get("available_actions", [])
            ],
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/{world_id}/sessions/{session_id}/combat/action")
async def execute_combat_action(
    world_id: str,
    session_id: str,
    payload: CombatActionRequest,
    coordinator: AdminCoordinator = Depends(get_coordinator),
) -> CombatActionResponse:
    """执行战斗行动"""
    try:
        result = await coordinator.execute_combat_action(
            world_id=world_id,
            session_id=session_id,
            action_id=payload.action_id,
        )
        if result.get("type") == "error":
            raise HTTPException(status_code=400, detail=result.get("response"))
        return CombatActionResponse(
            phase=result.get("phase", "action"),
            narration=result.get("narration", ""),
            action_result=result.get("action_result"),
            combat_result=result.get("result"),
            available_actions=[
                a.model_dump() if hasattr(a, "model_dump") else a
                for a in result.get("available_actions", [])
            ],
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/{world_id}/sessions/{session_id}/combat/start")
async def start_combat(
    world_id: str,
    session_id: str,
    payload: CombatStartRequest,
    coordinator: AdminCoordinator = Depends(get_coordinator),
) -> CombatStartResponse:
    """战斗初始化（兼容）"""
    try:
        return await coordinator.start_combat(world_id, session_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/{world_id}/sessions/{session_id}/combat/resolve")
async def resolve_combat(
    world_id: str,
    session_id: str,
    payload: CombatResolveRequest,
    coordinator: AdminCoordinator = Depends(get_coordinator),
) -> CombatResolveResponse:
    """战斗结算（兼容）"""
    try:
        return await coordinator.resolve_combat(world_id, session_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/{world_id}/sessions/{session_id}/advance-day")
async def advance_day(
    world_id: str,
    session_id: str,
    coordinator: AdminCoordinator = Depends(get_coordinator),
) -> PlayerInputResponse:
    """推进游戏日"""
    try:
        result = await coordinator.advance_day(world_id, session_id)
        narration = await coordinator.narrate_state_change(
            world_id,
            session_id,
            change_type="day_advance",
            change_details={"day": result.get("game_day")},
        )
        return PlayerInputResponse(
            type=result.get("type", "system"),
            response=result.get("response", ""),
            speaker="系统",
            state_changes={"game_day": result.get("game_day")},
            narration=narration,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/{world_id}/sessions/{session_id}/party")
async def create_party(
    world_id: str,
    session_id: str,
    payload: CreatePartyRequest,
    coordinator: AdminCoordinator = Depends(get_coordinator),
):
    """创建队伍"""
    try:
        return await coordinator.create_party(
            world_id=world_id,
            session_id=session_id,
            leader_id=payload.leader_id,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/{world_id}/sessions/{session_id}/party")
async def get_party_info(
    world_id: str,
    session_id: str,
    coordinator: AdminCoordinator = Depends(get_coordinator),
):
    """获取队伍信息"""
    try:
        return await coordinator.get_party_info(world_id, session_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/{world_id}/sessions/{session_id}/party/add")
async def add_teammate(
    world_id: str,
    session_id: str,
    payload: AddTeammateRequest,
    coordinator: AdminCoordinator = Depends(get_coordinator),
):
    """添加队友"""
    try:
        return await coordinator.add_teammate(
            world_id=world_id,
            session_id=session_id,
            character_id=payload.character_id,
            name=payload.name,
            role=payload.role,
            personality=payload.personality,
            response_tendency=payload.response_tendency,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/{world_id}/sessions/{session_id}/party/{character_id}")
async def remove_teammate(
    world_id: str,
    session_id: str,
    character_id: str,
    coordinator: AdminCoordinator = Depends(get_coordinator),
):
    """移除队友"""
    try:
        return await coordinator.remove_teammate(world_id, session_id, character_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/{world_id}/sessions/{session_id}/party/load")
async def load_predefined_teammates(
    world_id: str,
    session_id: str,
    payload: LoadTeammatesRequest,
    coordinator: AdminCoordinator = Depends(get_coordinator),
):
    """加载预定义队友"""
    try:
        return await coordinator.load_predefined_teammates(
            world_id=world_id,
            session_id=session_id,
            teammate_configs=payload.teammates,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


class TriggerEventRequest(BaseModel):
    """触发叙事事件请求"""

    event_id: str


@router.get("/{world_id}/sessions/{session_id}/narrative/progress")
async def get_narrative_progress(
    world_id: str,
    session_id: str,
    coordinator: AdminCoordinator = Depends(get_coordinator),
):
    """获取当前叙事进度"""
    try:
        progress = await coordinator.narrative_service.get_progress(world_id, session_id)
        chapter_info = coordinator.narrative_service.get_chapter_info(
            world_id, progress.current_chapter,
        )
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


@router.get("/{world_id}/sessions/{session_id}/narrative/flow-board")
async def get_narrative_flow_board(
    world_id: str,
    session_id: str,
    lookahead: int = Query(3, ge=1, le=8),
    coordinator: AdminCoordinator = Depends(get_coordinator),
):
    """获取基于世界书主线的流程编排板"""
    try:
        return await coordinator.narrative_service.get_flow_board(
            world_id=world_id,
            session_id=session_id,
            lookahead=lookahead,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/{world_id}/sessions/{session_id}/narrative/current-plan")
async def get_narrative_current_plan(
    world_id: str,
    session_id: str,
    coordinator: AdminCoordinator = Depends(get_coordinator),
):
    """获取当前章节内容编排建议"""
    try:
        return await coordinator.narrative_service.get_current_chapter_plan(
            world_id=world_id,
            session_id=session_id,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/{world_id}/sessions/{session_id}/narrative/available-maps")
async def get_available_maps(
    world_id: str,
    session_id: str,
    coordinator: AdminCoordinator = Depends(get_coordinator),
):
    """获取当前章节可用的地图"""
    try:
        available_maps = await coordinator.narrative_service.get_available_maps(
            world_id, session_id
        )
        return {
            "available_maps": available_maps,
            "all_unlocked": "*" in available_maps,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/{world_id}/sessions/{session_id}/narrative/trigger-event")
async def trigger_narrative_event(
    world_id: str,
    session_id: str,
    payload: TriggerEventRequest,
    coordinator: AdminCoordinator = Depends(get_coordinator),
):
    """触发叙事事件"""
    try:
        return await coordinator.narrative_service.trigger_event(
            world_id, session_id, payload.event_id
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


class PasserbyDialogueRequest(BaseModel):
    """路人对话请求"""

    instance_id: str
    message: str


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
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


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
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


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
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/{world_id}/events/ingest")
async def ingest_event(
    world_id: str,
    payload: GMEventIngestRequest,
    coordinator: AdminCoordinator = Depends(get_coordinator),
) -> GMEventIngestResponse:
    """GM 结构化事件摄入"""
    try:
        return await coordinator.ingest_event(world_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/{world_id}/events/ingest-natural")
async def ingest_event_natural(
    world_id: str,
    payload: NaturalEventIngestRequest,
    coordinator: AdminCoordinator = Depends(get_coordinator),
) -> NaturalEventIngestResponse:
    """GM 自然语言事件摄入"""
    try:
        return await coordinator.ingest_event_natural(world_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/{world_id}/sessions/{session_id}/history")
async def get_session_history(
    world_id: str,
    session_id: str,
    limit: int = Query(50, ge=1, le=200),
    coordinator: AdminCoordinator = Depends(get_coordinator),
):
    """获取会话聊天历史"""
    try:
        messages = await coordinator.get_session_history(world_id, session_id, limit)
        return {"messages": messages}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
