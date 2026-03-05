"""SharedExperienceHook — records party shared experiences after key actions."""

from __future__ import annotations

from typing import Any

from app.game_core.orchestration.hooks.base import NoOpSettlementHook
from app.game_core.orchestration.models import HookResult
from app.game_core.orchestration.settlement import SettlementContext


class SharedExperienceHook(NoOpSettlementHook):
    """Records party shared experiences (P62 — before RelationshipHook=65).

    Detects combat / quest / rest from action_log and SceneBus ENGINE tags,
    then calls PartySlice.record_experience().

    Design ref: P5 Phase 3 (NPC运行时规范 §十.4).
    """

    HOOK_PRIORITY = 62
    HOOK_NAME = "shared_experience"

    async def execute(self, context: SettlementContext) -> HookResult:
        if not context.state.has_slice("party"):
            return HookResult()
        members = context.state.party.members
        if not isinstance(members, dict) or not members:
            return HookResult()

        experience = self._detect_experience(context)
        if experience is None:
            return HookResult()

        context.state.party.record_experience(experience)
        return HookResult()

    def _detect_experience(
        self, context: SettlementContext
    ) -> dict[str, Any] | None:
        """Detect the most significant experience in this tick."""
        action_types = {a.get("type") for a in context.action_log}

        day = (
            context.state.time.snapshot().get("day", 1)
            if context.state.has_slice("time") else 1
        )
        location = (
            context.state.player.current_area
            if context.state.has_slice("player") else ""
        )
        participants = list(context.state.party.members.keys())

        # Collect ENGINE tags from SceneBus (written by Phase 0 _emit_action_tags)
        bus_tags: set[str] = set()
        for entry in context.scene_bus.snapshot().get("entries", []):
            if isinstance(entry, dict) and entry.get("source") == "ENGINE":
                bus_tags.update(entry.get("tags", []))

        # Priority 1: combat
        if "COMBAT_END" in bus_tags or "end_combat" in action_types:
            return {
                "type": "combat",
                "summary": f"Day {day} combat at {location}",
                "day": day,
                "location": location,
                "participants": participants,
                "critical_moment": False,
                "emotion_tags": ["danger", "relief"],
            }

        # Priority 2: quest progress
        if "advance_quest" in action_types or "QUEST_PROGRESS" in bus_tags:
            return {
                "type": "discovery",
                "summary": f"Day {day} quest progress at {location}",
                "day": day,
                "location": location,
                "participants": participants,
                "critical_moment": False,
                "emotion_tags": ["achievement"],
            }

        # Priority 3: long rest
        if "rest_long" in action_types or "LONG_REST" in bus_tags:
            return {
                "type": "rest",
                "summary": f"Day {day} camp at {location}",
                "day": day,
                "location": location,
                "participants": participants,
                "critical_moment": False,
                "emotion_tags": ["rest"],
            }

        return None
