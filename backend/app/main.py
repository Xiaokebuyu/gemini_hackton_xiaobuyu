"""Minimal FastAPI shell for the active game-core API surface."""

from __future__ import annotations

import json
from typing import Any, Iterator, Mapping

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import StreamingResponse

from app.api_models import (
    ActionExecutionResponse,
    CharacterCreationOptionsResponse,
    CharacterCreationRequest,
    CharacterPanelResponse,
    HealthResponse,
    InteractRequest,
    InventoryPanelResponse,
    MapAreaSummary,
    MapPanelResponse,
    NavigateRequest,
    PlayerLocationBody,
    QuestPanelResponse,
    SessionLifecycleResponse,
    SessionSummaryBody,
    SessionSummaryResponse,
    StructuredActionRequest,
    TextInputRequest,
    WorldSummaryResponse,
)
from app.interaction_service import InteractionService
from app.game_core import CharacterCreationSpec, GameRuntime, ManagedSession
from app.game_core.adapters import FastAPIInputPort, InputPort


GAME_RUNTIME = GameRuntime()
app = FastAPI(title="Game Core API", version="0.1.0")
app.state.game_runtime = GAME_RUNTIME
app.state.input_port = FastAPIInputPort()
app.state.interaction_service = None
WORLD_CATALOG = [
    {
        "world_id": "goblin_slayer",
        "name": "哥布林杀手",
        "description": "当前运行内建骨架世界，用于会话与接口联调。",
        "cover_image": "worlds/goblin_slayer/cover.png",
    }
]


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
            save_session=get_game_runtime().save_session,
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


def _world_content_unavailable() -> HTTPException:
    """Return the unified placeholder error for world-backed APIs."""

    raise _api_error(
        503,
        "world_content_unavailable",
        "world content pipeline is not ready for live session APIs",
    )


