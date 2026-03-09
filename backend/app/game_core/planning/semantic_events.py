"""Semantic planner-event collection and normalization."""

from __future__ import annotations

from typing import Any, Mapping

from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.planning.subsystem import PlannerEvent
from app.game_core.state import StateChange

_TASK_EVENT_KINDS = {
    "bootstrap",
    "milestone_available",
    "milestone_activated",
    "milestone_completed",
    "milestone_failed",
    "quest_accepted",
    "quest_created",
    "quest_updated",
    "quest_objective_completed",
    "quest_completed",
    "quest_retired",
    "quest_expired",
    "quest_state_changed",
}
_SCENE_EVENT_KINDS = {
    "area_entered",
    "sub_location_entered",
    "sub_location_left",
    "scene_changed",
}
_WORLD_EVENT_KINDS = {
    "relationship_stage_changed",
    "shop_refreshed",
    "shop_inventory_changed",
    "world_event_available",
    "world_event_active",
    "world_event_resolved",
    "world_event_expired",
    "world_event_state_changed",
    "rest_completed",
    "combat_resolved",
}
_HEALTH_EVENT_KINDS = {
    "area_sparse",
    "stagnation_threshold_reached",
}
_AUTO_ESCALATION_THRESHOLDS = [4, 7, 10, 13, 16]
_FALLBACK_ESCALATION_INTERVAL = 6
_WORLD_EVENT_STATE_MAP = {
    "available": "world_event_available",
    "active": "world_event_active",
    "resolved": "world_event_resolved",
    "expired": "world_event_expired",
}
_REST_ACTIONS = frozenset({"rest_short", "rest_long", "night_watch", "set_camp"})
_COMBAT_ACTIONS = frozenset({"end_combat"})


def collect_planner_events(
    context: SettlementContext,
    current_tick: int,
    *,
    change_window_start: int = 0,
    round_index: int = 0,
    include_action_log: bool = True,
    seed_events: list[PlannerEvent | Mapping[str, Any]] | None = None,
    include_tick_event: bool | None = None,
) -> list[PlannerEvent]:
    """Collect deterministic planner events for the current replay window."""

    events: list[PlannerEvent] = []
    seen_action_markers: set[str] = set()

    if include_tick_event is None:
        include_tick_event = round_index == 0

    for raw_seed in seed_events or []:
        seed = _coerce_seed_event(raw_seed, current_tick=current_tick, round_index=round_index)
        if seed is not None:
            events.append(seed)

    action_records = []
    if include_action_log:
        action_records = [
            record
            for record in context.action_log
            if isinstance(record, Mapping) and record.get("executed", True) is not False
        ]
        for raw_action in action_records:
            events.extend(
                _events_from_action(
                    context,
                    raw_action,
                    current_tick=current_tick,
                    round_index=round_index,
                    seen_action_markers=seen_action_markers,
                )
            )

    for change in context.change_log[change_window_start:]:
        events.extend(
            _events_from_change(
                context,
                change,
                current_tick=current_tick,
                round_index=round_index,
                seen_action_markers=seen_action_markers,
            )
        )

    area_sparse = _area_sparse_event(
        context,
        current_tick=current_tick,
        round_index=round_index,
    )
    if area_sparse is not None:
        events.append(area_sparse)

    stagnation = _stagnation_event(
        context,
        current_tick=current_tick,
        round_index=round_index,
    )
    if stagnation is not None:
        events.append(stagnation)

    if include_tick_event:
        events.append(
            _event(
                kind="tick_settlement",
                tick=current_tick,
                source="system",
                emitter="system",
                round_index=round_index,
                dedupe_key=f"tick_settlement:{current_tick}",
                payload={"action_count": len(action_records)},
            )
        )

    return _dedupe_and_sort(events)


def planner_event_snapshot(event: PlannerEvent) -> dict[str, Any]:
    return {
        "kind": event.kind,
        "tick": event.tick,
        "source": event.source,
        "priority": event.priority,
        "dedupe_key": event.dedupe_key,
        "round_index": event.round_index,
        "emitter": event.emitter or event.source,
        "payload": {
            key: value
            for key, value in event.payload.items()
            if key != "planner_context"
        },
    }


