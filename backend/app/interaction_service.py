"""Application-layer interaction execution and view orchestration."""

from __future__ import annotations

import dataclasses as _dataclasses
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping

from app.game_core import ManagedSession
from app.game_core.content import WorldInstance
from app.game_core.orchestration.interaction import (
    InteractionPolicyContext,
    build_interaction_policy_context,
    resolve_accept_quest_board_id,
    validate_preconditions,
    validate_presence,
)
from app.game_core.orchestration.models import PipelineResult
from app.game_core.rules import Command
from app.game_core.state import StateContainer
from app.interaction_views import (
    build_inspect_item_payload,
    build_quest_brief_payload,
    build_quest_location_payload,
    build_quest_progress_payload,
    build_quest_requirements_payload,
    build_quest_reward_payload,
    build_shop_snapshot_payload,
    build_talk_snapshot_payload,
)
from app.quest_views import normalize_dynamic_quest_panel


@dataclass(frozen=True)
class InteractionOutputEvent:
    """One interaction SSE event before the terminal stream_end wrapper."""

    event_type: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class InteractionExecutionResult:
    """Application-layer output consumed by the HTTP route shell."""

    completed: bool
    reason: str
    events: list[InteractionOutputEvent]


@dataclass(frozen=True)
class InteractionViewContext:
    """Pure data snapshot for interaction presence, precheck, and view builders."""

    current_area: str
    current_location: str | None
    npc_positions: dict[str, tuple[str | None, str | None]]
    npc_names: dict[str, str]
    npc_tags: dict[str, frozenset[str]]
    relationship_stages: dict[str, str]
    npc_dispositions: dict[str, dict[str, int]]
    npc_impressions: dict[str, list[str]]
    npc_refresh_modes: dict[str, list[str]]
    shop_states: dict[str, dict[str, Any]]
    dynamic_quest_views: dict[str, dict[str, Any]]
    milestone_states: dict[str, Any]
    board_quest_metadata: dict[str, dict[str, Any]]
    current_day: int
    current_slot: int
    current_period: str
    player_gold: int
    player_inventory: list[dict[str, Any]]
    item_catalog: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class _PipelineActionRequest:
    """Minimal action request shape accepted by the injected executor."""

    action_type: str
    params: dict[str, Any]
    source: str = "player"
    context: dict[str, Any] | None = None


