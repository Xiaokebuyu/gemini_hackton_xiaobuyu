"""Tests for P19 Phase A — Planner ↔ NPC interaction repair.

Covers:
- A4: add_directive() dedup — same NPC pending directive replaced, consumed retained
- A1: DirectiveTriggerHook — guards, core firing, cooldown, filters, max-one-per-tick
- A2: planner context includes pending npc_directives; _format_planner_context shows them
- A3: prompt constraints (tested indirectly via _SYSTEM_PROMPT text assertions)
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.game_core.content import WorldInstance
from app.game_core.orchestration.hooks.directive_trigger import DirectiveTriggerHook
from app.game_core.orchestration.hooks.narrative_planner import NarrativePlannerHook
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules import RulesEngine
from app.game_core.rules.defaults import register_default_rules_handlers
from app.game_core.state import StateChange, StateContainer, StateDelta
from app.game_core.state.slices import (
    AreaSlice,
    NarrativePlanSlice,
    PlayerSlice,
    QuestSlice,
    SceneSlice,
    TimeSlice,
)
from app.game_core.state.slices.flags import FlagSlice


# ------------------------------------------------------------------
# Shared helpers
# ------------------------------------------------------------------


def _make_settlement_context(
    *,
    area_id: str = "test_area",
    npc_in_area: str | None = None,
    player_location: str | None = None,
    player_room: str | None = None,
    npc_location: str | None = "market",
    npc_room: str | None = None,
    with_flags: bool = True,
    absolute_tick: int = 10,
) -> SettlementContext:
    """Minimal SettlementContext with common slices."""
    world = WorldInstance("test_world")
    state = StateContainer()

    time_slice = TimeSlice()
    time_slice.restore({"day": 1, "slot": absolute_tick})
    state.register(time_slice)

    player = PlayerSlice()
    player.restore({
        "current_area": area_id,
        "current_location": player_location,
        "current_room": player_room,
    })
    state.register(player)

    quests = QuestSlice()
    quests.restore({"milestone_states": {}, "dynamic_quests": {}})
    state.register(quests)

    narrative_plan = NarrativePlanSlice()
    narrative_plan.restore({
        "current_chapter": "chapter_1",
        "escalation_level": 0,
        "last_run_tick": 0,
        "ticks_since_milestone_progress": 0,
    })
    state.register(narrative_plan)

    areas = AreaSlice()
    if npc_in_area:
        areas.restore({
            "areas": {
                area_id: {
                    "npc_locations": {npc_in_area: npc_location},
                    "npc_rooms": {npc_in_area: npc_room},
                }
            }
        })
    else:
        areas.restore({"areas": {area_id: {}}})
    state.register(areas)

    if with_flags:
        flags = FlagSlice()
        flags.restore({})
        state.register(flags)

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


def _add_directive(
    ctx: SettlementContext,
    npc_id: str,
    *,
    priority: str = "high",
    consumed: bool = False,
    expires_at_tick: int = 100,
    issued_at_tick: int = 5,
    linked_quest_id: str | None = None,
) -> dict[str, Any]:
    directive: dict[str, Any] = {
        "npc_id": npc_id,
        "directive": {"kind": "talk", "topic": "test topic"},
        "priority": priority,
        "consumed": consumed,
        "expires_at_tick": expires_at_tick,
        "issued_at_tick": issued_at_tick,
    }
    if linked_quest_id is not None:
        directive["linked_quest_id"] = linked_quest_id
    return ctx.state.narrative_plan.add_directive(directive)


# ------------------------------------------------------------------
# A4: add_directive() dedup
# ------------------------------------------------------------------


class TestAddDirectiveDedup:
    def test_add_directive_dedup_same_npc(self) -> None:
        """Adding a second directive for the same NPC replaces the first pending one."""
        ctx = _make_settlement_context()
        np_slice = ctx.state.narrative_plan

        _add_directive(ctx, "npc_alice", priority="medium", issued_at_tick=1)
        assert len(np_slice.npc_directives) == 1

        # Second directive for same NPC — should replace the first
        _add_directive(ctx, "npc_alice", priority="high", issued_at_tick=5)
        assert len(np_slice.npc_directives) == 1
        assert np_slice.npc_directives[0]["priority"] == "high"
        assert np_slice.npc_directives[0]["issued_at_tick"] == 5

    def test_add_directive_preserves_consumed(self) -> None:
        """Consumed directives for the same NPC are kept; only pending is replaced."""
        ctx = _make_settlement_context()
        np_slice = ctx.state.narrative_plan

        # Add a consumed directive for npc_alice
        d = _add_directive(ctx, "npc_alice", priority="low", issued_at_tick=1)
        d["consumed"] = True  # mark consumed in-place (simulating consumption)
        np_slice.npc_directives[0]["consumed"] = True

        # Now add another directive for the same NPC
        _add_directive(ctx, "npc_alice", priority="high", issued_at_tick=9)

        # Both should exist: consumed old one + new pending one
        assert len(np_slice.npc_directives) == 2
        consumed_ones = [d for d in np_slice.npc_directives if d.get("consumed")]
        pending_ones = [d for d in np_slice.npc_directives if not d.get("consumed")]
        assert len(consumed_ones) == 1
        assert len(pending_ones) == 1
        assert pending_ones[0]["priority"] == "high"

    def test_add_directive_different_npcs_coexist(self) -> None:
        """Directives for different NPCs coexist independently."""
        ctx = _make_settlement_context()
        np_slice = ctx.state.narrative_plan

        _add_directive(ctx, "npc_alice")
        _add_directive(ctx, "npc_bob")
        _add_directive(ctx, "npc_charlie")

        assert len(np_slice.npc_directives) == 3
        npc_ids = {d["npc_id"] for d in np_slice.npc_directives}
        assert npc_ids == {"npc_alice", "npc_bob", "npc_charlie"}

    def test_add_directive_marks_dirty(self) -> None:
        """add_directive() marks the slice dirty."""
        ctx = _make_settlement_context()
        np_slice = ctx.state.narrative_plan
        np_slice.clear_dirty()  # reset

        _add_directive(ctx, "npc_test")
        assert np_slice.dirty


# ------------------------------------------------------------------
# A1: DirectiveTriggerHook
# ------------------------------------------------------------------


class TestDirectiveTriggerHookGuards:
    def test_skips_no_narrative_plan(self) -> None:
        """Hook skips when narrative_plan slice is absent."""
        world = WorldInstance("w")
        state = StateContainer()
        player = PlayerSlice()
        player.restore({"current_area": "a", "current_location": None})
        state.register(player)
        scene_slice = SceneSlice()
        scene_slice.restore({})
        state.register(scene_slice)
        scene_bus = SceneBus(scene_slice)

        rules_engine = RulesEngine()
        ctx = SettlementContext(
            change_log=[],
            state=state,
            world=world,
            scene_bus=scene_bus,
            _rules_engine=rules_engine,
            _apply_delta=lambda _d: None,
        )

        def _run() -> Any:
            async def _body() -> Any:
                hook = DirectiveTriggerHook()
                return await hook.execute(ctx)
            return asyncio.run(_body())

        result = _run()
        assert result.metadata.get("skipped") == "no_narrative_plan"

    def test_skips_no_player_slice(self) -> None:
        """Hook skips when player slice is absent."""
        world = WorldInstance("w")
        state = StateContainer()
        np_slice = NarrativePlanSlice()
        np_slice.restore({})
        state.register(np_slice)
        scene_slice = SceneSlice()
        scene_slice.restore({})
        state.register(scene_slice)
        scene_bus = SceneBus(scene_slice)

        rules_engine = RulesEngine()
        ctx = SettlementContext(
            change_log=[],
            state=state,
            world=world,
            scene_bus=scene_bus,
            _rules_engine=rules_engine,
            _apply_delta=lambda _d: None,
        )

        def _run() -> Any:
            async def _body() -> Any:
                hook = DirectiveTriggerHook()
                return await hook.execute(ctx)
            return asyncio.run(_body())

        result = _run()
        assert result.metadata.get("skipped") == "no_player_slice"

    def test_skips_private_chat_location(self) -> None:
        """Hook skips when player is already in a private chat location."""
        ctx = _make_settlement_context(player_location="_private_npc_alice")
        _add_directive(ctx, "npc_alice")

        def _run() -> Any:
            async def _body() -> Any:
                hook = DirectiveTriggerHook()
                return await hook.execute(ctx)
            return asyncio.run(_body())

        result = _run()
        assert result.metadata.get("skipped") == "already_in_private_chat"

    def test_skips_no_pending_directives(self) -> None:
        """Hook skips when there are no pending directives."""
        ctx = _make_settlement_context(npc_in_area="npc_alice")
        # No directives added

        def _run() -> Any:
            async def _body() -> Any:
                hook = DirectiveTriggerHook()
                return await hook.execute(ctx)
            return asyncio.run(_body())

        result = _run()
        assert result.metadata.get("skipped") == "no_pending_directives"

    def test_skips_no_reachable_npcs(self) -> None:
        """Hook skips when no NPCs are reachable (empty area)."""
        ctx = _make_settlement_context()  # no npc_in_area
        _add_directive(ctx, "npc_alice")

        def _run() -> Any:
            async def _body() -> Any:
                hook = DirectiveTriggerHook()
                return await hook.execute(ctx)
            return asyncio.run(_body())

        result = _run()
        assert result.metadata.get("skipped") == "no_reachable_npcs"


class TestDirectiveTriggerHookCore:
    def test_fires_for_high_priority_deterministic(self) -> None:
        """High priority (0.90) directive fires reliably over 20 runs."""
        ctx = _make_settlement_context(npc_in_area="npc_alice")
        _add_directive(ctx, "npc_alice", priority="high")

        fired_count = 0
        for _ in range(20):
            # Reset cooldown flag each iteration
            if ctx.state.has_slice("flags"):
                ctx.state.flags.remove("directive_trigger_cooldown_npc_alice")

            def _run() -> Any:
                async def _body() -> Any:
                    hook = DirectiveTriggerHook()
                    return await hook.execute(ctx)
                return asyncio.run(_body())

            result = _run()
            if result.metadata.get("triggered", 0) > 0:
                fired_count += 1

        # At p=0.90 over 20 trials: extremely unlikely to fail all
        assert fired_count >= 10, f"Only fired {fired_count}/20 times for high priority"

    def test_fires_correct_sse_event(self) -> None:
        """Fired event has correct structure."""
        import random as _random
        # Force deterministic fire by patching random to always return 0.0
        original_random = _random.random
        _random.random = lambda: 0.0  # type: ignore[method-assign]
        try:
            ctx = _make_settlement_context(npc_in_area="npc_alice")
            _add_directive(
                ctx,
                "npc_alice",
                priority="medium",
                linked_quest_id="dq_notice",
            )

            def _run() -> Any:
                async def _body() -> Any:
                    hook = DirectiveTriggerHook()
                    return await hook.execute(ctx)
                return asyncio.run(_body())

            result = _run()
        finally:
            _random.random = original_random  # type: ignore[method-assign]

        assert result.metadata.get("triggered") == 1
        assert len(result.sse_events) == 1
        event = result.sse_events[0]
        assert event.event_type == "npc_wants_to_chat"
        assert event.payload["npc_id"] == "npc_alice"
        assert event.payload["reason"] == "directive"
        assert event.payload["linked_quest_id"] == "dq_notice"
        assert event.payload["colocated"] is False
        assert event.payload["npc_location"] == "market"

    def test_same_room_directive_sets_colocated_true(self) -> None:
        """Same room directives produce a colocated invitation payload."""
        import random as _random
        original_random = _random.random
        _random.random = lambda: 0.0  # type: ignore[method-assign]
        try:
            ctx = _make_settlement_context(
                npc_in_area="npc_alice",
                player_location="guild_hall",
                player_room="counter",
                npc_location="guild_hall",
                npc_room="counter",
            )
            _add_directive(ctx, "npc_alice", priority="high")

            def _run() -> Any:
                async def _body() -> Any:
                    hook = DirectiveTriggerHook()
                    return await hook.execute(ctx)
                return asyncio.run(_body())

            result = _run()
        finally:
            _random.random = original_random  # type: ignore[method-assign]

        assert result.metadata.get("triggered") == 1
        payload = result.sse_events[0].payload
        assert payload["colocated"] is True
        assert "npc_location" not in payload
        assert "npc_room" not in payload

    def test_different_room_directive_carries_room_hint(self) -> None:
        """Same sub-location but different room should degrade to a location hint."""
        import random as _random
        original_random = _random.random
        _random.random = lambda: 0.0  # type: ignore[method-assign]
        try:
            ctx = _make_settlement_context(
                npc_in_area="npc_alice",
                player_location="guild_hall",
                player_room="office",
                npc_location="guild_hall",
                npc_room="counter",
            )
            _add_directive(ctx, "npc_alice", priority="high", issued_at_tick=6)

            def _run() -> Any:
                async def _body() -> Any:
                    hook = DirectiveTriggerHook()
                    return await hook.execute(ctx)
                return asyncio.run(_body())

            result = _run()
        finally:
            _random.random = original_random  # type: ignore[method-assign]

        assert result.metadata.get("triggered") == 1
        payload = result.sse_events[0].payload
        assert payload["colocated"] is False
        assert payload["npc_location"] == "guild_hall"
        assert payload["npc_room"] == "counter"

    def test_respects_cooldown(self) -> None:
        """Hook respects cooldown flag — same NPC not triggered again within cooldown."""
        import random as _random
        original_random = _random.random
        _random.random = lambda: 0.0  # type: ignore[method-assign]
        try:
            ctx = _make_settlement_context(npc_in_area="npc_alice", absolute_tick=10)
            _add_directive(ctx, "npc_alice", priority="high")

            def _run_hook() -> Any:
                async def _body() -> Any:
                    hook = DirectiveTriggerHook()
                    return await hook.execute(ctx)
                return asyncio.run(_body())

            # First run — sets cooldown
            result1 = _run_hook()
            assert result1.metadata.get("triggered") == 1

            # Second run at same tick — cooldown active
            result2 = _run_hook()
            assert result2.metadata.get("triggered") == 0
        finally:
            _random.random = original_random  # type: ignore[method-assign]

    def test_skips_consumed_directive(self) -> None:
        """Consumed directives are skipped."""
        import random as _random
        original_random = _random.random
        _random.random = lambda: 0.0  # type: ignore[method-assign]
        try:
            ctx = _make_settlement_context(npc_in_area="npc_alice")
            _add_directive(ctx, "npc_alice", priority="high", consumed=True)

            def _run() -> Any:
                async def _body() -> Any:
                    hook = DirectiveTriggerHook()
                    return await hook.execute(ctx)
                return asyncio.run(_body())

            result = _run()
        finally:
            _random.random = original_random  # type: ignore[method-assign]

        assert result.metadata.get("skipped") == "no_pending_directives"

    def test_skips_expired_directive(self) -> None:
        """Expired directives are skipped."""
        import random as _random
        original_random = _random.random
        _random.random = lambda: 0.0  # type: ignore[method-assign]
        try:
            ctx = _make_settlement_context(
                npc_in_area="npc_alice",
                absolute_tick=20,
            )
            # expires_at_tick=5 < current_tick=20 → expired
            _add_directive(ctx, "npc_alice", priority="high", expires_at_tick=5)

            def _run() -> Any:
                async def _body() -> Any:
                    hook = DirectiveTriggerHook()
                    return await hook.execute(ctx)
                return asyncio.run(_body())

            result = _run()
        finally:
            _random.random = original_random  # type: ignore[method-assign]

        assert result.metadata.get("skipped") == "no_pending_directives"

    def test_max_one_per_tick(self) -> None:
        """Only one NPC invitation is emitted even with multiple reachable directives."""
        import random as _random
        original_random = _random.random
        _random.random = lambda: 0.0  # type: ignore[method-assign]
        try:
            ctx = _make_settlement_context(area_id="area1")
            # Add two NPCs to the area
            ctx.state.areas.move_npc("npc_alice", "area1", "market")
            ctx.state.areas.move_npc("npc_bob", "area1", "market")

            _add_directive(ctx, "npc_alice", priority="high")
            _add_directive(ctx, "npc_bob", priority="high")

            def _run() -> Any:
                async def _body() -> Any:
                    hook = DirectiveTriggerHook()
                    return await hook.execute(ctx)
                return asyncio.run(_body())

            result = _run()
        finally:
            _random.random = original_random  # type: ignore[method-assign]

        assert result.metadata.get("triggered") == 1
        assert len(result.sse_events) == 1

    def test_hook_priority_is_76(self) -> None:
        """DirectiveTriggerHook has priority 76."""
        hook = DirectiveTriggerHook()
        assert hook.priority == 76
        assert hook.name == "directive_trigger"

    def test_skips_npc_not_reachable(self) -> None:
        """Directive for an NPC not in the player's current area is skipped."""
        import random as _random
        original_random = _random.random
        _random.random = lambda: 0.0  # type: ignore[method-assign]
        try:
            # NPC "npc_bob" is in the area but directive is for "npc_alice" who is absent
            ctx = _make_settlement_context(npc_in_area="npc_bob")
            _add_directive(ctx, "npc_alice", priority="high")

            def _run() -> Any:
                async def _body() -> Any:
                    hook = DirectiveTriggerHook()
                    return await hook.execute(ctx)
                return asyncio.run(_body())

            result = _run()
        finally:
            _random.random = original_random  # type: ignore[method-assign]

        assert result.metadata.get("triggered") == 0

    def test_hook_registered_in_defaults(self) -> None:
        """DirectiveTriggerHook is present in DEFAULT_SETTLEMENT_HOOK_TYPES."""
        from app.game_core.orchestration.defaults import DEFAULT_SETTLEMENT_HOOK_TYPES
        hook_names = {h.__name__ for h in DEFAULT_SETTLEMENT_HOOK_TYPES}
        assert "DirectiveTriggerHook" in hook_names


