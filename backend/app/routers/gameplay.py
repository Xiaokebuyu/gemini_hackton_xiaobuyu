"""Gameplay action and streaming routes."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Mapping

logger = logging.getLogger(__name__)

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response, StreamingResponse

from app.api_models import (
    CompanionRequest,
    InteractRequest,
    NavigateRequest,
    StructuredActionRequest,
    TextInputRequest,
)
from app.deps import (
    _api_error,
    _execute_structured_action,
    _finalize_dialogue_turn,
    _load_session_or_404,
    _session_phase,
    get_admin_coordinator,
    get_agent_orchestration,
    get_game_runtime,
    get_input_port,
    get_interaction_service,
)
from app.game_core import ManagedSession
from app.game_core.location_utils import scene_position
from app.game_core.result_semantics import outcome_passed
from app.game_core.adapters.presentation import format_sse_event
from app.game_core.orchestration.models import PipelineResult, SSEEvent
from app.game_core.rules.models import Command
from app.opening_views import (
    build_opening_character_enters,
    build_opening_comment,
    build_opening_dialogue_options,
    build_opening_narration,
    build_opening_status_snapshot,
)
from app.image_prefetch import get_asset_resolver, prefetch_sub_area_backgrounds
from app.scene_views import build_location_overview, build_scene_change
from app.utterance_orchestration import (
    UtteranceOrchestrator,
    UtteranceRequest,
    UtteranceTarget,
    build_utterance_request,
)

router = APIRouter()

_SSE_HEADERS = {"Cache-Control": "no-cache"}
_AGENT_REACTION_EVENTS = frozenset({
    "gm_narration",
    "gm_comment",
    "npc_response",
    "npc_emote",
    "teammate_response",
})


@router.post("/api/game/{world_id}/sessions/{session_id}/navigate")
async def navigate(
    world_id: str,
    session_id: str,
    request: NavigateRequest,
) -> StreamingResponse:
    """Execute one navigation action and stream the result as SSE.

    Parameter validation raises HTTP 4xx before the stream starts so clients
    get proper error codes for malformed requests. Execution failures (unknown
    area, blocked path) surface as action_result with executed=False inside the
    stream.
    """
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
            raise _api_error(400, "invalid_navigation_request", "location_id is required")
        params["location_id"] = location_id
        area_id = (request.area_id or "").strip()
        if area_id:
            params["area_id"] = area_id
    else:
        area_id = (request.area_id or "").strip()
        if area_id:
            params["area_id"] = area_id

    structured_request = StructuredActionRequest(action_type=action, params=params)

    async def _execute(session: ManagedSession, queue: asyncio.Queue[SSEEvent | None]) -> None:
        before_location = session.runtime.state.player.current_location
        result = await _execute_structured_action(session, structured_request)
        await queue.put(_build_action_result_event(result, action))
        if result.executed:
            # Non-blocking prefetch: generate background images for any new
            # temporary sub-areas that were created during this tick.
            _schedule_sub_area_prefetch(result, session)
            if action == "leave_sub_location":
                previous_location = _non_empty_string(before_location)
                if previous_location is not None:
                    await _reset_hostile_to_spotted(session, previous_location)
            resolver = get_asset_resolver()
            await queue.put(SSEEvent("scene_change", build_scene_change(session, asset_resolver=resolver)))
        for event in result.sse_events:
            await queue.put(event)
        if result.executed:
            if action == "enter_sub_location":
                await queue.put(SSEEvent("status_update", build_opening_status_snapshot(session)))
                await queue.put(SSEEvent("location_overview", build_location_overview(session)))
                location_id = _non_empty_string(params.get("location_id"))
                if location_id is not None:
                    await _emit_hostile_entry_events(queue, session, sub_area_id=location_id)
                await queue.put(_build_stream_end_event("completed", result.executed))
                return
        await queue.put(SSEEvent("status_update", build_opening_status_snapshot(session)))
        await queue.put(SSEEvent("location_overview", build_location_overview(session)))
        await queue.put(_build_stream_end_event("completed", result.executed))

    return await _stream_with_lock(world_id, session_id, _execute)


# ---------------------------------------------------------------------------
# Streaming routes — true async SSE via asyncio.Queue + create_task
# ---------------------------------------------------------------------------


def _error_payload(exc: Exception) -> dict[str, Any]:
    """Extract a serialisable payload from an exception."""
    if isinstance(exc, HTTPException):
        detail = exc.detail
        return detail if isinstance(detail, dict) else {"message": str(detail)}
    return {"message": str(exc)}


def _schedule_sub_area_prefetch(result: PipelineResult, session: ManagedSession) -> None:
    """Fire non-blocking background-image generation for new temporary sub-areas.

    Reads the current in-game time period from session state and delegates to
    prefetch_sub_area_backgrounds().  Failures are silently logged inside the
    per-task coroutine — this call always returns immediately.
    """
    time_period = "day"
    try:
        if session.runtime.state.has_slice("time"):
            time_period = str(session.runtime.state.time.period or "day")
    except Exception:
        pass  # defensive — time slice access failure must not disrupt navigation
    prefetch_sub_area_backgrounds(result, time_period=time_period)


# ---------------------------------------------------------------------------
# Endpoint-level stream event builders (presentation / transport events).
#
# These are NOT game-core events.  Game-core events (settlement hooks,
# dice_roll, scene_change, etc.) are produced by TickCoordinator and
# travel through PipelineResult.sse_events → event_sink.
#
# Distinction rule:
#   "事件内容代表游戏世界里发生了什么" → 编排层产出 (PipelineResult.sse_events)
#   "事件内容代表 SSE 会话该怎么和客户端交互" → 应用层构造 (下方 helpers)
# ---------------------------------------------------------------------------


def _build_action_result_event(
    result: PipelineResult, action_type: str,
) -> SSEEvent:
    """Summarise one PipelineResult as an SSE envelope event."""
    outcome = _result_outcome(result)
    return SSEEvent(
        event_type="action_result",
        payload={
            "executed": _result_executed(result),
            "outcome": outcome,
            "action_type": action_type,
            "time_cost": result.time_cost,
            "errors": list(result.errors),
            "metadata": dict(result.metadata),
            "narrative_hints": list(result.narrative_hints),
            "rolls": [_build_action_result_roll_payload(roll) for roll in result.rolls],
        },
    )


def _build_companion_result_event(result: PipelineResult) -> SSEEvent | None:
    event_type = _non_empty_string(result.metadata.get("event_type"))
    if event_type not in {"companion_recruited", "companion_dismissed"}:
        return None
    npc_id = _non_empty_string(result.metadata.get("npc_id"))
    if npc_id is None:
        return None
    return SSEEvent(
        event_type=event_type,
        payload={
            "npc_id": npc_id,
            "reason": _non_empty_string(result.metadata.get("reason")) or "",
            "party_members": list(result.metadata.get("party_members", [])),
        },
    )


def _build_stream_end_event(reason: str, completed: bool) -> SSEEvent:
    """Build a stream-termination signal."""
    return SSEEvent(
        event_type="stream_end",
        payload={"reason": reason, "completed": completed},
    )


def _non_empty_string(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _player_scene_position(session: ManagedSession) -> tuple[str, str | None, str | None]:
    player = session.runtime.state.player
    return scene_position(
        player.current_area,
        player.current_location,
        getattr(player, "current_room", None),
    )


def _result_executed(result: PipelineResult) -> bool:
    return result.executed


def _result_outcome(result: PipelineResult) -> dict[str, Any] | None:
    outcome = result.metadata.get("outcome")
    if not isinstance(outcome, Mapping):
        return None
    return dict(outcome)


def _build_dialogue_turn_record(
    *,
    kind: str,
    npc_id: str | None = None,
    intent: str | None = None,
    scope: str | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {}
    normalized_npc_id = _non_empty_string(npc_id)
    if normalized_npc_id is not None:
        params["npc_id"] = normalized_npc_id
    normalized_intent = _non_empty_string(intent)
    if normalized_intent is not None:
        params["intent"] = normalized_intent
    normalized_scope = _non_empty_string(scope)
    if normalized_scope is not None:
        params["scope"] = normalized_scope
    return {
        "type": kind,
        "actor": "player",
        "params": params,
        "executed": True,
        "source": "external_turn",
    }


def _coerce_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _modifier_total(roll: Any) -> int:
    total = 0
    for item in getattr(roll, "modifiers", []):
        try:
            total += int(item.get("value", 0))
        except (AttributeError, TypeError, ValueError):
            continue
    return total


def _build_action_result_roll_payload(roll: Any) -> dict[str, Any]:
    return {
        "purpose": str(getattr(roll, "purpose", "")),
        "dice": str(getattr(roll, "dice", "")),
        "result": _coerce_int(getattr(roll, "result", 0)),
        "total": _coerce_int(getattr(roll, "total", 0)),
        "critical": getattr(roll, "critical", None),
        "modifiers": list(getattr(roll, "modifiers", [])),
    }


def _roll_descriptor(
    *,
    result: PipelineResult,
    roll: Any,
) -> tuple[str, int, bool] | None:
    purpose = str(getattr(roll, "purpose", "")).strip()
    metadata = result.metadata
    outcome = _result_outcome(result)
    if purpose == "skill_check":
        return (
            _non_empty_string(metadata.get("skill")) or "skill_check",
            _coerce_int(metadata.get("dc")),
            outcome_passed(outcome) if outcome is not None else bool(metadata.get("passed", False)),
        )
    if purpose == "saving_throw":
        return (
            _non_empty_string(metadata.get("ability")) or "saving_throw",
            _coerce_int(metadata.get("dc")),
            outcome_passed(outcome) if outcome is not None else bool(metadata.get("passed", False)),
        )
    if purpose == "contest_actor":
        actor_total = _coerce_int(metadata.get("actor_total"))
        target_total = _coerce_int(metadata.get("target_total"))
        winner = str((outcome or {}).get("winner") or metadata.get("winner") or "").strip()
        return (
            _non_empty_string(metadata.get("actor_skill")) or "contest",
            target_total,
            winner == "actor" if winner else actor_total > target_total,
        )
    if purpose == "contest_target":
        actor_total = _coerce_int(metadata.get("actor_total"))
        target_total = _coerce_int(metadata.get("target_total"))
        winner = str((outcome or {}).get("winner") or metadata.get("winner") or "").strip()
        return (
            _non_empty_string(metadata.get("target_skill")) or "contest",
            actor_total,
            winner == "target" if winner else target_total > actor_total,
        )
    if purpose == "investigate":
        found = outcome_passed(outcome)
        if found is None:
            found = metadata.get("status") == "discovered"
        return (
            _non_empty_string(metadata.get("skill")) or "investigate",
            0,
            found,
        )
    if purpose.startswith("discover_") or purpose.startswith("interact_"):
        return (
            _non_empty_string(metadata.get("skill")) or purpose,
            _coerce_int(metadata.get("dc")),
            outcome_passed(outcome) if outcome is not None else bool(metadata.get("passed", False)),
        )
    return None


def _build_dice_roll_event(
    *,
    result: PipelineResult,
    roll: Any,
    session: ManagedSession,
) -> SSEEvent | None:
    descriptor = _roll_descriptor(result=result, roll=roll)
    if descriptor is None:
        return None
    skill, dc, passed = descriptor
    return SSEEvent(
        "dice_roll",
        {
            "type": str(getattr(roll, "dice", "")),
            "result": _coerce_int(getattr(roll, "result", 0)),
            "modifier": _modifier_total(roll),
            "total": _coerce_int(getattr(roll, "total", 0)),
            "dc": dc,
            "passed": passed,
            "skill": skill,
            "roller": "player",
            "roller_name": session.runtime.state.player.character_name or "Player",
        },
    )


async def _emit_roll_events(
    queue: asyncio.Queue[SSEEvent | None],
    *,
    result: PipelineResult,
    session: ManagedSession,
) -> None:
    for roll in result.rolls:
        event = _build_dice_roll_event(result=result, roll=roll, session=session)
        if event is not None:
            await queue.put(event)


def _get_hostile_payload(
    session: ManagedSession,
    sub_area_id: str,
) -> dict[str, Any] | None:
    payload = session.runtime.state.areas.get_hostile_state(sub_area_id)
    if not isinstance(payload, Mapping):
        return None
    return session.runtime.state.areas.copy_hostile_state(payload)


async def _execute_command(
    session: ManagedSession,
    command: Command,
) -> PipelineResult:
    result = await session.runtime.tick_coordinator.process(command)
    await get_admin_coordinator().save_session(session)
    return result


async def _emit_hostile_entry_events(
    queue: asyncio.Queue[SSEEvent | None],
    session: ManagedSession,
    *,
    sub_area_id: str,
) -> None:
    payload = _get_hostile_payload(session, sub_area_id)
    if payload is None:
        return
    status = _non_empty_string(payload.get("status")) or "spotted"
    if bool(payload.get("cleared", False)) or bool(payload.get("combat_active", False)):
        return
    if status != "spotted":
        return

    result = await _execute_command(
        session,
        Command(
            type="enter_hostile",
            params={"sub_area_id": sub_area_id},
            source="system",
        ),
    )
    if not result.executed:
        return

    stealth_meta = dict(result.metadata)
    stealth_outcome = _result_outcome(result)
    stealth_passed = outcome_passed(stealth_outcome)
    if stealth_passed is None:
        stealth_passed = bool(stealth_meta.get("passed", False))
    for roll in result.rolls:
        modifier = 0
        for item in roll.modifiers:
            try:
                modifier += int(item.get("value", 0))
            except (AttributeError, TypeError, ValueError):
                continue
        await queue.put(
            SSEEvent(
                "dice_roll",
                {
                    "type": roll.dice,
                    "result": roll.result,
                    "modifier": modifier,
                    "total": roll.total,
                    "dc": int(stealth_meta.get("dc", 0)),
                    "passed": stealth_passed,
                    "skill": "stealth",
                    "roller": "player",
                    "roller_name": session.runtime.state.player.character_name or "Player",
                },
            )
        )

    await queue.put(
        SSEEvent(
            "stealth_result",
            {
                "passed": stealth_passed,
                "roll": int(stealth_meta.get("roll", 0)),
                "dc": int(stealth_meta.get("dc", 0)),
                "modifier": int(stealth_meta.get("modifier", 0)),
                "advantage": bool(stealth_meta.get("advantage", False)),
                "disadvantage": bool(stealth_meta.get("disadvantage", False)),
                "narrative": str(stealth_meta.get("narrative", "")),
                "options": list(stealth_meta.get("options", [])),
                "surprise_state": str(stealth_meta.get("surprise_state", "none")),
            },
        )
    )

    if stealth_passed:
        return

    start_result = await _execute_command(
        session,
        Command(
            type="start_combat",
            params={
                "sub_area_id": sub_area_id,
                "surprise_state": str(stealth_meta.get("surprise_state", "none")),
            },
            source="system",
        ),
    )
    if not start_result.executed:
        return

    combat_payload = _get_hostile_payload(session, sub_area_id)
    if combat_payload is None:
        return
    await queue.put(
        SSEEvent(
            "combat_start",
            {
                "sub_area_id": sub_area_id,
                "round": int(combat_payload.get("combat_round", 1)),
                "surprise_state": str(combat_payload.get("surprise_state", "none")),
                "blocking": bool(combat_payload.get("blocking", False)),
                "participants": _participant_cards(combat_payload, include_is_dead=False),
                "player": _player_card(session),
            },
        )
    )


async def _reset_hostile_to_spotted(
    session: ManagedSession,
    sub_area_id: str,
) -> None:
    payload = _get_hostile_payload(session, sub_area_id)
    if payload is None:
        return
    if bool(payload.get("cleared", False)) or bool(payload.get("combat_active", False)):
        return
    updated = session.runtime.state.areas.copy_hostile_state(payload)
    updated["status"] = "spotted"
    updated["entry_mode"] = None
    updated["last_stealth_result"] = None
    updated["last_stealth_choice"] = None
    session.runtime.state.areas.upsert_hostile(sub_area_id, updated)
    await get_admin_coordinator().save_session(session)


def _participant_cards(
    payload: Mapping[str, Any],
    *,
    include_is_dead: bool,
) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    raw_participants = payload.get("participants", [])
    if not isinstance(raw_participants, list):
        return cards
    for item in raw_participants:
        if not isinstance(item, Mapping):
            continue
        card = {
            "id": str(item.get("id", "")),
            "name": str(item.get("name") or item.get("monster_id") or "Unknown"),
            "hp": int(item.get("hp", 0)),
            "max_hp": int(item.get("max_hp", 0)),
            "status_effects": [
                str(effect.get("effect_name") or effect.get("effect_id") or "")
                for effect in item.get("active_effects", [])
                if isinstance(effect, Mapping)
                and str(effect.get("effect_name") or effect.get("effect_id") or "")
            ],
        }
        if include_is_dead:
            card["is_dead"] = not bool(item.get("alive", False))
        else:
            card["ac"] = int(item.get("ac", 10))
            card["is_player"] = False
        cards.append(card)
    return cards


def _player_card(session: ManagedSession) -> dict[str, Any]:
    player = session.runtime.state.player
    return {
        "hp": int(player.hp),
        "max_hp": int(player.max_hp),
        "ac": int(player.ac),
        "active_effects": [
            str(effect.get("effect_name") or effect.get("effect_id") or "")
            for effect in player.active_effects
            if isinstance(effect, Mapping)
            and str(effect.get("effect_name") or effect.get("effect_id") or "")
        ],
    }


def _build_stream_error_event(exc: Exception) -> SSEEvent:
    """Build a stream-error signal from an exception."""
    return SSEEvent(
        event_type="stream_error",
        payload=_error_payload(exc),
    )


async def _emit_terminal_error(
    queue: asyncio.Queue[SSEEvent | None], exc: Exception,
) -> None:
    """Push stream_error + stream_end into one queue (except-block helper)."""
    await queue.put(_build_stream_error_event(exc))
    await queue.put(_build_stream_end_event("error", False))


async def _stream_with_lock(
    world_id: str,
    session_id: str,
    execute_fn: Callable[[ManagedSession, asyncio.Queue[SSEEvent | None]], Awaitable[None]],
) -> StreamingResponse:
    """Run execute_fn inside a per-session lock and stream results via SSE.

    Encapsulates the queue + lock + task + generator skeleton shared by all
    streaming endpoints, leaving each caller to supply only its unique logic.

    Session validation is performed BEFORE the HTTP response is committed so
    that invalid world/session IDs return proper 4xx HTTP responses instead of
    being surfaced as stream_error events inside a 200 SSE stream.
    """
    # Pre-validate: raises HTTPException before HTTP 200 headers are sent.
    await _load_session_or_404(world_id, session_id)

    coordinator = get_admin_coordinator()
    lock = await coordinator.session_lock(world_id, session_id)
    queue: asyncio.Queue[SSEEvent | None] = asyncio.Queue()

    async def _execute() -> None:
        try:
            async with lock:
                # Re-load inside the lock to get fresh state.
                session = await _load_session_or_404(world_id, session_id)
                await execute_fn(session, queue)
        except Exception as exc:
            logger.exception("stream task failed for session %s", session_id)
            await _emit_terminal_error(queue, exc)
        finally:
            await queue.put(None)

    task = asyncio.create_task(_execute())

    async def _generate():
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield format_sse_event(event.event_type, event.payload)
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(
        _generate(), media_type="text/event-stream", headers=_SSE_HEADERS,
    )


@router.post("/api/game/{world_id}/sessions/{session_id}/action/stream")
async def action_stream(
    world_id: str,
    session_id: str,
    request: StructuredActionRequest,
) -> StreamingResponse:
    """Execute one structured action and stream the result."""
    session = await _load_session_or_404(world_id, session_id)
    action_type = request.action_type.strip()
    if not action_type:
        raise _api_error(400, "invalid_action_request", "action_type must be non-empty")
    if not isinstance(request.params, dict):
        raise _api_error(400, "invalid_action_request", "params must be an object")
    if not session.runtime.action_dispatcher.has_action(action_type):
        raise _api_error(400, "unknown_action", f"unknown action: {action_type}")

    async def _execute(session: ManagedSession, queue: asyncio.Queue[SSEEvent | None]) -> None:
        before_position = _player_scene_position(session)
        async def _after_engine(result: PipelineResult) -> None:
            await _emit_roll_events(queue, result=result, session=session)
            await queue.put(_build_action_result_event(result, request.action_type))

        result = await _execute_structured_action(
            session,
            request,
            event_sink=queue.put,
            after_engine=_after_engine,
        )
        if result.executed:
            after_position = _player_scene_position(session)
            if after_position != before_position:
                _schedule_sub_area_prefetch(result, session)
                resolver = get_asset_resolver()
                await queue.put(SSEEvent("scene_change", build_scene_change(session, asset_resolver=resolver)))
        await queue.put(SSEEvent("status_update", build_opening_status_snapshot(session)))
        await queue.put(SSEEvent("location_overview", build_location_overview(session)))
        await queue.put(_build_stream_end_event("completed", result.executed))

    return await _stream_with_lock(world_id, session_id, _execute)


@router.post("/api/game/{world_id}/sessions/{session_id}/input/stream")
async def input_stream(
    world_id: str,
    session_id: str,
    request: TextInputRequest,
) -> StreamingResponse:
    """Parse one minimal text command and stream the execution result."""

    async def _execute(session: ManagedSession, queue: asyncio.Queue[SSEEvent | None]) -> None:
        parsed = await get_input_port().process_text(request.text)
        normalized_text = str(parsed.get("normalized_text", "")).strip()

        if str(parsed.get("status", "")) != "parsed":
            await queue.put(SSEEvent(
                event_type="input_rejected",
                payload={
                    "text": request.text,
                    "normalized_text": normalized_text,
                    "code": str(parsed.get("code", "unsupported_input")),
                    "message": str(parsed.get(
                        "message",
                        "input is not a supported command alias",
                    )),
                },
            ))
            await queue.put(_build_stream_end_event("input_rejected", False))
            return

        action_type = str(parsed.get("action_type", "")).strip()
        raw_params = parsed.get("params", {})
        params = dict(raw_params) if isinstance(raw_params, Mapping) else {}
        await queue.put(SSEEvent(
            event_type="input_parsed",
            payload={
                "text": request.text,
                "normalized_text": normalized_text,
                "action_type": action_type,
                "params": params,
            },
        ))
        async def _after_engine(result: PipelineResult) -> None:
            await _emit_roll_events(queue, result=result, session=session)
            await queue.put(_build_action_result_event(result, action_type))

        before_position = _player_scene_position(session)
        result = await _execute_structured_action(
            session,
            StructuredActionRequest(action_type=action_type, params=params),
            event_sink=queue.put,
            after_engine=_after_engine,
        )
        if result.executed:
            after_position = _player_scene_position(session)
            if after_position != before_position:
                _schedule_sub_area_prefetch(result, session)
                resolver = get_asset_resolver()
                await queue.put(SSEEvent("scene_change", build_scene_change(session, asset_resolver=resolver)))
        await queue.put(SSEEvent("status_update", build_opening_status_snapshot(session)))
        await queue.put(SSEEvent("location_overview", build_location_overview(session)))
        await queue.put(_build_stream_end_event("completed", result.executed))

    return await _stream_with_lock(world_id, session_id, _execute)


@router.post("/api/game/{world_id}/sessions/{session_id}/opening/stream")
async def opening_stream(
    world_id: str,
    session_id: str,
) -> StreamingResponse:
    """Stream the deterministic new-game opening sequence."""

    session = await _load_session_or_404(world_id, session_id)
    current_phase = _session_phase(session)
    if current_phase != "opening_ready":
        raise _api_error(
            409,
            "opening_not_available",
            "opening is only available for opening-ready sessions",
        )
    if not session.runtime.state.player.current_area.strip():
        raise _api_error(409, "opening_not_available", "player has no current area")

    async def _execute(session: ManagedSession, queue: asyncio.Queue[SSEEvent | None]) -> None:
        current_phase = _session_phase(session)
        if current_phase != "opening_ready":
            raise _api_error(
                409,
                "opening_not_available",
                "opening is only available for opening-ready sessions",
            )

        bootstrap_events = await get_game_runtime().bootstrap_opening_planner(
            session,
            persist=True,
        )
        for event in bootstrap_events:
            await queue.put(event)

        await queue.put(SSEEvent("scene_change", build_scene_change(session)))

        opening_sequence = None
        agent_svc = get_agent_orchestration()
        if agent_svc is not None:
            opening_sequence = await agent_svc.generate_opening_sequence(session)
            logger.info("[opening] LLM opening_sequence: narration=%s, comment=%s",
                        opening_sequence.narration_event is not None if opening_sequence else "N/A",
                        opening_sequence.comment_event is not None if opening_sequence else "N/A")
        else:
            logger.info("[opening] agent_svc is None — using deterministic fallback")

        narration_event = opening_sequence.narration_event if opening_sequence is not None else None
        if narration_event is not None:
            await queue.put(narration_event)
        else:
            narration = build_opening_narration(session).strip()
            logger.info("[opening] fallback narration len=%d, preview=%.100s",
                        len(narration), narration[:100] if narration else "(empty)")
            if narration:
                await queue.put(SSEEvent("gm_narration", {"content": narration}))

        comment_event = opening_sequence.comment_event if opening_sequence is not None else None
        if comment_event is not None:
            await queue.put(comment_event)
        else:
            gm_comment = build_opening_comment(session)
            if gm_comment.get("content"):
                await queue.put(SSEEvent("gm_comment", dict(gm_comment)))

        for payload in build_opening_character_enters(session):
            await queue.put(SSEEvent("character_enter", payload))

        await queue.put(SSEEvent("status_update", build_opening_status_snapshot(session)))
        # Opening dialogue_options removed — location_overview provides all
        # scene-based options via buildFromOverview (room-aware NPC filtering).
        await queue.put(SSEEvent("location_overview", build_location_overview(session)))
        session.phase = "active"
        await get_admin_coordinator().save_session(session)
        await queue.put(_build_stream_end_event("completed", True))

    return await _stream_with_lock(world_id, session_id, _execute)


@router.post("/api/game/{world_id}/sessions/{session_id}/interact/stream")
async def interact_stream(
    world_id: str,
    session_id: str,
    request: InteractRequest,
) -> StreamingResponse:
    """Execute one minimal target-aware interaction and stream the result."""

    async def _execute(session: ManagedSession, queue: asyncio.Queue[SSEEvent | None]) -> None:
        utterance = build_utterance_request(request.model_dump())
        if utterance is not None:
            async def _text_chunk_sink_utterance(chunk: str) -> None:
                await queue.put(SSEEvent("text_chunk", {"text": chunk}))

            utterance_result = await UtteranceOrchestrator(
                get_agent_orchestration(),
            ).execute(
                session,
                utterance,
                text_chunk_sink=_text_chunk_sink_utterance,
            )
            for event in utterance_result.events:
                await queue.put(event)
            if utterance_result.completed and utterance_result.turn_kind is not None:
                await _finalize_dialogue_turn(
                    session,
                    time_cost=utterance_result.time_cost,
                    event_sink=queue.put,
                    turn_action_record=_build_dialogue_turn_record(
                        kind=utterance_result.turn_kind,
                        npc_id=utterance_result.turn_npc_id,
                        intent=utterance.intent,
                        scope=utterance_result.turn_scope,
                    ),
                )
            await queue.put(SSEEvent("status_update", build_opening_status_snapshot(session)))
            await queue.put(SSEEvent("location_overview", build_location_overview(session)))
            await queue.put(SSEEvent("stream_end", {
                "reason": utterance_result.reason,
                "completed": utterance_result.completed,
            }))
            return

        normalized = await get_input_port().process_action(
            {"channel": "interaction", "payload": request.model_dump()},
        )
        interaction_result = await get_interaction_service().execute(session, normalized)
        stream_completed = interaction_result.completed
        stream_reason = interaction_result.reason
        for event in interaction_result.events:
            event_type = getattr(event, "event_type", None)
            payload = getattr(event, "payload", None)
            if event_type is not None and payload is not None:
                await queue.put(SSEEvent(str(event_type), dict(payload)))

        # ── Skill check execution (dialogue option with check) ──
        check_result: dict[str, Any] | None = None
        if request.check_skill and request.check_dc:
            check_cmd = Command(
                type="skill_check",
                params={"skill": request.check_skill, "dc": request.check_dc},
                source="player",
            )
            exec_result = session.runtime.rules_engine.execute(
                check_cmd, session.runtime.state, session.runtime.world,
            )
            session.runtime.tick_coordinator.apply_external_result(exec_result)

            if exec_result.rolls:
                roll = exec_result.rolls[0]
                check_pipeline_result = PipelineResult(
                    executed=exec_result.executed,
                    action_type="skill_check",
                    time_cost=exec_result.time_cost,
                    errors=list(exec_result.errors),
                    narrative_hints=list(exec_result.narrative_hints),
                    rolls=list(exec_result.rolls),
                    metadata=dict(exec_result.metadata),
                )
                dice_event = _build_dice_roll_event(
                    result=check_pipeline_result,
                    roll=roll,
                    session=session,
                )
                if dice_event is not None:
                    outcome = _result_outcome(check_pipeline_result)
                    passed = outcome_passed(outcome)
                    dice_event.payload.update({
                        "roll": roll.result,
                        "passed": passed if passed is not None else bool(exec_result.metadata.get("passed", False)),
                        "narrative_hints": list(exec_result.narrative_hints),
                    })
                    await queue.put(dice_event)

            outcome = check_pipeline_result.metadata.get("outcome") if exec_result.rolls else exec_result.metadata.get("outcome")
            passed = outcome_passed(outcome) if isinstance(outcome, Mapping) else None
            check_result = {
                "skill": request.check_skill,
                "dc": request.check_dc,
                "passed": passed if passed is not None else bool(exec_result.metadata.get("passed", False)),
                "total": exec_result.rolls[0].total if exec_result.rolls else 0,
                "narrative_hints": list(exec_result.narrative_hints),
                "outcome": dict(outcome) if isinstance(outcome, Mapping) else None,
                "grade": str(exec_result.metadata.get("grade") or ("good" if passed else "bad")),
                "margin": int(exec_result.metadata.get("margin", 0)),
            }
            if not bool(exec_result.executed):
                stream_completed = False
                stream_reason = exec_result.errors[0] if exec_result.errors else "check_execution_failed"

        # Apply clue check result if this skill check was for a simplified clue
        if check_result and request.clue_id:
            clue_check_cmd = Command(
                type="apply_clue_check_result",
                params={
                    "interactable_id": request.clue_id,
                    "passed": check_result["passed"],
                    "grade": check_result.get("grade", "good" if check_result["passed"] else "bad"),
                    "margin": check_result.get("margin", 0),
                },
                source="player",
            )
            clue_exec = session.runtime.rules_engine.execute(
                clue_check_cmd, session.runtime.state, session.runtime.world,
            )
            session.runtime.tick_coordinator.apply_external_result(clue_exec)

        # Full 6-step NPC interaction (Steps 2-6)
        agent_svc = get_agent_orchestration()
        npc_id = (request.target_id or request.npc_id or "").strip()
        should_call_npc = (
            agent_svc is not None
            and interaction_result.completed
            and stream_completed
            and npc_id
            and request.message
            and request.intent in ("talk", "greet", "ask", "chat")
        )
        should_call_free_chat = (
            agent_svc is not None
            and not should_call_npc
            and interaction_result.completed
            and request.message
            and request.intent in ("chat",)
        )
        if should_call_npc:
            async def _text_chunk_sink_interact(chunk: str) -> None:
                await queue.put(SSEEvent("text_chunk", {"text": chunk}))
            npc_events = await agent_svc.run_npc_interaction(
                session=session,
                npc_id=npc_id,
                player_message=request.message,
                intent=request.intent or "talk",
                text_chunk_sink=_text_chunk_sink_interact,
                check_result=check_result,
            )
            for evt in npc_events:
                await queue.put(evt)
            dialogue_succeeded = not any(
                evt.event_type in ("npc_error", "npc_response_error")
                for evt in npc_events
            )
            stream_completed = stream_completed and dialogue_succeeded
            if not dialogue_succeeded:
                stream_reason = "dialogue_failed"
            if dialogue_succeeded and npc_events:
                await _finalize_dialogue_turn(
                    session,
                    time_cost=1 / 6,
                    event_sink=queue.put,
                    turn_action_record=_build_dialogue_turn_record(
                        kind="dialogue_turn",
                        npc_id=npc_id,
                        intent=request.intent,
                    ),
                )
        elif should_call_free_chat:
            free_chat_events = await agent_svc.run_free_chat(
                session=session,
                player_message=request.message,
            )
            for evt in free_chat_events:
                await queue.put(evt)
            chat_succeeded = not any(
                evt.event_type in ("npc_error", "stream_error")
                for evt in free_chat_events
            )
            stream_completed = stream_completed and chat_succeeded
            if not chat_succeeded:
                stream_reason = "free_chat_failed"
            if chat_succeeded and free_chat_events:
                await _finalize_dialogue_turn(
                    session,
                    time_cost=1 / 6,
                    event_sink=queue.put,
                    turn_action_record={
                        "type": "free_chat_turn",
                        "actor": "player",
                        "params": {},
                        "executed": True,
                        "source": "external_turn",
                    },
                )

        await queue.put(SSEEvent("status_update", build_opening_status_snapshot(session)))
        await queue.put(SSEEvent("location_overview", build_location_overview(session)))
        await queue.put(SSEEvent("stream_end", {
            "reason": stream_reason,
            "completed": stream_completed,
        }))

    return await _stream_with_lock(world_id, session_id, _execute)


# ---------------------------------------------------------------------------
# Companion recruit / dismiss
# ---------------------------------------------------------------------------


@router.post("/api/game/{world_id}/sessions/{session_id}/companion/recruit")
async def companion_recruit(
    world_id: str,
    session_id: str,
    request: CompanionRequest,
) -> StreamingResponse:
    """Recruit an NPC as a companion and stream the result."""

    async def _execute(session: ManagedSession, queue: asyncio.Queue[SSEEvent | None]) -> None:
        result = await _execute_command(
            session,
            Command(
                type="recruit_companion",
                params={"npc_id": request.npc_id},
                source="player",
            ),
        )
        await queue.put(_build_action_result_event(result, "recruit_companion"))
        companion_event = _build_companion_result_event(result)
        if companion_event is not None:
            await queue.put(companion_event)
        for event in result.sse_events:
            await queue.put(event)
        await queue.put(SSEEvent("status_update", build_opening_status_snapshot(session)))
        await queue.put(SSEEvent("location_overview", build_location_overview(session)))
        reason = (
            result.errors[0]
            if result.errors else ("completed" if result.executed else "failed")
        )
        await queue.put(_build_stream_end_event(reason, result.executed))

    return await _stream_with_lock(world_id, session_id, _execute)


@router.post("/api/game/{world_id}/sessions/{session_id}/companion/dismiss")
async def companion_dismiss(
    world_id: str,
    session_id: str,
    request: CompanionRequest,
) -> StreamingResponse:
    """Dismiss a companion and stream the result."""

    async def _execute(session: ManagedSession, queue: asyncio.Queue[SSEEvent | None]) -> None:
        result = await _execute_command(
            session,
            Command(
                type="dismiss_companion",
                params={"npc_id": request.npc_id},
                source="player",
            ),
        )
        await queue.put(_build_action_result_event(result, "dismiss_companion"))
        companion_event = _build_companion_result_event(result)
        if companion_event is not None:
            await queue.put(companion_event)
        for event in result.sse_events:
            await queue.put(event)
        await queue.put(SSEEvent("status_update", build_opening_status_snapshot(session)))
        await queue.put(SSEEvent("location_overview", build_location_overview(session)))
        reason = (
            result.errors[0]
            if result.errors else ("completed" if result.executed else "failed")
        )
        await queue.put(_build_stream_end_event(reason, result.executed))

    return await _stream_with_lock(world_id, session_id, _execute)


@router.post(
    "/api/game/{world_id}/sessions/{session_id}/save",
    status_code=204,
    response_class=Response,
)
async def save_session_explicit(world_id: str, session_id: str) -> Response:
    """Explicitly persist the current session state."""
    session = await _load_session_or_404(world_id, session_id)
    await get_admin_coordinator().save_session(session)
    return Response(status_code=204)
