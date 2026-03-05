"""CampfireHook — post-long-rest companion memory recall and dialogue."""

from __future__ import annotations

import random
from typing import Any

from app.game_core.orchestration.hooks.base import NoOpSettlementHook
from app.game_core.orchestration.models import HookResult, SSEEvent
from app.game_core.orchestration.settlement import SettlementContext


# Stages on which a companion will not participate in campfire dialogue.
_NON_CAMPFIRE_STAGES: frozenset[str] = frozenset(
    {"stranger", "cold", "hostile", "nemesis", "enemy"}
)

# Static campfire dialogue templates keyed by memory type.
# {summary} placeholder is filled with the experience summary string.
_CAMPFIRE_LINES: dict[str, list[str]] = {
    "combat": [
        "那场战斗……{summary}。我还没想清楚。",
        "你还记得吗——{summary}？那时候真的很险。",
        "今晚的火让我想起了{summary}。",
    ],
    "discovery": [
        "{summary}。我一直觉得那背后还有什么。",
        "关于{summary}……我想多了解一些。",
    ],
    "rest": [
        "今晚的营火让我想到{summary}。",
        "能这样休息一下，感觉还不错。",
    ],
}


class CampfireHook(NoOpSettlementHook):
    """Post-long-rest campfire dialogue between player and companions (P63).

    Consumes shared_experiences recorded by SharedExperienceHook (P62).
    Runs before RelationshipHook (P65) so that the +5 approval bump
    is included in the stage transition evaluation.

    Trigger:
    - long rest detected (LONG_REST tag or rest_long in action_log)
    - party has eligible members (stage not stranger/cold/hostile, approval >= 0)
    - major experience today → always fires; otherwise 30% probability

    Design ref: P5 Phase 5 (NPC运行时规范 §十.5).
    """

    HOOK_PRIORITY = 63
    HOOK_NAME = "campfire"

    async def execute(self, context: SettlementContext) -> HookResult:
        if not _is_long_rest(context):
            return HookResult()
        if not context.state.has_slice("party"):
            return HookResult()
        members = context.state.party.members
        if not isinstance(members, dict) or not members:
            return HookResult()

        major = _has_major_experience_today(context)
        if not major and random.random() > 0.3:
            return HookResult()

        day = (
            context.state.time.snapshot().get("day", 1)
            if context.state.has_slice("time") else 0
        )
        sse_events: list[SSEEvent] = []
        for member_id in members:
            if not _teammate_eligible(member_id, context):
                continue
            memory = _select_memory(member_id, context, day)
            if memory is None:
                continue
            line = _generate_campfire_line(memory)
            if context.state.has_slice("relations"):
                try:
                    context.state.relations.modify_disposition(
                        member_id, "approval", 5
                    )
                except Exception:
                    pass
            sse_events.append(SSEEvent(
                event_type="campfire_dialogue",
                payload={
                    "teammate_id": member_id,
                    "content": line,
                    "memory_type": memory.get("type", ""),
                    "memory_summary": memory.get("summary", ""),
                },
            ))
        return HookResult(sse_events=sse_events)


# ------------------------------------------------------------------
# Pure helpers
# ------------------------------------------------------------------


def _is_long_rest(context: SettlementContext) -> bool:
    """Return True if a long rest occurred this tick."""
    for entry in context.scene_bus.snapshot().get("entries", []):
        if isinstance(entry, dict) and "LONG_REST" in entry.get("tags", []):
            return True
    return any(a.get("type") == "rest_long" for a in context.action_log)


def _has_major_experience_today(context: SettlementContext) -> bool:
    """Return True if combat ended or quest progressed this tick."""
    for entry in context.scene_bus.snapshot().get("entries", []):
        if isinstance(entry, dict) and entry.get("source") == "ENGINE":
            tags = entry.get("tags", [])
            if "COMBAT_END" in tags or "QUEST_PROGRESS" in tags:
                return True
    return any(
        a.get("type") in ("end_combat", "advance_quest")
        for a in context.action_log
    )


def _teammate_eligible(member_id: str, context: SettlementContext) -> bool:
    """Return True if the teammate should participate in campfire.

    Conditions: relationship stage is positive (not stranger / negative path)
    and current approval >= 0.
    """
    if not context.state.has_slice("relations"):
        return False
    stage = context.state.relations.get_stage(member_id) or "stranger"
    if stage in _NON_CAMPFIRE_STAGES:
        return False
    disp = context.state.relations.get_disposition(member_id)
    approval = disp.get("approval", 0) if isinstance(disp, dict) else 0
    return approval >= 0


def _select_memory(
    member_id: str,
    context: SettlementContext,
    today: int,
) -> dict[str, Any] | None:
    """Select the most relevant shared memory for campfire recall.

    Scoring (additive):
    - Today's major experience (combat/discovery): +100
    - Today's experience (any type): +50
    - critical_moment flag: +30
    - Major type (combat/discovery): +10
    """
    experiences = context.state.party.get_shared_experiences(
        with_character=member_id
    )
    if not experiences:
        return None

    def _score(exp: dict[str, Any]) -> int:
        score = 0
        exp_type = exp.get("type", "")
        exp_day = exp.get("day", 0)
        is_today = exp_day == today and today > 0
        is_major = exp_type in ("combat", "discovery")
        if is_today and is_major:
            score += 100
        elif is_today:
            score += 50
        if exp.get("critical_moment"):
            score += 30
        if is_major:
            score += 10
        return score

    return max(experiences, key=_score)


def _generate_campfire_line(memory: dict[str, Any]) -> str:
    """Generate a static campfire dialogue line from memory type and summary."""
    exp_type = memory.get("type", "rest")
    templates = _CAMPFIRE_LINES.get(exp_type, _CAMPFIRE_LINES["rest"])
    summary = memory.get("summary", "")
    return random.choice(templates).format(summary=summary)
