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
        action_types = {
            str(a.get("type", "")).strip().lower()
            for a in context.action_log
            if isinstance(a, dict)
        }

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
            return _build_experience(
                "combat",
                day=day,
                location=location,
                participants=participants,
                summary=f"Day {day} combat at {location}",
                critical_moment="COMBAT_END" in bus_tags,
                emotion_tags=["danger", "relief"],
            )

        # Priority 2: crisis / betrayal / loss signals (new in GAP3 phase2)
        if {"CRISIS", "BEREAVEMENT", "RELATIONSHIP_DROPPING"} & bus_tags:
            return _build_experience(
                "crisis",
                day=day,
                location=location,
                participants=participants,
                summary=f"Day {day} crisis at {location}",
                critical_moment=True,
                emotion_tags=["alert"],
            )
        if {"BETRAYAL", "TRUST_LOSS"} & bus_tags:
            return _build_experience(
                "betrayal",
                day=day,
                location=location,
                participants=participants,
                summary=f"Day {day} betrayal at {location}",
                critical_moment=True,
                emotion_tags=["grief", "alert"],
            )
        if "LOSS" in bus_tags:
            return _build_experience(
                "loss",
                day=day,
                location=location,
                participants=participants,
                summary=f"Day {day} loss at {location}",
                critical_moment=True,
                emotion_tags=["sorrow"],
            )

        # Priority 3: quest progress
        if "advance_quest" in action_types or "QUEST_PROGRESS" in bus_tags:
            return _build_experience(
                "discovery",
                day=day,
                location=location,
                participants=participants,
                summary=f"Day {day} quest progress at {location}",
                critical_moment=False,
                emotion_tags=["achievement"],
            )

        # Priority 4: exploration
        if "navigate" in action_types or "NAVIGATION" in bus_tags:
            return _build_experience(
                "exploration",
                day=day,
                location=location,
                participants=participants,
                summary=f"Day {day} explored {location}",
                critical_moment=False,
                emotion_tags=["curiosity"],
            )

        # Priority 5: dialogue
        if (
            "talk" in action_types
            or "speak" in action_types
            or "dialogue_turn" in action_types
            or "public_utterance_turn" in action_types
            or "party_chat_turn" in action_types
            or "free_chat_turn" in action_types
            or any(a in bus_tags for a in ("DIALOGUE", "NPC_INTERACTION"))
        ):
            return _build_experience(
                "dialogue",
                day=day,
                location=location,
                participants=participants,
                summary=f"Day {day} meaningful dialogue at {location}",
                critical_moment=False,
                emotion_tags=["empathy"],
            )

        # Priority 6: celebration
        if "CRISIS_RESOLVED" in bus_tags or "VICTORY" in bus_tags:
            return _build_experience(
                "celebration",
                day=day,
                location=location,
                participants=participants,
                summary=f"Day {day} celebration at {location}",
                critical_moment=False,
                emotion_tags=["joy"],
            )

        # Priority 7: long rest
        if "rest_long" in action_types or "LONG_REST" in bus_tags:
            return _build_experience(
                "rest",
                day=day,
                location=location,
                participants=participants,
                summary=f"Day {day} camp at {location}",
                critical_moment=False,
                emotion_tags=["rest"],
            )

        return None


def _build_experience(
    exp_type: str,
    *,
    day: int,
    location: str,
    participants: list[str],
    summary: str,
    critical_moment: bool,
    emotion_tags: list[str],
) -> dict[str, Any]:
    return {
        "type": exp_type,
        "summary": summary,
        "day": day,
        "location": location,
        "participants": participants,
        "critical_moment": critical_moment,
        "emotion_tags": emotion_tags,
    }
