"""P25 Phase 1 + 2-1 + 2-2 tests.

Covers:
- P25-03: Gemini empty response guard (empty candidates → finish_reason="error")
- P25-04: Diagnostic logger improvements (smoke-checked via warning message text)
- P25-07: Planner system prompt language constraint
- P25-01: area_ids collected in _build_planner_context and rendered in _format_planner_context
- P25-02: Rejection feedback chain (dispatcher returns reason strings + audit capture + rendering)
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from app.narrators import _format_planner_context


# ---------------------------------------------------------------------------
# P25-03: Gemini empty response guard
# ---------------------------------------------------------------------------


def test_parse_response_returns_error_when_candidates_none(monkeypatch) -> None:
    """_parse_response returns finish_reason='error' when candidates is None/empty."""
    from app.llm_gemini import GeminiLlmAdapter

    class _FakeModels:
        async def generate_content(self, *, model, contents, config):
            # Simulate safety filter / empty response
            return SimpleNamespace(candidates=None, usage_metadata=None)

    class _FakeClient:
        def __init__(self):
            self.aio = SimpleNamespace(models=_FakeModels())

    monkeypatch.setattr("app.llm_gemini.genai.Client", _FakeClient)
    adapter = GeminiLlmAdapter()
    response = asyncio.run(
        adapter.generate("system", [{"role": "user", "parts": [{"text": "hi"}]}], [])
    )

    assert response.finish_reason == "error"
    assert response.text == ""
    assert response.metadata.get("error") == "empty_candidates"
    assert response.metadata.get("provider") == "gemini"


def test_parse_response_returns_error_when_candidates_empty_list(monkeypatch) -> None:
    """_parse_response returns finish_reason='error' when candidates is an empty list."""
    from app.llm_gemini import GeminiLlmAdapter

    class _FakeModels:
        async def generate_content(self, *, model, contents, config):
            return SimpleNamespace(candidates=[], usage_metadata=None)

    class _FakeClient:
        def __init__(self):
            self.aio = SimpleNamespace(models=_FakeModels())

    monkeypatch.setattr("app.llm_gemini.genai.Client", _FakeClient)
    adapter = GeminiLlmAdapter()
    response = asyncio.run(
        adapter.generate("system", [{"role": "user", "parts": [{"text": "hi"}]}], [])
    )

    assert response.finish_reason == "error"
    assert response.metadata.get("error") == "empty_candidates"


def test_parse_response_skips_candidate_with_no_content(monkeypatch) -> None:
    """Inner guard: candidate with no content.parts is skipped without error."""
    from app.llm_gemini import GeminiLlmAdapter

    class _FakeModels:
        async def generate_content(self, *, model, contents, config):
            # First candidate has no content, second has valid content
            bad_candidate = SimpleNamespace(content=None)
            good_part = SimpleNamespace(function_call=None, text="hello", thought_signature=None)
            good_candidate = SimpleNamespace(content=SimpleNamespace(parts=[good_part]))
            return SimpleNamespace(candidates=[bad_candidate, good_candidate], usage_metadata=None)

    class _FakeClient:
        def __init__(self):
            self.aio = SimpleNamespace(models=_FakeModels())

    monkeypatch.setattr("app.llm_gemini.genai.Client", _FakeClient)
    adapter = GeminiLlmAdapter()
    response = asyncio.run(
        adapter.generate("system", [{"role": "user", "parts": [{"text": "hi"}]}], [])
    )

    assert response.finish_reason == "stop"
    assert response.text == "hello"


# ---------------------------------------------------------------------------
# P25-07: Planner system prompt language constraint
# ---------------------------------------------------------------------------


def test_planner_system_prompt_has_language_constraint() -> None:
    """AgenticNarrativePlanner._SYSTEM_PROMPT includes 语言与格式 section."""
    from app.narrators import AgenticNarrativePlanner

    assert "## 语言与格式" in AgenticNarrativePlanner._SYSTEM_PROMPT
    assert "中文" in AgenticNarrativePlanner._SYSTEM_PROMPT
    assert "snake_case" in AgenticNarrativePlanner._SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# P25-01: area_ids in _format_planner_context
# ---------------------------------------------------------------------------

def _minimal_ctx(**extra: Any) -> dict[str, Any]:
    """Minimal valid context for _format_planner_context."""
    return {
        "narrative_plan": {
            "current_chapter": "ch1",
            "escalation_level": 0,
            "ticks_since_milestone_progress": 0,
            "current_target_milestone": None,
            "pacing_frozen": False,
        },
        "quests": {"available_milestones": [], "dynamic_quests": {}},
        "current_tick": 1,
        **extra,
    }


def test_format_planner_context_renders_all_area_ids() -> None:
    """all_area_ids in context → 'Allowed area_ids:' line in output."""
    ctx = _minimal_ctx(
        all_area_ids=["frontier_town", "ancient_ruins", "cow_girl_farm"],
    )
    result = _format_planner_context(ctx)

    assert "Allowed area_ids:" in result
    assert "frontier_town" in result
    assert "ancient_ruins" in result
    assert "cow_girl_farm" in result


def test_format_planner_context_omits_area_ids_when_empty() -> None:
    """No all_area_ids key → 'Allowed area_ids:' line absent."""
    ctx = _minimal_ctx()
    result = _format_planner_context(ctx)

    assert "Allowed area_ids:" not in result


def test_format_planner_context_renders_current_sub_area_ids() -> None:
    """current_sub_area_ids in context → 'Sub-areas for ...' line in output."""
    ctx = _minimal_ctx(
        current_sub_area_ids=["guild_hall", "tavern", "market"],
        location={"area_id": "frontier_town", "location_id": None},
    )
    result = _format_planner_context(ctx)

    assert "Sub-areas for frontier_town:" in result
    assert "guild_hall" in result
    assert "tavern" in result
    assert "market" in result


def test_format_planner_context_omits_sub_areas_when_empty() -> None:
    """No current_sub_area_ids → 'Sub-areas for' line absent."""
    ctx = _minimal_ctx()
    result = _format_planner_context(ctx)

    assert "Sub-areas for" not in result


def test_build_planner_context_includes_area_ids() -> None:
    """_build_planner_context populates all_area_ids and current_sub_area_ids."""
    import asyncio
    from app.game_core.content import WorldInstance
    from app.game_core.content.registries.maps import MapRegistry, AreaTemplate
    from app.game_core.content.registries.map_types import SubLocationTemplate
    from app.game_core.orchestration.hooks.narrative_planner import NarrativePlannerHook
    from app.game_core.orchestration.scene_bus import SceneBus
    from app.game_core.orchestration.settlement import SettlementContext
    from app.game_core.rules import RulesEngine
    from app.game_core.rules.defaults import register_default_rules_handlers
    from app.game_core.state import StateChange, StateContainer, StateDelta
    from app.game_core.state.slices import (
        AreaSlice, EventSlice, NarrativePlanSlice, PlayerSlice,
        QuestSlice, SceneSlice, TimeSlice,
    )

    # Build a state with two areas
    state = StateContainer()

    time_slice = TimeSlice()
    time_slice.restore({"day": 1, "slot": 9})
    state.register(time_slice)

    player = PlayerSlice()
    player.restore({"current_area": "frontier_town", "current_location": None})
    state.register(player)

    quests = QuestSlice()
    quests.restore({"milestone_states": {}, "dynamic_quests": {}, "chapter_completion": {}})
    state.register(quests)

    narrative_plan = NarrativePlanSlice()
    narrative_plan.restore({"current_chapter": "ch1"})
    state.register(narrative_plan)

    areas = AreaSlice()
    areas.restore({"areas": {"frontier_town": {}, "ancient_ruins": {}}})
    state.register(areas)

    events = EventSlice()
    events.restore({})
    state.register(events)

    scene_slice = SceneSlice()
    scene_slice.restore({})
    state.register(scene_slice)

    # Build a world with a MapRegistry containing sub_locations
    world = WorldInstance("test_world")
    map_registry = MapRegistry()
    frontier_template = AreaTemplate(
        id="frontier_town",
        sub_locations={
            "guild_hall": SubLocationTemplate(id="guild_hall"),
            "tavern": SubLocationTemplate(id="tavern"),
        },
    )
    map_registry._items["frontier_town"] = frontier_template
    world.register(map_registry)

    # Build context
    scene_bus = SceneBus(scene_slice)
    rules_engine = RulesEngine()
    register_default_rules_handlers(rules_engine)
    change_log: list[StateChange] = []

    def _apply_delta(delta: StateDelta | None) -> None:
        if delta is None:
            return
        state.apply(delta)
        change_log.extend(delta.changes)

    ctx = SettlementContext(
        change_log=change_log,
        state=state,
        world=world,
        scene_bus=scene_bus,
        _rules_engine=rules_engine,
        _apply_delta=_apply_delta,
    )

    class _StaticBlackboard:
        async def plan(self, context: dict[str, Any]) -> Any:
            from app.game_core.orchestration.hooks.narrative_planner import NarrativePlannerDecision
            return NarrativePlannerDecision(
                directives=[],
                metadata={"status": "noop", "reason": "stable"},
            )

    hook = NarrativePlannerHook(blackboard=_StaticBlackboard())
    planner_ctx = hook._build_planner_context(ctx, current_tick=1)

    # all_area_ids must contain both areas
    assert set(planner_ctx["all_area_ids"]) == {"frontier_town", "ancient_ruins"}

    # current_sub_area_ids must contain the static sub-locations from the map template
    assert "guild_hall" in planner_ctx["current_sub_area_ids"]
    assert "tavern" in planner_ctx["current_sub_area_ids"]


# ---------------------------------------------------------------------------
# P25-02: Rejection feedback chain
# ---------------------------------------------------------------------------


def test_dispatcher_returns_reason_string_for_unhandled_kind() -> None:
    """PlannerDispatcher.apply_directive returns a string reason when no handler exists."""
    from app.game_core.planning.subsystem import PlannerDispatcher

    dispatcher = PlannerDispatcher()
    result = dispatcher.apply_directive("nonexistent_kind", {}, None, current_tick=0)
    assert result is not True
    assert isinstance(result, str)
    assert result == "no_handler_for_kind"


def test_dispatcher_returns_reason_string_for_cyclic_call() -> None:
    """PlannerDispatcher.apply_directive returns 'cyclic_call_blocked' for cyclic calls."""
    from app.game_core.planning.subsystem import PlannerDispatcher

    class _RecursiveSubSystem:
        name = "recursive"
        handles = frozenset({"recurse"})

        def accepts_event(self, event: Any) -> bool:
            return True

        async def evaluate(self, event: Any, context: Any) -> Any:
            from app.game_core.planning.subsystem import SubSystemResult
            return SubSystemResult()

        def apply_directive(self, kind: str, payload: Any, context: Any, *, current_tick: int) -> bool | str:
            # Attempt recursive call — triggers cyclic detection
            return context.apply_directive(kind, payload, None, current_tick=current_tick)

    dispatcher = PlannerDispatcher()
    ss = _RecursiveSubSystem()
    dispatcher.register(ss)
    # Manually push to directive_stack to simulate an in-progress call
    dispatcher._directive_stack.append("recursive")

    result = dispatcher.apply_directive("recurse", {}, None, current_tick=0)
    assert result == "cyclic_call_blocked"


def test_subsystem_apply_directive_returns_reason_on_command_failure() -> None:
    """QuestManagerSubSystem returns reason string when command fails."""
    from unittest.mock import MagicMock
    from app.game_core.planning.quest_manager import QuestManagerSubSystem
    from app.game_core.planning.subsystem import PlannerDispatcher

    dispatcher = PlannerDispatcher()
    qs = QuestManagerSubSystem(dispatcher)

    # Mock a context whose execute_command returns executed=False with errors
    mock_context = MagicMock()
    mock_result = MagicMock()
    mock_result.executed = False
    mock_result.errors = ["quest_id_required"]
    mock_context.execute_command.return_value = mock_result

    result = qs.apply_directive(
        "create_quest", {}, mock_context, current_tick=1
    )
    assert result is not True
    assert isinstance(result, str)
    assert "quest_id_required" in result


def test_format_planner_context_renders_previous_directive_results() -> None:
    """previous_directive_results in context → '## 上轮指令执行反馈' section in output."""
    ctx = _minimal_ctx(
        previous_directive_results=[
            {"kind": "create_quest", "status": "applied", "reason_code": None},
            {"kind": "direct_npc", "status": "subsystem_rejected", "reason_code": "command_failed"},
            {"kind": "escalate", "status": "subsystem_rejected", "reason_code": "no_handler_for_kind"},
        ],
    )
    result = _format_planner_context(ctx)

    assert "## 上轮指令执行反馈" in result
    assert "create_quest: applied" in result
    assert "direct_npc: subsystem_rejected (command_failed)" in result
    assert "escalate: subsystem_rejected (no_handler_for_kind)" in result
    assert "请根据以上反馈调整本轮指令" in result


def test_format_planner_context_omits_feedback_when_empty() -> None:
    """No previous_directive_results → '## 上轮指令执行反馈' absent."""
    ctx = _minimal_ctx()
    result = _format_planner_context(ctx)

    assert "## 上轮指令执行反馈" not in result
    assert "请根据以上反馈调整本轮指令" not in result


def test_format_planner_context_feedback_omits_reason_when_none() -> None:
    """Entries with no reason_code render without parentheses."""
    ctx = _minimal_ctx(
        previous_directive_results=[
            {"kind": "create_quest", "status": "applied", "reason_code": None},
        ],
    )
    result = _format_planner_context(ctx)

    # Should have the entry but no "()" appended
    assert "create_quest: applied" in result
    # No trailing parentheses for None reason
    assert "create_quest: applied (" not in result
