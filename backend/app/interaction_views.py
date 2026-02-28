"""Shared interaction-view payload builders for the API shell."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from app.interaction_service import InteractionContext


def find_linked_bulletin(
    context: InteractionContext,
    quest_id: str,
) -> dict[str, Any] | None:
    """Return the first active bulletin linked to one quest id."""

    if not quest_id:
        return None
    for raw_entry in context.active_bulletins:
        metadata = raw_entry.get("metadata", {})
        metadata_map = metadata if isinstance(metadata, dict) else {}
        if str(metadata_map.get("quest_id", "")).strip() == quest_id:
            return dict(raw_entry)
    return None


def build_board_entries(
    context: InteractionContext,
    board_id: str,
) -> list[dict[str, Any]]:
    """Build normalized board entries for one board id."""

    if not board_id:
        return []
    entries: list[dict[str, Any]] = []
    for raw_entry in context.active_bulletins:
        entry_board_id = str(raw_entry.get("board_id", "")).strip()
        if entry_board_id != board_id:
            continue
        metadata = raw_entry.get("metadata", {})
        metadata_map = metadata if isinstance(metadata, dict) else {}
        quest_id = str(metadata_map.get("quest_id", "")).strip() or None
        quest_status: str | None = None
        if quest_id is not None:
            dynamic_quest = context.dynamic_quests.get(quest_id, {})
            if isinstance(dynamic_quest, dict):
                status_text = str(dynamic_quest.get("status", "")).strip()
                quest_status = status_text or None
        published_at_tick = raw_entry.get("published_at_tick")
        try:
            normalized_tick = int(published_at_tick) if published_at_tick is not None else None
        except (TypeError, ValueError):
            normalized_tick = None
        entries.append(
            {
                "board_id": entry_board_id,
                "quest_id": quest_id,
                "title": str(raw_entry.get("title", "")),
                "content": str(raw_entry.get("content", "")),
                "quest_status": quest_status,
                "source": str(raw_entry.get("source", "")),
                "published_at_tick": normalized_tick,
            }
        )
    return entries


def build_board_snapshot_payload(
    context: InteractionContext,
    board_id: str,
) -> dict[str, Any]:
    """Return the current quest-board view from bulletin and quest state."""

    return {
        "target_kind": "board",
        "target_id": board_id,
        "entries": build_board_entries(context, board_id),
    }


def build_shop_snapshot_payload(
    context: InteractionContext,
    npc_id: str,
) -> dict[str, Any]:
    """Return the current shop-state snapshot for one merchant NPC."""

    raw_state = context.shop_states.get(npc_id, {})
    shop_state = raw_state if isinstance(raw_state, dict) else {}
    stock = shop_state.get("current_stock", [])
    last_refresh_tick = shop_state.get("last_refresh_tick")
    try:
        normalized_tick = int(last_refresh_tick) if last_refresh_tick is not None else None
    except (TypeError, ValueError):
        normalized_tick = None
    return {
        "npc_id": npc_id,
        "stock": list(stock) if isinstance(stock, list) else [],
        "last_refresh_tick": normalized_tick,
    }


def build_talk_snapshot_payload(
    context: InteractionContext,
    npc_id: str,
) -> dict[str, Any]:
    """Return the current read-only NPC profile view for talk interactions."""

    area_id, location_id = context.npc_positions.get(npc_id, (None, None))
    relationship_stage = str(context.relationship_stages.get(npc_id, "")).strip() or None
    disposition = dict(context.npc_dispositions.get(npc_id, {}))
    for key in ("approval", "trust", "fear", "romance"):
        try:
            disposition[key] = int(disposition.get(key, 0))
        except (TypeError, ValueError):
            disposition[key] = 0
    impressions = context.npc_impressions.get(npc_id, [])
    recent_impressions = impressions[-3:] if isinstance(impressions, list) else []
    return {
        "target_kind": "npc",
        "target_id": npc_id,
        "profile": {
            "npc_id": npc_id,
            "name": context.npc_names.get(npc_id, npc_id),
            "area_id": area_id,
            "location_id": location_id,
            "relationship_stage": relationship_stage,
            "disposition": disposition,
            "recent_impressions": recent_impressions,
        },
    }


def build_quest_brief_payload(
    context: InteractionContext,
    npc_id: str,
    quest_id: str,
) -> dict[str, Any]:
    """Return one read-only dynamic-quest brief for NPC ask_quest interactions."""

    quest_map = context.dynamic_quests.get(quest_id, {})
    linked_bulletin = find_linked_bulletin(context, quest_id)
    linked_metadata = {}
    if isinstance(linked_bulletin, dict):
        raw_metadata = linked_bulletin.get("metadata", {})
        linked_metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
    return {
        "target_kind": "npc",
        "target_id": npc_id,
        "quest": {
            "quest_id": quest_id,
            "quest_kind": "dynamic",
            "status": str(quest_map.get("status", "")).strip(),
            "title": str(quest_map.get("title", "")),
            "summary": str(quest_map.get("summary", "")),
            "listed_on_board": linked_bulletin is not None,
            "board_id": (
                str(linked_bulletin.get("board_id", "")).strip()
                if isinstance(linked_bulletin, dict)
                else None
            ) or None,
            "board_title": (
                str(linked_bulletin.get("title", ""))
                if isinstance(linked_bulletin, dict)
                else None
            ) or None,
            "source_milestone": str(linked_metadata.get("source_milestone", "")).strip()
            or None,
        },
    }


def build_quest_progress_payload(
    context: InteractionContext,
    npc_id: str,
    quest_id: str,
) -> dict[str, Any]:
    """Return one read-only dynamic-quest status snapshot for NPC ask_progress."""

    quest_map = context.dynamic_quests.get(quest_id, {})
    status = str(quest_map.get("status", "")).strip()
    linked_bulletin = find_linked_bulletin(context, quest_id)
    linked_metadata = {}
    if isinstance(linked_bulletin, dict):
        raw_metadata = linked_bulletin.get("metadata", {})
        linked_metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
    source_milestone = str(linked_metadata.get("source_milestone", "")).strip() or None
    source_milestone_state: str | None = None
    if source_milestone:
        resolved_state = context.milestone_states.get(source_milestone)
        if isinstance(resolved_state, str):
            source_milestone_state = resolved_state or None
        elif isinstance(resolved_state, dict):
            source_milestone_state = str(resolved_state.get("state", "")).strip() or None
    return {
        "target_kind": "npc",
        "target_id": npc_id,
        "quest": {
            "quest_id": quest_id,
            "quest_kind": "dynamic",
            "status": status,
            "title": str(quest_map.get("title", "")),
            "summary": str(quest_map.get("summary", "")),
            "can_accept": status == "available",
            "is_active": status == "active",
            "is_closed": status in {"completed", "failed", "retired"},
            "listed_on_board": linked_bulletin is not None,
            "board_id": (
                str(linked_bulletin.get("board_id", "")).strip()
                if isinstance(linked_bulletin, dict)
                else None
            ) or None,
            "source_milestone": source_milestone,
            "source_milestone_state": source_milestone_state,
        },
    }


def build_quest_location_payload(
    context: InteractionContext,
    npc_id: str,
    quest_id: str,
) -> dict[str, Any]:
    """Return one read-only dynamic-quest location hint for NPC ask_location."""

    quest_map = context.dynamic_quests.get(quest_id, {})
    status = str(quest_map.get("status", "")).strip()
    resolved_area_id = str(quest_map.get("area_id", "")).strip() or None
    resolved_location_id = str(quest_map.get("location_id", "")).strip() or None
    linked_bulletin = find_linked_bulletin(context, quest_id)
    linked_metadata = {}
    if isinstance(linked_bulletin, dict):
        raw_metadata = linked_bulletin.get("metadata", {})
        linked_metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
    return {
        "target_kind": "npc",
        "target_id": npc_id,
        "quest": {
            "quest_id": quest_id,
            "quest_kind": "dynamic",
            "status": status,
            "location_known": bool(resolved_area_id or resolved_location_id),
            "area_id": resolved_area_id,
            "location_id": resolved_location_id,
            "board_id": (
                str(linked_bulletin.get("board_id", "")).strip()
                if isinstance(linked_bulletin, dict)
                else None
            ) or None,
            "source_milestone": str(linked_metadata.get("source_milestone", "")).strip()
            or None,
        },
    }


def build_quest_requirements_payload(
    context: InteractionContext,
    npc_id: str,
    quest_id: str,
) -> dict[str, Any]:
    """Return one read-only dynamic-quest requirements view for NPC ask_requirements."""

    quest_map = context.dynamic_quests.get(quest_id, {})
    status = str(quest_map.get("status", "")).strip()
    raw_requirements = quest_map.get("requirements", [])
    requirements = (
        [str(item).strip() for item in raw_requirements if str(item).strip()]
        if isinstance(raw_requirements, list)
        else []
    )
    linked_bulletin = find_linked_bulletin(context, quest_id)
    linked_metadata = {}
    if isinstance(linked_bulletin, dict):
        raw_metadata = linked_bulletin.get("metadata", {})
        linked_metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
    if status == "available":
        gating_reason: str | None = None
    elif status == "active":
        gating_reason = "quest is already active"
    elif status:
        gating_reason = f"quest is {status}"
    else:
        gating_reason = None
    return {
        "target_kind": "npc",
        "target_id": npc_id,
        "quest": {
            "quest_id": quest_id,
            "quest_kind": "dynamic",
            "status": status,
            "requirements_known": bool(requirements),
            "requirements": requirements,
            "can_accept": status == "available",
            "gating_reason": gating_reason,
            "source_milestone": str(linked_metadata.get("source_milestone", "")).strip()
            or None,
        },
    }


def build_quest_reward_payload(
    context: InteractionContext,
    npc_id: str,
    quest_id: str,
) -> dict[str, Any]:
    """Return one read-only dynamic-quest reward view for NPC ask_reward."""

    quest_map = context.dynamic_quests.get(quest_id, {})
    status = str(quest_map.get("status", "")).strip()
    reward_gold = quest_map.get("reward_gold")
    try:
        gold = int(reward_gold) if reward_gold is not None and int(reward_gold) >= 0 else None
    except (TypeError, ValueError):
        gold = None
    raw_items = quest_map.get("reward_items", [])
    items: list[dict[str, Any]] = []
    if isinstance(raw_items, list):
        for raw_item in raw_items:
            if not isinstance(raw_item, dict):
                continue
            item_id = str(raw_item.get("item_id", "")).strip()
            if not item_id:
                continue
            try:
                count = int(raw_item.get("count", 1))
            except (TypeError, ValueError):
                count = 1
            if count < 1:
                count = 1
            items.append({"item_id": item_id, "count": count})
    reward_summary = str(quest_map.get("reward_summary", "")).strip() or None
    return {
        "target_kind": "npc",
        "target_id": npc_id,
        "quest": {
            "quest_id": quest_id,
            "quest_kind": "dynamic",
            "status": status,
            "reward_known": bool(gold is not None or items or reward_summary is not None),
            "gold": gold,
            "items": items,
            "reward_summary": reward_summary,
        },
    }
