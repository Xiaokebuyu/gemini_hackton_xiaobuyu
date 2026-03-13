"""NPC presence helpers — single source of truth for area/location queries.

These are pure stateless functions that consolidate two previously divergent
NPC-location resolution paths (scene_views.py and interaction.py).

Design ref: P11 Block A — NPC Presence 单一真相源
"""

from __future__ import annotations

from typing import Mapping

from app.game_core.content import WorldInstance
from app.game_core.state import StateContainer


def get_area_npcs(
    state: StateContainer,
    world: WorldInstance,
    area_id: str,
) -> dict[str, str | None]:
    """Return all NPCs present in *area_id* and their sub-location.

    The returned dict maps ``npc_id → sub_location_id`` where the sub-location
    is ``None`` when the NPC occupies the area's main scene (no sub-location).

    Primary source: ``state.areas.areas[area_id].npc_locations``
    Fallback: CharacterRegistry entries whose ``area_id`` or ``current_area``
    matches *area_id* but are not yet tracked in AreaSlice.  This ensures
    that static NPCs defined only in content (never moved at runtime) are
    visible in both scene_views and interaction validation.

    Only NPCs in the *requested* area are returned — other areas are ignored.
    """
    normalized_area = area_id.strip()
    result: dict[str, str | None] = {}

    # Primary: AreaSlice runtime positions
    area_state = state.areas.areas.get(normalized_area)
    if area_state is not None:
        for raw_npc_id, raw_loc in area_state.npc_locations.items():
            npc_id = str(raw_npc_id).strip()
            if not npc_id:
                continue
            loc_str = str(raw_loc).strip() if isinstance(raw_loc, str) else ""
            result[npc_id] = loc_str or None

    # Secondary: static map resident placements.
    if world.has_registry("maps"):
        area_template = world.maps.get(normalized_area)
        if area_template is not None:
            for raw_sub_id, sub_template in area_template.sub_locations.items():
                sub_id = str(raw_sub_id).strip()
                if not sub_id:
                    continue
                for raw_npc_id in getattr(sub_template, "resident_npcs", []):
                    npc_id = str(raw_npc_id).strip()
                    if npc_id and npc_id not in result:
                        result[npc_id] = sub_id
                for room_template in getattr(sub_template, "rooms", {}).values():
                    for raw_npc_id in getattr(room_template, "resident_npcs", []):
                        npc_id = str(raw_npc_id).strip()
                        if npc_id and npc_id not in result:
                            result[npc_id] = sub_id

    # Fallback: CharacterRegistry static positions (not yet in AreaSlice)
    if world.has_registry("characters"):
        for template in world.characters.list_all():
            npc_id = template.id.strip()
            if not npc_id or npc_id in result:
                continue
            # Resolve the character's declared area
            resolved_area = ""
            for field_name in ("area_id", "current_area"):
                raw_val = str(getattr(template, field_name, "") or "").strip()
                if raw_val:
                    resolved_area = raw_val
                    break
            if resolved_area != normalized_area:
                continue
            # Resolve the character's declared sub-location
            resolved_loc = ""
            for field_name in ("location_id", "current_location"):
                raw_val = str(getattr(template, field_name, "") or "").strip()
                if raw_val:
                    resolved_loc = raw_val
                    break
            result[npc_id] = resolved_loc or None

    return result


def get_npc_room(
    state: StateContainer,
    world: WorldInstance,
    area_id: str,
    npc_id: str,
) -> str | None:
    """Resolve the room for *npc_id* within *area_id*.

    Runtime AreaSlice placement wins. Static map resident room assignments are
    used as a fallback so room-level co-location remains stable in tests and
    content-only scenes before runtime initialization.
    """
    normalized_area = area_id.strip()
    normalized_npc = npc_id.strip()
    if not normalized_area or not normalized_npc:
        return None

    if hasattr(state, "areas"):
        area_state = state.areas.areas.get(normalized_area)
        if area_state is not None:
            runtime_room = str(area_state.npc_rooms.get(normalized_npc) or "").strip()
            if runtime_room:
                return runtime_room
    location_id = get_area_npcs(state, world, normalized_area).get(normalized_npc)
    if location_id is None:
        return None
    static_room = _static_room_for_npc(world, normalized_area, location_id, normalized_npc)
    if static_room is not None:
        return static_room
    return get_default_room(world, normalized_area, location_id)


def get_area_npc_sources(
    state: StateContainer,
    area_id: str,
) -> dict[str, str]:
    """Return the presence_source for each NPC in *area_id*.

    Only covers NPCs tracked in AreaSlice.npc_presence_sources.  NPCs that
    arrived via the CharacterRegistry static fallback in get_area_npcs() are
    treated as ``"resident"`` by callers (not returned here, since they have
    no runtime source entry).

    Returns an empty dict when the area has no tracked source data.
    """
    normalized_area = area_id.strip()
    area_state = state.areas.areas.get(normalized_area)
    if area_state is None:
        return {}
    return dict(area_state.npc_presence_sources)


