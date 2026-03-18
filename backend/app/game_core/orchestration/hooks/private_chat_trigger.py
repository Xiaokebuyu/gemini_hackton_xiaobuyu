"""Presence helpers — shared NPC reachability utilities for settlement hooks.

Originally part of the private-chat trigger system; the hook and evaluator
classes have been removed.  Only the reachability helpers remain because
they are consumed by DirectiveTriggerHook.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.game_core.orchestration.settlement import SettlementContext

if TYPE_CHECKING:
    from app.game_core.content import WorldInstance


def _collect_reachable_npcs(
    context: SettlementContext,
) -> tuple[set[str], dict[str, str | None]]:
    """Return (colocated_npc_ids, area_npc_locations) for the player's current area.

    *colocated_npc_ids* — NPCs truly sharing the player's sub-location (and room
    where applicable), plus party members who travel with the player.

    *area_npc_locations* — mapping of npc_id → sub_location for all NPCs present
    in the area (used for building the ``npc_location`` field in non-colocated
    SSE events).
    """
    if not (context.state.has_slice("player") and context.state.has_slice("areas")):
        return set(), {}

    player_area = context.state.player.current_area
    if not player_area:
        return set(), {}

    player_location: str | None = (context.state.player.current_location or "").strip() or None
    player_room: str | None = (
        str(getattr(context.state.player, "current_room", "") or "").strip() or None
    )

    # Import here to avoid circular imports at module load time
    from app.game_core.orchestration.presence import (  # noqa: PLC0415
        get_area_npcs,
        get_npc_room,
        is_colocated,
    )

    npc_locations = get_area_npcs(context.state, context.world, player_area)
    if not isinstance(npc_locations, dict):
        return set(), {}

    colocated: set[str] = set()
    for npc_id, npc_sub_loc in npc_locations.items():
        npc_loc = (npc_sub_loc or "").strip() or None
        npc_room = get_npc_room(context.state, context.world, player_area, npc_id)
        if is_colocated(npc_loc, player_location, npc_room, player_room):
            colocated.add(npc_id)

    # Party members: check colocation like any other NPC.
    # They may be tracked in party.members but not in npc_locations;
    # add them to the area_npc dict so directive_trigger can see them,
    # but only add to colocated if they pass the room check.
    if context.state.has_slice("party"):
        for member_id in context.state.party.get_members().keys():
            if member_id not in npc_locations:
                # Party member not in npc_locations — assume they are
                # at the player's sub-location (they travel together).
                npc_locations[member_id] = player_location
            member_loc = (npc_locations.get(member_id) or "").strip() or None
            member_room = get_npc_room(context.state, context.world, player_area, member_id)
            if is_colocated(member_loc, player_location, member_room, player_room):
                colocated.add(member_id)

    return colocated, dict(npc_locations)


def _get_npc_name(world: WorldInstance, npc_id: str) -> str:
    """Safely extract display name from characters registry."""
    if not world.has_registry("characters"):
        return npc_id
    profile = world.characters.get(npc_id)
    if profile is None:
        return npc_id
    # Lazy import to avoid circular dependency at module load time
    from app.game_core.narrative.context_builder import _profile_get  # noqa: PLC0415
    return str(_profile_get(profile, "name", npc_id))