def _events_from_action(
    context: SettlementContext,
    raw_action: Mapping[str, Any],
    *,
    current_tick: int,
    round_index: int,
    seen_action_markers: set[str],
) -> list[PlannerEvent]:
    action_type = _string(raw_action.get("type")).lower()
    params = raw_action.get("params", {})
    if not isinstance(params, Mapping):
        params = {}

    if action_type == "board_accept_quest":
        quest_id = _non_empty_string(params.get("quest_id"))
        if quest_id is None:
            return []
        seen_action_markers.add(f"quest:{quest_id}")
        return [
            _event(
                kind="quest_accepted",
                tick=current_tick,
                source="action_log",
                emitter="action_log",
                round_index=round_index,
                dedupe_key=f"quest_accepted:{quest_id}",
                payload={
                    "quest_id": quest_id,
                    "board_id": _string(params.get("board_id")),
                    "action_type": action_type,
                },
            )
        ]

    if action_type == "board_complete_quest":
        quest_id = _non_empty_string(params.get("quest_id"))
        if quest_id is None:
            return []
        seen_action_markers.add(f"quest:{quest_id}")
        return [
            _event(
                kind="quest_completed",
                tick=current_tick,
                source="action_log",
                emitter="action_log",
                round_index=round_index,
                dedupe_key=f"quest_completed:{quest_id}",
                payload={
                    "quest_id": quest_id,
                    "board_id": _string(params.get("board_id")),
                    "action_type": action_type,
                },
            )
        ]

    if action_type == "board_retire_quest":
        quest_id = _non_empty_string(params.get("quest_id"))
        if quest_id is None:
            return []
        seen_action_markers.add(f"quest:{quest_id}")
        return [
            _event(
                kind="quest_retired",
                tick=current_tick,
                source="action_log",
                emitter="action_log",
                round_index=round_index,
                dedupe_key=f"quest_retired:{quest_id}",
                payload={
                    "quest_id": quest_id,
                    "board_id": _string(params.get("board_id")),
                    "action_type": action_type,
                },
            )
        ]

    if action_type == "move_area":
        area_id = (
            _non_empty_string(params.get("area_id"))
            or _non_empty_string(params.get("to"))
            or _current_area_id(context)
        )
        if area_id is None:
            return []
        seen_action_markers.add(f"area:{area_id}")
        seen_action_markers.add(f"scene:{area_id}:")
        return [
            _event(
                kind="area_entered",
                tick=current_tick,
                source="action_log",
                emitter="action_log",
                round_index=round_index,
                dedupe_key=f"area_entered:{area_id}",
                payload={
                    "area_id": area_id,
                    "action_type": action_type,
                },
            ),
            _event(
                kind="scene_changed",
                tick=current_tick,
                source="action_log",
                emitter="action_log",
                round_index=round_index,
                dedupe_key=f"scene_changed:{area_id}:",
                payload={
                    "area_id": area_id,
                    "location_id": None,
                    "action_type": action_type,
                },
            ),
        ]

    if action_type == "enter_sub_location":
        location_id = (
            _non_empty_string(params.get("location_id"))
            or _non_empty_string(params.get("location"))
            or _non_empty_string(params.get("sub_location_id"))
        )
        if location_id is None:
            return []
        area_id = _current_area_id(context) or ""
        seen_action_markers.add(f"scene:{area_id}:{location_id}")
        return [
            _event(
                kind="sub_location_entered",
                tick=current_tick,
                source="action_log",
                emitter="action_log",
                round_index=round_index,
                dedupe_key=f"sub_location_entered:{location_id}",
                payload={
                    "area_id": area_id,
                    "location_id": location_id,
                    "action_type": action_type,
                },
            ),
            _event(
                kind="scene_changed",
                tick=current_tick,
                source="action_log",
                emitter="action_log",
                round_index=round_index,
                dedupe_key=f"scene_changed:{area_id}:{location_id}",
                payload={
                    "area_id": area_id,
                    "location_id": location_id,
                    "action_type": action_type,
                },
            ),
        ]

    if action_type == "leave_sub_location":
        area_id = _current_area_id(context) or ""
        seen_action_markers.add(f"scene:{area_id}:")
        return [
            _event(
                kind="sub_location_left",
                tick=current_tick,
                source="action_log",
                emitter="action_log",
                round_index=round_index,
                dedupe_key=f"sub_location_left:{area_id}",
                payload={
                    "area_id": area_id,
                    "location_id": None,
                    "action_type": action_type,
                },
            ),
            _event(
                kind="scene_changed",
                tick=current_tick,
                source="action_log",
                emitter="action_log",
                round_index=round_index,
                dedupe_key=f"scene_changed:{area_id}:",
                payload={
                    "area_id": area_id,
                    "location_id": None,
                    "action_type": action_type,
                },
            ),
        ]

    if action_type == "refresh_shop":
        npc_id = _non_empty_string(params.get("npc_id"))
        if npc_id is None:
            return []
        seen_action_markers.add(f"shop:{npc_id}")
        shop_state = None
        if context.state.has_slice("relations"):
            shop_state = context.state.relations.get_shop_state(npc_id)
        return [
            _event(
                kind="shop_refreshed",
                tick=current_tick,
                source="action_log",
                emitter="action_log",
                round_index=round_index,
                dedupe_key=f"shop_refreshed:{npc_id}",
                payload={
                    "npc_id": npc_id,
                    "shop_state": shop_state or {},
                    "action_type": action_type,
                },
            )
        ]

    if action_type in _REST_ACTIONS:
        return [
            _event(
                kind="rest_completed",
                tick=current_tick,
                source="action_log",
                emitter="action_log",
                round_index=round_index,
                dedupe_key=f"rest_completed:{current_tick}",
                payload={"action_type": action_type},
            )
        ]

    if action_type in _COMBAT_ACTIONS:
        return [
            _event(
                kind="combat_resolved",
                tick=current_tick,
                source="action_log",
                emitter="action_log",
                round_index=round_index,
                dedupe_key=f"combat_resolved:{current_tick}",
                payload={"action_type": action_type},
            )
        ]

    return []