def build_interaction_view_context(
    state: StateContainer,
    world: WorldInstance,
) -> InteractionViewContext:
    """Build one pure-data view context from current state and world."""

    player = state.player
    current_area = (player.current_area or "").strip()
    current_location_text = (player.current_location or "").strip()
    current_location = current_location_text or None

    npc_positions: dict[str, tuple[str | None, str | None]] = {}
    for raw_area_id, area in state.areas.areas.items():
        area_id = str(raw_area_id).strip()
        if not area_id:
            continue
        for raw_npc_id, raw_location_id in area.npc_locations.items():
                npc_id = str(raw_npc_id).strip()
                if not npc_id:
                    continue
                location_text = (
                    str(raw_location_id).strip() if isinstance(raw_location_id, str) else ""
                )
                npc_positions[npc_id] = (area_id, location_text or None)

    npc_names: dict[str, str] = {}
    npc_tags: dict[str, frozenset[str]] = {}
    npc_refresh_modes: dict[str, list[str]] = {}
    if world.has_registry("characters"):
        for raw_character in world.characters.list_all():
            npc_id = raw_character.id.strip()
            if not npc_id:
                continue
            npc_name = raw_character.name.strip() or npc_id
            npc_names[npc_id] = npc_name
            npc_tags[npc_id] = frozenset(
                str(tag).strip().lower()
                for tag in getattr(raw_character, "tags", [])
                if str(tag).strip()
            )
            refresh_modes: list[str] = []
            raw_refresh = getattr(raw_character, "refresh_on", None)
            if raw_refresh is None:
                shop_inventory = getattr(raw_character, "shop_inventory", None)
                raw_refresh = getattr(shop_inventory, "refresh_on", None) if shop_inventory else None
            if isinstance(raw_refresh, str):
                refresh_modes = [raw_refresh.strip()] if raw_refresh.strip() else []
            elif isinstance(raw_refresh, list):
                refresh_modes = [
                    str(entry).strip()
                    for entry in raw_refresh
                    if str(entry).strip()
                ]
            if refresh_modes:
                npc_refresh_modes[npc_id] = refresh_modes
            if npc_id in npc_positions:
                continue
            area_id: str | None = None
            for field_name in ("area_id", "current_area"):
                raw_area_id = str(getattr(raw_character, field_name, "") or "").strip()
                if raw_area_id:
                    area_id = raw_area_id
                    break
            location_id: str | None = None
            for field_name in ("location_id", "current_location"):
                raw_location_id = str(getattr(raw_character, field_name, "") or "").strip()
                if raw_location_id:
                    location_id = raw_location_id
                    break
            npc_positions[npc_id] = (area_id, location_id)

    relations = state.relations
    relationship_stages = {
        str(key): str(value) for key, value in relations.relationship_stages.items() if str(key)
    }

    npc_dispositions: dict[str, dict[str, int]] = {}
    for raw_npc_id, raw_values in relations.npc_dispositions.items():
            npc_id = str(raw_npc_id).strip()
            if not npc_id:
                continue
            normalized_values: dict[str, int] = {}
            for key in ("approval", "trust", "fear", "romance"):
                try:
                    normalized_values[key] = int(raw_values.get(key, 0))
                except (TypeError, ValueError):
                    normalized_values[key] = 0
            npc_dispositions[npc_id] = normalized_values

    npc_impressions: dict[str, list[str]] = {}
    for raw_npc_id, raw_values in relations.npc_impressions.items():
        npc_id = str(raw_npc_id).strip()
        if not npc_id:
            continue
        npc_impressions[npc_id] = [
            str(item).strip() for item in raw_values if str(item).strip()
        ]

    shop_states = {
        str(key): dict(value)
        for key, value in relations.shop_states.items()
        if str(key).strip()
    }

    quests = state.quests
    dynamic_quests = {
        str(key): dict(value)
        for key, value in quests.dynamic_quests.items()
        if str(key).strip()
    }
    dynamic_quest_views = normalize_dynamic_quest_panel(dynamic_quests)
    milestone_states: dict[str, str] = {}
    for milestone_id, milestone in quests.milestone_states.items():
        normalized_state = milestone.state.strip()
        if milestone_id.strip() and normalized_state:
            milestone_states[milestone_id.strip()] = normalized_state

    player_gold = player.gold
    current_day = int(state.time.day)
    current_slot = int(state.time.slot)
    current_period = str(state.time.period)

    player_inventory: list[dict[str, Any]] = []
    for stack in player.inventory:
        player_inventory.append({
            "item_id": stack.item_id,
            "count": max(1, stack.count),
        })

    item_catalog: dict[str, dict[str, Any]] = {}
    if world.has_registry("items"):
        for raw_item in world.items.list_all():
            if isinstance(raw_item, Mapping):
                item_id = str(raw_item.get("id", "")).strip()
                if not item_id:
                    continue
                item_catalog[item_id] = dict(raw_item)
            elif _dataclasses.is_dataclass(raw_item) and not isinstance(raw_item, type):
                item_id = str(getattr(raw_item, "id", "")).strip()
                if not item_id:
                    continue
                item_catalog[item_id] = _dataclasses.asdict(raw_item)

    board_quest_metadata: dict[str, dict[str, Any]] = {}
    if state.has_slice("areas"):
        for area_state in state.areas.areas.values():
            for raw_entries in area_state.board_bulletins.values():
                if not isinstance(raw_entries, list):
                    continue
                for raw_entry in raw_entries:
                    if not isinstance(raw_entry, Mapping):
                        continue
                    quest_id = str(raw_entry.get("quest_id", "")).strip()
                    if not quest_id:
                        continue
                    raw_metadata = raw_entry.get("metadata")
                    if isinstance(raw_metadata, Mapping):
                        board_quest_metadata[quest_id] = {
                            str(meta_key): meta_value
                            for meta_key, meta_value in raw_metadata.items()
                        }

    return InteractionViewContext(
        current_area=current_area,
        current_location=current_location,
        npc_positions=npc_positions,
        npc_names=npc_names,
        npc_tags=npc_tags,
        relationship_stages=relationship_stages,
        npc_dispositions=npc_dispositions,
        npc_impressions=npc_impressions,
        npc_refresh_modes=npc_refresh_modes,
        shop_states=shop_states,
        dynamic_quest_views=dynamic_quest_views,
        milestone_states=milestone_states,
        board_quest_metadata=board_quest_metadata,
        current_day=current_day,
        current_slot=current_slot,
        current_period=current_period,
        player_gold=player_gold,
        player_inventory=player_inventory,
        item_catalog=item_catalog,
    )


