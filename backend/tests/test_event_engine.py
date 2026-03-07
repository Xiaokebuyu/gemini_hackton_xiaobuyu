"""Smoke test: BasicEventConditionEvaluator importable from event_engine (O-2)."""

from __future__ import annotations

from app.game_core.orchestration.event_engine import (
    BasicEventConditionEvaluator,
    EventConditionDecision,
    EventConditionEvaluator,
    EventTransition,
    run_inline_event_check,
)
from app.game_core.state import StateContainer
from app.game_core.content import WorldInstance
from app.game_core.rules import RulesEngine
from app.game_core.rules.handlers import WorldStateHandler
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.state.slices import (
    EventSlice,
    FlagSlice,
    QuestSlice,
    RelationSlice,
    SceneSlice,
    TimeSlice,
)


def _build_rules_engine() -> RulesEngine:
    engine = RulesEngine()
    engine.register(WorldStateHandler())
    return engine


def _make_scene_state() -> SceneSlice:
    scene = SceneSlice()
    scene.restore({})
    return scene


def test_basic_evaluator_importable_from_event_engine() -> None:
    """BasicEventConditionEvaluator must be importable from event_engine and functional."""
    evaluator = BasicEventConditionEvaluator()
    state = StateContainer()
    world = WorldInstance("test_world")
    decision = evaluator.evaluate(state, world)
    assert isinstance(decision, EventConditionDecision)
    assert decision.transitions == []


def test_event_condition_hook_still_works_after_extraction() -> None:
    """EventConditionHook must still import and instantiate without errors."""
    from app.game_core.orchestration.hooks.event_condition import EventConditionHook
    hook = EventConditionHook()
    assert hook is not None
    assert isinstance(hook._evaluator, BasicEventConditionEvaluator)


# ------------------------------------------------------------------
# EventSlice.spread_rumor() tests
# ------------------------------------------------------------------


def _make_events_with_rumor(rumor_id: str = "rumor_abc123") -> EventSlice:
    events = EventSlice()
    events.restore({
        "rumors": [{"rumor_id": rumor_id, "text": "The king is ill", "known_by": []}]
    })
    return events


class TestSpreadRumor:
    def test_adds_npc_to_known_by(self) -> None:
        events = _make_events_with_rumor("r001")
        result = events.spread_rumor("r001", "npc_aldric")
        assert result is True
        assert "npc_aldric" in events.rumors[0]["known_by"]

    def test_idempotent_returns_false(self) -> None:
        events = _make_events_with_rumor("r001")
        events.spread_rumor("r001", "npc_aldric")
        result = events.spread_rumor("r001", "npc_aldric")
        assert result is False
        assert events.rumors[0]["known_by"].count("npc_aldric") == 1

    def test_unknown_rumor_id_returns_false(self) -> None:
        events = _make_events_with_rumor("r001")
        result = events.spread_rumor("nonexistent", "npc_aldric")
        assert result is False
        assert events.rumors[0].get("known_by") == []


# ------------------------------------------------------------------
# EventEngine condition type tests
# ------------------------------------------------------------------


def _make_state_with_relations(
    npc_id: str = "npc_mayor",
    approval: int = 60,
    trust: int = 40,
) -> StateContainer:
    state = StateContainer()
    rel = RelationSlice()
    rel.restore({
        "npc_dispositions": {npc_id: {"approval": approval, "trust": trust}},
        "relationship_stages": {},
        "shop_states": {},
    })
    state.register(rel)
    return state


def _make_state_with_time(day: int = 2, slot: int = 6) -> StateContainer:
    state = StateContainer()
    time_slice = TimeSlice()
    time_slice.restore({"day": day, "slot": slot})
    state.register(time_slice)
    return state


