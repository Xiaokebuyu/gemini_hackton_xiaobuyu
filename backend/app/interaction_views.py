"""Shared interaction-view payload builders for the API shell."""

from __future__ import annotations

from typing import Any, Mapping, TYPE_CHECKING

if TYPE_CHECKING:
    from app.interaction_service import InteractionViewContext


_INTENT_ORDER = (
    "talk",
    "greet",
    "donate",
    "browse",
    "buy",
    "sell",
    "buy_service",
    "inspect_item",
    "ask_quest",
    "ask_progress",
    "ask_location",
    "ask_requirements",
    "ask_reward",
    "accept_quest",
    "report_quest",
)

_REFRESH_MODE_PRIORITY = ("daily", "long_rest", "rest")


def _normalize_refresh_modes(raw_refresh: Any) -> list[str]:
    if isinstance(raw_refresh, str):
        mode = raw_refresh.strip()
        return [mode] if mode else []
    if isinstance(raw_refresh, list):
        return [
            str(entry).strip()
            for entry in raw_refresh
            if str(entry).strip()
        ]
    return []


def _resolve_primary_refresh_mode(modes: list[str]) -> str:
    lowered = {mode.strip().lower(): mode.strip().lower() for mode in modes if mode.strip()}
    for preferred in _REFRESH_MODE_PRIORITY:
        if preferred in lowered:
            return preferred
    if modes:
        return str(modes[0]).strip().lower() or "manual"
    return "manual"


def _build_refresh_hint(
    *,
    mode: str,
    auto_refresh: bool,
    current_day: int,
) -> str | None:
    if auto_refresh and mode == "daily":
        return f"Refreshes automatically when day {current_day + 1} begins."
    if mode == "long_rest":
        return "Configured to refresh after a long rest."
    if mode == "rest":
        return "Configured to refresh after rest."
    if mode == "manual":
        return None
    return "Refreshes from system-driven shop events."


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
    refresh_modes = _normalize_refresh_modes(shop_state.get("refresh_on"))
    if not refresh_modes:
        refresh_modes = list(context.npc_refresh_modes.get(npc_id, []))
    primary_refresh_mode = _resolve_primary_refresh_mode(refresh_modes)
    auto_refresh = "daily" in {mode.lower() for mode in refresh_modes}
    refresh_policy = {
        "mode": primary_refresh_mode,
        "configured_modes": [mode.lower() for mode in refresh_modes],
        "auto_refresh": auto_refresh,
        "last_refresh_tick": normalized_tick,
    }
    next_refresh_hint = _build_refresh_hint(
        mode=primary_refresh_mode,
        auto_refresh=auto_refresh,
        current_day=context.current_day,
    )

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

    # Build services list (excludes "donation" — separate donate intent).
    raw_services = context.npc_services.get(npc_id, [])
    services: list[dict[str, Any]] = [
        {
            "service_id": str(svc.get("service_id", "")),
            "label": str(svc.get("label", svc.get("service_id", ""))),
            "price": int(svc.get("price", 0) or 0),
            "notes": str(svc.get("notes", "")),
            "type": "service",
            "effects_summary": _build_effects_summary(svc.get("effects", [])),
        }
        for svc in raw_services
        if isinstance(svc, dict) and svc.get("service_id")
    ]

    return {
        "npc_id": npc_id,
        "player_gold": context.player_gold,
        "stock": enriched_stock,
        "player_sellable_items": player_sellable,
        "services": services,
        "last_refresh_tick": normalized_tick,
        "refresh_policy": refresh_policy,
        "next_refresh_hint": next_refresh_hint,
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
    available_intents = {"talk", "greet"}
    shop_state = context.shop_states.get(npc_id, {})
    if isinstance(shop_state, dict) and shop_state.get("current_stock"):
        available_intents.update({"browse", "buy", "sell", "inspect_item"})
    if "donation" in context.npc_service_ids.get(npc_id, []):
        available_intents.add("donate")
    if context.npc_services.get(npc_id):
        available_intents.add("browse")
        available_intents.add("buy_service")
    if context.dynamic_quest_views:
        available_intents.update({
            "ask_quest", "ask_progress", "ask_location",
            "ask_requirements", "ask_reward",
        })
    if "receptionist" in context.npc_tags.get(npc_id, frozenset()):
        if any(
            str(quest.get("status", "")).strip() == "available"
            for quest in context.dynamic_quest_views.values()
            if isinstance(quest, Mapping)
        ):
            available_intents.add("accept_quest")
        if any(
            bool(quest.get("can_report"))
            for quest in context.dynamic_quest_views.values()
            if isinstance(quest, Mapping)
        ):
            available_intents.add("report_quest")

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
        "available_intents": _ordered_available_intents(available_intents),
    }


def build_quest_brief_payload(
    context: InteractionViewContext,
    npc_id: str,
    quest_id: str,
) -> dict[str, Any]:
    """Return one read-only dynamic-quest brief for NPC ask_quest interactions."""

    quest_map = _get_dynamic_quest_view(context, quest_id)
    source_milestone = _resolve_quest_source_milestone(context, quest_id, quest_map)
    return {
        "target_kind": "npc",
        "target_id": npc_id,
        "quest": {
            "quest_id": quest_id,
            "quest_kind": "dynamic",
            "status": str(quest_map.get("status", "")).strip(),
            "ui_state": str(quest_map.get("ui_state", "")),
            "badge": _copy_badge(quest_map.get("badge")),
            "title": str(quest_map.get("title", "")),
            "summary": str(quest_map.get("summary", "")),
            "source_milestone": source_milestone,
            "requires_report": bool(quest_map.get("requires_report")),
            "reported": bool(quest_map.get("reported")),
            "can_report": bool(quest_map.get("can_report")),
        },
    }


