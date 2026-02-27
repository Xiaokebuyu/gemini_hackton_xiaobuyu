"""Party-related API routes."""
import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.dependencies import get_coordinator
from app.routers._common import CATCHABLE_EXCEPTIONS, map_exception_to_http
from app.services.admin.admin_coordinator import AdminCoordinator

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Party"])


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
    except CATCHABLE_EXCEPTIONS as exc:
        raise map_exception_to_http(exc) from exc


@router.get("/{world_id}/sessions/{session_id}/party")
async def get_party_info(
    world_id: str,
    session_id: str,
    coordinator: AdminCoordinator = Depends(get_coordinator),
):
    """获取队伍信息"""
    try:
        return await coordinator.get_party_info(world_id, session_id)
    except CATCHABLE_EXCEPTIONS as exc:
        raise map_exception_to_http(exc) from exc


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
    except CATCHABLE_EXCEPTIONS as exc:
        raise map_exception_to_http(exc) from exc


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
    except CATCHABLE_EXCEPTIONS as exc:
        raise map_exception_to_http(exc) from exc


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
    except CATCHABLE_EXCEPTIONS as exc:
        raise map_exception_to_http(exc) from exc
