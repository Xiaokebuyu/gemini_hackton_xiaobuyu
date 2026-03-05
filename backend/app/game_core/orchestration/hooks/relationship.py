"""RelationshipHook — checks and applies NPC relationship stage transitions."""

from __future__ import annotations

from typing import Any

from app.game_core.orchestration.hooks.base import NoOpSettlementHook
from app.game_core.orchestration.models import HookResult, SSEEvent
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.state import StateChange


# Ordered positive relationship stages (ascending).
_POSITIVE_STAGE_ORDER: tuple[str, ...] = (
    "stranger",
    "acquaintance",
    "friend",
    "close_friend",
    "intimate",
)

# Transition table: from_stage → (to_stage, min_approval, min_trust, min_romance, min_shared, min_critical)
# A value of 0 means the dimension is not checked.
# Threshold semantics: strictly greater than (approval > min_approval).
# Design source: NPC 运行时规范 §六
_TRANSITIONS: dict[str, tuple[str, int, int, int, int, int]] = {
    "stranger":     ("acquaintance",  10,  0,   0,  0, 0),
    "acquaintance": ("friend",        30, 20,   0,  3, 0),
    "friend":       ("close_friend",   0, 60,   0, 10, 1),
    # TODO: close_friend→intimate also requires a companion quest milestone
    # (同伴个人线完成). Milestone check deferred until quest system matures.
    "close_friend": ("intimate",       0, 80,  60,  0, 0),
}

# Negative entry thresholds: current_stage → (target_stage, max_approval)
# Triggered when approval drops below max_approval.
# stranger is excluded: baseline strangers do not enter negative stages.
_NEGATIVE_ENTRY_THRESHOLD: dict[str, tuple[str, int]] = {
    "acquaintance": ("cold", -20),
    "friend":       ("cold", -30),
    "close_friend": ("cold", -40),
    "intimate":     ("cold", -50),
}

# Progression within negative stages:
# cold → hostile: both approval AND trust must be below their thresholds.
# hostile → enemy: trust alone is sufficient at extreme levels.
# Design source: P5 文档 Phase 6.1
_NEGATIVE_PROGRESSION: dict[str, tuple[str, dict[str, int]]] = {
    "cold":    ("hostile", {"approval": -50, "trust": -30}),
    "hostile": ("enemy",   {"trust": -60}),
}


def _next_negative_stage(
    current_stage: str,
    dispositions: dict[str, int],
) -> str | None:
    """Return the next negative stage if thresholds are met, else None.

    Handles two cases:
    1. Positive stage → cold (approval drops below entry threshold).
    2. cold → hostile → enemy (further deterioration, multi-dimension check).
    """
    # Case 1: positive stage → cold
    threshold = _NEGATIVE_ENTRY_THRESHOLD.get(current_stage)
    if threshold is not None:
        neg_target, max_approval = threshold
        return neg_target if int(dispositions.get("approval", 0)) < max_approval else None

    # Case 2: cold → hostile → enemy (all dimension thresholds must be met)
    progression = _NEGATIVE_PROGRESSION.get(current_stage)
    if progression is None:
        return None
    next_neg_stage, thresholds = progression
    for dim, max_val in thresholds.items():
        if int(dispositions.get(dim, 0)) >= max_val:
            return None
    return next_neg_stage


