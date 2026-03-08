"""NPC presence helpers — single source of truth for area/location queries.

These are pure stateless functions that consolidate two previously divergent
NPC-location resolution paths (scene_views.py and interaction.py).

Design ref: P11 Block A — NPC Presence 单一真相源
"""

from __future__ import annotations

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


def is_colocated(
    npc_location: str | None,
    player_location: str | None,
) -> bool:
    """Return True when NPC and player share the same sub-location.

    Normalisation rules:
    - Empty string is treated as None (area main scene).
    - Both None → both at area main scene → True.
    - One None, one non-empty → different positions → False.
    - Both non-empty → True iff they are equal.
    """
    npc_loc = (npc_location or "").strip() or None
    player_loc = (player_location or "").strip() or None
    return npc_loc == player_loc