def _events_from_change(
    context: SettlementContext,
    change: StateChange,
    *,
    current_tick: int,
    round_index: int,
    seen_action_markers: set[str],
) -> list[PlannerEvent]:
    if change.slice == "quests" and change.path.startswith("milestone_states."):
        milestone_id = change.path[len("milestone_states."):]
        state_name = _normalize_milestone_state(change.value)
        if state_name not in {"AVAILABLE", "ACTIVE", "COMPLETED", "FAILED"}:
            return []
        return [
            _event(
                kind=f"milestone_{state_name.lower()}",
                tick=current_tick,
                source="change_log",
                emitter=_change_emitter(round_index),
                round_index=round_index,
                dedupe_key=f"milestone_{state_name.lower()}:{milestone_id}",
                payload={
                    "milestone_id": milestone_id,
                    "state": state_name,
                    "title": _milestone_title(context, milestone_id),
                },
            )
        ]

    if change.slice == "quests" and change.path.startswith("dynamic_quests."):
        return _quest_events_from_change(
            context,
            change,
            current_tick=current_tick,
            round_index=round_index,
            seen_action_markers=seen_action_markers,
        )

    if change.slice == "relations" and change.path.startswith("relationship_stages."):
        npc_id = change.path[len("relationship_stages."):]
        stage = _string(change.value)
        if not npc_id or not stage:
            return []
        return [
            _event(
                kind="relationship_stage_changed",
                tick=current_tick,
                source="change_log",
                emitter=_change_emitter(round_index),
                round_index=round_index,
                dedupe_key=f"relationship_stage_changed:{npc_id}:{stage}",
                payload={
                    "npc_id": npc_id,
                    "stage": stage,
                },
            )
        ]

    if change.slice == "relations" and change.path.startswith("shop_states."):
        npc_id = change.path[len("shop_states."):]
        if not npc_id or f"shop:{npc_id}" in seen_action_markers:
            return []
        shop_state = change.value if isinstance(change.value, Mapping) else {}
        return [
            _event(
                kind="shop_inventory_changed",
                tick=current_tick,
                source="change_log",
                emitter=_change_emitter(round_index),
                round_index=round_index,
                dedupe_key=f"shop_inventory_changed:{npc_id}",
                payload={
                    "npc_id": npc_id,
                    "shop_state": dict(shop_state) if isinstance(shop_state, Mapping) else {},
                },
            )
        ]

    if change.slice == "events" and change.path.startswith("state."):
        event_id = change.path[len("state."):]
        if not event_id:
            return []
        snapshot = context.state.events.get_event(event_id) if context.state.has_slice("events") else None
        if not isinstance(snapshot, Mapping):
            snapshot = {}
        to_state = _string(snapshot.get("state") or change.value).lower()
        kind = _WORLD_EVENT_STATE_MAP.get(to_state, "world_event_state_changed")
        return [
            _event(
                kind=kind,
                tick=current_tick,
                source="change_log",
                emitter=_change_emitter(round_index),
                round_index=round_index,
                dedupe_key=f"{kind}:{event_id}:{to_state}",
                payload={
                    "event_id": event_id,
                    "event_type": _string(snapshot.get("event_type")),
                    "from_state": _string(snapshot.get("from_state")),
                    "to_state": to_state,
                    "title": _string(snapshot.get("title") or snapshot.get("name") or snapshot.get("label")),
                    "reason": _string(snapshot.get("reason")),
                },
            )
        ]

    if change.slice != "player":
        return []

    if change.path == "current_area":
        area_id = _non_empty_string(change.value)
        if area_id is None or f"area:{area_id}" in seen_action_markers:
            return []
        return [
            _event(
                kind="area_entered",
                tick=current_tick,
                source="change_log",
                emitter=_change_emitter(round_index),
                round_index=round_index,
                dedupe_key=f"area_entered:{area_id}",
                payload={"area_id": area_id},
            )
        ]

    if change.path == "current_location":
        area_id = _current_area_id(context) or ""
        location_id = _non_empty_string(change.value)
        scene_marker = f"scene:{area_id}:{location_id or ''}"
        if scene_marker in seen_action_markers:
            return []
        if location_id is None:
            return []
        return [
            _event(
                kind="sub_location_entered",
                tick=current_tick,
                source="change_log",
                emitter=_change_emitter(round_index),
                round_index=round_index,
                dedupe_key=f"sub_location_entered:{location_id}",
                payload={
                    "area_id": area_id,
                    "location_id": location_id,
                },
            ),
            _event(
                kind="scene_changed",
                tick=current_tick,
                source="change_log",
                emitter=_change_emitter(round_index),
                round_index=round_index,
                dedupe_key=f"scene_changed:{area_id}:{location_id}",
                payload={
                    "area_id": area_id,
                    "location_id": location_id,
                },
            ),
        ]

    return []


