"""PrivateChatTriggerHook — detects NPCs wanting to initiate private conversations.

Implements 设计规范 §7.4 Phase B (NPC-initiated private chat).

Runs at settlement (priority 75, after NpcScheduleHook=60).
For each NPC with known disposition, checks:
  - romance >= 60, OR
  - trust   >= 50, OR
  - relationship_stage == "intimate"

If triggered, emits SSE event "npc_wants_to_chat" and sets a FlagSlice
cooldown to prevent re-triggering within COOLDOWN_TICKS absolute ticks.

Decision record: D-N20 (narrative.md)
"""

from __future__ import annotations

import logging
import random
from typing import TYPE_CHECKING, Protocol

from app.game_core.orchestration.hooks.base import NoOpSettlementHook
from app.game_core.orchestration.hooks.rest_phase import resolve_rest_phase
from app.game_core.orchestration.models import HookResult, SSEEvent
from app.game_core.orchestration.settlement import SettlementContext

if TYPE_CHECKING:
    from app.game_core.content import WorldInstance
    from app.game_core.orchestration.scene_bus import SceneBus

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Trigger thresholds (§7.4)
# ------------------------------------------------------------------

ROMANCE_THRESHOLD: int = 60
# TRUST_THRESHOLD is intentionally kept at 50 to align with Phase B orchestration
# guidance (orchestration layer §3.2). NPC spec §7.1 currently states trust > 60,
# so this mismatch is documented here for follow-up spec sync.
TRUST_THRESHOLD: int = 50
INTIMATE_STAGE: str = "intimate"
COOLDOWN_TICKS: int = 6  # ~1/4 game day (absolute_tick units)
_NON_CHAT_STAGES: frozenset[str] = frozenset({
    "stranger",
    "cold",
    "hostile",
    "nemesis",
    "enemy",
})
_TRIGGER_CHANCE: dict[str, float] = {
    "romance": 0.40,   # romance 触发情境
    "trust": 0.30,     # trust 驱动
    "intimate": 0.60,  # intimate 更容易触发
}


# ------------------------------------------------------------------
# Evaluator protocol + implementations
# ------------------------------------------------------------------


class PrivateChatTriggerEvaluator(Protocol):
    """Decides whether an NPC should initiate a private conversation."""

    def should_initiate(
        self,
        npc_id: str,
        dispositions: dict[str, int],
        stage: str,
    ) -> tuple[bool, str]:
        """Return (should_trigger, reason).

        reason: 'romance' | 'trust' | 'intimate' | ''
        """


class BasicPrivateChatTriggerEvaluator:
    """Default evaluator: romance ≥ 60 / trust ≥ 50 / stage == 'intimate'."""

    def should_initiate(
        self,
        npc_id: str,
        dispositions: dict[str, int],
        stage: str,
    ) -> tuple[bool, str]:
        del npc_id  # unused — decision is purely disposition/stage based
        if dispositions.get("romance", 0) >= ROMANCE_THRESHOLD:
            return True, "romance"
        if dispositions.get("trust", 0) >= TRUST_THRESHOLD:
            return True, "trust"
        if stage == INTIMATE_STAGE:
            return True, "intimate"
        return False, ""


class NullPrivateChatTriggerEvaluator:
    """No-op evaluator — never triggers (safe default for testing / opt-out)."""

    def should_initiate(
        self,
        npc_id: str,
        dispositions: dict[str, int],
        stage: str,
    ) -> tuple[bool, str]:
        del npc_id, dispositions, stage
        return False, ""


def _collect_reachable_npcs(context: SettlementContext) -> set[str]:
    """Return NPC ids reachable by player at current tick."""
    if not (context.state.has_slice("player") and context.state.has_slice("areas")):
        return set()

    player_area = context.state.player.current_area
    if not player_area:
        return set()

    area_snap = context.state.areas.snapshot()
    area_data = area_snap.get("areas", {}).get(player_area, {})
    npc_locations = area_data.get("npc_locations")
    if not isinstance(npc_locations, dict):
        return set()

    reachable_npc_ids = set(npc_locations.keys())
    if context.state.has_slice("party"):
        reachable_npc_ids.update(context.state.party.get_members().keys())
    return reachable_npc_ids


