"""
Unified Game V2 API routes (Flash-Only).
"""
import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.dependencies import get_coordinator
from app.models.admin_protocol import CoordinatorResponse, InteractRequest
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
    DiceRollAPIRequest,
    GameContextResponse,
    GamePhase,
    GameSessionState,
    PlayerInputRequest,
    TriggerCombatRequest,
    TriggerCombatResponse,
)
from app.services.admin.admin_coordinator import AdminCoordinator
from app.services.mcp_client_pool import MCPServiceUnavailableError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/game", tags=["Game V2"])


def _validate_fixed_world(world_id: Optional[str] = None) -> None:
    """Enforce single world deployment in this environment."""
    from app.config import settings

    if world_id is None:
        return
    expected = settings.fixed_world_id
    if world_id != expected:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported world_id='{world_id}', this environment only supports '{expected}'",
        )


router.dependencies.append(Depends(_validate_fixed_world))


def _map_exception_to_http(exc: Exception) -> HTTPException:
    if isinstance(exc, MCPServiceUnavailableError):
        return HTTPException(status_code=503, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


@router.get("/worlds")
async def list_worlds(coordinator=Depends(get_coordinator)):
    """列出所有可用世界"""
    worlds = await coordinator.list_worlds()
    return {"worlds": worlds}


@router.get("/agentic-trace-viewer", response_class=HTMLResponse)
async def get_agentic_trace_viewer():
    """GM agentic trace 可视化调试页。"""
    viewer_path = Path(__file__).resolve().parent.parent / "static" / "agentic_trace_viewer.html"
    try:
        return HTMLResponse(content=viewer_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"trace viewer load failed: {exc}") from exc


class CreateGameSessionRequest(BaseModel):
    """创建游戏会话请求（统一版）"""

    user_id: str
    session_id: Optional[str] = None
    participants: Optional[List[str]] = None
    starting_location: Optional[str] = None
    starting_time: Optional[Dict[str, int]] = None  # {"day": 1, "hour": 8, "minute": 0}
    known_characters: List[str] = Field(default_factory=list)
    character_locations: Dict[str, str] = Field(default_factory=dict)



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
    needs_character_creation: bool = False


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

        # 检查是否已有角色（恢复/重建场景）
        player_char = await coordinator.character_store.get_character(world_id, state.session_id)
        if player_char:
            # 已有角色，直接生成开场叙述
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
                "phase": "active",
                "location": location_info,
                "time": state.game_time.model_dump() if state.game_time else None,
                "opening_narration": opening_narration,
            }
        else:
            # 无角色，进入角色创建阶段（不生成叙述）
            return {
                "session_id": state.session_id,
                "world_id": world_id,
                "phase": "character_creation",
                "location": location_info,
                "time": state.game_time.model_dump() if state.game_time else None,
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


# ==================== Character Creation ====================


@router.get("/{world_id}/character-creation/options")
async def get_character_creation_options(
    world_id: str,
    coordinator: AdminCoordinator = Depends(get_coordinator),
):
    """获取角色创建选项（种族、职业、背景、技能、点数购买规则）"""
    try:
        config = await coordinator.character_store.get_creation_config(world_id)
        if not config:
            raise HTTPException(status_code=404, detail="Character creation config not found for this world")
        return config
    except HTTPException:
        raise
    except Exception as exc:
        raise _map_exception_to_http(exc) from exc


class CharacterCreateRequest(BaseModel):
    """角色创建请求"""

    name: str
    race: str
    character_class: str
    background: str = ""
    ability_scores: Dict[str, int]
    skill_proficiencies: List[str] = Field(default_factory=list)
    backstory: str = ""


@router.post("/{world_id}/sessions/{session_id}/character")
async def create_character(
    world_id: str,
    session_id: str,
    payload: CharacterCreateRequest,
    coordinator: AdminCoordinator = Depends(get_coordinator),
):
    """创建玩家角色并生成开场叙述"""
    try:
        # 幂等检查：如果角色已存在，返回现有角色
        existing = await coordinator.character_store.get_character(world_id, session_id)
        if existing:
            return {
                "character": existing.model_dump(mode="json"),
                "opening_narration": "",
                "phase": "active",
            }

        from app.models.character_creation import CharacterCreationRequest
        request = CharacterCreationRequest(
            name=payload.name,
            race=payload.race,
            character_class=payload.character_class,
            background=payload.background,
            ability_scores=payload.ability_scores,
            skill_proficiencies=payload.skill_proficiencies,
            backstory=payload.backstory,
        )
        character = await coordinator.character_service.create_character(
            world_id, session_id, request,
        )

        # 生成开场叙述（此时有角色信息可注入）
        opening_narration = ""
        try:
            opening_narration = await coordinator.generate_opening_narration(
                world_id, session_id,
            )
        except Exception as exc:
            logger.warning("角色创建后开场叙述生成失败: %s", exc)

        return {
            "character": character.model_dump(mode="json"),
            "opening_narration": opening_narration,
            "phase": "active",
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("[character] 角色创建失败: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/{world_id}/sessions/{session_id}/character")
async def get_character(
    world_id: str,
    session_id: str,
    coordinator: AdminCoordinator = Depends(get_coordinator),
):
    """获取当前角色信息"""
    character = await coordinator.character_store.get_character(world_id, session_id)
    if not character:
        raise HTTPException(status_code=404, detail="No character found")
    return {"character": character.model_dump(mode="json")}


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



@router.post("/{world_id}/sessions/{session_id}/input")
async def process_input_v2(
    world_id: str,
    session_id: str,
    payload: PlayerInputRequest,
    coordinator: AdminCoordinator = Depends(get_coordinator),
) -> CoordinatorResponse:
    """处理玩家输入（V4 Agentic 管线）"""
    try:
        return await asyncio.wait_for(
            coordinator.process_player_input_v3(
                world_id=world_id,
                session_id=session_id,
                player_input=payload.input,
                is_private=payload.is_private,
                private_target=payload.private_target,
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
        raise _map_exception_to_http(exc) from exc


@router.post("/{world_id}/sessions/{session_id}/input/stream")
async def process_input_v2_stream(
    world_id: str,
    session_id: str,
    payload: PlayerInputRequest,
    coordinator: AdminCoordinator = Depends(get_coordinator),
):
    """流式处理玩家输入（V4 Agentic 管线）"""

    async def event_generator():
        try:
            async for event in coordinator.process_player_input_v3_stream(
                world_id=world_id,
                session_id=session_id,
                player_input=payload.input,
                is_private=payload.is_private,
                private_target=payload.private_target,
            ):
                yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
        except asyncio.CancelledError:
            logger.debug("[input/stream] 客户端断开，终止流 world=%s session=%s", world_id, session_id)
            raise
        except Exception as exc:
            logger.exception("[input/stream] 流式处理失败: %s", exc)
            mapped_exc = _map_exception_to_http(exc)
            error_event = {
                "type": "error",
                "error": str(exc),
                "status_code": mapped_exc.status_code,
                "detail": mapped_exc.detail,
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
        except Exception as exc:
            logger.exception("[interact/stream] 流式处理失败: %s", exc)
            error_event = {"type": "error", "error": str(exc)}
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
        raise _map_exception_to_http(exc) from exc



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
        raise _map_exception_to_http(exc) from exc




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
        raise _map_exception_to_http(exc) from exc


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
        raise _map_exception_to_http(exc) from exc


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
        raise _map_exception_to_http(exc) from exc


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
        raise _map_exception_to_http(exc) from exc


@router.post("/{world_id}/sessions/{session_id}/dice-roll")
async def player_dice_roll(
    world_id: str,
    session_id: str,
    payload: DiceRollAPIRequest,
    coordinator: AdminCoordinator = Depends(get_coordinator),
):
    """玩家通过 UI 按钮发起掷骰（不触发 LLM 叙述）。"""
    try:
        from app.services.ability_check_service import AbilityCheckService

        svc = AbilityCheckService(store=coordinator.character_store)
        result = await svc.perform_check(
            world_id=world_id,
            session_id=session_id,
            skill=payload.skill or None,
            ability=payload.ability or None,
            dc=payload.dc if payload.dc is not None else 10,
            source="player",
            turn_key=f"{session_id}:api",
        )
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return {"type": "dice_roll", "result": result}
    except HTTPException:
        raise
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



@router.get("/{world_id}/sessions/{session_id}/narrative/progress")
async def get_narrative_progress(
    world_id: str,
    session_id: str,
    coordinator: AdminCoordinator = Depends(get_coordinator),
):
    """获取当前叙事进度"""
    try:
        await coordinator.narrative_service.load_narrative_data(world_id, force_reload=True)
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
        await coordinator.narrative_service.load_narrative_data(world_id, force_reload=True)
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
        await coordinator.narrative_service.load_narrative_data(world_id, force_reload=True)
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
        await coordinator.narrative_service.load_narrative_data(world_id, force_reload=True)
        available_maps = await coordinator.narrative_service.get_available_maps(
            world_id, session_id
        )
        return {
            "available_maps": available_maps,
            "all_unlocked": "*" in available_maps,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc



class PrivateChatRequest(BaseModel):
    """私聊请求"""

    target_character_id: str
    input: str


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
        except Exception as exc:
            logger.exception("[private-chat/stream] 私聊流式处理失败: %s", exc)
            error_event = {
                "type": "error",
                "error": str(exc),
                "status_code": 503 if isinstance(exc, MCPServiceUnavailableError) else 500,
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
