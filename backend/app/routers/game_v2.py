"""
Unified Game V2 API routes (Flash-Only).

Sub-routers: game_combat, game_party, game_narrative, game_npc.
"""
import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.dependencies import get_coordinator
from app.exceptions import LLMServiceError, MCPServiceUnavailableError
from app.models.game import (
    DiceRollAPIRequest,
    GameContextResponse,
    GameSessionState,
    PlayerInputRequest,
)
from app.services.admin.admin_coordinator import AdminCoordinator
from ._common import CATCHABLE_EXCEPTIONS, map_exception_to_http as _map_exception_to_http

# Sub-routers
from .game_combat import router as combat_router
from .game_party import router as party_router
from .game_narrative import router as narrative_router
from .game_npc import router as npc_router

# Backward-compat re-exports (used by tests)
from .game_party import (  # noqa: F401
    AddTeammateRequest,
    CreatePartyRequest,
    LoadTeammatesRequest,
    add_teammate,
    create_party,
    get_party_info,
    load_predefined_teammates,
    remove_teammate,
)

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

# Mount sub-routers
router.include_router(combat_router)
router.include_router(party_router)
router.include_router(narrative_router)
router.include_router(npc_router)


# ==================== World & Utility ====================


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
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=f"trace viewer load failed: {exc}") from exc


# ==================== Session ====================


class CreateGameSessionRequest(BaseModel):
    """创建游戏会话请求（统一版）"""

    user_id: str
    session_id: Optional[str] = None
    participants: Optional[List[str]] = None
    starting_location: Optional[str] = None
    starting_time: Optional[Dict[str, int]] = None  # {"day": 1, "hour": 8, "minute": 0}
    known_characters: List[str] = Field(default_factory=list)
    character_locations: Dict[str, str] = Field(default_factory=dict)


class RecoverableSessionItem(BaseModel):
    """可恢复会话摘要"""

    session_id: str
    world_id: str
    status: str
    updated_at: Any
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
        from app.runtime.session_runtime import SessionRuntime
        session = await SessionRuntime.get_or_restore(world_id, state.session_id)
        player_char = session.player
        if player_char:
            # 已有角色，直接生成开场叙述
            opening_narration = ""
            try:
                opening_narration = await coordinator.generate_opening_narration(
                    world_id, state.session_id,
                )
            except (LLMServiceError, asyncio.TimeoutError, OSError, MCPServiceUnavailableError) as exc:
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
    except CATCHABLE_EXCEPTIONS as exc:
        raise _map_exception_to_http(exc) from exc


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
    except CATCHABLE_EXCEPTIONS as exc:
        raise _map_exception_to_http(exc) from exc


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
    except CATCHABLE_EXCEPTIONS as exc:
        raise _map_exception_to_http(exc) from exc


# ==================== Character Creation ====================


@router.get("/{world_id}/character-creation/options")
async def get_character_creation_options(
    world_id: str,
    coordinator: AdminCoordinator = Depends(get_coordinator),
):
    """获取角色创建选项（种族、职业、背景、技能、点数购买规则）"""
    try:
        from app.runtime.game_runtime import GameRuntime
        rt = await GameRuntime.get_instance()
        world_instance = await rt.get_world(world_id)
        config = world_instance.character_creation_config
        if not config:
            raise HTTPException(status_code=404, detail="Character creation config not found for this world")
        return config
    except HTTPException:
        raise
    except CATCHABLE_EXCEPTIONS as exc:
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
        from app.runtime.session_runtime import SessionRuntime
        from app.runtime.game_runtime import GameRuntime
        from app.world.player.operations import create_player_node

        session = await SessionRuntime.get_or_restore(world_id, session_id)

        # 幂等检查：如果角色已存在，返回现有角色
        if session.player:
            return {
                "character": session.player.model_dump(mode="json"),
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

        rt = await GameRuntime.get_instance()
        world_instance = await rt.get_world(world_id)
        config = world_instance.character_creation_config
        if not config:
            raise ValueError("Character creation config not available for this world")

        player_view = create_player_node(
            session.world_graph, config, request, session.player_location,
        )
        session.mark_game_state_dirty()  # 确保 metadata.has_character 被写入
        await session.persist()

        # 生成开场叙述（此时有角色信息可注入）
        opening_narration = ""
        try:
            opening_narration = await coordinator.generate_opening_narration(
                world_id, session_id,
            )
        except (LLMServiceError, asyncio.TimeoutError, OSError, MCPServiceUnavailableError) as exc:
            logger.warning("角色创建后开场叙述生成失败: %s", exc)

        return {
            "character": player_view.model_dump(mode="json"),
            "opening_narration": opening_narration,
            "phase": "active",
        }
    except CATCHABLE_EXCEPTIONS as exc:
        logger.exception("[character] 角色创建失败: %s", exc)
        raise _map_exception_to_http(exc) from exc


@router.get("/{world_id}/sessions/{session_id}/character")
async def get_character(
    world_id: str,
    session_id: str,
    coordinator: AdminCoordinator = Depends(get_coordinator),
):
    """获取当前角色信息"""
    from app.runtime.session_runtime import SessionRuntime
    session = await SessionRuntime.get_or_restore(world_id, session_id)
    player = session.player
    if not player:
        raise HTTPException(status_code=404, detail="No character found")
    return {"character": player.model_dump(mode="json")}


# ==================== Game Context & Input ====================


@router.get("/{world_id}/sessions/{session_id}/context")
async def get_context(
    world_id: str,
    session_id: str,
    coordinator: AdminCoordinator = Depends(get_coordinator),
) -> GameContextResponse:
    """获取当前上下文"""
    try:
        ctx = await coordinator.get_context_async(world_id, session_id)
        if ctx is None:
            raise HTTPException(status_code=404, detail="会话上下文不存在")
        from dataclasses import asdict
        return GameContextResponse(**asdict(ctx))
    except HTTPException:
        raise
    except CATCHABLE_EXCEPTIONS as exc:
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
        except CATCHABLE_EXCEPTIONS as exc:
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


# ==================== Location & Time ====================


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
    except CATCHABLE_EXCEPTIONS as exc:
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
    except CATCHABLE_EXCEPTIONS as exc:
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
    except CATCHABLE_EXCEPTIONS as exc:
        raise _map_exception_to_http(exc) from exc


# ==================== Dice Roll ====================


@router.post("/{world_id}/sessions/{session_id}/dice-roll")
async def player_dice_roll(
    world_id: str,
    session_id: str,
    payload: DiceRollAPIRequest,
    coordinator: AdminCoordinator = Depends(get_coordinator),
):
    """玩家通过 UI 按钮发起掷骰（不触发 LLM 叙述）。"""
    try:
        from app.runtime.session_runtime import SessionRuntime
        from app.world.player.ability_check import AbilityCheckService

        session = await SessionRuntime.get_or_restore(world_id, session_id)
        if not session.player:
            raise HTTPException(status_code=404, detail="No character found")
        svc = AbilityCheckService()
        result = svc.perform_check(
            player=session.player,
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
    except CATCHABLE_EXCEPTIONS as exc:
        raise _map_exception_to_http(exc) from exc


# ==================== History ====================


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
    except CATCHABLE_EXCEPTIONS as exc:
        raise _map_exception_to_http(exc) from exc
