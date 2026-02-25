"""Narrative and event API routes."""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from app.dependencies import get_coordinator
from app.routers._common import CATCHABLE_EXCEPTIONS, map_exception_to_http
from app.models.event import (
    GMEventIngestRequest,
    GMEventIngestResponse,
    NaturalEventIngestRequest,
    NaturalEventIngestResponse,
)
from app.services.admin.admin_coordinator import AdminCoordinator

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Narrative"])


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
    except CATCHABLE_EXCEPTIONS as exc:
        raise map_exception_to_http(exc) from exc


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
    except CATCHABLE_EXCEPTIONS as exc:
        raise map_exception_to_http(exc) from exc


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
    except CATCHABLE_EXCEPTIONS as exc:
        raise map_exception_to_http(exc) from exc


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
    except CATCHABLE_EXCEPTIONS as exc:
        raise map_exception_to_http(exc) from exc


@router.post("/{world_id}/events/ingest")
async def ingest_event(
    world_id: str,
    payload: GMEventIngestRequest,
    coordinator: AdminCoordinator = Depends(get_coordinator),
) -> GMEventIngestResponse:
    """GM 结构化事件摄入"""
    try:
        return await coordinator.ingest_event(world_id, payload)
    except CATCHABLE_EXCEPTIONS as exc:
        raise map_exception_to_http(exc) from exc


@router.post("/{world_id}/events/ingest-natural")
async def ingest_event_natural(
    world_id: str,
    payload: NaturalEventIngestRequest,
    coordinator: AdminCoordinator = Depends(get_coordinator),
) -> NaturalEventIngestResponse:
    """GM 自然语言事件摄入"""
    try:
        return await coordinator.ingest_event_natural(world_id, payload)
    except CATCHABLE_EXCEPTIONS as exc:
        raise map_exception_to_http(exc) from exc
