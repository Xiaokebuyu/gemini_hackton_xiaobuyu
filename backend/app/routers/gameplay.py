"""Gameplay action and streaming routes."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Mapping

logger = logging.getLogger(__name__)

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.api_models import (
    InteractRequest,
    NavigateRequest,
    PrivateChatRequest,
    StructuredActionRequest,
    TextInputRequest,
)
from app.deps import (
    _api_error,
    _execute_structured_action,
    _load_session_or_404,
    get_agent_orchestration,
    get_game_runtime,
    get_input_port,
    get_interaction_service,
)
from app.game_core import ManagedSession
from app.game_core.adapters.presentation import format_sse_event
from app.game_core.orchestration.models import PipelineResult, SSEEvent
from app.scene_views import build_location_overview, build_scene_change

router = APIRouter()

_SSE_HEADERS = {"Cache-Control": "no-cache"}


@router.post("/api/game/{world_id}/sessions/{session_id}/navigate")
async def navigate(
    world_id: str,
    session_id: str,
    request: NavigateRequest,
) -> StreamingResponse:
    """Execute one navigation action and stream the result as SSE.

    Parameter validation raises HTTP 4xx before the stream starts so clients
    get proper error codes for malformed requests. Execution failures (unknown
    area, blocked path) surface as action_result with success=False inside the
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
        result = await _execute_structured_action(session, structured_request, event_sink=queue.put)
        await queue.put(_build_action_result_event(result, action))
        if result.success:
            await queue.put(SSEEvent("scene_change", build_scene_change(session)))
        await queue.put(SSEEvent("location_overview", build_location_overview(session)))
        await queue.put(_build_stream_end_event("completed", result.success))

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
    return SSEEvent(
        event_type="action_result",
        payload={
            "success": result.success,
            "action_type": action_type,
            "time_cost": result.time_cost,
            "errors": list(result.errors),
            "metadata": dict(result.metadata),
            "narrative_hints": list(result.narrative_hints),
        },
    )


def _build_stream_end_event(reason: str, success: bool) -> SSEEvent:
    """Build a stream-termination signal."""
    return SSEEvent(
        event_type="stream_end",
        payload={"reason": reason, "success": success},
    )


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

    runtime = get_game_runtime()
    lock = await runtime.session_lock(session_id)
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

    async def _execute(session: ManagedSession, queue: asyncio.Queue[SSEEvent | None]) -> None:
        result = await _execute_structured_action(session, request, event_sink=queue.put)
        await queue.put(_build_action_result_event(result, request.action_type))
        agent_svc = get_agent_orchestration()
        if agent_svc is not None and result.success:
            async def _text_chunk_sink_action(chunk: str) -> None:
                await queue.put(SSEEvent("text_chunk", {"text": chunk}))
            reaction_events = await agent_svc.generate_post_action_reactions(
                session=session, result=result,
                text_chunk_sink=_text_chunk_sink_action,
            )
            for evt in reaction_events:
                await queue.put(evt)
            if reaction_events:
                await get_game_runtime().save_session(session)
        await queue.put(SSEEvent("location_overview", build_location_overview(session)))
        await queue.put(_build_stream_end_event("completed", result.success))

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
        result = await _execute_structured_action(
            session,
            StructuredActionRequest(action_type=action_type, params=params),
            event_sink=queue.put,
        )
        await queue.put(_build_action_result_event(result, action_type))
        agent_svc = get_agent_orchestration()
        if agent_svc is not None and result.success:
            async def _text_chunk_sink_input(chunk: str) -> None:
                await queue.put(SSEEvent("text_chunk", {"text": chunk}))
            reaction_events = await agent_svc.generate_post_action_reactions(
                session=session, result=result,
                text_chunk_sink=_text_chunk_sink_input,
            )
            for evt in reaction_events:
                await queue.put(evt)
            if reaction_events:
                await get_game_runtime().save_session(session)
        await queue.put(SSEEvent("location_overview", build_location_overview(session)))
        await queue.put(_build_stream_end_event("completed", result.success))

    return await _stream_with_lock(world_id, session_id, _execute)


@router.post("/api/game/{world_id}/sessions/{session_id}/interact/stream")
async def interact_stream(
    world_id: str,
    session_id: str,
    request: InteractRequest,
) -> StreamingResponse:
    """Execute one minimal target-aware interaction and stream the result."""

    async def _execute(session: ManagedSession, queue: asyncio.Queue[SSEEvent | None]) -> None:
        normalized = await get_input_port().process_action(
            {"channel": "interaction", "payload": request.model_dump()},
        )
        interaction_result = await get_interaction_service().execute(session, normalized)
        for event in interaction_result.events:
            event_type = getattr(event, "event_type", None)
            payload = getattr(event, "payload", None)
            if event_type is not None and payload is not None:
                await queue.put(SSEEvent(str(event_type), dict(payload)))

        # Full 6-step NPC interaction (Steps 2-6)
        agent_svc = get_agent_orchestration()
        npc_id = (request.target_id or request.npc_id or "").strip()
        should_call_npc = (
            agent_svc is not None
            and interaction_result.success
            and npc_id
            and request.message
            and request.intent in ("talk", "greet", "ask", "chat")
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
            )
            for evt in npc_events:
                await queue.put(evt)
            if npc_events:
                await get_game_runtime().save_session(session)

        await queue.put(SSEEvent("location_overview", build_location_overview(session)))
        await queue.put(SSEEvent("stream_end", {
            "reason": interaction_result.reason,
            "success": interaction_result.success,
        }))

    return await _stream_with_lock(world_id, session_id, _execute)


@router.post("/api/game/{world_id}/sessions/{session_id}/private_chat/stream")
async def private_chat_stream(
    world_id: str,
    session_id: str,
    request: PrivateChatRequest,
) -> StreamingResponse:
    """Initiate a private conversation with an NPC (no GM/teammate observation)."""

    async def _execute(session: ManagedSession, queue: asyncio.Queue[SSEEvent | None]) -> None:
        agent_svc = get_agent_orchestration()
        if agent_svc is None:
            await queue.put(SSEEvent("stream_error", {"code": "no_llm"}))
            await queue.put(SSEEvent("stream_end", {"reason": "no_llm", "success": False}))
            return

        async def _text_chunk_sink_private(chunk: str) -> None:
            await queue.put(SSEEvent("text_chunk", {"text": chunk}))

        events = await agent_svc.run_private_chat(
            session=session,
            npc_id=request.npc_id,
            player_message=request.message,
            text_chunk_sink=_text_chunk_sink_private,
        )
        if not events:
            # run_private_chat returns [] only when NPC is not found.
            await queue.put(SSEEvent("npc_error", {
                "npc_id": request.npc_id, "code": "npc_not_found",
            }))
            await queue.put(SSEEvent("stream_end", {"reason": "npc_not_found", "success": False}))
            return
        for evt in events:
            await queue.put(evt)
        chat_succeeded = not any(
            e.event_type in ("npc_error", "stream_error") for e in events
        )
        if chat_succeeded:
            await get_game_runtime().save_session(session)
        await queue.put(SSEEvent("location_overview", build_location_overview(session)))
        reason = "completed" if chat_succeeded else "failed"
        await queue.put(SSEEvent("stream_end", {"reason": reason, "success": chat_succeeded}))

    return await _stream_with_lock(world_id, session_id, _execute)


@router.post("/api/game/{world_id}/sessions/{session_id}/save", status_code=204)
async def save_session_explicit(world_id: str, session_id: str) -> None:
    """Explicitly persist the current session state."""
    session = await _load_session_or_404(world_id, session_id)
    await get_game_runtime().save_session(session)
