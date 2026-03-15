"""Tests for P18 Phase 1 — §1.1 escalate RulesEngine, §1.4 SSE/SceneBus,
§1.5 keyword extraction rewrite and §1.3a story_facts field.

Covers:
- §1.1: escalate directive calls adjust_danger + set_flag via RulesEngine
- §1.4: plant_environmental and fill_area emit environment_changed SSE + SceneBus ENGINE entries
- §1.5: _extract_scene_keywords returns English entity IDs (actor, area, location,
  quest milestones, NPC IDs from scene metadata), not word-tokenized text from Chinese content
- §1.3a: NarrativePlanSlice.story_facts field: init, add_story_facts(), snapshot(), restore()
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.game_core.bootstrap import build_default_world, build_runtime_for_world
from app.game_core.content import WorldInstance
from app.game_core.narrative.context_builder import AgentContextBuilder
from app.game_core.orchestration.hooks.narrative_planner import NarrativePlannerHook
from app.game_core.orchestration.models import SSEEvent
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.planning import NarrativeWeaverSubSystem
from app.game_core.planning.npc_director import NpcDirectorSubSystem
from app.game_core.planning.pacing_controller import PacingControllerSubSystem
from app.game_core.planning.quest_manager import QuestManagerSubSystem
from app.game_core.planning.subsystem import PlannerDispatcher
from app.game_core.planning.world_builder import WorldBuilderSubSystem
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
from app.game_core.state.slices.narrative_plan import NarrativePlanSlice  # noqa: F811


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_builder(
    *,
    area_id: str = "frontier_town",
    location: str = "tavern",
    quests: dict[str, Any] | None = None,
) -> AgentContextBuilder:
    world = build_default_world("test_world")
    runtime = build_runtime_for_world(world)
    state = runtime.state

    state.player.restore({
        "current_area": area_id,
        "current_location": location,
    })

    if quests is not None:
        state.quests.restore({
            "available_milestones": [],
            "active_milestones": [],
            "dynamic_quests": quests,
        })

    return AgentContextBuilder(world, state)


# ------------------------------------------------------------------
# 1.5  _extract_scene_keywords tests
# ------------------------------------------------------------------


class TestExtractSceneKeywords:
    def test_returns_actor_id_as_first_keyword(self) -> None:
        builder = _make_builder()
        keywords = builder._extract_scene_keywords("merchant_tom", role="npc")
        assert keywords[0] == "merchant_tom"

    def test_includes_area_and_location_ids(self) -> None:
        builder = _make_builder(area_id="dark_forest", location="ruined_shrine")
        keywords = builder._extract_scene_keywords("some_npc", role="npc")
        assert "dark_forest" in keywords
        assert "ruined_shrine" in keywords

    def test_includes_active_quest_milestones(self) -> None:
        quests = {
            "q_rescue": {
                "title": "Rescue the Farmer",
                "status": "in_progress",
                "target_milestone": "milestone_find_clue",
            },
        }
        builder = _make_builder(quests=quests)
        keywords = builder._extract_scene_keywords("npc_a", role="npc")
        assert "milestone_find_clue" in keywords

    def test_ignores_completed_quest_milestones(self) -> None:
        quests = {
            "q_done": {
                "title": "Old Quest",
                "status": "completed",
                "target_milestone": "milestone_old",
            },
        }
        builder = _make_builder(quests=quests)
        keywords = builder._extract_scene_keywords("npc_a", role="npc")
        assert "milestone_old" not in keywords

    def test_no_text_splitting_from_chinese_content(self) -> None:
        world = build_default_world("test_world")
        runtime = build_runtime_for_world(world)
        state = runtime.state
        state.player.restore({"current_area": "town", "current_location": "market"})

        from app.game_core.state.slices.scene import SceneEntry
        entry = SceneEntry(
            source="npc_chen",
            content="\u8fd9\u91cc\u6709\u5f88\u591a\u5546\u54c1\uff0c\u8bf7\u95ee\u60a8\u9700\u8981\u4ec0\u4e48\uff1f",
            visibility="public",
        )
        state.scene.add_entry(entry)

        builder = AgentContextBuilder(world, state)
        keywords = builder._extract_scene_keywords("npc_chen", role="npc")

        for kw in keywords:
            assert kw.isascii(), f"Unexpected non-ASCII keyword: {kw!r}"

    def test_extracts_npc_id_from_scene_entry_metadata(self) -> None:
        world = build_default_world("test_world")
        runtime = build_runtime_for_world(world)
        state = runtime.state
        state.player.restore({"current_area": "town", "current_location": "square"})

        from app.game_core.state.slices.scene import SceneEntry
        entry = SceneEntry(
            source="player",
            content="greets the guard",
            visibility="public",
            metadata={"npc_id": "guard_captain"},
        )
        state.scene.add_entry(entry)

        builder = AgentContextBuilder(world, state)
        keywords = builder._extract_scene_keywords("player", role="npc")
        assert "guard_captain" in keywords

    def test_extracts_character_id_from_scene_entry_metadata(self) -> None:
        world = build_default_world("test_world")
        runtime = build_runtime_for_world(world)
        state = runtime.state
        state.player.restore({"current_area": "town", "current_location": "inn"})

        from app.game_core.state.slices.scene import SceneEntry
        entry = SceneEntry(
            source="innkeeper",
            content="Welcome, traveler.",
            visibility="public",
            metadata={"character_id": "innkeeper_val"},
        )
        state.scene.add_entry(entry)

        builder = AgentContextBuilder(world, state)
        keywords = builder._extract_scene_keywords("innkeeper", role="npc")
        assert "innkeeper_val" in keywords

    def test_deduplicates_keywords(self) -> None:
        builder = _make_builder(area_id="frontier_town", location="tavern")
        keywords = builder._extract_scene_keywords("frontier_town", role="npc")
        assert keywords.count("frontier_town") == 1

    def test_caps_at_20_keywords(self) -> None:
        quests = {
            f"q{i}": {
                "title": f"Quest {i}",
                "status": "in_progress",
                "target_milestone": f"milestone_{i:03d}",
            }
            for i in range(30)
        }
        builder = _make_builder(quests=quests)
        keywords = builder._extract_scene_keywords("npc_x", role="npc")
        assert len(keywords) <= 20

    def test_empty_area_and_location_skipped(self) -> None:
        world = build_default_world("test_world")
        runtime = build_runtime_for_world(world)
        state = runtime.state
        state.player.restore({})

        builder = AgentContextBuilder(world, state)
        keywords = builder._extract_scene_keywords("lonely_npc", role="npc")
        assert "lonely_npc" in keywords
        assert "" not in keywords


# ------------------------------------------------------------------
# 1.3a  NarrativePlanSlice.story_facts tests
# ------------------------------------------------------------------


class TestNarrativePlanStoryFacts:
    def test_initial_story_facts_is_empty_list(self) -> None:
        slice_ = NarrativePlanSlice()
        assert slice_.story_facts == []

    def test_add_story_facts_appends_copies(self) -> None:
        slice_ = NarrativePlanSlice()
        fact = {"subject": "hero", "relation": "defeated", "object": "dragon"}
        slice_.add_story_facts([fact])
        assert len(slice_.story_facts) == 1
        assert slice_.story_facts[0] == fact
        fact["subject"] = "villain"
        assert slice_.story_facts[0]["subject"] == "hero"

    def test_add_story_facts_marks_dirty(self) -> None:
        slice_ = NarrativePlanSlice()
        slice_.clear_dirty()
        slice_.add_story_facts([{"subject": "a", "relation": "b", "object": "c"}])
        assert slice_.dirty

    def test_empty_add_still_marks_dirty(self) -> None:
        slice_ = NarrativePlanSlice()
        slice_.clear_dirty()
        slice_.add_story_facts([])
        assert slice_.dirty

    def test_add_story_facts_multiple_calls_accumulate(self) -> None:
        slice_ = NarrativePlanSlice()
        slice_.add_story_facts([{"subject": "a", "relation": "r", "object": "b"}])
        slice_.add_story_facts([{"subject": "c", "relation": "r", "object": "d"}])
        assert len(slice_.story_facts) == 2

    def test_snapshot_includes_story_facts(self) -> None:
        slice_ = NarrativePlanSlice()
        slice_.add_story_facts([{"subject": "x", "relation": "y", "object": "z"}])
        snap = slice_.snapshot()
        assert "story_facts" in snap
        assert snap["story_facts"] == [{"subject": "x", "relation": "y", "object": "z"}]

    def test_snapshot_story_facts_is_defensive_copy(self) -> None:
        slice_ = NarrativePlanSlice()
        slice_.add_story_facts([{"subject": "a", "relation": "r", "object": "b"}])
        snap = slice_.snapshot()
        snap["story_facts"].clear()
        assert len(slice_.story_facts) == 1

    def test_restore_loads_story_facts(self) -> None:
        slice_ = NarrativePlanSlice()
        payload = {
            "story_facts": [
                {"subject": "hero", "relation": "found", "object": "sword"},
                {"subject": "guild", "relation": "controls", "object": "town"},
            ]
        }
        slice_.restore(payload)
        assert len(slice_.story_facts) == 2
        assert slice_.story_facts[0]["subject"] == "hero"
        assert slice_.story_facts[1]["object"] == "town"

    def test_restore_missing_story_facts_defaults_to_empty(self) -> None:
        slice_ = NarrativePlanSlice()
        slice_.restore({})
        assert slice_.story_facts == []

    def test_restore_filters_non_mapping_items(self) -> None:
        slice_ = NarrativePlanSlice()
        slice_.restore({"story_facts": [
            {"subject": "a", "relation": "r", "object": "b"},
            "bad_string",
            42,
        ]})
        assert len(slice_.story_facts) == 1

    def test_serialize_restore_round_trip(self) -> None:
        original = NarrativePlanSlice()
        original.add_story_facts([
            {"subject": "rebel_leader", "relation": "hides_in", "object": "cave_of_shadows"},
            {"subject": "king", "relation": "fears", "object": "rebel_leader"},
        ])

        serialized = original.serialize()

        restored = NarrativePlanSlice()
        restored.restore(serialized)

        assert len(restored.story_facts) == 2
        assert restored.story_facts[0] == {
            "subject": "rebel_leader",
            "relation": "hides_in",
            "object": "cave_of_shadows",
        }
        assert restored.story_facts[1] == {
            "subject": "king",
            "relation": "fears",
            "object": "rebel_leader",
        }

    def test_restore_clears_dirty_flag(self) -> None:
        slice_ = NarrativePlanSlice()
        slice_.add_story_facts([{"subject": "a", "relation": "r", "object": "b"}])
        assert slice_.dirty
        slice_.restore({"story_facts": [{"subject": "a", "relation": "r", "object": "b"}]})
        assert not slice_.dirty


# ------------------------------------------------------------------
# Shared settlement-context factory for §1.1 and §1.4 tests
# ------------------------------------------------------------------


def _make_settlement_context(
    *,
    area_id: str = "test_area",
    initial_escalation: int = 0,
    knowledge_graph: Any | None = None,
) -> SettlementContext:
    """Minimal SettlementContext with FlagSlice and AreaSlice registered."""
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
        "ticks_since_milestone_progress": 0,
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
        knowledge_graph=knowledge_graph,
    )


class _RecordingKnowledgeGraph:
    def __init__(self) -> None:
        self.inject_calls: list[list[dict[str, Any]]] = []

    def inject_story_facts(self, facts: list[dict[str, Any]], session_id: str = "") -> None:
        self.inject_calls.append([dict(fact) for fact in facts])


# ------------------------------------------------------------------
# §1.3c  Hook-level story_facts persistence + immediate graph injection
# ------------------------------------------------------------------


class TestNarrativePlannerStoryFacts:
    def test_execute_persists_and_injects_story_facts(self) -> None:
        graph = _RecordingKnowledgeGraph()
        ctx = _make_settlement_context(knowledge_graph=graph)

        class _StoryFactsPlanner:
            async def plan(self, _context: Any) -> Any:
                return {
                    "directives": [],
                    "story_facts": [
                        {"subject": "guild", "relation": "warns_about", "object": "raiders"},
                        {"subject": "raiders", "relation": "target", "object": "western_farm", "weight": "0.5"},
                        {"subject": "", "relation": "bad", "object": "ignored"},
                        {"subject": "broken", "relation": "weight", "object": "node", "weight": "nope"},
                    ],
                }

        result = asyncio.run(NarrativePlannerHook(planner=_StoryFactsPlanner()).execute(ctx))

        assert result.metadata["story_fact_count"] == 3
        assert ctx.state.narrative_plan.story_facts == [
            {"subject": "guild", "relation": "warns_about", "object": "raiders"},
            {"subject": "raiders", "relation": "target", "object": "western_farm", "weight": 0.5},
            {"subject": "broken", "relation": "weight", "object": "node"},
        ]
        assert graph.inject_calls == [[
            {"subject": "guild", "relation": "warns_about", "object": "raiders"},
            {"subject": "raiders", "relation": "target", "object": "western_farm", "weight": 0.5},
            {"subject": "broken", "relation": "weight", "object": "node"},
        ]]


# ------------------------------------------------------------------
# §1.1  escalate -> RulesEngine (adjust_danger + set_flag)
# ------------------------------------------------------------------


class TestEscalateProducesWorldStateChanges:
    def _apply_directive(
        self,
        context: SettlementContext,
        delta: int,
    ) -> bool:
        pacing = PacingControllerSubSystem()
        return pacing.apply_directive("escalate", {"delta": delta}, context, current_tick=0)

    def test_escalate_sets_narrative_escalation_level_flag(self) -> None:
        """escalate directive sets narrative_escalation_level flag in FlagSlice."""
        ctx = _make_settlement_context(initial_escalation=2)

        result = self._apply_directive(ctx, delta=1)

        assert result is True
        assert ctx.state.narrative_plan.escalation_level == 3
        flag_value = ctx.state.flags.get("narrative_escalation_level")
        assert flag_value == 3

    def test_escalate_adjusts_area_danger_level(self) -> None:
        """escalate directive calls adjust_danger for the current area."""
        ctx = _make_settlement_context(area_id="test_area", initial_escalation=0)
        assert "test_area" in ctx.state.areas.areas
        danger_before = ctx.state.areas.areas["test_area"].danger_level

        result = self._apply_directive(ctx, delta=2)

        assert result is True
        area_snap = ctx.state.areas.snapshot()
        danger = area_snap.get("areas", {}).get("test_area", {}).get("danger_level", 0.0)
        assert abs(danger - (danger_before + 0.1)) < 1e-6

    def test_escalate_negative_delta_reduces_danger(self) -> None:
        """Negative escalate delta decrements danger level."""
        ctx = _make_settlement_context(area_id="test_area", initial_escalation=2)
        danger_before = ctx.state.areas.areas["test_area"].danger_level

        self._apply_directive(ctx, delta=-1)

        area_snap = ctx.state.areas.snapshot()
        danger = area_snap.get("areas", {}).get("test_area", {}).get("danger_level", 0.0)
        assert abs(danger - (danger_before - 0.05)) < 1e-6

    def test_escalate_invalid_delta_bool_rejected(self) -> None:
        """escalate with a bool delta returns a rejection reason (not True)."""
        ctx = _make_settlement_context()
        pacing = PacingControllerSubSystem()
        result = pacing.apply_directive("escalate", {"delta": True}, ctx, current_tick=0)
        assert result is not True

    def test_escalate_out_of_range_delta_rejected(self) -> None:
        """escalate with delta > 3 returns a rejection reason (not True)."""
        ctx = _make_settlement_context()
        pacing = PacingControllerSubSystem()
        result = pacing.apply_directive("escalate", {"delta": 4}, ctx, current_tick=0)
        assert result is not True

    def test_escalate_no_player_slice_skips_adjust_danger(self) -> None:
        """If no player slice, escalate still works - skips adjust_danger but set_flag runs."""
        ctx = _make_settlement_context()
        del ctx.state._slices["player"]

        pacing = PacingControllerSubSystem()
        result = pacing.apply_directive("escalate", {"delta": 1}, ctx, current_tick=0)
        assert result is True
        assert ctx.state.narrative_plan.escalation_level == 1


class TestOnExpireEscalateProducesWorldStateChanges:
    def test_on_expire_escalate_sets_flag(self) -> None:
        """on_expire='escalate' writes narrative_escalation_level flag."""
        ctx = _make_settlement_context(area_id="test_area", initial_escalation=1)
        ctx.state.quests.add_dynamic_quest("dq_expired", {
            "quest_id": "dq_expired",
            "status": "active",
            "title": "Old Crisis",
            "summary": "Expired crisis",
            "on_expire": "escalate",
            "expiry_ticks": 5,
            "created_at_tick": 0,
        })

        # _expire_dynamic_quests has been migrated to NarrativeWeaverSubSystem
        weaver = NarrativeWeaverSubSystem()
        weaver._expire_dynamic_quests(ctx, current_tick=10)

        flag_value = ctx.state.flags.get("narrative_escalation_level")
        assert flag_value == 2

    def test_on_expire_escalate_bumps_danger(self) -> None:
        """on_expire='escalate' bumps danger level by 0.05 from baseline."""
        ctx = _make_settlement_context(area_id="test_area", initial_escalation=0)
        danger_before = ctx.state.areas.areas["test_area"].danger_level

        ctx.state.quests.add_dynamic_quest("dq_expired", {
            "quest_id": "dq_expired",
            "status": "active",
            "title": "Old Crisis",
            "summary": "Expired crisis",
            "on_expire": "escalate",
            "expiry_ticks": 5,
            "created_at_tick": 0,
        })

        # _expire_dynamic_quests has been migrated to NarrativeWeaverSubSystem
        weaver = NarrativeWeaverSubSystem()
        weaver._expire_dynamic_quests(ctx, current_tick=10)

        area_snap = ctx.state.areas.snapshot()
        danger = area_snap.get("areas", {}).get("test_area", {}).get("danger_level", 0.0)
        assert abs(danger - (danger_before + 0.05)) < 1e-6


# ------------------------------------------------------------------
# §1.4  plant_environmental and fill_area -> SSE + SceneBus
# ------------------------------------------------------------------


def _make_full_hook_p18(planner: Any = None) -> tuple[NarrativePlannerHook, list[SSEEvent]]:
    """Build a NarrativePlannerHook with all 6 sub-systems, returning (hook, pending_sse)."""
    hook = NarrativePlannerHook(planner=planner)
    dispatcher = PlannerDispatcher()
    quest_manager = QuestManagerSubSystem(dispatcher=dispatcher)
    dispatcher.register(quest_manager)
    dispatcher.register(NpcDirectorSubSystem())
    dispatcher.register(WorldBuilderSubSystem(sse_collector=hook._pending_sse))
    dispatcher.register(PacingControllerSubSystem())
    dispatcher.register(NarrativeWeaverSubSystem(sse_collector=hook._pending_sse))
    hook._dispatcher = dispatcher
    return hook, hook._pending_sse


class TestPlantEnvironmentalEmitsSSE:
    def _run_plant_environmental(
        self,
        *,
        area_id: str = "test_area",
        description: str = "神秘符文区",
        clue_id: str = "clue_001",
    ) -> tuple[list[SSEEvent], SettlementContext]:
        ctx = _make_settlement_context(area_id=area_id)
        pending_sse: list[SSEEvent] = []
        world_builder = WorldBuilderSubSystem(sse_collector=pending_sse)
        world_builder.apply_directive(
            "plant_environmental",
            {
                "area_id": area_id,
                "clue_id": clue_id,
                "description": description,
                "dc": 10,
                "expiry_ticks": 8,
            },
            ctx,
            current_tick=5,
        )
        return pending_sse, ctx

    def test_plant_environmental_appends_environment_changed_sse(self) -> None:
        pending_sse, _ctx = self._run_plant_environmental()
        assert len(pending_sse) == 1
        event = pending_sse[0]
        assert event.event_type == "environment_changed"
        assert event.payload["change_type"] == "plant_environmental"

    def test_plant_environmental_sse_contains_area_and_sub_area(self) -> None:
        pending_sse, _ctx = self._run_plant_environmental(
            area_id="test_area",
            clue_id="clue_alpha",
        )
        event = pending_sse[0]
        assert event.payload["area_id"] == "test_area"
        assert event.payload["sub_area_id"] == "clue_alpha"

    def test_plant_environmental_writes_engine_entry_to_scene_bus(self) -> None:
        _pending_sse, ctx = self._run_plant_environmental(description="Glowing rune")
        scene_snap = ctx.scene_bus.snapshot()
        entries = scene_snap.get("entries", [])
        engine_entries = [e for e in entries if e.get("source") == "ENGINE"]
        assert len(engine_entries) >= 1
        found_tags = [tag for e in engine_entries for tag in e.get("tags", [])]
        assert "environment_changed" in found_tags
        assert "narrative_planner" in found_tags

    def test_plant_environmental_sse_drained_into_hook_result(self) -> None:
        """When execute() runs plant_environmental, SSE appears in HookResult."""
        ctx = _make_settlement_context(area_id="test_area")
        # Cooldown bypass: last_run_tick=0, current_tick=9 (day=1,slot=9).
        # FALLBACK_INTERVAL=6, so ticks_since_last_run=9 >= 6.
        ctx.state.narrative_plan.last_run_tick = 0

        class _PlantPlanner:
            async def plan(self, _context: Any) -> Any:
                return {
                    "directives": [
                        {
                            "kind": "plant_environmental",
                            "payload": {
                                "area_id": "test_area",
                                "clue_id": "clue_from_planner",
                                "description": "Ancient runes",
                                "dc": 12,
                            },
                        }
                    ]
                }

        hook, _ = _make_full_hook_p18(planner=_PlantPlanner())
        result = asyncio.run(hook.execute(ctx))

        env_events = [e for e in result.sse_events if e.event_type == "environment_changed"]
        assert len(env_events) == 1
        assert env_events[0].payload["sub_area_id"] == "clue_from_planner"
        assert env_events[0].payload["change_type"] == "plant_environmental"


class TestFillAreaEmitsSSE:
    def _run_fill_area(
        self,
        *,
        area_id: str = "test_area",
        sub_area_id: str = "new_location",
        label: str = "The Old Mill",
    ) -> tuple[list[SSEEvent], SettlementContext]:
        ctx = _make_settlement_context(area_id=area_id)
        pending_sse: list[SSEEvent] = []
        world_builder = WorldBuilderSubSystem(sse_collector=pending_sse)
        world_builder.apply_directive(
            "fill_area",
            {
                "area_id": area_id,
                "id": sub_area_id,
                "label": label,
                "description": "An abandoned mill on the outskirts",
                "type": "visit",
            },
            ctx,
            current_tick=3,
        )
        return pending_sse, ctx

    def test_fill_area_appends_environment_changed_sse(self) -> None:
        pending_sse, _ctx = self._run_fill_area()
        assert len(pending_sse) == 1
        event = pending_sse[0]
        assert event.event_type == "environment_changed"
        assert event.payload["change_type"] == "fill_area"

    def test_fill_area_sse_contains_area_and_sub_area(self) -> None:
        pending_sse, _ctx = self._run_fill_area(
            area_id="test_area",
            sub_area_id="mill_sub",
            label="The Mill",
        )
        event = pending_sse[0]
        assert event.payload["area_id"] == "test_area"
        assert event.payload["sub_area_id"] == "mill_sub"
        assert event.payload["sub_area_label"] == "The Mill"

    def test_fill_area_writes_engine_entry_to_scene_bus(self) -> None:
        _pending_sse, ctx = self._run_fill_area(label="Spooky Cave")
        scene_snap = ctx.scene_bus.snapshot()
        entries = scene_snap.get("entries", [])
        engine_entries = [e for e in entries if e.get("source") == "ENGINE"]
        assert len(engine_entries) >= 1
        found_tags = [tag for e in engine_entries for tag in e.get("tags", [])]
        assert "environment_changed" in found_tags
        assert "narrative_planner" in found_tags

    def test_fill_area_sse_drained_into_hook_result(self) -> None:
        """When execute() runs fill_area, SSE appears in HookResult."""
        ctx = _make_settlement_context(area_id="test_area")
        ctx.state.narrative_plan.last_run_tick = 0

        class _FillPlanner:
            async def plan(self, _context: Any) -> Any:
                return {
                    "directives": [
                        {
                            "kind": "fill_area",
                            "payload": {
                                "area_id": "test_area",
                                "id": "new_cave",
                                "label": "Dark Cave",
                                "description": "A cave in the cliff",
                                "type": "visit",
                            },
                        }
                    ]
                }

        hook, _ = _make_full_hook_p18(planner=_FillPlanner())
        result = asyncio.run(hook.execute(ctx))

        env_events = [e for e in result.sse_events if e.event_type == "environment_changed"]
        assert len(env_events) == 1
        assert env_events[0].payload["sub_area_id"] == "new_cave"
        assert env_events[0].payload["change_type"] == "fill_area"


# ------------------------------------------------------------------
# §1.3b  WorldKnowledgeGraph.inject_story_facts
# ------------------------------------------------------------------


class TestInjectStoryFacts:
    def test_inject_adds_nodes_and_edge(self) -> None:
        """inject_story_facts creates subject/object nodes and a directed edge in session overlay."""
        from app.world_knowledge_graph import WorldKnowledgeGraph

        graph = WorldKnowledgeGraph()
        facts = [{"subject": "rebel_leader", "relation": "hides_in", "object": "cave_of_shadows"}]
        graph.inject_story_facts(facts, session_id="test")

        overlay = graph._sessions["test"].overlay
        assert "rebel_leader" in overlay
        assert "cave_of_shadows" in overlay
        assert overlay.has_edge("rebel_leader", "cave_of_shadows")

    def test_inject_reuses_existing_nodes(self) -> None:
        """inject_story_facts does not duplicate existing overlay nodes."""
        from app.world_knowledge_graph import WorldKnowledgeGraph

        graph = WorldKnowledgeGraph()
        # Pre-seed a node in the session overlay
        session = graph._ensure_session("test")
        session.overlay.add_node("player", label="Player", tags=[], description="", node_type="character")

        facts = [{"subject": "player", "relation": "knows_about", "object": "secret_passage"}]
        graph.inject_story_facts(facts, session_id="test")

        # Overlay node count should be 2 (player + secret_passage), not 3
        assert session.overlay.number_of_nodes() == 2

    def test_inject_skips_incomplete_facts(self) -> None:
        """inject_story_facts ignores facts with empty subject, relation, or object."""
        from app.world_knowledge_graph import WorldKnowledgeGraph

        graph = WorldKnowledgeGraph()
        facts = [
            {"subject": "", "relation": "r", "object": "b"},       # empty subject
            {"subject": "a", "relation": "", "object": "b"},       # empty relation
            {"subject": "a", "relation": "r", "object": ""},       # empty object
            {"subject": "a", "relation": "r", "object": "b"},      # valid
        ]
        graph.inject_story_facts(facts, session_id="test")
        overlay = graph._sessions["test"].overlay
        assert overlay.number_of_nodes() == 2
        assert overlay.number_of_edges() == 1

    def test_inject_weight_stored_on_edge(self) -> None:
        """inject_story_facts stores the fact's weight on the edge."""
        from app.world_knowledge_graph import WorldKnowledgeGraph

        graph = WorldKnowledgeGraph()
        facts = [{"subject": "king", "relation": "fears", "object": "dragon", "weight": 0.7}]
        graph.inject_story_facts(facts, session_id="test")

        overlay = graph._sessions["test"].overlay
        edge_data = overlay["king"]["dragon"]
        assert abs(edge_data.get("weight", 0.0) - 0.7) < 1e-6

    def test_inject_default_weight_is_one(self) -> None:
        """inject_story_facts defaults edge weight to 1.0 when not specified."""
        from app.world_knowledge_graph import WorldKnowledgeGraph

        graph = WorldKnowledgeGraph()
        facts = [{"subject": "hero", "relation": "defeated", "object": "orc"}]
        graph.inject_story_facts(facts, session_id="test")

        overlay = graph._sessions["test"].overlay
        edge_data = overlay["hero"]["orc"]
        assert abs(edge_data.get("weight", 0.0) - 1.0) < 1e-6

    def test_inject_facts_reachable_via_query_spread(self) -> None:
        """Injected story facts are discoverable via query_spread (spreading activation)."""
        def _run() -> None:
            import asyncio as _asyncio
            from app.world_knowledge_graph import WorldKnowledgeGraph

            graph = WorldKnowledgeGraph()
            # Inject a chain: player_hero → knows_about → ancient_artifact
            graph.inject_story_facts([
                {"subject": "player_hero", "relation": "knows_about", "object": "ancient_artifact"},
            ], session_id="test")

            async def _query():
                return await graph.query_spread(
                    "player_hero",
                    keywords=["player_hero"],
                    context={},
                    top_k=5,
                    session_id="test",
                )

            hits = _asyncio.run(_query())
            node_ids = {h["node_id"] for h in hits}
            # ancient_artifact is reachable from player_hero via one hop
            assert "ancient_artifact" in node_ids

        _run()

    def test_inject_empty_list_is_noop(self) -> None:
        """inject_story_facts with empty list does not change the session overlay."""
        from app.world_knowledge_graph import WorldKnowledgeGraph

        graph = WorldKnowledgeGraph()
        graph.inject_story_facts([], session_id="test")
        # Static base graph unchanged; no session created or overlay empty
        assert graph.node_count() == 0
        assert graph.edge_count() == 0

    def test_node_type_set_to_story_fact(self) -> None:
        """Nodes created by inject_story_facts have node_type='story_fact'."""
        from app.world_knowledge_graph import WorldKnowledgeGraph

        graph = WorldKnowledgeGraph()
        graph.inject_story_facts([
            {"subject": "shadow_guild", "relation": "controls", "object": "black_market"},
        ], session_id="test")
        overlay = graph._sessions["test"].overlay
        assert overlay.nodes["shadow_guild"]["node_type"] == "story_fact"
        assert overlay.nodes["black_market"]["node_type"] == "story_fact"

    def test_edge_source_attribute_is_story_fact(self) -> None:
        """Edges created by inject_story_facts are tagged with source='story_fact'."""
        from app.world_knowledge_graph import WorldKnowledgeGraph

        graph = WorldKnowledgeGraph()
        graph.inject_story_facts([
            {"subject": "a", "relation": "r", "object": "b"},
        ], session_id="test")
        overlay = graph._sessions["test"].overlay
        edge_data = overlay["a"]["b"]
        assert edge_data.get("source") == "story_fact"