class TestConditionDisposition:
    _evaluator = BasicEventConditionEvaluator()

    def _check(self, state: StateContainer, params: dict) -> bool:
        met, _ = self._evaluator._condition_met(
            state, {"type": "disposition", **params}
        )
        return met

    def test_gte_passes(self) -> None:
        state = _make_state_with_relations(approval=60)
        assert self._check(
            state, {"npc_id": "npc_mayor", "dimension": "approval", "threshold": 50}
        ) is True

    def test_gte_fails(self) -> None:
        state = _make_state_with_relations(approval=40)
        assert self._check(
            state, {"npc_id": "npc_mayor", "dimension": "approval", "threshold": 50}
        ) is False

    def test_lte_operator(self) -> None:
        state = _make_state_with_relations(trust=30)
        assert self._check(
            state,
            {"npc_id": "npc_mayor", "dimension": "trust", "threshold": 40, "operator": "lte"},
        ) is True

    def test_eq_operator(self) -> None:
        state = _make_state_with_relations(approval=50)
        assert self._check(
            state,
            {"npc_id": "npc_mayor", "dimension": "approval", "threshold": 50, "operator": "eq"},
        ) is True

    def test_missing_relations_slice_returns_false(self) -> None:
        assert self._check(
            StateContainer(),
            {"npc_id": "npc_mayor", "dimension": "approval", "threshold": 50},
        ) is False


class TestConditionTimeElapsed:
    _evaluator = BasicEventConditionEvaluator()

    def _check(self, state: StateContainer, params: dict) -> bool:
        met, _ = self._evaluator._condition_met(
            state, {"type": "time_elapsed", **params}
        )
        return met

    def test_elapsed_passes(self) -> None:
        # day=2 slot=6 → absolute_tick = 24+6 = 30; since_tick=20, elapsed=10 → 30-20=10 >= 10
        state = _make_state_with_time(day=2, slot=6)
        assert self._check(state, {"since_tick": 20, "elapsed": 10}) is True

    def test_elapsed_fails(self) -> None:
        # absolute_tick=30; since_tick=25, elapsed=10 → 5 < 10
        state = _make_state_with_time(day=2, slot=6)
        assert self._check(state, {"since_tick": 25, "elapsed": 10}) is False

    def test_missing_time_slice_returns_false(self) -> None:
        assert self._check(StateContainer(), {"since_tick": 0, "elapsed": 1}) is False


class TestConditionCustom:
    _evaluator = BasicEventConditionEvaluator()

    def test_always_false(self) -> None:
        met, _ = self._evaluator._condition_met(
            StateContainer(), {"type": "custom", "key": "anything"}
        )
        assert met is False


def test_complete_objective_command_marks_objective_and_completes_quest() -> None:
    state = StateContainer()
    state.register(QuestSlice())
    state.register(EventSlice())
    state.register(FlagSlice())
    state.register(_make_scene_state())
    state.quests.restore({
        "dynamic_quests": {
            "dq_obj": {
                "status": "active",
                "objectives": [
                    {"type": "collect", "target": "crystal", "completed": False},
                ],
            },
        },
    })
    state.flags.restore({"flags": {"ready": True}})
    state.events.restore({
        "active_events": {
            "evt_obj_complete": {
                "id": "evt_obj_complete",
                "event_id": "evt_obj_complete",
                "state": "locked",
                "conditions": {"type": "flag_set", "key": "ready", "value": True},
                "on_trigger": [{
                    "type": "complete_objective",
                    "params": {"quest_id": "dq_obj", "objective_index": 0},
                }],
            },
        },
    })

    change_log: list = []
    scene_bus = SceneBus(state.scene)

    payloads = run_inline_event_check(
        state=state,
        world=WorldInstance("test_world"),
        rules_engine=_build_rules_engine(),
        apply_delta=state.apply,
        change_log=change_log,
        scene_bus=scene_bus,
        label="test_obj_complete",
    )

    assert len(payloads) == 1
    assert payloads[0]["event_id"] == "evt_obj_complete"
    quest = state.quests.get_dynamic_quest("dq_obj")
    assert quest is not None
    assert quest["status"] == "completed"
    assert isinstance(quest.get("objectives"), list)
    assert quest["objectives"][0].get("completed") is True
