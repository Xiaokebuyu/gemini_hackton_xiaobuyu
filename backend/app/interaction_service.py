"""Application-layer interaction execution and view orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping

from app.game_core import ManagedSession
from app.game_core.content import WorldInstance
from app.game_core.orchestration.interaction import (
    InteractionPolicyContext,
    build_interaction_policy_context,
    validate_preconditions,
    validate_presence,
)
from app.game_core.orchestration.models import PipelineResult
from app.game_core.rules import Command
from app.game_core.state import StateContainer
from app.interaction_views import (
    build_board_entries,
    build_board_snapshot_payload,
    build_inspect_item_payload,
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
class InteractionViewContext:
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

    area_sub_locations: dict[str, list[str]] = {}
    if world.has_registry("maps"):
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
    if world.has_registry("characters"):
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
    milestone_states: dict[str, str] = {}
    for milestone_id, milestone in quests.milestone_states.items():
        normalized_state = milestone.state.strip()
        if milestone_id.strip() and normalized_state:
            milestone_states[milestone_id.strip()] = normalized_state

    active_bulletins = [
        dict(item) for item in state.narrative_plan.active_bulletins
        if isinstance(item, Mapping)
    ]

    player_gold = player.gold

    player_inventory: list[dict[str, Any]] = []
    for stack in player.inventory:
        player_inventory.append({
            "item_id": stack.item_id,
            "count": max(1, stack.count),
        })

    item_catalog: dict[str, dict[str, Any]] = {}
    if world.has_registry("items"):
        for raw_item in world.items.list_all():
            if not isinstance(raw_item, Mapping):
                continue
            item_id = str(raw_item.get("id", "")).strip()
            if not item_id:
                continue
            item_catalog[item_id] = dict(raw_item)

    return InteractionViewContext(
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
            "talk": ("talk_snapshot", self._build_talk_snapshot),
            "board": ("board_snapshot", self._build_board_snapshot),
            "inspect_item": ("inspect_item", self._build_inspect_item_snapshot),
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
        session: ManagedSession,
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

        # Parse interaction fields once
        target_kind = self._normalized_text(normalized_map.get("target_kind"))
        target_id = self._normalized_id(normalized_map.get("target_id")) or ""
        intent = self._normalized_text(normalized_map.get("intent"))
        quest_id = self._normalized_id(normalized_map.get("quest_id"))

        # Policy validation (game_core)
        state = session.runtime.state
        world = session.runtime.world
        policy_ctx = build_interaction_policy_context(state, world)
        presence_issue = validate_presence(policy_ctx, target_kind, target_id, intent)
        if presence_issue is not None:
            return InteractionExecutionResult(
                success=False,
                reason="interaction_rejected",
                events=[self._interaction_rejected_event(normalized_map, issue=presence_issue)],
            )
        precheck_issue = validate_preconditions(
            policy_ctx, target_kind, target_id, intent, quest_id,
        )
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

    def _execute_snapshot(
        self,
        context: InteractionViewContext,
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
        item_id = (
            self._normalized_id(execution.get("item_id"))
            or self._normalized_id(normalized.get("item_id"))
            or ""
        )
        payload = builder(context, target_id, quest_id, item_id)
        return InteractionExecutionResult(
            success=True,
            reason="completed",
            events=[resolved_event, InteractionOutputEvent(event_type, payload)],
        )

    async def _execute_shop_refresh(
        self,
        session: ManagedSession,
        normalized: Mapping[str, Any],
        resolved_event: InteractionOutputEvent,
    ) -> InteractionExecutionResult:
        target_id = self._normalized_id(normalized.get("target_id")) or ""
        request = _PipelineActionRequest(
            action_type="refresh_shop",
            params={"npc_id": target_id},
            source="system",
        )
        result = await self._execute_structured_action(session, request)
        if not result.success:
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
        refreshed_context = build_interaction_view_context(session.runtime.state, session.runtime.world)
        return InteractionExecutionResult(
            success=True,
            reason="completed",
            events=[
                resolved_event,
                InteractionOutputEvent(
                    "shop_snapshot",
                    self._build_shop_snapshot(refreshed_context, target_id, "", ""),
                ),
            ],
        )

    async def _execute_pipeline_action(
        self,
        session: ManagedSession,
        normalized: Mapping[str, Any],
        execution: Mapping[str, Any],
        resolved_event: InteractionOutputEvent,
    ) -> InteractionExecutionResult:
        action_type = self._normalized_text(execution.get("action_type"))
        params = execution.get("params", {})
        params_map = dict(params) if isinstance(params, Mapping) else {}
        request = _PipelineActionRequest(action_type=action_type, params=params_map)
        result = await self._execute_structured_action(session, request)
        if not result.success:
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
        for event in result.sse_events:
            event_type = self._normalized_text(event.event_type)
            if not event_type:
                continue
            events.append(InteractionOutputEvent(event_type, dict(event.payload)))

        post_snapshot = self._normalized_text(execution.get("post_snapshot"))
        builder_entry = self._post_snapshot_builders.get(post_snapshot)
        if builder_entry is not None:
            refreshed_context = build_interaction_view_context(session.runtime.state, session.runtime.world)
            event_type, builder = builder_entry
            target_id = self._normalized_id(normalized.get("target_id")) or ""
            events.append(
                InteractionOutputEvent(
                    event_type,
                    builder(refreshed_context, target_id, "", ""),
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
        result: PipelineResult,
    ) -> InteractionOutputEvent:
        return InteractionOutputEvent(
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

    @staticmethod
    def _build_talk_snapshot(
        context: InteractionViewContext,
        target_id: str,
        quest_id: str,
        item_id: str,
    ) -> dict[str, Any]:
        del quest_id, item_id
        return build_talk_snapshot_payload(context, target_id)

    @staticmethod
    def _build_shop_snapshot(
        context: InteractionViewContext,
        target_id: str,
        quest_id: str,
        item_id: str,
    ) -> dict[str, Any]:
        del quest_id, item_id
        return build_shop_snapshot_payload(context, target_id)

    @staticmethod
    def _build_board_snapshot(
        context: InteractionViewContext,
        target_id: str,
        quest_id: str,
        item_id: str,
    ) -> dict[str, Any]:
        del quest_id, item_id
        return build_board_snapshot_payload(context, target_id)

    @staticmethod
    def _build_quest_brief_snapshot(
        context: InteractionViewContext,
        target_id: str,
        quest_id: str,
        item_id: str,
    ) -> dict[str, Any]:
        del item_id
        return build_quest_brief_payload(context, target_id, quest_id)

    @staticmethod
    def _build_quest_progress_snapshot(
        context: InteractionViewContext,
        target_id: str,
        quest_id: str,
        item_id: str,
    ) -> dict[str, Any]:
        del item_id
        return build_quest_progress_payload(context, target_id, quest_id)

    @staticmethod
    def _build_quest_location_snapshot(
        context: InteractionViewContext,
        target_id: str,
        quest_id: str,
        item_id: str,
    ) -> dict[str, Any]:
        del item_id
        return build_quest_location_payload(context, target_id, quest_id)

    @staticmethod
    def _build_quest_requirements_snapshot(
        context: InteractionViewContext,
        target_id: str,
        quest_id: str,
        item_id: str,
    ) -> dict[str, Any]:
        del item_id
        return build_quest_requirements_payload(context, target_id, quest_id)

    @staticmethod
    def _build_quest_reward_snapshot(
        context: InteractionViewContext,
        target_id: str,
        quest_id: str,
        item_id: str,
    ) -> dict[str, Any]:
        del item_id
        return build_quest_reward_payload(context, target_id, quest_id)

    @staticmethod
    def _build_inspect_item_snapshot(
        context: InteractionViewContext,
        target_id: str,
        quest_id: str,
        item_id: str,
    ) -> dict[str, Any]:
        del quest_id
        return build_inspect_item_payload(context, target_id, item_id)

    @staticmethod
    def _first_error(
        result: PipelineResult,
        *,
        default: str,
    ) -> str:
        if result.errors:
            text = str(result.errors[0]).strip()
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