def _quest_events_from_change(
    context: SettlementContext,
    change: StateChange,
    *,
    current_tick: int,
    round_index: int,
    seen_action_markers: set[str],
) -> list[PlannerEvent]:
    quest_id, status, quest_payload, parts = _quest_change_details(context, change)
    if quest_id is None:
        return []
    if f"quest:{quest_id}" in seen_action_markers:
        return []

    emitter = _change_emitter(round_index)
    title = _string(quest_payload.get("title"))
    summary = _string(quest_payload.get("summary"))

    if len(parts) > 3 and parts[2] == "objectives":
        objective_index = _coerce_int(parts[3])
        if objective_index is None:
            return []
        if parts[-1] == "completed" and bool(change.value):
            return [
                _event(
                    kind="quest_objective_completed",
                    tick=current_tick,
                    source="change_log",
                    emitter=emitter,
                    round_index=round_index,
                    dedupe_key=f"quest_objective_completed:{quest_id}:{objective_index}",
                    payload={
                        "quest_id": quest_id,
                        "objective_index": objective_index,
                        "status": status,
                        "title": title,
                        "summary": summary,
                    },
                )
            ]

    if len(parts) == 2:
        if change.operation == "set" and _quest_created_in_window(quest_payload, current_tick):
            kind = "quest_created"
            dedupe_key = f"quest_created:{quest_id}"
        elif status == "completed":
            kind = "quest_completed"
            dedupe_key = f"quest_completed:{quest_id}"
        elif status == "retired":
            kind = "quest_retired"
            dedupe_key = f"quest_retired:{quest_id}"
        elif status == "expired":
            kind = "quest_expired"
            dedupe_key = f"quest_expired:{quest_id}"
        elif change.operation == "modify":
            kind = "quest_updated"
            dedupe_key = f"quest_updated:{quest_id}"
        else:
            kind = "quest_state_changed"
            dedupe_key = f"quest_state_changed:{quest_id}:{status or 'unknown'}"
        return [
            _event(
                kind=kind,
                tick=current_tick,
                source="change_log",
                emitter=emitter,
                round_index=round_index,
                dedupe_key=dedupe_key,
                payload={
                    "quest_id": quest_id,
                    "status": status,
                    "title": title,
                    "summary": summary,
                },
            )
        ]

    if len(parts) > 2 and parts[2] == "status":
        kind = {
            "completed": "quest_completed",
            "retired": "quest_retired",
            "expired": "quest_expired",
        }.get(status, "quest_state_changed")
        suffix = status or "unknown"
        return [
            _event(
                kind=kind,
                tick=current_tick,
                source="change_log",
                emitter=emitter,
                round_index=round_index,
                dedupe_key=f"{kind}:{quest_id}:{suffix}",
                payload={
                    "quest_id": quest_id,
                    "status": status,
                    "title": title,
                    "summary": summary,
                },
            )
        ]

    return [
        _event(
            kind="quest_updated",
            tick=current_tick,
            source="change_log",
            emitter=emitter,
            round_index=round_index,
            dedupe_key=f"quest_updated:{quest_id}",
            payload={
                "quest_id": quest_id,
                "status": status,
                "title": title,
                "summary": summary,
            },
        )
    ]


