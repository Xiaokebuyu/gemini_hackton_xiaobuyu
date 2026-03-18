"""WorldBuilder sub-system — environmental directive handlers.

Handles: plant_environmental, fill_area, fill_location.

Decision record: D-P20b (narrative.md)
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Mapping

from app.game_core.orchestration.models import SSEEvent
from app.game_core.planning.dynamic_sub_area import DynamicSubAreaManager
from app.game_core.planning.subsystem import PlannerEvent, SubSystemResult
from app.game_core.planning.utils import coerce_non_empty_string, string_or_empty
from app.game_core.rules.models import Command

if TYPE_CHECKING:
    from app.game_core.adapters.planner_system import PlannerAgentPort
    from app.game_core.orchestration.settlement import SettlementContext

logger = logging.getLogger(__name__)

_LABEL_FALLBACK_MAX_CHARS = 20


def _resolve_sub_area_label(payload: Mapping[str, Any]) -> str:
    """Return the display label for a plant_environmental sub-area.

    Prefers the explicit ``label`` param; falls back to the first
    ``_LABEL_FALLBACK_MAX_CHARS`` characters of ``description`` + "…".
    """
    explicit = coerce_non_empty_string(payload.get("label"))
    if explicit is not None:
        return explicit.strip()
    description = str(payload.get("description") or "").strip()
    if not description:
        return ""
    if len(description) > _LABEL_FALLBACK_MAX_CHARS:
        return description[:_LABEL_FALLBACK_MAX_CHARS] + "…"
    return description


class WorldBuilderSubSystem:
    """PlannerSubSystem responsible for environmental/world-building directives."""

    _HANDLES: frozenset[str] = frozenset({"plant_environmental", "fill_area", "fill_location", "plant_encounter", "discover_room", "fill_room"})

    def __init__(
        self,
        *,
        sub_area_manager: DynamicSubAreaManager | None = None,
        sse_collector: list[SSEEvent] | None = None,
        agent: PlannerAgentPort | None = None,
    ) -> None:
        self._sub_area_manager = sub_area_manager
        # Reference to Hook's _pending_sse scratch buffer. Directives append SSE
        # events here; Hook.execute() drains the list at the end of each call.
        self._sse_collector: list[SSEEvent] = sse_collector if sse_collector is not None else []
        self._agent = agent

    # ------------------------------------------------------------------
    # PlannerSubSystem protocol
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "world_builder"

    @property
    def handles(self) -> frozenset[str]:
        return self._HANDLES

    def accepts_event(self, event: PlannerEvent) -> bool:
        return event.kind in {
            "area_entered",
            "sub_location_entered",
            "scene_changed",
            "area_sparse",
            "location_sparse",
            "milestone_completed",
            "quest_created",
            "world_event_available",
            "world_event_active",
            "world_event_resolved",
            "rest_completed",
        }

    async def evaluate(self, event: PlannerEvent, context: Any) -> SubSystemResult:
        if self._agent is not None:
            return await self._evaluate_with_agent(event, context)
        return SubSystemResult()

    def apply_directive(
        self,
        kind: str,
        payload: dict[str, Any],
        context: Any,
        *,
        current_tick: int,
    ) -> bool | str:
        if kind == "plant_environmental":
            return self._apply_plant_environmental(payload, context, current_tick=current_tick)
        if kind == "fill_area":
            return self._apply_fill_area(payload, context, current_tick=current_tick)
        if kind == "fill_location":
            return self._apply_fill_location(payload, context, current_tick=current_tick)
        if kind == "plant_encounter":
            return self._apply_plant_encounter(payload, context, current_tick=current_tick)
        if kind == "discover_room":
            return self._apply_discover_room(payload, context, current_tick=current_tick)
        if kind == "fill_room":
            return self._apply_fill_room(payload, context, current_tick=current_tick)
        return "unsupported_kind"

    # ------------------------------------------------------------------
    # Handler: plant_environmental
    # ------------------------------------------------------------------

    def _apply_plant_environmental(
        self,
        payload: dict[str, Any],
        context: SettlementContext,
        *,
        current_tick: int,
    ) -> bool | str:
        translated = self._translate_environmental_clue_payload(payload, context)
        if translated is not None:
            return self._apply_fill_location(
                translated,
                context,
                current_tick=current_tick,
            )
        self._preview_sub_area_create(
            area_id=coerce_non_empty_string(payload.get("area_id")),
            spec={
                "id": coerce_non_empty_string(payload.get("clue_id")) or f"clue_{current_tick}",
                "label": _resolve_sub_area_label(payload),
                "description": string_or_empty(payload.get("description")),
                "type": "discovery",
                "tier": "temporary",
                "discovery_mode": coerce_non_empty_string(payload.get("discovery_mode")) or "check",
                "discovery_dc": payload.get("dc", 12),
                "linked_quest_id": coerce_non_empty_string(payload.get("linked_quest_id")),
                "linked_milestone": coerce_non_empty_string(payload.get("linked_milestone")),
                "source": "narrative_planner",
                "created_at_tick": current_tick,
                "expiry_ticks": payload.get("expiry_ticks", 12),
            },
        )
        params = dict(payload)
        params["current_tick"] = current_tick
        # C3: propagate location context as parent_location_id / parent_room_id so
        # the created sub-area knows which location it belongs to, even when Path A
        # (fill_location translation) was not used. Prefer explicit payload values;
        # fall back to player position.
        if coerce_non_empty_string(params.get("parent_location_id")) is None:
            resolved_location = (
                coerce_non_empty_string(payload.get("location_id"))
                or (
                    coerce_non_empty_string(context.state.player.current_location)
                    if context.state.has_slice("player")
                    else None
                )
            )
            if resolved_location is not None:
                params["parent_location_id"] = resolved_location
                if coerce_non_empty_string(params.get("parent_room_id")) is None:
                    resolved_room = (
                        coerce_non_empty_string(payload.get("room_id"))
                        or (
                            coerce_non_empty_string(context.state.player.current_room)
                            if context.state.has_slice("player")
                            else None
                        )
                    )
                    if resolved_room is not None:
                        params["parent_room_id"] = resolved_room
        result = context.execute_command(
            Command(
                type="planner_plant_environmental",
                params=params,
                source="narrative_planner",
            )
        )
        if not result.executed:
            return "; ".join(result.errors) if result.errors else "command_failed"
        metadata = dict(result.metadata) if isinstance(result.metadata, dict) else {}
        area_id = coerce_non_empty_string(metadata.get("area_id"))
        sub_area_id = coerce_non_empty_string(metadata.get("sub_area_id"))
        sub_area_label = string_or_empty(metadata.get("sub_area_label"))
        if area_id is None or sub_area_id is None:
            return "missing_area_or_sub_area_id"
        self._sse_collector.append(SSEEvent(
            event_type="environment_changed",
            payload={
                "area_id": area_id,
                "sub_area_id": sub_area_id,
                "sub_area_label": sub_area_label,
                "change_type": "plant_environmental",
            },
        ))
        context.scene_bus.add_entry({
            "source": "ENGINE",
            "content": (
                f"[ENGINE:environment_changed] New discovery point appeared:"
                f" {sub_area_label or sub_area_id}"
            ),
            "visibility": "system",
            "tags": ["environment_changed", "narrative_planner"],
        })
        return True

    def _translate_environmental_clue_payload(
        self,
        payload: Mapping[str, Any],
        context: SettlementContext,
    ) -> dict[str, Any] | None:
        if payload.get("interactables") or payload.get("resident_npcs"):
            return None

        area_id = coerce_non_empty_string(payload.get("area_id"))
        clue_id = coerce_non_empty_string(payload.get("clue_id"))
        if area_id is None or clue_id is None:
            return None

        location_id = coerce_non_empty_string(payload.get("location_id"))
        room_id = coerce_non_empty_string(payload.get("room_id"))
        if context.state.has_slice("player"):
            if location_id is None:
                location_id = coerce_non_empty_string(context.state.player.current_location)
            if room_id is None:
                room_id = coerce_non_empty_string(context.state.player.current_room)
        if location_id is None and context.world.has_registry("maps"):
            area_template = context.world.maps.get(area_id)
            if area_template is not None:
                location_id = coerce_non_empty_string(getattr(area_template, "default_sub_location", None))
                if room_id is None and location_id is not None:
                    sub_loc = area_template.sub_locations.get(location_id)
                    if sub_loc is not None:
                        room_id = coerce_non_empty_string(getattr(sub_loc, "default_room", None))
        if location_id is None:
            return None

        name = (
            coerce_non_empty_string(payload.get("label"))
            or coerce_non_empty_string(payload.get("name"))
            or _humanize_identifier(clue_id)
        )
        description = string_or_empty(payload.get("description"))

        # Collect tags common to both schema paths
        tags = [
            str(item).strip()
            for item in payload.get("tags", [])
            if isinstance(item, str) and str(item).strip()
        ]
        for tag in ("clue", "party_discussion"):
            if tag not in tags:
                tags.append(tag)

        # ── Simplified schema path (base_effects present, no legacy options) ──
        if "base_effects" in payload and "options" not in payload:
            raw_hints = payload.get("content_hints", payload.get("hints"))
            party_prompt_hints = [
                str(item).strip()
                for item in raw_hints
                if isinstance(item, str) and str(item).strip()
            ] if isinstance(raw_hints, list) else []
            clue_interactable = {
                "id": clue_id,
                "name": name,
                "description": description,
                "type": "inspect",
                "tags": tags,
                "functional": {
                    "type": "investigate_clue",
                    "params": {
                        "clue_id": clue_id,
                        "base_effects": list(payload.get("base_effects") or []),
                        "check": payload.get("check"),
                        "check_effects": list(payload.get("check_effects") or []),
                        "narrative": str(payload.get("narrative") or ""),
                        "topic": coerce_non_empty_string(payload.get("topic")),
                        "linked_quest_id": coerce_non_empty_string(payload.get("linked_quest_id")),
                        "linked_milestone": coerce_non_empty_string(payload.get("linked_milestone")),
                        "party_prompt_hints": party_prompt_hints,
                        "hide_on_resolve": True,
                    },
                },
            }
            return {
                "area_id": area_id,
                "location_id": location_id,
                "room_id": room_id,
                "interactables": [clue_interactable],
            }

        # ── Legacy schema path (options/outcomes) ──
        raw_options = payload.get("options")
        options = raw_options if isinstance(raw_options, list) and 2 <= len(raw_options) <= 4 else [
            {"id": "examine", "label": "仔细检查"},
            {"id": "ask_party", "label": "听听队友判断"},
        ]
        raw_outcomes = payload.get("outcomes")
        outcomes = raw_outcomes if isinstance(raw_outcomes, Mapping) else {
            "examine": [],
            "ask_party": [],
        }
        raw_hints = payload.get("content_hints", payload.get("hints"))
        party_prompt_hints = [
            str(item).strip()
            for item in raw_hints
            if isinstance(item, str) and str(item).strip()
        ] if isinstance(raw_hints, list) else []

        clue_interactable = {
            "id": clue_id,
            "name": name,
            "description": description,
            "type": "inspect",
            "tags": tags,
            "functional": {
                "type": "investigate_clue",
                "params": {
                    "clue_id": clue_id,
                    "topic": coerce_non_empty_string(payload.get("topic")),
                    "linked_quest_id": coerce_non_empty_string(payload.get("linked_quest_id")),
                    "linked_milestone": coerce_non_empty_string(payload.get("linked_milestone")),
                    "party_prompt_hints": party_prompt_hints,
                    "options": options,
                    "outcomes": outcomes,
                    "hide_on_resolve": True,
                },
            },
        }
        return {
            "area_id": area_id,
            "location_id": location_id,
            "room_id": room_id,
            "interactables": [clue_interactable],
        }

    # ------------------------------------------------------------------
    # Handler: fill_area
    # ------------------------------------------------------------------

    def _apply_fill_area(
        self,
        payload: dict[str, Any],
        context: SettlementContext,
        *,
        current_tick: int,
    ) -> bool | str:
        self._preview_sub_area_create(
            area_id=coerce_non_empty_string(payload.get("area_id")),
            spec={
                "id": coerce_non_empty_string(payload.get("id")) or f"fill_{current_tick}",
                "label": string_or_empty(payload.get("label")),
                "description": string_or_empty(payload.get("description")),
                "type": coerce_non_empty_string(payload.get("type")) or "visit",
                "tier": "permanent",
                "source": "narrative_planner",
                "created_at_tick": current_tick,
                "expiry_ticks": payload.get("expiry_ticks", -1),
            },
        )
        params = dict(payload)
        params["current_tick"] = current_tick
        result = context.execute_command(
            Command(
                type="planner_fill_area",
                params=params,
                source="narrative_planner",
            )
        )
        if not result.executed:
            return "; ".join(result.errors) if result.errors else "command_failed"
        metadata = dict(result.metadata) if isinstance(result.metadata, dict) else {}
        area_id = coerce_non_empty_string(metadata.get("area_id"))
        sub_area_id = coerce_non_empty_string(metadata.get("sub_area_id"))
        sub_area_label = string_or_empty(metadata.get("sub_area_label"))
        if area_id is None or sub_area_id is None:
            return "missing_area_or_sub_area_id"
        self._sse_collector.append(SSEEvent(
            event_type="environment_changed",
            payload={
                "area_id": area_id,
                "sub_area_id": sub_area_id,
                "sub_area_label": sub_area_label,
                "change_type": "fill_area",
            },
        ))
        context.scene_bus.add_entry({
            "source": "ENGINE",
            "content": (
                f"[ENGINE:environment_changed] New area location added:"
                f" {sub_area_label or sub_area_id}"
            ),
            "visibility": "system",
            "tags": ["environment_changed", "narrative_planner"],
        })
        return True

    def _apply_fill_location(
        self,
        payload: dict[str, Any],
        context: SettlementContext,
        *,
        current_tick: int,
    ) -> bool | str:
        params = dict(payload)
        params["current_tick"] = current_tick
        result = context.execute_command(
            Command(
                type="planner_fill_location",
                params=params,
                source="narrative_planner",
            )
        )
        if not result.executed:
            return "; ".join(result.errors) if result.errors else "command_failed"
        metadata = dict(result.metadata) if isinstance(result.metadata, dict) else {}
        area_id = coerce_non_empty_string(metadata.get("area_id"))
        location_id = coerce_non_empty_string(metadata.get("location_id"))
        room_id = coerce_non_empty_string(metadata.get("room_id"))
        interactable_count = int(metadata.get("interactable_count", 0))
        if area_id is None or location_id is None:
            return "missing_area_or_location_id"
        self._sse_collector.append(SSEEvent(
            event_type="environment_changed",
            payload={
                "area_id": area_id,
                "location_id": location_id,
                "room_id": room_id,
                "change_type": "fill_location",
                "interactable_count": interactable_count,
            },
        ))
        scope_label = f"{location_id}/{room_id}" if room_id is not None else location_id
        context.scene_bus.add_entry({
            "source": "ENGINE",
            "content": (
                f"[ENGINE:environment_changed] Scene interactables updated at"
                f" {scope_label} ({interactable_count})"
            ),
            "visibility": "system",
            "tags": ["environment_changed", "narrative_planner", "fill_location"],
        })
        return True

    # ------------------------------------------------------------------
    # Handler: plant_encounter
    # ------------------------------------------------------------------

    def _apply_plant_encounter(
        self,
        payload: dict[str, Any],
        context: SettlementContext,
        *,
        current_tick: int,
    ) -> bool | str:
        params = dict(payload)
        params["current_tick"] = current_tick
        result = context.execute_command(
            Command(
                type="planner_plant_encounter",
                params=params,
                source="narrative_planner",
            )
        )
        if not result.executed:
            return "; ".join(result.errors) if result.errors else "command_failed"
        metadata = dict(result.metadata) if isinstance(result.metadata, dict) else {}
        area_id = coerce_non_empty_string(metadata.get("area_id"))
        sub_area_id = coerce_non_empty_string(metadata.get("sub_area_id"))
        entry = metadata.get("entry")
        if area_id is None or sub_area_id is None or not isinstance(entry, Mapping):
            return "missing_area_or_sub_area_or_entry"
        context.scene_bus.add_entry({
            "source": "ENGINE",
            "content": (
                f"[ENGINE:encounter_planted] Encounter seeded at {sub_area_id}"
                f" in {area_id}: {entry.get('description') or ', '.join(entry.get('monster_ids', [])[:3])}"
            ),
            "visibility": "system",
            "tags": ["encounter_planted", "narrative_planner"],
        })
        return True

    # ------------------------------------------------------------------
    # Handler: discover_room
    # ------------------------------------------------------------------

    def _apply_discover_room(
        self,
        payload: dict[str, Any],
        context: SettlementContext,
        *,
        current_tick: int,
    ) -> bool | str:
        params = dict(payload)
        params["current_tick"] = current_tick
        result = context.execute_command(
            Command(
                type="planner_discover_room",
                params=params,
                source="narrative_planner",
            )
        )
        if not result.executed:
            return "; ".join(result.errors) if result.errors else "command_failed"
        metadata = dict(result.metadata) if isinstance(result.metadata, dict) else {}
        area_id = coerce_non_empty_string(metadata.get("area_id"))
        location_id = coerce_non_empty_string(metadata.get("location_id"))
        room_id = coerce_non_empty_string(metadata.get("room_id"))
        if area_id is None or location_id is None or room_id is None:
            return "missing_area_or_location_or_room_id"
        self._sse_collector.append(SSEEvent(
            event_type="room_discovered",
            payload={
                "area_id": area_id,
                "location_id": location_id,
                "room_id": room_id,
            },
        ))
        context.scene_bus.add_entry({
            "source": "ENGINE",
            "content": (
                f"[ENGINE:room_discovered] Room '{room_id}' discovered in"
                f" {location_id} ({area_id})"
            ),
            "visibility": "system",
            "tags": ["room_discovered", "narrative_planner"],
        })
        return True

    # ------------------------------------------------------------------
    # Handler: fill_room
    # ------------------------------------------------------------------

    def _apply_fill_room(
        self,
        payload: dict[str, Any],
        context: SettlementContext,
        *,
        current_tick: int,
    ) -> bool | str:
        params = dict(payload)
        params["current_tick"] = current_tick
        result = context.execute_command(
            Command(
                type="planner_fill_room",
                params=params,
                source="narrative_planner",
            )
        )
        if not result.executed:
            return "; ".join(result.errors) if result.errors else "command_failed"
        metadata = dict(result.metadata) if isinstance(result.metadata, dict) else {}
        area_id = coerce_non_empty_string(metadata.get("area_id"))
        location_id = coerce_non_empty_string(metadata.get("location_id"))
        room_id = coerce_non_empty_string(metadata.get("room_id"))
        name = string_or_empty(metadata.get("name"))
        if area_id is None or location_id is None or room_id is None:
            return "missing_area_or_location_or_room_id"
        self._sse_collector.append(SSEEvent(
            event_type="dynamic_room_added",
            payload={
                "area_id": area_id,
                "location_id": location_id,
                "room_id": room_id,
                "name": name,
            },
        ))
        context.scene_bus.add_entry({
            "source": "ENGINE",
            "content": (
                f"[ENGINE:dynamic_room_added] New room '{name or room_id}' added to"
                f" {location_id} in {area_id}"
            ),
            "visibility": "system",
            "tags": ["dynamic_room_added", "narrative_planner"],
        })
        return True

    def _preview_sub_area_create(
        self,
        *,
        area_id: str | None,
        spec: dict[str, Any],
    ) -> None:
        manager = self._sub_area_manager
        if manager is None or area_id is None:
            return
        if isinstance(manager, DynamicSubAreaManager):
            return
        create = getattr(manager, "create", None)
        if not callable(create):
            return
        try:
            create(area_id, dict(spec))
        except Exception:
            logger.debug("WorldBuilderSubSystem: preview create failed", exc_info=True)

    async def _evaluate_with_agent(
        self,
        event: PlannerEvent,
        context: SettlementContext,
    ) -> SubSystemResult:
        base_context = event.payload.get("planner_context", {})
        if not isinstance(base_context, Mapping):
            base_context = {}
        agent_context = dict(base_context)
        current_event = {
            "kind": event.kind,
            "tick": event.tick,
            "source": event.source,
            "emitter": event.emitter,
            "round_index": event.round_index,
            "payload": {
                key: value
                for key, value in event.payload.items()
                if key != "planner_context"
            },
        }
        agent_context["event"] = current_event
        agent_context["current_event"] = current_event
        raw = await self._agent.evaluate(agent_context)
        directives = raw.get("directives", []) if isinstance(raw, Mapping) else []
        story_facts = raw.get("story_facts", []) if isinstance(raw, Mapping) else []
        strategy_notes = (
            string_or_empty(raw.get("strategy_notes"))
            if isinstance(raw, Mapping)
            else ""
        )
        metadata = {}
        if isinstance(raw, Mapping):
            raw_meta = raw.get("metadata")
            if isinstance(raw_meta, Mapping):
                metadata = dict(raw_meta)
        return SubSystemResult(
            directives=list(directives) if isinstance(directives, list) else [],
            story_facts=(
                [dict(item) for item in story_facts if isinstance(item, Mapping)]
                if isinstance(story_facts, list)
                else []
            ),
            strategy_notes=strategy_notes,
            metadata=metadata,
        )


def _humanize_identifier(value: str) -> str:
    normalized = value.replace("_", " ").replace("-", " ").strip()
    if not normalized:
        return value
    return normalized.title()
