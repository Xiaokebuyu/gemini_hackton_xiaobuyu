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
from typing import Any

from app.game_core.content import WorldInstance
from app.game_core.orchestration.presence import get_area_npcs, is_colocated
from app.game_core.state import StateContainer
from app.game_core.state.quest_runtime import normalize_runtime_dynamic_quest


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
    npc_tags: dict[str, frozenset[str]]
    dynamic_quests: dict[str, dict[str, Any]]
    board_quest_offers: dict[str, str]


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

    # Collect all area IDs to scan: AreaSlice runtime areas + CharacterRegistry
    # declared areas (for NPCs that exist only in content, never moved at runtime).
    all_area_ids: set[str] = set()
    for raw_area_id in state.areas.areas:
        aid = str(raw_area_id).strip()
        if aid:
            all_area_ids.add(aid)
    if world.has_registry("characters"):
        for template in world.characters.list_all():
            for field_name in ("area_id", "current_area"):
                raw_val = str(getattr(template, field_name, "") or "").strip()
                if raw_val:
                    all_area_ids.add(raw_val)
                    break

    # Build npc_positions using the shared presence helper (covers both primary
    # AreaSlice data and CharacterRegistry fallback per area).
    npc_positions: dict[str, tuple[str | None, str | None]] = {}
    for aid in all_area_ids:
        for npc_id, sub_loc in get_area_npcs(state, world, aid).items():
            if npc_id not in npc_positions:
                npc_positions[npc_id] = (aid, sub_loc)

    # NPC names from CharacterRegistry
    npc_names: dict[str, str] = {}
    npc_tags: dict[str, frozenset[str]] = {}
    if world.has_registry("characters"):
        for template in world.characters.list_all():
            npc_id = template.id.strip()
            if npc_id:
                npc_names[npc_id] = template.name.strip() or npc_id
                npc_tags[npc_id] = frozenset(
                    str(tag).strip().lower()
                    for tag in getattr(template, "tags", [])
                    if str(tag).strip()
                )

    # Dynamic quests from QuestSlice
    dynamic_quests = {
        str(key): dict(value)
        for key, value in state.quests.dynamic_quests.items()
        if str(key).strip()
    }

    board_quest_offers: dict[str, str] = {}
    if current_area and state.has_slice("areas"):
        for board_id, entries in state.areas.get_all_board_bulletins(current_area).items():
            for entry in entries:
                quest_id = str(entry.get("quest_id", "")).strip()
                if quest_id and quest_id not in board_quest_offers:
                    board_quest_offers[quest_id] = board_id

    return InteractionPolicyContext(
        current_area=current_area,
        current_location=current_location,
        npc_positions=npc_positions,
        npc_names=npc_names,
        npc_tags=npc_tags,
        dynamic_quests=dynamic_quests,
        board_quest_offers=board_quest_offers,
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
    return {
        "code": "invalid_target_kind",
        "message": "target_kind must be npc",
    }


def validate_preconditions(
    context: InteractionPolicyContext,
    target_kind: str,
    target_id: str,
    intent: str,
    quest_id: str | None,
) -> dict[str, str] | None:
    """Check interaction preconditions. Returns an error dict or None."""

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
    if target_kind == "npc" and intent == "accept_quest":
        if quest_id is None:
            return {
                "code": "missing_quest",
                "message": "quest_id is required for npc accept_quest",
            }
        quest_issue = _validate_dynamic_quest_exists(context, quest_id)
        if quest_issue is not None:
            return quest_issue
        role_issue = _validate_required_npc_tag(
            context,
            target_id,
            required_tag="receptionist",
            error_code="npc_cannot_accept_quests",
            action_label="quest acceptance",
        )
        if role_issue is not None:
            return role_issue
        return _validate_quest_offerable(context, quest_id)
    if target_kind == "npc" and intent == "report_quest":
        if quest_id is None:
            return {
                "code": "missing_quest",
                "message": "quest_id is required for npc report_quest",
            }
        quest_issue = _validate_dynamic_quest_exists(context, quest_id)
        if quest_issue is not None:
            return quest_issue
        role_issue = _validate_required_npc_tag(
            context,
            target_id,
            required_tag="receptionist",
            error_code="npc_cannot_report_quests",
            action_label="quest reporting",
        )
        if role_issue is not None:
            return role_issue
        return _validate_quest_reportable(context, quest_id)

    return None


def resolve_accept_quest_board_id(
    context: InteractionPolicyContext,
    quest_id: str,
) -> str | None:
    """Resolve the current-area board that is offering *quest_id*."""
    return context.board_quest_offers.get(quest_id.strip())


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
    if npc_area == context.current_area and is_colocated(npc_location, context.current_location):
        return None
    return {
        "code": "npc_not_present",
        "message": f"npc is not in the current location: {npc_id}",
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


def _validate_required_npc_tag(
    context: InteractionPolicyContext,
    npc_id: str,
    *,
    required_tag: str,
    error_code: str,
    action_label: str,
) -> dict[str, str] | None:
    tags = context.npc_tags.get(npc_id, frozenset())
    if required_tag in tags:
        return None
    return {
        "code": error_code,
        "message": f"npc does not support {action_label}: {npc_id}",
    }


def _validate_quest_offerable(
    context: InteractionPolicyContext,
    quest_id: str,
) -> dict[str, str] | None:
    if resolve_accept_quest_board_id(context, quest_id) is not None:
        return None
    return {
        "code": "quest_not_offerable",
        "message": f"quest is not currently offerable here: {quest_id}",
    }


def _validate_quest_reportable(
    context: InteractionPolicyContext,
    quest_id: str,
) -> dict[str, str] | None:
    quest_map = normalize_runtime_dynamic_quest(
        quest_id,
        context.dynamic_quests.get(quest_id, {}),
    )
    if not bool(quest_map.get("requires_report")):
        return {
            "code": "quest_not_reportable",
            "message": f"quest does not require reporting: {quest_id}",
        }

    status = str(quest_map.get("status", "")).strip().lower()
    if status != "completed":
        return {
            "code": "quest_not_ready_to_report",
            "message": f"quest is not ready to report: {quest_id}",
        }

    if bool(quest_map.get("reported")):
        return {
            "code": "quest_already_reported",
            "message": f"quest has already been reported: {quest_id}",
        }
    return None