class InteractionService:
    """Application-layer interaction execution and view orchestration."""

    def __init__(
        self,
        *,
        execute_structured_action: Callable[
            [ManagedSession, _PipelineActionRequest], Awaitable[PipelineResult]
        ],
    ) -> None:
        self._execute_structured_action = execute_structured_action
        self._snapshot_builders = {
            "talk": ("talk_snapshot", _build_talk_snapshot),
            "inspect_item": ("inspect_item", _build_inspect_item_snapshot),
            "quest_brief": ("quest_brief", _build_quest_brief_snapshot),
            "quest_progress": ("quest_progress", _build_quest_progress_snapshot),
            "quest_location": ("quest_location", _build_quest_location_snapshot),
            "quest_requirements": (
                "quest_requirements",
                _build_quest_requirements_snapshot,
            ),
            "quest_reward": ("quest_reward", _build_quest_reward_snapshot),
        }
        self._post_snapshot_builders = {
            "shop": ("shop_snapshot", _build_shop_snapshot),
            "talk": ("talk_snapshot", _build_talk_snapshot),
        }

    async def execute(
        self,
        session: ManagedSession,
        normalized: Mapping[str, Any] | Any,
    ) -> InteractionExecutionResult:
        """Execute one normalized interaction into an ordered event list."""

        normalized_map = normalized if isinstance(normalized, Mapping) else {}
        status = _normalized_text(normalized_map.get("status"))
        if status != "resolved":
            return InteractionExecutionResult(
                completed=False,
                reason="interaction_rejected",
                events=[_interaction_rejected_event(normalized_map)],
            )

        # Parse interaction fields once
        target_kind = _normalized_text(normalized_map.get("target_kind"))
        target_id = _normalized_id(normalized_map.get("target_id")) or ""
        intent = _normalized_text(normalized_map.get("intent"))
        quest_id = _normalized_id(normalized_map.get("quest_id"))

        # Policy validation (game_core)
        state = session.runtime.state
        world = session.runtime.world
        policy_ctx = build_interaction_policy_context(state, world)

        # Party-level chat bypasses NPC presence / precondition checks
        if target_kind != "party":
            presence_issue = validate_presence(policy_ctx, target_kind, target_id, intent)
            if presence_issue is not None:
                return InteractionExecutionResult(
                    completed=False,
                    reason="interaction_rejected",
                    events=[_interaction_rejected_event(normalized_map, issue=presence_issue)],
                )
            precheck_issue = validate_preconditions(
                policy_ctx, target_kind, target_id, intent, quest_id,
            )
            if precheck_issue is not None:
                return InteractionExecutionResult(
                    completed=False,
                    reason="interaction_rejected",
                    events=[_interaction_rejected_event(normalized_map, issue=precheck_issue)],
                )

        resolved_event = _interaction_resolved_event(normalized_map)
        execution = normalized_map.get("execution", {})
        execution_map = execution if isinstance(execution, Mapping) else {}
        execution_kind = _normalized_text(execution_map.get("kind"))

        if execution_kind == "party_chat":
            # Free chat with party — no pipeline execution needed.
            # The streaming endpoint will handle agent orchestration.
            return InteractionExecutionResult(
                completed=True,
                reason="completed",
                events=[resolved_event],
            )
        if execution_kind == "snapshot":
            view_ctx = build_interaction_view_context(state, world)
            return self._execute_snapshot(view_ctx, normalized_map, execution_map, resolved_event)
        if execution_kind == "shop_refresh":
            return await self._execute_shop_refresh(
                session,
                normalized_map,
                resolved_event,
            )
        if execution_kind == "pipeline_action":
            return await self._execute_pipeline_action(
                session,
                normalized_map,
                execution_map,
                policy_ctx,
                resolved_event,
            )
        return InteractionExecutionResult(
            completed=False,
            reason="interaction_rejected",
            events=[
                resolved_event,
                _interaction_failed_event(normalized_map, message="interaction failed"),
            ],
        )

    def _execute_snapshot(
        self,
        context: InteractionViewContext,
        normalized: Mapping[str, Any],
        execution: Mapping[str, Any],
        resolved_event: InteractionOutputEvent,
    ) -> InteractionExecutionResult:
        snapshot_type = _normalized_text(execution.get("snapshot_type"))
        builder_entry = self._snapshot_builders.get(snapshot_type)
        if builder_entry is None:
            return InteractionExecutionResult(
                completed=False,
                reason="interaction_rejected",
                events=[
                    resolved_event,
                    _interaction_failed_event(normalized, message="interaction failed"),
                ],
            )
        event_type, builder = builder_entry
        target_id = _normalized_id(normalized.get("target_id")) or ""
        quest_id = (
            _normalized_id(execution.get("quest_id"))
            or _normalized_id(normalized.get("quest_id"))
            or ""
        )
        item_id = (
            _normalized_id(execution.get("item_id"))
            or _normalized_id(normalized.get("item_id"))
            or ""
        )
        payload = builder(context, target_id, quest_id, item_id)
        return InteractionExecutionResult(
            completed=True,
            reason="completed",
            events=[resolved_event, InteractionOutputEvent(event_type, payload)],
        )

    async def _execute_shop_refresh(
        self,
        session: ManagedSession,
        normalized: Mapping[str, Any],
        resolved_event: InteractionOutputEvent,
    ) -> InteractionExecutionResult:
        target_id = _normalized_id(normalized.get("target_id")) or ""
        request = _PipelineActionRequest(
            action_type="refresh_shop",
            params={"npc_id": target_id},
            source="system",
        )
        result = await self._execute_structured_action(session, request)
        if not result.executed:
            return InteractionExecutionResult(
                completed=False,
                reason="interaction_rejected",
                events=[
                    resolved_event,
                    _interaction_failed_event(
                        normalized,
                        message=_first_error(result, default="interaction failed"),
                    ),
                ],
            )
        refreshed_context = build_interaction_view_context(session.runtime.state, session.runtime.world)
        return InteractionExecutionResult(
            completed=True,
            reason="completed",
            events=[
                resolved_event,
                InteractionOutputEvent(
                    "shop_snapshot",
                    _build_shop_snapshot(refreshed_context, target_id, "", ""),
                ),
            ],
        )

    async def _execute_pipeline_action(
        self,
        session: ManagedSession,
        normalized: Mapping[str, Any],
        execution: Mapping[str, Any],
        policy_context: InteractionPolicyContext,
        resolved_event: InteractionOutputEvent,
    ) -> InteractionExecutionResult:
        action_type = _normalized_text(execution.get("action_type"))
        params = execution.get("params", {})
        params_map = dict(params) if isinstance(params, Mapping) else {}
        if action_type in {"accept_quest", "report_quest"}:
            quest_id = (
                _normalized_id(normalized.get("quest_id"))
                or _normalized_id(params_map.get("quest_id"))
                or ""
            )
            params_map["quest_id"] = quest_id
            params_map["npc_id"] = _normalized_id(normalized.get("target_id")) or ""
        if action_type == "accept_quest":
            board_id = resolve_accept_quest_board_id(policy_context, quest_id)
            if not board_id:
                return InteractionExecutionResult(
                    completed=False,
                    reason="interaction_rejected",
                    events=[
                        _interaction_rejected_event(
                            normalized,
                            issue={
                                "code": "quest_not_offerable",
                                "message": f"quest is not currently offerable here: {quest_id}",
                            },
                        )
                    ],
                )
            params_map["board_id"] = board_id
        request = _PipelineActionRequest(action_type=action_type, params=params_map)
        result = await self._execute_structured_action(session, request)
        if not result.executed:
            return InteractionExecutionResult(
                completed=False,
                reason="interaction_rejected",
                events=[
                    resolved_event,
                    _interaction_failed_event(
                        normalized,
                        message=_first_error(result, default="interaction failed"),
                    ),
                ],
            )

        events = [
            resolved_event,
            _action_result_event(action_type=action_type, result=result),
        ]
        for event in result.sse_events:
            event_type = _normalized_text(event.event_type)
            if not event_type:
                continue
            events.append(InteractionOutputEvent(event_type, dict(event.payload)))

        post_snapshot = _normalized_text(execution.get("post_snapshot"))
        builder_entry = self._post_snapshot_builders.get(post_snapshot)
        if builder_entry is not None:
            refreshed_context = build_interaction_view_context(session.runtime.state, session.runtime.world)
            event_type, builder = builder_entry
            target_id = _normalized_id(normalized.get("target_id")) or ""
            events.append(
                InteractionOutputEvent(
                    event_type,
                    builder(refreshed_context, target_id, "", ""),
                )
            )

        return InteractionExecutionResult(
            completed=True,
            reason="completed",
            events=events,
        )


