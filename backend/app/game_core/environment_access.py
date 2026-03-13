"""Helpers for resolving the player's currently reachable environment objects."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from app.game_core.content import WorldInstance
from app.game_core.location_utils import coerce_non_empty_string, scene_position
from app.game_core.scene_interactables import normalize_functional_binding
from app.game_core.state import StateContainer


@dataclass(slots=True)
class ResolvedInteractable:
    interactable_id: str
    area_id: str
    location_id: str
    room_id: str | None
    source: str
    name: str
    description: str
    tags: list[str]
    checks: list[Any]
    visibility_dc: int | None
    reward: dict[str, Any]
    one_time: bool
    functional: dict[str, Any]
    container_data: Any | None
    dynamic: bool
    overlay: bool
    raw: Any


def current_scene_position(state: StateContainer) -> tuple[str, str | None, str | None]:
    player = getattr(state, "player", None)
    if player is None:
        return "", None, None
    return scene_position(
        getattr(player, "current_area", None),
        getattr(player, "current_location", None),
        getattr(player, "current_room", None),
    )


def list_current_scene_interactables(
    state: StateContainer,
    world: WorldInstance,
) -> list[ResolvedInteractable]:
    area_id, location_id, room_id = current_scene_position(state)
    if not area_id or not location_id:
        return []

    area_slice = getattr(state, "areas", None)
    area_state = area_slice.areas.get(area_id) if area_slice is not None else None
    resolved: list[ResolvedInteractable] = []
    if room_id is not None:
        _append_entries(
            resolved,
            _room_interactables(world, area_state, area_id, location_id, room_id),
        )
        _append_entries(
            resolved,
            _overlay_interactables(area_slice, area_id, location_id, room_id),
        )
        return resolved
    _append_entries(
        resolved,
        _static_location_interactables(world, area_state, area_id, location_id),
    )
    _append_entries(
        resolved,
        _overlay_interactables(area_slice, area_id, location_id, None),
    )
    if not world.has_registry("maps") or _get_sub_location(world, area_id, location_id) is None:
        _append_entries(
            resolved,
            _dynamic_sub_area_interactables(area_state, area_id, location_id),
        )
    return resolved


def list_visible_scene_interactables(
    state: StateContainer,
    world: WorldInstance,
) -> list[ResolvedInteractable]:
    return [
        entry
        for entry in list_current_scene_interactables(state, world)
        if interactable_is_visible(state, entry)
    ]


def find_current_interactable(
    state: StateContainer,
    world: WorldInstance,
    interactable_id: str,
) -> tuple[ResolvedInteractable | None, bool]:
    target_id = coerce_non_empty_string(interactable_id)
    if target_id is None:
        return None, False
    for entry in list_current_scene_interactables(state, world):
        if entry.interactable_id != target_id:
            continue
        return entry, interactable_is_visible(state, entry)
    return None, False


def find_current_container(
    state: StateContainer,
    world: WorldInstance,
    container_id: str,
) -> tuple[ResolvedInteractable | None, bool]:
    entry, visible = find_current_interactable(state, world, container_id)
    if entry is None or entry.container_data is None:
        return None, False
    return entry, visible


def interactable_is_visible(state: StateContainer, entry: ResolvedInteractable) -> bool:
    if entry.visibility_dc is None:
        return True
    area_slice = getattr(state, "areas", None)
    if area_slice is None:
        return False
    return area_slice.is_discovery_found(entry.area_id, entry.interactable_id)


def room_display_entries(
    state: StateContainer,
    world: WorldInstance,
    area_id: str,
    location_id: str,
) -> list[dict[str, Any]]:
    area_slice = getattr(state, "areas", None)
    area_state = area_slice.areas.get(area_id) if area_slice is not None else None
    if not world.has_registry("maps"):
        return []
    area_template = world.maps.get(area_id)
    if area_template is None:
        return []

    rooms: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    sub_loc = area_template.sub_locations.get(location_id)
    if sub_loc is not None:
        for room_id, room_template in sub_loc.rooms.items():
            room_id_str = coerce_non_empty_string(room_id)
            if room_id_str is None:
                continue
            discoverable = bool(getattr(room_template, "discoverable", False))
            discovered = True
            if discoverable and area_slice is not None:
                discovered = area_slice.is_room_discovered(area_id, location_id, room_id_str)
            rooms.append({
                "id": room_id_str,
                "name": coerce_non_empty_string(getattr(room_template, "name", None)) or room_id_str,
                "discoverable": discoverable,
                "discovered": discovered,
            })
            seen_ids.add(room_id_str)

    if area_state is None:
        return rooms

    for raw_room in area_slice.list_dynamic_rooms(area_id, location_id):
        room_id = coerce_non_empty_string(raw_room.get("room_id"))
        if room_id is None or room_id in seen_ids:
            continue
        discoverable = bool(raw_room.get("discoverable"))
        discovered = True
        if discoverable:
            discovered = area_slice.is_room_discovered(area_id, location_id, room_id)
        rooms.append({
            "id": room_id,
            "name": coerce_non_empty_string(raw_room.get("name")) or room_id,
            "discoverable": discoverable,
            "discovered": discovered,
            "dynamic": True,
            "source": coerce_non_empty_string(raw_room.get("source")) or "runtime",
        })
    return rooms


def resolve_room_name(
    state: StateContainer,
    world: WorldInstance,
    area_id: str,
    location_id: str,
    room_id: str | None,
) -> str | None:
    if room_id is None or not world.has_registry("maps"):
        return None
    area_template = world.maps.get(area_id)
    if area_template is None:
        return None
    sub_loc = area_template.sub_locations.get(location_id)
    if sub_loc is not None:
        room_template = sub_loc.rooms.get(room_id)
        if room_template is not None:
            return coerce_non_empty_string(getattr(room_template, "name", None)) or room_id
    area_slice = getattr(state, "areas", None)
    if area_slice is not None:
        for raw_room in area_slice.list_dynamic_rooms(area_id, location_id):
            if coerce_non_empty_string(raw_room.get("room_id")) == room_id:
                return coerce_non_empty_string(raw_room.get("name")) or room_id
    return room_id


def _append_entries(
    resolved: list[ResolvedInteractable],
    incoming: list[ResolvedInteractable],
) -> None:
    for entry in incoming:
        existing_index = next(
            (
                index
                for index, existing in enumerate(resolved)
                if existing.interactable_id == entry.interactable_id
            ),
            None,
        )
        if existing_index is None:
            resolved.append(entry)
            continue
        existing = resolved[existing_index]
        if (
            entry.overlay
            and existing.location_id == entry.location_id
            and existing.room_id == entry.room_id
        ):
            resolved[existing_index] = _merge_entries(existing, entry)


def _merge_entries(
    base: ResolvedInteractable,
    overlay: ResolvedInteractable,
) -> ResolvedInteractable:
    return ResolvedInteractable(
        interactable_id=base.interactable_id,
        area_id=base.area_id,
        location_id=base.location_id,
        room_id=base.room_id,
        source=overlay.source or base.source,
        name=overlay.name or base.name,
        description=overlay.description or base.description,
        tags=list(overlay.tags or base.tags),
        checks=list(overlay.checks or base.checks),
        visibility_dc=(
            overlay.visibility_dc
            if overlay.visibility_dc is not None
            else base.visibility_dc
        ),
        reward=dict(overlay.reward or base.reward),
        one_time=overlay.one_time or base.one_time,
        functional=dict(overlay.functional or base.functional),
        container_data=(
            overlay.container_data
            if overlay.container_data is not None
            else base.container_data
        ),
        dynamic=base.dynamic or overlay.dynamic,
        overlay=base.overlay or overlay.overlay,
        raw=overlay.raw,
    )


def _static_location_interactables(
    world: WorldInstance,
    area_state: Any,
    area_id: str,
    location_id: str,
) -> list[ResolvedInteractable]:
    resolved: list[ResolvedInteractable] = []
    if world.has_registry("maps"):
        sub_loc = _get_sub_location(world, area_id, location_id)
        if sub_loc is not None:
            resolved.extend(
                _coerce_interactable_entries(
                    getattr(sub_loc, "interactables", []),
                    area_id=area_id,
                    location_id=location_id,
                    room_id=None,
                    source="sub_location",
                    dynamic=False,
                    overlay=False,
                )
            )
    return resolved


def _dynamic_sub_area_interactables(
    area_state: Any,
    area_id: str,
    location_id: str,
) -> list[ResolvedInteractable]:
    resolved: list[ResolvedInteractable] = []
    raw_sub_areas = getattr(area_state, "temporary_sub_areas", []) if area_state is not None else []
    for raw_sub_area in raw_sub_areas:
        if not isinstance(raw_sub_area, Mapping):
            continue
        if coerce_non_empty_string(raw_sub_area.get("id")) != location_id:
            continue
        resolved.extend(
            _coerce_interactable_entries(
                raw_sub_area.get("interactables") or [],
                area_id=area_id,
                location_id=location_id,
                room_id=None,
                source=coerce_non_empty_string(raw_sub_area.get("source")) or "dynamic_sub_area",
                dynamic=True,
                overlay=False,
            )
        )
        break
    return resolved


def _overlay_interactables(
    area_slice: Any,
    area_id: str,
    location_id: str,
    room_id: str | None,
) -> list[ResolvedInteractable]:
    if area_slice is None:
        return []
    return _coerce_interactable_entries(
        area_slice.list_scoped_interactable_overlays(area_id, location_id, room_id),
        area_id=area_id,
        location_id=location_id,
        room_id=room_id,
        source="overlay",
        dynamic=True,
        overlay=True,
    )


def _room_interactables(
    world: WorldInstance,
    area_state: Any,
    area_id: str,
    location_id: str,
    room_id: str,
) -> list[ResolvedInteractable]:
    resolved: list[ResolvedInteractable] = []
    if world.has_registry("maps"):
        sub_loc = _get_sub_location(world, area_id, location_id)
        if sub_loc is not None:
            room_template = sub_loc.rooms.get(room_id)
            if room_template is not None:
                resolved.extend(
                    _coerce_interactable_entries(
                        getattr(room_template, "interactables", []),
                        area_id=area_id,
                        location_id=location_id,
                        room_id=room_id,
                        source="room",
                        dynamic=False,
                        overlay=False,
                    )
                )

    dynamic_rooms = getattr(area_state, "dynamic_rooms", []) if area_state is not None else []
    for raw_room in dynamic_rooms:
        if not isinstance(raw_room, Mapping):
            continue
        if coerce_non_empty_string(raw_room.get("sub_loc_id")) != location_id:
            continue
        if coerce_non_empty_string(raw_room.get("room_id")) != room_id:
            continue
        resolved.extend(
            _coerce_interactable_entries(
                raw_room.get("interactables") or [],
                area_id=area_id,
                location_id=location_id,
                room_id=room_id,
                source=coerce_non_empty_string(raw_room.get("source")) or "dynamic_room",
                dynamic=True,
                overlay=False,
            )
        )
        break
    return resolved


def _coerce_interactable_entries(
    interactables: Iterable[Any],
    *,
    area_id: str,
    location_id: str,
    room_id: str | None,
    source: str,
    dynamic: bool,
    overlay: bool,
) -> list[ResolvedInteractable]:
    resolved: list[ResolvedInteractable] = []
    for raw in interactables:
        entry = _coerce_interactable(
            raw,
            area_id=area_id,
            location_id=location_id,
            room_id=room_id,
            source=source,
            dynamic=dynamic,
            overlay=overlay,
        )
        if entry is not None:
            resolved.append(entry)
    return resolved


def _coerce_interactable(
    raw: Any,
    *,
    area_id: str,
    location_id: str,
    room_id: str | None,
    source: str,
    dynamic: bool,
    overlay: bool,
) -> ResolvedInteractable | None:
    if isinstance(raw, Mapping):
        interactable_id = coerce_non_empty_string(raw.get("id"))
        if interactable_id is None:
            return None
        reward = raw.get("reward")
        checks = raw.get("checks")
        visibility_dc = _coerce_int(raw.get("visibility_dc"))
        return ResolvedInteractable(
            interactable_id=interactable_id,
            area_id=area_id,
            location_id=location_id,
            room_id=room_id,
            source=source,
            name=coerce_non_empty_string(raw.get("name")) or interactable_id,
            description=str(raw.get("description", "")),
            tags=_coerce_string_list(raw.get("tags")),
            checks=list(checks) if isinstance(checks, list) else [],
            visibility_dc=visibility_dc,
            reward=dict(reward) if isinstance(reward, Mapping) else {},
            one_time=bool(raw.get("one_time", False)),
            functional=normalize_functional_binding(raw.get("functional")),
            container_data=raw.get("container_data"),
            dynamic=dynamic,
            overlay=overlay,
            raw=dict(raw),
        )

    interactable_id = coerce_non_empty_string(getattr(raw, "id", None))
    if interactable_id is None:
        interactable_id = coerce_non_empty_string(raw)
        if interactable_id is None:
            return None
        return ResolvedInteractable(
            interactable_id=interactable_id,
            area_id=area_id,
            location_id=location_id,
            room_id=room_id,
            source=source,
            name=interactable_id,
            description="",
            tags=[],
            checks=[],
            visibility_dc=None,
            reward={},
            one_time=False,
            functional={},
            container_data=None,
            dynamic=dynamic,
            overlay=overlay,
            raw=raw,
        )

    reward = getattr(raw, "reward", None)
    return ResolvedInteractable(
        interactable_id=interactable_id,
        area_id=area_id,
        location_id=location_id,
        room_id=room_id,
        source=source,
        name=coerce_non_empty_string(getattr(raw, "name", None)) or interactable_id,
        description=str(getattr(raw, "description", "")),
        tags=_coerce_string_list(getattr(raw, "tags", [])),
        checks=list(getattr(raw, "checks", [])),
        visibility_dc=_coerce_int(getattr(raw, "visibility_dc", None)),
        reward=dict(reward) if isinstance(reward, Mapping) else {},
        one_time=bool(getattr(raw, "one_time", False)),
        functional=normalize_functional_binding(getattr(raw, "functional", None)),
        container_data=getattr(raw, "container_data", None),
        dynamic=dynamic,
        overlay=overlay,
        raw=raw,
    )


def _coerce_string_list(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if coerce_non_empty_string(item) is not None]


def _coerce_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _get_sub_location(world: WorldInstance, area_id: str, location_id: str) -> Any:
    maps = getattr(world, "maps", None)
    if maps is None:
        return None
    getter = getattr(maps, "get_sub_location", None)
    if callable(getter):
        return getter(area_id, location_id)
    area_template = maps.get(area_id) if callable(getattr(maps, "get", None)) else None
    sub_locations = getattr(area_template, "sub_locations", None)
    if isinstance(sub_locations, dict):
        return sub_locations.get(location_id)
    return None