# ------------------------------------------------------------------
# §1.2  _collect_npc_interaction_exchange / _collect_private_chat_exchange
# NOTE: These functions were removed in P19-C4.  Tests kept for history but skipped.
# ------------------------------------------------------------------


import pytest


@pytest.mark.skip(reason="P19-C4: _collect_npc_interaction_exchange removed (proactive path deleted)")
class TestCollectNpcInteractionExchange:
    def _make_round_messages(
        self,
        player_text: str = "Hello there",
        npc_text: str = "Greetings, traveler",
    ):
        from app.game_core.orchestration.npc_interaction import RoundMessage
        return [
            RoundMessage("player", "player", player_text, "speech"),
            RoundMessage("npc_innkeeper", "npc", npc_text, "speech"),
        ]

    def test_returns_two_messages_for_normal_exchange(self) -> None:
        from app.game_core.orchestration.npc_interaction import NpcInteractionResult
        from app.agent_orchestration import _collect_npc_interaction_exchange

        result = NpcInteractionResult(
            completed=True,
            npc_id="npc_innkeeper",
            round_messages=self._make_round_messages(),
        )
        messages = _collect_npc_interaction_exchange(result)
        assert len(messages) == 2
        assert messages[0].role == "user"
        assert messages[0].content == "Hello there"
        assert messages[1].role == "model"
        assert messages[1].content == "Greetings, traveler"

    def test_returns_empty_when_no_npc_speech(self) -> None:
        from app.game_core.orchestration.npc_interaction import NpcInteractionResult, RoundMessage
        from app.agent_orchestration import _collect_npc_interaction_exchange

        result = NpcInteractionResult(
            completed=True,
            npc_id="npc_silent",
            round_messages=[
                RoundMessage("player", "player", "Speak!", "speech"),
            ],
        )
        messages = _collect_npc_interaction_exchange(result)
        assert messages == []

    def test_returns_empty_when_round_messages_empty(self) -> None:
        from app.game_core.orchestration.npc_interaction import NpcInteractionResult
        from app.agent_orchestration import _collect_npc_interaction_exchange

        result = NpcInteractionResult(
            completed=True,
            npc_id="npc_empty",
            round_messages=[],
        )
        assert _collect_npc_interaction_exchange(result) == []

    def test_uses_first_player_and_first_npc_message_only(self) -> None:
        from app.game_core.orchestration.npc_interaction import NpcInteractionResult, RoundMessage
        from app.agent_orchestration import _collect_npc_interaction_exchange

        result = NpcInteractionResult(
            completed=True,
            npc_id="npc_chatty",
            round_messages=[
                RoundMessage("player", "player", "First player msg", "speech"),
                RoundMessage("npc_chatty", "npc", "First NPC reply", "speech"),
                RoundMessage("player", "player", "Second player msg", "speech"),
                RoundMessage("npc_chatty", "npc", "Second NPC reply", "speech"),
            ],
        )
        messages = _collect_npc_interaction_exchange(result)
        assert len(messages) == 2
        assert messages[0].content == "First player msg"
        assert messages[1].content == "First NPC reply"


