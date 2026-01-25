"""
Game Master API routes - Phase 6 完整游戏循环API

这些API提供完整的游戏循环功能：
- 会话管理
- 场景管理
- 玩家输入处理
- NPC对话
- 战斗整合
"""
from typing import List, Optional
from fastapi import APIRouter, HTTPException

from app.models.game import (
    CombatActionRequest,
    CombatActionResponse,
    CreateSessionRequest,
    CreateSessionResponse,
    EnterSceneRequest,
    EnterSceneResponse,
    GameContextResponse,
    GamePhase,
    PlayerInputRequest,
    PlayerInputResponse,
    StartDialogueRequest,
    StartDialogueResponse,
    TriggerCombatRequest,
    TriggerCombatResponse,
)
from app.services.game_master_service import GameMasterService, GamePhase as ServiceGamePhase


router = APIRouter(prefix="/gm", tags=["Game Master"])
gm_service = GameMasterService()


# ============================================
# 会话管理
# ============================================


@router.post("/{world_id}/sessions")
async def create_session(
    world_id: str,
    payload: CreateSessionRequest,
    known_characters: Optional[List[str]] = None,
) -> CreateSessionResponse:
    """
    创建新的游戏会话

    Args:
        world_id: 世界ID
        payload: 创建会话请求
        known_characters: 世界中的已知角色列表

    Returns:
        CreateSessionResponse: 包含会话状态
    """
    try:
        context = await gm_service.start_session(
            world_id=world_id,
            session_id=payload.session_id,
            participants=payload.participants,
            known_characters=known_characters,
        )
        session = await gm_service.get_session(world_id, context.session_id)
        return CreateSessionResponse(session=session)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/{world_id}/sessions/{session_id}/context")
async def get_context(world_id: str, session_id: str) -> GameContextResponse:
    """
    获取游戏上下文

    Returns:
        GameContextResponse: 当前游戏状态
    """
    context = gm_service.get_context(world_id, session_id)
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


# ============================================
# 场景管理
# ============================================


@router.post("/{world_id}/sessions/{session_id}/scene")
async def enter_scene(
    world_id: str,
    session_id: str,
    payload: EnterSceneRequest,
) -> EnterSceneResponse:
    """
    进入新场景

    这是场景切换的核心API：
    1. 更新会话的当前场景
    2. 为在场的NPC预加载相关记忆
    3. 生成场景描述（GM叙述）

    Args:
        world_id: 世界ID
        session_id: 会话ID
        payload: 场景请求

    Returns:
        EnterSceneResponse: 包含场景描述和NPC记忆
    """
    try:
        result = await gm_service.enter_scene(
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


# ============================================
# 玩家输入处理
# ============================================


@router.post("/{world_id}/sessions/{session_id}/input")
async def process_input(
    world_id: str,
    session_id: str,
    payload: PlayerInputRequest,
) -> PlayerInputResponse:
    """
    处理玩家输入 - 游戏循环的核心入口

    根据输入类型和当前状态，决定如何处理：
    - 叙述/行动 → GM处理并生成结果
    - 对话 → NPC Pro处理
    - 战斗指令 → 战斗引擎处理
    - 系统命令 → 系统处理

    Args:
        world_id: 世界ID
        session_id: 会话ID
        payload: 玩家输入

    Returns:
        PlayerInputResponse: 游戏响应
    """
    try:
        from app.services.game_master_service import InputType

        input_type = None
        if payload.input_type:
            try:
                input_type = InputType(payload.input_type)
            except ValueError:
                pass

        result = await gm_service.process_player_input(
            world_id=world_id,
            session_id=session_id,
            player_input=payload.input,
            input_type=input_type,
        )

        return PlayerInputResponse(
            type=result.get("type", "error"),
            response=result.get("response", ""),
            speaker=result.get("speaker", "GM"),
            npc_id=result.get("npc_id"),
            event_recorded=result.get("event_recorded", False),
            tool_called=result.get("tool_called", False),
            recalled_memory=result.get("recalled_memory"),
            available_actions=[
                a.model_dump() if hasattr(a, 'model_dump') else a
                for a in result.get("available_actions", [])
            ],
            state_changes=result.get("state_changes", {}),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ============================================
# NPC对话
# ============================================


@router.post("/{world_id}/sessions/{session_id}/dialogue/start")
async def start_dialogue(
    world_id: str,
    session_id: str,
    payload: StartDialogueRequest,
) -> StartDialogueResponse:
    """
    开始与NPC对话

    Args:
        world_id: 世界ID
        session_id: 会话ID
        payload: 对话请求

    Returns:
        StartDialogueResponse: NPC的开场白
    """
    try:
        result = await gm_service.start_dialogue(
            world_id=world_id,
            session_id=session_id,
            npc_id=payload.npc_id,
        )

        if result.get("type") == "error":
            raise HTTPException(status_code=400, detail=result.get("response"))

        return StartDialogueResponse(
            npc_id=payload.npc_id,
            npc_name=result.get("speaker", payload.npc_id),
            greeting=result.get("response", ""),
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/{world_id}/sessions/{session_id}/dialogue/end")
async def end_dialogue(world_id: str, session_id: str) -> PlayerInputResponse:
    """
    结束当前对话

    Returns:
        PlayerInputResponse: 系统确认
    """
    try:
        result = await gm_service.end_dialogue(world_id, session_id)
        return PlayerInputResponse(
            type=result.get("type", "system"),
            response=result.get("response", ""),
            speaker=result.get("speaker", "系统"),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ============================================
# 战斗系统
# ============================================


@router.post("/{world_id}/sessions/{session_id}/combat/trigger")
async def trigger_combat(
    world_id: str,
    session_id: str,
    payload: TriggerCombatRequest,
) -> TriggerCombatResponse:
    """
    触发战斗

    Args:
        world_id: 世界ID
        session_id: 会话ID
        payload: 战斗请求

    Returns:
        TriggerCombatResponse: 战斗初始化信息
    """
    try:
        result = await gm_service.trigger_combat(
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
                a.model_dump() if hasattr(a, 'model_dump') else a
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
) -> CombatActionResponse:
    """
    执行战斗行动

    Args:
        world_id: 世界ID
        session_id: 会话ID
        payload: 行动请求

    Returns:
        CombatActionResponse: 行动结果
    """
    try:
        result = await gm_service.execute_combat_action(
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
                a.model_dump() if hasattr(a, 'model_dump') else a
                for a in result.get("available_actions", [])
            ],
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ============================================
# 工具方法
# ============================================


@router.post("/{world_id}/sessions/{session_id}/advance-day")
async def advance_day(world_id: str, session_id: str) -> PlayerInputResponse:
    """
    推进游戏日

    Returns:
        PlayerInputResponse: 系统确认
    """
    try:
        result = await gm_service.advance_day(world_id, session_id)
        return PlayerInputResponse(
            type=result.get("type", "system"),
            response=result.get("response", ""),
            speaker="系统",
            state_changes={"game_day": result.get("game_day")},
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
