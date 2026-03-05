"""D-P3f tests: encounter table data + failure_fallback SSE."""

from __future__ import annotations

import asyncio
import json
import os

from app.game_core.content import WorldInstance
from app.game_core.content.registries.maps import MapRegistry
from app.game_core.content.registries.monsters import MonsterRegistry
from app.game_core.content.registries.quests import QuestRegistry
from app.game_core.orchestration.hooks.narrative_planner import NarrativePlannerHook
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules import RulesEngine
from app.game_core.state import StateChange, StateContainer, StateDelta
from app.game_core.state.slices import (
    NarrativePlanSlice,
    QuestSlice,
    SceneSlice,
    TimeSlice,
)

_DATA_DIR = os.path.join(
    os.path.dirname(__file__),
    "..",
    "data",
    "goblin_slayer",
    "v2",
)


def _load_json(name: str) -> dict:
    with open(os.path.join(_DATA_DIR, name), encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# TestEncounterTableData
# ---------------------------------------------------------------------------

class TestEncounterTableData:
    def setup_method(self):
        maps_data = _load_json("maps.json")
        monsters_data = _load_json("monsters.json")
        self.maps_reg = MapRegistry()
        self.maps_reg.load(maps_data)
        self.monster_reg = MonsterRegistry()
        self.monster_reg.load(monsters_data)
        self.known_monster_ids = {m.id for m in self.monster_reg.list_all()}

    def test_frontier_town_encounter_table_nonempty(self):
        area = self.maps_reg.get("frontier_town")
        assert area is not None
        assert len(area.encounter_table) == 2

    def test_cow_girl_farm_encounter_table_nonempty(self):
        area = self.maps_reg.get("cow_girl_farm")
        assert area is not None
        assert len(area.encounter_table) == 2

    def test_encounter_table_schema_valid(self):
        for area_id in ("frontier_town", "cow_girl_farm"):
            area = self.maps_reg.get(area_id)
            for entry in area.encounter_table:
                assert len(entry.monster_ids) > 0, f"{area_id}: monster_ids must be non-empty"
                assert entry.weight > 0, f"{area_id}: weight must be positive"
                assert entry.description, f"{area_id}: description must be non-empty"

    def test_monster_ids_reference_known_monsters(self):
        for area_id in ("frontier_town", "cow_girl_farm"):
            area = self.maps_reg.get(area_id)
            for entry in area.encounter_table:
                for mid in entry.monster_ids:
                    assert mid in self.known_monster_ids, (
                        f"{area_id}: unknown monster_id '{mid}'"
                    )


# ---------------------------------------------------------------------------
# Helpers for NarrativePlannerHook tests
# ---------------------------------------------------------------------------

class _NoOpPlanner:
    async def plan(self, context):
        return None


def _make_planner_context(
    change_log: list[StateChange],
    quest_registry: QuestRegistry | None = None,
) -> SettlementContext:
    world = WorldInstance("test_world")
    if quest_registry is not None:
        world.register(quest_registry)

    state = StateContainer()

    time_slice = TimeSlice()
    time_slice.restore({"day": 1, "slot": 9})
    state.register(time_slice)

    quests = QuestSlice()
    quests.restore({"milestone_states": {}, "dynamic_quests": {}, "chapter_completion": {}})
    state.register(quests)

    narrative_plan = NarrativePlanSlice()
    narrative_plan.restore({"current_chapter": "ch2", "ticks_since_milestone_progress": 2})
    state.register(narrative_plan)

    scene_slice = SceneSlice()
    scene_slice.restore({})
    state.register(scene_slice)
    scene_bus = SceneBus(scene_slice)

    active_log = list(change_log)

    def _apply_delta(delta: StateDelta | None) -> None:
        if delta is None:
            return
        state.apply(delta)
        active_log.extend(delta.changes)

    return SettlementContext(
        change_log=active_log,
        state=state,
        world=world,
        scene_bus=scene_bus,
        _rules_engine=RulesEngine(),
        _apply_delta=_apply_delta,
    )


def _failed_change(milestone_id: str) -> StateChange:
    return StateChange(
        slice="quests",
        operation="modify",
        path=f"milestone_states.{milestone_id}",
        value={"state": "FAILED"},
    )


# ---------------------------------------------------------------------------
# TestMilestoneFailedSSE
# ---------------------------------------------------------------------------

class TestMilestoneFailedSSE:
    def _make_registry(self, failure_fallback: str | None) -> QuestRegistry:
        reg = QuestRegistry()
        reg.load({
            "milestones": {
                "ms_test": {
                    "id": "ms_test",
                    "title": "Test Milestone",
                    "chapter_id": "ch2",
                    "failure_fallback": failure_fallback,
                }
            }
        })
        return reg

    def test_emits_milestone_failed_sse_on_failed_state(self):
        ctx = _make_planner_context(
            change_log=[_failed_change("ms_test")],
            quest_registry=self._make_registry("fallback text"),
        )
        hook = NarrativePlannerHook(planner=_NoOpPlanner())
        result = asyncio.run(hook.execute(ctx))
        types = [e.event_type for e in result.sse_events]
        assert "milestone_failed" in types

    def test_failure_fallback_text_in_payload(self):
        ctx = _make_planner_context(
            change_log=[_failed_change("ms_test")],
            quest_registry=self._make_registry("公会将另寻高手"),
        )
        hook = NarrativePlannerHook(planner=_NoOpPlanner())
        result = asyncio.run(hook.execute(ctx))
        event = next(e for e in result.sse_events if e.event_type == "milestone_failed")
        assert event.payload["milestone_id"] == "ms_test"
        assert event.payload["failure_fallback"] == "公会将另寻高手"

    def test_no_fallback_returns_empty_string(self):
        ctx = _make_planner_context(
            change_log=[_failed_change("ms_test")],
            quest_registry=self._make_registry(None),
        )
        hook = NarrativePlannerHook(planner=_NoOpPlanner())
        result = asyncio.run(hook.execute(ctx))
        event = next(e for e in result.sse_events if e.event_type == "milestone_failed")
        assert event.payload["failure_fallback"] == ""

    def test_non_failed_state_change_no_sse(self):
        completed_change = StateChange(
            slice="quests",
            operation="modify",
            path="milestone_states.ms_test",
            value={"state": "COMPLETED"},
        )
        ctx = _make_planner_context(
            change_log=[completed_change],
            quest_registry=self._make_registry("fallback"),
        )
        hook = NarrativePlannerHook(planner=_NoOpPlanner())
        result = asyncio.run(hook.execute(ctx))
        types = [e.event_type for e in result.sse_events]
        assert "milestone_failed" not in types
