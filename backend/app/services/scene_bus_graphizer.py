"""SceneBus graphization adapters for Direction A.1 persistent layer."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Optional

from app.models.context_window import GraphizeRequest, WindowMessage
from app.models.graph_elements import GraphizeResult
from app.models.graph_scope import GraphScope
from app.world.scene_bus import BusEntry, SceneBus

logger = logging.getLogger(__name__)


def _estimate_tokens(text: str) -> int:
    """Estimate token usage for a plain text entry."""
    return max(1, len(text) // 4)


def _entry_to_window_message(entry: BusEntry) -> WindowMessage:
    """Convert SceneBus entry to GraphizeRequest message format."""
    role = "user" if entry.actor == "player" else "assistant"
    if entry.actor == "engine":
        role = "system"

    return WindowMessage(
        id=f"scene_bus_{entry.id}",
        role=role,
        content=entry.content,
        timestamp=datetime.now(),
        token_count=_estimate_tokens(entry.content),
        metadata={
            "scene_bus": True,
            "entry_id": entry.id,
            "actor": entry.actor,
            "actor_name": entry.actor_name,
            "entry_type": entry.type.value,
            "round": entry.round,
            "game_time": entry.game_time,
            "responds_to": entry.responds_to,
            "topics": list(entry.topics or []),
            "visibility": entry.visibility,
            "data": dict(entry.data or {}),
        },
    )


def _resolve_location_scope(session: Any) -> Optional[GraphScope]:
    """Resolve location graph scope from session runtime."""
    chapter_id = getattr(session, "chapter_id", None)
    area_id = getattr(session, "area_id", None) or getattr(session, "player_location", None)
    location_id = getattr(session, "sub_location", None) or area_id
    if not chapter_id or not area_id or not location_id:
        return None
    return GraphScope.location(chapter_id=chapter_id, area_id=area_id, location_id=location_id)


def build_graphize_request_from_scene_bus(
    scene_bus: SceneBus,
    *,
    world_id: str,
    session_id: str,
    game_day: int,
    current_scene: Optional[str],
) -> Optional[GraphizeRequest]:
    """Build GraphizeRequest payload from current round SceneBus entries."""
    entries = scene_bus.entries
    if not entries:
        return None

    messages = [_entry_to_window_message(e) for e in entries if e.content]
    if not messages:
        return None

    return GraphizeRequest(
        npc_id=f"scene_bus:{session_id}",
        world_id=world_id,
        messages=messages,
        conversation_summary=scene_bus.get_round_summary(max_length=1200),
        current_scene=current_scene,
        game_day=max(1, int(game_day or 1)),
    )


async def graphize_scene_bus_round(
    *,
    scene_bus: SceneBus,
    session: Any,
    memory_graphizer: Any,
) -> GraphizeResult:
    """Graphize current round SceneBus entries into location scope."""
    started = time.perf_counter()
    scope = _resolve_location_scope(session)
    if scope is None:
        return GraphizeResult(
            success=False,
            error="missing chapter_id/area_id/location_id for scene bus graphization",
            messages_processed=0,
        )

    request = build_graphize_request_from_scene_bus(
        scene_bus,
        world_id=getattr(session, "world_id", ""),
        session_id=getattr(session, "session_id", ""),
        game_day=getattr(getattr(session, "time", None), "day", 1),
        current_scene=getattr(session, "sub_location", None) or getattr(session, "player_location", None),
    )
    if request is None:
        return GraphizeResult(success=True, messages_processed=0)

    result = await memory_graphizer.graphize(
        request=request,
        target_scope=scope,
        mode="scene_bus",
    )
    if not result.processing_time_ms:
        result.processing_time_ms = int((time.perf_counter() - started) * 1000)

    if result.success:
        logger.info(
            "[scene_bus_graphize] scope=%s:%s:%s messages=%d nodes=%d edges=%d elapsed=%dms",
            scope.chapter_id,
            scope.area_id,
            scope.location_id,
            len(request.messages),
            result.nodes_added,
            result.edges_added,
            result.processing_time_ms,
        )
    else:
        logger.warning(
            "[scene_bus_graphize] failed scope=%s:%s:%s error=%s",
            scope.chapter_id,
            scope.area_id,
            scope.location_id,
            result.error,
        )
    return result