# ── Event builders ────────────────────────────────────────────────────────────────


def _interaction_resolved_event(normalized: Mapping[str, Any]) -> InteractionOutputEvent:
    return InteractionOutputEvent(
        "interaction_resolved",
        {
            "target_kind": _normalized_id(normalized.get("target_kind")) or "",
            "target_id": _normalized_id(normalized.get("target_id")) or "",
            "intent": _normalized_text(normalized.get("intent")),
            "item_id": _normalized_id(normalized.get("item_id")),
            "quest_id": _normalized_id(normalized.get("quest_id")),
            "count": _coerce_int(normalized.get("count"), 1),
        },
    )


def _interaction_rejected_event(
    normalized: Mapping[str, Any],
    *,
    issue: Mapping[str, Any] | None = None,
) -> InteractionOutputEvent:
    source = issue if isinstance(issue, Mapping) else normalized
    return InteractionOutputEvent(
        "interaction_rejected",
        {
            "target_kind": _normalized_id(normalized.get("target_kind")),
            "target_id": _normalized_id(normalized.get("target_id")),
            "intent": _normalized_text(normalized.get("intent")),
            "item_id": _normalized_id(normalized.get("item_id")),
            "quest_id": _normalized_id(normalized.get("quest_id")),
            "count": _coerce_int(normalized.get("count"), 1),
            "code": _normalized_text(source.get("code")) or "interaction_failed",
            "message": _normalized_text(source.get("message")) or "interaction failed",
        },
    )


