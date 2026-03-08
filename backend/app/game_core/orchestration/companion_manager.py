"""CompanionManager — companion follow-sync helpers."""

from __future__ import annotations

from app.game_core.state import StateContainer


class CompanionManager:
    """Handles companion position syncing outside the rules layer."""

    MAX_PARTY_SIZE: int = 4

    def __init__(self, state: StateContainer) -> None:
        self._state = state

    def sync_to_player(self) -> list[str]:
        """Sync all party members to the player's current position.

        Returns list of member IDs that were actually moved.
        """
        if (
            not self._state.has_slice("party")
            or not self._state.has_slice("areas")
            or not self._state.has_slice("player")
        ):
            return []

        player_area = self._state.player.current_area
        player_loc = self._state.player.current_location
        if not player_area:
            return []

        moved: list[str] = []
        for member_id in self._state.party.members:
            current_area = self._state.areas.find_npc_area(member_id)
            if current_area == player_area:
                area_state = self._state.areas.get_area(player_area)
                if area_state.npc_locations.get(member_id) == player_loc:
                    continue
            self._state.areas.move_npc(member_id, player_area, player_loc, source="companion")
            moved.append(member_id)

        return moved
