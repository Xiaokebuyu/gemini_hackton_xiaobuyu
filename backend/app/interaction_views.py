"""Shared interaction-view payload builders for the API shell."""

from __future__ import annotations

from typing import Any, Mapping, TYPE_CHECKING

if TYPE_CHECKING:
    from app.interaction_service import InteractionViewContext


def build_shop_snapshot_payload(
    context: InteractionViewContext,
    npc_id: str,
) -> dict[str, Any]:
    """Return the current shop-state snapshot for one merchant NPC."""

    raw_state = context.shop_states.get(npc_id, {})
    shop_state = raw_state if isinstance(raw_state, dict) else {}
    raw_stock = shop_state.get("current_stock", [])
    stock_list = list(raw_stock) if isinstance(raw_stock, list) else []
    last_refresh_tick = shop_state.get("last_refresh_tick")
    try:
        normalized_tick = int(last_refresh_tick) if last_refresh_tick is not None else None
    except (TypeError, ValueError):
        normalized_tick = None

    enriched_stock: list[dict[str, Any]] = []
    for raw_item in stock_list:
        if not isinstance(raw_item, dict):
            continue
        item_id = str(raw_item.get("item_id", "")).strip()
        entry = dict(raw_item)
        catalog = context.item_catalog.get(item_id, {})
        entry["name"] = str(catalog.get("name", item_id))
        entry["type"] = str(catalog.get("type", ""))
        entry["rarity"] = str(catalog.get("rarity", ""))
        enriched_stock.append(entry)

    player_sellable: list[dict[str, Any]] = []
    for inv_item in context.player_inventory:
        inv_item_id = str(inv_item.get("item_id", "")).strip()
        if not inv_item_id:
            continue
        catalog = context.item_catalog.get(inv_item_id, {})
        try:
            base_price = int(catalog.get("base_price", 0) or 0)
        except (TypeError, ValueError):
            base_price = 0
        player_sellable.append({
            "item_id": inv_item_id,
            "count": int(inv_item.get("count", 1)),
            "name": str(catalog.get("name", inv_item_id)),
            "type": str(catalog.get("type", "")),
            "base_price": base_price,
        })

    return {
        "npc_id": npc_id,
        "player_gold": context.player_gold,
        "stock": enriched_stock,
        "player_sellable_items": player_sellable,
        "last_refresh_tick": normalized_tick,
    }


def _resolve_quest_source_milestone(
    context: InteractionViewContext,
    quest_id: str,
    quest_map: Mapping[str, Any],
) -> str | None:
    raw_metadata = quest_map.get("metadata", {})
    metadata = raw_metadata if isinstance(raw_metadata, Mapping) else {}
    source_milestone = str(metadata.get("source_milestone", "")).strip()
    if source_milestone:
        return source_milestone

    target_milestone = str(quest_map.get("target_milestone", "")).strip()
    if target_milestone:
        return target_milestone

    board_metadata = context.board_quest_metadata.get(quest_id, {})
    if not isinstance(board_metadata, Mapping):
        board_metadata = {}
    return str(board_metadata.get("source_milestone", "")).strip() or None


def build_talk_snapshot_payload(
    context: InteractionViewContext,
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
    available_intents = ["talk", "greet"]
    if npc_id in context.shop_states:
        available_intents.extend(["browse", "buy", "sell", "inspect_item"])
    if context.dynamic_quests:
        available_intents.extend([
            "ask_quest", "ask_progress", "ask_location",
            "ask_requirements", "ask_reward",
        ])

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
        "available_intents": available_intents,
    }


def build_quest_brief_payload(
    context: InteractionViewContext,
    npc_id: str,
    quest_id: str,
) -> dict[str, Any]:
    """Return one read-only dynamic-quest brief for NPC ask_quest interactions."""

    quest_map = context.dynamic_quests.get(quest_id, {})
    source_milestone = _resolve_quest_source_milestone(context, quest_id, quest_map)
    return {
        "target_kind": "npc",
        "target_id": npc_id,
        "quest": {
            "quest_id": quest_id,
            "quest_kind": "dynamic",
            "status": str(quest_map.get("status", "")).strip(),
            "title": str(quest_map.get("title", "")),
            "summary": str(quest_map.get("summary", "")),
            "source_milestone": source_milestone,
        },
    }


def build_quest_progress_payload(
    context: InteractionViewContext,
    npc_id: str,
    quest_id: str,
) -> dict[str, Any]:
    """Return one read-only dynamic-quest status snapshot for NPC ask_progress."""

    quest_map = context.dynamic_quests.get(quest_id, {})
    status = str(quest_map.get("status", "")).strip()
    source_milestone = _resolve_quest_source_milestone(context, quest_id, quest_map)
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
            "source_milestone": source_milestone,
            "source_milestone_state": source_milestone_state,
        },
    }


def build_quest_location_payload(
    context: InteractionViewContext,
    npc_id: str,
    quest_id: str,
) -> dict[str, Any]:
    """Return one read-only dynamic-quest location hint for NPC ask_location."""

    quest_map = context.dynamic_quests.get(quest_id, {})
    status = str(quest_map.get("status", "")).strip()
    resolved_area_id = str(quest_map.get("area_id", "")).strip() or None
    resolved_location_id = str(quest_map.get("location_id", "")).strip() or None
    source_milestone = _resolve_quest_source_milestone(context, quest_id, quest_map)
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
            "source_milestone": source_milestone,
        },
    }


def build_quest_requirements_payload(
    context: InteractionViewContext,
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
    source_milestone = _resolve_quest_source_milestone(context, quest_id, quest_map)
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
            "source_milestone": source_milestone,
        },
    }


def build_quest_reward_payload(
    context: InteractionViewContext,
    npc_id: str,
    quest_id: str,
) -> dict[str, Any]:
    """Return one read-only dynamic-quest reward view for NPC ask_reward."""

    quest_map = context.dynamic_quests.get(quest_id, {})
    status = str(quest_map.get("status", "")).strip()
    source_milestone = _resolve_quest_source_milestone(context, quest_id, quest_map)
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
            "source_milestone": source_milestone,
            "reward_known": bool(gold is not None or items or reward_summary is not None),
            "gold": gold,
            "items": items,
            "reward_summary": reward_summary,
        },
    }


def build_inspect_item_payload(
    context: InteractionViewContext,
    npc_id: str,
    item_id: str,
) -> dict[str, Any]:
    """Return detailed item information from the item catalog."""

    catalog_entry = context.item_catalog.get(item_id, {})
    player_count = 0
    for inv_item in context.player_inventory:
        if inv_item.get("item_id") == item_id:
            player_count = int(inv_item.get("count", 0))
            break
    try:
        base_price = int(catalog_entry.get("base_price", 0) or 0)
    except (TypeError, ValueError):
        base_price = 0
    try:
        ac_bonus = int(catalog_entry.get("ac_bonus", 0) or 0)
    except (TypeError, ValueError):
        ac_bonus = 0
    return {
        "target_kind": "npc",
        "target_id": npc_id,
        "item": {
            "item_id": item_id,
            "name": str(catalog_entry.get("name", item_id)),
            "type": str(catalog_entry.get("type", "")),
            "rarity": str(catalog_entry.get("rarity", "")),
            "base_price": base_price,
            "slot": str(catalog_entry.get("slot", "")),
            "damage_dice": str(catalog_entry.get("damage_dice", "")),
            "damage_type": str(catalog_entry.get("damage_type", "")),
            "ac_bonus": ac_bonus,
            "player_owned_count": player_count,
        },
    }