class RelationshipHook(NoOpSettlementHook):
    """Check and advance NPC relationship stages each settlement tick.

    Positive path: stranger → acquaintance → friend → close_friend → intimate.
    Negative entry: acquaintance/friend/close_friend/intimate → cold (low approval).
    Further negative progression (cold → hostile → nemesis) deferred to later round.
    """

    HOOK_PRIORITY = 65  # After NpcScheduleHook(60), before TimeAdvanceHook(70)
    HOOK_NAME = "relationship"

    _TRIGGER_SLICES: frozenset[str] = frozenset({"relations", "party"})

    def should_skip(self, change_log: list[Any]) -> bool:
        return not any(
            getattr(change, "slice", "") in self._TRIGGER_SLICES
            for change in change_log
        )

    async def execute(self, context: SettlementContext) -> HookResult:
        if not context.state.has_slice("relations"):
            return HookResult(
                metadata={
                    "status": "noop",
                    "reason": "missing_relations_slice",
                    "checked_npc_count": 0,
                    "transitioned_count": 0,
                    "transitions": [],
                }
            )

        has_party = context.state.has_slice("party")
        transitions: list[dict[str, Any]] = []
        sse_events: list[SSEEvent] = []

        for npc_id in list(context.state.relations.npc_dispositions.keys()):
            dispositions = context.state.relations.get_disposition(npc_id) or {}
            if not isinstance(dispositions, dict):
                continue
            old_stage = context.state.relations.get_stage(npc_id) or "stranger"
            new_stage = self._next_stage(
                npc_id, old_stage, dispositions, context, has_party
            )
            if new_stage is None:
                new_stage = _next_negative_stage(old_stage, dispositions)
            if new_stage is None:
                continue
            context.state.relations.set_relationship_stage(npc_id, new_stage)
            context.record_change(
                StateChange(
                    slice="relations",
                    operation="set",
                    path=f"relationship_stages.{npc_id}",
                    value=new_stage,
                )
            )
            transitions.append(
                {"npc_id": npc_id, "old_stage": old_stage, "new_stage": new_stage}
            )
            sse_events.append(
                SSEEvent(
                    event_type="relationship_stage_changed",
                    payload={
                        "npc_id": npc_id,
                        "old_stage": old_stage,
                        "new_stage": new_stage,
                    },
                )
            )

            # Hostile/enemy stages: auto-dismiss companions
            if new_stage in ("hostile", "enemy") and has_party:
                if npc_id in (context.state.party.members or {}):
                    from app.game_core.orchestration.companion_manager import (
                        CompanionManager,
                    )
                    mgr = CompanionManager(context.world, context.state)
                    dismiss_result = mgr.force_leave(npc_id, reason=new_stage)
                    if dismiss_result.success:
                        context.record_change(
                            StateChange(
                                slice="party",
                                operation="remove",
                                path=f"members.{npc_id}",
                                value=None,
                            )
                        )
                        sse_events.append(
                            SSEEvent(
                                event_type="companion_dismissed",
                                payload={
                                    "npc_id": npc_id,
                                    "reason": f"relationship_{new_stage}",
                                    "old_stage": old_stage,
                                    "new_stage": new_stage,
                                },
                            )
                        )

        return HookResult(
            sse_events=sse_events,
            metadata={
                "status": "applied" if transitions else "noop",
                "checked_npc_count": len(context.state.relations.npc_dispositions),
                "transitioned_count": len(transitions),
                "transitions": transitions,
            },
        )

    @classmethod
    def _next_stage(
        cls,
        npc_id: str,
        current_stage: str,
        dispositions: dict[str, int],
        context: SettlementContext,
        has_party: bool,
    ) -> str | None:
        """Return the next stage if transition conditions are met, else None."""
        rule = _TRANSITIONS.get(current_stage)
        if rule is None:
            # Already at intimate, or on a negative path (not handled yet).
            return None

        to_stage, min_approval, min_trust, min_romance, min_shared, min_critical = rule

        approval = int(dispositions.get("approval", 0))
        trust    = int(dispositions.get("trust", 0))
        romance  = int(dispositions.get("romance", 0))

        if min_approval > 0 and approval <= min_approval:
            return None
        if min_trust > 0 and trust <= min_trust:
            return None
        if min_romance > 0 and romance <= min_romance:
            return None

        if (min_shared > 0 or min_critical > 0) and not has_party:
            # Cross-slice conditions cannot be evaluated without party slice.
            return None

        if min_shared > 0:
            shared = len(
                context.state.party.get_shared_experiences(with_character=npc_id)
            )
            if shared < min_shared:
                return None

        if min_critical > 0:
            if context.state.party.count_critical_moments(npc_id) < min_critical:
                return None

        return to_stage