@pytest.mark.skip(reason="P19-C4: _collect_private_chat_exchange removed (proactive path deleted)")
class TestCollectPrivateChatExchange:
    def _make_npc_result_with_speech(self, text: str):
        """Build a minimal AgentResult that _extract_visible_reply_text will return text from."""
        from app.game_core.narrative.models import AgentResult, ToolResult

        tool_result = ToolResult(
            ok=True,
            message=text,
            commands=[],
            metadata={"event_type": "speech"},
        )
        return AgentResult(tool_results=[tool_result], metadata={})

    def test_returns_two_messages_for_normal_exchange(self) -> None:
        from app.game_core.orchestration.private_chat import PrivateChatResult
        from app.agent_orchestration import _collect_private_chat_exchange

        npc_result = self._make_npc_result_with_speech("I've been waiting for you.")
        result = PrivateChatResult(
            completed=True,
            npc_id="npc_rogue",
            npc_result=npc_result,
        )
        messages = _collect_private_chat_exchange("A private word?", result)
        assert len(messages) == 2
        assert messages[0].role == "user"
        assert messages[0].content == "A private word?"
        assert messages[1].role == "model"
        assert messages[1].content == "I've been waiting for you."

    def test_returns_empty_when_no_npc_speech(self) -> None:
        from app.game_core.narrative.models import AgentResult
        from app.game_core.orchestration.private_chat import PrivateChatResult
        from app.agent_orchestration import _collect_private_chat_exchange

        # npc_result has no speech tool call
        npc_result = AgentResult(tool_results=[], metadata={})
        result = PrivateChatResult(
            completed=True,
            npc_id="npc_silent",
            npc_result=npc_result,
        )
        messages = _collect_private_chat_exchange("Hello?", result)
        assert messages == []

    def test_returns_empty_when_npc_result_is_none(self) -> None:
        from app.game_core.orchestration.private_chat import PrivateChatResult
        from app.agent_orchestration import _collect_private_chat_exchange

        result = PrivateChatResult(
            completed=True,
            npc_id="npc_none",
            npc_result=None,
        )
        messages = _collect_private_chat_exchange("Any reply?", result)
        assert messages == []

    def test_empty_player_message_still_returns_npc_message(self) -> None:
        from app.game_core.orchestration.private_chat import PrivateChatResult
        from app.agent_orchestration import _collect_private_chat_exchange

        npc_result = self._make_npc_result_with_speech("I speak regardless.")
        result = PrivateChatResult(
            completed=True,
            npc_id="npc_solo",
            npc_result=npc_result,
        )
        messages = _collect_private_chat_exchange("", result)
        # Empty player_message skips the user WindowMessage
        assert len(messages) == 1
        assert messages[0].role == "model"
        assert messages[0].content == "I speak regardless."
