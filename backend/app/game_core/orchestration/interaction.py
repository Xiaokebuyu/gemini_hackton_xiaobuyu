"""Interaction policy validation — game-core boundary for interaction rules.

This module owns the "is this interaction allowed?" decision:
  - Spatial presence checks (NPC / board reachability)
  - Precondition validation (quest state transitions)
  - Board–quest membership queries

View / snapshot construction stays in the application layer
(app/interaction_service.py + app/interaction_views.py).

Design spec ref: 编排层设计规范 §2.5 (NpcInteractionCoordinator)
Implementation deviation: validation extracted as stateless functions
rather than a full coordinator class — see D-O20 in orchestration.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from app.game_core.content import WorldInstance
from app.game_core.state import StateContainer


@dataclass(frozen=True)
class InteractionPolicyContext:
    """Minimum game-state snapshot needed by interaction validators.

    Does NOT include view-only fields (dispositions, impressions,
    shop_states, player_inventory, item_catalog, etc.) — those belong
    to the application-layer InteractionViewContext.
    """

    current_area: str
    current_location: str | None
    npc_positions: dict[str, tuple[str | None, str | None]]
    npc_names: dict[str, str]
    area_sub_locations: dict[str, list[str]]
    dynamic_quests: dict[str, dict[str, Any]]
    active_bulletins: list[dict[str, Any]]


# ------------------------------------------------------------------
# Context builder
# ------------------------------------------------------------------


def build_interaction_policy_context(
    state: StateContainer,
    world: WorldInstance,
) -> InteractionPolicyContext:
    """Build the minimum policy context from current state and world."""

    player = state.player
    current_area = (player.current_area or "").strip()
    current_location_text = (player.current_location or "").strip()
    current_location = current_location_text or None

    # Area sub-locations from MapRegistry
    area_sub_locations: dict[str, list[str]] = {}
    if world.has_registry("maps"):
        for raw_area in world.maps.list_all():
            area_id = raw_area.id.strip() if raw_area.id else ""
            if not area_id:
                continue
            sub_location_ids: list[str] = []
            for raw_key in raw_area.sub_locations.keys():
                sub_location_id = str(raw_key).strip()
                if sub_location_id:
                    sub_location_ids.append(sub_location_id)
            area_sub_locations[area_id] = sub_location_ids

    # NPC positions from AreaSlice
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
                str(raw_location_id).strip()
                if isinstance(raw_location_id, str)
                else ""
            )
            npc_positions[npc_id] = (area_id, location_text or None)

    # NPC names + fallback positions from CharacterRegistry
    npc_names: dict[str, str] = {}
    if world.has_registry("characters"):
        for raw_character in world.characters.list_all():
            npc_id = raw_character.id.strip()
            if not npc_id:
                continue
            npc_name = raw_character.name.strip() or npc_id
            npc_names[npc_id] = npc_name
            if npc_id in npc_positions:
                continue
            resolved_area: str | None = None
            for field_name in ("area_id", "current_area"):
                raw_val = str(getattr(raw_character, field_name, "") or "").strip()
                if raw_val:
                    resolved_area = raw_val
                    break
            resolved_location: str | None = None
            for field_name in ("location_id", "current_location"):
                raw_val = str(getattr(raw_character, field_name, "") or "").strip()
                if raw_val:
                    resolved_location = raw_val
                    break
            npc_positions[npc_id] = (resolved_area, resolved_location)

    # Dynamic quests from QuestSlice
    dynamic_quests = {
        str(key): dict(value)
        for key, value in state.quests.dynamic_quests.items()
        if str(key).strip()
    }

    # Active bulletins from NarrativePlanSlice
    active_bulletins = [
        dict(item)
        for item in state.narrative_plan.active_bulletins
        if isinstance(item, Mapping)
    ]

    return InteractionPolicyContext(
        current_area=current_area,
        current_location=current_location,
        npc_positions=npc_positions,
        npc_names=npc_names,
        area_sub_locations=area_sub_locations,
        dynamic_quests=dynamic_quests,
        active_bulletins=active_bulletins,
    )


# ------------------------------------------------------------------
# Validators
# ------------------------------------------------------------------


def validate_presence(
    context: InteractionPolicyContext,
    target_kind: str,
    target_id: str,
    intent: str,
) -> dict[str, str] | None:
    """Check spatial reachability. Returns an error dict or None."""

    if target_kind == "npc":
        return _validate_npc_presence(context, target_id, intent)
    if target_kind == "board":
        return _validate_board_presence(context, target_id)
    return {
        "code": "invalid_target_kind",
        "message": "target_kind must be npc or board",
    }


def validate_preconditions(
    context: InteractionPolicyContext,
    target_kind: str,
    target_id: str,
    intent: str,
    quest_id: str | None,
) -> dict[str, str] | None:
    """Check interaction preconditions. Returns an error dict or None."""

    if target_kind == "board" and intent in {"accept", "complete", "retire"}:
        if quest_id is None:
            messages = {
                "accept": "quest_id is required for board accept",
                "complete": "quest_id is required for board complete",
                "retire": "quest_id is required for board retire",
            }
            return {
                "code": "missing_quest",
                "message": messages.get(
                    intent, "quest_id is required for board accept"
                ),
            }
        target_states = {
            "accept": "active",
            "complete": "completed",
            "retire": "retired",
        }
        return _validate_board_transition(
            context, target_id, quest_id, target_states[intent]
        )

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
                "message": messages.get(
                    intent, "quest_id is required for npc ask_quest"
                ),
            }
        return _validate_dynamic_quest_exists(context, quest_id)

    return None


# ------------------------------------------------------------------
# Board–quest membership query
# ------------------------------------------------------------------


def board_has_quest(
    context: InteractionPolicyContext,
    board_id: str,
    quest_id: str,
) -> bool:
    """Check whether a quest is listed on a specific board."""

    for bulletin in context.active_bulletins:
        if str(bulletin.get("board_id", "")).strip() != board_id:
            continue
        metadata = bulletin.get("metadata", {})
        metadata_map = metadata if isinstance(metadata, dict) else {}
        if str(metadata_map.get("quest_id", "")).strip() == quest_id:
            return True
    return False


# ------------------------------------------------------------------
# Private helpers
# ------------------------------------------------------------------


def _validate_npc_presence(
    context: InteractionPolicyContext,
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


def _validate_board_presence(
    context: InteractionPolicyContext,
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


def _validate_board_transition(
    context: InteractionPolicyContext,
    board_id: str,
    quest_id: str,
    to_state: str,
) -> dict[str, str] | None:
    if not board_has_quest(context, board_id, quest_id):
        return {
            "code": "quest_not_listed",
            "message": f"quest is not listed on board: {quest_id}",
        }
    quest_issue = _validate_dynamic_quest_exists(context, quest_id)
    if quest_issue is not None:
        return quest_issue
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


def _validate_dynamic_quest_exists(
    context: InteractionPolicyContext,
    quest_id: str,
) -> dict[str, str] | None:
    if quest_id in context.dynamic_quests:
        return None
    return {
        "code": "quest_not_found",
        "message": f"dynamic quest not found: {quest_id}",
    }
