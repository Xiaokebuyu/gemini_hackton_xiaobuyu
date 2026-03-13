"""Tests for Phase 3.4: update_quest directive.

Tests cover:
- QuestManager._apply_update_quest logic (current_step, next_steps, hints, completed_objectives)
- Incremental merge — only provided fields are updated
- Guard clauses (unknown quest, non-active status, missing quest_id)
- SSE notification (quest_progress_updated)
- quest_history logging
- NarrativePlannerHook._SUPPORTED_DIRECTIVES membership
- QuestManagerSubSystem._HANDLES membership

Decision record: D-P34 (narrative.md)
"""

from __future__ import annotations

from typing import Any

import pytest

from app.game_core.content import WorldInstance
from app.game_core.orchestration.hooks.narrative_planner import NarrativePlannerHook
from app.game_core.orchestration.models import SSEEvent
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.planning.quest_manager import QuestManagerSubSystem
from app.game_core.planning.subsystem import PlannerDispatcher
from app.game_core.rules import RulesEngine
from app.game_core.rules.defaults import register_default_rules_handlers
from app.game_core.state import StateChange, StateContainer, StateDelta
from app.game_core.state.slices import (
    NarrativePlanSlice,
    QuestSlice,
    SceneSlice,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_context(
    *,
    dynamic_quests: dict[str, Any] | None = None,
) -> SettlementContext:
    """Minimal SettlementContext with QuestSlice and NarrativePlanSlice."""
    world = WorldInstance("test_world")
    state = StateContainer()

    quests = QuestSlice()
    base_quests: dict[str, Any] = {
        "dynamic_quests": dynamic_quests or {},
        "milestone_states": {},
        "chapter_completion": {},
    }
    quests.restore(base_quests)
    state.register(quests)

    narrative_plan = NarrativePlanSlice()
    narrative_plan.restore({"current_chapter": "chapter_1"})
    state.register(narrative_plan)

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


def _make_manager(*, sse_collector: list | None = None) -> QuestManagerSubSystem:
    dispatcher = PlannerDispatcher()
    return QuestManagerSubSystem(dispatcher=dispatcher, sse_collector=sse_collector)


def _active_quest(*, title: str = "Active Quest") -> dict[str, Any]:
    return {
        "title": title,
        "summary": "An active quest.",
        "status": "active",
        "objectives": [],
    }


# ---------------------------------------------------------------------------
# Test: _HANDLES membership
# ---------------------------------------------------------------------------


def test_update_quest_in_handles() -> None:
    """QuestManagerSubSystem._HANDLES must include update_quest."""
    assert "update_quest" in QuestManagerSubSystem._HANDLES


# ---------------------------------------------------------------------------
# Test: _SUPPORTED_DIRECTIVES membership
# ---------------------------------------------------------------------------


def test_update_quest_in_supported_directives() -> None:
    """NarrativePlannerHook._SUPPORTED_DIRECTIVES must include update_quest."""
    assert "update_quest" in NarrativePlannerHook._SUPPORTED_DIRECTIVES


# ---------------------------------------------------------------------------
# Test: basic field updates
# ---------------------------------------------------------------------------


def test_update_quest_sets_current_step() -> None:
    """update_quest updates current_step in dynamic_quests."""
    context = _make_context(dynamic_quests={"dq_1": _active_quest()})
    manager = _make_manager()

    result = manager.apply_directive(
        "update_quest",
        {"quest_id": "dq_1", "current_step": "Go to the tavern"},
        context,
        current_tick=5,
    )

    assert result is True
    quest = context.state.quests.dynamic_quests["dq_1"]
    assert quest["current_step"] == "Go to the tavern"


def test_update_quest_sets_next_steps_and_hints() -> None:
    """update_quest stores next_steps and hints lists correctly."""
    context = _make_context(dynamic_quests={"dq_2": _active_quest()})
    manager = _make_manager()

    result = manager.apply_directive(
        "update_quest",
        {
            "quest_id": "dq_2",
            "next_steps": ["Find the key", "Open the chest"],
            "hints": ["The key is near water"],
        },
        context,
        current_tick=10,
    )

    assert result is True
    quest = context.state.quests.dynamic_quests["dq_2"]
    assert quest["next_steps"] == ["Find the key", "Open the chest"]
    assert quest["hints"] == ["The key is near water"]


def test_update_quest_sets_completed_objectives() -> None:
    """update_quest stores completed_objectives list correctly."""
    context = _make_context(dynamic_quests={"dq_3": _active_quest()})
    manager = _make_manager()

    result = manager.apply_directive(
        "update_quest",
        {
            "quest_id": "dq_3",
            "completed_objectives": ["Talked to the guard"],
        },
        context,
        current_tick=7,
    )

    assert result is True
    quest = context.state.quests.dynamic_quests["dq_3"]
    assert quest["completed_objectives"] == ["Talked to the guard"]


# ---------------------------------------------------------------------------
# Test: guard clauses
# ---------------------------------------------------------------------------


def test_update_quest_ignores_non_active() -> None:
    """update_quest returns False when quest status is not active."""
    context = _make_context(
        dynamic_quests={
            "dq_available": {"status": "available", "title": "Not started", "summary": "", "objectives": []},
        }
    )
    manager = _make_manager()

    result = manager.apply_directive(
        "update_quest",
        {"quest_id": "dq_available", "current_step": "Should not apply"},
        context,
        current_tick=1,
    )

    assert result is not True  # Returns a rejection reason string
    quest = context.state.quests.dynamic_quests["dq_available"]
    assert "current_step" not in quest


def test_update_quest_ignores_retired() -> None:
    """update_quest returns a rejection reason when quest status is retired."""
    context = _make_context(
        dynamic_quests={
            "dq_retired": {"status": "retired", "title": "Done", "summary": "", "objectives": []},
        }
    )
    manager = _make_manager()

    result = manager.apply_directive(
        "update_quest",
        {"quest_id": "dq_retired", "current_step": "Should not apply"},
        context,
        current_tick=1,
    )

    assert result is not True  # Returns a rejection reason string


def test_update_quest_ignores_unknown_quest() -> None:
    """update_quest returns a rejection reason when quest_id is not in dynamic_quests."""
    context = _make_context(dynamic_quests={})
    manager = _make_manager()

    result = manager.apply_directive(
        "update_quest",
        {"quest_id": "dq_nonexistent", "current_step": "Step 1"},
        context,
        current_tick=1,
    )

    assert result is not True  # Returns a rejection reason string


def test_update_quest_missing_quest_id_returns_false() -> None:
    """update_quest returns a rejection reason when quest_id is absent."""
    context = _make_context(dynamic_quests={"dq_x": _active_quest()})
    manager = _make_manager()

    result = manager.apply_directive(
        "update_quest",
        {"current_step": "No quest_id provided"},
        context,
        current_tick=1,
    )

    assert result is not True  # Returns a rejection reason string


# ---------------------------------------------------------------------------
# Test: incremental merge
# ---------------------------------------------------------------------------


def test_update_quest_incremental_merge() -> None:
    """Providing only current_step must not erase pre-existing hints."""
    quest_with_hints: dict[str, Any] = {
        **_active_quest(),
        "hints": ["Existing hint"],
        "next_steps": ["Existing step"],
    }
    context = _make_context(dynamic_quests={"dq_merge": quest_with_hints})
    manager = _make_manager()

    result = manager.apply_directive(
        "update_quest",
        {"quest_id": "dq_merge", "current_step": "New step only"},
        context,
        current_tick=3,
    )

    assert result is True
    quest = context.state.quests.dynamic_quests["dq_merge"]
    assert quest["current_step"] == "New step only"
    # Pre-existing hints and next_steps are preserved
    assert quest["hints"] == ["Existing hint"]
    assert quest["next_steps"] == ["Existing step"]


# ---------------------------------------------------------------------------
# Test: SSE notification
# ---------------------------------------------------------------------------


def test_update_quest_sse_emitted() -> None:
    """quest_progress_updated SSE is appended to sse_collector with correct payload."""
    sse_collector: list[SSEEvent] = []
    context = _make_context(dynamic_quests={"dq_sse": _active_quest()})
    manager = _make_manager(sse_collector=sse_collector)

    manager.apply_directive(
        "update_quest",
        {
            "quest_id": "dq_sse",
            "current_step": "Visit market",
            "next_steps": ["Buy supplies"],
            "hints": ["The market is open at dawn"],
        },
        context,
        current_tick=8,
    )

    assert len(sse_collector) == 1
    event = sse_collector[0]
    assert isinstance(event, SSEEvent)
    assert event.event_type == "quest_progress_updated"
    assert event.payload["quest_id"] == "dq_sse"
    assert event.payload["current_step"] == "Visit market"
    assert event.payload["next_steps"] == ["Buy supplies"]
    assert event.payload["hints"] == ["The market is open at dawn"]


def test_update_quest_no_sse_when_collector_none() -> None:
    """No SSE error when sse_collector is None (no collector attached)."""
    context = _make_context(dynamic_quests={"dq_no_sse": _active_quest()})
    manager = _make_manager(sse_collector=None)

    # Should not raise
    result = manager.apply_directive(
        "update_quest",
        {"quest_id": "dq_no_sse", "current_step": "Quiet step"},
        context,
        current_tick=2,
    )

    assert result is True


# ---------------------------------------------------------------------------
# Test: quest_history logging
# ---------------------------------------------------------------------------


def test_update_quest_logged_to_quest_history() -> None:
    """update_quest appends an update_quest entry to narrative_plan.quest_history."""
    context = _make_context(dynamic_quests={"dq_hist": _active_quest()})
    manager = _make_manager()

    manager.apply_directive(
        "update_quest",
        {"quest_id": "dq_hist", "current_step": "Check logs"},
        context,
        current_tick=12,
    )

    history = context.state.narrative_plan.quest_history
    matching = [h for h in history if h.get("kind") == "update_quest" and h.get("quest_id") == "dq_hist"]
    assert len(matching) == 1
    assert matching[0]["tick"] == 12


def test_update_quest_dirty_flag_set() -> None:
    """update_quest marks quests slice as dirty after modification."""
    context = _make_context(dynamic_quests={"dq_dirty": _active_quest()})
    manager = _make_manager()

    # Reset dirty flag to confirm update_quest sets it
    context.state.quests._dirty = False

    manager.apply_directive(
        "update_quest",
        {"quest_id": "dq_dirty", "current_step": "Step"},
        context,
        current_tick=1,
    )

    assert context.state.quests._dirty is True
