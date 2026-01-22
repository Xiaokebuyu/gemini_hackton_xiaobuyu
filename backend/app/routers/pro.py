"""
Pro API routes (context assembly).
"""
from fastapi import APIRouter, HTTPException

from app.models.pro import CharacterProfile, ProContextRequest, ProContextResponse
from app.services.pro_service import ProService


router = APIRouter()
pro_service = ProService()


@router.get("/pro/{world_id}/characters/{character_id}/profile")
async def get_profile(world_id: str, character_id: str) -> CharacterProfile:
    """Get character profile."""
    return await pro_service.get_profile(world_id, character_id)


@router.put("/pro/{world_id}/characters/{character_id}/profile")
async def set_profile(
    world_id: str,
    character_id: str,
    payload: CharacterProfile,
) -> CharacterProfile:
    """Set character profile."""
    try:
        return await pro_service.set_profile(world_id, character_id, payload)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/pro/{world_id}/characters/{character_id}/context")
async def build_context(
    world_id: str,
    character_id: str,
    payload: ProContextRequest,
) -> ProContextResponse:
    """Build Pro context."""
    try:
        return await pro_service.build_context(world_id, character_id, payload)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