def get_default_room(
    world: WorldInstance,
    area_id: str,
    location_id: str | None,
) -> str | None:
    """Return the sub-location's default room when it exists."""
    normalized_area = area_id.strip()
    normalized_location = (location_id or "").strip()
    if not normalized_area or not normalized_location or not world.has_registry("maps"):
        return None
    sub_location = world.maps.get_sub_location(normalized_area, normalized_location)
    if sub_location is None:
        return None
    default_room = str(getattr(sub_location, "default_room", "") or "").strip()
    return default_room or None


def room_exists(
    state: StateContainer,
    world: WorldInstance,
    area_id: str,
    location_id: str | None,
    room_id: str | None,
) -> bool:
    """Return True when *room_id* exists under the target location.

    Both static map rooms and planner-created dynamic rooms are treated as
    valid targets.
    """
    normalized_area = area_id.strip()
    normalized_location = (location_id or "").strip()
    normalized_room = (room_id or "").strip()
    if not normalized_area or not normalized_location or not normalized_room:
        return False

    if world.has_registry("maps"):
        sub_location = world.maps.get_sub_location(normalized_area, normalized_location)
        if sub_location is not None and normalized_room in getattr(sub_location, "rooms", {}):
            return True

    if not hasattr(state, "areas"):
        return False
    area_state = state.areas.areas.get(normalized_area)
    if area_state is None:
        return False
    return any(
        str(raw_room.get("sub_loc_id", "")).strip() == normalized_location
        and str(raw_room.get("room_id", "")).strip() == normalized_room
        for raw_room in area_state.dynamic_rooms
        if isinstance(raw_room, Mapping)
    )


def resolve_npc_room(
    state: StateContainer,
    world: WorldInstance,
    *,
    area_id: str,
    location_id: str | None,
    npc_id: str,
    requested_room_id: str | None = None,
) -> str | None:
    """Resolve the NPC room using explicit, static, then default fallbacks."""
    normalized_area = area_id.strip()
    normalized_location = (location_id or "").strip()
    normalized_npc = npc_id.strip()
    explicit_room = (requested_room_id or "").strip() or None
    if not normalized_area or not normalized_npc or not normalized_location:
        return None
    if explicit_room is not None:
        return explicit_room if room_exists(
            state,
            world,
            normalized_area,
            normalized_location,
            explicit_room,
        ) else None
    static_room = _static_room_for_npc(
        world,
        normalized_area,
        normalized_location,
        normalized_npc,
    )
    if static_room is not None:
        return static_room
    default_room = get_default_room(world, normalized_area, normalized_location)
    if default_room is None:
        return None
    return default_room if room_exists(
        state,
        world,
        normalized_area,
        normalized_location,
        default_room,
    ) else None


def is_colocated(
    npc_location: str | None,
    player_location: str | None,
    npc_room: str | None = None,
    player_room: str | None = None,
) -> bool:
    """Return True when NPC and player share the same position (three-level check).

    Level 1 — sub_location:
    - Empty string is treated as None (area main scene).
    - Both None → both at area main scene → True.
    - One None, one non-empty → different positions → False.
    - Both non-empty → True iff they are equal.

    Level 2 — room (only checked when player is in a room):
    - If ``player_room`` is not None, NPC must be in the same room.
    - If ``player_room`` is None, any NPC in the same sub_location is visible.

    This allows ``is_colocated`` to remain backward-compatible: all existing
    callers that omit ``npc_room``/``player_room`` retain level-1 semantics only.
    """
    npc_loc = (npc_location or "").strip() or None
    player_loc = (player_location or "").strip() or None
    if npc_loc != player_loc:
        return False
    # Sub_location matches.  Now apply room filter.
    p_room = (player_room or "").strip() or None
    if p_room is None:
        # Player not in a room → all NPCs in same sub_location are visible.
        return True
    npc_r = (npc_room or "").strip() or None
    return npc_r == p_room


def _iter_area_rooms(area_template: object) -> list[tuple[str, object]]:
    rooms: list[tuple[str, object]] = []
    for sub_template in getattr(area_template, "sub_locations", {}).values():
        for raw_room_id, room_template in getattr(sub_template, "rooms", {}).items():
            room_id = str(raw_room_id).strip()
            if room_id:
                rooms.append((room_id, room_template))
    return rooms


def _static_room_for_npc(
    world: WorldInstance,
    area_id: str,
    location_id: str,
    npc_id: str,
) -> str | None:
    if not world.has_registry("maps"):
        return None
    sub_location = world.maps.get_sub_location(area_id, location_id)
    if sub_location is None:
        return None
    normalized_npc = npc_id.strip()
    for raw_room_id, room_template in getattr(sub_location, "rooms", {}).items():
        room_id = str(raw_room_id).strip()
        if not room_id:
            continue
        for raw_npc_id in getattr(room_template, "resident_npcs", []):
            if str(raw_npc_id).strip() == normalized_npc:
                return room_id
    return None
