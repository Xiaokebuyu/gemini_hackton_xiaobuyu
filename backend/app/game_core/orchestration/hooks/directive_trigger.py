"""DirectiveTriggerHook — translates NarrativePlanner directives into NPC chat triggers.

When NarrativePlannerHook emits a ``direct_npc`` directive the NPC id is stored
in ``NarrativePlanSlice.npc_directives``.  Without this hook those directives
are only consumed when the *player* initiates dialogue — meaning NPCs never
proactively walk up to the player.

This hook closes that loop: every tick it scans pending directives, checks
whether the target NPC is in the current area, and fires an
``npc_wants_to_chat`` SSE event so the frontend can surface the conversation
invitation or a location-based reminder. At most one NPC invitation is emitted
per tick to avoid overwhelming the player.

Priority 76 — runs after:
  - NarrativePlannerHook (35) — directives already written
  - NpcScheduleHook      (60) — NPCs are in their scheduled locations
  - PrivateChatTriggerHook (75) — relationship-driven invitations already emitted

Decision record: D-P19a (narrative.md)
"""

from __future__ import annotations

import logging
import random
from typing import TYPE_CHECKING

from app.game_core.orchestration.hooks.base import NoOpSettlementHook
from app.game_core.orchestration.hooks.private_chat_trigger import (
    _collect_reachable_npcs,
    _get_npc_name,
)
from app.game_core.orchestration.models import HookResult, SSEEvent
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules.models import Command

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Trigger probability per directive priority level
# ------------------------------------------------------------------

_PRIORITY_CHANCE: dict[str, float] = {
    "high": 0.90,
    "medium": 0.60,
    "low": 0.30,
}

COOLDOWN_TICKS: int = 4  # ticks between re-triggering the same NPC


class DirectiveTriggerHook(NoOpSettlementHook):
    """Settlement hook that promotes NPC directives to player-facing invitations.

    For each pending (unconsumed, non-expired) directive whose target NPC is
    currently in the player's area, rolls a probability gate and — on success —
    emits an ``npc_wants_to_chat`` SSE event with ``reason="directive"``.

    When the NPC is not truly co-located with the player (same sub-location and
    room where applicable), the payload includes ``colocated=false`` and
    location hints so the UI can degrade to a non-interrupting reminder instead
    of a direct private-chat invitation.

    At most **one** invitation is emitted per tick to avoid flooding the player.
    """

    HOOK_PRIORITY: int = 76
    HOOK_NAME: str = "directive_trigger"

    async def execute(self, context: SettlementContext) -> HookResult:
        # Guard: required slices
        if not context.state.has_slice("narrative_plan"):
            return HookResult(metadata={"skipped": "no_narrative_plan"})
        if not context.state.has_slice("player"):
            return HookResult(metadata={"skipped": "no_player_slice"})

        # Guard: player must not already be inside a private chat location
        current_loc = context.state.player.current_location
        if current_loc and current_loc.startswith("_private_"):
            return HookResult(metadata={"skipped": "already_in_private_chat"})

        current_tick: int = (
            context.state.time.absolute_tick()
            if context.state.has_slice("time")
            else 0
        )

        pending_directives = [
            d for d in context.state.narrative_plan.npc_directives
            if not d.get("consumed", False)
            and d.get("expires_at_tick", current_tick + 1) >= current_tick
        ]
        if not pending_directives:
            return HookResult(metadata={"skipped": "no_pending_directives"})

        colocated_npc_ids, area_npc_locations = _collect_reachable_npcs(context)
        all_area_npc_ids: set[str] = set(area_npc_locations.keys())
        if context.state.has_slice("party"):
            all_area_npc_ids.update(context.state.party.get_members().keys())
        if not all_area_npc_ids:
            return HookResult(metadata={"skipped": "no_reachable_npcs"})

        has_flags: bool = context.state.has_slice("flags")
        sse_events: list[SSEEvent] = []

        for directive_entry in pending_directives:
            npc_id = directive_entry.get("npc_id")
            if not isinstance(npc_id, str) or not npc_id:
                continue

            if npc_id not in all_area_npc_ids:
                continue

            cooldown_key = f"directive_trigger_cooldown_{npc_id}"

            # Cooldown check
            if has_flags:
                cooldown_until = context.state.flags.get(cooldown_key, 0)
                if isinstance(cooldown_until, int) and current_tick < cooldown_until:
                    continue
                # Expired — clear stale flag
                if context.state.flags.has(cooldown_key):
                    context.execute_command(Command(
                        type="remove_flag",
                        params={"key": cooldown_key},
                        source="system",
                    ))

            # Probability gate per directive priority
            priority = str(directive_entry.get("priority", "medium")).lower()
            chance = _PRIORITY_CHANCE.get(priority, _PRIORITY_CHANCE["medium"])
            if random.random() > chance:
                continue

            # Set cooldown flag via command (architecture-compliant)
            if has_flags:
                context.execute_command(Command(
                    type="set_flag",
                    params={"key": cooldown_key, "value": current_tick + COOLDOWN_TICKS},
                    source="system",
                ))

            npc_name = _get_npc_name(context.world, npc_id)
            npc_colocated = npc_id in colocated_npc_ids
            linked_quest_id = str(directive_entry.get("linked_quest_id", "")).strip() or None
            payload: dict[str, object] = {
                "npc_id": npc_id,
                "npc_name": npc_name,
                "reason": "directive",
                "colocated": npc_colocated,
            }
            if linked_quest_id is not None:
                payload["linked_quest_id"] = linked_quest_id
            if not npc_colocated:
                payload["npc_location"] = area_npc_locations.get(npc_id)
                current_area = str(context.state.player.current_area or "").strip()
                if current_area:
                    from app.game_core.orchestration.presence import get_npc_room  # noqa: PLC0415

                    npc_room = get_npc_room(context.state, context.world, current_area, npc_id)
                    if npc_room is not None:
                        payload["npc_room"] = npc_room

            sse_events.append(SSEEvent(
                event_type="npc_wants_to_chat",
                payload=payload,
            ))
            logger.debug(
                "DirectiveTriggerHook: %s wants to chat (directive priority=%s, colocated=%s)",
                npc_id, priority, npc_colocated,
            )
            # Only one invitation per tick
            break

        return HookResult(
            sse_events=sse_events,
            metadata={"triggered": len(sse_events)},
        )
