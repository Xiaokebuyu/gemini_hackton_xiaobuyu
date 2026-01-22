"""
Game loop API routes.
"""
from fastapi import APIRouter, HTTPException

from app.models.game import (
    CombatResolveRequest,
    CombatResolveResponse,
    CombatStartRequest,
    CombatStartResponse,
    CreateSessionRequest,
    CreateSessionResponse,
    GameSessionState,
    UpdateSceneRequest,
)
from app.services.game_loop_service import GameLoopService


router = APIRouter()
game_service = GameLoopService()


@router.post("/game/{world_id}/sessions")
async def create_session(world_id: str, payload: CreateSessionRequest) -> CreateSessionResponse:
    """Create game session."""
    try:
        return await game_service.create_session(world_id, payload)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/game/{world_id}/sessions/{session_id}")
async def get_session(world_id: str, session_id: str) -> GameSessionState:
    """Get game session."""
    session = await game_service.get_session(world_id, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    return session


@router.post("/game/{world_id}/sessions/{session_id}/scene")
async def update_scene(
    world_id: str,
    session_id: str,
    payload: UpdateSceneRequest,
) -> GameSessionState:
    """Update scene state."""
    try:
        return await game_service.update_scene(world_id, session_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/game/{world_id}/sessions/{session_id}/combat/start")
async def start_combat(
    world_id: str,
    session_id: str,
    payload: CombatStartRequest,
) -> CombatStartResponse:
    """Start combat."""
    try:
        return await game_service.start_combat(world_id, session_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/game/{world_id}/sessions/{session_id}/combat/resolve")
async def resolve_combat(
    world_id: str,
    session_id: str,
    payload: CombatResolveRequest,
) -> CombatResolveResponse:
    """Resolve combat and dispatch event."""
    try:
        return await game_service.resolve_combat(world_id, session_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
