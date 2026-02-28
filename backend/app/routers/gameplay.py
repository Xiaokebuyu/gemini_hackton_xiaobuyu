"""Gameplay action and streaming routes."""

from __future__ import annotations

import asyncio
from typing import Any, Mapping

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.api_models import (
    ActionExecutionResponse,
    InteractRequest,
    NavigateRequest,
    PlayerLocationBody,
    StructuredActionRequest,
    TextInputRequest,
)
from app.deps import (
    _api_error,
    _execute_structured_action,
    _load_session_or_404,
    get_game_runtime,
    get_input_port,
    get_interaction_service,
)
from app.game_core import ManagedSession
from app.game_core.adapters.presentation import format_sse_event
from app.game_core.orchestration.models import PipelineResult, SSEEvent

router = APIRouter()

_SSE_HEADERS = {"Cache-Control": "no-cache"}


def _action_execution_response(
    result: PipelineResult,
    action_type: str,
    session: ManagedSession,
) -> ActionExecutionResponse:
    """Convert one pipeline result into the JSON action response model."""

    player = session.runtime.state.player
    return ActionExecutionResponse(
        success=result.success,
        action_type=action_type,
        time_cost=result.time_cost,
        metadata=dict(result.metadata),
        errors=list(result.errors),
        player_location=PlayerLocationBody(
            area_id=player.current_area,
            location_id=player.current_location,
        ),
    )


@router.post(
    "/api/game/{world_id}/sessions/{session_id}/navigate",
    response_model=ActionExecutionResponse,
)
async def navigate(
    world_id: str,
    session_id: str,
    request: NavigateRequest,
) -> ActionExecutionResponse:
    """Execute one navigation action through the real runtime pipeline."""

    runtime = get_game_runtime()
    lock = await runtime.session_lock(session_id)
    async with lock:
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


@router.post("/api/game/{world_id}/sessions/{session_id}/action/stream")
async def action_stream(
    world_id: str,
    session_id: str,
    request: StructuredActionRequest,
) -> StreamingResponse:
    """Execute one structured action and stream the result."""

    runtime = get_game_runtime()
    lock = await runtime.session_lock(session_id)
    queue: asyncio.Queue[SSEEvent | None] = asyncio.Queue()

    async def _execute() -> None:
        try:
            async with lock:
                session = await _load_session_or_404(world_id, session_id)
                result = await _execute_structured_action(
                    session, request, event_sink=queue.put,
                )
                await queue.put(_build_action_result_event(result, request.action_type))
                await queue.put(_build_stream_end_event("completed", result.success))
        except Exception as exc:
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


@router.post("/api/game/{world_id}/sessions/{session_id}/input/stream")
async def input_stream(
    world_id: str,
    session_id: str,
    request: TextInputRequest,
) -> StreamingResponse:
    """Parse one minimal text command and stream the execution result."""

    runtime = get_game_runtime()
    lock = await runtime.session_lock(session_id)
    queue: asyncio.Queue[SSEEvent | None] = asyncio.Queue()

    async def _execute() -> None:
        try:
            async with lock:
                session = await _load_session_or_404(world_id, session_id)
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
                await queue.put(_build_stream_end_event("completed", result.success))
        except Exception as exc:
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


@router.post("/api/game/{world_id}/sessions/{session_id}/interact/stream")
async def interact_stream(
    world_id: str,
    session_id: str,
    request: InteractRequest,
) -> StreamingResponse:
    """Execute one minimal target-aware interaction and stream the result."""

    runtime = get_game_runtime()
    lock = await runtime.session_lock(session_id)

    async def _generate():
        async with lock:
            try:
                session = await _load_session_or_404(world_id, session_id)
                normalized = await get_input_port().process_action(
                    {
                        "channel": "interaction",
                        "payload": request.model_dump(),
                    }
                )
                interaction_result = await get_interaction_service().execute(
                    session, normalized,
                )
                for event in interaction_result.events:
                    event_type = getattr(event, "event_type", None)
                    payload = getattr(event, "payload", None)
                    if event_type is not None and payload is not None:
                        yield format_sse_event(str(event_type), dict(payload))
                yield format_sse_event("stream_end", {
                    "reason": interaction_result.reason,
                    "success": interaction_result.success,
                })
            except Exception as exc:
                yield format_sse_event("stream_error", _error_payload(exc))
                yield format_sse_event("stream_end", {
                    "reason": "error", "success": False,
                })

    return StreamingResponse(
        _generate(), media_type="text/event-stream", headers=_SSE_HEADERS,
    )
