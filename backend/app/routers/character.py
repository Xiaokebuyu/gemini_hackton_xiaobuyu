"""Character creation and character panel routes."""

from __future__ import annotations

from fastapi import APIRouter

from app.api_models import (
    CharacterCreationOptionsResponse,
    CharacterCreationRequest,
    CharacterPanelResponse,
)
from app.deps import (
    _api_error,
    _ensure_shell_world,
    _load_session_or_404,
    _require_world,
    _session_phase,
    get_game_runtime,
)
from app.game_core import CharacterCreationSpec

router = APIRouter()


@router.get(
    "/api/game/{world_id}/character-creation/options",
    response_model=CharacterCreationOptionsResponse,
)
async def character_creation_options(world_id: str) -> CharacterCreationOptionsResponse:
    """Return real character-creation options for the built-in shell world."""

    _require_world(world_id)
    runtime = get_game_runtime()
    _ensure_shell_world(runtime, world_id)
    options = runtime.get_character_creation_options(world_id)
    return CharacterCreationOptionsResponse(
        races=list(options.races),
        classes=list(options.classes),
        backgrounds=list(options.backgrounds),
    )


@router.post(
    "/api/game/{world_id}/sessions/{session_id}/character",
    response_model=CharacterPanelResponse,
)
async def complete_character_creation(
    world_id: str,
    session_id: str,
    request: CharacterCreationRequest,
) -> CharacterPanelResponse:
    """Submit the first-pass character-creation payload."""

    runtime = get_game_runtime()
    lock = await runtime.session_lock(session_id)
    async with lock:
        session = await _load_session_or_404(world_id, session_id)
        race_id = (request.race_id or request.race or "").strip()
        class_id = (request.class_id or request.character_class or "").strip()
        background_id = (request.background_id or request.background or "").strip()
        if not race_id:
            raise _api_error(400, "invalid_character_creation", "race_id is required")
        if not class_id:
            raise _api_error(400, "invalid_character_creation", "class_id is required")
        if not background_id:
            raise _api_error(400, "invalid_character_creation", "background_id is required")
        spec = CharacterCreationSpec(
            name=request.name,
            race_id=race_id,
            class_id=class_id,
            background_id=background_id,
            ability_scores=dict(request.ability_scores),
            backstory=request.backstory,
        )
        try:
            result = await runtime.complete_character_creation(session, spec)
        except ValueError as exc:
            raise _api_error(400, "invalid_character_creation", str(exc)) from exc
        return CharacterPanelResponse(
            phase=result.phase,
            player=dict(result.player),
        )


@router.get(
    "/api/game/{world_id}/sessions/{session_id}/character",
    response_model=CharacterPanelResponse,
)
async def get_character_panel(
    world_id: str,
    session_id: str,
) -> CharacterPanelResponse:
    """Return the current player panel, even before character creation."""

    session = await _load_session_or_404(world_id, session_id)
    return CharacterPanelResponse(
        phase=_session_phase(session),
        player=session.runtime.state.player.snapshot(),
    )