# ------------------------------------------------------------------
# A2: Planner context includes pending directives
# ------------------------------------------------------------------


class TestPlannerContextIncludesPendingDirectives:
    def test_planner_context_includes_pending_directives(self) -> None:
        """_build_planner_context() includes unconsumed non-expired directives."""
        ctx = _make_settlement_context(absolute_tick=5)
        _add_directive(
            ctx, "npc_alice", priority="high",
            issued_at_tick=3, expires_at_tick=50,
        )

        hook = NarrativePlannerHook()
        # Use a test double that returns a recording decision
        pc = hook._build_planner_context(ctx, current_tick=5)
        npc_directives = pc.get("narrative_plan", {}).get("npc_directives", [])
        assert len(npc_directives) == 1
        entry = npc_directives[0]
        assert entry["npc_id"] == "npc_alice"
        assert entry["priority"] == "high"
        assert entry["issued_at_tick"] == 3
        assert entry["kind"] == "talk"

    def test_planner_context_excludes_consumed_directives(self) -> None:
        """Consumed directives are excluded from the context snapshot."""
        ctx = _make_settlement_context(absolute_tick=5)
        _add_directive(ctx, "npc_alice", priority="high", consumed=True)

        hook = NarrativePlannerHook()
        pc = hook._build_planner_context(ctx, current_tick=5)
        npc_directives = pc.get("narrative_plan", {}).get("npc_directives", [])
        assert npc_directives == []

    def test_planner_context_excludes_expired_directives(self) -> None:
        """Expired directives are excluded from the context snapshot."""
        ctx = _make_settlement_context(absolute_tick=20)
        _add_directive(ctx, "npc_alice", priority="medium", expires_at_tick=10)

        hook = NarrativePlannerHook()
        pc = hook._build_planner_context(ctx, current_tick=20)
        npc_directives = pc.get("narrative_plan", {}).get("npc_directives", [])
        assert npc_directives == []

    def test_format_planner_context_shows_pending(self) -> None:
        """_format_planner_context() emits the pending-directives section."""
        from app.narrators import _format_planner_context

        ctx_dict: dict[str, Any] = {
            "current_tick": 10,
            "narrative_plan": {
                "current_chapter": "ch1",
                "escalation_level": 0,
                "ticks_since_milestone_progress": 0,
                "chapter_completion": 0.0,
                "current_target_milestone": None,
                "pacing_frozen": False,
                "strategy_notes": "",
                "last_run_tick": 0,
                "next_scheduled_tick": None,
                "behavior_window": [],
                "npc_directives": [
                    {
                        "npc_id": "npc_alice",
                        "kind": "talk",
                        "priority": "high",
                        "issued_at_tick": 5,
                    }
                ],
            },
            "quests": {
                "available_milestones": [],
                "active_milestones": [],
                "completed_milestones": [],
                "dynamic_quests": {},
            },
            "time": {"day": 1, "slot": 3, "period": "morning", "absolute_tick": 10},
            "location": {"area_id": "frontier_town", "location_id": None},
            "area_npcs": [],
            "area_boards": [],
            "party": [],
            "play_style_tags": [],
            "changed_slices": [],
            "change_count": 0,
            "recent_changes": [],
            "scene": {"entry_count": 0, "state_change_count": 0, "system_entries_digest": [], "visible_command_types": []},
            "events": {"pending_events_digest": []},
            "story_facts": [],
            "world_context": {},
            "area_cluster": None,
            "target_milestone_detail": None,
            "danger_level": 0.0,
        }

        formatted = _format_planner_context(ctx_dict)
        assert "未消费指令" in formatted
        assert "npc=npc_alice" in formatted
        assert "priority=high" in formatted
        assert "issued_at=5" in formatted

    def test_format_planner_context_no_pending_omits_section(self) -> None:
        """When no pending directives exist, the section is omitted."""
        from app.narrators import _format_planner_context

        ctx_dict: dict[str, Any] = {
            "current_tick": 10,
            "narrative_plan": {
                "current_chapter": "ch1",
                "escalation_level": 0,
                "ticks_since_milestone_progress": 0,
                "chapter_completion": 0.0,
                "current_target_milestone": None,
                "pacing_frozen": False,
                "strategy_notes": "",
                "last_run_tick": 0,
                "next_scheduled_tick": None,
                "behavior_window": [],
                "npc_directives": [],
            },
            "quests": {
                "available_milestones": [],
                "active_milestones": [],
                "completed_milestones": [],
                "dynamic_quests": {},
            },
            "time": {"day": 1, "slot": 3, "period": "morning", "absolute_tick": 10},
            "location": {"area_id": "frontier_town", "location_id": None},
            "area_npcs": [],
            "area_boards": [],
            "party": [],
            "play_style_tags": [],
            "changed_slices": [],
            "change_count": 0,
            "recent_changes": [],
            "scene": {"entry_count": 0, "state_change_count": 0, "system_entries_digest": [], "visible_command_types": []},
            "events": {"pending_events_digest": []},
            "story_facts": [],
            "world_context": {},
            "area_cluster": None,
            "target_milestone_detail": None,
            "danger_level": 0.0,
        }

        formatted = _format_planner_context(ctx_dict)
        assert "未消费指令" not in formatted


