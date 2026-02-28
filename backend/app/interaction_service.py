"""Application-layer interaction execution and view orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping

from app.game_core.rules import Command
from app.interaction_views import (
    build_board_entries,
    build_board_snapshot_payload,
    build_quest_brief_payload,
    build_quest_location_payload,
    build_quest_progress_payload,
    build_quest_requirements_payload,
    build_quest_reward_payload,
    build_shop_snapshot_payload,
    build_talk_snapshot_payload,
)


@dataclass(frozen=True)
class InteractionOutputEvent:
    """One interaction SSE event before the terminal stream_end wrapper."""

    event_type: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class InteractionExecutionResult:
    """Application-layer output consumed by the HTTP route shell."""

    success: bool
    reason: str
    events: list[InteractionOutputEvent]


@dataclass(frozen=True)
class InteractionContext:
    """Pure data snapshot for interaction presence, precheck, and view builders."""

    current_area: str
    current_location: str | None
    area_sub_locations: dict[str, list[str]]
    npc_positions: dict[str, tuple[str | None, str | None]]
    npc_names: dict[str, str]
    relationship_stages: dict[str, str]
    npc_dispositions: dict[str, dict[str, int]]
    npc_impressions: dict[str, list[str]]
    shop_states: dict[str, dict[str, Any]]
    dynamic_quests: dict[str, dict[str, Any]]
    milestone_states: dict[str, Any]
    active_bulletins: list[dict[str, Any]]


@dataclass(frozen=True)
class _PipelineActionRequest:
    """Minimal action request shape accepted by the injected executor."""

    action_type: str
    params: dict[str, Any]
    source: str = "player"
    context: dict[str, Any] | None = None


def build_interaction_context(session: Any) -> InteractionContext:
    """Build one pure-data context from the current managed session."""

    runtime = getattr(session, "runtime", None)
    state = getattr(runtime, "state", None)
    world = getattr(runtime, "world", None)
    player = getattr(state, "player", None)
    current_area = str(getattr(player, "current_area", "") or "").strip()
    current_location_text = str(getattr(player, "current_location", "") or "").strip()
    current_location = current_location_text or None

    area_sub_locations: dict[str, list[str]] = {}
    if world is not None and bool(world.has_registry("maps")):
        for raw_area in world.maps.list_all():
            if not isinstance(raw_area, Mapping):
                continue
            area_id = str(raw_area.get("id", "")).strip()
            if not area_id:
                continue
            raw_sub_locations = raw_area.get("sub_locations", {})
            sub_location_ids: list[str] = []
            if isinstance(raw_sub_locations, Mapping):
                for raw_key in raw_sub_locations.keys():
                    sub_location_id = str(raw_key).strip()
                    if sub_location_id:
                        sub_location_ids.append(sub_location_id)
            area_sub_locations[area_id] = sub_location_ids

    npc_positions: dict[str, tuple[str | None, str | None]] = {}
    runtime_areas = getattr(getattr(state, "areas", None), "areas", {})
    if isinstance(runtime_areas, Mapping):
        for raw_area_id, area in runtime_areas.items():
            area_id = str(raw_area_id).strip()
            if not area_id:
                continue
            npc_locations = getattr(area, "npc_locations", {})
            if not isinstance(npc_locations, Mapping):
                continue
            for raw_npc_id, raw_location_id in npc_locations.items():
                npc_id = str(raw_npc_id).strip()
                if not npc_id:
                    continue
                location_text = (
                    str(raw_location_id).strip() if isinstance(raw_location_id, str) else ""
                )
                npc_positions[npc_id] = (area_id, location_text or None)

    npc_names: dict[str, str] = {}
    if world is not None and bool(world.has_registry("characters")):
        for raw_character in world.characters.list_all():
            if not isinstance(raw_character, Mapping):
                continue
            npc_id = str(raw_character.get("id", "")).strip()
            if not npc_id:
                continue
            npc_name = str(raw_character.get("name", "")).strip() or npc_id
            npc_names[npc_id] = npc_name
            if npc_id in npc_positions:
                continue
            area_id: str | None = None
            for field_name in ("area_id", "current_area"):
                raw_area_id = str(raw_character.get(field_name, "")).strip()
                if raw_area_id:
                    area_id = raw_area_id
                    break
            location_id: str | None = None
            for field_name in ("location_id", "current_location"):
                raw_location_id = str(raw_character.get(field_name, "")).strip()
                if raw_location_id:
                    location_id = raw_location_id
                    break
            npc_positions[npc_id] = (area_id, location_id)

    relations = getattr(state, "relations", None)
    raw_relationship_stages = getattr(relations, "relationship_stages", {})
    relationship_stages = (
        {str(key): str(value) for key, value in raw_relationship_stages.items() if str(key)}
        if isinstance(raw_relationship_stages, Mapping)
        else {}
    )

    raw_dispositions = getattr(relations, "npc_dispositions", {})
    npc_dispositions: dict[str, dict[str, int]] = {}
    if isinstance(raw_dispositions, Mapping):
        for raw_npc_id, raw_values in raw_dispositions.items():
            npc_id = str(raw_npc_id).strip()
            if not npc_id:
                continue
            values = raw_values if isinstance(raw_values, Mapping) else {}
            normalized_values: dict[str, int] = {}
            for key in ("approval", "trust", "fear", "romance"):
                try:
                    normalized_values[key] = int(values.get(key, 0))
                except (TypeError, ValueError):
                    normalized_values[key] = 0
            npc_dispositions[npc_id] = normalized_values

    raw_impressions = getattr(relations, "npc_impressions", {})
    npc_impressions: dict[str, list[str]] = {}
    if isinstance(raw_impressions, Mapping):
        for raw_npc_id, raw_values in raw_impressions.items():
            npc_id = str(raw_npc_id).strip()
            if not npc_id:
                continue
            values = raw_values if isinstance(raw_values, list) else []
            npc_impressions[npc_id] = [
                str(item).strip() for item in values if str(item).strip()
            ]

    raw_shop_states = getattr(relations, "shop_states", {})
    shop_states = (
        {
            str(key): dict(value) if isinstance(value, Mapping) else {}
            for key, value in raw_shop_states.items()
            if str(key).strip()
        }
        if isinstance(raw_shop_states, Mapping)
        else {}
    )

    quests = getattr(state, "quests", None)
    raw_dynamic_quests = getattr(quests, "dynamic_quests", {})
    dynamic_quests = (
        {
            str(key): dict(value) if isinstance(value, Mapping) else {}
            for key, value in raw_dynamic_quests.items()
            if str(key).strip()
        }
        if isinstance(raw_dynamic_quests, Mapping)
        else {}
    )
    raw_milestone_states = getattr(quests, "milestone_states", {})
    milestone_states: dict[str, str] = {}
    if isinstance(raw_milestone_states, Mapping):
        for raw_key, raw_value in raw_milestone_states.items():
            milestone_id = str(raw_key).strip()
            if not milestone_id:
                continue
            normalized_state: str | None = None
            if isinstance(raw_value, str):
                normalized_state = raw_value.strip() or None
            elif isinstance(raw_value, Mapping):
                normalized_state = str(raw_value.get("state", "")).strip() or None
            else:
                state_value = getattr(raw_value, "state", None)
                if state_value is not None:
                    normalized_state = str(state_value).strip() or None
            if normalized_state is not None:
                milestone_states[milestone_id] = normalized_state

    narrative_plan = getattr(state, "narrative_plan", None)
    raw_bulletins = getattr(narrative_plan, "active_bulletins", [])
    active_bulletins = [dict(item) for item in raw_bulletins if isinstance(item, Mapping)]

    return InteractionContext(
        current_area=current_area,
        current_location=current_location,
        area_sub_locations=area_sub_locations,
        npc_positions=npc_positions,
        npc_names=npc_names,
        relationship_stages=relationship_stages,
        npc_dispositions=npc_dispositions,
        npc_impressions=npc_impressions,
        shop_states=shop_states,
        dynamic_quests=dynamic_quests,
        milestone_states=milestone_states,
        active_bulletins=active_bulletins,
    )


class InteractionService:
    """Application-layer interaction execution and view orchestration."""

    def __init__(
        self,
        *,
        execute_structured_action: Callable[[Any, Any], Awaitable[Any]],
        save_session: Callable[[Any], Awaitable[None]],
    ) -> None:
        self._execute_structured_action = execute_structured_action
        self._save_session = save_session
        self._snapshot_builders = {
            "talk": ("talk_snapshot", self._build_talk_snapshot),
            "board": ("board_snapshot", self._build_board_snapshot),
            "quest_brief": ("quest_brief", self._build_quest_brief_snapshot),
            "quest_progress": ("quest_progress", self._build_quest_progress_snapshot),
            "quest_location": ("quest_location", self._build_quest_location_snapshot),
            "quest_requirements": (
                "quest_requirements",
                self._build_quest_requirements_snapshot,
            ),
            "quest_reward": ("quest_reward", self._build_quest_reward_snapshot),
        }
        self._post_snapshot_builders = {
            "shop": ("shop_snapshot", self._build_shop_snapshot),
            "board": ("board_snapshot", self._build_board_snapshot),
            "talk": ("talk_snapshot", self._build_talk_snapshot),
        }

    async def execute(
        self,
        session: Any,
        normalized: Mapping[str, Any] | Any,
    ) -> InteractionExecutionResult:
        """Execute one normalized interaction into an ordered event list."""

        normalized_map = normalized if isinstance(normalized, Mapping) else {}
        status = self._normalized_text(normalized_map.get("status"))
        if status != "resolved":
            return InteractionExecutionResult(
                success=False,
                reason="interaction_rejected",
                events=[self._interaction_rejected_event(normalized_map)],
            )

        context = build_interaction_context(session)
        presence_issue = self._validate_presence(context, normalized_map)
        if presence_issue is not None:
            return InteractionExecutionResult(
                success=False,
                reason="interaction_rejected",
                events=[self._interaction_rejected_event(normalized_map, issue=presence_issue)],
            )
        precheck_issue = self._validate_preconditions(context, normalized_map)
        if precheck_issue is not None:
            return InteractionExecutionResult(
                success=False,
                reason="interaction_rejected",
                events=[self._interaction_rejected_event(normalized_map, issue=precheck_issue)],
            )

        resolved_event = self._interaction_resolved_event(normalized_map)
        execution = normalized_map.get("execution", {})
        execution_map = execution if isinstance(execution, Mapping) else {}
        execution_kind = self._normalized_text(execution_map.get("kind"))

        if execution_kind == "snapshot":
            return self._execute_snapshot(context, normalized_map, execution_map, resolved_event)
        if execution_kind == "shop_refresh":
            return await self._execute_shop_refresh(
                session,
                context,
                normalized_map,
                resolved_event,
            )
        if execution_kind == "pipeline_action":
            return await self._execute_pipeline_action(
                session,
                normalized_map,
                execution_map,
                resolved_event,
            )
        return InteractionExecutionResult(
            success=False,
            reason="interaction_rejected",
            events=[
                resolved_event,
                self._interaction_failed_event(normalized_map, message="interaction failed"),
            ],
        )

    def _validate_presence(
        self,
        context: InteractionContext,
        normalized: Mapping[str, Any],
    ) -> dict[str, str] | None:
        target_kind = self._normalized_text(normalized.get("target_kind"))
        target_id = self._normalized_id(normalized.get("target_id")) or ""
        intent = self._normalized_text(normalized.get("intent"))
        if target_kind == "npc":
            return self._validate_npc_presence(context, target_id, intent)
        if target_kind == "board":
            return self._validate_board_presence(context, target_id)
        return {
            "code": "invalid_target_kind",
            "message": "target_kind must be npc or board",
        }

    def _validate_npc_presence(
        self,
        context: InteractionContext,
        npc_id: str,
        intent: str,
    ) -> dict[str, str] | None:
        if npc_id not in context.npc_names and npc_id not in context.npc_positions:
            return {
                "code": "npc_not_found",
                "message": f"unknown character: {npc_id}",
            }
        npc_area, npc_location = context.npc_positions.get(npc_id, (None, None))
        if not npc_area:
            return {
                "code": "npc_not_available",
                "message": f"npc is not currently placed: {npc_id}",
            }
        if intent in {"browse", "buy", "sell"}:
            if npc_area == context.current_area:
                return None
            return {
                "code": "npc_not_present",
                "message": f"npc is not in the current area: {npc_id}",
            }
        if (
            npc_area == context.current_area
            and npc_location
            and context.current_location
            and npc_location == context.current_location
        ):
            return None
        return {
            "code": "npc_not_present",
            "message": f"npc is not in the current location: {npc_id}",
        }

    @staticmethod
    def _validate_board_presence(
        context: InteractionContext,
        board_id: str,
    ) -> dict[str, str] | None:
        if not context.current_area:
            return {
                "code": "board_not_present",
                "message": f"board is not in the current location: {board_id}",
            }
        sub_locations = context.area_sub_locations.get(context.current_area, [])
        if board_id not in sub_locations:
            return {
                "code": "board_not_found",
                "message": f"unknown board: {board_id}",
            }
        if context.current_location != board_id:
            return {
                "code": "board_not_present",
                "message": f"board is not in the current location: {board_id}",
            }
        return None

    def _validate_preconditions(
        self,
        context: InteractionContext,
        normalized: Mapping[str, Any],
    ) -> dict[str, str] | None:
        target_kind = self._normalized_text(normalized.get("target_kind"))
        target_id = self._normalized_id(normalized.get("target_id")) or ""
        intent = self._normalized_text(normalized.get("intent"))
        quest_id = self._normalized_id(normalized.get("quest_id"))
        if target_kind == "board" and intent in {"accept", "complete", "retire"}:
            if quest_id is None:
                messages = {
                    "accept": "quest_id is required for board accept",
                    "complete": "quest_id is required for board complete",
                    "retire": "quest_id is required for board retire",
                }
                return {
                    "code": "missing_quest",
                    "message": messages.get(intent, "quest_id is required for board accept"),
                }
            target_states = {
                "accept": "active",
                "complete": "completed",
                "retire": "retired",
            }
            return self._validate_board_transition(context, target_id, quest_id, target_states[intent])
        if target_kind == "npc" and intent in {
            "ask_quest",
            "ask_progress",
            "ask_location",
            "ask_requirements",
            "ask_reward",
        }:
            if quest_id is None:
                messages = {
                    "ask_quest": "quest_id is required for npc ask_quest",
                    "ask_progress": "quest_id is required for npc ask_progress",
                    "ask_location": "quest_id is required for npc ask_location",
                    "ask_requirements": "quest_id is required for npc ask_requirements",
                    "ask_reward": "quest_id is required for npc ask_reward",
                }
                return {
                    "code": "missing_quest",
                    "message": messages.get(intent, "quest_id is required for npc ask_quest"),
                }
            return self._validate_dynamic_quest_exists(context, quest_id)
        return None

    @staticmethod
    def _validate_dynamic_quest_exists(
        context: InteractionContext,
        quest_id: str,
    ) -> dict[str, str] | None:
        if quest_id in context.dynamic_quests:
            return None
        return {
            "code": "quest_not_found",
            "message": f"dynamic quest not found: {quest_id}",
        }

    def _validate_board_transition(
        self,
        context: InteractionContext,
        board_id: str,
        quest_id: str,
        to_state: str,
    ) -> dict[str, str] | None:
        board_entries = build_board_entries(context, board_id)
        if not any(entry.get("quest_id") == quest_id for entry in board_entries):
            return {
                "code": "quest_not_listed",
                "message": f"quest is not listed on board: {quest_id}",
            }
        dynamic_quest_issue = self._validate_dynamic_quest_exists(context, quest_id)
        if dynamic_quest_issue is not None:
            return dynamic_quest_issue
        dynamic_quest = context.dynamic_quests.get(quest_id, {})
        current_state = str(dynamic_quest.get("status", "")).strip().lower()
        allowed_states = {
            "active": {"available"},
            "completed": {"active"},
            "retired": {"available", "active"},
        }.get(to_state, set())
        if current_state in allowed_states:
            return None
        return {
            "code": "interaction_failed",
            "message": f"invalid dynamic quest transition: {current_state} -> {to_state}",
        }

    def _execute_snapshot(
        self,
        context: InteractionContext,
        normalized: Mapping[str, Any],
        execution: Mapping[str, Any],
        resolved_event: InteractionOutputEvent,
    ) -> InteractionExecutionResult:
        snapshot_type = self._normalized_text(execution.get("snapshot_type"))
        builder_entry = self._snapshot_builders.get(snapshot_type)
        if builder_entry is None:
            return InteractionExecutionResult(
                success=False,
                reason="interaction_rejected",
                events=[
                    resolved_event,
                    self._interaction_failed_event(normalized, message="interaction failed"),
                ],
            )
        event_type, builder = builder_entry
        target_id = self._normalized_id(normalized.get("target_id")) or ""
        quest_id = (
            self._normalized_id(execution.get("quest_id"))
            or self._normalized_id(normalized.get("quest_id"))
            or ""
        )
        payload = builder(context, target_id, quest_id)
        return InteractionExecutionResult(
            success=True,
            reason="completed",
            events=[resolved_event, InteractionOutputEvent(event_type, payload)],
        )

    async def _execute_shop_refresh(
        self,
        session: Any,
        context: InteractionContext,
        normalized: Mapping[str, Any],
        resolved_event: InteractionOutputEvent,
    ) -> InteractionExecutionResult:
        del context
        target_id = self._normalized_id(normalized.get("target_id")) or ""
        result = session.runtime.rules_engine.execute(
            Command(type="refresh_shop", source="system", params={"npc_id": target_id}),
            session.runtime.state,
            session.runtime.world,
        )
        if getattr(result, "delta", None) is not None:
            session.runtime.state.apply(result.delta)
        if not bool(getattr(result, "success", False)):
            return InteractionExecutionResult(
                success=False,
                reason="interaction_rejected",
                events=[
                    resolved_event,
                    self._interaction_failed_event(
                        normalized,
                        message=self._first_error(result, default="interaction failed"),
                    ),
                ],
            )
        await self._save_session(session)
        refreshed_context = build_interaction_context(session)
        return InteractionExecutionResult(
            success=True,
            reason="completed",
            events=[
                resolved_event,
                InteractionOutputEvent(
                    "shop_snapshot",
                    self._build_shop_snapshot(refreshed_context, target_id, ""),
                ),
            ],
        )

    async def _execute_pipeline_action(
        self,
        session: Any,
        normalized: Mapping[str, Any],
        execution: Mapping[str, Any],
        resolved_event: InteractionOutputEvent,
    ) -> InteractionExecutionResult:
        action_type = self._normalized_text(execution.get("action_type"))
        params = execution.get("params", {})
        params_map = dict(params) if isinstance(params, Mapping) else {}
        request = _PipelineActionRequest(action_type=action_type, params=params_map)
        result = await self._execute_structured_action(session, request)
        if not bool(getattr(result, "success", False)):
            return InteractionExecutionResult(
                success=False,
                reason="interaction_rejected",
                events=[
                    resolved_event,
                    self._interaction_failed_event(
                        normalized,
                        message=self._first_error(result, default="interaction failed"),
                    ),
                ],
            )

        events = [
            resolved_event,
            self._action_result_event(action_type=action_type, result=result),
        ]
        for event in getattr(result, "sse_events", []):
            event_type = self._normalized_text(getattr(event, "event_type", ""))
            if not event_type:
                continue
            payload = getattr(event, "payload", {})
            payload_map = payload if isinstance(payload, Mapping) else {}
            events.append(InteractionOutputEvent(event_type, dict(payload_map)))

        post_snapshot = self._normalized_text(execution.get("post_snapshot"))
        builder_entry = self._post_snapshot_builders.get(post_snapshot)
        if builder_entry is not None:
            refreshed_context = build_interaction_context(session)
            event_type, builder = builder_entry
            target_id = self._normalized_id(normalized.get("target_id")) or ""
            events.append(
                InteractionOutputEvent(
                    event_type,
                    builder(refreshed_context, target_id, ""),
                )
            )

        return InteractionExecutionResult(
            success=True,
            reason="completed",
            events=events,
        )

    def _interaction_resolved_event(
        self,
        normalized: Mapping[str, Any],
    ) -> InteractionOutputEvent:
        return InteractionOutputEvent(
            "interaction_resolved",
            {
                "target_kind": self._normalized_id(normalized.get("target_kind")) or "",
                "target_id": self._normalized_id(normalized.get("target_id")) or "",
                "intent": self._normalized_text(normalized.get("intent")),
                "item_id": self._normalized_id(normalized.get("item_id")),
                "quest_id": self._normalized_id(normalized.get("quest_id")),
                "count": self._coerce_int(normalized.get("count"), 1),
            },
        )

    def _interaction_rejected_event(
        self,
        normalized: Mapping[str, Any],
        *,
        issue: Mapping[str, Any] | None = None,
    ) -> InteractionOutputEvent:
        source = issue if isinstance(issue, Mapping) else normalized
        return InteractionOutputEvent(
            "interaction_rejected",
            {
                "target_kind": self._normalized_id(normalized.get("target_kind")),
                "target_id": self._normalized_id(normalized.get("target_id")),
                "intent": self._normalized_text(normalized.get("intent")),
                "item_id": self._normalized_id(normalized.get("item_id")),
                "quest_id": self._normalized_id(normalized.get("quest_id")),
                "count": self._coerce_int(normalized.get("count"), 1),
                "code": self._normalized_text(source.get("code")) or "interaction_failed",
                "message": self._normalized_text(source.get("message")) or "interaction failed",
            },
        )

    def _interaction_failed_event(
        self,
        normalized: Mapping[str, Any],
        *,
        message: str,
    ) -> InteractionOutputEvent:
        payload = dict(self._interaction_rejected_event(normalized).payload)
        payload["code"] = "interaction_failed"
        payload["message"] = message
        return InteractionOutputEvent("interaction_rejected", payload)

    def _action_result_event(
        self,
        *,
        action_type: str,
        result: Any,
    ) -> InteractionOutputEvent:
        return InteractionOutputEvent(
            "action_result",
            {
                "success": bool(getattr(result, "success", False)),
                "action_type": action_type,
                "time_cost": float(getattr(result, "time_cost", 0.0)),
                "errors": list(getattr(result, "errors", [])),
                "metadata": dict(getattr(result, "metadata", {})),
                "narrative_hints": list(getattr(result, "narrative_hints", [])),
            },
        )

    @staticmethod
    def _build_talk_snapshot(
        context: InteractionContext,
        target_id: str,
        quest_id: str,
    ) -> dict[str, Any]:
        del quest_id
        return build_talk_snapshot_payload(context, target_id)

    @staticmethod
    def _build_shop_snapshot(
        context: InteractionContext,
        target_id: str,
        quest_id: str,
    ) -> dict[str, Any]:
        del quest_id
        return build_shop_snapshot_payload(context, target_id)

    @staticmethod
    def _build_board_snapshot(
        context: InteractionContext,
        target_id: str,
        quest_id: str,
    ) -> dict[str, Any]:
        del quest_id
        return build_board_snapshot_payload(context, target_id)

    @staticmethod
    def _build_quest_brief_snapshot(
        context: InteractionContext,
        target_id: str,
        quest_id: str,
    ) -> dict[str, Any]:
        return build_quest_brief_payload(context, target_id, quest_id)

    @staticmethod
    def _build_quest_progress_snapshot(
        context: InteractionContext,
        target_id: str,
        quest_id: str,
    ) -> dict[str, Any]:
        return build_quest_progress_payload(context, target_id, quest_id)

    @staticmethod
    def _build_quest_location_snapshot(
        context: InteractionContext,
        target_id: str,
        quest_id: str,
    ) -> dict[str, Any]:
        return build_quest_location_payload(context, target_id, quest_id)

    @staticmethod
    def _build_quest_requirements_snapshot(
        context: InteractionContext,
        target_id: str,
        quest_id: str,
    ) -> dict[str, Any]:
        return build_quest_requirements_payload(context, target_id, quest_id)

    @staticmethod
    def _build_quest_reward_snapshot(
        context: InteractionContext,
        target_id: str,
        quest_id: str,
    ) -> dict[str, Any]:
        return build_quest_reward_payload(context, target_id, quest_id)

    @staticmethod
    def _first_error(
        result: Any,
        *,
        default: str,
    ) -> str:
        errors = list(getattr(result, "errors", []))
        if errors:
            text = str(errors[0]).strip()
            if text:
                return text
        return default

    @staticmethod
    def _normalized_text(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _normalized_id(value: Any) -> str | None:
        text = str(value or "").strip()
        return text or None

    @staticmethod
    def _coerce_int(value: Any, default: int) -> int:
        if value is None or isinstance(value, bool):
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
