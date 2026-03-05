"""CompanionManager — recruit / dismiss / force_leave logic (P5 Phase 7)."""

from __future__ import annotations

from dataclasses import dataclass

from app.game_core.content import WorldInstance
from app.game_core.state import StateContainer


@dataclass(slots=True)
class RecruitResult:
    """Result of a CompanionManager operation."""

    success: bool
    reason: str = ""  # "recruited" / "dismissed" / "force_leave:{reason}"
    # or failure reasons: "npc_not_found" / "not_recruitable" / "stranger"
    # "npc_refuses" / "already_member" / "party_full" / "not_member"


class CompanionManager:
    """Handles companion recruitment and dismissal (NPC运行时规范 §十.2).

    Called by:
    - RelationshipHook: force_leave() when stage transitions to hostile/enemy
    - InteractionService: recruit() / dismiss() for player-initiated actions
    """

    MAX_PARTY_SIZE: int = 4

    def __init__(self, world: WorldInstance, state: StateContainer) -> None:
        self._world = world
        self._state = state

    def recruit(self, npc_id: str) -> RecruitResult:
        """Recruit an NPC as a companion.

        Preconditions (all must be satisfied):
        - NPC exists in character registry with 'recruitable' tag
        - Relationship stage is not 'stranger'
        - approval > 0
        - Party has fewer than MAX_PARTY_SIZE members
        - NPC is not already a party member
        """
        if not self._world.has_registry("characters"):
            return RecruitResult(False, "no_character_registry")
        profile = self._world.characters.get(npc_id)
        if profile is None:
            return RecruitResult(False, "npc_not_found")

        tags = [t.lower() for t in getattr(profile, "tags", [])]
        if "recruitable" not in tags:
            return RecruitResult(False, "not_recruitable")

        if self._state.has_slice("relations"):
            stage = self._state.relations.get_stage(npc_id) or "stranger"
            if stage == "stranger":
                return RecruitResult(False, "stranger")
            disp = self._state.relations.get_disposition(npc_id)
            if isinstance(disp, dict) and int(disp.get("approval", 0)) <= 0:
                return RecruitResult(False, "npc_refuses")

        if not self._state.has_slice("party"):
            return RecruitResult(False, "no_party_slice")

        members = self._state.party.members
        if not isinstance(members, dict):
            return RecruitResult(False, "no_party_slice")
        if npc_id in members:
            return RecruitResult(False, "already_member")
        if len(members) >= self.MAX_PARTY_SIZE:
            return RecruitResult(False, "party_full")

        tick = self._state.time.absolute_tick() if self._state.has_slice("time") else 0
        self._state.party.add_member(npc_id, {
            "name": getattr(profile, "name", npc_id),
            "class_id": getattr(profile, "class_id", ""),
            "recruited_tick": tick,
        })
        return RecruitResult(True, "recruited")

    def dismiss(self, npc_id: str) -> RecruitResult:
        """Remove a companion from the party voluntarily."""
        if not self._state.has_slice("party"):
            return RecruitResult(False, "no_party_slice")
        if npc_id not in (self._state.party.members or {}):
            return RecruitResult(False, "not_member")
        self._state.party.remove_member(npc_id)
        return RecruitResult(True, "dismissed")

    def force_leave(self, npc_id: str, reason: str = "") -> RecruitResult:
        """NPC-initiated departure due to relationship deterioration.

        Called by RelationshipHook when an NPC's stage transitions to
        'hostile' or 'enemy'.  If the NPC is not a party member, this
        is a no-op and returns a failure result.
        """
        result = self.dismiss(npc_id)
        if result.success:
            result.reason = f"force_leave:{reason}" if reason else "force_leave"
        return result
