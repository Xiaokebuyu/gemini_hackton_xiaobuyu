"""PassivePerceptionHook — auto-detects discoveries, hidden objects, traps on location entry."""

from __future__ import annotations

from typing import Any

from app.game_core.orchestration.hooks.base import NoOpSettlementHook
from app.game_core.orchestration.models import HookResult, SSEEvent
from app.game_core.orchestration.settlement import SettlementContext


class PassivePerceptionHook(NoOpSettlementHook):
    """Settlement hook: triggers passive perception checks when player enters a new area/sub-location.

    Priority P45 — runs after EncounterHook (P40), before RelationshipHook (P65).

    Checks:
    1. Area-level discoveries (visibility_dc) when player is on world map (no sub-location).
    2. Sub-location interactable visibility_dc for hidden interactables.
    3. Sub-location container trap detect_dc.
    """

    HOOK_PRIORITY = 45
    HOOK_NAME = "passive_perception"

    def should_skip(self, change_log: list[Any]) -> bool:
        for change in change_log:
            slice_name = getattr(change, "slice", getattr(change, "slice_name", ""))
            if slice_name == "player" and getattr(change, "path", "") in {
                "current_area",
                "current_location",
            }:
                return False
        return True

    async def execute(self, context: SettlementContext) -> HookResult:
        if not context.state.has_slice("player"):
            return HookResult(metadata={"status": "noop", "reason": "no_player_slice"})
        if not context.world.has_registry("maps"):
            return HookResult(metadata={"status": "noop", "reason": "no_maps_registry"})
        if not context.state.has_slice("areas"):
            return HookResult(metadata={"status": "noop", "reason": "no_areas_slice"})

        try:
            passive = 10 + context.state.player.get_modifier("wis")
        except (KeyError, TypeError):
            passive = 10

        area_id = context.state.player.current_area
        sub_loc_id = context.state.player.current_location

        sse_events: list[SSEEvent] = []
        discoveries_found: list[str] = []
        interactables_revealed: list[str] = []
        traps_detected: list[str] = []

        # 1. Area-level discoveries — only when on world map (no sub-location)
        if sub_loc_id is None and area_id:
            area_template = context.world.maps.get(area_id)
            if area_template is not None:
                for disc in area_template.discoveries:
                    if not disc.id:
                        continue
                    if context.state.areas.is_discovery_found(area_id, disc.id):
                        continue
                    if passive >= disc.dc:
                        context.state.areas.mark_discovery(area_id, disc.id)
                        discoveries_found.append(disc.id)
                        sse_events.append(SSEEvent(
                            event_type="discovery_found",
                            payload={
                                "area_id": area_id,
                                "discovery_id": disc.id,
                                "name": disc.name,
                            },
                        ))

        # 2+3. Sub-location interactables — visibility_dc and trap detect_dc
        if sub_loc_id and area_id:
            sub_loc = context.world.maps.get_sub_location(area_id, sub_loc_id)
            if sub_loc is not None:
                for iact in sub_loc.interactables:
                    if not iact.id:
                        continue

                    # visibility_dc check (hidden interactable reveal)
                    if iact.visibility_dc is not None:
                        if not context.state.areas.is_discovery_found(area_id, iact.id):
                            if passive >= iact.visibility_dc:
                                context.state.areas.mark_discovery(area_id, iact.id)
                                interactables_revealed.append(iact.id)
                                sse_events.append(SSEEvent(
                                    event_type="hidden_object_revealed",
                                    payload={
                                        "area_id": area_id,
                                        "interactable_id": iact.id,
                                        "name": iact.name,
                                    },
                                ))

                    # trap detect_dc check
                    if iact.container_data is not None and iact.container_data.trap is not None:
                        if not context.state.areas.is_trap_detected(area_id, iact.id):
                            if passive >= iact.container_data.trap.detect_dc:
                                context.state.areas.mark_trap_detected(area_id, iact.id)
                                traps_detected.append(iact.id)
                                sse_events.append(SSEEvent(
                                    event_type="trap_detected",
                                    payload={
                                        "area_id": area_id,
                                        "interactable_id": iact.id,
                                    },
                                ))

        status = (
            "applied"
            if (discoveries_found or interactables_revealed or traps_detected)
            else "noop"
        )
        return HookResult(
            sse_events=sse_events,
            metadata={
                "status": status,
                "passive_perception": passive,
                "discoveries_found": discoveries_found,
                "interactables_revealed": interactables_revealed,
                "traps_detected": traps_detected,
            },
        )