def _format_sse_event(event_type: str, payload: Mapping[str, Any]) -> str:
    """Serialize one SSE event chunk."""

    encoded = json.dumps(
        dict(payload),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"event: {event_type}\ndata: {encoded}\n\n"


def _stream_response(chunks: Iterator[str] | list[str]) -> StreamingResponse:
    """Return one SSE response with consistent headers."""

    return StreamingResponse(
        iter(chunks),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


def _disabled_stream_payload() -> Iterator[str]:
    """Emit one standard SSE error event followed by stream termination."""

    yield _format_sse_event(
        "api_unavailable",
        {
            "code": "world_content_unavailable",
            "message": "world content pipeline is not ready for live session APIs",
        },
    )
    yield _format_sse_event(
        "stream_end",
        {"reason": "world_content_unavailable"},
    )


def _disabled_stream() -> StreamingResponse:
    """Return one real SSE placeholder stream."""

    return _stream_response(_disabled_stream_payload())


def _shell_world_seed(world_id: str) -> dict[str, Any]:
    """Return one built-in synthetic world used by the API shell."""

    if world_id != "goblin_slayer":
        raise ValueError(f"unsupported shell world: {world_id}")
    return {
        "maps": {
            "guild_hall": {
                "id": "guild_hall",
                "name": "Guild Hall",
                "is_starting_area": True,
                "base_danger": 0.1,
                "sub_locations": {
                    "counter": {"id": "counter", "name": "Front Counter"},
                    "board": {"id": "board", "name": "Quest Board"},
                },
            },
            "training_grounds": {
                "id": "training_grounds",
                "name": "Training Grounds",
                "base_danger": 0.2,
                "sub_locations": {
                    "yard": {"id": "yard", "name": "Sparring Yard"},
                },
            },
            "frontier": {
                "id": "frontier",
                "name": "Frontier",
                "base_danger": 0.6,
                "sub_locations": {
                    "camp": {"id": "camp", "name": "Frontier Camp"},
                },
            },
        },
        "classes": {
            "classes": {
                "fighter": {
                    "id": "fighter",
                    "name": "Fighter",
                    "description": "A disciplined martial combatant.",
                    "hit_die": 10,
                    "hp_per_level": 6,
                    "subclass_level": 3,
                    "starting_gold": 10,
                    "starting_equipment": ["training_sword", "wooden_shield"],
                    "default_equipped": {
                        "main_hand": "training_sword",
                        "off_hand": "wooden_shield",
                    },
                    "level_features": {
                        "1": ["Second Wind"],
                        "2": ["Action Surge"],
                    },
                }
            },
            "races": {
                "human": {
                    "id": "human",
                    "name": "Human",
                    "description": "Adaptable and resilient.",
                    "stat_bonuses": {"str": 1},
                    "racial_traits": ["Adaptable"],
                }
            },
            "backgrounds": {
                "adventurer": {
                    "id": "adventurer",
                    "name": "Adventurer",
                    "description": "A road-worn beginner guild member.",
                    "feature": "Road-Worn",
                    "gold_bonus": 15,
                }
            },
        },
        "items": {
            "training_sword": {
                "id": "training_sword",
                "name": "Training Sword",
                "slot": "main_hand",
                "base_price": 12,
            },
            "wooden_shield": {
                "id": "wooden_shield",
                "name": "Wooden Shield",
                "slot": "off_hand",
                "base_price": 8,
            },
            "bandage": {
                "id": "bandage",
                "name": "Bandage",
                "base_price": 5,
            },
            "torch": {
                "id": "torch",
                "name": "Torch",
                "base_price": 3,
            },
        },
        "quests": {
            "chapters": [
                {
                    "id": "chapter_intro",
                    "name": "Introduction",
                }
            ],
            "milestones": {
                "report_in": {
                    "id": "report_in",
                    "name": "Report In",
                    "chapter_id": "chapter_intro",
                    "next_milestones": [],
                }
            },
        },
        "tags": {},
        "skills": {},
        "lore": {},
        "characters": {
            "merchant": {
                "id": "merchant",
                "name": "Guild Merchant",
                "current_area": "guild_hall",
                "current_location": "counter",
                "sell_markup": 1.0,
                "buy_rate": 0.5,
                "shop_inventory": {
                    "base_pool": [
                        {"item_id": "bandage", "count": 5},
                        {"item_id": "torch", "count": 3},
                    ]
                },
            }
        },
        "monsters": {},
        "factions": {},
    }


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


def _session_lifecycle_response(session: ManagedSession) -> SessionLifecycleResponse:
    """Convert one managed session into the lifecycle response model."""

    return SessionLifecycleResponse(
        world_id=session.world_id,
        session_id=session.session_id,
        phase=_session_phase(session),
    )


def _session_summary_response(item: object) -> SessionSummaryResponse:
    """Convert one SavedSessionInfo dataclass into the HTTP response model."""

    summary = getattr(item, "summary")
    return SessionSummaryResponse(
        session_id=str(getattr(item, "session_id", "")),
        created_at=float(getattr(item, "created_at", 0.0)),
        last_played=float(getattr(item, "last_played", 0.0)),
        phase=str(getattr(item, "phase", "character_creation")),
        summary=SessionSummaryBody(
            player_name=str(getattr(summary, "player_name", "")),
            player_class=str(getattr(summary, "player_class", "")),
            level=int(getattr(summary, "level", 1)),
            location=str(getattr(summary, "location", "")),
            day=getattr(summary, "day", None),
            play_time_hours=getattr(summary, "play_time_hours", None),
        ),
    )


def _inventory_response(session: ManagedSession) -> InventoryPanelResponse:
    """Build the inventory panel payload from the player slice."""

    player_payload = session.runtime.state.player.snapshot()
    inventory = player_payload.get("inventory", [])
    equipment = player_payload.get("equipment", {})
    return InventoryPanelResponse(
        gold=int(player_payload.get("gold", 0)),
        inventory=list(inventory) if isinstance(inventory, list) else [],
        equipment=dict(equipment) if isinstance(equipment, Mapping) else {},
    )


def _map_response(session: ManagedSession) -> MapPanelResponse:
    """Build the map panel payload from world templates and runtime state."""

    player = session.runtime.state.player
    area_snapshot = session.runtime.state.areas.snapshot()
    raw_areas = area_snapshot.get("areas", {})
    state_areas = raw_areas if isinstance(raw_areas, Mapping) else {}
    summaries: list[MapAreaSummary] = []
    discovered_area_ids: list[str] = []
    world_areas = (
        session.runtime.world.maps.list_all()
        if session.runtime.world.has_registry("maps")
        else []
    )
    for template in world_areas:
        if not isinstance(template, Mapping):
            continue
        area_id = str(template.get("id", "")).strip()
        if not area_id:
            continue
        state_area = state_areas.get(area_id, {})
        if not isinstance(state_area, Mapping):
            state_area = {}
        exploration = str(state_area.get("exploration", "undiscovered"))
        if exploration != "undiscovered":
            discovered_area_ids.append(area_id)
        raw_sub_locations = template.get("sub_locations", {})
        sub_locations: list[dict[str, str]] = []
        if isinstance(raw_sub_locations, Mapping):
            for key, raw_location in raw_sub_locations.items():
                location_id = str(key).strip()
                if not location_id:
                    continue
                location_name = location_id
                if isinstance(raw_location, Mapping):
                    raw_name = str(raw_location.get("name", "")).strip()
                    if raw_name:
                        location_name = raw_name
                sub_locations.append({"id": location_id, "name": location_name})
        raw_tags = template.get("tags", [])
        tags = (
            [str(tag) for tag in raw_tags if str(tag).strip()]
            if isinstance(raw_tags, list)
            else []
        )
        raw_danger = state_area.get(
            "danger_level",
            template.get("base_danger", template.get("danger_level")),
        )
        try:
            danger_level = float(raw_danger) if raw_danger is not None else None
        except (TypeError, ValueError):
            danger_level = None
        summaries.append(
            MapAreaSummary(
                id=area_id,
                name=str(template.get("name", area_id)),
                danger_level=danger_level,
                exploration=exploration,
                tags=tags,
                sub_locations=sub_locations,
            )
        )
    return MapPanelResponse(
        current_area=player.current_area,
        current_location=player.current_location,
        discovered_area_ids=discovered_area_ids,
        areas=summaries,
    )


def _quest_response(session: ManagedSession) -> QuestPanelResponse:
    """Build the quest panel payload from the quest slice."""

    quest_payload = session.runtime.state.quests.snapshot()
    return QuestPanelResponse(
        milestone_states=dict(quest_payload.get("milestone_states", {})),
        dynamic_quests=dict(quest_payload.get("dynamic_quests", {})),
        chapter_completion=dict(quest_payload.get("chapter_completion", {})),
    )


def _action_execution_response(
    result: Any,
    action_type: str,
    session: ManagedSession,
) -> ActionExecutionResponse:
    """Convert one pipeline result into the JSON action response model."""

    player = session.runtime.state.player
    return ActionExecutionResponse(
        success=bool(getattr(result, "success", False)),
        action_type=action_type,
        time_cost=float(getattr(result, "time_cost", 0.0)),
        metadata=dict(getattr(result, "metadata", {})),
        errors=list(getattr(result, "errors", [])),
        player_location=PlayerLocationBody(
            area_id=player.current_area,
            location_id=player.current_location,
        ),
    )


async def _execute_structured_action(
    session: ManagedSession,
    request: StructuredActionRequest,
) -> Any:
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
        }
    )
    await get_game_runtime().save_session(session)
    return result


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Return a minimal liveness signal for the API shell."""

    return HealthResponse(
        status="ok",
        service="game_core_api",
        mode="api_shell",
    )


@app.get("/api/game/worlds", response_model=list[WorldSummaryResponse])
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


@app.get("/api/game/{world_id}/sessions", response_model=list[SessionSummaryResponse])
async def list_sessions(world_id: str) -> list[SessionSummaryResponse]:
    """Return the real save catalog for one supported world."""

    _require_world(world_id)
    runtime = get_game_runtime()
    return [
        _session_summary_response(item)
        for item in await runtime.list_sessions(world_id)
    ]


@app.post(
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


@app.post(
    "/api/game/{world_id}/sessions/{session_id}/resume",
    response_model=SessionLifecycleResponse,
)
async def resume_session(world_id: str, session_id: str) -> SessionLifecycleResponse:
    """Resume one stored session against the built-in shell world."""

    session = await _load_session_or_404(world_id, session_id)
    return _session_lifecycle_response(session)


@app.delete("/api/game/{world_id}/sessions/{session_id}")
async def delete_session(world_id: str, session_id: str) -> Response:
    """Delete one real save if it belongs to the requested world."""

    _require_world(world_id)
    _validate_session_id(session_id)
    deleted = await get_game_runtime().delete_session(world_id, session_id)
    if not deleted:
        _session_not_found()
    return Response(status_code=204)


@app.get(
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


@app.post(
    "/api/game/{world_id}/sessions/{session_id}/character",
    response_model=CharacterPanelResponse,
)
async def complete_character_creation(
    world_id: str,
    session_id: str,
    request: CharacterCreationRequest,
) -> CharacterPanelResponse:
    """Submit the first-pass character-creation payload."""

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
        result = await get_game_runtime().complete_character_creation(session, spec)
    except ValueError as exc:
        raise _api_error(400, "invalid_character_creation", str(exc)) from exc
    return CharacterPanelResponse(
        phase=result.phase,
        player=dict(result.player),
    )


@app.get(
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


@app.get(
    "/api/game/{world_id}/sessions/{session_id}/inventory",
    response_model=InventoryPanelResponse,
)
async def get_inventory_panel(
    world_id: str,
    session_id: str,
) -> InventoryPanelResponse:
    """Return the current inventory and equipment panel."""

    session = await _load_session_or_404(world_id, session_id)
    return _inventory_response(session)


@app.get(
    "/api/game/{world_id}/sessions/{session_id}/map",
    response_model=MapPanelResponse,
)
async def get_map_panel(world_id: str, session_id: str) -> MapPanelResponse:
    """Return the current map panel."""

    session = await _load_session_or_404(world_id, session_id)
    return _map_response(session)


@app.get(
    "/api/game/{world_id}/sessions/{session_id}/quests",
    response_model=QuestPanelResponse,
)
async def get_quest_panel(world_id: str, session_id: str) -> QuestPanelResponse:
    """Return the current quest panel."""

    session = await _load_session_or_404(world_id, session_id)
    return _quest_response(session)


@app.post(
    "/api/game/{world_id}/sessions/{session_id}/navigate",
    response_model=ActionExecutionResponse,
)
async def navigate(
    world_id: str,
    session_id: str,
    request: NavigateRequest,
) -> ActionExecutionResponse:
    """Execute one navigation action through the real runtime pipeline."""

    session = await _load_session_or_404(world_id, session_id)
    action = request.action.strip()
    if action not in {"move_area", "enter_sub_location", "leave_sub_location"}:
        raise _api_error(400, "invalid_navigation_request", "unknown navigation action")

    params: dict[str, Any] = {}
    if action == "move_area":
        area_id = (request.area_id or "").strip()
        if not area_id:
            raise _api_error(400, "invalid_navigation_request", "area_id is required")
        params["area_id"] = area_id
    elif action == "enter_sub_location":
        location_id = (request.location_id or "").strip()
        if not location_id:
            raise _api_error(
                400,
                "invalid_navigation_request",
                "location_id is required",
            )
        params["location_id"] = location_id
        area_id = (request.area_id or "").strip()
        if area_id:
            params["area_id"] = area_id
    else:
        area_id = (request.area_id or "").strip()
        if area_id:
            params["area_id"] = area_id

    result = await _execute_structured_action(
        session,
        StructuredActionRequest(action_type=action, params=params),
    )
    if not result.success:
        message = result.errors[0] if result.errors else "navigation failed"
        raise _api_error(400, "navigation_rejected", message)
    return _action_execution_response(result, action, session)


@app.post("/api/game/{world_id}/sessions/{session_id}/input/stream")
async def input_stream(
    world_id: str,
    session_id: str,
    request: TextInputRequest,
) -> StreamingResponse:
    """Parse one minimal text command and stream the execution result."""

    session = await _load_session_or_404(world_id, session_id)
    parsed = await get_input_port().process_text(request.text)
    normalized_text = str(parsed.get("normalized_text", "")).strip()

    if str(parsed.get("status", "")) != "parsed":
        chunks = [
            _format_sse_event(
                "input_rejected",
                {
                    "text": request.text,
                    "normalized_text": normalized_text,
                    "code": str(parsed.get("code", "unsupported_input")),
                    "message": str(
                        parsed.get(
                            "message",
                            "input is not a supported command alias",
                        )
                    ),
                },
            ),
            _format_sse_event(
                "stream_end",
                {
                    "reason": "input_rejected",
                    "success": False,
                },
            ),
        ]
        return _stream_response(chunks)

    action_type = str(parsed.get("action_type", "")).strip()
    raw_params = parsed.get("params", {})
    params = dict(raw_params) if isinstance(raw_params, Mapping) else {}
    chunks = [
        _format_sse_event(
            "input_parsed",
            {
                "text": request.text,
                "normalized_text": normalized_text,
                "action_type": action_type,
                "params": params,
            },
        )
    ]
    result = await _execute_structured_action(
        session,
        StructuredActionRequest(action_type=action_type, params=params),
    )
    chunks.append(
        _format_sse_event(
            "action_result",
            {
                "success": result.success,
                "action_type": action_type,
                "time_cost": result.time_cost,
                "errors": list(result.errors),
                "metadata": dict(result.metadata),
                "narrative_hints": list(result.narrative_hints),
            },
        )
    )
    for event in result.sse_events:
        chunks.append(_format_sse_event(event.event_type, event.payload))
    chunks.append(
        _format_sse_event(
            "stream_end",
            {
                "reason": "completed",
                "success": result.success,
            },
        )
    )
    return _stream_response(chunks)


@app.post("/api/game/{world_id}/sessions/{session_id}/action/stream")
async def action_stream(
    world_id: str,
    session_id: str,
    request: StructuredActionRequest,
) -> StreamingResponse:
    """Execute one structured action and stream the result."""

    session = await _load_session_or_404(world_id, session_id)
    result = await _execute_structured_action(session, request)
    chunks = [
        _format_sse_event(
            "action_result",
            {
                "success": result.success,
                "action_type": request.action_type,
                "time_cost": result.time_cost,
                "errors": list(result.errors),
                "metadata": dict(result.metadata),
                "narrative_hints": list(result.narrative_hints),
            },
        )
    ]
    for event in result.sse_events:
        chunks.append(_format_sse_event(event.event_type, event.payload))
    chunks.append(
        _format_sse_event(
            "stream_end",
            {
                "reason": "completed",
                "success": result.success,
            },
        )
    )
    return _stream_response(chunks)


@app.post("/api/game/{world_id}/sessions/{session_id}/interact/stream")
async def interact_stream(
    world_id: str,
    session_id: str,
    request: InteractRequest,
) -> StreamingResponse:
    """Execute one minimal target-aware interaction and stream the result."""

    session = await _load_session_or_404(world_id, session_id)
    normalized = await get_input_port().process_action(
        {
            "channel": "interaction",
            "payload": request.model_dump(),
        }
    )
    interaction_result = await get_interaction_service().execute(session, normalized)
    chunks = [
        _format_sse_event(event.event_type, event.payload)
        for event in interaction_result.events
    ]
    chunks.append(
        _format_sse_event(
            "stream_end",
            {
                "reason": interaction_result.reason,
                "success": interaction_result.success,
            },
        )
    )
    return _stream_response(chunks)
