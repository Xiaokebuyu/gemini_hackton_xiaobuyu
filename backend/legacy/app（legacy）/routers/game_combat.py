"""Combat-related API routes."""
import logging

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_coordinator
from app.models.game import (
    CombatActionRequest,
    CombatActionResponse,
    CombatResolveRequest,
    CombatResolveResponse,
    CombatStartRequest,
    CombatStartResponse,
    TriggerCombatRequest,
    TriggerCombatResponse,
)
from app.services.admin.admin_coordinator import AdminCoordinator
from ._common import CATCHABLE_EXCEPTIONS, map_exception_to_http

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Combat"])


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
    except CATCHABLE_EXCEPTIONS as exc:
        raise map_exception_to_http(exc) from exc


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
    except CATCHABLE_EXCEPTIONS as exc:
        raise map_exception_to_http(exc) from exc


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
    except CATCHABLE_EXCEPTIONS as exc:
        raise map_exception_to_http(exc) from exc


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
    except CATCHABLE_EXCEPTIONS as exc:
        raise map_exception_to_http(exc) from exc
