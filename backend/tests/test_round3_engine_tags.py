"""Tests for Phase 0: SceneBus ENGINE tag injection (_emit_action_tags)."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from app.game_core.content import WorldInstance
from app.game_core.orchestration.models import PipelineResult
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.tick_coordinator import TickCoordinator, _SEMANTIC_TAGS
from app.game_core.rules import RulesEngine
from app.game_core.state import StateContainer
from app.game_core.state.slices import SceneSlice


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_coordinator() -> tuple[TickCoordinator, SceneBus]:
    state = StateContainer()
    scene_slice = SceneSlice()
    scene_slice.restore({})
    state.register(scene_slice)

    scene_bus = SceneBus(scene_slice)
    coordinator = TickCoordinator(
        world=WorldInstance("test"),
        state=state,
        rules_engine=RulesEngine(),
        scene_bus=scene_bus,
    )
    return coordinator, scene_bus


def _make_result(action_type: str, narrative_hints: list[str] | None = None) -> PipelineResult:
    return PipelineResult(
        executed=True,
        action_type=action_type,
        narrative_hints=narrative_hints or [],
    )


def _engine_entries(scene_bus: SceneBus) -> list[dict]:
    return [
        e for e in scene_bus.snapshot().get("entries", [])
        if isinstance(e, dict) and e.get("source") == "ENGINE"
    ]


# ------------------------------------------------------------------
# _SEMANTIC_TAGS constant
# ------------------------------------------------------------------


def test_semantic_tags_contains_expected_keys() -> None:
    assert "rest_long" in _SEMANTIC_TAGS
    assert "end_combat" in _SEMANTIC_TAGS
    assert "skill_check" in _SEMANTIC_TAGS
    assert "advance_quest" in _SEMANTIC_TAGS


def test_semantic_tags_rest_long_value() -> None:
    assert set(_SEMANTIC_TAGS["rest_long"]) == {"REST", "LONG_REST"}


def test_semantic_tags_end_combat_value() -> None:
    assert "COMBAT_END" in _SEMANTIC_TAGS["end_combat"]


# ------------------------------------------------------------------
# _emit_action_tags: known action types
# ------------------------------------------------------------------


def test_emit_rest_long_writes_entry() -> None:
    coordinator, scene_bus = _make_coordinator()
    coordinator._emit_action_tags(_make_result("rest_long"))
    entries = _engine_entries(scene_bus)
    assert len(entries) == 1
    assert "REST" in entries[0]["tags"]
    assert "LONG_REST" in entries[0]["tags"]


def test_emit_skill_check_writes_skill_check_tag() -> None:
    coordinator, scene_bus = _make_coordinator()
    coordinator._emit_action_tags(_make_result("skill_check"))
    entries = _engine_entries(scene_bus)
    assert any("SKILL_CHECK" in e.get("tags", []) for e in entries)


def test_emit_advance_quest_writes_quest_progress_tag() -> None:
    coordinator, scene_bus = _make_coordinator()
    coordinator._emit_action_tags(_make_result("advance_quest"))
    entries = _engine_entries(scene_bus)
    assert any("QUEST_PROGRESS" in e.get("tags", []) for e in entries)


def test_emit_end_combat_writes_combat_end_tag() -> None:
    coordinator, scene_bus = _make_coordinator()
    coordinator._emit_action_tags(_make_result("end_combat"))
    entries = _engine_entries(scene_bus)
    assert any("COMBAT_END" in e.get("tags", []) for e in entries)


# ------------------------------------------------------------------
# _emit_action_tags: unknown / noop action types
# ------------------------------------------------------------------


def test_emit_noop_writes_nothing() -> None:
    coordinator, scene_bus = _make_coordinator()
    coordinator._emit_action_tags(_make_result("noop"))
    assert len(_engine_entries(scene_bus)) == 0


def test_emit_unknown_type_writes_nothing() -> None:
    coordinator, scene_bus = _make_coordinator()
    coordinator._emit_action_tags(_make_result("totally_unknown_action"))
    assert len(_engine_entries(scene_bus)) == 0


# ------------------------------------------------------------------
# _emit_action_tags: content with narrative_hints
# ------------------------------------------------------------------


def test_emit_with_narrative_hints_includes_hint_in_content() -> None:
    coordinator, scene_bus = _make_coordinator()
    coordinator._emit_action_tags(_make_result("rest_long", narrative_hints=["The party rests by the fire."]))
    entries = _engine_entries(scene_bus)
    assert len(entries) == 1
    assert "The party rests by the fire." in entries[0]["content"]


def test_emit_without_narrative_hints_content_is_bracketed_type() -> None:
    coordinator, scene_bus = _make_coordinator()
    coordinator._emit_action_tags(_make_result("skill_check"))
    entries = _engine_entries(scene_bus)
    assert entries[0]["content"] == "[skill_check]"


# ------------------------------------------------------------------
# ENGINE entry visibility = "system"
# ------------------------------------------------------------------


def test_emit_entry_visibility_is_system() -> None:
    coordinator, scene_bus = _make_coordinator()
    coordinator._emit_action_tags(_make_result("navigate"))
    entries = _engine_entries(scene_bus)
    assert entries[0]["visibility"] == "system"