def build_quest_progress_payload(
    context: InteractionViewContext,
    npc_id: str,
    quest_id: str,
) -> dict[str, Any]:
    """Return one read-only dynamic-quest status snapshot for NPC ask_progress."""

    quest_map = _get_dynamic_quest_view(context, quest_id)
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
            "ui_state": str(quest_map.get("ui_state", "")),
            "badge": _copy_badge(quest_map.get("badge")),
            "title": str(quest_map.get("title", "")),
            "summary": str(quest_map.get("summary", "")),
            "can_accept": status == "available",
            "is_active": status == "active",
            "is_closed": status in {"completed", "failed", "retired"},
            "requires_report": bool(quest_map.get("requires_report")),
            "reported": bool(quest_map.get("reported")),
            "can_report": bool(quest_map.get("can_report")),
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

    quest_map = _get_dynamic_quest_view(context, quest_id)
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
            "ui_state": str(quest_map.get("ui_state", "")),
            "badge": _copy_badge(quest_map.get("badge")),
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

    quest_map = _get_dynamic_quest_view(context, quest_id)
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
            "ui_state": str(quest_map.get("ui_state", "")),
            "badge": _copy_badge(quest_map.get("badge")),
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

    quest_map = _get_dynamic_quest_view(context, quest_id)
    status = str(quest_map.get("status", "")).strip()
    source_milestone = _resolve_quest_source_milestone(context, quest_id, quest_map)
    raw_rewards = quest_map.get("rewards", {})
    rewards = raw_rewards if isinstance(raw_rewards, Mapping) else {}
    gold = rewards.get("gold")
    xp = rewards.get("xp")
    items = _enrich_reward_items(rewards.get("items", []), context.item_catalog)
    return {
        "target_kind": "npc",
        "target_id": npc_id,
        "quest": {
            "quest_id": quest_id,
            "quest_kind": "dynamic",
            "status": status,
            "ui_state": str(quest_map.get("ui_state", "")),
            "badge": _copy_badge(quest_map.get("badge")),
            "source_milestone": source_milestone,
            "reward_known": bool(gold is not None or xp is not None or items),
            "gold": gold,
            "xp": xp,
            "items": items,
        },
    }


def _build_effects_summary(effects: Any) -> str:
    """Convert a list of effect atoms into a human-readable summary string."""
    if not isinstance(effects, list) or not effects:
        return ""
    parts: list[str] = []
    for atom in effects:
        if not isinstance(atom, dict):
            continue
        atom_type = str(atom.get("type", ""))
        if atom_type == "restore_hp":
            amount = int(atom.get("amount", 0) or 0)
            parts.append(f"恢复{amount}点HP")
        elif atom_type == "modify_gold":
            amount = int(atom.get("amount", 0) or 0)
            if amount >= 0:
                parts.append(f"获得{amount}金币")
            else:
                parts.append(f"消耗{-amount}金币")
        elif atom_type == "grant_item":
            item_id = str(atom.get("item_id", ""))
            qty = int(atom.get("count", 1) or 1)
            parts.append(f"获得{item_id}×{qty}")
        elif atom_type == "remove_item":
            item_id = str(atom.get("item_id", ""))
            qty = int(atom.get("count", 1) or 1)
            parts.append(f"移除{item_id}×{qty}")
        elif atom_type == "apply_effect":
            effect_id = str(atom.get("effect_id", ""))
            duration = atom.get("duration_ticks")
            if duration is not None:
                parts.append(f"获得{effect_id}（{duration}回合）")
            else:
                parts.append(f"获得{effect_id}")
        elif atom_type == "remove_effect":
            effect_id = str(atom.get("effect_id", ""))
            parts.append(f"移除{effect_id}")
        elif atom_type == "add_xp":
            amount = int(atom.get("amount", 0) or 0)
            parts.append(f"获得{amount}经验")
        elif atom_type == "add_knowledge":
            parts.append("获得知识")
    return "；".join(parts) if parts else ""


def _ordered_available_intents(enabled_intents: set[str]) -> list[str]:
    return [intent for intent in _INTENT_ORDER if intent in enabled_intents]


def _get_dynamic_quest_view(
    context: InteractionViewContext,
    quest_id: str,
) -> Mapping[str, Any]:
    quest_view = context.dynamic_quest_views.get(quest_id, {})
    return quest_view if isinstance(quest_view, Mapping) else {}


def _copy_badge(raw_badge: Any) -> dict[str, str]:
    badge = raw_badge if isinstance(raw_badge, Mapping) else {}
    return {
        "key": str(badge.get("key", "")).strip(),
        "label": str(badge.get("label", "")).strip(),
    }


def _enrich_reward_items(
    raw_items: Any,
    item_catalog: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(raw_items, list):
        return []
    enriched: list[dict[str, Any]] = []
    for raw_item in raw_items:
        if not isinstance(raw_item, Mapping):
            continue
        item_id = str(raw_item.get("item_id", "")).strip()
        if not item_id:
            continue
        item_view = dict(raw_item)
        catalog_entry = item_catalog.get(item_id, {})
        item_view["name"] = str(catalog_entry.get("name", item_id))
        item_view["type"] = str(catalog_entry.get("type", ""))
        item_view["rarity"] = str(catalog_entry.get("rarity", ""))
        enriched.append(item_view)
    return enriched


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
