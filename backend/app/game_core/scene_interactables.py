"""Shared helpers for scene-level interactable semantics and migrations."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping


_FRONTIER_TOWN_DUPLICATE_FACILITY_MAP: dict[str, dict[str, str | None]] = {
    "adventurer_notice_board": {
        "location_id": "adventurer_guild",
        "room_id": "guild_counter",
        "interactable_id": "quest_board",
    },
    "quest_board_slips": {
        "location_id": "adventurer_guild",
        "room_id": "guild_counter",
        "interactable_id": "quest_board",
    },
    "guild_main_counter": {
        "location_id": "adventurer_guild",
        "room_id": "guild_counter",
        "interactable_id": None,
    },
    "guild_tavern_corner": {
        "location_id": "adventurer_guild",
        "room_id": "guild_hall",
        "interactable_id": None,
    },
    "charity_box": {
        "location_id": "mother_earth_temple",
        "room_id": "prayer_hall",
        "interactable_id": "charity_box",
    },
    "donation_slot": {
        "location_id": "mother_earth_temple",
        "room_id": "prayer_hall",
        "interactable_id": "charity_box",
    },
    "herbal_drying_rack": {
        "location_id": "mother_earth_temple",
        "room_id": "herb_garden",
        "interactable_id": None,
    },
    "healing_fountain": {
        "location_id": "mother_earth_temple",
        "room_id": "herb_garden",
        "interactable_id": None,
    },
    "temple_meditation_hall": {
        "location_id": "mother_earth_temple",
        "room_id": "prayer_hall",
        "interactable_id": None,
    },
}


def normalize_functional_binding(raw: Any) -> dict[str, Any]:
    """Normalize one interactable functional binding."""

    if not isinstance(raw, Mapping):
        return {}
    functional_type = str(raw.get("type", "")).strip()
    action_type = str(raw.get("action_type", "")).strip()
    if not functional_type and not action_type:
        return {}
    normalized: dict[str, Any] = {}
    if functional_type:
        normalized["type"] = functional_type
    if action_type:
        normalized["action_type"] = action_type
    raw_params = raw.get("params")
    if isinstance(raw_params, Mapping) and raw_params:
        normalized["params"] = {
            str(key): value
            for key, value in raw_params.items()
            if str(key).strip()
        }
    return normalized


def functional_type(raw: Any) -> str | None:
    normalized = normalize_functional_binding(raw)
    resolved = str(normalized.get("type", "")).strip()
    return resolved or None


def classify_interaction_kind(
    *,
    functional: Any,
    is_container: bool = False,
) -> str:
    kind = functional_type(functional)
    if kind == "board_browse":
        return "board"
    if kind == "donation":
        return "donation"
    if is_container:
        return "container"
    return "generic"


def build_primary_action(
    interactable_id: str,
    *,
    functional: Any,
) -> dict[str, Any]:
    """Resolve the primary UI action for an interactable."""

    normalized = normalize_functional_binding(functional)
    resolved_type = str(normalized.get("type", "")).strip()
    explicit_action_type = str(normalized.get("action_type", "")).strip()
    params = deepcopy(normalized.get("params", {}))
    if not isinstance(params, dict):
        params = {}
    if explicit_action_type:
        return {"action_type": explicit_action_type, "params": params}
    if resolved_type == "board_browse":
        params.setdefault("board_id", interactable_id)
        return {"action_type": "browse_board", "params": params}
    if resolved_type == "donation":
        params.setdefault("target_kind", "interactable")
        params.setdefault("target_id", interactable_id)
        return {"action_type": "donate", "params": params}
    if resolved_type == "investigate_clue":
        params.setdefault("interactable_id", interactable_id)
        return {"action_type": "investigate_clue", "params": params}
    return {
        "action_type": "interact_object_v2",
        "params": {"interactable_id": interactable_id},
    }


def duplicate_facility_target(
    area_id: str,
    duplicate_id: str,
) -> dict[str, str | None] | None:
    if area_id != "frontier_town":
        return None
    return _FRONTIER_TOWN_DUPLICATE_FACILITY_MAP.get(duplicate_id)


def duplicate_facility_aliases(area_id: str) -> set[str]:
    if area_id != "frontier_town":
        return set()
    return set(_FRONTIER_TOWN_DUPLICATE_FACILITY_MAP)


def canonical_facility_ids(area_id: str) -> set[str]:
    if area_id != "frontier_town":
        return set()
    ids = {
        "quest_board",
        "charity_box",
        "guild_counter",
        "guild_hall",
        "prayer_hall",
        "herb_garden",
    }
    ids.update(duplicate_facility_aliases(area_id))
    return ids
