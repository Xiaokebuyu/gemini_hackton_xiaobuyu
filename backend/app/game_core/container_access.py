"""Container reachability and lazy state helpers."""

from __future__ import annotations

from typing import Any, Mapping

from app.game_core.environment_access import ResolvedInteractable, find_current_container
from app.game_core.rules.handler_utils import roll_damage_dice
from app.game_core.state import StateChange, StateContainer


def find_accessible_container(
    state: StateContainer,
    world: Any,
    container_id: str,
) -> tuple[ResolvedInteractable | None, str | None]:
    entry, visible = find_current_container(state, world, container_id)
    if entry is None:
        return None, "unknown_container"
    if not visible:
        return None, "container_not_revealed"
    return entry, None


def ensure_container_state(
    state: StateContainer,
    entry: ResolvedInteractable,
) -> tuple[dict[str, Any], list[StateChange]]:
    existing = state.areas.get_container_state(entry.area_id, entry.interactable_id)
    if existing is not None:
        existing["area_id"] = existing.get("area_id") or entry.area_id
        existing["location_id"] = existing.get("location_id") or entry.location_id
        existing["room_id"] = existing.get("room_id") or entry.room_id
        existing["source"] = existing.get("source") or entry.source
        return existing, []

    initial_state = build_container_state(entry)
    return initial_state, [
        StateChange("areas", "set", f"container_states.{entry.interactable_id}", initial_state),
    ]


def initialize_container_loot(container_state: Mapping[str, Any]) -> dict[str, Any]:
    if bool(container_state.get("loot_initialized", False)):
        return dict(container_state)

    updated = dict(container_state)
    loot = updated.get("loot")
    updated["remaining_gold"] = _roll_gold(loot)
    updated["remaining_items"] = _materialize_items(loot)
    updated["loot_initialized"] = True
    return updated


def build_container_state(entry: ResolvedInteractable) -> dict[str, Any]:
    container_data = entry.container_data
    locked = _get_bool(container_data, "locked")
    trap_payload = _trap_payload(container_data)
    loot_payload = _loot_payload(container_data)
    return {
        "area_id": entry.area_id,
        "location_id": entry.location_id,
        "room_id": entry.room_id,
        "source": entry.source,
        "opened": False,
        "looted": False,
        "loot_initialized": False,
        "lock_status": "locked" if locked else "unlocked",
        "remaining_items": [],
        "remaining_gold": 0,
        "loot": loot_payload,
        "trap_status": "armed" if trap_payload is not None else "disarmed",
        "trap_detected": False,
        "trap": trap_payload,
    }


def _trap_payload(container_data: Any) -> dict[str, Any] | None:
    trap = _get_nested(container_data, "trap")
    if trap is None:
        return None
    return {
        "detect_dc": _get_int(trap, "detect_dc", 15),
        "disarm_dc": _get_int(trap, "disarm_dc", 15),
        "damage": _get_string(trap, "damage", "1d6"),
        "damage_type": _get_string(trap, "damage_type", "piercing"),
        "effect": _get_optional_string(trap, "effect"),
    }


def _loot_payload(container_data: Any) -> dict[str, Any]:
    loot = _get_nested(container_data, "loot")
    if loot is None:
        return {"gold": "0", "items": []}
    items = _get_nested(loot, "items")
    if not isinstance(items, list):
        items = []
    return {
        "gold": _get_string(loot, "gold", "0"),
        "items": list(items),
    }


def _roll_gold(loot: Any) -> int:
    if not isinstance(loot, Mapping):
        return 0
    raw_gold = loot.get("gold")
    if raw_gold is None:
        return 0
    if isinstance(raw_gold, str):
        normalized = raw_gold.strip()
        if not normalized:
            return 0
        try:
            return max(0, int(normalized))
        except ValueError:
            return max(0, roll_damage_dice(normalized))
    try:
        return max(0, int(raw_gold))
    except (TypeError, ValueError):
        return 0


def _materialize_items(loot: Any) -> list[dict[str, Any]]:
    if not isinstance(loot, Mapping):
        return []
    raw_items = loot.get("items")
    if not isinstance(raw_items, list):
        return []
    materialized: list[dict[str, Any]] = []
    for raw_item in raw_items:
        if isinstance(raw_item, Mapping):
            item_id = _get_optional_string(raw_item, "item_id") or _get_optional_string(raw_item, "id")
            if item_id is None:
                continue
            try:
                count = max(1, int(raw_item.get("count", raw_item.get("quantity", 1))))
            except (TypeError, ValueError):
                count = 1
            materialized.append({
                "item_id": item_id,
                "count": count,
                "tags": [str(tag) for tag in raw_item.get("tags", [])] if isinstance(raw_item.get("tags"), list) else [],
            })
            continue
        item_id = _get_optional_string(raw_item)
        if item_id is None:
            continue
        materialized.append({"item_id": item_id, "count": 1, "tags": []})
    return materialized


def _get_nested(value: Any, key: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(key)
    return getattr(value, key, None)


def _get_bool(value: Any, key: str) -> bool:
    nested = _get_nested(value, key)
    return bool(nested)


def _get_string(value: Any, key: str, default: str) -> str:
    nested = _get_nested(value, key)
    if nested is None:
        return default
    normalized = str(nested).strip()
    return normalized or default


def _get_optional_string(value: Any, key: str | None = None) -> str | None:
    nested = _get_nested(value, key) if key is not None else value
    if nested is None:
        return None
    normalized = str(nested).strip()
    return normalized or None


def _get_int(value: Any, key: str, default: int) -> int:
    nested = _get_nested(value, key)
    if nested is None:
        return default
    try:
        return int(nested)
    except (TypeError, ValueError):
        return default
