"""Smoke test: BasicEventConditionEvaluator importable from event_engine (O-2)."""

from __future__ import annotations

from app.game_core.orchestration.event_engine import (
    BasicEventConditionEvaluator,
    EventConditionDecision,
    EventConditionEvaluator,
    EventTransition,
)
from app.game_core.state import StateContainer
from app.game_core.content import WorldInstance


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
