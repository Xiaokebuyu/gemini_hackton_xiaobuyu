"""NarrativePlannerHook implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import TYPE_CHECKING, Any, Mapping, Protocol

from app.game_core.adapters.planner_system import PlannerBlackboardPort
from app.game_core.environment_access import list_visible_scene_interactables
from app.game_core.orchestration.event_engine import _normalize_mapping, run_inline_event_check
from app.game_core.orchestration.hooks.base import NoOpSettlementHook
from app.game_core.rules.models import Command
from app.game_core.orchestration.hooks.rest_phase import (
    is_quiet_rest_slot,
    resolve_rest_phase,
)
from app.game_core.orchestration.models import HookResult, SSEEvent
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.planning.directive_contracts import (
    SUPPORTED_PLANNER_DIRECTIVE_KINDS,
    DirectiveValidationResult,
    expand_planner_directive,
    is_unsupported_directive_reason,
    normalize_planner_directive,
    validate_planner_directive,
)
from app.game_core.planning.semantic_events import (
    collect_planner_events,
    planner_event_snapshot,
)
from app.game_core.state import StateChange
from app.game_core.state.quest_runtime import normalize_runtime_dynamic_quest

from app.game_core.planning.subsystem import PlannerEvent

if TYPE_CHECKING:
    from app.game_core.planning.subsystem import PlannerDispatcher


logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Content-layer service → effect-atom mapping (Phase 4)
# Maps known service_id values to executable effect atom lists.
# Unknown service_id → empty list (NPC can discuss but not execute mechanically).
# "donation" is intentionally excluded — handled by the existing DonationHandler.
# ------------------------------------------------------------------
_CONTENT_SERVICE_EFFECTS: dict[str, list[dict[str, Any]]] = {
    "heal": [{"type": "restore_hp", "amount": 30}],
    "blessing": [{"type": "apply_effect", "effect_id": "blessed", "duration_ticks": 6}],
    "field_dressing": [{"type": "restore_hp", "amount": 15}],
    "trail_prayer": [
        {"type": "apply_effect", "effect_id": "trail_protection", "duration_ticks": 4}
    ],
}

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
    outline_updates: dict[str, Any] = field(default_factory=dict)
    outline: dict[str, Any] | None = field(default=None)
    player_hint: str | None = field(default=None)


class NarrativePlannerProvider(Protocol):
    async def plan(self, context: dict[str, Any]) -> Any:
        ...


class MilestoneOutlineGeneratorPort(Protocol):
    """Injectable port for milestone outline generation (A9b/A9c).

    Implemented by ``app.narrators.MilestoneOutlineGenerator``.
    The Protocol lives in game_core so the hook can remain import-clean.
    """

    async def generate(
        self,
        *,
        milestone_template: dict[str, Any],
        target_milestone_id: str,
        chapter_id: str,
        current_tick: int,
        game_state_summary: dict[str, Any] | None = None,
        supported_condition_types: list[str] | None = None,
    ) -> dict[str, Any]:
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
    FALLBACK_INTERVAL = 1  # P32-1: shortened to 1 so planner runs every settlement tick
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
        "time",  # A-7 (S1-09/10/11): time slice changes (day/slot transitions) should trigger
    }
    _SUPPORTED_DIRECTIVES = {
        *SUPPORTED_PLANNER_DIRECTIVE_KINDS,
    }
    def __init__(
        self,
        planner: NarrativePlannerProvider | None = None,
        *,
        blackboard: PlannerBlackboardPort | None = None,
        dispatcher: PlannerDispatcher | None = None,
        outline_generator: MilestoneOutlineGeneratorPort | None = None,
    ) -> None:
        self.blackboard = blackboard if blackboard is not None else planner
        self._dispatcher = dispatcher
        # outline_generator is kept for API compatibility but no longer used:
        # LLM outline generation now happens via Planner.plan() (needs_outline signal).
        self._outline_generator = outline_generator
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

        # Auto-select current_target_milestone before outline generation
        self._auto_select_target_milestone(context)

        # A9c: check if current milestone changed and outline needs (re)generation
        await self._ensure_milestone_outline(context, current_tick=current_tick)

        reason = "trigger" if triggered else "fallback"

        # === Deterministic pre-processing (Phase 3d) ===
        # 1. Directive GC
        context.execute_command(
            Command(
                type="planner_prune_npc_directives",
                params={"current_tick": current_tick},
                source="narrative_planner",
            )
        )
        # 2. Temporary NPC despawn
        self._despawn_expired_quest_npcs(context, current_tick=current_tick)

        # 3. Auto-escalation safety net (cap=10)
        pre_directives: list[dict[str, Any]] = []
        escalate_directive = self._check_auto_escalation(context)
        if escalate_directive is not None:
            pre_directives.append(escalate_directive)

        # 4. Collect all semantic events (single pass).
        # Dedup is handled internally by _dedupe_and_sort() in collect_planner_events.
        # No need for external seen_dedupe_keys tracking in single-pass unified flow.
        all_pending_events = collect_planner_events(
            context,
            current_tick,
            change_window_start=0,
            round_index=0,
            include_action_log=True,
            include_tick_event=True,
        )
        for event in all_pending_events:
            if event.kind == "quest_completed":
                quest_completed_directives = self._handle_quest_completed_deterministic(
                    event, context
                )
                pre_directives.extend(quest_completed_directives)

        # Apply deterministic pre-processing directives
        pre_apply_summary = self._empty_apply_summary()
        if pre_directives:
            pre_apply_summary = self._apply_directive_batch(
                pre_directives,
                context,
                current_tick=current_tick,
                allowed_directives=self._SUPPORTED_DIRECTIVES,
                source="deterministic",
                subsystem_name="pre_processing",
                round_index=0,
            )

        # Build planner event summaries for context
        planner_event_summaries = [
            planner_event_snapshot(event) for event in all_pending_events
        ]

        # Build trace skeleton (simplified — no replay rounds)
        trace: dict[str, Any] = {
            "hook_priority": self.HOOK_PRIORITY,
            "current_tick": current_tick,
            "reason": reason,
            "round_count": 0,
            "stop_reason": "unified",
            "rounds": [],
            "directive_audit": list(pre_apply_summary.get("directive_audit", [])),
            "blackboard_summary": {},
        }

        blackboard_decision = NarrativePlannerDecision()
        blackboard_metadata: dict[str, Any] = {}
        blackboard_apply_summary = self._empty_apply_summary()
        blackboard_story_fact_count = 0
        if self.blackboard is not None:
            planner_context = self._build_planner_context(context, current_tick=current_tick)
            planner_context["planner_events"] = planner_event_summaries
            planner_context["replay_trace"] = trace
            self._inject_runtime_refs(planner_context, context)
            # QF-4: signal that planner LLM is starting
            self._pending_sse.append(SSEEvent(
                event_type="ai_processing",
                payload={"system": "planner", "status": "start"},
            ))
            try:
                raw_decision = await self.blackboard.plan(planner_context)
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
                trace["blackboard_summary"] = {
                    "source": "blackboard",
                    "requested_directive_count": 0,
                    "applied_directive_count": 0,
                    "skipped_unsupported_count": 0,
                    "skipped_invalid_count": 0,
                    "applied_kinds": [],
                    "story_fact_count": 0,
                    "directive_audit": [],
                    "planner_metadata": {},
                    "status": "planner_error",
                }
                self._commit_runtime_state(
                    context,
                    last_planner_replay_trace=trace,
                )
                return HookResult(
                    sse_events=[
                        SSEEvent(
                            event_type="ai_processing",
                            payload={"system": "planner", "status": "done"},
                        ),
                        SSEEvent(
                            event_type="narrative_planner_error",
                            payload={"error": str(exc)},
                        ),
                    ],
                    metadata={
                        "status": "planner_error",
                        "evaluated": False,
                        "reason": reason,
                        "current_tick": current_tick,
                        "ticks_since_last_run": ticks_since_last_run,
                        "requested_count": int(pre_apply_summary["requested_count"]),
                        "applied_count": int(pre_apply_summary["applied_count"]),
                        "skipped_unsupported_count": int(pre_apply_summary["skipped_unsupported_count"]),
                        "skipped_invalid_count": int(pre_apply_summary["skipped_invalid_count"]),
                        "story_fact_count": 0,
                        "applied_kinds": list(pre_apply_summary["applied_kinds"]),
                        "planner_metadata": {},
                        "replay_round_count": 0,
                        "replay_stop_reason": "error",
                        "replay_event_count": len(planner_event_summaries),
                        "replay_applied_directive_count": int(pre_apply_summary["applied_count"]),
                    },
                )
            # QF-4: signal that planner LLM finished
            self._pending_sse.append(SSEEvent(
                event_type="ai_processing",
                payload={"system": "planner", "status": "done"},
            ))
            blackboard_decision = self._normalize_decision(raw_decision)
            blackboard_apply_summary = self._apply_directive_batch(
                blackboard_decision.directives,
                context,
                current_tick=current_tick,
                allowed_directives=self._SUPPORTED_DIRECTIVES,
                source="blackboard",
                subsystem_name="blackboard",
                round_index=0,
            )
            blackboard_metadata = dict(blackboard_decision.metadata)

        # Inline event check (flag-based event condition triggers)
        run_inline_event_check(
            state=context.state,
            world=context.world,
            rules_engine=context._rules_engine,
            apply_delta=context._apply_delta,
            change_log=context.change_log,
            scene_bus=context.scene_bus,
            label="planner_unified",
            sse_collector=self._pending_sse,
        )

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

        # Merge counts: pre-processing + blackboard
        requested_count = int(pre_apply_summary["requested_count"]) + int(blackboard_apply_summary["requested_count"])
        applied_count = int(pre_apply_summary["applied_count"]) + int(blackboard_apply_summary["applied_count"])
        skipped_unsupported_count = int(pre_apply_summary["skipped_unsupported_count"]) + int(
            blackboard_apply_summary["skipped_unsupported_count"]
        )
        skipped_invalid_count = int(pre_apply_summary["skipped_invalid_count"]) + int(
            blackboard_apply_summary["skipped_invalid_count"]
        )
        applied_kinds = list(pre_apply_summary["applied_kinds"]) + list(blackboard_apply_summary["applied_kinds"])
        story_fact_count = blackboard_story_fact_count

        trace["directive_audit"].extend(blackboard_apply_summary["directive_audit"])
        trace["blackboard_summary"] = {
            "source": "blackboard",
            "requested_directive_count": int(blackboard_apply_summary["requested_count"]),
            "applied_directive_count": int(blackboard_apply_summary["applied_count"]),
            "skipped_unsupported_count": int(
                blackboard_apply_summary["skipped_unsupported_count"]
            ),
            "skipped_invalid_count": int(blackboard_apply_summary["skipped_invalid_count"]),
            "applied_kinds": list(blackboard_apply_summary["applied_kinds"]),
            "story_fact_count": blackboard_story_fact_count,
            "directive_audit": list(blackboard_apply_summary["directive_audit"]),
            "planner_metadata": dict(blackboard_metadata),
        }
        final_context = self._build_planner_context(context, current_tick=current_tick)
        behavior_window = list(context.state.narrative_plan.behavior_window)
        behavior_entry = {
            "tick": current_tick,
            "changed_slices": final_context["changed_slices"],
            "reason": reason,
            "directive_count": requested_count,
        }
        simulated_behavior_window = behavior_window + [behavior_entry]
        simulated_behavior_window = simulated_behavior_window[-24:]
        derived_style_tags = self._derive_play_style_tags(
            simulated_behavior_window
        )
        commit_payload: dict[str, Any] = {
            "last_planner_replay_trace": trace,
            "last_run_tick": current_tick,
            "ticks_since_milestone_progress": progress_value,
            "behavior_entry": behavior_entry,
            "play_style_tags": list(derived_style_tags),
        }
        if blackboard_decision.strategy_notes:
            commit_payload["strategy_notes"] = blackboard_decision.strategy_notes
        if (
            isinstance(blackboard_decision.next_scheduled_tick, int)
            and blackboard_decision.next_scheduled_tick >= current_tick
        ):
            commit_payload["next_scheduled_tick"] = blackboard_decision.next_scheduled_tick
        self._commit_runtime_state(context, **commit_payload)

        # Apply outline from the planner's response (new full outline takes precedence)
        if (
            blackboard_decision.outline
            and isinstance(blackboard_decision.outline.get("steps"), list)
            and blackboard_decision.outline["steps"]
            and context.state.has_slice("narrative_plan")
        ):
            outline = dict(blackboard_decision.outline)
            outline.setdefault(
                "target_milestone_id",
                context.state.narrative_plan.current_target_milestone or "",
            )
            outline.setdefault("computed_at_tick", current_tick)
            context.state.narrative_plan.set_milestone_outline(outline)

        # Apply outline_updates from the planner's response
        elif blackboard_decision.outline_updates and context.state.has_slice("narrative_plan"):
            context.state.narrative_plan.update_milestone_outline(
                blackboard_decision.outline_updates
            )

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
        # Emit quest_hint SSE if planner provided a player hint
        if blackboard_decision.player_hint:
            sse_events.append(
                SSEEvent(
                    event_type="quest_hint",
                    payload={"hint": blackboard_decision.player_hint, "tick": current_tick},
                )
            )

        # Detect FAILED and COMPLETED milestone transitions
        sse_events.extend(_detect_failed_milestones_sse(context))
        sse_events.extend(_detect_completed_milestones_sse(context))

        # Drain SSE events emitted by sub-systems during dispatch
        sse_events.extend(self._pending_sse)
        self._pending_sse.clear()

        # Update area_situation from area_events + danger_level + hostile_tracking
        self._update_area_situation(context)

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
                "planner_metadata": dict(blackboard_metadata),
                "replay_round_count": 0,
                "replay_stop_reason": "unified",
                "replay_event_count": len(planner_event_summaries),
                "replay_applied_directive_count": applied_count,
            },
        )

    # ------------------------------------------------------------------
    # Deterministic pre-processing helpers (Phase 3d)
    # These contain logic migrated from NarrativeWeaverSubSystem and
    # NpcDirectorSubSystem.  Called at the top of execute() before the
    # single LLM call so that deterministic state changes are reflected
    # in the context passed to blackboard.plan().
    # ------------------------------------------------------------------

    _AUTO_ESCALATION_THRESHOLDS: list[int] = [4, 7, 10, 13, 16]
    _FALLBACK_ESCALATION_INTERVAL: int = 6
    _MAX_ESCALATION_LEVEL: int = 10

    def _despawn_expired_quest_npcs(
        self,
        context: SettlementContext,
        *,
        current_tick: int,
    ) -> None:
        """Despawn temporary quest NPCs whose despawn_tick has passed."""
        from app.game_core.planning.utils import coerce_non_empty_string
        for entry in list(context.state.narrative_plan.quest_history):
            if entry.get("kind") != "spawn_quest_npc":
                continue
            raw_despawn_tick = entry.get("despawn_tick")
            if raw_despawn_tick is None:
                continue
            try:
                despawn_tick = int(raw_despawn_tick)
            except (TypeError, ValueError):
                continue
            if current_tick < despawn_tick:
                continue
            npc_id = coerce_non_empty_string(entry.get("npc_id"))
            if npc_id is None:
                continue
            context.execute_command(
                Command(
                    type="planner_despawn_quest_npc",
                    params={"npc_id": npc_id},
                    source="narrative_planner",
                )
            )

    def _check_auto_escalation(
        self, context: SettlementContext
    ) -> dict[str, Any] | None:
        """Return an escalate directive when the auto-escalation safety net fires, else None."""
        np_state = context.state.narrative_plan
        if np_state.pacing_frozen:
            return None

        ticks = np_state.ticks_since_milestone_progress
        level = np_state.escalation_level

        if level >= self._MAX_ESCALATION_LEVEL:
            return None

        # Pick threshold for the current escalation level (or fallback interval)
        if 0 <= level < len(self._AUTO_ESCALATION_THRESHOLDS):
            threshold = self._AUTO_ESCALATION_THRESHOLDS[level]
        else:
            threshold = self._FALLBACK_ESCALATION_INTERVAL

        if ticks >= threshold:
            return {"kind": "escalate", "payload": {"delta": 1}}
        return None

    def _handle_quest_completed_deterministic(
        self,
        event: "PlannerEvent",
        context: SettlementContext,
    ) -> list[dict[str, Any]]:
        """Generate an assign_service directive for the receptionist NPC when a
        quest is completed.  Returns [] when graceful degradation is needed."""
        from app.game_core.planning.utils import coerce_non_empty_string

        quest_id = coerce_non_empty_string(event.payload.get("quest_id"))
        if quest_id is None:
            return []

        # Resolve rewards from dynamic quest state or content quest registry.
        rewards: dict[str, Any] = {}
        if context.state.has_slice("quests"):
            quest = context.state.quests.get_dynamic_quest(quest_id)
            if isinstance(quest, dict):
                raw = quest.get("rewards")
                if isinstance(raw, dict) and raw:
                    rewards = raw
        if not rewards and context.world.has_registry("quests"):
            milestone = context.world.quests.get(quest_id)
            if milestone is not None:
                raw2 = milestone.rewards
                if isinstance(raw2, dict) and raw2:
                    rewards = raw2
        if not rewards:
            return []

        # Build effect atoms from rewards dict.
        effects: list[dict[str, Any]] = []
        gold = rewards.get("gold", 0)
        try:
            gold = int(gold)
        except (TypeError, ValueError):
            gold = 0
        if gold > 0:
            effects.append({"type": "modify_gold", "amount": gold})
        xp = rewards.get("xp", 0)
        try:
            xp = int(xp)
        except (TypeError, ValueError):
            xp = 0
        if xp > 0:
            effects.append({"type": "add_xp", "amount": xp})
        items = rewards.get("items", [])
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                item_id = item.get("item_id", "")
                if not isinstance(item_id, str) or not item_id.strip():
                    continue
                count = item.get("count", 1)
                try:
                    count = int(count)
                except (TypeError, ValueError):
                    count = 1
                effects.append({"type": "grant_item", "item_id": item_id.strip(), "count": count})
        if not effects:
            return []

        # Find receptionist NPC.
        receptionist_id: str | None = None
        if context.world.has_registry("characters"):
            for template in context.world.characters.list_all():
                if "receptionist" in (template.tags or []):
                    receptionist_id = template.id
                    break
        if receptionist_id is None:
            logger.debug(
                "NarrativePlannerHook: no receptionist NPC found for quest %s; "
                "skipping reward service assignment",
                quest_id,
            )
            return []

        service_id = f"reward_{quest_id}"

        # Idempotency: do not create a duplicate service if already assigned.
        if context.state.has_slice("narrative_plan"):
            existing = context.state.narrative_plan.get_services(receptionist_id)
            if any(s.get("service_id") == service_id for s in existing):
                logger.debug(
                    "NarrativePlannerHook: reward service %s already assigned to %s; skipping",
                    service_id,
                    receptionist_id,
                )
                return []

        directive = {
            "kind": "assign_service",
            "payload": {
                "npc_id": receptionist_id,
                "service_id": service_id,
                "label": "领取任务报酬",
                "price": 0,
                "effects": effects,
                "preconditions": {"quest_completed": quest_id},
                "one_shot": True,
                "notes": f"完成任务 {quest_id} 后可领取的报酬",
            },
        }
        return [directive]

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
            allowed_directives=self._SUPPORTED_DIRECTIVES,
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
        replay_requested_count = replay["requested_count"]
        replay_applied_count = replay["applied_count"]
        replay_skipped_unsupported_count = replay["skipped_unsupported_count"]
        replay_skipped_invalid_count = replay["skipped_invalid_count"]
        replay_applied_kinds = replay["applied_kinds"]
        subsystem_story_fact_count = replay["story_fact_count"]
        replay_trace = replay["trace"]
        planner_event_summaries = replay["planner_event_summaries"]

        # Defensive: ensure target milestone is set after replay applied any
        # planner_create_quest directives (Fix 1 may have already set it via
        # StateChange, but replay applies deltas so it's safe to call again).
        self._auto_select_target_milestone(context)

        blackboard_decision = NarrativePlannerDecision()
        blackboard_metadata: dict[str, Any] = {}
        blackboard_apply_summary = self._empty_apply_summary()
        blackboard_story_fact_count = 0
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
                replay_trace["blackboard_summary"] = {
                    "source": "blackboard",
                    "requested_directive_count": 0,
                    "applied_directive_count": 0,
                    "skipped_unsupported_count": 0,
                    "skipped_invalid_count": 0,
                    "applied_kinds": [],
                    "story_fact_count": 0,
                    "directive_audit": [],
                    "planner_metadata": {},
                    "status": "planner_error",
                }
                self._commit_runtime_state(
                    context,
                    last_planner_replay_trace=replay_trace,
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
                        "requested_count": replay_requested_count,
                        "applied_count": replay_applied_count,
                        "skipped_unsupported_count": replay_skipped_unsupported_count,
                        "skipped_invalid_count": replay_skipped_invalid_count,
                        "story_fact_count": subsystem_story_fact_count,
                        "applied_kinds": replay_applied_kinds,
                        "planner_metadata": {},
                        "replay_round_count": replay_trace.get("round_count", 0),
                        "replay_stop_reason": replay_trace.get("stop_reason", "error"),
                        "replay_event_count": len(planner_event_summaries),
                        "replay_applied_directive_count": replay_applied_count,
                    },
                )
            blackboard_decision = self._normalize_decision(raw_decision)
            blackboard_apply_summary = self._apply_directive_batch(
                blackboard_decision.directives,
                context,
                current_tick=current_tick,
                allowed_directives=self._SUPPORTED_DIRECTIVES,
                source="blackboard",
                subsystem_name="blackboard",
                round_index=int(replay_trace.get("round_count", 0)),
            )
            blackboard_story_fact_count = self._apply_story_facts(
                blackboard_decision.story_facts,
                context,
            )
            blackboard_metadata = dict(blackboard_decision.metadata)
        requested_count = replay_requested_count + int(blackboard_apply_summary["requested_count"])
        applied_count = replay_applied_count + int(blackboard_apply_summary["applied_count"])
        skipped_unsupported_count = replay_skipped_unsupported_count + int(
            blackboard_apply_summary["skipped_unsupported_count"]
        )
        skipped_invalid_count = replay_skipped_invalid_count + int(
            blackboard_apply_summary["skipped_invalid_count"]
        )
        applied_kinds = list(replay_applied_kinds) + list(blackboard_apply_summary["applied_kinds"])
        story_fact_count = subsystem_story_fact_count + blackboard_story_fact_count
        replay_trace["directive_audit"] = list(replay_trace.get("directive_audit", []))
        replay_trace["directive_audit"].extend(blackboard_apply_summary["directive_audit"])
        replay_trace["blackboard_summary"] = {
            "source": "blackboard",
            "requested_directive_count": int(blackboard_apply_summary["requested_count"]),
            "applied_directive_count": int(blackboard_apply_summary["applied_count"]),
            "skipped_unsupported_count": int(
                blackboard_apply_summary["skipped_unsupported_count"]
            ),
            "skipped_invalid_count": int(blackboard_apply_summary["skipped_invalid_count"]),
            "applied_kinds": list(blackboard_apply_summary["applied_kinds"]),
            "story_fact_count": blackboard_story_fact_count,
            "directive_audit": list(blackboard_apply_summary["directive_audit"]),
            "planner_metadata": dict(blackboard_metadata),
        }
        bootstrap_commit_payload: dict[str, Any] = {
            "last_planner_replay_trace": replay_trace,
            # Bypass FALLBACK_INTERVAL cooldown on first settlement after opening
            "last_run_tick": -(current_tick + 100),
        }
        if blackboard_decision.strategy_notes:
            bootstrap_commit_payload["strategy_notes"] = blackboard_decision.strategy_notes
        if (
            isinstance(blackboard_decision.next_scheduled_tick, int)
            and blackboard_decision.next_scheduled_tick >= current_tick
        ):
            bootstrap_commit_payload["next_scheduled_tick"] = blackboard_decision.next_scheduled_tick
        self._commit_runtime_state(context, **bootstrap_commit_payload)

        # Apply outline from Planner's bootstrap response (full generation)
        if blackboard_decision.outline and context.state.has_slice("narrative_plan"):
            outline = dict(blackboard_decision.outline)
            outline.setdefault(
                "target_milestone_id",
                context.state.narrative_plan.current_target_milestone or "",
            )
            outline.setdefault("computed_at_tick", current_tick)
            context.state.narrative_plan.set_milestone_outline(outline)

        # Apply outline_updates from the planner's bootstrap response
        if blackboard_decision.outline_updates and context.state.has_slice("narrative_plan"):
            context.state.narrative_plan.update_milestone_outline(
                blackboard_decision.outline_updates
            )

        # Phase 4: inject content-layer services into NarrativePlanSlice
        self._bootstrap_content_services(context)

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

    def _auto_select_target_milestone(self, context: SettlementContext) -> None:
        """Auto-select current_target_milestone when stale, completed, or missing.

        Called at the start of execute() before _ensure_milestone_outline so the
        outline generator always has a valid target to work with.

        Priority: ACTIVE > AVAILABLE; ties broken by MilestoneTemplate.sequence.
        No-ops when the current target is still ACTIVE.
        """
        if not context.state.has_slice("narrative_plan") or not context.state.has_slice("quests"):
            return
        current_ms = context.state.narrative_plan.current_target_milestone
        # If the current target is still ACTIVE, nothing to do
        if current_ms:
            ms_state = context.state.quests.get_milestone_state(current_ms)
            if ms_state == "ACTIVE":
                return
        # Collect candidates: (priority, sequence, ms_id)
        candidates: list[tuple[int, int, str]] = []
        for ms_id, ms in context.state.quests.milestone_states.items():
            if ms.state not in ("ACTIVE", "AVAILABLE"):
                continue
            seq = 0
            if context.world.has_registry("quests"):
                tmpl = context.world.quests.get_milestone(ms_id)
                if tmpl is not None:
                    seq = tmpl.sequence
            priority = 0 if ms.state == "ACTIVE" else 1
            candidates.append((priority, seq, ms_id))
        if not candidates:
            return
        candidates.sort()
        best_id = candidates[0][2]
        if best_id != current_ms:
            context.state.narrative_plan.set_target_milestone(best_id)

    def _has_trigger_change(self, change_log: list[StateChange]) -> bool:
        for change in change_log:
            if change.slice in self._TRIGGER_SLICES:
                return True
        return False

    def _update_area_situation(self, context: SettlementContext) -> None:
        """Build a deterministic natural-language situation summary for the current area.

        Reads area_events (recent 10), danger_level, area tags, and hostile_tracking
        counters, then writes the result into AreaSlice.area_situation.  No LLM call —
        pure template concatenation so the data is always up-to-date.
        """
        if not context.state.has_slice("areas") or not context.state.has_slice("player"):
            return
        area_id = context.state.player.current_area
        if not area_id:
            return
        area_state = context.state.areas.areas.get(area_id)
        if area_state is None:
            return

        danger_level = area_state.danger_level
        area_tags = list(area_state.tags) if isinstance(area_state.tags, list) else []
        recent_events = [
            e for e in area_state.area_events[-10:]
            if isinstance(e, dict)
        ]
        # Count active vs cleared hostile encounters
        active_encounters = 0
        cleared_encounters = 0
        for ht_state in area_state.hostile_tracking.values():
            if not isinstance(ht_state, dict):
                continue
            if str(ht_state.get("status", "")) == "cleared":
                cleared_encounters += 1
            else:
                active_encounters += 1

        parts: list[str] = []
        if danger_level >= 3.0:
            parts.append(f"当前区域危险等级较高({danger_level:.1f})")
        if active_encounters:
            parts.append(f"区域内有{active_encounters}处已知敌对遭遇")
        if cleared_encounters:
            parts.append(f"已清除{cleared_encounters}处遭遇")
        if recent_events:
            event_texts = [
                str(e.get("event", "")).strip()
                for e in recent_events[-3:]
                if str(e.get("event", "")).strip()
            ]
            if event_texts:
                parts.append("近期事件：" + "；".join(event_texts))

        # NPC location descriptions
        if context.world.has_registry("characters"):
            npc_locs = area_state.npc_locations  # {npc_id: location_id}
            npc_descs: list[str] = []
            for npc_id, loc_id in npc_locs.items():
                tmpl = context.world.characters.get(npc_id)
                if tmpl is not None:
                    name = tmpl.name or npc_id
                    npc_descs.append(f"{name}在{loc_id or '附近'}")
            if npc_descs:
                parts.append("当前区域NPC：" + "、".join(npc_descs[:5]))

        # Time period atmosphere
        if context.state.has_slice("time"):
            period = context.state.time.period
            period_desc = {
                "dawn": "清晨时分，一切刚刚苏醒",
                "day": "白天，小镇正常运转中",
                "dusk": "傍晚，冒险者们陆续回城",
                "night": "夜晚，小镇安静下来",
            }
            desc = period_desc.get(period)
            if desc:
                parts.append(desc)

        situation = "。".join(parts) + "。" if parts else ""
        context.state.areas.set_area_situation(area_id, situation)

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

        # A-4 (S1-06/S6-05): richer quest entries with objectives, rewards, area_id
        dynamic_quests = {
            quest_id: {
                "status": self._string_or_empty(quest.get("status")),
                "title": self._string_or_empty(quest.get("title")),
                "summary": self._string_or_empty(quest.get("summary")),
                "objectives": list(quest.get("objectives", [])) if isinstance(quest.get("objectives"), list) else [],
                "rewards": dict(quest.get("rewards", {})) if isinstance(quest.get("rewards"), dict) else {},
                "area_id": quest.get("area_id"),
                "giver_npc": quest.get("giver_npc"),
            }
            for quest_id, quest in context.state.quests.dynamic_quests.items()
        }
        completed_dynamic_quests, report_ready_dynamic_quests = (
            self._build_completed_dynamic_quest_summaries(
                context.state.quests.dynamic_quests
            )
        )

        play_style_tags: list[str] = []
        if hasattr(context.state.narrative_plan, "play_style_tags"):
            raw_play_style_tags = context.state.narrative_plan.play_style_tags
            if isinstance(raw_play_style_tags, list):
                play_style_tags = [str(tag) for tag in raw_play_style_tags]

        area_npcs: list[str] = []
        area_npc_summaries: list[dict[str, Any]] = []
        area_boards: list[dict[str, str]] = []
        area_cluster = None
        all_area_ids: list[str] = []
        current_sub_area_ids: list[str] = []
        existing_npc_ids: list[str] = []  # A5b: all NPC IDs present in current area
        interactable_examples: list[dict[str, Any]] = []  # A5e: static interactable examples
        hostile_config_locations: list[dict[str, Any]] = []  # A6b: hostile sub-loc configs
        area_situation_ctx: str = ""
        area_events_ctx: list[dict[str, Any]] = []
        exploration_summary_ctx: dict[str, Any] = {}
        if context.state.has_slice("areas") and context.state.has_slice("player"):
            # Collect all known area_ids for planner reference
            all_area_ids = list(context.state.areas.areas.keys())
            area_id = context.state.player.current_area
            if area_id and area_id in context.state.areas.areas:
                counts = context.state.areas.count_dynamic_sub_areas(area_id)
                total_dynamic = counts.get("total", 0)
                permanent_dynamic = counts.get("permanent", 0)
                # A5a: explicit capacity headroom instead of boolean
                area_cluster = {
                    "area_id": area_id,
                    "total_dynamic": total_dynamic,
                    "remaining_permanent": max(0, 8 - permanent_dynamic),
                    "remaining_total": max(0, 15 - total_dynamic),
                }
                area_state = context.state.areas.areas[area_id]
                # 3-B: inject area_situation, area_events, exploration_summary
                area_situation_ctx = area_state.area_situation
                area_events_ctx = [
                    dict(e) for e in area_state.area_events[-10:]
                    if isinstance(e, dict)
                ]
                hostile_tracking = area_state.hostile_tracking
                interactable_states = area_state.interactable_states
                exploration_summary_ctx = {
                    "encounters_total": len(hostile_tracking),
                    "encounters_cleared": sum(
                        1 for e in hostile_tracking.values() if e.get("cleared")
                    ),
                    "clues_investigated": len(interactable_states),
                    "discovered_rooms": len(area_state.discovered_rooms),
                }
                # Collect sub_area_ids for current area
                if context.world.has_registry("maps"):
                    area_template = context.world.maps.get(area_id)
                    if area_template is not None:
                        current_sub_area_ids.extend(area_template.sub_locations.keys())
                for temp_sub in area_state.temporary_sub_areas:
                    if isinstance(temp_sub, Mapping):
                        sub_id = str(temp_sub.get("id", "")).strip()
                        if sub_id and sub_id not in current_sub_area_ids:
                            current_sub_area_ids.append(sub_id)
                # A-4 (S1-06/S6-05): enrich NPC summaries with name, tags, approval, trust
                area_npc_ids = [
                    str(npc_id)
                    for npc_id in area_state.npc_locations.keys()
                    if self._coerce_non_empty_string(npc_id) is not None
                ]
                area_npcs_summaries: list[dict[str, Any]] = []
                for npc_id in area_npc_ids:
                    npc_entry: dict[str, Any] = {"id": npc_id}
                    if context.world.has_registry("characters"):
                        tmpl = context.world.characters.get(npc_id)
                        if tmpl is not None:
                            npc_entry["name"] = str(tmpl.name)
                            raw_tags = getattr(tmpl, "tags", None)
                            npc_entry["tags"] = (
                                list(raw_tags) if isinstance(raw_tags, (list, set, frozenset))
                                else []
                            )
                            npc_current_area = getattr(tmpl, "area_id", None)
                            if npc_current_area:
                                npc_entry["area_id"] = str(npc_current_area)
                    if context.state.has_slice("relations"):
                        npc_entry["approval"] = (
                            context.state.relations.get_disposition(npc_id, "approval") or 0
                        )
                        npc_entry["trust"] = (
                            context.state.relations.get_disposition(npc_id, "trust") or 0
                        )
                        npc_entry["stage"] = (
                            context.state.relations.get_stage(npc_id) or "stranger"
                        )
                    area_npcs_summaries.append(npc_entry)
                # backward-compat: area_npcs stays as list[str], new area_npc_summaries is rich
                area_npcs = area_npc_ids
                area_npc_summaries = area_npcs_summaries
                # A5b: build deduplicated existing_npc_ids (runtime presence + static residents)
                npc_id_set: set[str] = set(area_npc_ids)
                if context.world.has_registry("maps"):
                    _area_tmpl_for_npc = context.world.maps.get(area_id)
                    if _area_tmpl_for_npc is not None:
                        for sub_tmpl_npc in _area_tmpl_for_npc.sub_locations.values():
                            for resident_npc in getattr(sub_tmpl_npc, "resident_npcs", []):
                                if isinstance(resident_npc, str) and resident_npc.strip():
                                    npc_id_set.add(resident_npc.strip())
                existing_npc_ids = sorted(npc_id_set)

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
                                if "quest_source" in raw_tags:
                                    area_boards.append({
                                        "id": board_id,
                                        "sub_location": str(sub_id),
                                    })
                            # A5e: collect interactable examples from static sub-locs (cap=3 total)
                            if len(interactable_examples) < 3:
                                for ia in getattr(sub_tmpl, "interactables", []):
                                    if len(interactable_examples) >= 3:
                                        break
                                    ia_id = self._coerce_non_empty_string(
                                        getattr(ia, "id", None)
                                    )
                                    if ia_id is None:
                                        continue
                                    ia_checks = getattr(ia, "checks", [])
                                    checks_serialized = []
                                    for ch in ia_checks:
                                        if hasattr(ch, "__dict__"):
                                            checks_serialized.append({
                                                k: v for k, v in ch.__dict__.items()
                                            })
                                        elif isinstance(ch, Mapping):
                                            checks_serialized.append(dict(ch))
                                    interactable_examples.append({
                                        "id": ia_id,
                                        "name": str(getattr(ia, "name", "")),
                                        "description": str(getattr(ia, "description", "")),
                                        "type": str(getattr(ia, "type", "inspect")),
                                        "tags": list(getattr(ia, "tags", [])),
                                        "checks": checks_serialized,
                                    })
                            # A6b: collect hostile_config sub-locations
                            hc = getattr(sub_tmpl, "hostile_config", None)
                            if hc is not None:
                                existing_hs = context.state.areas.get_hostile_state(str(sub_id))
                                already_planted = (
                                    existing_hs is not None
                                    and str(existing_hs.get("status", "")) != "cleared"
                                )
                                already_cleared = (
                                    existing_hs is not None
                                    and str(existing_hs.get("status", "")) == "cleared"
                                )
                                monster_ids_flat = [
                                    mid
                                    for grp in getattr(hc, "hostile_groups", [])
                                    for mid in getattr(grp, "monster_ids", [])
                                ]
                                hostile_config_locations.append({
                                    "sub_location_id": str(sub_id),
                                    "sub_location_name": str(getattr(sub_tmpl, "name", str(sub_id))),
                                    "monster_ids": monster_ids_flat,
                                    "stealth_dc": getattr(hc, "stealth_dc", 12),
                                    "blocking": getattr(hc, "blocking", True),
                                    "already_planted": already_planted,
                                    "already_cleared": already_cleared,
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
        # A-3 (S1-04/S1-05/S1-08): inject last round's directive_audit as feedback loop
        previous_directive_results: list[dict[str, Any]] = []
        last_trace = context.state.narrative_plan.last_planner_replay_trace
        if isinstance(last_trace, dict):
            raw_audit = last_trace.get("directive_audit", [])
            if isinstance(raw_audit, list):
                for entry in raw_audit[-10:]:
                    if not isinstance(entry, dict):
                        continue
                    # A5d: include entity_id for better feedback loop visibility
                    pdr_entry: dict[str, Any] = {
                        "kind": str(entry.get("kind", "")),
                        "status": str(entry.get("status", "")),
                        "reason_code": entry.get("reason_code"),
                    }
                    payload_digest = entry.get("payload_digest")
                    if isinstance(payload_digest, Mapping):
                        entity_id = payload_digest.get("entity_id") or payload_digest.get(
                            "npc_id"
                        ) or payload_digest.get("quest_id")
                        if entity_id is not None:
                            pdr_entry["entity_id"] = entity_id
                    previous_directive_results.append(pdr_entry)

        # A8d: active_quests with objective progress (replaces available_quest_ids/active_quest_ids)
        active_quests: list[dict[str, Any]] = []
        active_statuses = {"in_progress", "active", "accepted"}
        for q_id, q_data in context.state.quests.dynamic_quests.items():
            if not isinstance(q_data, Mapping):
                continue
            if str(q_data.get("status", "")).strip().lower() not in active_statuses:
                continue
            raw_objectives = q_data.get("objectives", [])
            objectives_summary: list[dict[str, Any]] = []
            if isinstance(raw_objectives, list):
                for obj in raw_objectives:
                    if not isinstance(obj, Mapping):
                        continue
                    obj_entry: dict[str, Any] = {
                        "description": self._string_or_empty(obj.get("description")),
                        "completed": bool(obj.get("completed", False)),
                    }
                    condition = obj.get("condition")
                    if isinstance(condition, Mapping):
                        obj_entry["condition"] = dict(condition)
                    objectives_summary.append(obj_entry)
            active_quests.append({
                "quest_id": str(q_id),
                "title": self._string_or_empty(q_data.get("title")),
                "objectives": objectives_summary,
            })

        # A8a: supported objective condition types (guidance for Planner)
        supported_objective_conditions: list[dict[str, Any]] = [
            {
                "type": "npc_talked",
                "params": {"npc_id": "character_id"},
                "desc": "与指定NPC对话",
            },
            {
                "type": "item_obtained",
                "params": {"item_id": "物品ID", "count": 1},
                "desc": "获得指定物品",
            },
            {
                "type": "kill_count",
                "params": {"monster_id": "怪物ID", "count": 3},
                "desc": "击杀指定数量怪物",
            },
            {
                "type": "location_entered",
                "params": {"area_id": "区域ID", "location_id": "子地点ID"},
                "desc": "进入指定地点",
            },
            {
                "type": "flag_set",
                "params": {"flag": "flag_name"},
                "desc": "特定标记被设置",
            },
        ]

        # A9d: current milestone outline for planner context
        milestone_outline_ctx: dict[str, Any] | None = None
        raw_outline = context.state.narrative_plan.milestone_outline
        if isinstance(raw_outline, Mapping) and raw_outline.get("steps"):
            steps = raw_outline.get("steps", [])
            if isinstance(steps, list):
                milestone_outline_ctx = {
                    "target": raw_outline.get("target_milestone_id"),
                    "steps": [
                        {
                            "index": s.get("index"),
                            "description": s.get("description", ""),
                            "type": s.get("type", ""),
                            "completed": bool(s.get("completed", False)),
                            "quest_id": s.get("quest_id"),
                        }
                        for s in steps
                        if isinstance(s, Mapping)
                    ],
                    "current_step": next(
                        (
                            dict(s) for s in steps
                            if isinstance(s, Mapping) and not s.get("completed", False)
                        ),
                        None,
                    ),
                }

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
                "completed_dynamic_quests": completed_dynamic_quests,
                "report_ready_dynamic_quests": report_ready_dynamic_quests,
            },
            "area_npcs": area_npcs,
            "area_npc_summaries": area_npc_summaries,
            "area_boards": area_boards,
            # A5b: deduplicated NPC IDs present in current area (runtime + static residents)
            "existing_npc_ids": existing_npc_ids,
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
                "recent_dialogue": self._build_recent_dialogue(scene_snapshot),
            },
            "events": {
                "pending_events_digest": pending_events_digest,
            },
            "world_context": world_context,
            "maps": context.world.maps if context.world.has_registry("maps") else None,
            "target_milestone_detail": self._build_target_milestone_detail(context),
            "story_facts": list(context.state.narrative_plan.story_facts),
            "danger_level": self._get_area_danger(context),
            # 3-B: area situation + recent events + exploration summary
            "area_situation": area_situation_ctx,
            "area_events": area_events_ctx,
            "exploration_summary": exploration_summary_ctx,
            "previous_directive_results": previous_directive_results,
            "all_area_ids": all_area_ids,
            "current_sub_area_ids": current_sub_area_ids,
            # Phase 3 (P26-3-4a): player level/xp for planner difficulty guidance
            "player_level": (
                context.state.player.level
                if context.state.has_slice("player")
                else 1
            ),
            "player_xp": (
                context.state.player.xp
                if context.state.has_slice("player")
                else 0
            ),
            # Available items for grant_item / item_obtained (daily/misc only)
            "available_items": self._build_available_items(context),
            # B-5 (P28): discoverable rooms hidden from player + dynamic room capacity
            "discoverable_rooms_hidden": self._build_discoverable_rooms_hidden(context),
            "dynamic_location_capacity": self._build_dynamic_location_capacity(context),
            "static_room_ids": self._build_static_room_ids(context),
            "scene_interactable_capacity": self._build_scene_interactable_capacity(context),
            # A5e: interactable template examples + fill_area authoring guidance
            "interactable_examples": interactable_examples,
            "fill_area_guidance": (
                "fill_area 的 interactables 字段需含完整定义："
                "每个 interactable 必须包含 id/name/description/type/tags/checks。"
                "只有真正新增可进入空间时才使用 fill_area，"
                "不要为已有核心设施再造平行子地点。"
                "参考 interactable_examples 中的静态模板结构。"
            ),
            "fill_location_guidance": (
                "fill_location 用于给现有 location/room 增加微交互。"
                "payload 需含 area_id/location_id/interactables，可选 room_id。"
                "每个 interactable 至少包含 id/name/description/type/tags/checks，"
                "功能型设施可补 functional.type。单场景 overlay 上限 4 个。"
            ),
            # A6b/A6c: hostile sub-location configs + encounter placement guidance
            "hostile_config_locations": hostile_config_locations,
            "encounter_guidance": (
                "hostile_config_locations 列出了设计上应有敌人的子地点。"
                "对于 already_planted=false 且 already_cleared=false 的条目，"
                "应使用 planner_plant_encounter 指令放置敌人。"
                "参数：area_id, sub_area_id=子地点ID, monster_ids=怪物列表, "
                "threat_level, blocking, stealth_dc, map_category='ruins', expiry_ticks=-1（不过期）。"
            ),
            # A8a/A8d: quest objective condition guidance + active quest progress
            "supported_objective_conditions": supported_objective_conditions,
            "quest_objective_guidance": (
                "每个 objective 必须包含 condition 字段（含 type 和对应 params），"
                "否则无法自动追踪完成。type 必须从 supported_objective_conditions 中选择。"
            ),
            "active_quests": active_quests,
            # A9d: current milestone outline for structured planning
            "milestone_outline": milestone_outline_ctx,
            "outline_guidance": (
                "milestone_outline.steps 是当前里程碑的叙事规划。"
                "创建任务时必须关联到某个 step（通过 step_index），"
                "并使用该 step 的 condition 作为任务目标。"
                "优先推进 current_step，不要跳过未完成的步骤。"
            ) if milestone_outline_ctx else None,
            # R3-C: rich feedback — quest progress, milestone condition satisfaction,
            #        NPC directive confirmation, clue interaction summary
            "planner_feedback": self._build_planner_feedback(
                context, current_tick=current_tick,
            ),
            # Outline generation signal: when True, Planner must output an "outline" field
            **self._build_outline_signal(context),
        }

    async def _ensure_milestone_outline(
        self,
        context: "SettlementContext",
        *,
        current_tick: int,
    ) -> None:
        """A9c: Set a deterministic fallback outline when target milestone changes.

        LLM-quality outline generation is now handled by Planner.plan() via the
        ``needs_outline`` signal injected in ``_build_planner_context()``.
        This method only writes the deterministic fallback so there is always
        *something* in NarrativePlanSlice.milestone_outline before plan() runs.
        The Planner's richer outline will overwrite it in execute() if produced.
        """
        if not context.state.has_slice("narrative_plan"):
            return
        current_ms = context.state.narrative_plan.current_target_milestone
        if not current_ms:
            return
        existing_outline = context.state.narrative_plan.milestone_outline
        if (
            isinstance(existing_outline, Mapping)
            and existing_outline.get("target_milestone_id") == current_ms
        ):
            # Already have a valid outline for this milestone — skip
            return

        outline_ctx = self._build_outline_context(context, current_ms, current_tick)
        if not outline_ctx["milestone_template"]:
            # No template data — cannot build meaningful outline
            return

        # A9f: Always use deterministic fallback here; LLM outline arrives later via plan()
        fallback = self._build_fallback_outline_from_template(
            milestone_template=outline_ctx["milestone_template"],
            target_milestone_id=current_ms,
            chapter_id=outline_ctx["chapter_id"],
            current_tick=current_tick,
        )
        context.state.narrative_plan.set_milestone_outline(fallback)

    def _check_needs_outline(self, context: "SettlementContext") -> bool:
        """Return True when the current milestone lacks a valid LLM outline."""
        if not context.state.has_slice("narrative_plan"):
            return False
        current_ms = context.state.narrative_plan.current_target_milestone
        if not current_ms:
            return False
        existing = context.state.narrative_plan.milestone_outline
        if isinstance(existing, Mapping) and existing.get("target_milestone_id") == current_ms:
            return False
        return True

    def _get_milestone_template(self, context: "SettlementContext") -> dict[str, Any]:
        """Return milestone template data for the current target milestone."""
        current_ms = context.state.narrative_plan.current_target_milestone
        if not current_ms:
            return {}
        if not context.world.has_registry("quests"):
            return {}
        tmpl = context.world.quests.get_milestone(current_ms)
        if tmpl is None:
            return {}
        return {
            "key_elements": list(tmpl.key_elements),
            "involved_npcs": list(tmpl.involved_npcs),
            "involved_locations": list(tmpl.involved_locations),
            "narrative_context": tmpl.narrative_context,
            "success_conditions": list(getattr(tmpl, "success_conditions", None) or []),
        }

    def _build_outline_signal(self, context: "SettlementContext") -> dict[str, Any]:
        """Build the needs_outline + milestone_template_for_outline entries for planner context."""
        needs = self._check_needs_outline(context)
        return {
            "needs_outline": needs,
            "milestone_template_for_outline": (
                self._get_milestone_template(context) if needs else None
            ),
        }

    def _build_outline_context(
        self,
        context: "SettlementContext",
        target_milestone_id: str,
        current_tick: int,
    ) -> dict[str, Any]:
        """Build the input package for milestone outline generation."""
        chapter_id = context.state.narrative_plan.current_chapter or ""
        milestone_template: dict[str, Any] = {}
        if context.world.has_registry("quests"):
            tmpl = context.world.quests.get_milestone(target_milestone_id)
            if tmpl is not None:
                # Prefer chapter_id from milestone template over runtime state
                if not chapter_id and hasattr(tmpl, "chapter_id") and tmpl.chapter_id:
                    chapter_id = str(tmpl.chapter_id)
                milestone_template = {
                    "key_elements": list(tmpl.key_elements),
                    "involved_npcs": list(tmpl.involved_npcs),
                    "involved_locations": list(tmpl.involved_locations),
                    "narrative_context": tmpl.narrative_context,
                    "success_conditions": list(
                        getattr(tmpl, "success_conditions", None) or []
                    ),
                }
        game_state_summary: dict[str, Any] = {
            "current_tick": current_tick,
            "chapter_id": chapter_id,
        }
        if context.state.has_slice("player"):
            game_state_summary["player_area"] = context.state.player.current_area
            game_state_summary["player_level"] = getattr(context.state.player, "level", 1)
        active_quest_titles = [
            str(q.get("title", ""))
            for q in context.state.quests.dynamic_quests.values()
            if isinstance(q, Mapping)
            and str(q.get("status", "")).strip().lower()
            in {"active", "in_progress", "accepted"}
        ]
        if active_quest_titles:
            game_state_summary["active_quest_titles"] = active_quest_titles[:5]
        if context.state.has_slice("party"):
            members = list(getattr(context.state.party, "members", {}).keys())
            if members:
                game_state_summary["party_members"] = [str(m) for m in members[:4]]
        return {
            "milestone_template": milestone_template,
            "target_milestone_id": target_milestone_id,
            "chapter_id": chapter_id,
            "game_state_summary": game_state_summary,
            "supported_condition_types": [
                "npc_talked",
                "item_obtained",
                "kill_count",
                "location_entered",
                "flag_set",
            ],
        }

    @staticmethod
    def _build_fallback_outline_from_template(
        *,
        milestone_template: dict[str, Any],
        target_milestone_id: str,
        chapter_id: str,
        current_tick: int,
    ) -> dict[str, Any]:
        """A9f: Deterministic fallback outline from key_elements.

        One step per key_element, with a ``flag_set`` condition as placeholder.
        """
        key_elements = milestone_template.get("key_elements") or []
        if not isinstance(key_elements, list):
            key_elements = [str(key_elements)]
        involved_npcs = list(milestone_template.get("involved_npcs") or [])
        involved_locations = list(milestone_template.get("involved_locations") or [])
        steps: list[dict[str, Any]] = []
        for idx, elem in enumerate(key_elements[:10]):
            steps.append({
                "index": idx,
                "description": str(elem),
                "type": "exploration",
                "condition": {
                    "type": "flag_set",
                    "flag": f"{target_milestone_id}_step_{idx}",
                },
                "related_npcs": involved_npcs[:2],
                "related_locations": involved_locations[:2],
                "completed": False,
                "quest_id": None,
            })
        if not steps:
            steps = [
                {
                    "index": 0,
                    "description": f"推进里程碑：{target_milestone_id}",
                    "type": "exploration",
                    "condition": {
                        "type": "flag_set",
                        "flag": f"{target_milestone_id}_step_0",
                    },
                    "related_npcs": involved_npcs[:2],
                    "related_locations": involved_locations[:2],
                    "completed": False,
                    "quest_id": None,
                }
            ]
        return {
            "target_milestone_id": target_milestone_id,
            "chapter_id": chapter_id,
            "computed_at_tick": current_tick,
            "steps": steps,
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
    def _build_recent_dialogue(scene_snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Extract recent public dialogue entries for planner awareness.

        Returns up to 8 recent public scene entries so the planner knows what
        players and NPCs said, enabling context-aware follow-up directives
        (e.g. moving an NPC after they promised to go somewhere).
        """
        raw_entries = scene_snapshot.get("entries", [])
        if not isinstance(raw_entries, list):
            return []
        dialogue: list[dict[str, Any]] = []
        for entry in raw_entries:
            if not isinstance(entry, Mapping):
                continue
            if str(entry.get("visibility", "")).strip() != "public":
                continue
            source = str(entry.get("source", "")).strip()
            content = str(entry.get("content", "")).strip()
            if not content:
                continue
            tags = entry.get("tags", [])
            if not isinstance(tags, list):
                tags = []
            dialogue.append({
                "source": source,
                "content": content[:200],  # cap length
                "tags": [str(t) for t in tags[:5] if isinstance(t, str)],
            })
        return dialogue[-8:]  # last 8 entries

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
        summary = self._apply_directive_batch(
            decision.directives,
            context,
            current_tick=current_tick,
            allowed_directives=allowed_directives,
            source="blackboard",
            subsystem_name="blackboard",
            round_index=0,
        )
        return self._summary_tuple(summary)

    def _apply_subsystem_results(
        self,
        results: list[Any],
        context: SettlementContext,
        *,
        current_tick: int,
        allowed_directives: set[str],
    ) -> tuple[int, int, int, int, list[str]]:
        summary = self._empty_apply_summary()
        for result in results:
            directives = result.directives if hasattr(result, "directives") else []
            if not isinstance(directives, list):
                continue
            apply_summary = self._apply_directive_batch(
                directives,
                context,
                current_tick=current_tick,
                allowed_directives=allowed_directives,
                source="subsystem",
                subsystem_name=self._metadata_string(
                    getattr(result, "metadata", {}),
                    "subsystem",
                    default="unknown",
                ),
                round_index=0,
            )
            self._merge_apply_summary(summary, apply_summary)
        return self._summary_tuple(summary)

    @staticmethod
    def _summary_tuple(summary: dict[str, Any]) -> tuple[int, int, int, int, list[str]]:
        return (
            int(summary.get("requested_count", 0)),
            int(summary.get("applied_count", 0)),
            int(summary.get("skipped_unsupported_count", 0)),
            int(summary.get("skipped_invalid_count", 0)),
            list(summary.get("applied_kinds", [])),
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

    def _apply_directive_batch(
        self,
        directives: list[Any],
        context: SettlementContext | None,
        *,
        current_tick: int,
        allowed_directives: set[str],
        source: str,
        subsystem_name: str,
        round_index: int,
    ) -> dict[str, Any]:
        summary = self._empty_apply_summary()
        for raw_directive in directives:
            expanded_directives = expand_planner_directive(raw_directive)
            summary["requested_count"] += len(expanded_directives)
            for expanded_directive in expanded_directives:
                validation = validate_planner_directive(
                    expanded_directive,
                    allowed_directives=allowed_directives,
                )
                if not validation.ok:
                    status = (
                        "unsupported"
                        if is_unsupported_directive_reason(validation.reason_code)
                        else "invalid_contract"
                    )
                    if status == "unsupported":
                        summary["skipped_unsupported_count"] += 1
                    else:
                        summary["skipped_invalid_count"] += 1
                    audit_entry = self._directive_audit_entry(
                        source=source,
                        subsystem_name=subsystem_name,
                        round_index=round_index,
                        validation=validation,
                        status=status,
                    )
                    summary["directive_audit"].append(audit_entry)
                    if status == "invalid_contract":
                        logger.warning(
                            "planner directive validation failed (invalid_contract): "
                            "subsystem=%s kind=%s reason=%s",
                            subsystem_name or "?",
                            validation.kind or "?",
                            validation.reason_code or "?",
                            extra={
                                "kind": validation.kind,
                                "status": status,
                                "reason_code": validation.reason_code,
                                "subsystem": subsystem_name,
                            },
                        )
                        self._pending_sse.append(SSEEvent(
                            event_type="planner_directive_rejected",
                            payload={
                                "kind": audit_entry["kind"],
                                "status": audit_entry["status"],
                                "reason_code": audit_entry["reason_code"],
                                "payload_digest": audit_entry["payload_digest"],
                            },
                        ))
                    continue

                if self._dispatcher is None:
                    summary["skipped_invalid_count"] += 1
                    rejected_entry = self._directive_audit_entry(
                        source=source,
                        subsystem_name=subsystem_name,
                        round_index=round_index,
                        validation=validation,
                        status="subsystem_rejected",
                        reason_code="missing_dispatcher",
                    )
                    summary["directive_audit"].append(rejected_entry)
                    logger.warning(
                        "planner directive rejected: missing_dispatcher (kind=%s, subsystem=%s)",
                        validation.kind, subsystem_name,
                    )
                    self._pending_sse.append(SSEEvent(
                        event_type="planner_directive_rejected",
                        payload={
                            "kind": rejected_entry["kind"],
                            "status": rejected_entry["status"],
                            "reason_code": rejected_entry["reason_code"],
                            "payload_digest": rejected_entry["payload_digest"],
                        },
                    ))
                    continue

                applied = self._dispatcher.apply_directive(
                    validation.kind,
                    validation.payload,
                    context,
                    current_tick=current_tick,
                )
                if applied is not True:
                    summary["skipped_invalid_count"] += 1
                    reason_code = applied if isinstance(applied, str) else "dispatcher_rejected"
                    dispatcher_rejected_entry = self._directive_audit_entry(
                        source=source,
                        subsystem_name=subsystem_name,
                        round_index=round_index,
                        validation=validation,
                        status="subsystem_rejected",
                        reason_code=reason_code,
                    )
                    summary["directive_audit"].append(dispatcher_rejected_entry)
                    logger.warning(
                        "planner directive rejected: %s (kind=%s, subsystem=%s)",
                        reason_code, validation.kind, subsystem_name,
                    )
                    self._pending_sse.append(SSEEvent(
                        event_type="planner_directive_rejected",
                        payload={
                            "kind": dispatcher_rejected_entry["kind"],
                            "status": dispatcher_rejected_entry["status"],
                            "reason_code": dispatcher_rejected_entry["reason_code"],
                            "payload_digest": dispatcher_rejected_entry["payload_digest"],
                        },
                    ))
                    continue

                summary["applied_count"] += 1
                summary["applied_kinds"].append(validation.kind)
                summary["directive_audit"].append(
                    self._directive_audit_entry(
                        source=source,
                        subsystem_name=subsystem_name,
                        round_index=round_index,
                        validation=validation,
                        status="applied",
                    )
                )

        return summary

    @staticmethod
    def _empty_apply_summary() -> dict[str, Any]:
        return {
            "requested_count": 0,
            "applied_count": 0,
            "skipped_unsupported_count": 0,
            "skipped_invalid_count": 0,
            "applied_kinds": [],
            "directive_audit": [],
        }

    @classmethod
    def _merge_apply_summary(
        cls,
        target: dict[str, Any],
        source_summary: dict[str, Any],
    ) -> None:
        target["requested_count"] += int(source_summary.get("requested_count", 0))
        target["applied_count"] += int(source_summary.get("applied_count", 0))
        target["skipped_unsupported_count"] += int(
            source_summary.get("skipped_unsupported_count", 0)
        )
        target["skipped_invalid_count"] += int(source_summary.get("skipped_invalid_count", 0))
        target["applied_kinds"].extend(source_summary.get("applied_kinds", []))
        target["directive_audit"].extend(source_summary.get("directive_audit", []))

    @staticmethod
    def _directive_audit_entry(
        *,
        source: str,
        subsystem_name: str,
        round_index: int,
        validation: DirectiveValidationResult,
        status: str,
        reason_code: str | None = None,
    ) -> dict[str, Any]:
        return {
            "source": source,
            "subsystem": subsystem_name,
            "round_index": round_index,
            "kind": validation.kind,
            "status": status,
            "reason_code": reason_code or validation.reason_code,
            "payload_digest": dict(validation.payload_digest),
        }

    @staticmethod
    def _new_round_subsystem_summary(name: str) -> dict[str, Any]:
        return {
            "name": name,
            "event_kinds": [],
            "accepted_event_count": 0,
            "requested_directive_count": 0,
            "applied_directive_count": 0,
            "skipped_unsupported_count": 0,
            "skipped_invalid_count": 0,
            "applied_kinds": [],
            "story_fact_count": 0,
            "directive_audit": [],
            "metadata": {},
        }

    @staticmethod
    def _merge_subsystem_metadata(
        summary: dict[str, Any],
        metadata: Mapping[str, Any] | None,
    ) -> None:
        if not isinstance(metadata, Mapping):
            return
        raw_target = summary.get("metadata")
        target = raw_target if isinstance(raw_target, dict) else {}
        for key, value in metadata.items():
            if key in {"subsystem", "event_kind"}:
                continue
            if key == "candidate_summary" and isinstance(value, Mapping):
                existing = target.get("candidate_summary")
                merged = dict(existing) if isinstance(existing, Mapping) else {}
                for nested_key, nested_value in value.items():
                    merged[str(nested_key)] = nested_value
                target["candidate_summary"] = merged
                continue
            if key == "rejected_directives" and isinstance(value, list):
                existing_list = target.get("rejected_directives")
                merged_list = list(existing_list) if isinstance(existing_list, list) else []
                merged_list.extend(
                    dict(entry) for entry in value if isinstance(entry, Mapping)
                )
                target["rejected_directives"] = merged_list
                continue
            if key == "filtered_directive_count":
                current = target.get("filtered_directive_count", 0)
                try:
                    target["filtered_directive_count"] = int(current) + int(value)
                except (TypeError, ValueError):
                    target["filtered_directive_count"] = current
                continue
            target[str(key)] = value
        summary["metadata"] = target

    @classmethod
    def _result_has_meaningful_output(
        cls,
        *,
        apply_summary: Mapping[str, Any] | None,
        applied_story_facts: int,
        strategy_notes: Any,
    ) -> bool:
        if isinstance(apply_summary, Mapping) and int(apply_summary.get("applied_count", 0) or 0) > 0:
            return True
        if applied_story_facts > 0:
            return True
        return bool(cls._string_or_empty(strategy_notes))

    @classmethod
    def _metadata_string(
        cls,
        metadata: Any,
        key: str,
        *,
        default: str = "",
    ) -> str:
        if isinstance(metadata, Mapping):
            return cls._string_or_empty(metadata.get(key)) or default
        return default

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
        planner_context["__session_id__"] = context.session_id

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
        directive_audit: list[dict[str, Any]] = []
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
            round_subsystems: dict[str, dict[str, Any]] = {}
            round_directive_audit: list[dict[str, Any]] = []

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
                event_accepted = False
                for result in dispatch_results:
                    subsystem_name = self._metadata_string(
                        getattr(result, "metadata", {}),
                        "subsystem",
                        default="unknown",
                    )
                    subsystem_summary = round_subsystems.setdefault(
                        subsystem_name,
                        self._new_round_subsystem_summary(subsystem_name),
                    )
                    event_kind = self._metadata_string(
                        getattr(result, "metadata", {}),
                        "event_kind",
                        default=semantic_event.kind,
                    )
                    if event_kind and event_kind not in subsystem_summary["event_kinds"]:
                        subsystem_summary["event_kinds"].append(event_kind)
                    self._merge_subsystem_metadata(
                        subsystem_summary,
                        _normalize_mapping(getattr(result, "metadata", {})),
                    )

                    directives = (
                        result.directives
                        if hasattr(result, "directives") and isinstance(result.directives, list)
                        else []
                    )
                    apply_summary = self._apply_directive_batch(
                        directives,
                        context,
                        current_tick=current_tick,
                        allowed_directives=allowed_directives,
                        source="subsystem",
                        subsystem_name=subsystem_name,
                        round_index=round_index,
                    )
                    subsystem_summary["requested_directive_count"] += int(
                        apply_summary.get("requested_count", 0)
                    )
                    subsystem_summary["applied_directive_count"] += int(
                        apply_summary.get("applied_count", 0)
                    )
                    subsystem_summary["skipped_unsupported_count"] += int(
                        apply_summary.get("skipped_unsupported_count", 0)
                    )
                    subsystem_summary["skipped_invalid_count"] += int(
                        apply_summary.get("skipped_invalid_count", 0)
                    )
                    subsystem_summary["applied_kinds"].extend(
                        apply_summary.get("applied_kinds", [])
                    )
                    subsystem_summary["directive_audit"].extend(
                        apply_summary.get("directive_audit", [])
                    )
                    round_requested_count += int(apply_summary.get("requested_count", 0))
                    round_applied_count += int(apply_summary.get("applied_count", 0))
                    round_skipped_unsupported_count += int(
                        apply_summary.get("skipped_unsupported_count", 0)
                    )
                    round_skipped_invalid_count += int(
                        apply_summary.get("skipped_invalid_count", 0)
                    )
                    round_applied_kinds.extend(apply_summary.get("applied_kinds", []))
                    round_directive_audit.extend(apply_summary.get("directive_audit", []))

                    story_facts = (
                        result.story_facts
                        if hasattr(result, "story_facts") and isinstance(result.story_facts, list)
                        else []
                    )
                    applied_story_facts = 0
                    if story_facts:
                        applied_story_facts = self._apply_story_facts(
                            self._normalize_story_facts(story_facts),
                            context,
                        )
                    subsystem_summary["story_fact_count"] += applied_story_facts
                    story_fact_count += applied_story_facts
                    result_accepted = self._result_has_meaningful_output(
                        apply_summary=apply_summary,
                        applied_story_facts=applied_story_facts,
                        strategy_notes=getattr(result, "strategy_notes", ""),
                    )
                    if result_accepted:
                        subsystem_summary["accepted_event_count"] += 1
                        event_accepted = True
                if event_accepted:
                    accepted_event_count += 1

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
                    "applied_directive_count": round_applied_count,
                    "skipped_unsupported_count": round_skipped_unsupported_count,
                    "skipped_invalid_count": round_skipped_invalid_count,
                    "applied_directive_kinds": list(round_applied_kinds),
                    "new_change_count": new_change_count,
                    "inline_event_transition_count": len(inline_payloads),
                    "subsystems": list(round_subsystems.values()),
                }
            )
            requested_count += round_requested_count
            applied_count += round_applied_count
            skipped_unsupported_count += round_skipped_unsupported_count
            skipped_invalid_count += round_skipped_invalid_count
            applied_kinds.extend(round_applied_kinds)
            directive_audit.extend(round_directive_audit)

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
            "directive_audit": directive_audit,
            "blackboard_summary": {},
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
            history_key = getattr(self.blackboard, "history_key", "__planner__")
            participants[str(history_key)] = self.blackboard
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
        result = context.execute_command(
            Command(
                type="planner_add_story_facts",
                params={"facts": list(facts)},
                source="narrative_planner",
            )
        )
        if not result.executed:
            return 0
        graph = context.knowledge_graph
        if graph is not None:
            try:
                graph.inject_story_facts(facts, session_id=context.session_id)
            except Exception:
                logger.exception("failed to inject story_facts into knowledge graph")
        return len(facts)

    @staticmethod
    def _commit_runtime_state(
        context: SettlementContext,
        **params: Any,
    ) -> bool:
        result = context.execute_command(
            Command(
                type="planner_commit_runtime_state",
                params=params,
                source="narrative_planner",
            )
        )
        return result.executed

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
        # R3-A: include success_conditions as structured list[dict] for Planner
        raw_success = getattr(template, "success_conditions", None) or []
        success_conditions_serialized: list[dict[str, Any]] = []
        for cond in raw_success:
            if isinstance(cond, dict):
                success_conditions_serialized.append(cond)
            else:
                # MilestoneCondition dataclass: .type, .params, .optional
                entry: dict[str, Any] = {"type": str(getattr(cond, "type", ""))}
                params = getattr(cond, "params", None)
                if isinstance(params, dict) and params:
                    entry["params"] = dict(params)
                optional = getattr(cond, "optional", False)
                if optional:
                    entry["optional"] = True
                success_conditions_serialized.append(entry)
        return {
            "key_elements": list(template.key_elements),
            "involved_npcs": list(template.involved_npcs),
            "involved_locations": list(template.involved_locations),
            "narrative_context": template.narrative_context,
            "failure_fallback": template.failure_fallback,
            "success_conditions": success_conditions_serialized,
        }

    @staticmethod
    def _build_planner_feedback(
        context: SettlementContext,
        *,
        current_tick: int,
    ) -> dict[str, Any]:
        """R3-C: Build a rich feedback dict for the Planner.

        Returns keys:
        - quest_progress: list[dict] — active quests with obj completion ratio
        - milestone_satisfaction: list[dict] — current milestone conditions (met/not_met)
        - npc_directive_confirmations: list[dict] — recent npc_directives vs talked_to flags
        - clue_interaction_summary: dict — discovered/examined/resolved/unexamined counts
        """
        result: dict[str, Any] = {}

        # 1. Quest progress (active quests with objectives completion ratio)
        quest_progress: list[dict[str, Any]] = []
        if context.state.has_slice("quests"):
            active_statuses = {"in_progress", "active", "accepted"}
            for q_id, q_data in context.state.quests.dynamic_quests.items():
                if not isinstance(q_data, Mapping):
                    continue
                if str(q_data.get("status", "")).strip().lower() not in active_statuses:
                    continue
                raw_objectives = q_data.get("objectives", [])
                if not isinstance(raw_objectives, list) or not raw_objectives:
                    continue
                total = len(raw_objectives)
                completed = sum(
                    1 for obj in raw_objectives
                    if isinstance(obj, Mapping) and bool(obj.get("completed", False))
                )
                quest_progress.append({
                    "quest_id": str(q_id),
                    "title": str(q_data.get("title", q_id)),
                    "completed_objectives": completed,
                    "total_objectives": total,
                    "ratio": round(completed / total, 2) if total else 0.0,
                })
        result["quest_progress"] = quest_progress

        # 2. Milestone condition satisfaction (current target milestone)
        milestone_satisfaction: list[dict[str, Any]] = []
        if (
            context.state.has_slice("quests")
            and context.world.has_registry("quests")
        ):
            target_ms = context.state.narrative_plan.current_target_milestone
            if target_ms:
                template = context.world.quests.get_milestone(target_ms)
                if template is not None:
                    from app.game_core.orchestration.event_engine import BasicEventConditionEvaluator
                    from app.game_core.orchestration.hooks.milestone_completion import (
                        _condition_to_dict,
                        _has_condition_type,
                    )
                    evaluator = BasicEventConditionEvaluator()
                    raw_conds = getattr(template, "success_conditions", None) or []
                    for cond in raw_conds:
                        if not _has_condition_type(cond):
                            continue
                        cond_dict = _condition_to_dict(cond)
                        met, _ = evaluator._condition_met(context.state, cond_dict)
                        cond_type = str(cond_dict.get("type", "?"))
                        cond_params = cond_dict.get("params", {})
                        milestone_satisfaction.append({
                            "type": cond_type,
                            "params": cond_params if isinstance(cond_params, dict) else {},
                            "met": met,
                            "optional": bool(getattr(cond, "optional", cond_dict.get("optional", False))),
                        })
        result["milestone_satisfaction"] = milestone_satisfaction

        # 3. NPC directive execution confirmation (recent unconsumed directives vs talked_to flags)
        npc_directive_confirmations: list[dict[str, Any]] = []
        if context.state.has_slice("narrative_plan") and context.state.has_slice("flags"):
            for d in context.state.narrative_plan.npc_directives:
                if not isinstance(d, Mapping):
                    continue
                if d.get("consumed", False):
                    continue
                expires = d.get("expires_at_tick", current_tick + 1)
                if isinstance(expires, (int, float)) and expires < current_tick:
                    continue
                npc_id = str(d.get("npc_id", "")).strip()
                if not npc_id:
                    continue
                # Check if talked_to_{npc_id} flag is set as confirmation
                flag_key = f"talked_to_{npc_id}"
                flag_value = context.state.flags.get(flag_key)
                talked = bool(flag_value) if flag_value is not None else False
                npc_directive_confirmations.append({
                    "npc_id": npc_id,
                    "directive_kind": d.get("directive", {}).get("kind", "?") if isinstance(d.get("directive"), Mapping) else "?",
                    "issued_at_tick": d.get("issued_at_tick", 0),
                    "talked_flag_set": talked,
                })
        result["npc_directive_confirmations"] = npc_directive_confirmations

        # 4. Clue interaction summary (current area)
        clue_summary: dict[str, Any] = {}
        if (
            context.state.has_slice("areas")
            and context.state.has_slice("player")
        ):
            area_id = context.state.player.current_area
            if area_id and area_id in context.state.areas.areas:
                area_state = context.state.areas.areas[area_id]
                discovered = 0
                examined = 0
                resolved = 0
                unexamined = 0
                for _iid, istate in area_state.interactable_states.items():
                    if not isinstance(istate, Mapping):
                        continue
                    # Only count clue interactables
                    ft = str(istate.get("functional_type", "")).strip()
                    if ft != "investigate_clue":
                        continue
                    discovered += 1
                    if istate.get("resolved_option_id"):
                        resolved += 1
                    elif istate.get("first_inspected"):
                        examined += 1
                    else:
                        unexamined += 1
                if discovered:
                    clue_summary = {
                        "discovered": discovered,
                        "examined": examined,
                        "resolved": resolved,
                        "unexamined": unexamined,
                    }
        result["clue_interaction_summary"] = clue_summary

        return result

    @staticmethod
    @staticmethod
    def _build_available_items(context: SettlementContext) -> list[dict[str, Any]]:
        """Extract daily/misc items from ItemRegistry for planner context."""
        if not context.world.has_registry("items"):
            return []
        _DAILY_TAGS = {"material", "herbs", "daily", "gift", "food", "misc", "giftable"}
        items: list[dict[str, Any]] = []
        for template in context.world.items.list_all():
            if not template.id:
                continue
            tags = set(template.tags or [])
            if not tags.intersection(_DAILY_TAGS):
                continue
            items.append({
                "id": template.id,
                "name": getattr(template, "name", template.id),
                "tags": list(tags),
            })
            if len(items) >= 50:
                break
        return items

    @staticmethod
    def _get_area_danger(context: SettlementContext) -> float:
        if not context.state.has_slice("areas") or not context.state.has_slice("player"):
            return 0.0
        area_id = context.state.player.current_area
        if not area_id or area_id not in context.state.areas.areas:
            return 0.0
        return context.state.areas.areas[area_id].danger_level

    @staticmethod
    def _build_discoverable_rooms_hidden(context: SettlementContext) -> list[dict[str, Any]]:
        """List rooms that are discoverable but not yet discovered by the player."""
        if not context.state.has_slice("areas") or not context.state.has_slice("player"):
            return []
        if not context.world.has_registry("maps"):
            return []
        area_id = context.state.player.current_area
        if not area_id:
            return []
        hidden: list[dict[str, Any]] = []
        area_tmpl = context.world.maps.get(area_id)
        if area_tmpl is None:
            return []
        for sub_loc_id, sub_loc in area_tmpl.sub_locations.items():
            for room_id, room_tmpl in getattr(sub_loc, "rooms", {}).items():
                if not getattr(room_tmpl, "discoverable", False):
                    continue
                if context.state.areas.is_room_discovered(area_id, sub_loc_id, room_id):
                    continue
                hidden.append({
                    "area_id": area_id,
                    "location_id": str(sub_loc_id),
                    "room_id": str(room_id),
                    "name": getattr(room_tmpl, "name", str(room_id)),
                })
        return hidden

    @staticmethod
    def _build_dynamic_location_capacity(context: SettlementContext) -> dict[str, Any]:
        """Return dynamic room counts per sub_location for the current area."""
        if not context.state.has_slice("areas") or not context.state.has_slice("player"):
            return {}
        area_id = context.state.player.current_area
        if not area_id:
            return {}
        result: dict[str, Any] = {}
        if not context.world.has_registry("maps"):
            return {}
        area_tmpl = context.world.maps.get(area_id)
        if area_tmpl is None:
            return {}
        for sub_loc_id in area_tmpl.sub_locations.keys():
            count = context.state.areas.count_dynamic_rooms(area_id, str(sub_loc_id))
            result[str(sub_loc_id)] = {"dynamic_rooms": count, "max_dynamic_rooms": 5}
        return result

    @staticmethod
    def _build_static_room_ids(context: SettlementContext) -> dict[str, list[str]]:
        """Return static room IDs per sub_location for the current area."""
        if not context.state.has_slice("player") or not context.world.has_registry("maps"):
            return {}
        area_id = context.state.player.current_area
        if not area_id:
            return {}
        area_tmpl = context.world.maps.get(area_id)
        if area_tmpl is None:
            return {}
        result: dict[str, list[str]] = {}
        for sub_loc_id, sub_tmpl in area_tmpl.sub_locations.items():
            room_ids = list(sub_tmpl.rooms.keys())
            if room_ids:
                result[str(sub_loc_id)] = room_ids
        return result

    @staticmethod
    def _build_scene_interactable_capacity(context: SettlementContext) -> dict[str, Any]:
        if not context.state.has_slice("areas") or not context.state.has_slice("player"):
            return {}
        area_id = str(context.state.player.current_area or "").strip()
        location_id = str(context.state.player.current_location or "").strip()
        room_id = str(getattr(context.state.player, "current_room", None) or "").strip()
        if not area_id or not location_id:
            return {}
        overlay_count = context.state.areas.count_scoped_interactable_overlays(
            area_id,
            location_id,
            room_id or None,
        )
        merged_count = len(list_visible_scene_interactables(context.state, context.world))
        return {
            "area_id": area_id,
            "location_id": location_id,
            "room_id": room_id or None,
            "merged_interactables": merged_count,
            "overlay_count": overlay_count,
            "max_overlay_count": 4,
            "remaining_overlay_slots": max(0, 4 - overlay_count),
        }

    @classmethod
    def _build_completed_dynamic_quest_summaries(
        cls,
        dynamic_quests: Mapping[str, Any],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        completed: list[tuple[int, str, dict[str, Any]]] = []
        report_ready: list[tuple[int, str, dict[str, Any]]] = []
        for raw_quest_id, raw_quest in dynamic_quests.items():
            quest_id = cls._coerce_non_empty_string(raw_quest_id)
            if quest_id is None or not isinstance(raw_quest, Mapping):
                continue
            runtime_quest = normalize_runtime_dynamic_quest(quest_id, raw_quest)
            status = cls._string_or_empty(runtime_quest.get("status")).strip().lower()
            if status != "completed":
                continue
            summary = cls._summarize_completed_dynamic_quest(
                quest_id=quest_id,
                raw_quest=raw_quest,
                runtime_quest=runtime_quest,
            )
            sort_key = cls._coerce_int(raw_quest.get("created_at_tick")) or -1
            completed.append((sort_key, quest_id, summary))
            if bool(runtime_quest.get("can_report")):
                report_ready.append((sort_key, quest_id, summary))

        completed.sort(key=lambda item: (-item[0], item[1]))
        report_ready.sort(key=lambda item: (-item[0], item[1]))
        return (
            [dict(summary) for _, _, summary in completed[:5]],
            [dict(summary) for _, _, summary in report_ready[:3]],
        )

    @classmethod
    def _summarize_completed_dynamic_quest(
        cls,
        *,
        quest_id: str,
        raw_quest: Mapping[str, Any],
        runtime_quest: Mapping[str, Any],
    ) -> dict[str, Any]:
        completed_objectives: list[str] = []
        raw_objectives = raw_quest.get("objectives", [])
        if isinstance(raw_objectives, list):
            for raw_objective in raw_objectives:
                if isinstance(raw_objective, Mapping):
                    description = cls._coerce_non_empty_string(
                        raw_objective.get("description")
                    )
                else:
                    description = cls._coerce_non_empty_string(raw_objective)
                if description is not None:
                    completed_objectives.append(description)

        rewards = (
            dict(raw_quest.get("rewards", {}))
            if isinstance(raw_quest.get("rewards"), Mapping)
            else {}
        )

        return {
            "quest_id": quest_id,
            "title": cls._string_or_empty(raw_quest.get("title")),
            "status": cls._string_or_empty(runtime_quest.get("status")),
            "summary": cls._string_or_empty(raw_quest.get("summary")),
            "requires_report": bool(runtime_quest.get("requires_report")),
            "reported": bool(runtime_quest.get("reported")),
            "can_report": bool(runtime_quest.get("can_report")),
            "completed_objectives": completed_objectives,
            "rewards": rewards,
        }

    @classmethod
    def _normalize_decision(cls, raw: Any) -> NarrativePlannerDecision:
        if isinstance(raw, NarrativePlannerDecision):
            return NarrativePlannerDecision(
                directives=list(raw.directives),
                story_facts=cls._normalize_story_facts(raw.story_facts),
                strategy_notes=cls._string_or_empty(raw.strategy_notes),
                next_scheduled_tick=raw.next_scheduled_tick,
                metadata=_normalize_mapping(raw.metadata),
                outline_updates=dict(raw.outline_updates) if isinstance(raw.outline_updates, Mapping) else {},
                outline=raw.outline,
                player_hint=raw.player_hint,
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
        raw_outline_updates = raw.get("outline_updates")
        outline_updates = (
            dict(raw_outline_updates)
            if isinstance(raw_outline_updates, Mapping)
            else {}
        )
        raw_outline = raw.get("outline")
        outline = (
            dict(raw_outline)
            if isinstance(raw_outline, Mapping) and raw_outline.get("steps")
            else None
        )
        raw_hint = raw.get("player_hint")
        player_hint = str(raw_hint).strip() if isinstance(raw_hint, str) and raw_hint.strip() else None
        return NarrativePlannerDecision(
            directives=directives,
            story_facts=cls._normalize_story_facts(raw.get("story_facts")),
            strategy_notes=cls._string_or_empty(raw.get("strategy_notes")),
            next_scheduled_tick=next_tick,
            metadata=_normalize_mapping(raw.get("metadata")),
            outline_updates=outline_updates,
            outline=outline,
            player_hint=player_hint,
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
        return normalize_planner_directive(raw)

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

    @staticmethod
    def _coerce_int(value: Any) -> int | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _string_or_empty(cls, value: Any) -> str:
        return cls._coerce_non_empty_string(value) or ""

    # ------------------------------------------------------------------
    # Content-layer service bootstrap (Phase 4)
    # ------------------------------------------------------------------

    def _bootstrap_content_services(self, context: "SettlementContext") -> None:
        """Inject shop.services from character templates into NarrativePlanSlice.

        Idempotent: if an NPC already has any service with source="content",
        that NPC is skipped entirely to avoid duplicate injection on repeated
        bootstrap calls.

        "donation" entries are skipped — they are handled by DonationHandler.
        Unknown service_ids get an empty effects list so the NPC can discuss
        them in dialogue but the execute_service tool cannot mechanically execute.
        """
        if not context.state.has_slice("narrative_plan"):
            return
        if not context.world.has_registry("characters"):
            return

        for template in context.world.characters.list_all():
            npc_id = template.id
            shop = template.shop
            if not isinstance(shop, Mapping):
                continue
            raw_services = shop.get("services", [])
            if not isinstance(raw_services, list) or not raw_services:
                continue

            # Idempotency: skip NPC if any content-sourced service already exists
            existing = context.state.narrative_plan.get_services(npc_id)
            if any(str(s.get("source", "")) == "content" for s in existing):
                continue

            for raw_svc in raw_services:
                if not isinstance(raw_svc, Mapping):
                    continue
                service_id = str(
                    raw_svc.get("service_id") or raw_svc.get("id") or ""
                ).strip()
                if not service_id:
                    continue
                # Skip donation — handled by dedicated DonationHandler
                if service_id == "donation":
                    continue
                label = str(raw_svc.get("label") or service_id).strip() or service_id
                try:
                    price = int(raw_svc.get("price", 0))
                except (TypeError, ValueError):
                    price = 0
                notes = str(
                    raw_svc.get("notes") or raw_svc.get("availability") or ""
                ).strip()
                effects = list(_CONTENT_SERVICE_EFFECTS.get(service_id, []))
                svc_dict: dict[str, Any] = {
                    "service_id": service_id,
                    "npc_id": npc_id,
                    "label": label,
                    "price": price,
                    "notes": notes,
                    "effects": effects,
                    "preconditions": {},
                    "one_shot": False,
                    "assigned_tick": 0,
                    "expiry_tick": 0,  # never expires
                    "source": "content",
                }
                context.state.narrative_plan.assign_service(npc_id, svc_dict)
