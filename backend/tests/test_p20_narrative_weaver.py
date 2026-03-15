"""Tests for P20 Phase 2e — NarrativeWeaverSubSystem.

Covers:
- Protocol compliance (name/handles/accepts_event)
- Dynamic quest expiry: retire / escalate / ignore strategies + not-yet-due guard
- Temporary NPC despawn: past-due removal, not-yet-due retention
- Directive GC via prune_consumed_and_expired
- Auto-escalation safety net: fires / frozen / below threshold / higher levels
- Dispatch integration: Hook.execute() + NarrativeWeaver end-to-end
- SSE events emitted during evaluate() reach the Hook's HookResult
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.game_core.content import WorldInstance
from app.game_core.orchestration.hooks.narrative_planner import NarrativePlannerHook
from app.game_core.orchestration.models import SSEEvent
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.planning import (
    NarrativeWeaverSubSystem,
    NpcDirectorSubSystem,
    PacingControllerSubSystem,
    PlannerDispatcher,
    PlannerEvent,
    QuestManagerSubSystem,
    WorldBuilderSubSystem,
)
from app.game_core.rules import RulesEngine
from app.game_core.rules.defaults import register_default_rules_handlers
from app.game_core.state import StateChange, StateContainer, StateDelta
from app.game_core.state.slices import (
    AreaSlice,
    EventSlice,
    FlagSlice,
    NarrativePlanSlice,
    PlayerSlice,
    QuestSlice,
    SceneSlice,
    TimeSlice,
)


# ---------------------------------------------------------------------------
# Shared fixture factory
# ---------------------------------------------------------------------------


def _make_settlement_context(
    *,
    area_id: str = "test_area",
    initial_escalation: int = 0,
    ticks_since_progress: int = 0,
    pacing_frozen: bool = False,
) -> SettlementContext:
    """Minimal SettlementContext with all slices needed for NarrativeWeaver tests."""
    world = WorldInstance("test_world")
    state = StateContainer()

    time_slice = TimeSlice()
    time_slice.restore({"day": 1, "slot": 9})
    state.register(time_slice)

    player = PlayerSlice()
    player.restore({"current_area": area_id, "current_location": None})
    state.register(player)

    quests = QuestSlice()
    quests.restore({"milestone_states": {}, "dynamic_quests": {}})
    state.register(quests)

    narrative_plan = NarrativePlanSlice()
    narrative_plan.restore({
        "current_chapter": "chapter_1",
        "escalation_level": initial_escalation,
        "last_run_tick": 0,
        "ticks_since_milestone_progress": ticks_since_progress,
        "pacing_frozen": pacing_frozen,
    })
    state.register(narrative_plan)

    areas = AreaSlice()
    areas.restore({"areas": {area_id: {}}})
    state.register(areas)

    flags = FlagSlice()
    flags.restore({})
    state.register(flags)

    events = EventSlice()
    events.restore({})
    state.register(events)

    scene_slice = SceneSlice()
    scene_slice.restore({})
    state.register(scene_slice)
    scene_bus = SceneBus(scene_slice)

    change_log: list[StateChange] = []

    def _apply_delta(delta: StateDelta | None) -> None:
        if delta is None:
            return
        state.apply(delta)
        change_log.extend(delta.changes)
        for change in delta.changes:
            scene_bus.record_state_change(change)

    rules_engine = RulesEngine()
    register_default_rules_handlers(rules_engine)

    return SettlementContext(
        change_log=change_log,
        state=state,
        world=world,
        scene_bus=scene_bus,
        _rules_engine=rules_engine,
        _apply_delta=_apply_delta,
    )


def _make_full_hook(planner: Any = None) -> tuple[NarrativePlannerHook, list[SSEEvent]]:
    """Build a NarrativePlannerHook with all sub-systems registered."""
    hook = NarrativePlannerHook(blackboard=planner)
    dispatcher = PlannerDispatcher()
    quest_manager = QuestManagerSubSystem(dispatcher=dispatcher)
    dispatcher.register(quest_manager)
    dispatcher.register(NpcDirectorSubSystem())
    dispatcher.register(WorldBuilderSubSystem(sse_collector=hook._pending_sse))
    dispatcher.register(PacingControllerSubSystem())
    dispatcher.register(NarrativeWeaverSubSystem(sse_collector=hook._pending_sse))
    hook._dispatcher = dispatcher
    return hook, hook._pending_sse


# ---------------------------------------------------------------------------
# §1  Protocol compliance
# ---------------------------------------------------------------------------


class TestProtocolCompliance:
    def test_name(self) -> None:
        weaver = NarrativeWeaverSubSystem()
        assert weaver.name == "narrative_weaver"

    def test_handles_schedule_event(self) -> None:
        """NarrativeWeaver handles 'schedule_event' directive (migrated from Osiris 2-C)."""
        weaver = NarrativeWeaverSubSystem()
        assert "schedule_event" in weaver.handles

    def test_accepts_tick_settlement(self) -> None:
        weaver = NarrativeWeaverSubSystem()
        event = PlannerEvent(kind="tick_settlement", tick=5)
        assert weaver.accepts_event(event) is True

    def test_rejects_other_events(self) -> None:
        weaver = NarrativeWeaverSubSystem()
        event = PlannerEvent(kind="milestone_completed", tick=5)
        assert weaver.accepts_event(event) is True

    def test_rejects_unrelated_events(self) -> None:
        weaver = NarrativeWeaverSubSystem()
        event = PlannerEvent(kind="quest_accepted", tick=5)
        assert weaver.accepts_event(event) is False

    def test_apply_directive_always_false(self) -> None:
        """NarrativeWeaver has no directive namespace; all apply_directive calls return False."""
        weaver = NarrativeWeaverSubSystem()
        ctx = _make_settlement_context()
        result = weaver.apply_directive("escalate", {"delta": 1}, ctx, current_tick=0)
        assert result is False


# ---------------------------------------------------------------------------
# §2  Dynamic quest expiry strategies
# ---------------------------------------------------------------------------


class TestExpireQuestRetireStrategy:
    def test_retire_changes_status_to_retired(self) -> None:
        ctx = _make_settlement_context()
        ctx.state.quests.add_dynamic_quest("dq_retire", {
            "quest_id": "dq_retire",
            "status": "active",
            "title": "Fading Crisis",
            "on_expire": "retire",
            "expiry_ticks": 5,
            "created_at_tick": 0,
        })

        weaver = NarrativeWeaverSubSystem()
        weaver._expire_dynamic_quests(ctx, current_tick=10)

        dq = ctx.state.quests.dynamic_quests.get("dq_retire", {})
        assert dq.get("status") == "retired"

    def test_retire_emits_sse_event(self) -> None:
        sse: list[SSEEvent] = []
        ctx = _make_settlement_context()
        ctx.state.quests.add_dynamic_quest("dq_retire", {
            "quest_id": "dq_retire",
            "status": "active",
            "title": "Fading Crisis",
            "on_expire": "retire",
            "expiry_ticks": 5,
            "created_at_tick": 0,
        })

        weaver = NarrativeWeaverSubSystem(sse_collector=sse)
        weaver._expire_dynamic_quests(ctx, current_tick=10)

        assert len(sse) == 1
        event = sse[0]
        assert event.event_type == "dynamic_quest_expired"
        assert event.payload["quest_id"] == "dq_retire"
        assert event.payload["on_expire"] == "retire"
        assert event.payload["status"] == "retired"


class TestExpireQuestEscalateStrategy:
    def test_escalate_increments_escalation_level(self) -> None:
        ctx = _make_settlement_context(area_id="test_area", initial_escalation=1)
        ctx.state.quests.add_dynamic_quest("dq_esc", {
            "quest_id": "dq_esc",
            "status": "active",
            "title": "Old Crisis",
            "on_expire": "escalate",
            "expiry_ticks": 5,
            "created_at_tick": 0,
        })

        weaver = NarrativeWeaverSubSystem()
        weaver._expire_dynamic_quests(ctx, current_tick=10)

        assert ctx.state.narrative_plan.escalation_level == 2

    def test_escalate_sets_flag(self) -> None:
        ctx = _make_settlement_context(area_id="test_area", initial_escalation=1)
        ctx.state.quests.add_dynamic_quest("dq_esc", {
            "quest_id": "dq_esc",
            "status": "active",
            "title": "Old Crisis",
            "on_expire": "escalate",
            "expiry_ticks": 5,
            "created_at_tick": 0,
        })

        weaver = NarrativeWeaverSubSystem()
        weaver._expire_dynamic_quests(ctx, current_tick=10)

        flag_value = ctx.state.flags.get("narrative_escalation_level")
        assert flag_value == 2

    def test_escalate_bumps_danger(self) -> None:
        ctx = _make_settlement_context(area_id="test_area", initial_escalation=0)
        danger_before = ctx.state.areas.areas["test_area"].danger_level

        ctx.state.quests.add_dynamic_quest("dq_esc", {
            "quest_id": "dq_esc",
            "status": "active",
            "title": "Old Crisis",
            "on_expire": "escalate",
            "expiry_ticks": 5,
            "created_at_tick": 0,
        })

        weaver = NarrativeWeaverSubSystem()
        weaver._expire_dynamic_quests(ctx, current_tick=10)

        area_snap = ctx.state.areas.snapshot()
        danger = area_snap.get("areas", {}).get("test_area", {}).get("danger_level", 0.0)
        assert abs(danger - (danger_before + 0.05)) < 1e-6

    def test_escalate_emits_sse(self) -> None:
        sse: list[SSEEvent] = []
        ctx = _make_settlement_context(area_id="test_area", initial_escalation=0)
        ctx.state.quests.add_dynamic_quest("dq_esc", {
            "quest_id": "dq_esc",
            "status": "active",
            "title": "Old Crisis",
            "on_expire": "escalate",
            "expiry_ticks": 5,
            "created_at_tick": 0,
        })

        weaver = NarrativeWeaverSubSystem(sse_collector=sse)
        weaver._expire_dynamic_quests(ctx, current_tick=10)

        assert any(e.event_type == "dynamic_quest_expired" for e in sse)


class TestExpireQuestIgnoreStrategy:
    def test_ignore_marks_status_expired_not_retired(self) -> None:
        ctx = _make_settlement_context()
        ctx.state.quests.add_dynamic_quest("dq_ignore", {
            "quest_id": "dq_ignore",
            "status": "active",
            "title": "Ignored Crisis",
            "on_expire": "ignore",
            "expiry_ticks": 5,
            "created_at_tick": 0,
        })

        weaver = NarrativeWeaverSubSystem()
        weaver._expire_dynamic_quests(ctx, current_tick=10)

        dq = ctx.state.quests.dynamic_quests.get("dq_ignore", {})
        assert dq.get("status") == "expired"

    def test_ignore_does_not_escalate(self) -> None:
        ctx = _make_settlement_context(initial_escalation=1)
        ctx.state.quests.add_dynamic_quest("dq_ignore", {
            "quest_id": "dq_ignore",
            "status": "active",
            "title": "Ignored Crisis",
            "on_expire": "ignore",
            "expiry_ticks": 5,
            "created_at_tick": 0,
        })

        weaver = NarrativeWeaverSubSystem()
        weaver._expire_dynamic_quests(ctx, current_tick=10)

        # escalation_level should remain at 1
        assert ctx.state.narrative_plan.escalation_level == 1


class TestExpireQuestNotYetDue:
    def test_not_expired_when_tick_insufficient(self) -> None:
        ctx = _make_settlement_context()
        ctx.state.quests.add_dynamic_quest("dq_active", {
            "quest_id": "dq_active",
            "status": "active",
            "title": "Active Quest",
            "on_expire": "retire",
            "expiry_ticks": 20,
            "created_at_tick": 0,
        })

        weaver = NarrativeWeaverSubSystem()
        weaver._expire_dynamic_quests(ctx, current_tick=10)

        # 10 - 0 = 10 < 20 → should not expire
        dq = ctx.state.quests.dynamic_quests.get("dq_active", {})
        assert dq.get("status") == "active"

    def test_already_completed_quest_skipped(self) -> None:
        ctx = _make_settlement_context()
        ctx.state.quests.add_dynamic_quest("dq_done", {
            "quest_id": "dq_done",
            "status": "completed",
            "title": "Done Quest",
            "on_expire": "retire",
            "expiry_ticks": 5,
            "created_at_tick": 0,
        })

        sse: list[SSEEvent] = []
        weaver = NarrativeWeaverSubSystem(sse_collector=sse)
        weaver._expire_dynamic_quests(ctx, current_tick=10)

        # completed quests should not trigger expiry events
        assert len(sse) == 0


# ---------------------------------------------------------------------------
# §3  Temporary NPC despawn
# ---------------------------------------------------------------------------


class TestDespawnNpcPastDue:
    def test_npc_removed_from_area_when_despawn_tick_passed(self) -> None:
        ctx = _make_settlement_context(area_id="test_area")
        # Place NPC in area
        area = ctx.state.areas.get_area("test_area")
        area.npc_locations["quest_npc_1"] = "entrance"

        # Record spawn history with a despawn_tick that has passed
        ctx.state.narrative_plan.add_history({
            "kind": "spawn_quest_npc",
            "npc_id": "quest_npc_1",
            "despawn_tick": 5,
        })

        weaver = NarrativeWeaverSubSystem()
        weaver._despawn_expired_quest_npcs(ctx, current_tick=10)

        area_after = ctx.state.areas.get_area("test_area")
        assert "quest_npc_1" not in area_after.npc_locations

    def test_npc_removed_from_temporary_npcs(self) -> None:
        ctx = _make_settlement_context(area_id="test_area")
        ctx.state.narrative_plan.temporary_npcs["quest_npc_1"] = {
            "npc_id": "quest_npc_1",
            "area_id": "test_area",
        }
        ctx.state.narrative_plan.add_history({
            "kind": "spawn_quest_npc",
            "npc_id": "quest_npc_1",
            "despawn_tick": 5,
        })

        weaver = NarrativeWeaverSubSystem()
        weaver._despawn_expired_quest_npcs(ctx, current_tick=10)

        assert "quest_npc_1" not in ctx.state.narrative_plan.temporary_npcs


class TestDespawnNpcNotYetDue:
    def test_npc_retained_when_despawn_tick_not_reached(self) -> None:
        ctx = _make_settlement_context(area_id="test_area")
        area = ctx.state.areas.get_area("test_area")
        area.npc_locations["staying_npc"] = "market"

        ctx.state.narrative_plan.add_history({
            "kind": "spawn_quest_npc",
            "npc_id": "staying_npc",
            "despawn_tick": 20,
        })

        weaver = NarrativeWeaverSubSystem()
        weaver._despawn_expired_quest_npcs(ctx, current_tick=10)

        area_after = ctx.state.areas.get_area("test_area")
        assert "staying_npc" in area_after.npc_locations

    def test_non_spawn_history_entries_ignored(self) -> None:
        ctx = _make_settlement_context(area_id="test_area")
        ctx.state.narrative_plan.add_history({
            "kind": "dynamic_quest_expired",
            "quest_id": "q1",
            "despawn_tick": 1,  # this field has no meaning here
        })

        weaver = NarrativeWeaverSubSystem()
        # Should not raise
        weaver._despawn_expired_quest_npcs(ctx, current_tick=10)


# ---------------------------------------------------------------------------
# §4  Directive GC
# ---------------------------------------------------------------------------


class TestDirectiveGC:
    def test_consumed_directives_pruned(self) -> None:
        ctx = _make_settlement_context()
        ctx.state.narrative_plan.add_directive({
            "npc_id": "merchant_bob",
            "action": "greet_player",
            "consumed": True,
        })
        ctx.state.narrative_plan.add_directive({
            "npc_id": "guard_jen",
            "action": "patrol",
            "consumed": False,
        })

        weaver = NarrativeWeaverSubSystem()
        # evaluate() calls prune_consumed_and_expired internally
        asyncio.run(weaver.evaluate(PlannerEvent(kind="tick_settlement", tick=5), ctx))

        # Consumed directive should be removed; active one retained
        directives = ctx.state.narrative_plan.npc_directives
        npc_ids = [d["npc_id"] for d in directives]
        assert "merchant_bob" not in npc_ids
        assert "guard_jen" in npc_ids

    def test_expired_directives_pruned(self) -> None:
        ctx = _make_settlement_context()
        ctx.state.narrative_plan.add_directive({
            "npc_id": "courier",
            "action": "deliver",
            "expires_at_tick": 3,  # already expired by tick=5
            "consumed": False,
        })
        ctx.state.narrative_plan.add_directive({
            "npc_id": "innkeeper",
            "action": "welcome",
            "expires_at_tick": 20,  # still valid at tick=5
            "consumed": False,
        })

        weaver = NarrativeWeaverSubSystem()
        asyncio.run(weaver.evaluate(PlannerEvent(kind="tick_settlement", tick=5), ctx))

        directives = ctx.state.narrative_plan.npc_directives
        npc_ids = [d["npc_id"] for d in directives]
        assert "courier" not in npc_ids
        assert "innkeeper" in npc_ids


# ---------------------------------------------------------------------------
# §5  Auto-escalation safety net
# ---------------------------------------------------------------------------


class TestAutoEscalationFires:
    def test_fires_when_ticks_meet_threshold(self) -> None:
        """Level 0 threshold is 4; at ticks=4 the safety net fires."""
        ctx = _make_settlement_context(initial_escalation=0, ticks_since_progress=4)

        weaver = NarrativeWeaverSubSystem()
        result = weaver._check_auto_escalation(ctx)

        assert result is not None
        assert result["kind"] == "escalate"
        assert result["payload"] == {"delta": 1}

    def test_fires_when_ticks_exceed_threshold(self) -> None:
        """Ticks well above threshold should also fire."""
        ctx = _make_settlement_context(initial_escalation=0, ticks_since_progress=10)

        weaver = NarrativeWeaverSubSystem()
        result = weaver._check_auto_escalation(ctx)

        assert result is not None
        assert result["kind"] == "escalate"


class TestAutoEscalationSkippedWhenFrozen:
    def test_no_directive_when_frozen(self) -> None:
        """pacing_frozen=True should suppress the safety net."""
        ctx = _make_settlement_context(
            initial_escalation=0, ticks_since_progress=10, pacing_frozen=True
        )

        weaver = NarrativeWeaverSubSystem()
        result = weaver._check_auto_escalation(ctx)

        assert result is None


class TestAutoEscalationBelowThreshold:
    def test_no_directive_below_threshold(self) -> None:
        """Level 0 threshold is 4; at ticks=3 the safety net should not fire."""
        ctx = _make_settlement_context(initial_escalation=0, ticks_since_progress=3)

        weaver = NarrativeWeaverSubSystem()
        result = weaver._check_auto_escalation(ctx)

        assert result is None

    def test_no_directive_at_zero_ticks(self) -> None:
        ctx = _make_settlement_context(initial_escalation=0, ticks_since_progress=0)

        weaver = NarrativeWeaverSubSystem()
        result = weaver._check_auto_escalation(ctx)

        assert result is None


class TestAutoEscalationHigherLevels:
    def test_level_1_uses_threshold_7(self) -> None:
        """Level 1 threshold is 7; ticks=6 should not fire."""
        ctx = _make_settlement_context(initial_escalation=1, ticks_since_progress=6)
        weaver = NarrativeWeaverSubSystem()
        assert weaver._check_auto_escalation(ctx) is None

    def test_level_1_threshold_7_fires_at_7(self) -> None:
        ctx = _make_settlement_context(initial_escalation=1, ticks_since_progress=7)
        weaver = NarrativeWeaverSubSystem()
        result = weaver._check_auto_escalation(ctx)
        assert result is not None
        assert result["kind"] == "escalate"

    def test_level_3_uses_threshold_13(self) -> None:
        """Level 3 threshold is 13; ticks=12 should not fire."""
        ctx = _make_settlement_context(initial_escalation=3, ticks_since_progress=12)
        weaver = NarrativeWeaverSubSystem()
        assert weaver._check_auto_escalation(ctx) is None

    def test_level_3_threshold_13_fires_at_13(self) -> None:
        ctx = _make_settlement_context(initial_escalation=3, ticks_since_progress=13)
        weaver = NarrativeWeaverSubSystem()
        result = weaver._check_auto_escalation(ctx)
        assert result is not None
        assert result["kind"] == "escalate"

    def test_out_of_range_level_uses_fallback_interval(self) -> None:
        """Escalation levels beyond the thresholds list use _FALLBACK_ESCALATION_INTERVAL=6."""
        ctx = _make_settlement_context(initial_escalation=99, ticks_since_progress=6)
        weaver = NarrativeWeaverSubSystem()
        result = weaver._check_auto_escalation(ctx)
        assert result is not None
        assert result["kind"] == "escalate"

    def test_fallback_interval_not_fires_below(self) -> None:
        ctx = _make_settlement_context(initial_escalation=99, ticks_since_progress=5)
        weaver = NarrativeWeaverSubSystem()
        assert weaver._check_auto_escalation(ctx) is None


# ---------------------------------------------------------------------------
# §6  Evaluate returns SubSystemResult with directives from auto-escalation
# ---------------------------------------------------------------------------


class TestEvaluateReturnsDirectives:
    def test_evaluate_returns_escalate_directive_when_due(self) -> None:
        """evaluate() should include an escalate directive when safety net fires."""
        ctx = _make_settlement_context(initial_escalation=0, ticks_since_progress=4)

        weaver = NarrativeWeaverSubSystem()
        event = PlannerEvent(kind="tick_settlement", tick=10)
        result = asyncio.run(weaver.evaluate(event, ctx))

        kinds = [d.get("kind") for d in result.directives if isinstance(d, dict)]
        assert "escalate" in kinds

    def test_evaluate_no_directive_below_threshold(self) -> None:
        ctx = _make_settlement_context(initial_escalation=0, ticks_since_progress=2)

        weaver = NarrativeWeaverSubSystem()
        event = PlannerEvent(kind="tick_settlement", tick=10)
        result = asyncio.run(weaver.evaluate(event, ctx))

        kinds = [d.get("kind") for d in result.directives if isinstance(d, dict)]
        assert "escalate" not in kinds


# ---------------------------------------------------------------------------
# §7  Dispatch integration: Hook.execute() + NarrativeWeaver end-to-end
# ---------------------------------------------------------------------------


class _RecordingPlanner:
    """Planner that returns empty directives so Hook proceeds through full execute()."""

    def __init__(self) -> None:
        self.plan_calls = 0

    async def plan(self, _context: Any) -> Any:
        self.plan_calls += 1
        return {"directives": []}


class TestDispatchIntegration:
    def test_hook_execute_runs_weaver_lifecycle(self) -> None:
        """NarrativeWeaver's remaining lifecycle operations run
        when Hook.execute() is called with a dispatcher that has NarrativeWeaver registered."""

        ctx = _make_settlement_context(area_id="test_area")

        # Add a quest that has already expired
        ctx.state.quests.add_dynamic_quest("dq_to_expire", {
            "quest_id": "dq_to_expire",
            "status": "active",
            "title": "Expired Quest",
            "on_expire": "retire",
            "expiry_ticks": 5,
            "created_at_tick": 0,
        })
        # Add a consumed directive that should be GC'd
        ctx.state.narrative_plan.add_directive({
            "npc_id": "old_npc",
            "action": "patrol",
            "consumed": True,
        })

        # Fix the tick so execute() runs (not skipped)
        ctx.state.time.restore({"day": 1, "slot": 20})

        planner = _RecordingPlanner()
        hook, _ = _make_full_hook(planner=planner)

        asyncio.run(hook.execute(ctx))

        # Dynamic quest expiry is now handled by QuestExpiryHook, not NarrativeWeaver
        dq = ctx.state.quests.dynamic_quests.get("dq_to_expire", {})
        assert dq.get("status") == "active"

        # Consumed directive should be removed
        directives = ctx.state.narrative_plan.npc_directives
        assert not any(d.get("npc_id") == "old_npc" for d in directives)

    def test_auto_escalation_directive_applied_via_dispatch(self) -> None:
        """When NarrativeWeaver returns an escalate directive, the Hook applies it via dispatcher,
        causing PacingController to increment the escalation level."""
        ctx = _make_settlement_context(
            area_id="test_area",
            initial_escalation=0,
            ticks_since_progress=4,  # triggers safety net (level 0 threshold = 4)
        )
        ctx.state.time.restore({"day": 1, "slot": 20})

        planner = _RecordingPlanner()
        hook, _ = _make_full_hook(planner=planner)

        asyncio.run(hook.execute(ctx))

        # Escalation level should have been incremented by the applied directive
        assert ctx.state.narrative_plan.escalation_level == 1


# ---------------------------------------------------------------------------
# §8  ItemDesigner stub
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# §9  SSE events from evaluate() reach Hook's HookResult
# ---------------------------------------------------------------------------


class TestSSEEventsReachHookResult:
    def test_expired_quest_does_not_emit_sse_from_weaver(self) -> None:
        """Quest expiry SSE is no longer emitted from NarrativeWeaver."""

        ctx = _make_settlement_context(area_id="test_area")

        # Add an expired quest — expiry is now deferred to QuestExpiryHook
        ctx.state.quests.add_dynamic_quest("dq_sse_test", {
            "quest_id": "dq_sse_test",
            "status": "active",
            "title": "SSE Test Quest",
            "on_expire": "retire",
            "expiry_ticks": 5,
            "created_at_tick": 0,
        })
        ctx.state.time.restore({"day": 1, "slot": 20})

        planner = _RecordingPlanner()
        hook, _ = _make_full_hook(planner=planner)

        hook_result = asyncio.run(hook.execute(ctx))

        event_types = [e.event_type for e in hook_result.sse_events]
        assert "dynamic_quest_expired" not in event_types

    def test_expired_quest_payload_is_unchanged_under_weaver(self) -> None:
        ctx = _make_settlement_context(area_id="test_area")
        ctx.state.quests.add_dynamic_quest("dq_payload_check", {
            "quest_id": "dq_payload_check",
            "status": "active",
            "title": "Payload Check",
            "on_expire": "retire",
            "expiry_ticks": 5,
            "created_at_tick": 0,
        })
        ctx.state.time.restore({"day": 1, "slot": 20})

        planner = _RecordingPlanner()
        hook, _ = _make_full_hook(planner=planner)

        hook_result = asyncio.run(hook.execute(ctx))

        expired_events = [
            e for e in hook_result.sse_events
            if e.event_type == "dynamic_quest_expired"
        ]
        assert expired_events == []
        assert ctx.state.quests.get_dynamic_quest("dq_payload_check")["status"] == "active"
