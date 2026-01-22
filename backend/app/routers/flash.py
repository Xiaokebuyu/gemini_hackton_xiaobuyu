"""
Flash API routes for character memory.
"""
from fastapi import APIRouter, HTTPException

from app.models.flash import EventIngestRequest, EventIngestResponse, RecallRequest, RecallResponse
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