def _area_sparse_event(
    context: SettlementContext,
    *,
    current_tick: int,
    round_index: int,
) -> PlannerEvent | None:
    if not (context.state.has_slice("areas") and context.state.has_slice("player")):
        return None
    area_id = _current_area_id(context)
    if area_id is None:
        return None
    counts = context.state.areas.count_dynamic_sub_areas(area_id)
    has_capacity = context.state.areas.has_cluster_capacity(area_id)
    if not (has_capacity and counts.get("total", 0) == 0):
        return None
    return _event(
        kind="area_sparse",
        tick=current_tick,
        source="state_snapshot",
        emitter=_snapshot_emitter(round_index),
        round_index=round_index,
        dedupe_key=f"area_sparse:{area_id}",
        payload={
            "area_id": area_id,
            "has_capacity": has_capacity,
            "total_dynamic": counts.get("total", 0),
        },
    )


def _stagnation_event(
    context: SettlementContext,
    *,
    current_tick: int,
    round_index: int,
) -> PlannerEvent | None:
    if not context.state.has_slice("narrative_plan"):
        return None
    np_state = context.state.narrative_plan
    if np_state.pacing_frozen:
        return None
    threshold = _stagnation_threshold(np_state.escalation_level)
    if np_state.ticks_since_milestone_progress < threshold:
        return None
    return _event(
        kind="stagnation_threshold_reached",
        tick=current_tick,
        source="state_snapshot",
        emitter=_snapshot_emitter(round_index),
        round_index=round_index,
        dedupe_key=f"stagnation_threshold_reached:{np_state.escalation_level}:{threshold}",
        payload={
            "ticks_since_milestone_progress": np_state.ticks_since_milestone_progress,
            "escalation_level": np_state.escalation_level,
            "threshold": threshold,
        },
    )


def _coerce_seed_event(
    raw: PlannerEvent | Mapping[str, Any],
    *,
    current_tick: int,
    round_index: int,
) -> PlannerEvent | None:
    if isinstance(raw, PlannerEvent):
        payload = dict(raw.payload)
        return PlannerEvent(
            kind=raw.kind,
            tick=raw.tick,
            source=raw.source or "seed",
            priority=raw.priority or _priority_for_kind(raw.kind),
            dedupe_key=raw.dedupe_key or f"{raw.kind}:{raw.tick}",
            round_index=round_index,
            emitter=raw.emitter or raw.source or "seed",
            payload=payload,
        )
    if not isinstance(raw, Mapping):
        return None
    kind = _non_empty_string(raw.get("kind"))
    if kind is None:
        return None
    payload = raw.get("payload", {})
    if not isinstance(payload, Mapping):
        payload = {}
    tick = _coerce_int(raw.get("tick"))
    if tick is None:
        tick = current_tick
    source = _string(raw.get("source")) or "seed"
    emitter = _string(raw.get("emitter")) or source
    dedupe_key = _string(raw.get("dedupe_key")) or f"{kind}:{tick}"
    priority = _coerce_int(raw.get("priority"))
    if priority is None:
        priority = _priority_for_kind(kind)
    return PlannerEvent(
        kind=kind,
        tick=tick,
        source=source,
        priority=priority,
        dedupe_key=dedupe_key,
        round_index=round_index,
        emitter=emitter,
        payload=dict(payload),
    )