def _is_rest_tick(scene_bus: SceneBus) -> bool:
    """Return True when current tick is a rest/campfire moment."""
    bus_snap = scene_bus.snapshot()
    for entry in bus_snap.get("entries", []):
        if not isinstance(entry, dict) or entry.get("source") != "ENGINE":
            continue
        tags = entry.get("tags", [])
        if not isinstance(tags, list):
            continue
        if "REST" in tags or "LONG_REST" in tags:
            return True
    return False


# ------------------------------------------------------------------
# Hook
# ------------------------------------------------------------------


class PrivateChatTriggerHook(NoOpSettlementHook):
    """Settlement hook that checks NPC disposition thresholds and emits
    ``npc_wants_to_chat`` SSE events when an NPC wants to chat privately.

    Priority 75 — runs after NpcScheduleHook (60) so NPCs are in their
    scheduled locations before the trigger check.
    """

    HOOK_PRIORITY: int = 75
    HOOK_NAME: str = "private_chat_trigger"

    def __init__(
        self,
        evaluator: PrivateChatTriggerEvaluator | None = None,
    ) -> None:
        self._evaluator: PrivateChatTriggerEvaluator = (
            evaluator if evaluator is not None else BasicPrivateChatTriggerEvaluator()
        )

    async def execute(self, context: SettlementContext) -> HookResult:
        """Scan NPC dispositions; emit SSE for each NPC wanting to chat."""
        if not context.state.has_slice("relations"):
            return HookResult(metadata={"skipped": "no_relations"})

        if context.state.has_slice("player"):
            current_loc = context.state.player.current_location
            if current_loc and current_loc.startswith("_private_"):
                return HookResult(metadata={"skipped": "already_in_private_chat"})
        else:
            return HookResult(metadata={"skipped": "no_player_slice"})

        rest_phase = resolve_rest_phase(context)
        if rest_phase is None:
            return HookResult(metadata={"skipped": "not_rest_tick"})
        if rest_phase.rest_action_type == "rest_long" and not rest_phase.is_final_rest_slot:
            return HookResult(metadata={"skipped": "not_final_rest_slot"})

        reachable_npc_ids = _collect_reachable_npcs(context)
        if not reachable_npc_ids:
            return HookResult(metadata={"skipped": "no_reachable_npcs"})

        current_tick: int = (
            context.state.time.absolute_tick()
            if context.state.has_slice("time") else 0
        )

        dispositions_map: dict[str, dict[str, int]] = (
            context.state.relations.npc_dispositions
        )
        stages_map: dict[str, str] = context.state.relations.relationship_stages
        has_flags: bool = context.state.has_slice("flags")

        sse_events: list[SSEEvent] = []

        for npc_id, dispositions in dispositions_map.items():
            if npc_id not in reachable_npc_ids:
                continue

            cooldown_key = f"private_chat_cooldown_{npc_id}"

            # Cooldown check
            if has_flags:
                cooldown_until = context.state.flags.get(cooldown_key, 0)
                if isinstance(cooldown_until, int) and current_tick < cooldown_until:
                    continue    # still in cooldown
                # Expired — clear stale flag
                if context.state.flags.has(cooldown_key):
                    context.state.flags.remove(cooldown_key)

            stage = stages_map.get(npc_id, "acquaintance")
            if stage in _NON_CHAT_STAGES:
                continue

            should_trigger, reason = self._evaluator.should_initiate(
                npc_id,
                dict(dispositions) if not isinstance(dispositions, dict) else dispositions,
                stage,
            )
            if not should_trigger:
                continue

            chance = _TRIGGER_CHANCE.get(reason, 0.30)
            if random.random() > chance:
                continue

            # Set cooldown flag (direct mutation — same pattern as NpcScheduleHook)
            if has_flags:
                context.state.flags.set(cooldown_key, current_tick + COOLDOWN_TICKS)

            npc_name = _get_npc_name(context.world, npc_id)
            sse_events.append(SSEEvent(
                event_type="npc_wants_to_chat",
                payload={
                    "npc_id": npc_id,
                    "npc_name": npc_name,
                    "reason": reason,
                },
            ))
            logger.debug(
                "PrivateChatTriggerHook: %s wants to chat (reason=%s)", npc_id, reason,
            )

        return HookResult(
            sse_events=sse_events,
            metadata={"triggered": len(sse_events)},
        )


# ------------------------------------------------------------------
# Module-level helper
# ------------------------------------------------------------------


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
