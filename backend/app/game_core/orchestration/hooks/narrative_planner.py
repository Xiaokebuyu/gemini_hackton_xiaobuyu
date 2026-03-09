"""NarrativePlannerHook implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import TYPE_CHECKING, Any, Mapping, Protocol

from app.game_core.adapters.planner_system import PlannerBlackboardPort
from app.game_core.orchestration.event_engine import _normalize_mapping, run_inline_event_check
from app.game_core.orchestration.hooks.base import NoOpSettlementHook
from app.game_core.rules.models import Command
from app.game_core.orchestration.hooks.rest_phase import (
    is_quiet_rest_slot,
    resolve_rest_phase,
)
from app.game_core.orchestration.models import HookResult, SSEEvent
from app.game_core.orchestration.settlement import SettlementContext
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
from app.game_core.planning.semantic_events import (
    collect_planner_events,
    planner_event_snapshot,
)
from app.game_core.state import StateChange

from app.game_core.planning.subsystem import PlannerEvent

if TYPE_CHECKING:
    from app.game_core.planning.subsystem import PlannerDispatcher


logger = logging.getLogger(__name__)

_TICK_KIND_TRAVEL = frozenset({"move_area", "enter_sub_location", "leave_sub_location"})
_TICK_KIND_REST = frozenset({"rest_short", "rest_long", "night_watch", "set_camp"})
_TICK_KIND_CONVERSATION = frozenset(
    {
        "speak",
        "dialogue",
        "talk",
        "emote",
        "dialogue_turn",
        "public_utterance_turn",
        "party_chat_turn",
        "free_chat_turn",
        "private_chat_turn",
    }
)
_TICK_KIND_COMBAT = frozenset(
    {
        "attack",
        "defend",
        "disengage",
        "dash",
        "shove",
        "flee",
        "offhand_attack",
        "stand_up",
        "use_combat_item",
        "saving_throw",
        "contest",
    }
)


@dataclass(slots=True)
class NarrativePlannerDecision:
    directives: list[Any] = field(default_factory=list)
    story_facts: list[dict[str, Any]] = field(default_factory=list)
    strategy_notes: str = ""
    next_scheduled_tick: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class NarrativePlannerProvider(Protocol):
    async def plan(self, context: dict[str, Any]) -> Any:
        ...


def _detect_failed_milestones_sse(
    context: "SettlementContext",
) -> list[SSEEvent]:
    """Scan change_log for FAILED milestone transitions and emit milestone_failed SSE."""
    extra_sse: list[SSEEvent] = []
    for change in context.change_log:
        if not (
            change.slice == "quests"
            and change.path.startswith("milestone_states.")
            and isinstance(change.value, dict)
            and change.value.get("state") == "FAILED"
        ):
            continue
        milestone_id = change.path[len("milestone_states."):]
        fallback: str | None = None
        if context.world.has_registry("quests"):
            tmpl = context.world.quests.get_milestone(milestone_id)
            fallback = tmpl.failure_fallback if tmpl else None
        extra_sse.append(SSEEvent(
            event_type="milestone_failed",
            payload={
                "milestone_id": milestone_id,
                "failure_fallback": fallback or "",
            },
        ))
    return extra_sse


def _detect_completed_milestones_sse(
    context: "SettlementContext",
) -> list[SSEEvent]:
    """Scan change_log for COMPLETED milestone transitions.

    For each completed milestone:
    - Emits a ``milestone_completed`` SSE event.
    - Cascade-unlocks next_milestones that are still LOCKED.
    - Updates chapter_completion via execute_command.

    Returns list of SSE events to append (does NOT mutate sse_events directly).
    """
    extra_sse: list[SSEEvent] = []
    for change in context.change_log:
        ms_state_value = change.value
        if isinstance(ms_state_value, dict):
            completed_state = ms_state_value.get("state")
        elif isinstance(ms_state_value, str):
            completed_state = ms_state_value
        else:
            completed_state = None
        if not (
            change.slice == "quests"
            and change.path.startswith("milestone_states.")
            and completed_state == "COMPLETED"
        ):
            continue

        milestone_id = change.path[len("milestone_states."):]
        template = None
        if context.world.has_registry("quests"):
            template = context.world.quests.get_milestone(milestone_id)

        # 1. Emit milestone_completed SSE
        extra_sse.append(SSEEvent(
            event_type="milestone_completed",
            payload={
                "milestone_id": milestone_id,
                "title": template.title if template else milestone_id,
                "completion_value": template.completion_value if template else 0,
            },
        ))

        if template is None:
            continue

        # 2. Cascade-unlock next_milestones that are still LOCKED
        for next_id in template.next_milestones:
            next_ms = context.state.quests.get_milestone(next_id)
            if next_ms is not None and next_ms.state == "LOCKED":
                context.execute_command(Command(
                    type="advance_quest",
                    params={
                        "quest_id": next_id,
                        "to_state": "AVAILABLE",
                        "quest_kind": "milestone",
                    },
                    source="system",
                ))

        # 3. Update chapter_completion
        if template.chapter_id:
            context.execute_command(Command(
                type="modify_completion",
                params={
                    "chapter_id": template.chapter_id,
                    "delta": template.completion_value / 100.0,
                },
                source="system",
            ))

    return extra_sse


class NarrativePlannerHook(NoOpSettlementHook):
    HOOK_PRIORITY = 66
    HOOK_NAME = "narrative_planner"
    FALLBACK_INTERVAL = 6
    _MAX_REPLAY_ROUNDS = 5
    _BOOTSTRAP_DIRECTIVES = {
        "create_quest",
        "direct_npc",
        "publish_bulletin",
    }

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
        "update_quest",
        "curate_shop",
    }
    def __init__(
        self,
        planner: NarrativePlannerProvider | None = None,
        *,
        blackboard: PlannerBlackboardPort | None = None,
        dispatcher: PlannerDispatcher | None = None,
    ) -> None:
        self.blackboard = blackboard if blackboard is not None else planner
        self._dispatcher = dispatcher
        # Scratch buffer for SSE events; written by WorldBuilderSubSystem (via reference),
        # drained by execute() at the end of each planning cycle.
        self._pending_sse: list[SSEEvent] = []

    @property
    def planner(self) -> NarrativePlannerProvider | None:
        """Backward-compatible alias for the central blackboard."""
        return self.blackboard

    @planner.setter
    def planner(self, value: NarrativePlannerProvider | None) -> None:
        self.blackboard = value

    def should_skip(
        self,
        change_log: list[StateChange],
        action_log: list[dict[str, Any]] | None = None,
    ) -> bool:
        del action_log
        del change_log
        return False

    async def execute(self, context: SettlementContext) -> HookResult:
        # Reset per-call SSE scratch buffer in-place so WorldBuilderSubSystem's
        # reference (captured at construction) stays valid.
        self._pending_sse.clear()

        if not context.state.has_slice("narrative_plan"):
            return HookResult(metadata=self._noop_metadata(reason="missing_slice"))
        if not context.state.has_slice("quests"):
            return HookResult(metadata=self._noop_metadata(reason="missing_slice"))
        if not context.state.has_slice("time"):
            return HookResult(metadata=self._noop_metadata(reason="missing_slice"))

        current_tick = context.state.time.absolute_tick()
        ticks_since_last_run = max(0, current_tick - context.state.narrative_plan.last_run_tick)
        triggered = self._has_trigger_change(context.change_log)
        rest_phase = resolve_rest_phase(context)
        if is_quiet_rest_slot(context, rest_phase) and not triggered:
            return HookResult(
                metadata=self._noop_metadata(
                    reason="quiet_rest_slot",
                    current_tick=current_tick,
                    ticks_since_last_run=ticks_since_last_run,
                )
            )
        if not triggered and ticks_since_last_run < self.FALLBACK_INTERVAL:
            return HookResult(
                metadata=self._noop_metadata(
                    reason="cooldown",
                    current_tick=current_tick,
                    ticks_since_last_run=ticks_since_last_run,
                )
            )

        if self.blackboard is None and self._dispatcher is None:
            # Still detect and cascade COMPLETED/FAILED milestones even with no planner
            milestone_sse = (
                _detect_failed_milestones_sse(context)
                + _detect_completed_milestones_sse(context)
            )
            return HookResult(
                sse_events=milestone_sse,
                metadata=self._noop_metadata(reason="no_planner"),
            )

        reason = "trigger" if triggered else "fallback"
        replay = await self._run_replay(
            context,
            current_tick=current_tick,
            reason=reason,
            allowed_directives=self._SUPPORTED_DIRECTIVES,
            initial_change_window_start=0,
            include_action_log=True,
            include_tick_event=True,
        )
        requested_count = replay["requested_count"]
        applied_count = replay["applied_count"]
        skipped_unsupported_count = replay["skipped_unsupported_count"]
        skipped_invalid_count = replay["skipped_invalid_count"]
        applied_kinds = replay["applied_kinds"]
        subsystem_story_fact_count = replay["story_fact_count"]
        replay_trace = replay["trace"]
        planner_event_summaries = replay["planner_event_summaries"]

        context.state.narrative_plan.set_last_planner_replay_trace(replay_trace)

        blackboard_decision = NarrativePlannerDecision()
        if self.blackboard is not None:
            post_dispatch_context = self._build_planner_context(context, current_tick=current_tick)
            post_dispatch_context["planner_events"] = planner_event_summaries
            post_dispatch_context["replay_trace"] = replay_trace
            self._inject_runtime_refs(post_dispatch_context, context)
            try:
                raw_decision = await self.blackboard.plan(post_dispatch_context)
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
                        "requested_count": requested_count,
                        "applied_count": applied_count,
                        "skipped_unsupported_count": skipped_unsupported_count,
                        "skipped_invalid_count": skipped_invalid_count,
                        "story_fact_count": subsystem_story_fact_count,
                        "applied_kinds": applied_kinds,
                        "planner_metadata": {},
                        "replay_round_count": replay_trace.get("round_count", 0),
                        "replay_stop_reason": replay_trace.get("stop_reason", "error"),
                        "replay_event_count": len(planner_event_summaries),
                        "replay_applied_directive_count": applied_count,
                    },
                )
            blackboard_decision = self._normalize_decision(raw_decision)

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

        blackboard_story_fact_count = self._apply_story_facts(
            blackboard_decision.story_facts,
            context,
        )
        story_fact_count = subsystem_story_fact_count + blackboard_story_fact_count

        context.state.narrative_plan.last_run_tick = current_tick
        context.state.narrative_plan.ticks_since_milestone_progress = progress_value
        context.state.narrative_plan._dirty = True
        if blackboard_decision.strategy_notes:
            context.state.narrative_plan.set_strategy(blackboard_decision.strategy_notes)
        if (
            isinstance(blackboard_decision.next_scheduled_tick, int)
            and blackboard_decision.next_scheduled_tick >= current_tick
        ):
            context.state.narrative_plan.schedule_next(blackboard_decision.next_scheduled_tick)
        final_context = self._build_planner_context(context, current_tick=current_tick)
        context.state.narrative_plan.record_behavior(
            {
                "tick": current_tick,
                "changed_slices": final_context["changed_slices"],
                "reason": reason,
                "directive_count": requested_count,
            }
        )
        derived_style_tags = self._derive_play_style_tags(
            context.state.narrative_plan.behavior_window
        )
        if derived_style_tags != context.state.narrative_plan.play_style_tags:
            context.state.narrative_plan.play_style_tags = list(derived_style_tags)
            context.state.narrative_plan._dirty = True

        sse_events: list[SSEEvent] = list(self._pending_sse)
        self._pending_sse.clear()
        if applied_count > 0:
            sse_events.append(
                SSEEvent(
                    event_type="narrative_plan_updated",
                    payload={
                        "applied_count": applied_count,
                        "story_fact_count": story_fact_count,
                        "applied_kinds": list(applied_kinds),
                        "current_tick": current_tick,
                    },
                )
            )
        # Detect FAILED and COMPLETED milestone transitions
        sse_events.extend(_detect_failed_milestones_sse(context))
        sse_events.extend(_detect_completed_milestones_sse(context))

        # Drain SSE events emitted by sub-systems during dispatch
        sse_events.extend(self._pending_sse)
        self._pending_sse.clear()

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
                "story_fact_count": story_fact_count,
                "applied_kinds": applied_kinds,
                "planner_metadata": dict(blackboard_decision.metadata),
                "replay_round_count": replay_trace.get("round_count", 0),
                "replay_stop_reason": replay_trace.get("stop_reason", "steady_state"),
                "replay_event_count": len(planner_event_summaries),
                "replay_applied_directive_count": applied_count,
            },
        )

    async def bootstrap(self, context: SettlementContext) -> HookResult:
        """Seed opening quests without advancing normal planner bookkeeping."""
        self._pending_sse.clear()

        if not context.state.has_slice("narrative_plan"):
            return HookResult(metadata=self._noop_metadata(reason="missing_slice"))
        if not context.state.has_slice("quests"):
            return HookResult(metadata=self._noop_metadata(reason="missing_slice"))
        if not context.state.has_slice("time"):
            return HookResult(metadata=self._noop_metadata(reason="missing_slice"))

        current_tick = context.state.time.absolute_tick()
        replay = await self._run_replay(
            context,
            current_tick=current_tick,
            reason="bootstrap",
            allowed_directives=self._BOOTSTRAP_DIRECTIVES,
            initial_change_window_start=len(context.change_log),
            include_action_log=False,
            include_tick_event=False,
            seed_events=[
                PlannerEvent(
                    kind="bootstrap",
                    tick=current_tick,
                    source="hook",
                    priority=0,
                    dedupe_key="bootstrap",
                    emitter="hook",
                )
            ],
        )
        requested_count = replay["requested_count"]
        applied_count = replay["applied_count"]
        skipped_unsupported_count = replay["skipped_unsupported_count"]
        skipped_invalid_count = replay["skipped_invalid_count"]
        applied_kinds = replay["applied_kinds"]
        story_fact_count = replay["story_fact_count"]
        replay_trace = replay["trace"]
        planner_event_summaries = replay["planner_event_summaries"]

        context.state.narrative_plan.set_last_planner_replay_trace(replay_trace)

        blackboard_metadata: dict[str, Any] = {}
        if self.blackboard is not None:
            blackboard_context = self._build_planner_context(context, current_tick=current_tick)
            blackboard_context["planner_events"] = planner_event_summaries
            blackboard_context["replay_trace"] = replay_trace
            self._inject_runtime_refs(blackboard_context, context)
            try:
                raw_decision = await self.blackboard.plan(blackboard_context)
            except Exception as exc:
                logger.exception(
                    "hook failed: narrative_planner_bootstrap",
                    extra={
                        "hook_name": "narrative_planner_bootstrap",
                        "current_tick": current_tick,
                    },
                )
                return HookResult(
                    sse_events=[
                        SSEEvent(
                            event_type="hook_error",
                            payload={
                                "hook": "narrative_planner_bootstrap",
                                "error_type": type(exc).__name__,
                                "message": str(exc),
                            },
                        )
                    ],
                    metadata={
                        "status": "planner_error",
                        "evaluated": False,
                        "reason": "bootstrap",
                        "current_tick": current_tick,
                        "requested_count": requested_count,
                        "applied_count": applied_count,
                        "skipped_unsupported_count": skipped_unsupported_count,
                        "skipped_invalid_count": skipped_invalid_count,
                        "story_fact_count": story_fact_count,
                        "applied_kinds": applied_kinds,
                        "planner_metadata": {},
                        "replay_round_count": replay_trace.get("round_count", 0),
                        "replay_stop_reason": replay_trace.get("stop_reason", "error"),
                        "replay_event_count": len(planner_event_summaries),
                        "replay_applied_directive_count": applied_count,
                    },
                )
            blackboard_decision = self._normalize_decision(raw_decision)
            story_fact_count += self._apply_story_facts(blackboard_decision.story_facts, context)
            blackboard_metadata = dict(blackboard_decision.metadata)
            if blackboard_decision.strategy_notes:
                context.state.narrative_plan.set_strategy(blackboard_decision.strategy_notes)
            if (
                isinstance(blackboard_decision.next_scheduled_tick, int)
                and blackboard_decision.next_scheduled_tick >= current_tick
            ):
                context.state.narrative_plan.schedule_next(blackboard_decision.next_scheduled_tick)
        sse_events = list(self._pending_sse)
        self._pending_sse.clear()
        return HookResult(
            sse_events=sse_events,
            metadata={
                "status": "updated" if (applied_count > 0 or story_fact_count > 0) else "noop",
                "evaluated": True,
                "reason": "bootstrap",
                "current_tick": current_tick,
                "requested_count": requested_count,
                "applied_count": applied_count,
                "skipped_unsupported_count": skipped_unsupported_count,
                "skipped_invalid_count": skipped_invalid_count,
                "story_fact_count": story_fact_count,
                "applied_kinds": applied_kinds,
                "planner_metadata": blackboard_metadata,
                "replay_round_count": replay_trace.get("round_count", 0),
                "replay_stop_reason": replay_trace.get("stop_reason", "bootstrap_complete"),
                "replay_event_count": len(planner_event_summaries),
                "replay_applied_directive_count": applied_count,
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

        play_style_tags: list[str] = []
        if hasattr(context.state.narrative_plan, "play_style_tags"):
            raw_play_style_tags = context.state.narrative_plan.play_style_tags
            if isinstance(raw_play_style_tags, list):
                play_style_tags = [str(tag) for tag in raw_play_style_tags]

        area_npcs: list[str] = []
        area_boards: list[dict[str, str]] = []
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
                area_state = context.state.areas.areas[area_id]
                area_npcs = [
                    str(npc_id)
                    for npc_id in area_state.npc_locations.keys()
                    if self._coerce_non_empty_string(npc_id) is not None
                ]
                if context.world.has_registry("maps"):
                    area_tmpl = context.world.maps.get(area_id)
                    if area_tmpl is not None:
                        for sub_id, sub_tmpl in area_tmpl.sub_locations.items():
                            for interactable in getattr(sub_tmpl, "interactables", []):
                                board_id = self._coerce_non_empty_string(
                                    getattr(interactable, "id", None)
                                )
                                if board_id is None:
                                    continue
                                raw_tags = getattr(interactable, "tags", [])
                                if not isinstance(raw_tags, list):
                                    continue
                                if "quest_source" not in raw_tags:
                                    continue
                                area_boards.append({
                                    "id": board_id,
                                    "sub_location": str(sub_id),
                                })

        scene_snapshot = context.scene_bus.snapshot()
        world_id = location.get("area_id", "")
        world_context: dict[str, Any] = {}
        if context.world.has_registry("maps") and world_id:
            area_tmpl = context.world.maps.get(world_id)
            if area_tmpl is not None:
                world_context["area_description"] = area_tmpl.description
        if context.world.has_registry("factions") and world_id:
            factions = context.world.factions.get_factions_in_area(world_id)
            relevant_factions = [
                {"id": str(f.id), "name": str(f.name)}
                for f in factions
                if f.id
            ]
            if relevant_factions:
                world_context["relevant_factions"] = relevant_factions
        if context.world.has_registry("lore"):
            chapter = context.state.narrative_plan.current_chapter
            faction_ids = [
                f.get("id")
                for f in world_context.get("relevant_factions", [])
                if f.get("id")
            ]
            rules = context.world.lore.get_rules_for_context(
                chapter_id=str(chapter or ""),
                area_id=world_id,
                faction_ids=faction_ids or None,
            )
            world_context["world_rules"] = [
                {
                    "id": str(rule.id),
                    "title": str(rule.title),
                    "description": str(rule.description),
                }
                for rule in rules[:5]
            ]
        system_entries_digest = self._build_system_entries_digest(scene_snapshot)
        visible_command_types = [
            entry["command_type"]
            for entry in system_entries_digest
            if isinstance(entry.get("command_type"), str) and entry["command_type"]
        ]
        pending_events_digest = self._build_pending_events_digest(context)
        rest_phase = resolve_rest_phase(context)
        rest_phase_snapshot = rest_phase.snapshot() if rest_phase is not None else None
        if rest_phase_snapshot is not None:
            rest_phase_snapshot["is_quiet_rest_slot"] = is_quiet_rest_slot(context, rest_phase)
        return {
            "current_tick": current_tick,
            "maps": getattr(context.world, "maps", None) if context.world.has_registry("maps") else None,
            "time": {
                "day": context.state.time.day,
                "slot": context.state.time.slot,
                "period": context.state.time.period,
                "absolute_tick": current_tick,
            },
            "tick_kind": self._build_tick_kind(context.action_log),
            "rest_phase": rest_phase_snapshot,
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
            "area_npcs": area_npcs,
            "area_boards": area_boards,
            "party": [
                {"id": str(member_id)}
                for member_id in getattr(context.state.party, "members", {}).keys()
            ] if context.state.has_slice("party") else [],
            "play_style_tags": play_style_tags,
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
                "behavior_window": list(context.state.narrative_plan.behavior_window),
                "npc_directives": [
                    {
                        "npc_id": d.get("npc_id", ""),
                        "kind": d.get("directive", {}).get("kind", ""),
                        "priority": d.get("priority", "medium"),
                        "issued_at_tick": d.get("issued_at_tick", 0),
                    }
                    for d in context.state.narrative_plan.npc_directives
                    if not d.get("consumed", False)
                    and d.get("expires_at_tick", current_tick + 1) >= current_tick
                ],
            },
            "area_cluster": area_cluster,
            "scene": {
                "entry_count": len(scene_snapshot.get("entries", [])),
                "state_change_count": len(scene_snapshot.get("state_changes", [])),
                "system_entries_digest": system_entries_digest,
                "visible_command_types": visible_command_types,
            },
            "events": {
                "pending_events_digest": pending_events_digest,
            },
            "world_context": world_context,
            "maps": context.world.maps if context.world.has_registry("maps") else None,
            "target_milestone_detail": self._build_target_milestone_detail(context),
            "story_facts": list(context.state.narrative_plan.story_facts),
            "danger_level": self._get_area_danger(context),
        }

    @staticmethod
    def _build_tick_kind(action_log: list[dict[str, Any]]) -> str:
        action_types = {
            str(raw_action.get("type", "")).strip().lower()
            for raw_action in action_log
            if isinstance(raw_action, Mapping)
        } - {""}
        if not action_types:
            return "normal"
        if action_types & _TICK_KIND_TRAVEL:
            return "travel"
        if action_types & _TICK_KIND_REST:
            return "rest"
        if action_types & _TICK_KIND_CONVERSATION:
            return "conversation"
        if action_types & _TICK_KIND_COMBAT:
            return "combat_resolution"
        return "normal"

    @staticmethod
    def _build_system_entries_digest(scene_snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
        raw_entries = scene_snapshot.get("entries", [])
        if not isinstance(raw_entries, list):
            return []
        digest: list[dict[str, Any]] = []
        for entry in raw_entries:
            if not isinstance(entry, Mapping):
                continue
            if str(entry.get("visibility", "")).strip() != "system":
                continue
            tags = entry.get("tags", [])
            if not isinstance(tags, list):
                tags = []
            metadata = entry.get("metadata", {})
            if not isinstance(metadata, Mapping):
                metadata = {}
            digest.append({
                "source": str(entry.get("source", "")).strip(),
                "content": str(entry.get("content", "")).strip(),
                "tags": [str(tag) for tag in tags if isinstance(tag, str)],
                "command_type": str(metadata.get("command_type", "")).strip(),
                "reason": str(metadata.get("reason", "")).strip(),
                "visibility_hint": str(metadata.get("visibility_hint", "")).strip(),
                "confidence": str(metadata.get("confidence", "")).strip(),
                "refs": _normalize_mapping(metadata.get("refs", {})),
            })
        return digest[:5]

    @staticmethod
    def _build_pending_events_digest(context: SettlementContext) -> list[dict[str, Any]]:
        if not context.state.has_slice("events"):
            return []
        snapshot = context.state.events.snapshot()
        raw_pending = snapshot.get("pending_events", [])
        if not isinstance(raw_pending, list):
            return []
        digest: list[dict[str, Any]] = []
        for event in raw_pending:
            if not isinstance(event, Mapping):
                continue
            trigger = event.get("trigger_condition", {})
            digest.append({
                "event_id": str(event.get("event_id", "")).strip(),
                "event_type": str(event.get("event_type", "")).strip(),
                "source": str(event.get("source", "")).strip(),
                "trigger_condition": _normalize_mapping(trigger),
            })
        return digest[:5]

    def _derive_play_style_tags(self, window: list[dict[str, Any]]) -> list[str]:
        """从行为窗口推导玩家风格标签。"""
        if len(window) < 6:
            return []
        action_counts: dict[str, int] = {}
        for entry in window:
            action = self._coerce_non_empty_string(entry.get("action_type"))
            if action is None:
                continue
            action_counts[action] = action_counts.get(action, 0) + 1
        total = sum(action_counts.values()) or 1
        tags: list[str] = []
        if action_counts.get("dialogue", 0) / total > 0.35:
            tags.append("DIALOGUE_HEAVY")
        if action_counts.get("combat", 0) / total > 0.35:
            tags.append("COMBAT_FOCUSED")
        if action_counts.get("start_combat", 0) / total > 0.35:
            tags.append("COMBAT_FOCUSED")
        if action_counts.get("end_combat", 0) / total > 0.35:
            tags.append("COMBAT_FOCUSED")
        if action_counts.get("explore", 0) / total > 0.25:
            tags.append("EXPLORER")
        if action_counts.get("navigate", 0) / total > 0.25:
            tags.append("EXPLORER")
        if action_counts.get("trade", 0) / total > 0.2:
            tags.append("TRADER")
        if action_counts.get("rest", 0) / total > 0.2:
            tags.append("TRADER")
        # 去重并保持顺序
        seen: set[str] = set()
        deduped: list[str] = []
        for tag in tags:
            if tag in seen:
                continue
            seen.add(tag)
            deduped.append(tag)
        return deduped

    def _apply_normalized_decision(
        self,
        decision: NarrativePlannerDecision,
        context: SettlementContext,
        *,
        current_tick: int,
        allowed_directives: set[str],
    ) -> tuple[int, int, int, int, list[str]]:
        requested_count = len(decision.directives)
        applied_count = 0
        skipped_unsupported_count = 0
        skipped_invalid_count = 0
        applied_kinds: list[str] = []

        if self._dispatcher is None:
            logger.warning(
                "NarrativePlannerHook: no dispatcher configured, directives will be dropped"
            )
            return (len(decision.directives), 0, 0, len(decision.directives), [])

        apply_fn = self._dispatcher.apply_directive

        for raw_directive in decision.directives:
            normalized = self._normalize_directive(raw_directive)
            if normalized is None:
                skipped_invalid_count += 1
                continue
            kind, payload = normalized
            if kind not in allowed_directives:
                skipped_unsupported_count += 1
                continue
            if not apply_fn(kind, payload, context, current_tick=current_tick):
                skipped_invalid_count += 1
                continue
            applied_count += 1
            applied_kinds.append(kind)
        return (
            requested_count,
            applied_count,
            skipped_unsupported_count,
            skipped_invalid_count,
            applied_kinds,
        )

    def _apply_subsystem_results(
        self,
        results: list[Any],
        context: SettlementContext,
        *,
        current_tick: int,
        allowed_directives: set[str],
    ) -> tuple[int, int, int, int, list[str]]:
        requested_count = 0
        applied_count = 0
        skipped_unsupported_count = 0
        skipped_invalid_count = 0
        applied_kinds: list[str] = []

        if self._dispatcher is None:
            return (0, 0, 0, 0, [])

        for result in results:
            directives = result.directives if hasattr(result, "directives") else []
            if not isinstance(directives, list):
                continue
            requested_count += len(directives)
            for raw_directive in directives:
                normalized = self._normalize_directive(raw_directive)
                if normalized is None:
                    skipped_invalid_count += 1
                    continue
                kind, payload = normalized
                if kind not in allowed_directives:
                    skipped_unsupported_count += 1
                    continue
                if not self._dispatcher.apply_directive(
                    kind,
                    payload,
                    context,
                    current_tick=current_tick,
                ):
                    skipped_invalid_count += 1
                    continue
                applied_count += 1
                applied_kinds.append(kind)
        return (
            requested_count,
            applied_count,
            skipped_unsupported_count,
            skipped_invalid_count,
            applied_kinds,
        )

    def _apply_subsystem_story_facts(
        self,
        results: list[Any],
        context: SettlementContext,
    ) -> int:
        count = 0
        for result in results:
            story_facts = result.story_facts if hasattr(result, "story_facts") else []
            if not isinstance(story_facts, list) or not story_facts:
                continue
            count += self._apply_story_facts(
                self._normalize_story_facts(story_facts),
                context,
            )
        return count

    @staticmethod
    def _attach_planner_context(
        event: PlannerEvent,
        *,
        planner_context: dict[str, Any],
        extra_payload: Mapping[str, Any] | None = None,
    ) -> PlannerEvent:
        payload = dict(event.payload)
        payload["planner_context"] = planner_context
        if isinstance(extra_payload, Mapping):
            for key, value in extra_payload.items():
                payload.setdefault(str(key), value)
        return PlannerEvent(
            kind=event.kind,
            tick=event.tick,
            source=event.source,
            priority=event.priority,
            dedupe_key=event.dedupe_key,
            round_index=event.round_index,
            emitter=event.emitter,
            payload=payload,
        )

    @staticmethod
    def _inject_runtime_refs(
        planner_context: dict[str, Any],
        context: SettlementContext,
    ) -> None:
        planner_context["__world__"] = context.world
        planner_context["__state__"] = context.state
        planner_context["__world_id__"] = getattr(context.world, "world_id", "")

    async def _run_replay(
        self,
        context: SettlementContext,
        *,
        current_tick: int,
        reason: str,
        allowed_directives: set[str],
        initial_change_window_start: int,
        include_action_log: bool,
        include_tick_event: bool,
        seed_events: list[PlannerEvent] | None = None,
    ) -> dict[str, Any]:
        requested_count = 0
        applied_count = 0
        skipped_unsupported_count = 0
        skipped_invalid_count = 0
        applied_kinds: list[str] = []
        story_fact_count = 0
        all_events: list[PlannerEvent] = []
        rounds: list[dict[str, Any]] = []
        seen_dedupe_keys: set[str] = set()
        stop_reason = "steady_state"
        change_cursor = initial_change_window_start

        pending_events = self._filter_new_events(
            collect_planner_events(
                context,
                current_tick,
                change_window_start=change_cursor,
                round_index=0,
                include_action_log=include_action_log,
                seed_events=seed_events,
                include_tick_event=include_tick_event,
            ),
            seen_dedupe_keys=seen_dedupe_keys,
        )

        for round_index in range(self._MAX_REPLAY_ROUNDS):
            if not pending_events:
                stop_reason = "bootstrap_complete" if reason == "bootstrap" else "steady_state"
                break

            pre_round_change_count = len(context.change_log)
            round_requested_count = 0
            round_applied_count = 0
            round_skipped_unsupported_count = 0
            round_skipped_invalid_count = 0
            round_applied_kinds: list[str] = []
            accepted_event_count = 0

            for semantic_event in pending_events:
                seen_dedupe_keys.add(semantic_event.dedupe_key)
                all_events.append(semantic_event)

            planner_event_summaries = [
                planner_event_snapshot(event)
                for event in all_events
            ]

            for semantic_event in pending_events:
                event_context = self._build_planner_context(
                    context,
                    current_tick=current_tick,
                )
                event_context["planner_events"] = planner_event_summaries
                self._inject_runtime_refs(event_context, context)
                dispatch_event = self._attach_planner_context(
                    semantic_event,
                    planner_context=event_context,
                    extra_payload={
                        "dispatch_reason": reason,
                        "replay_round_index": round_index,
                    },
                )
                dispatch_results = []
                if self._dispatcher is not None:
                    dispatch_results = await self._dispatcher.dispatch(dispatch_event, context)
                if dispatch_results:
                    accepted_event_count += 1
                (
                    sub_requested,
                    sub_applied,
                    sub_skipped_unsupported,
                    sub_skipped_invalid,
                    sub_applied_kinds,
                ) = self._apply_subsystem_results(
                    dispatch_results,
                    context,
                    current_tick=current_tick,
                    allowed_directives=allowed_directives,
                )
                round_requested_count += sub_requested
                round_applied_count += sub_applied
                round_skipped_unsupported_count += sub_skipped_unsupported
                round_skipped_invalid_count += sub_skipped_invalid
                round_applied_kinds.extend(sub_applied_kinds)
                story_fact_count += self._apply_subsystem_story_facts(
                    dispatch_results,
                    context,
                )

            inline_payloads = run_inline_event_check(
                state=context.state,
                world=context.world,
                rules_engine=context._rules_engine,
                apply_delta=context._apply_delta,
                change_log=context.change_log,
                scene_bus=context.scene_bus,
                label=f"planner_replay_r{round_index}",
                sse_collector=self._pending_sse,
            )
            new_change_count = max(0, len(context.change_log) - pre_round_change_count)
            rounds.append(
                {
                    "round_index": round_index,
                    "event_kinds": [event.kind for event in pending_events],
                    "accepted_event_count": accepted_event_count,
                    "requested_directive_count": round_requested_count,
                    "applied_directive_kinds": list(round_applied_kinds),
                    "new_change_count": new_change_count,
                    "inline_event_transition_count": len(inline_payloads),
                }
            )
            requested_count += round_requested_count
            applied_count += round_applied_count
            skipped_unsupported_count += round_skipped_unsupported_count
            skipped_invalid_count += round_skipped_invalid_count
            applied_kinds.extend(round_applied_kinds)

            change_cursor = pre_round_change_count
            if round_index + 1 >= self._MAX_REPLAY_ROUNDS:
                stop_reason = "max_rounds"
                break

            pending_events = self._filter_new_events(
                collect_planner_events(
                    context,
                    current_tick,
                    change_window_start=change_cursor,
                    round_index=round_index + 1,
                    include_action_log=False,
                    include_tick_event=False,
                ),
                seen_dedupe_keys=seen_dedupe_keys,
            )
            if not pending_events:
                stop_reason = "bootstrap_complete" if reason == "bootstrap" else "steady_state"
                break
            if new_change_count == 0 and not round_applied_kinds and not inline_payloads:
                stop_reason = "bootstrap_complete" if reason == "bootstrap" else "steady_state"
                break

        planner_event_summaries = [
            planner_event_snapshot(event)
            for event in all_events
        ]
        trace = {
            "hook_priority": self.HOOK_PRIORITY,
            "current_tick": current_tick,
            "reason": reason,
            "round_count": len(rounds),
            "stop_reason": stop_reason,
            "rounds": rounds,
        }
        return {
            "requested_count": requested_count,
            "applied_count": applied_count,
            "skipped_unsupported_count": skipped_unsupported_count,
            "skipped_invalid_count": skipped_invalid_count,
            "applied_kinds": applied_kinds,
            "story_fact_count": story_fact_count,
            "planner_event_summaries": planner_event_summaries,
            "trace": trace,
        }

    @staticmethod
    def _filter_new_events(
        events: list[PlannerEvent],
        *,
        seen_dedupe_keys: set[str],
    ) -> list[PlannerEvent]:
        filtered: list[PlannerEvent] = []
        for event in events:
            key = event.dedupe_key or f"{event.kind}:{event.tick}:{event.round_index}"
            if key in seen_dedupe_keys:
                continue
            filtered.append(event)
        return filtered

    def history_participants(self) -> dict[str, Any]:
        participants: dict[str, Any] = {}
        if self.blackboard is not None:
            history_key = getattr(self.blackboard, "history_key", "__planner_blackboard__")
            participants[str(history_key)] = self.blackboard
            if history_key == "__planner__":
                participants["__planner_blackboard__"] = self.blackboard
        if self._dispatcher is None:
            return participants
        for subsystem in self._dispatcher.subsystems:
            agent = getattr(subsystem, "_agent", None)
            if agent is None:
                continue
            history_key = getattr(agent, "history_key", "")
            if history_key:
                participants[str(history_key)] = agent
        return participants

    def _apply_story_facts(
        self,
        facts: list[dict[str, Any]],
        context: SettlementContext,
    ) -> int:
        if not facts or not context.state.has_slice("narrative_plan"):
            return 0
        context.state.narrative_plan.add_story_facts(facts)
        graph = context.knowledge_graph
        if graph is not None:
            try:
                graph.inject_story_facts(facts)
            except Exception:
                logger.exception("failed to inject story_facts into knowledge graph")
        return len(facts)

    def _build_target_milestone_detail(
        self, context: SettlementContext,
    ) -> dict[str, Any]:
        """P3-6: 返回当前目标里程碑的叙事细节，供确定性和 LLM planner 精确决策。"""
        target_milestone_id = context.state.narrative_plan.current_target_milestone
        if not target_milestone_id or not context.world.has_registry("quests"):
            return {}
        template = context.world.quests.get_milestone(target_milestone_id)
        if template is None:
            return {}
        return {
            "key_elements": list(template.key_elements),
            "involved_npcs": list(template.involved_npcs),
            "involved_locations": list(template.involved_locations),
            "narrative_context": template.narrative_context,
            "failure_fallback": template.failure_fallback,
        }

    @staticmethod
    def _get_area_danger(context: SettlementContext) -> float:
        if not context.state.has_slice("areas") or not context.state.has_slice("player"):
            return 0.0
        area_id = context.state.player.current_area
        if not area_id or area_id not in context.state.areas.areas:
            return 0.0
        return context.state.areas.areas[area_id].danger_level

    @classmethod
    def _normalize_decision(cls, raw: Any) -> NarrativePlannerDecision:
        if isinstance(raw, NarrativePlannerDecision):
            return NarrativePlannerDecision(
                directives=list(raw.directives),
                story_facts=cls._normalize_story_facts(raw.story_facts),
                strategy_notes=cls._string_or_empty(raw.strategy_notes),
                next_scheduled_tick=raw.next_scheduled_tick,
                metadata=_normalize_mapping(raw.metadata),
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
            story_facts=cls._normalize_story_facts(raw.get("story_facts")),
            strategy_notes=cls._string_or_empty(raw.get("strategy_notes")),
            next_scheduled_tick=next_tick,
            metadata=_normalize_mapping(raw.get("metadata")),
        )

    @classmethod
    def _normalize_story_facts(cls, raw: Any) -> list[dict[str, Any]]:
        if not isinstance(raw, list):
            return []
        facts: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            subject = cls._coerce_non_empty_string(item.get("subject"))
            relation = cls._coerce_non_empty_string(item.get("relation"))
            obj = cls._coerce_non_empty_string(item.get("object"))
            if subject is None or relation is None or obj is None:
                continue
            normalized: dict[str, Any] = {
                "subject": subject,
                "relation": relation,
                "object": obj,
            }
            weight = item.get("weight")
            if isinstance(weight, (int, float)) and not isinstance(weight, bool):
                normalized["weight"] = float(weight)
            elif isinstance(weight, str):
                try:
                    normalized["weight"] = float(weight.strip())
                except ValueError:
                    pass
            facts.append(normalized)
        return facts

    @classmethod
    def _normalize_directive(
        cls,
        raw: Any,
    ) -> tuple[str, dict[str, Any]] | None:
        if isinstance(raw, PlanningDirective):
            kind = cls._coerce_non_empty_string(raw.kind)
            if kind is None:
                return None
            return kind, _normalize_mapping(raw.payload)
        if isinstance(raw, CreateQuestPlan):
            return "create_quest", cls._merge_payload({"quest_id": raw.quest_id}, raw.payload)
        if isinstance(raw, DirectNpcPlan):
            if not raw.directive:
                return None
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
        return kind, _normalize_mapping(raw.get("payload"))

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
            "story_fact_count": 0,
            "applied_kinds": [],
            "planner_metadata": {},
            "replay_round_count": 0,
            "replay_stop_reason": "steady_state",
            "replay_event_count": 0,
            "replay_applied_directive_count": 0,
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

    @classmethod
    def _merge_payload(
        cls,
        base: Mapping[str, Any],
        extra: Any,
    ) -> dict[str, Any]:
        merged = _normalize_mapping(extra)
        result = dict(merged)
        for key, value in base.items():
            result[str(key)] = value
        return result