def _event(
    *,
    kind: str,
    tick: int,
    source: str,
    emitter: str,
    round_index: int,
    dedupe_key: str,
    payload: Mapping[str, Any] | None = None,
) -> PlannerEvent:
    return PlannerEvent(
        kind=kind,
        tick=tick,
        source=source,
        priority=_priority_for_kind(kind),
        dedupe_key=dedupe_key,
        round_index=round_index,
        emitter=emitter,
        payload=dict(payload) if isinstance(payload, Mapping) else {},
    )


def _dedupe_and_sort(events: list[PlannerEvent]) -> list[PlannerEvent]:
    deduped: dict[str, PlannerEvent] = {}
    order: dict[str, int] = {}
    for idx, event in enumerate(events):
        key = event.dedupe_key or f"{event.kind}:{idx}"
        existing = deduped.get(key)
        if existing is None:
            deduped[key] = event
            order[key] = idx
            continue
        if existing.source != "action_log" and event.source == "action_log":
            deduped[key] = event
        elif existing.source == event.source and event.priority < existing.priority:
            deduped[key] = event
    return sorted(
        deduped.values(),
        key=lambda item: (
            item.priority,
            item.round_index,
            order.get(item.dedupe_key or "", 0),
        ),
    )


def _priority_for_kind(kind: str) -> int:
    if kind == "bootstrap":
        return 0
    if kind in _TASK_EVENT_KINDS:
        return 10
    if kind in _SCENE_EVENT_KINDS:
        return 20
    if kind in _WORLD_EVENT_KINDS:
        return 30
    if kind in _HEALTH_EVENT_KINDS:
        return 40
    if kind == "tick_settlement":
        return 50
    return 45


def _quest_change_details(
    context: SettlementContext,
    change: StateChange,
) -> tuple[str | None, str, Mapping[str, Any], list[str]]:
    parts = change.path.split(".")
    if len(parts) < 2:
        return None, "", {}, parts
    quest_id = _non_empty_string(parts[1])
    if quest_id is None:
        return None, "", {}, parts
    quest_payload: Mapping[str, Any] = {}
    if isinstance(change.value, Mapping):
        quest_payload = change.value
    else:
        stored = context.state.quests.get_dynamic_quest(quest_id) if context.state.has_slice("quests") else None
        if isinstance(stored, Mapping):
            quest_payload = stored
    status = _string(quest_payload.get("status")).lower()
    if not status and len(parts) > 2 and parts[2] == "status":
        status = _string(change.value).lower()
    return quest_id, status, quest_payload, parts


def _quest_created_in_window(quest_payload: Mapping[str, Any], current_tick: int) -> bool:
    created_at_tick = _coerce_int(quest_payload.get("created_at_tick"))
    return created_at_tick == current_tick


def _normalize_milestone_state(value: Any) -> str:
    if isinstance(value, Mapping):
        return _string(value.get("state")).upper()
    return _string(value).upper()


def _milestone_title(context: SettlementContext, milestone_id: str) -> str:
    if not context.world.has_registry("quests"):
        return ""
    template = context.world.quests.get_milestone(milestone_id)
    if template is None:
        return ""
    return _string(getattr(template, "title", ""))


def _current_area_id(context: SettlementContext) -> str | None:
    if not context.state.has_slice("player"):
        return None
    return _non_empty_string(context.state.player.current_area)


def _stagnation_threshold(level: int) -> int:
    if 0 <= level < len(_AUTO_ESCALATION_THRESHOLDS):
        return _AUTO_ESCALATION_THRESHOLDS[level]
    return _FALLBACK_ESCALATION_INTERVAL


def _change_emitter(round_index: int) -> str:
    if round_index <= 0:
        return "change_log"
    return f"planner_replay:r{round_index}"


def _snapshot_emitter(round_index: int) -> str:
    if round_index <= 0:
        return "state_snapshot"
    return f"planner_replay:r{round_index}"


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _string(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _non_empty_string(value: Any) -> str | None:
    normalized = _string(value)
    return normalized or None
