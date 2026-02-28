"""Shared dependencies for the Game Core API."""

from __future__ import annotations

from typing import Any, Callable

from fastapi import FastAPI, HTTPException

from app.api_models import StructuredActionRequest
from app.interaction_service import InteractionService
from app.game_core import GameRuntime, ManagedSession
from app.game_core.adapters import FastAPIInputPort, InputPort
from app.game_core.orchestration.models import PipelineResult
from app.world_seed import WORLD_CATALOG, _shell_world_seed


GAME_RUNTIME = GameRuntime()
app = FastAPI(title="Game Core API", version="0.1.0")
app.state.game_runtime = GAME_RUNTIME
app.state.input_port = FastAPIInputPort()
app.state.interaction_service = None


def get_game_runtime() -> GameRuntime:
    """Return the active runtime for request handlers and tests."""

    return app.state.game_runtime


def get_input_port() -> InputPort:
    """Return the active inbound adapter for request handlers and tests."""

    return app.state.input_port


def get_interaction_service() -> InteractionService:
    """Return the active interaction service for request handlers and tests."""

    service = getattr(app.state, "interaction_service", None)
    if service is None:
        service = InteractionService(
            execute_structured_action=_execute_structured_action,
        )
        app.state.interaction_service = service
    return service


def _api_error(status_code: int, code: str, message: str) -> HTTPException:
    """Build one consistent JSON error payload."""

    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    )


def _validate_world_id(world_id: str) -> None:
    """Reject empty or path-like world identifiers."""

    value = world_id.strip()
    if not value:
        raise _api_error(400, "invalid_world_id", "invalid world_id")
    if "/" in value or "\\" in value or ".." in value or value.startswith("."):
        raise _api_error(400, "invalid_world_id", "invalid world_id")


def _validate_session_id(session_id: str) -> None:
    """Reject empty or path-like session identifiers."""

    value = session_id.strip()
    if not value:
        raise _api_error(400, "invalid_session_id", "invalid session_id")
    if "/" in value or "\\" in value or ".." in value or value.startswith("."):
        raise _api_error(400, "invalid_session_id", "invalid session_id")


def _world_not_found() -> HTTPException:
    """Return the unified error for an unknown world identifier."""

    raise _api_error(404, "world_not_found", "world not found")


def _session_not_found() -> HTTPException:
    """Return the unified error for a missing session identifier."""

    raise _api_error(404, "session_not_found", "session not found")


def _require_world(world_id: str) -> None:
    """Validate one world id and ensure it belongs to the supported catalog."""

    _validate_world_id(world_id)
    known_ids = {item["world_id"] for item in WORLD_CATALOG}
    if world_id not in known_ids:
        _world_not_found()


def _ensure_shell_world(runtime: GameRuntime, world_id: str) -> None:
    """Refresh the runtime cache with the canonical synthetic world."""

    runtime.get_world(
        world_id,
        world_data=_shell_world_seed(world_id),
        force_reload=True,
    )


async def _load_session_or_404(world_id: str, session_id: str) -> ManagedSession:
    """Resume one session against the canonical shell world."""

    _require_world(world_id)
    _validate_session_id(session_id)
    runtime = get_game_runtime()
    _ensure_shell_world(runtime, world_id)
    session = await runtime.resume_session(world_id, session_id)
    if session is None:
        _session_not_found()
    return session


def _session_phase(session: ManagedSession) -> str:
    """Derive the current lifecycle phase from the player slice."""

    player = session.runtime.state.player
    if (
        player.character_id.strip()
        and player.character_name.strip()
        and player.character_class.strip()
    ):
        return "active"
    return "character_creation"


async def _execute_structured_action(
    session: ManagedSession,
    request: StructuredActionRequest,
    event_sink: Callable | None = None,
) -> PipelineResult:
    """Execute one structured action through the real tick pipeline."""

    action_type = request.action_type.strip()
    if not action_type:
        raise _api_error(400, "invalid_action_request", "action_type must be non-empty")
    if not isinstance(request.params, dict):
        raise _api_error(400, "invalid_action_request", "params must be an object")
    if not session.runtime.action_dispatcher.has_action(action_type):
        raise _api_error(400, "unknown_action", f"unknown action: {action_type}")
    result = await session.runtime.tick_coordinator.process(
        {
            "action_type": action_type,
            "params": dict(request.params),
            "source": request.source,
            "context": dict(request.context) if isinstance(request.context, dict) else None,
        },
        event_sink=event_sink,
    )
    await get_game_runtime().save_session(session)
    return result
