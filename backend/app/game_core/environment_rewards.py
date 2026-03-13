"""Reward application helpers for environmental discoveries and interactables."""

from __future__ import annotations

from typing import Any, Mapping

from app.game_core.location_utils import coerce_non_empty_string
from app.game_core.state import StateChange, StateContainer


def normalize_environment_reward(reward: Any) -> dict[str, Any]:
    if isinstance(reward, Mapping):
        return {str(key): value for key, value in reward.items()}
    return {}


def apply_environment_reward(
    state: StateContainer,
    reward: Any,
    *,
    area_id: str,
    source: str,
) -> tuple[list[StateChange], dict[str, Any]]:
    normalized = normalize_environment_reward(reward)
    reward_type = coerce_non_empty_string(normalized.get("type"))
    if reward_type is None:
        return [], {}

    if reward_type == "item":
        return _apply_item_reward(state, normalized)
    if reward_type == "sub_location":
        return _apply_sub_location_reward(state, normalized, area_id=area_id, source=source)
    if reward_type in {"quest_hook", "info"}:
        return [], {"pending": [{"type": reward_type, **normalized}]}
    return [], {"pending": [{"type": reward_type, **normalized}]}


def _apply_item_reward(
    state: StateContainer,
    reward: Mapping[str, Any],
) -> tuple[list[StateChange], dict[str, Any]]:
    if not state.has_slice("player"):
        return [], {}

    item_id = (
        coerce_non_empty_string(reward.get("item_id"))
        or coerce_non_empty_string(reward.get("id"))
    )
    if item_id is None:
        return [], {}

    count_raw = reward.get("count", reward.get("quantity", 1))
    try:
        count = max(1, int(count_raw))
    except (TypeError, ValueError):
        count = 1

    inventory = state.player.snapshot().get("inventory", [])
    if not isinstance(inventory, list):
        inventory = []
    updated_inventory: list[dict[str, Any]] = [
        dict(entry) for entry in inventory if isinstance(entry, Mapping)
    ]
    for entry in updated_inventory:
        if entry.get("item_id") != item_id:
            continue
        entry["count"] = int(entry.get("count", 0)) + count
        break
    else:
        updated_inventory.append({"item_id": item_id, "count": count, "tags": []})

    return [
        StateChange("player", "set", "inventory", updated_inventory),
    ], {
        "applied": [{"type": "item", "item_id": item_id, "count": count}],
    }


def _apply_sub_location_reward(
    state: StateContainer,
    reward: Mapping[str, Any],
    *,
    area_id: str,
    source: str,
) -> tuple[list[StateChange], dict[str, Any]]:
    if not area_id or not state.has_slice("areas"):
        return [], {}

    sub_location_id = (
        coerce_non_empty_string(reward.get("sub_location_id"))
        or coerce_non_empty_string(reward.get("id"))
    )
    if sub_location_id is None:
        return [], {}

    existing = state.areas.list_temporary_sub_areas(area_id)
    if any(coerce_non_empty_string(item.get("id")) == sub_location_id for item in existing):
        return [], {
            "applied": [{"type": "sub_location", "sub_location_id": sub_location_id, "created": False}],
        }

    label = (
        coerce_non_empty_string(reward.get("label"))
        or coerce_non_empty_string(reward.get("name"))
        or sub_location_id
    )
    payload = {
        "id": sub_location_id,
        "label": label,
        "description": str(reward.get("description", "")),
        "type": coerce_non_empty_string(reward.get("sub_location_type")) or "visit",
        "tier": "permanent",
        "expiry": -1,
        "temporary": False,
        "source": coerce_non_empty_string(reward.get("source")) or source,
        "status": "active",
    }
    updated = [dict(item) for item in existing]
    updated.append(payload)
    return [
        StateChange("areas", "set", f"{area_id}.temporary_sub_areas", updated),
    ], {
        "applied": [{"type": "sub_location", "sub_location_id": sub_location_id, "created": True}],
    }
