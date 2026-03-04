"""Scene and location state payload builders for the API shell."""

from __future__ import annotations

from typing import Any

from app.game_core import ManagedSession


def build_location_overview(session: ManagedSession) -> dict[str, Any]:
    """Build a location_overview payload from current session state.

    Aggregates player position, NPC presence, sub-locations, interactables,
    and exits into a single snapshot consumed by all SSE streams and GET /scene.
    """
    player = session.runtime.state.player
    current_area_id = (player.current_area or "").strip()
    current_location_id = (player.current_location or "").strip() or None

    area_state = session.runtime.state.areas.areas.get(current_area_id)

    area_template = None
    if session.runtime.world.has_registry("maps"):
        area_template = session.runtime.world.maps.get(current_area_id)

    relations = session.runtime.state.relations

    # ── present_npcs ──────────────────────────────────────────────────────────

    present_npcs: list[dict[str, Any]] = []
    if area_state is not None:
        for raw_npc_id, raw_loc in area_state.npc_locations.items():
            npc_id = str(raw_npc_id).strip()
            if not npc_id:
                continue

            # Filter by current player position
            npc_loc_str = str(raw_loc).strip() if isinstance(raw_loc, str) else ""
            if current_location_id is None:
                # Player at area main scene → only NPCs with no specific sub-location
                if npc_loc_str:
                    continue
            else:
                # Player inside a sub-location → only NPCs in the same sub-location
                if npc_loc_str != current_location_id:
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

            present_npcs.append({
                "character_id": npc_id,
                "name": npc_name,
                "role": role,
                "disposition_hint": disposition_hint,
                "has_shop": has_shop,
                "relationship_stage": relationship_stage,
                "tags": npc_tags,
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

    # ── interactables (current sub_location only) ─────────────────────────────

    interactables: list[dict[str, Any]] = []
    if current_location_id is not None and area_template is not None:
        loc_template = area_template.sub_locations.get(current_location_id)
        if loc_template is not None:
            interactable_states = (
                area_state.interactable_states if area_state is not None else {}
            )
            for iact in getattr(loc_template, "interactables", []):
                iact_id = str(getattr(iact, "id", "")).strip()
                if not iact_id:
                    continue

                requires_check = getattr(iact, "visibility_dc", None) is not None

                container_data = getattr(iact, "container_data", None)
                container_status: str | None = None
                trapped_hint = False
                if container_data is not None:
                    iact_state = interactable_states.get(iact_id, {})
                    iact_state = iact_state if isinstance(iact_state, dict) else {}
                    fallback_status = "locked" if container_data.locked else "closed"
                    container_status = str(
                        iact_state.get("container_status", fallback_status)
                    ).strip()
                    # Only surface trap hint if player has already discovered it
                    trapped_hint = bool(iact_state.get("trap_discovered", False))

                interactables.append({
                    "id": iact_id,
                    "name": str(getattr(iact, "name", iact_id)),
                    "description_hint": str(getattr(iact, "description", "")),
                    "requires_check": requires_check,
                    "container_status": container_status,
                    "trapped_hint": trapped_hint,
                })

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

    return {
        "area_id": current_area_id,
        "location_id": current_location_id,
        "present_npcs": present_npcs,
        "sub_locations": sub_locations,
        "interactables": interactables,
        "exits": exits,
    }


def build_scene_change(
    session: ManagedSession,
    transition: str = "fade",
) -> dict[str, Any]:
    """Build a scene_change payload for navigation transitions."""
    player = session.runtime.state.player
    current_area_id = (player.current_area or "").strip()
    current_location_id = (player.current_location or "").strip() or None

    location_name = current_area_id
    if current_location_id is not None:
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
                else:
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
                            break

    return {
        "location_id": current_location_id,
        "location_name": location_name,
        "background": background,
        "transition": transition,
        "ambient_preset": None,
        "ambient_override": None,
    }