# ------------------------------------------------------------------
# A3: Prompt constraints (static text assertions)
# ------------------------------------------------------------------


class TestSystemPromptConstraints:
    def test_direct_npc_kind_enum_in_prompt(self) -> None:
        """_SYSTEM_PROMPT includes the kind enum for direct_npc."""
        from app.narrators import AgenticNarrativePlanner
        prompt = AgenticNarrativePlanner._SYSTEM_PROMPT
        assert "talk|approach|react|inform" in prompt

    def test_direct_npc_topic_field_in_prompt(self) -> None:
        """_SYSTEM_PROMPT shows 'topic' field in the direct_npc example."""
        from app.narrators import AgenticNarrativePlanner
        prompt = AgenticNarrativePlanner._SYSTEM_PROMPT
        assert '"topic"' in prompt

    def test_rule_6_kind_constraint(self) -> None:
        """Rule 6 lists the allowed directive.kind values."""
        from app.narrators import AgenticNarrativePlanner
        prompt = AgenticNarrativePlanner._SYSTEM_PROMPT
        assert "talk" in prompt and "approach" in prompt
        assert "react" in prompt and "inform" in prompt
        # Ensure rule 6 exists
        assert "6." in prompt

    def test_rule_7_topic_not_content(self) -> None:
        """Rule 7 instructs to use 'topic' not full dialogue content."""
        from app.narrators import AgenticNarrativePlanner
        prompt = AgenticNarrativePlanner._SYSTEM_PROMPT
        assert "7." in prompt
        assert "topic" in prompt
