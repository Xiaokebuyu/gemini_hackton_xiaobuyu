"""ContextAssembler — builds the stable 8-layer context payload (L0-L7).

Design spec: 编排层设计规范 §4.1
L6 (memory_recall) remains a stub pending a future MemoryGraph adapter.
L7 (engine_result) is initialized with a stable empty shape, then overwritten
by PipelineOrchestrator after A4 runs the rules engine.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any

from app.game_core.orchestration.shared_context import SharedContext

if TYPE_CHECKING:
    from app.game_core.content.world import WorldInstance
    from app.game_core.state.base import StateContainer


class ContextAssembler:
    """Build stable, layered context payloads for engine and agent consumers."""

    def assemble(self, shared: SharedContext) -> dict[str, Any]:
        """Build the engine-facing full context payload."""
        state = shared.state
        world = shared.world
        current_area, current_location = self._resolve_location(state)
        area_states = self._get_area_states_snapshot(state)
        current_area_state = self._get_current_area_state(area_states, current_area)
        return {
            "l0_world_constants": self._build_world_constants(world),
            "l1_chapter_state": self._build_chapter_state(state),
            "l2_area_environment": self._build_area_environment(
                world,
                current_area,
                current_area_state,
            ),
            "l3_location_details": self._build_location_details(
                world,
                current_area,
                current_location,
                current_area_state,
            ),
            "l4_dynamic_state": self._build_dynamic_state(state),
            "l5_scene_bus": self._build_scene_bus_engine_view(shared),
            "l6_memory_recall": self._build_memory_recall_stub(),
            "l7_engine_result": self._build_engine_result_stub(),
        }

    def assemble_for_role(
        self,
        shared: SharedContext,
        role: str,
        character_id: str | None = None,
    ) -> dict[str, Any]:
        """Build a role-filtered context payload for agent use."""
        if role not in {"gm", "npc", "teammate"}:
            raise ValueError(f"unsupported role: {role}")
        if role in {"npc", "teammate"} and not character_id:
            raise ValueError(f"{role} context requires character_id")
        context = self.assemble(shared)
        context["l5_scene_bus"] = self._build_scene_bus_role_view(
            shared,
            role,
            character_id,
        )
        return context

    # ------------------------------------------------------------------
    # Private builders
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_location(state: StateContainer) -> tuple[str, str | None]:
        """Extract current area and sub-location from PlayerSlice."""
        if not state.has_slice("player"):
            return "", None
        player = state.player
        return player.current_area, player.current_location

    @staticmethod
    def _get_area_states_snapshot(state: StateContainer) -> dict[str, dict[str, Any]]:
        """Read AreaSlice snapshot without mutating state."""
        if not state.has_slice("areas"):
            return {}
        raw_areas = state.areas.snapshot().get("areas", {})
        if not isinstance(raw_areas, dict):
            return {}
        return {
            str(area_id): dict(area_state)
            for area_id, area_state in raw_areas.items()
            if isinstance(area_state, dict)
        }

    @staticmethod
    def _get_current_area_state(
        area_states: dict[str, dict[str, Any]],
        current_area: str,
    ) -> dict[str, Any] | None:
        if not current_area:
            return None
        area_state = area_states.get(current_area)
        return dict(area_state) if isinstance(area_state, dict) else None

    @staticmethod
    def _build_world_constants(world: WorldInstance) -> dict[str, Any]:
        """L0: static world knowledge — lore + factions."""
        lore: list[Any] = []
        factions: list[Any] = []
        if world.has_registry("lore"):
            lore = world.lore.list_all()
        if world.has_registry("factions"):
            factions = world.factions.list_all()
        return {
            "world_id": world.world_id,
            "lore": lore,
            "factions": factions,
        }

    @staticmethod
    def _build_chapter_state(state: StateContainer) -> dict[str, Any]:
        """L1: story progression — quest runtime + planning metadata."""
        result: dict[str, Any] = {
            "chapter_completion": {},
            "available_milestones": [],
            "milestone_states": {},
            "active_dynamic_quests": [],
            "current_chapter": "",
            "current_target_milestone": None,
            "escalation_level": 0,
            "strategy_notes": "",
        }

        if state.has_slice("quests"):
            quest_snapshot = state.quests.snapshot()
            chapter_completion = quest_snapshot.get("chapter_completion", {})
            milestone_states = quest_snapshot.get("milestone_states", {})
            dynamic_quests = quest_snapshot.get("dynamic_quests", {})
            result["chapter_completion"] = (
                dict(chapter_completion) if isinstance(chapter_completion, dict) else {}
            )
            result["milestone_states"] = (
                {
                    str(milestone_id): dict(milestone_state)
                    for milestone_id, milestone_state in milestone_states.items()
                    if isinstance(milestone_state, dict)
                }
                if isinstance(milestone_states, dict)
                else {}
            )
            result["available_milestones"] = state.quests.get_available_milestones()
            if isinstance(dynamic_quests, dict):
                result["active_dynamic_quests"] = [
                    dict(quest)
                    for quest in dynamic_quests.values()
                    if isinstance(quest, dict)
                    and str(quest.get("status", "")).lower() != "retired"
                ]

        if state.has_slice("narrative_plan"):
            plan_snapshot = state.narrative_plan.snapshot()
            result["current_chapter"] = str(plan_snapshot.get("current_chapter", ""))
            target = plan_snapshot.get("current_target_milestone")
            result["current_target_milestone"] = (
                str(target) if target is not None else None
            )
            result["escalation_level"] = int(plan_snapshot.get("escalation_level", 0))
            result["strategy_notes"] = str(plan_snapshot.get("strategy_notes", ""))

        return result

    @classmethod
    def _build_area_environment(
        cls,
        world: WorldInstance,
        current_area: str,
        current_area_state: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """L2: current area template + dynamic area state."""
        template: dict[str, Any] | None = None
        if current_area and world.has_registry("maps"):
            raw_template = world.maps.get(current_area)
            if raw_template is not None:
                if dataclasses.is_dataclass(raw_template):
                    template = dataclasses.asdict(raw_template)
                elif isinstance(raw_template, dict):
                    template = dict(raw_template)
                else:
                    template = None

        state_snapshot = (
            dict(current_area_state) if isinstance(current_area_state, dict) else None
        )
        dynamic_counts = cls._count_dynamic_sub_areas(current_area_state)
        return {
            "area_id": current_area,
            "template": template,
            "state": state_snapshot,
            "dynamic_sub_area_counts": dynamic_counts,
        }

    @classmethod
    def _build_location_details(
        cls,
        world: WorldInstance,
        current_area: str,
        current_location: str | None,
        current_area_state: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """L3: sub-location details + current exploration hints."""
        template: dict[str, Any] | None = None
        is_dynamic = False
        if current_area and current_location and world.has_registry("maps"):
            area_template = world.maps.get(current_area)
            if area_template is not None:
                sub_locations = getattr(area_template, "sub_locations", None)
                if sub_locations is None and isinstance(area_template, dict):
                    sub_locations = area_template.get("sub_locations", {})
                if isinstance(sub_locations, dict):
                    location_data = sub_locations.get(current_location)
                    if isinstance(location_data, dict):
                        template = dict(location_data)
                    elif dataclasses.is_dataclass(location_data):
                        template = dataclasses.asdict(location_data)

        # Fallback: check dynamic sub-areas in area state
        if template is None and current_location and isinstance(current_area_state, dict):
            raw_sub_areas = current_area_state.get("temporary_sub_areas", [])
            if isinstance(raw_sub_areas, list):
                for item in raw_sub_areas:
                    if isinstance(item, dict) and str(item.get("id", "")) == current_location:
                        template = dict(item)
                        is_dynamic = True
                        break

        area_exploration = None
        discovered_items: list[str] = []
        if isinstance(current_area_state, dict):
            raw_exploration = current_area_state.get("exploration")
            area_exploration = (
                str(raw_exploration) if raw_exploration is not None else None
            )
            raw_discoveries = current_area_state.get("discovered_items", [])
            if isinstance(raw_discoveries, list):
                discovered_items = sorted(str(item) for item in raw_discoveries)

        # Collect all dynamic sub-areas for AI context
        dynamic_sub_areas: list[dict[str, Any]] = []
        if isinstance(current_area_state, dict):
            raw_sub_areas = current_area_state.get("temporary_sub_areas", [])
            if isinstance(raw_sub_areas, list):
                dynamic_sub_areas = [
                    dict(item) for item in raw_sub_areas if isinstance(item, dict)
                ]

        return {
            "location_id": current_location,
            "template": template,
            "is_dynamic": is_dynamic,
            "area_exploration": area_exploration,
            "discovered_items": discovered_items,
            "dynamic_sub_areas": dynamic_sub_areas,
        }

    @staticmethod
    def _build_dynamic_state(state: StateContainer) -> dict[str, Any]:
        """L4: mutable runtime slices relevant to immediate gameplay."""
        result: dict[str, Any] = {
            "time": None,
            "player": None,
            "relations": None,
            "flags": None,
            "party": None,
        }
        for name in result:
            if state.has_slice(name):
                result[name] = state.get_slice(name).snapshot()
        return result

    @classmethod
    def _build_scene_bus_engine_view(
        cls,
        shared: SharedContext,
    ) -> dict[str, Any]:
        """L5 for engine/internal callers: full scene view, including state changes."""
        scene_snapshot = shared.scene_bus.snapshot()
        return {
            "entries": cls._copy_entries(scene_snapshot.get("entries", [])),
            "state_changes": cls._copy_mapping_list(
                scene_snapshot.get("state_changes", [])
            ),
            "viewer_role": "engine",
            "viewer_id": None,
        }

    @classmethod
    def _build_scene_bus_role_view(
        cls,
        shared: SharedContext,
        role: str,
        character_id: str | None,
    ) -> dict[str, Any]:
        """L5 for role-filtered agent callers: filtered entries, no state delta log."""
        visible_entries = [
            entry.snapshot()
            for entry in shared.scene_bus.get_for_role(role, character_id)
        ]
        return {
            "entries": visible_entries,
            "state_changes": [],
            "viewer_role": role,
            "viewer_id": character_id,
        }

    @staticmethod
    def _build_memory_recall_stub() -> dict[str, Any]:
        return {
            "hits": [],
            "source": "stub",
        }

    @staticmethod
    def _build_engine_result_stub() -> dict[str, Any]:
        return {
            "success": None,
            "narrative_hints": [],
            "rolls": [],
            "time_cost": 0.0,
        }

    @staticmethod
    def _count_dynamic_sub_areas(
        current_area_state: dict[str, Any] | None,
    ) -> dict[str, int] | None:
        """Count temporary sub-areas without mutating AreaSlice."""
        if not isinstance(current_area_state, dict):
            return None
        raw_sub_areas = current_area_state.get("temporary_sub_areas", [])
        if not isinstance(raw_sub_areas, list):
            return None
        counts = {"permanent": 0, "timed": 0, "temporary": 0, "total": 0}
        for item in raw_sub_areas:
            if not isinstance(item, dict):
                continue
            expiry = int(item.get("expiry", 0))
            if expiry == -1:
                counts["permanent"] += 1
            elif expiry >= 24:
                counts["timed"] += 1
            else:
                counts["temporary"] += 1
            counts["total"] += 1
        return counts

    @staticmethod
    def _copy_entries(raw_entries: Any) -> list[dict[str, Any]]:
        if not isinstance(raw_entries, list):
            return []
        return [dict(entry) for entry in raw_entries if isinstance(entry, dict)]

    @staticmethod
    def _copy_mapping_list(raw_values: Any) -> list[dict[str, Any]]:
        if not isinstance(raw_values, list):
            return []
        return [dict(item) for item in raw_values if isinstance(item, dict)]
