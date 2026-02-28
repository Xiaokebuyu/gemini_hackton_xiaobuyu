"""Session lifecycle and world discovery routes."""

from __future__ import annotations

from fastapi import APIRouter, Response

from app.api_models import (
    HealthResponse,
    SessionLifecycleResponse,
    SessionSummaryBody,
    SessionSummaryResponse,
    WorldSummaryResponse,
)
from app.deps import (
    _api_error,
    _ensure_shell_world,
    _load_session_or_404,
    _require_world,
    _session_not_found,
    _session_phase,
    _validate_session_id,
    get_game_runtime,
)
from app.game_core import ManagedSession
from app.game_core.runtime import SavedSessionInfo
from app.world_seed import WORLD_CATALOG

router = APIRouter()


def _session_lifecycle_response(session: ManagedSession) -> SessionLifecycleResponse:
    """Convert one managed session into the lifecycle response model."""

    return SessionLifecycleResponse(
        world_id=session.world_id,
        session_id=session.session_id,
        phase=_session_phase(session),
    )


def _session_summary_response(item: SavedSessionInfo) -> SessionSummaryResponse:
    """Convert one SavedSessionInfo dataclass into the HTTP response model."""

    return SessionSummaryResponse(
        session_id=item.session_id,
        created_at=item.created_at,
        last_played=item.last_played,
        phase=item.phase,
        summary=SessionSummaryBody(
            player_name=item.summary.player_name,
            player_class=item.summary.player_class,
            level=item.summary.level,
            location=item.summary.location,
            day=item.summary.day,
            play_time_hours=item.summary.play_time_hours,
        ),
    )


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Return a minimal liveness signal for the API shell."""

    return HealthResponse(
        status="ok",
        service="game_core_api",
        mode="api_shell",
    )


@router.get("/api/game/worlds", response_model=list[WorldSummaryResponse])
async def list_worlds() -> list[WorldSummaryResponse]:
    """Return the static world catalog shell plus real save counts."""

    runtime = get_game_runtime()
    responses: list[WorldSummaryResponse] = []
    for item in WORLD_CATALOG:
        world_id = item["world_id"]
        sessions = await runtime.list_sessions(world_id)
        responses.append(
            WorldSummaryResponse(
                world_id=world_id,
                name=item["name"],
                description=item["description"],
                cover_image=item["cover_image"],
                player_count=len(sessions),
            )
        )
    return responses


@router.get("/api/game/{world_id}/sessions", response_model=list[SessionSummaryResponse])
async def list_sessions(world_id: str) -> list[SessionSummaryResponse]:
    """Return the real save catalog for one supported world."""

    _require_world(world_id)
    runtime = get_game_runtime()
    return [
        _session_summary_response(item)
        for item in await runtime.list_sessions(world_id)
    ]


@router.post(
    "/api/game/{world_id}/sessions",
    response_model=SessionLifecycleResponse,
    status_code=201,
)
async def create_session(world_id: str) -> SessionLifecycleResponse:
    """Create one new session against the built-in shell world."""

    _require_world(world_id)
    runtime = get_game_runtime()
    _ensure_shell_world(runtime, world_id)
    session = await runtime.create_session(world_id)
    return _session_lifecycle_response(session)


@router.post(
    "/api/game/{world_id}/sessions/{session_id}/resume",
    response_model=SessionLifecycleResponse,
)
async def resume_session(world_id: str, session_id: str) -> SessionLifecycleResponse:
    """Resume one stored session against the built-in shell world."""

    session = await _load_session_or_404(world_id, session_id)
    return _session_lifecycle_response(session)


@router.delete("/api/game/{world_id}/sessions/{session_id}")
async def delete_session(world_id: str, session_id: str) -> Response:
    """Delete one real save if it belongs to the requested world."""

    _require_world(world_id)
    _validate_session_id(session_id)
    deleted = await get_game_runtime().delete_session(world_id, session_id)
    if not deleted:
        _session_not_found()
    return Response(status_code=204)