def _interaction_failed_event(
    normalized: Mapping[str, Any],
    *,
    message: str,
) -> InteractionOutputEvent:
    payload = dict(_interaction_rejected_event(normalized).payload)
    payload["code"] = "interaction_failed"
    payload["message"] = message
    return InteractionOutputEvent("interaction_rejected", payload)


def _action_result_event(
    *,
    action_type: str,
    result: PipelineResult,
) -> InteractionOutputEvent:
    outcome = result.metadata.get("outcome")
    return InteractionOutputEvent(
        "action_result",
        {
            "executed": result.executed,
            "action_type": action_type,
            "time_cost": result.time_cost,
            "errors": list(result.errors),
            "metadata": dict(result.metadata),
            "narrative_hints": list(result.narrative_hints),
            "outcome": dict(outcome) if isinstance(outcome, Mapping) else None,
        },
    )


# ── Snapshot adapters (uniform 4-arg signature for registry dispatch) ─────────────


def _build_talk_snapshot(
    context: InteractionViewContext, target_id: str, quest_id: str, item_id: str,
) -> dict[str, Any]:
    del quest_id, item_id
    return build_talk_snapshot_payload(context, target_id)


def _build_shop_snapshot(
    context: InteractionViewContext, target_id: str, quest_id: str, item_id: str,
) -> dict[str, Any]:
    del quest_id, item_id
    return build_shop_snapshot_payload(context, target_id)


def _build_quest_brief_snapshot(
    context: InteractionViewContext, target_id: str, quest_id: str, item_id: str,
) -> dict[str, Any]:
    del item_id
    return build_quest_brief_payload(context, target_id, quest_id)


def _build_quest_progress_snapshot(
    context: InteractionViewContext, target_id: str, quest_id: str, item_id: str,
) -> dict[str, Any]:
    del item_id
    return build_quest_progress_payload(context, target_id, quest_id)


def _build_quest_location_snapshot(
    context: InteractionViewContext, target_id: str, quest_id: str, item_id: str,
) -> dict[str, Any]:
    del item_id
    return build_quest_location_payload(context, target_id, quest_id)


def _build_quest_requirements_snapshot(
    context: InteractionViewContext, target_id: str, quest_id: str, item_id: str,
) -> dict[str, Any]:
    del item_id
    return build_quest_requirements_payload(context, target_id, quest_id)


def _build_quest_reward_snapshot(
    context: InteractionViewContext, target_id: str, quest_id: str, item_id: str,
) -> dict[str, Any]:
    del item_id
    return build_quest_reward_payload(context, target_id, quest_id)


def _build_inspect_item_snapshot(
    context: InteractionViewContext, target_id: str, quest_id: str, item_id: str,
) -> dict[str, Any]:
    del quest_id
    return build_inspect_item_payload(context, target_id, item_id)


# ── Utility helpers ───────────────────────────────────────────────────────────────


def _first_error(result: PipelineResult, *, default: str) -> str:
    if result.errors:
        text = str(result.errors[0]).strip()
        if text:
            return text
    return default


def _normalized_text(value: object) -> str:
    return str(value or "").strip()


def _normalized_id(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _coerce_int(value: Any, default: int) -> int:
    if value is None or isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
