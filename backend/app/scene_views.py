"""Scene and location state payload builders for the API shell."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.game_core import ManagedSession
from app.game_core.environment_access import (
    list_visible_scene_interactables,
    resolve_room_name,
    room_display_entries,
)
from app.game_core.orchestration.presence import get_area_npc_sources, get_area_npcs, is_colocated
from app.game_core.orchestration.presence import get_npc_room
from app.game_core.scene_interactables import build_primary_action, classify_interaction_kind

if TYPE_CHECKING:
    from app.asset_resolver import AssetResolver


def build_location_overview(session: ManagedSession) -> dict[str, Any]:
    """Build a location_overview payload from current session state.

    Aggregates player position, NPC presence, sub-locations, interactables,
    and exits into a single snapshot consumed by all SSE streams and GET /scene.
    """
    player = session.runtime.state.player
    current_area_id = (player.current_area or "").strip()
    current_location_id = (player.current_location or "").strip() or None
    current_room_id = (getattr(player, "current_room", None) or "").strip() or None

    area_slice = getattr(session.runtime.state, "areas", None)
    area_state = area_slice.areas.get(current_area_id) if area_slice is not None else None

    area_template = None
    if session.runtime.world.has_registry("maps"):
        area_template = session.runtime.world.maps.get(current_area_id)

    relations = session.runtime.state.relations

    # ── present_npcs ──────────────────────────────────────────────────────────

    party_member_ids: set[str] = set()
    if session.runtime.state.has_slice("party"):
        party_member_ids = set(session.runtime.state.party.members.keys())

    present_npcs: list[dict[str, Any]] = []
    area_npcs = get_area_npcs(session.runtime.state, session.runtime.world, current_area_id)
    npc_sources = get_area_npc_sources(session.runtime.state, current_area_id)
    for npc_id, npc_sub_loc in area_npcs.items():
        # Filter by current player position (sub_location + room)
        npc_room: str | None = None
        if current_room_id is not None:
            npc_room = get_npc_room(
                session.runtime.state,
                session.runtime.world,
                current_area_id,
                npc_id,
            )
        if not is_colocated(npc_sub_loc, current_location_id, npc_room, current_room_id):
            continue

        npc_template = None
        if session.runtime.world.has_registry("characters"):
            npc_template = session.runtime.world.characters.get(npc_id)

        npc_name = npc_id
        npc_tags: list[str] = []
        has_shop = False
        if npc_template is not None:
            npc_name = npc_template.name.strip() or npc_id
            npc_tags = [str(t).strip() for t in npc_template.tags if str(t).strip()]
            has_shop = (
                npc_template.shop is not None
                or npc_template.shop_inventory is not None
            )

        # role from tags
        if "main" in npc_tags:
            role = "main"
        elif "passerby" in npc_tags:
            role = "passerby"
        else:
            role = "secondary"

        # disposition_hint from numeric values
        raw_disp = relations.npc_dispositions.get(npc_id, {})
        disp = raw_disp if isinstance(raw_disp, dict) else {}
        try:
            approval = int(disp.get("approval", 0))
        except (TypeError, ValueError):
            approval = 0
        try:
            fear = int(disp.get("fear", 0))
        except (TypeError, ValueError):
            fear = 0
        if approval >= 50:
            disposition_hint = "friendly"
        elif fear >= 30:
            disposition_hint = "wary"
        else:
            disposition_hint = "neutral"

        relationship_stage = (
            str(relations.relationship_stages.get(npc_id, "")).strip() or None
        )

        is_companion = npc_id in party_member_ids

        present_npcs.append({
            "character_id": npc_id,
            "name": npc_name,
            "role": "companion" if is_companion else role,
            "is_companion": is_companion,
            "recruitable": "recruitable" in npc_tags,
            "disposition_hint": disposition_hint,
            "has_shop": has_shop,
            "relationship_stage": relationship_stage,
            "tags": npc_tags,
            "presence_source": npc_sources.get(npc_id, "resident"),
        })

    # ── sub_locations ─────────────────────────────────────────────────────────

    sub_locations: list[dict[str, Any]] = []
    seen_sub_location_ids: set[str] = set()
    if area_template is not None:
        for loc_id, loc_template in area_template.sub_locations.items():
            loc_id_str = str(loc_id).strip()
            if not loc_id_str:
                continue
            raw_name = getattr(loc_template, "name", "")
            loc_name = str(raw_name).strip() if raw_name else loc_id_str
            raw_type = getattr(loc_template, "type", "visit")
            sub_locations.append({
                "id": loc_id_str,
                "name": loc_name or loc_id_str,
                "type": str(raw_type),
                "available": True,  # available_hours check deferred to MVP+
            })
            seen_sub_location_ids.add(loc_id_str)

    if area_state is not None:
        for raw_sub_area in area_state.temporary_sub_areas:
            if not isinstance(raw_sub_area, dict):
                continue
            sub_area_id = str(raw_sub_area.get("id", "")).strip()
            if not sub_area_id or sub_area_id in seen_sub_location_ids:
                continue
            sub_area_name = (
                str(raw_sub_area.get("name") or raw_sub_area.get("label") or sub_area_id)
                .strip()
                or sub_area_id
            )
            entry = {
                "id": sub_area_id,
                "name": sub_area_name,
                "type": str(raw_sub_area.get("type") or "visit"),
                "available": True,
                "temporary": bool(raw_sub_area.get("temporary", True)),
                "source": str(raw_sub_area.get("source") or "runtime"),
            }
            if "hostile" in raw_sub_area:
                entry["hostile"] = bool(raw_sub_area.get("hostile", False))
            if "threat_level" in raw_sub_area:
                threat_level = str(raw_sub_area.get("threat_level") or "").strip()
                if threat_level:
                    entry["threat_level"] = threat_level
            if "blocking" in raw_sub_area:
                entry["blocking"] = bool(raw_sub_area.get("blocking", False))
            sub_locations.append(entry)
            seen_sub_location_ids.add(sub_area_id)

    # ── interactables (current reachable scene only) ──────────────────────────

    interactables: list[dict[str, Any]] = []
    for entry in list_visible_scene_interactables(session.runtime.state, session.runtime.world):
        container_status: str | None = None
        trapped_hint = False
        if entry.container_data is not None and area_slice is not None:
            container_state = area_slice.get_container_state(
                current_area_id,
                entry.interactable_id,
            )
            if isinstance(container_state, dict):
                if bool(container_state.get("opened") or container_state.get("looted")):
                    container_status = "opened"
                elif str(container_state.get("lock_status", "unlocked")) == "locked":
                    container_status = "locked"
                else:
                    container_status = "closed"
                trapped_hint = bool(container_state.get("trap_detected", False))
            else:
                locked = _container_locked(entry.container_data)
                container_status = "locked" if locked else "closed"

        interactables.append({
            "id": entry.interactable_id,
            "name": entry.name,
            "description_hint": entry.description,
            "requires_check": bool(entry.checks) or entry.visibility_dc is not None,
            "container_status": container_status,
            "trapped_hint": trapped_hint,
            "tags": list(entry.tags),
            "interaction_kind": classify_interaction_kind(
                functional=entry.functional,
                is_container=entry.container_data is not None,
            ),
            "primary_action": build_primary_action(
                entry.interactable_id,
                functional=entry.functional,
            ),
        })

    # ── rooms (current sub_location's rooms) ──────────────────────────────────

    rooms: list[dict[str, Any]] = []
    if current_location_id is not None:
        rooms = room_display_entries(
            session.runtime.state,
            session.runtime.world,
            current_area_id,
            current_location_id,
        )

    # ── exits ─────────────────────────────────────────────────────────────────

    exits: list[dict[str, Any]] = []
    if area_template is not None:
        for connection in area_template.connections:
            target_area_id = str(connection.target).strip()
            if not target_area_id:
                continue
            target_name = target_area_id
            if session.runtime.world.has_registry("maps"):
                target_template = session.runtime.world.maps.get(target_area_id)
                if target_template is not None:
                    target_name = target_template.name or target_area_id
            exits.append({
                "target_area_id": target_area_id,
                "name": target_name,
                "travel_slots": int(connection.travel_slots),
                "blocked": bool(connection.blocked),
            })

    payload: dict[str, Any] = {
        "area_id": current_area_id,
        "area_name": area_template.name if area_template else current_area_id,
        "location_id": current_location_id,
        "current_room": current_room_id,
        "present_npcs": present_npcs,
        "sub_locations": sub_locations,
        "rooms": rooms,
        "interactables": interactables,
        "exits": exits,
    }

    if current_location_id is not None:
        loc_tmpl = (
            area_template.sub_locations.get(current_location_id)
            if area_template is not None
            else None
        )
        if loc_tmpl is not None:
            raw_loc_name = getattr(loc_tmpl, "name", "")
            if raw_loc_name:
                payload["location_name"] = str(raw_loc_name).strip()
        else:
            # dynamic sub-area fallback
            for raw in (area_state.temporary_sub_areas if area_state is not None else []):
                if isinstance(raw, dict) and raw.get("id") == current_location_id:
                    payload["location_name"] = str(
                        raw.get("label") or raw.get("name") or current_location_id
                    )
                    break
    if current_room_id is not None and current_location_id is not None:
        payload["room_name"] = resolve_room_name(
            session.runtime.state,
            session.runtime.world,
            current_area_id,
            current_location_id,
            current_room_id,
        )

    return payload


def build_scene_change(
    session: ManagedSession,
    transition: str = "fade",
    asset_resolver: "AssetResolver | None" = None,
) -> dict[str, Any]:
    """Build a scene_change payload for navigation transitions.

    When ``asset_resolver`` is supplied and the player is entering a dynamic
    (temporary) sub-location that already has a cached background image, the
    payload will include a ``background_url`` field containing a data URL.
    This is a synchronous cache-only check — it never triggers generation.
    """
    player = session.runtime.state.player
    current_area_id = (player.current_area or "").strip()
    current_location_id = (player.current_location or "").strip() or None
    current_room_id = (getattr(player, "current_room", None) or "").strip() or None

    location_name = current_area_id
    is_dynamic_sub_location = False

    if current_location_id is not None and current_room_id is not None:
        background = f"locations/{current_area_id}/{current_location_id}/{current_room_id}.png"
    elif current_location_id is not None:
        background = f"locations/{current_area_id}/{current_location_id}.png"
    else:
        background = f"locations/{current_area_id}.png"

    if session.runtime.world.has_registry("maps"):
        area_template = session.runtime.world.maps.get(current_area_id)
        if area_template is not None:
            location_name = area_template.name or current_area_id
            if current_location_id is not None:
                loc_template = area_template.sub_locations.get(current_location_id)
                if loc_template is not None:
                    raw_name = getattr(loc_template, "name", "")
                    if raw_name:
                        location_name = str(raw_name).strip() or location_name
                    if current_room_id is not None:
                        room_template = loc_template.rooms.get(current_room_id)
                        if room_template is not None:
                            raw_room_name = getattr(room_template, "name", "")
                            if raw_room_name:
                                location_name = str(raw_room_name).strip() or location_name
                else:
                    # Not a static sub-location — check dynamic (temporary) sub-areas
                    area_state = session.runtime.state.areas.areas.get(current_area_id)
                    if area_state is not None:
                        for raw_sub_area in area_state.temporary_sub_areas:
                            if not isinstance(raw_sub_area, dict):
                                continue
                            sub_area_id = str(raw_sub_area.get("id", "")).strip()
                            if sub_area_id != current_location_id:
                                continue
                            raw_name = raw_sub_area.get("name") or raw_sub_area.get("label")
                            if raw_name:
                                location_name = str(raw_name).strip() or location_name
                            is_dynamic_sub_location = True
                            break
    if current_location_id is not None and current_room_id is not None:
        resolved_room_name = resolve_room_name(
            session.runtime.state,
            session.runtime.world,
            current_area_id,
            current_location_id,
            current_room_id,
        )
        if resolved_room_name:
            location_name = resolved_room_name

    payload: dict[str, Any] = {
        "location_id": current_location_id,
        "room_id": current_room_id,
        "location_name": location_name,
        "background": background,
        "transition": transition,
        "ambient_preset": None,
        "ambient_override": None,
    }

    # Attach cached background image URL for dynamic sub-locations
    if is_dynamic_sub_location and current_location_id is not None and asset_resolver is not None:
        background_url = _lookup_cached_background(
            current_location_id, session, asset_resolver
        )
        if background_url is not None:
            payload["background_url"] = background_url

    return payload


def _container_locked(container_data: Any) -> bool:
    if isinstance(container_data, dict):
        return bool(container_data.get("locked", False))
    return bool(getattr(container_data, "locked", False))


def _lookup_cached_background(
    sub_area_id: str,
    session: ManagedSession,
    resolver: "AssetResolver",
) -> str | None:
    """Synchronous cache-only lookup for a dynamic sub-area background.

    Reads the current in-game time period from state and checks the
    AssetResolver LRU cache.  Returns a data URL on hit, None on miss.
    """
    from app.image_prefetch import get_cached_background_url

    time_period = "day"
    if session.runtime.state.has_slice("time"):
        time_period = str(session.runtime.state.time.period or "day")

    return get_cached_background_url(sub_area_id, time_period, resolver)
