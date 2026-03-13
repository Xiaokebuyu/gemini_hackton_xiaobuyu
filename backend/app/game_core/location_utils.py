"""Shared helpers for area/location/room normalization and matching."""

from __future__ import annotations

from typing import Any, Mapping


def coerce_non_empty_string(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def normalize_location_params(raw: Mapping[str, Any]) -> dict[str, str]:
    """Normalize location aliases into canonical area/location/room keys."""
    area_id = coerce_non_empty_string(raw.get("area_id")) or coerce_non_empty_string(raw.get("area"))
    location_id = (
        coerce_non_empty_string(raw.get("location_id"))
        or coerce_non_empty_string(raw.get("location"))
        or coerce_non_empty_string(raw.get("sub_location"))
        or coerce_non_empty_string(raw.get("sub_location_id"))
    )
    room_id = coerce_non_empty_string(raw.get("room_id")) or coerce_non_empty_string(raw.get("room"))

    normalized: dict[str, str] = {}
    if area_id is not None:
        normalized["area_id"] = area_id
    if location_id is not None:
        normalized["location_id"] = location_id
    if room_id is not None:
        normalized["room_id"] = room_id
    return normalized


def normalize_condition_mapping(raw_condition: Mapping[str, Any]) -> dict[str, Any]:
    """Return a canonical trigger/condition mapping with normalized location keys."""
    normalized = {str(key): value for key, value in raw_condition.items()}
    raw_params = normalized.get("params")
    params = {str(key): value for key, value in raw_params.items()} if isinstance(raw_params, Mapping) else {}
    params.update({key: value for key, value in normalized.items() if key != "params"})

    condition_type = coerce_non_empty_string(params.get("type")) or ""
    if condition_type not in {"location_entered", "location_visited"}:
        return normalized

    merged = normalize_location_params(params)
    payload: dict[str, Any] = {"type": condition_type, **merged}
    return payload


def validate_location_params(
    raw: Mapping[str, Any],
    *,
    require_target: bool = True,
) -> tuple[dict[str, str], str | None]:
    normalized = normalize_location_params(raw)
    if require_target and "area_id" not in normalized and "location_id" not in normalized:
        return normalized, "location condition requires area_id or location_id"
    if "room_id" in normalized and "location_id" not in normalized:
        return normalized, "room_id requires location_id/sub_location"
    return normalized, None


def build_visited_area_flag(area_id: str) -> str:
    return f"visited_area_{area_id}"


def build_visited_location_flag(area_id: str, location_id: str) -> str:
    return f"visited_location_{area_id}__{location_id}"


def build_visited_room_flag(area_id: str, location_id: str, room_id: str) -> str:
    return f"visited_room_{area_id}__{location_id}__{room_id}"


def scene_position(
    area_id: Any,
    location_id: Any = None,
    room_id: Any = None,
) -> tuple[str, str | None, str | None]:
    return (
        coerce_non_empty_string(area_id) or "",
        coerce_non_empty_string(location_id),
        coerce_non_empty_string(room_id),
    )


def scene_position_matches(
    current_area: str | None,
    current_location: str | None,
    current_room: str | None,
    *,
    area_id: str | None = None,
    location_id: str | None = None,
    room_id: str | None = None,
) -> bool:
    if area_id is not None and (current_area or "") != area_id:
        return False
    if location_id is not None and current_location != location_id:
        return False
    if room_id is not None and current_room != room_id:
        return False
    return True


def location_condition_met(
    condition_type: str,
    raw_params: Mapping[str, Any],
    *,
    current_area: str | None,
    current_location: str | None,
    current_room: str | None,
    current_flags: Mapping[str, Any] | None = None,
) -> bool:
    params, error = validate_location_params(raw_params)
    if error is not None:
        return False

    area_id = params.get("area_id")
    location_id = params.get("location_id")
    room_id = params.get("room_id")

    realtime_match = scene_position_matches(
        current_area,
        current_location,
        current_room,
        area_id=area_id,
        location_id=location_id,
        room_id=room_id,
    )
    if condition_type == "location_entered":
        return realtime_match

    if condition_type != "location_visited":
        return False
    if realtime_match:
        return True

    flags = current_flags or {}
    if room_id is not None and location_id is not None:
        if area_id is not None:
            return flags.get(build_visited_room_flag(area_id, location_id, room_id)) is True
        suffix = f"__{location_id}__{room_id}"
        return any(
            str(key).startswith("visited_room_") and str(key).endswith(suffix) and value is True
            for key, value in flags.items()
        )

    if location_id is not None:
        if area_id is not None:
            return flags.get(build_visited_location_flag(area_id, location_id)) is True
        suffix = f"__{location_id}"
        return any(
            str(key).startswith("visited_location_") and str(key).endswith(suffix) and value is True
            for key, value in flags.items()
        )

    if area_id is None:
        return False
    return flags.get(build_visited_area_flag(area_id)) is True
