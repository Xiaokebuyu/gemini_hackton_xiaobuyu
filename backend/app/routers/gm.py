"""
GM API routes.
"""
from fastapi import APIRouter, HTTPException

from app.models.event import GMEventIngestRequest, GMEventIngestResponse
from app.services.gm_flash_service import GMFlashService


router = APIRouter()
gm_service = GMFlashService()


@router.post("/gm/{world_id}/events/ingest")
async def ingest_event(
    world_id: str,
    payload: GMEventIngestRequest,
) -> GMEventIngestResponse:
    """GM ingest event and dispatch."""
    try:
        return await gm_service.ingest_event(world_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
