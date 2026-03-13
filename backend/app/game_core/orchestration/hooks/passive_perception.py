"""PassivePerceptionHook — auto-detects discoveries, hidden objects, traps on location entry."""

from __future__ import annotations

from typing import Any

from app.game_core.environment_access import list_current_scene_interactables
from app.game_core.orchestration.hooks.base import NoOpSettlementHook
from app.game_core.orchestration.models import HookResult, SSEEvent
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.state import StateChange


class PassivePerceptionHook(NoOpSettlementHook):
    """Settlement hook: triggers passive perception checks when player enters a new area/sub-location.

    Priority P45 — runs after EncounterHook (P40), before RelationshipHook (P65).

    Checks:
    1. Area-level discoveries (visibility_dc) when player is on world map (no sub-location).
    2. Sub-location interactable visibility_dc for hidden interactables.
    3. Sub-location container trap detect_dc.
    4. Dynamic sub-area discovery (plant_environmental, discovery_mode="check").
    """

    HOOK_PRIORITY = 45
    HOOK_NAME = "passive_perception"

    def should_skip(
        self,
        change_log: list[Any],
        action_log: list[dict[str, Any]] | None = None,
    ) -> bool:
        del action_log
        for change in change_log:
            slice_name = getattr(change, "slice", getattr(change, "slice_name", ""))
            if slice_name == "player" and getattr(change, "path", "") in {
                "current_area",
                "current_location",
                "current_room",
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
                            event_type="discovery_reveal",
                            payload={
                                "area_id": area_id,
                                "discovery_id": disc.id,
                                "name": disc.name,
                            },
                        ))

        # 2+3. Current reachable scene interactables — hidden objects and traps
        if sub_loc_id and area_id:
            for iact in list_current_scene_interactables(context.state, context.world):
                if not iact.interactable_id:
                    continue

                if iact.visibility_dc is not None:
                    if not context.state.areas.is_discovery_found(area_id, iact.interactable_id):
                        if passive >= iact.visibility_dc:
                            context.state.areas.mark_discovery(area_id, iact.interactable_id)
                            interactables_revealed.append(iact.interactable_id)
                            sse_events.append(SSEEvent(
                                event_type="hidden_object_revealed",
                                payload={
                                    "area_id": area_id,
                                    "interactable_id": iact.interactable_id,
                                    "name": iact.name,
                                },
                            ))

                trap = None
                if isinstance(iact.container_data, dict):
                    raw_trap = iact.container_data.get("trap")
                    trap = raw_trap if isinstance(raw_trap, dict) else None
                else:
                    trap = getattr(iact.container_data, "trap", None)
                if trap is None:
                    continue

                detect_dc = int(getattr(trap, "detect_dc", trap.get("detect_dc", 15) if isinstance(trap, dict) else 15))
                if not context.state.areas.is_trap_detected(area_id, iact.interactable_id):
                    if passive >= detect_dc:
                        context.state.areas.mark_trap_detected(area_id, iact.interactable_id)
                        traps_detected.append(iact.interactable_id)
                        sse_events.append(SSEEvent(
                            event_type="trap_detected",
                            payload={
                                "area_id": area_id,
                                "interactable_id": iact.interactable_id,
                            },
                        ))

        # 4. Dynamic sub-area discovery (plant_environmental, discovery_mode="check")
        dynamic_sub_areas_found: list[str] = []
        if area_id:
            for sub_area in context.state.areas.list_temporary_sub_areas(area_id):
                if sub_area.get("discovery_mode") != "check":
                    continue
                sub_id = sub_area.get("id", "")
                if not sub_id or context.state.areas.is_discovery_found(area_id, sub_id):
                    continue
                dc = int(sub_area.get("discovery_dc", 0))
                if dc <= 0 or passive >= dc:
                    context.state.areas.mark_discovery(area_id, sub_id)
                    dynamic_sub_areas_found.append(sub_id)
                    context.record_change(StateChange(
                        slice="areas",
                        operation="set",
                        path=f"discovered_items.{sub_id}",
                        value=True,
                    ))
                    sse_events.append(SSEEvent(
                        event_type="discovery_reveal",
                        payload={
                            "area_id": area_id,
                            "discovery_id": sub_id,
                            "label": sub_area.get("label", sub_id),
                            "source": "dynamic_sub_area",
                        },
                    ))

        status = (
            "applied"
            if (discoveries_found or interactables_revealed or traps_detected or dynamic_sub_areas_found)
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
                "dynamic_sub_areas_found": dynamic_sub_areas_found,
            },
        )
