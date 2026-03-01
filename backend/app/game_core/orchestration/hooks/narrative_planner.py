"""NarrativePlannerHook implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any, Mapping, Protocol

from app.game_core.orchestration.hooks.base import NoOpSettlementHook
from app.game_core.orchestration.models import HookResult, SSEEvent
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.planning import NarrativePlanner
from app.game_core.planning.models import (
    AdjustPacingPlan,
    CreateQuestPlan,
    DirectNpcPlan,
    EscalatePlan,
    FillAreaPlan,
    PlanningDirective,
    PlantEnvironmentalPlan,
    PublishBulletinPlan,
    RetireQuestPlan,
    SpawnQuestNpcPlan,
)
from app.game_core.state import StateChange


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class NarrativePlannerDecision:
    directives: list[Any] = field(default_factory=list)
    strategy_notes: str = ""
    next_scheduled_tick: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class NarrativePlannerProvider(Protocol):
    async def plan(self, context: dict[str, Any]) -> Any:
        ...


class NarrativePlannerHook(NoOpSettlementHook):
    HOOK_PRIORITY = 35
    HOOK_NAME = "narrative_planner"
    FALLBACK_INTERVAL = 6

    _TRIGGER_SLICES = {
        "flags",
        "quests",
        "player",
        "areas",
        "events",
        "relations",
        "party",
    }
    _SUPPORTED_DIRECTIVES = {
        "create_quest",
        "direct_npc",
        "publish_bulletin",
        "escalate",
        "adjust_pacing",
        "retire_quest",
        "spawn_quest_npc",
        "plant_environmental",
        "fill_area",
    }
    _UNSUPPORTED_DIRECTIVES: set[str] = set()

    def __init__(self, planner: NarrativePlannerProvider | None = None) -> None:
        self.planner = planner or NarrativePlanner()

    def should_skip(self, change_log: list[StateChange]) -> bool:
        del change_log
        return False

    async def execute(self, context: SettlementContext) -> HookResult:
        if not context.state.has_slice("narrative_plan"):
            return HookResult(metadata=self._noop_metadata(reason="missing_slice"))
        if not context.state.has_slice("quests"):
            return HookResult(metadata=self._noop_metadata(reason="missing_slice"))
        if not context.state.has_slice("time"):
            return HookResult(metadata=self._noop_metadata(reason="missing_slice"))

        current_tick = context.state.time.absolute_tick()
        ticks_since_last_run = max(0, current_tick - context.state.narrative_plan.last_run_tick)
        triggered = self._has_trigger_change(context.change_log)
        if not triggered and ticks_since_last_run < self.FALLBACK_INTERVAL:
            return HookResult(
                metadata=self._noop_metadata(
                    reason="cooldown",
                    current_tick=current_tick,
                    ticks_since_last_run=ticks_since_last_run,
                )
            )

        reason = "trigger" if triggered else "fallback"
        planner_context = self._build_planner_context(context, current_tick=current_tick)
        try:
            raw_decision = await self.planner.plan(planner_context)
        except Exception as exc:
            logger.exception(
                "hook failed: narrative_planner",
                extra={
                    "hook_name": self.HOOK_NAME,
                    "current_tick": current_tick,
                    "reason": reason,
                    "ticks_since_last_run": ticks_since_last_run,
                },
            )
            return HookResult(
                sse_events=[
                    SSEEvent(
                        event_type="narrative_planner_error",
                        payload={"error": str(exc)},
                    )
                ],
                metadata={
                    "status": "planner_error",
                    "evaluated": False,
                    "reason": reason,
                    "current_tick": current_tick,
                    "ticks_since_last_run": ticks_since_last_run,
                    "requested_count": 0,
                    "applied_count": 0,
                    "skipped_unsupported_count": 0,
                    "skipped_invalid_count": 0,
                    "applied_kinds": [],
                    "planner_metadata": {},
                },
            )

        decision = self._normalize_decision(raw_decision)
        requested_count = len(decision.directives)
        applied_count = 0
        skipped_unsupported_count = 0
        skipped_invalid_count = 0
        applied_kinds: list[str] = []

        for raw_directive in decision.directives:
            normalized = self._normalize_directive(raw_directive)
            if normalized is None:
                skipped_invalid_count += 1
                continue
            kind, payload = normalized
            if kind in self._UNSUPPORTED_DIRECTIVES or kind not in self._SUPPORTED_DIRECTIVES:
                skipped_unsupported_count += 1
                continue
            if not self._apply_directive(kind, payload, context, current_tick=current_tick):
                skipped_invalid_count += 1
                continue
            applied_count += 1
            applied_kinds.append(kind)

        milestone_progressed = any(
            change.slice == "quests" and change.path.startswith("milestone_states.")
            for change in context.change_log
        )
        progress_value = 0
        if not milestone_progressed:
            progress_value = (
                context.state.narrative_plan.ticks_since_milestone_progress
                + max(1, ticks_since_last_run)
            )

        context.state.narrative_plan.last_run_tick = current_tick
        context.state.narrative_plan.ticks_since_milestone_progress = progress_value
        context.state.narrative_plan._dirty = True
        if decision.strategy_notes:
            context.state.narrative_plan.set_strategy(decision.strategy_notes)
        if (
            isinstance(decision.next_scheduled_tick, int)
            and decision.next_scheduled_tick >= current_tick
        ):
            context.state.narrative_plan.schedule_next(decision.next_scheduled_tick)
        context.state.narrative_plan.record_behavior(
            {
                "tick": current_tick,
                "changed_slices": planner_context["changed_slices"],
                "reason": reason,
                "directive_count": requested_count,
            }
        )

        sse_events: list[SSEEvent] = []
        if applied_count > 0:
            sse_events.append(
                SSEEvent(
                    event_type="narrative_plan_updated",
                    payload={
                        "applied_count": applied_count,
                        "applied_kinds": list(applied_kinds),
                        "current_tick": current_tick,
                    },
                )
            )
        return HookResult(
            sse_events=sse_events,
            metadata={
                "status": "updated",
                "evaluated": True,
                "reason": reason,
                "current_tick": current_tick,
                "ticks_since_last_run": ticks_since_last_run,
                "requested_count": requested_count,
                "applied_count": applied_count,
                "skipped_unsupported_count": skipped_unsupported_count,
                "skipped_invalid_count": skipped_invalid_count,
                "applied_kinds": applied_kinds,
                "planner_metadata": dict(decision.metadata),
            },
        )

    def _has_trigger_change(self, change_log: list[StateChange]) -> bool:
        for change in change_log:
            if change.slice in self._TRIGGER_SLICES:
                return True
        return False

    def _build_planner_context(
        self,
        context: SettlementContext,
        *,
        current_tick: int,
    ) -> dict[str, Any]:
        seen: set[str] = set()
        changed_slices: list[str] = []
        recent_changes: list[dict[str, Any]] = []
        for change in context.change_log:
            if change.slice not in seen:
                seen.add(change.slice)
                changed_slices.append(change.slice)
            recent_changes.append(
                {
                    "slice": change.slice,
                    "operation": change.operation,
                    "path": change.path,
                    "value": change.value,
                }
            )

        location = {"area_id": "", "location_id": None}
        if context.state.has_slice("player"):
            location = {
                "area_id": context.state.player.current_area,
                "location_id": context.state.player.current_location,
            }

        available_milestones: list[str] = []
        active_milestones: list[str] = []
        completed_milestones: list[str] = []
        for milestone_id, milestone in context.state.quests.milestone_states.items():
            state_name = str(milestone.state)
            if state_name == "AVAILABLE":
                available_milestones.append(milestone_id)
            elif state_name == "ACTIVE":
                active_milestones.append(milestone_id)
            elif state_name == "COMPLETED":
                completed_milestones.append(milestone_id)

        dynamic_quests = {
            quest_id: {
                "status": self._string_or_empty(quest.get("status")),
                "title": self._string_or_empty(quest.get("title")),
                "summary": self._string_or_empty(quest.get("summary")),
            }
            for quest_id, quest in context.state.quests.dynamic_quests.items()
        }

        area_cluster = None
        if context.state.has_slice("areas") and context.state.has_slice("player"):
            area_id = context.state.player.current_area
            if area_id and area_id in context.state.areas.areas:
                counts = context.state.areas.count_dynamic_sub_areas(area_id)
                area_cluster = {
                    "area_id": area_id,
                    "has_capacity": context.state.areas.has_cluster_capacity(
                        area_id
                    ),
                    "total_dynamic": counts.get("total", 0),
                }

        scene_snapshot = context.scene_bus.snapshot()
        return {
            "current_tick": current_tick,
            "time": {
                "day": context.state.time.day,
                "slot": context.state.time.slot,
                "period": context.state.time.period,
                "absolute_tick": current_tick,
            },
            "location": location,
            "changed_slices": changed_slices,
            "change_count": len(context.change_log),
            "recent_changes": recent_changes,
            "quests": {
                "available_milestones": available_milestones,
                "active_milestones": active_milestones,
                "completed_milestones": completed_milestones,
                "dynamic_quests": dynamic_quests,
            },
            "narrative_plan": {
                "current_chapter": context.state.narrative_plan.current_chapter,
                "current_target_milestone": context.state.narrative_plan.current_target_milestone,
                "chapter_completion": context.state.narrative_plan.chapter_completion,
                "escalation_level": context.state.narrative_plan.escalation_level,
                "ticks_since_milestone_progress": (
                    context.state.narrative_plan.ticks_since_milestone_progress
                ),
                "strategy_notes": context.state.narrative_plan.strategy_notes,
                "last_run_tick": context.state.narrative_plan.last_run_tick,
                "next_scheduled_tick": context.state.narrative_plan.next_scheduled_tick,
                "pacing_frozen": context.state.narrative_plan.pacing_frozen,
                "behavior_window_size": len(context.state.narrative_plan.behavior_window),
            },
            "area_cluster": area_cluster,
            "scene": {
                "entry_count": len(scene_snapshot.get("entries", [])),
                "state_change_count": len(scene_snapshot.get("state_changes", [])),
            },
        }

    @classmethod
    def _normalize_decision(cls, raw: Any) -> NarrativePlannerDecision:
        if isinstance(raw, NarrativePlannerDecision):
            return NarrativePlannerDecision(
                directives=list(raw.directives),
                strategy_notes=cls._string_or_empty(raw.strategy_notes),
                next_scheduled_tick=raw.next_scheduled_tick,
                metadata=cls._normalize_mapping(raw.metadata),
            )
        if isinstance(raw, list):
            return NarrativePlannerDecision(directives=list(raw))
        if not isinstance(raw, Mapping):
            return NarrativePlannerDecision(metadata={"status": "invalid_response"})
        raw_directives = raw.get("directives", [])
        directives = list(raw_directives) if isinstance(raw_directives, list) else []
        next_tick = raw.get("next_scheduled_tick")
        if next_tick is not None and (
            not isinstance(next_tick, int) or isinstance(next_tick, bool)
        ):
            next_tick = None
        return NarrativePlannerDecision(
            directives=directives,
            strategy_notes=cls._string_or_empty(raw.get("strategy_notes")),
            next_scheduled_tick=next_tick,
            metadata=cls._normalize_mapping(raw.get("metadata")),
        )

    @classmethod
    def _normalize_directive(
        cls,
        raw: Any,
    ) -> tuple[str, dict[str, Any]] | None:
        if isinstance(raw, PlanningDirective):
            kind = cls._coerce_non_empty_string(raw.kind)
            if kind is None:
                return None
            return kind, cls._normalize_mapping(raw.payload)
        if isinstance(raw, CreateQuestPlan):
            return "create_quest", cls._merge_payload({"quest_id": raw.quest_id}, raw.payload)
        if isinstance(raw, DirectNpcPlan):
            return "direct_npc", {
                "npc_id": raw.npc_id,
                "directive": dict(raw.directive) if isinstance(raw.directive, Mapping) else {},
            }
        if isinstance(raw, PublishBulletinPlan):
            return "publish_bulletin", cls._merge_payload(
                {"board_id": raw.board_id},
                raw.payload,
            )
        if isinstance(raw, EscalatePlan):
            return "escalate", cls._merge_payload({"delta": raw.delta}, raw.payload)
        if isinstance(raw, AdjustPacingPlan):
            return "adjust_pacing", cls._merge_payload({"frozen": raw.frozen}, raw.payload)
        if isinstance(raw, RetireQuestPlan):
            return "retire_quest", cls._merge_payload({"quest_id": raw.quest_id}, raw.payload)
        if isinstance(raw, SpawnQuestNpcPlan):
            return "spawn_quest_npc", cls._merge_payload({"npc_id": raw.npc_id}, raw.payload)
        if isinstance(raw, PlantEnvironmentalPlan):
            return "plant_environmental", cls._merge_payload({"area_id": raw.area_id}, raw.payload)
        if isinstance(raw, FillAreaPlan):
            return "fill_area", cls._merge_payload({"area_id": raw.area_id}, raw.payload)
        if not isinstance(raw, Mapping):
            return None
        kind = cls._coerce_non_empty_string(raw.get("kind"))
        if kind is None:
            return None
        return kind, cls._normalize_mapping(raw.get("payload"))

    def _apply_directive(
        self,
        kind: str,
        payload: dict[str, Any],
        context: SettlementContext,
        *,
        current_tick: int,
    ) -> bool:
        if kind == "create_quest":
            quest_id = self._coerce_non_empty_string(payload.get("quest_id"))
            if quest_id is None:
                return False
            if quest_id in context.state.quests.dynamic_quests:
                return False
            status = self._coerce_non_empty_string(payload.get("status")) or "available"
            quest_payload = {
                "quest_id": quest_id,
                "status": status,
                "title": self._string_or_empty(payload.get("title")),
                "summary": self._string_or_empty(payload.get("summary")),
                "source": "narrative_planner",
                "created_at_tick": current_tick,
                "metadata": self._normalize_mapping(payload.get("metadata")),
            }
            context.state.quests.add_dynamic_quest(quest_id, quest_payload)
            context.state.narrative_plan.add_history(
                {"kind": "create_quest", "quest_id": quest_id, "tick": current_tick}
            )
            return True

        if kind == "direct_npc":
            npc_id = self._coerce_non_empty_string(payload.get("npc_id"))
            if npc_id is None:
                return False
            directive = payload.get("directive")
            if directive is not None and not isinstance(directive, Mapping):
                return False
            context.state.narrative_plan.add_directive(
                {
                    "npc_id": npc_id,
                    "directive": dict(directive) if isinstance(directive, Mapping) else {},
                    "issued_at_tick": current_tick,
                    "source": "narrative_planner",
                }
            )
            return True

        if kind == "publish_bulletin":
            board_id = self._coerce_non_empty_string(payload.get("board_id"))
            if board_id is None:
                return False
            context.state.narrative_plan.add_bulletin(
                {
                    "board_id": board_id,
                    "title": self._string_or_empty(payload.get("title")),
                    "content": self._string_or_empty(payload.get("content")),
                    "metadata": self._normalize_mapping(payload.get("metadata")),
                    "published_at_tick": current_tick,
                    "source": "narrative_planner",
                }
            )
            return True

        if kind == "escalate":
            delta = payload.get("delta")
            if not isinstance(delta, int) or isinstance(delta, bool):
                return False
            if delta < -3 or delta > 3:
                return False
            context.state.narrative_plan.adjust_escalation(delta)
            return True

        if kind == "adjust_pacing":
            frozen = payload.get("frozen")
            if not isinstance(frozen, bool):
                return False
            context.state.narrative_plan.set_pacing_frozen(frozen)
            return True

        if kind == "retire_quest":
            quest_id = self._coerce_non_empty_string(payload.get("quest_id"))
            if quest_id is None:
                return False
            if quest_id not in context.state.quests.dynamic_quests:
                return False
            context.state.quests.retire_dynamic_quest(quest_id)
            context.state.narrative_plan.add_history(
                {"kind": "retire_quest", "quest_id": quest_id, "tick": current_tick}
            )
            return True

        if kind == "spawn_quest_npc":
            npc_id = self._coerce_non_empty_string(payload.get("npc_id"))
            if npc_id is None:
                return False
            if not context.state.has_slice("areas"):
                return False
            area_id = self._coerce_non_empty_string(payload.get("area_id"))
            if area_id is None and context.state.has_slice("player"):
                area_id = context.state.player.current_area
            if not area_id or area_id not in context.state.areas.areas:
                return False
            if context.state.areas.find_npc_area(npc_id) is not None:
                return False
            location_id = self._coerce_non_empty_string(payload.get("location_id"))
            context.state.areas.move_npc(npc_id, area_id, location_id)
            context.state.narrative_plan.add_directive({
                "npc_id": npc_id,
                "directive": {
                    "kind": "spawn_quest_npc",
                    "role": self._string_or_empty(payload.get("role")),
                    "description": self._string_or_empty(payload.get("description")),
                    "area_id": area_id,
                },
                "issued_at_tick": current_tick,
                "source": "narrative_planner",
            })
            return True

        if kind == "plant_environmental":
            area_id = self._coerce_non_empty_string(payload.get("area_id"))
            if area_id is None:
                return False
            if not context.state.has_slice("areas"):
                return False
            if area_id not in context.state.areas.areas:
                return False
            clue_id = self._coerce_non_empty_string(payload.get("clue_id"))
            if clue_id is None:
                clue_id = f"clue_{current_tick}"
            area = context.state.areas.areas[area_id]
            search_targets = dict(area.properties.get("search_targets", {}))
            if clue_id in search_targets:
                return False
            raw_dc = payload.get("dc", 12)
            try:
                dc = int(raw_dc)
            except (TypeError, ValueError):
                dc = 12
            search_targets[clue_id] = {
                "dc": dc,
                "description": self._string_or_empty(payload.get("description")),
            }
            context.state.areas.modify_property(area_id, "search_targets", search_targets)
            return True

        if kind == "fill_area":
            area_id = self._coerce_non_empty_string(payload.get("area_id"))
            if area_id is None:
                return False
            if not context.state.has_slice("areas"):
                return False
            if area_id not in context.state.areas.areas:
                return False
            if not context.state.areas.has_cluster_capacity(area_id):
                return False
            sub_area_id = self._coerce_non_empty_string(payload.get("id"))
            if sub_area_id is None:
                sub_area_id = f"fill_{current_tick}"
            context.state.areas.add_temporary_sub_area(area_id, {
                "id": sub_area_id,
                "label": self._string_or_empty(payload.get("label")),
                "description": self._string_or_empty(payload.get("description")),
                "expiry": -1,
                "source": "narrative_planner",
                "created_at_tick": current_tick,
            })
            return True

        return False

    @staticmethod
    def _noop_metadata(
        *,
        reason: str,
        current_tick: int = 0,
        ticks_since_last_run: int = 0,
    ) -> dict[str, Any]:
        return {
            "status": "noop",
            "evaluated": False,
            "reason": reason,
            "current_tick": current_tick,
            "ticks_since_last_run": ticks_since_last_run,
            "requested_count": 0,
            "applied_count": 0,
            "skipped_unsupported_count": 0,
            "skipped_invalid_count": 0,
            "applied_kinds": [],
            "planner_metadata": {},
        }

    @staticmethod
    def _coerce_non_empty_string(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None

    @classmethod
    def _string_or_empty(cls, value: Any) -> str:
        return cls._coerce_non_empty_string(value) or ""

    @staticmethod
    def _normalize_mapping(value: Any) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            return {}
        return {str(key): raw_value for key, raw_value in value.items()}

    @classmethod
    def _merge_payload(
        cls,
        base: Mapping[str, Any],
        extra: Any,
    ) -> dict[str, Any]:
        merged = cls._normalize_mapping(extra)
        result = dict(merged)
        for key, value in base.items():
            result[str(key)] = value
        return result
