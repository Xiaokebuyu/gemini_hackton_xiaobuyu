"""NarrativePlannerHook implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any, Mapping, Protocol

from app.game_core.narrative.instance_manager import InstanceManager
from app.game_core.orchestration.event_engine import _normalize_mapping
from app.game_core.orchestration.hooks.base import NoOpSettlementHook
from app.game_core.orchestration.hooks.rest_phase import (
    is_quiet_rest_slot,
    resolve_rest_phase,
)
from app.game_core.orchestration.models import HookResult, SSEEvent
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.planning import DynamicSubAreaManager, NarrativePlanner
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

_TICK_KIND_TRAVEL = frozenset({"move_area", "enter_sub_location", "leave_sub_location"})
_TICK_KIND_REST = frozenset({"rest_short", "rest_long", "night_watch", "set_camp"})
_TICK_KIND_CONVERSATION = frozenset(
    {"speak", "dialogue", "talk", "emote", "dialogue_turn", "private_chat_turn"}
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
    }
    def __init__(
        self,
        planner: NarrativePlannerProvider | None = None,
        *,
        instance_manager: InstanceManager | None = None,
        sub_area_manager: DynamicSubAreaManager | None = None,
    ) -> None:
        self.planner = planner or NarrativePlanner()
        self._bootstrap_planner = NarrativePlanner()
        self._instance_manager = instance_manager
        self._sub_area_manager = sub_area_manager

    def should_skip(
        self,
        change_log: list[StateChange],
        action_log: list[dict[str, Any]] | None = None,
    ) -> bool:
        del action_log
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
        (
            requested_count,
            applied_count,
            skipped_unsupported_count,
            skipped_invalid_count,
            applied_kinds,
        ) = self._apply_normalized_decision(
            decision,
            context,
            current_tick=current_tick,
            allowed_directives=self._SUPPORTED_DIRECTIVES,
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
        derived_style_tags = self._derive_play_style_tags(
            context.state.narrative_plan.behavior_window
        )
        if derived_style_tags != context.state.narrative_plan.play_style_tags:
            context.state.narrative_plan.play_style_tags = list(derived_style_tags)
            context.state.narrative_plan._dirty = True

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
        # Detect FAILED milestone transitions and surface failure_fallback
        for change in context.change_log:
            if (
                change.slice == "quests"
                and change.path.startswith("milestone_states.")
                and isinstance(change.value, dict)
                and change.value.get("state") == "FAILED"
            ):
                milestone_id = change.path[len("milestone_states."):]
                fallback: str | None = None
                if context.world.has_registry("quests"):
                    tmpl = context.world.quests.get_milestone(milestone_id)
                    fallback = tmpl.failure_fallback if tmpl else None
                sse_events.append(SSEEvent(
                    event_type="milestone_failed",
                    payload={
                        "milestone_id": milestone_id,
                        "failure_fallback": fallback or "",
                    },
                ))

        # Directive GC — prune consumed and expired entries each planning cycle
        context.state.narrative_plan.prune_consumed_and_expired(current_tick)
        for expired_payload in self._expire_dynamic_quests(
            context,
            current_tick=current_tick,
        ):
            sse_events.append(
                SSEEvent(
                    event_type="dynamic_quest_expired",
                    payload=expired_payload,
                )
            )
        self._despawn_expired_quest_npcs(
            context,
            current_tick=current_tick,
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

    async def bootstrap(self, context: SettlementContext) -> HookResult:
        """Seed opening quests without advancing normal planner bookkeeping."""
        if not context.state.has_slice("narrative_plan"):
            return HookResult(metadata=self._noop_metadata(reason="missing_slice"))
        if not context.state.has_slice("quests"):
            return HookResult(metadata=self._noop_metadata(reason="missing_slice"))
        if not context.state.has_slice("time"):
            return HookResult(metadata=self._noop_metadata(reason="missing_slice"))

        current_tick = context.state.time.absolute_tick()
        planner_context = self._build_planner_context(context, current_tick=current_tick)
        try:
            decision = self._bootstrap_decision(planner_context)
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
                    "requested_count": 0,
                    "applied_count": 0,
                    "skipped_unsupported_count": 0,
                    "skipped_invalid_count": 0,
                    "applied_kinds": [],
                    "planner_metadata": {},
                },
            )

        (
            requested_count,
            applied_count,
            skipped_unsupported_count,
            skipped_invalid_count,
            applied_kinds,
        ) = self._apply_normalized_decision(
            decision,
            context,
            current_tick=current_tick,
            allowed_directives=self._BOOTSTRAP_DIRECTIVES,
        )
        if applied_count > 0 and decision.strategy_notes:
            context.state.narrative_plan.set_strategy(decision.strategy_notes)
        return HookResult(
            metadata={
                "status": "updated" if applied_count > 0 else "noop",
                "evaluated": True,
                "reason": "bootstrap",
                "current_tick": current_tick,
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

    def _bootstrap_decision(self, planner_context: dict[str, Any]) -> NarrativePlannerDecision:
        normalized = self._bootstrap_planner._normalize_context(planner_context)
        if normalized is None:
            return self._normalize_decision(
                self._bootstrap_planner._noop(current_tick=0, reason="invalid_context")
            )
        raw = self._bootstrap_planner._try_seed_quest(normalized)
        if raw is None:
            raw = self._bootstrap_planner._noop(
                current_tick=normalized["current_tick"],
                reason="bootstrap_stable",
            )
        return self._normalize_decision(raw)

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

        for raw_directive in decision.directives:
            normalized = self._normalize_directive(raw_directive)
            if normalized is None:
                skipped_invalid_count += 1
                continue
            kind, payload = normalized
            if kind not in allowed_directives:
                skipped_unsupported_count += 1
                continue
            if not self._apply_directive(kind, payload, context, current_tick=current_tick):
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

    @classmethod
    def _normalize_decision(cls, raw: Any) -> NarrativePlannerDecision:
        if isinstance(raw, NarrativePlannerDecision):
            return NarrativePlannerDecision(
                directives=list(raw.directives),
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
            strategy_notes=cls._string_or_empty(raw.get("strategy_notes")),
            next_scheduled_tick=next_tick,
            metadata=_normalize_mapping(raw.get("metadata")),
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
            metadata = _normalize_mapping(payload.get("metadata"))
            raw_objectives = payload.get("objectives")
            if not isinstance(raw_objectives, list):
                raw_objectives = metadata.get("objectives")
            objectives = raw_objectives if isinstance(raw_objectives, list) else []
            raw_rewards = payload.get("rewards")
            if not isinstance(raw_rewards, Mapping):
                raw_rewards = metadata.get("rewards")
            rewards = raw_rewards if isinstance(raw_rewards, Mapping) else {}
            raw_expiry_ticks = payload.get("expiry_ticks")
            if raw_expiry_ticks is None:
                raw_expiry_ticks = metadata.get("expiry_ticks")
            expiry_ticks: int | None = None
            if raw_expiry_ticks is not None:
                try:
                    expiry_ticks = int(raw_expiry_ticks)
                except (TypeError, ValueError):
                    return False
            urgency = self._coerce_non_empty_string(metadata.get("urgency")) or "medium"
            on_expire = self._coerce_non_empty_string(payload.get("on_expire"))
            if on_expire is None:
                on_expire = self._coerce_non_empty_string(metadata.get("on_expire"))
            if on_expire is None:
                on_expire = "ignore"
            on_expire = on_expire.strip().lower()
            if on_expire not in {"ignore", "escalate", "retire"}:
                on_expire = "ignore"
            generated_raw = metadata.get("generated_by_escalation")
            try:
                generated_by_escalation = int(generated_raw)
            except (TypeError, ValueError):
                generated_by_escalation = 0
            target_milestone = self._coerce_non_empty_string(
                metadata.get("source_milestone")
            )
            delivery_method = (
                self._coerce_non_empty_string(payload.get("delivery_method"))
                or self._coerce_non_empty_string(metadata.get("delivery_method"))
                or "board"
            )
            quest_payload = {
                "quest_id": quest_id,
                "status": status,
                "title": self._string_or_empty(payload.get("title")),
                "summary": self._string_or_empty(payload.get("summary")),
                "source": "narrative_planner",
                "created_at_tick": current_tick,
                "target_milestone": target_milestone,
                "urgency": urgency,
                "objectives": objectives,
                "rewards": rewards,
                "delivery_method": delivery_method,
                "expiry_ticks": expiry_ticks,
                "on_expire": on_expire,
                "generated_by_escalation": generated_by_escalation,
                "planner_reasoning": self._string_or_empty(metadata.get("planner_reasoning")),
                "metadata": metadata,
            }
            context.state.quests.add_dynamic_quest(quest_id, quest_payload)
            context.state.narrative_plan.add_history(
                {"kind": "create_quest", "quest_id": quest_id, "tick": current_tick}
            )
            context.record_change(StateChange(slice="quests", operation="set", path=f"dynamic.{quest_id}", value=quest_payload))
            # P1-C Phase 1: map milestone success/failure conditions to EventSlice events
            self._create_milestone_condition_events(quest_id, context, current_tick=current_tick)
            self._create_objective_events(
                quest_id,
                quest_payload,
                context,
                current_tick=current_tick,
            )
            return True

        if kind == "direct_npc":
            npc_id = self._coerce_non_empty_string(payload.get("npc_id"))
            if npc_id is None:
                return False
            directive = payload.get("directive")
            if directive is not None and not isinstance(directive, Mapping):
                return False
            if not directive:
                return False
            expires_at_tick = current_tick + 24
            raw_expiry = payload.get("expires_at_tick")
            if raw_expiry is not None:
                try:
                    expires_at_tick = int(raw_expiry)
                except (TypeError, ValueError):
                    return False
            priority = payload.get("priority")
            if isinstance(priority, str):
                normalized_priority = priority.strip().lower() or "medium"
            else:
                normalized_priority = "medium"
            if normalized_priority not in {"high", "medium", "low"}:
                return False
            stored_directive = context.state.narrative_plan.add_directive(
                {
                    "npc_id": npc_id,
                    "directive": dict(directive) if isinstance(directive, Mapping) else {},
                    "priority": normalized_priority,
                    "issued_at_tick": current_tick,
                    "expires_at_tick": expires_at_tick,
                    "linked_quest_id": self._coerce_non_empty_string(payload.get("linked_quest_id")),
                    "source": "narrative_planner",
                    "consumed": False,
                }
            )
            if self._instance_manager is not None:
                self._instance_manager.inject_directive(
                    npc_id,
                    stored_directive,
                    current_tick=current_tick,
                )
            return True

        if kind == "publish_bulletin":
            board_id = self._coerce_non_empty_string(payload.get("board_id"))
            if board_id is None:
                return False
            if not context.state.has_slice("areas"):
                return False
            area_id = self._coerce_non_empty_string(payload.get("area_id"))
            if area_id is None and context.state.has_slice("player"):
                area_id = context.state.player.current_area
            if area_id is None:
                return False
            location_payload = payload.get("location")
            resolved_area_id = self._coerce_non_empty_string(
                location_payload.get("area_id")) if isinstance(location_payload, Mapping) else None
            resolved_sub_location = self._coerce_non_empty_string(
                location_payload.get("sub_location")) if isinstance(location_payload, Mapping) else None
            area_id = resolved_area_id or area_id
            if not area_id:
                return False
            metadata = _normalize_mapping(payload.get("metadata"))
            quest_id = self._coerce_non_empty_string(metadata.get("quest_id"))
            notify_resident_npcs = bool(payload.get("notify_resident_npcs", False))
            board_entry: dict[str, str] = {
                "board_id": board_id,
                "quest_id": quest_id or "",
                "title": self._string_or_empty(payload.get("title")),
                "content": self._string_or_empty(payload.get("content")),
                "published_at_tick": current_tick,
                "source": "narrative_planner",
                "area_id": area_id,
            }
            if resolved_sub_location is not None:
                board_entry["sub_location"] = resolved_sub_location
                board_entry["location"] = {
                    "area_id": area_id,
                    "sub_location": resolved_sub_location,
                }
            context.state.areas.add_board_bulletin(
                area_id,
                board_id,
                board_entry,
            )
            if notify_resident_npcs:
                for npc_id in self._resident_npcs_for_board(
                    context=context,
                    area_id=area_id,
                    board_id=board_id,
                    sub_location=resolved_sub_location,
                ):
                    self._apply_directive(
                        "direct_npc",
                        {
                            "npc_id": npc_id,
                            "directive": {
                                "kind": "bulletin_awareness",
                                "board_id": board_id,
                                "quest_id": quest_id,
                                "source_milestone": self._coerce_non_empty_string(metadata.get("source_milestone")),
                                "notice": self._string_or_empty(payload.get("title")),
                                "metadata": metadata,
                            },
                        },
                        context=context,
                        current_tick=current_tick,
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
            context.record_change(StateChange(slice="quests", operation="set", path=f"dynamic.{quest_id}.status", value="retired"))
            return True

        if kind == "spawn_quest_npc":
            npc_id = self._coerce_non_empty_string(payload.get("npc_id"))
            if not context.state.has_slice("areas"):
                return False
            area_id = self._coerce_non_empty_string(payload.get("area_id"))
            if not area_id or area_id not in context.state.areas.areas:
                return False
            auto_spawn = False
            if npc_id is None:
                npc_id = f"temp_npc_{current_tick}"
                auto_spawn = True
            if auto_spawn:
                npc_id = self._find_available_npc_id(context, npc_id, max_attempts=20)
            elif context.state.areas.find_npc_area(npc_id) is not None:
                return False
            location_id = self._coerce_non_empty_string(payload.get("location_id"))
            name = self._string_or_empty(payload.get("name")) or None
            appearance = self._string_or_empty(payload.get("appearance"))
            personality = self._string_or_empty(payload.get("personality"))
            dialogue_hook = self._string_or_empty(payload.get("dialogue_hook"))
            raw_tags = payload.get("tags")
            tags = [
                str(tag)
                for tag in raw_tags
                if isinstance(tag, str)
            ] if isinstance(raw_tags, list) else []
            linked_quest_id = self._coerce_non_empty_string(
                payload.get("linked_quest_id")
            )
            metadata = _normalize_mapping(payload.get("metadata"))
            if linked_quest_id is None:
                linked_quest_id = self._coerce_non_empty_string(
                    metadata.get("linked_quest_id")
                )
            raw_despawn_tick = payload.get("despawn_tick")
            if raw_despawn_tick is None:
                raw_despawn_tick = metadata.get("despawn_tick")
            try:
                despawn_tick = int(raw_despawn_tick) if raw_despawn_tick is not None else None
            except (TypeError, ValueError):
                despawn_tick = None
            if despawn_tick is None:
                raw_despawn_in_ticks = payload.get("despawn_in_ticks")
                if raw_despawn_in_ticks is None:
                    raw_despawn_in_ticks = metadata.get("despawn_in_ticks")
                try:
                    despawn_in_ticks = int(raw_despawn_in_ticks) if raw_despawn_in_ticks is not None else None
                except (TypeError, ValueError):
                    despawn_in_ticks = None
                if despawn_in_ticks is None:
                    despawn_in_ticks = 24
                despawn_tick = current_tick + max(0, despawn_in_ticks)
            npc_profile = {
                "npc_id": npc_id,
                "name": self._string_or_empty(name) or npc_id,
                "appearance": appearance,
                "personality": personality,
                "dialogue_hook": dialogue_hook,
                "description": self._string_or_empty(payload.get("description")),
                "role": self._string_or_empty(payload.get("role")),
                "tags": tags,
                "linked_quest_id": linked_quest_id,
                "area_id": area_id,
                "location_id": location_id,
                "despawn_tick": despawn_tick,
                "issued_at_tick": current_tick,
            }
            context.state.narrative_plan.add_history({
                "kind": "spawn_quest_npc",
                "npc_id": npc_id,
                "name": name,
                "appearance": appearance,
                "personality": personality,
                "dialogue_hook": dialogue_hook,
                "tags": tags,
                "linked_quest_id": linked_quest_id,
                "role": self._string_or_empty(payload.get("role")),
                "location_id": location_id,
                "area_id": area_id,
                "issued_at_tick": current_tick,
                "despawn_tick": despawn_tick,
            })
            context.state.narrative_plan.add_temporary_npc(npc_id, npc_profile)
            context.state.areas.move_npc(npc_id, area_id, location_id)
            context.record_change(StateChange(slice="areas", operation="set", path=f"npc_location.{npc_id}", value=area_id))
            context.state.narrative_plan.add_directive({
                "npc_id": npc_id,
                "directive": {
                    "kind": "spawn_quest_npc",
                    "role": self._string_or_empty(payload.get("role")),
                    "description": self._string_or_empty(payload.get("description")),
                    "personality": personality,
                    "dialogue_hook": dialogue_hook,
                    "name": name or "",
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
            raw_dc = payload.get("dc")
            try:
                discovery_dc = int(raw_dc)
            except (TypeError, ValueError):
                discovery_dc = 12
            spec = {
                "id": clue_id,
                "label": self._string_or_empty(payload.get("description")),
                "description": self._string_or_empty(payload.get("description")),
                "type": "discovery",
                "tier": "temporary",
                "discovery_mode": (
                    self._coerce_non_empty_string(payload.get("discovery_mode"))
                    or "check"
                ),
                "discovery_dc": discovery_dc,
                "linked_quest_id": self._coerce_non_empty_string(payload.get("linked_quest_id")),
                "linked_milestone": self._coerce_non_empty_string(payload.get("linked_milestone")),
                "source": "narrative_planner",
                "created_at_tick": current_tick,
                "expiry_ticks": payload.get("expiry_ticks", 12),
            }
            manager = self._resolve_sub_area_manager(context)
            created = manager.create(area_id, spec)
            if created is None:
                return False
            context.record_change(
                StateChange(
                    slice="areas",
                    operation="set",
                    path=f"{area_id}.temporary_sub_areas.{created['id']}",
                    value=created,
                )
            )
            return True

        if kind == "fill_area":
            area_id = self._coerce_non_empty_string(payload.get("area_id"))
            if area_id is None:
                return False
            if not context.state.has_slice("areas"):
                return False
            if area_id not in context.state.areas.areas:
                return False
            sub_area_id = self._coerce_non_empty_string(payload.get("id"))
            if sub_area_id is None:
                sub_area_id = f"fill_{current_tick}"
            spec = {
                "id": sub_area_id,
                "label": self._string_or_empty(payload.get("label")),
                "description": self._string_or_empty(payload.get("description")),
                "type": self._coerce_non_empty_string(payload.get("type")) or "visit",
                "tier": "permanent",
                "source": "narrative_planner",
                "created_at_tick": current_tick,
                "expiry_ticks": payload.get("expiry_ticks", -1),
            }
            manager = self._resolve_sub_area_manager(context)
            created = manager.create(area_id, spec)
            if created is None:
                return False
            context.record_change(
                StateChange(
                    slice="areas",
                    operation="set",
                    path=f"{area_id}.temporary_sub_areas.{created['id']}",
                    value=created,
                )
            )
            return True

        return False

    def _resolve_sub_area_manager(self, context: SettlementContext) -> DynamicSubAreaManager:
        if self._sub_area_manager is not None:
            return self._sub_area_manager
        return DynamicSubAreaManager(context.state.areas)

    @classmethod
    def _resident_npcs_for_board(
        cls,
        *,
        context: SettlementContext,
        area_id: str,
        board_id: str,
        sub_location: str | None,
    ) -> list[str]:
        if not context.world.has_registry("maps"):
            return []
        if not board_id:
            return []
        area_template = context.world.maps.get(area_id)
        if area_template is None:
            return []
        raw_sub_locations = getattr(area_template, "sub_locations", None)
        if raw_sub_locations is None and isinstance(area_template, Mapping):
            raw_sub_locations = area_template.get("sub_locations")
        if not isinstance(raw_sub_locations, Mapping):
            return []
        resolved_sub = cls._coerce_non_empty_string(sub_location) or None
        for raw_sub_id, raw_sub in raw_sub_locations.items():
            sub_id = cls._coerce_non_empty_string(str(raw_sub_id))
            if sub_id is None:
                continue
            if resolved_sub is not None and sub_id != resolved_sub:
                continue
            raw_interactables = getattr(raw_sub, "interactables", None)
            if raw_interactables is None and isinstance(raw_sub, Mapping):
                raw_interactables = raw_sub.get("interactables")
            if not isinstance(raw_interactables, list):
                continue
            has_board = False
            for raw_interactable in raw_interactables:
                if isinstance(raw_interactable, Mapping):
                    interactable_id = cls._coerce_non_empty_string(
                        raw_interactable.get("id")
                    )
                    raw_tags = raw_interactable.get("tags", [])
                else:
                    interactable_id = cls._coerce_non_empty_string(
                        getattr(raw_interactable, "id", None)
                    )
                    raw_tags = getattr(raw_interactable, "tags", [])
                if interactable_id != board_id:
                    continue
                if isinstance(raw_tags, list):
                    board_tags = [str(tag).strip() for tag in raw_tags if str(tag).strip()]
                    if "quest_source" not in board_tags:
                        continue
                has_board = True
                break
            if not has_board:
                continue
            raw_residents = getattr(raw_sub, "resident_npcs", None)
            if raw_residents is None and isinstance(raw_sub, Mapping):
                raw_residents = raw_sub.get("resident_npcs", [])
            if not isinstance(raw_residents, list):
                continue
            return [str(npc_id) for npc_id in raw_residents if cls._coerce_non_empty_string(npc_id)]
        return []

    def _create_milestone_condition_events(
        self,
        milestone_id: str,
        context: SettlementContext,
        *,
        current_tick: int,
    ) -> None:
        """Create dormant EventSlice events from a MilestoneTemplate's conditions.

        Called after a create_quest directive so that EventConditionHook can
        automatically advance the milestone state when game conditions are met.
        No-op if the milestone doesn't exist in QuestRegistry or has no conditions.
        """
        if not context.state.has_slice("events"):
            return
        if not context.world.has_registry("quests"):
            return
        milestone = context.world.quests.get_milestone(milestone_id)
        if milestone is None:
            return

        specs: list[tuple[list[Any], str]] = []
        if milestone.success_conditions:
            specs.append((list(milestone.success_conditions), "COMPLETED"))
        if milestone.failure_conditions:
            specs.append((list(milestone.failure_conditions), "FAILED"))

        for conditions, outcome_state in specs:
            prefix = "sc" if outcome_state == "COMPLETED" else "fc"
            for idx, cond in enumerate(conditions):
                event_id = f"milestone_{milestone_id}_{prefix}_{idx}"
                if context.state.events.get_event(event_id) is not None:
                    continue  # already registered
                context.state.events.activate(event_id, {
                    "id": event_id,
                    "event_id": event_id,
                    "state": "dormant",
                    "status": "dormant",
                    "conditions": [{"type": cond.type, "params": dict(cond.params)}],
                    "on_trigger": [{
                        "type": "advance_quest",
                        "params": {"quest_id": milestone_id, "to_state": outcome_state},
                    }],
                    "source": "narrative_planner",
                    "milestone_id": milestone_id,
                    "created_at_tick": current_tick,
                })
                context.record_change(StateChange(
                    slice="events",
                    operation="set",
                    path=f"active_events.{event_id}",
                    value={"state": "dormant"},
                ))

    def _create_objective_events(
        self,
        quest_id: str,
        quest_payload: Mapping[str, Any],
        context: SettlementContext,
        *,
        current_tick: int,
    ) -> None:
        if not context.state.has_slice("events"):
            return

        raw_objectives = quest_payload.get("objectives")
        if not isinstance(raw_objectives, list):
            return

        for idx, objective in enumerate(raw_objectives):
            if not isinstance(objective, Mapping):
                continue
            obj_type = self._coerce_non_empty_string(objective.get("type"))
            if obj_type is None:
                continue
            obj_type = obj_type.lower()
            if self._coerce_optional_bool(objective.get("optional")):
                continue

            condition_type = self._objective_to_condition_type(obj_type)
            if condition_type is None:
                continue
            raw_target = objective.get("target")
            params = self._objective_target_to_params(condition_type, raw_target)
            if params is None:
                continue

            event_id = f"dq_{quest_id}_obj_{idx}"
            if context.state.events.get_event(event_id) is not None:
                continue

            context.state.events.activate(event_id, {
                "id": event_id,
                "event_id": event_id,
                "state": "dormant",
                "status": "dormant",
                "conditions": [{
                    "type": condition_type,
                    "params": params,
                }],
                "on_trigger": [{
                    "type": "complete_objective",
                    "params": {
                        "quest_id": quest_id,
                        "objective_index": idx,
                    },
                }],
                "source": "narrative_planner",
                "created_at_tick": current_tick,
            })
            context.record_change(StateChange(
                slice="events",
                operation="set",
                path=f"active_events.{event_id}",
                value={"state": "dormant"},
            ))

    @staticmethod
    def _objective_to_condition_type(obj_type: str) -> str | None:
        mapping = {
            "reach_location": "location_visited",
            "location_visited": "location_visited",
            "talk_to": "npc_talked",
            "collect": "item_obtained",
            "kill": "kill_count",
        }
        return mapping.get(obj_type)

    @staticmethod
    def _objective_target_to_params(
        condition_type: str,
        target: Any,
    ) -> dict[str, Any] | None:
        if isinstance(target, Mapping):
            if condition_type in {"item_obtained", "kill_count", "talk_to", "npc_talked"}:
                return {str(key): value for key, value in target.items()}
            if condition_type in {"location_entered", "location_visited"}:
                area_id = NarrativePlannerHook._coerce_non_empty_string(target.get("area_id"))
                location_id = NarrativePlannerHook._coerce_non_empty_string(target.get("location_id"))
                sub_location_id = NarrativePlannerHook._coerce_non_empty_string(target.get("sub_location_id"))
                params = {
                    "area_id": area_id,
                    "location_id": location_id,
                    "sub_location_id": sub_location_id,
                }
                if area_id is None and location_id is None:
                    return None
                return params
            return None

        if condition_type == "item_obtained":
            if isinstance(target, str):
                return {"item_id": target}
            return None
        if condition_type == "kill_count":
            if isinstance(target, str):
                return {"monster_type": target}
            return None
        if condition_type in {"talk_to", "npc_talked"}:
            if isinstance(target, str):
                return {"npc_id": target}
            return None
        if condition_type in {"location_entered", "location_visited"}:
            if isinstance(target, str):
                return {"area_id": target}
            return None
        return None

    @staticmethod
    def _coerce_optional_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            return normalized in {"true", "1", "yes", "y", "on"}
        return False

    def _expire_dynamic_quests(
        self,
        context: SettlementContext,
        *,
        current_tick: int,
    ) -> list[dict[str, Any]]:
        expired_payloads: list[dict[str, Any]] = []
        for quest_id, quest in list(context.state.quests.dynamic_quests.items()):
            if not isinstance(quest, Mapping):
                continue
            status = str(quest.get("status", "")).strip().lower()
            if status in {"completed", "retired", "expired"}:
                continue

            raw_expiry_ticks = quest.get("expiry_ticks")
            if raw_expiry_ticks is None:
                continue
            try:
                expiry_ticks = int(raw_expiry_ticks)
            except (TypeError, ValueError):
                continue

            raw_created_at_tick = quest.get("created_at_tick")
            if raw_created_at_tick is None:
                continue
            try:
                created_at_tick = int(raw_created_at_tick)
            except (TypeError, ValueError):
                continue

            if current_tick - created_at_tick < expiry_ticks:
                continue

            on_expire = self._coerce_non_empty_string(quest.get("on_expire")) or "ignore"
            on_expire = on_expire.strip().lower()
            if on_expire not in {"ignore", "escalate", "retire"}:
                on_expire = "ignore"

            if on_expire in {"retire", "escalate"}:
                context.state.quests.retire_dynamic_quest(quest_id)
                new_status = "retired"
            else:
                stored = context.state.quests.dynamic_quests.get(quest_id)
                if isinstance(stored, Mapping):
                    updated = dict(stored)
                else:
                    updated = {}
                updated["status"] = "expired"
                context.state.quests.dynamic_quests[quest_id] = updated
                new_status = "expired"

            context.record_change(
                StateChange(
                    slice="quests",
                    operation="set",
                    path=f"dynamic.{quest_id}.status",
                    value=new_status,
                )
            )

            if on_expire == "escalate":
                context.state.narrative_plan.adjust_escalation(1)

            context.state.narrative_plan.add_history(
                {
                    "kind": "dynamic_quest_expired",
                    "quest_id": quest_id,
                    "tick": current_tick,
                    "on_expire": on_expire,
                    "status_before": status,
                    "status_after": new_status,
                }
            )
            expired_payloads.append({
                "quest_id": quest_id,
                "on_expire": on_expire,
                "tick": current_tick,
                "status": new_status,
            })

        return expired_payloads

    def _find_available_npc_id(
        self,
        context: SettlementContext,
        base_npc_id: str,
        *,
        max_attempts: int = 20,
    ) -> str:
        npc_id = base_npc_id
        for suffix in range(max_attempts):
            if suffix > 0:
                npc_id = f"{base_npc_id}_{suffix}"
            if context.state.areas.find_npc_area(npc_id) is None:
                return npc_id
        return f"{base_npc_id}_{max_attempts}"

    def _despawn_expired_quest_npcs(
        self,
        context: SettlementContext,
        *,
        current_tick: int,
    ) -> None:
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

            npc_id = self._coerce_non_empty_string(entry.get("npc_id"))
            if npc_id is None:
                continue
            if not context.state.has_slice("areas"):
                if context.state.has_slice("narrative_plan"):
                    context.state.narrative_plan.remove_temporary_npc(npc_id)
                continue

            removed = False
            for area in context.state.areas.areas.values():
                if npc_id in area.npc_locations:
                    area.npc_locations.pop(npc_id, None)
                    context.state.areas._dirty = True
                    removed = True
            if removed:
                context.record_change(
                    StateChange(
                        slice="areas",
                        operation="set",
                        path=f"npc_location.{npc_id}",
                        value=None,
                    )
                )
            if context.state.has_slice("narrative_plan"):
                context.state.narrative_plan.remove_temporary_npc(npc_id)

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
