"""Tests for P28 Wave 1 — Planner基础修复 (3-1 to 3-4).

Covers:
- P25-01 (3-1): _format_planner_context renders Escalate delta range
- P25-02 (3-2): _format_planner_context renders 上轮指令执行反馈 (already existed, verified)
- QF-2  (3-3): AgenticNarrativePlanner._SYSTEM_PROMPT contains Chinese language constraint
- QF-5  (3-4): _build_capability_boundary_prompt generates 你可以/你不可以 sections
"""
from __future__ import annotations

from typing import Any

from app.narrators import AgenticNarrativePlanner, _format_planner_context
from app.game_core.orchestration.npc_interaction import _build_capability_boundary_prompt
from app.game_core.narrative.executor import AgenticExecutor
from app.game_core.narrative.registry import RoleToolRegistry


# ── Helpers ───────────────────────────────────────────────────────────────────


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


def _make_executor_with_tools(tool_classes: list) -> AgenticExecutor:
    """Build a bare AgenticExecutor with the given tool classes registered."""
    registry = RoleToolRegistry()
    for tool_cls in tool_classes:
        registry.register(tool_cls())
    return AgenticExecutor(tool_registry=registry, llm=None)  # type: ignore[arg-type]


# ── P25-01: Escalate delta range ──────────────────────────────────────────────


class TestEscalateDeltaRange:
    def test_escalate_delta_range_always_rendered(self) -> None:
        """_format_planner_context always renders 'Escalate delta range' line."""
        ctx = _minimal_ctx()
        result = _format_planner_context(ctx)
        assert "Escalate delta range" in result, (
            f"Expected 'Escalate delta range' in output, got:\n{result}"
        )

    def test_escalate_delta_range_shows_correct_bounds(self) -> None:
        """The delta range line must mention -3 and 3."""
        ctx = _minimal_ctx()
        result = _format_planner_context(ctx)
        lines = [l for l in result.splitlines() if "Escalate delta range" in l]
        assert lines, "No 'Escalate delta range' line found"
        delta_line = lines[0]
        assert "-3" in delta_line, f"Expected -3 in delta range line: {delta_line}"
        assert "3" in delta_line, f"Expected 3 in delta range line: {delta_line}"

    def test_escalate_delta_range_present_even_without_area_ids(self) -> None:
        """Escalate delta range appears even when all_area_ids is absent."""
        ctx = _minimal_ctx()
        assert "all_area_ids" not in ctx
        result = _format_planner_context(ctx)
        assert "Escalate delta range" in result


# ── P25-02: 上轮指令执行反馈 ─────────────────────────────────────────────────


class TestPreviousDirectiveResults:
    def test_previous_directive_results_rendered(self) -> None:
        """previous_directive_results in context → '上轮指令执行反馈' section rendered."""
        ctx = _minimal_ctx(
            previous_directive_results=[
                {"kind": "escalate", "status": "applied", "reason_code": None},
                {"kind": "create_quest", "status": "subsystem_rejected", "reason_code": "quest_id_conflict"},
            ],
        )
        result = _format_planner_context(ctx)
        assert "上轮指令执行反馈" in result, (
            f"Expected '上轮指令执行反馈' section in output"
        )
        assert "escalate" in result
        assert "applied" in result
        assert "create_quest" in result
        assert "quest_id_conflict" in result

    def test_previous_directive_results_omitted_when_empty(self) -> None:
        """Empty previous_directive_results → no feedback section."""
        ctx = _minimal_ctx(previous_directive_results=[])
        result = _format_planner_context(ctx)
        assert "上轮指令执行反馈" not in result

    def test_previous_directive_results_omitted_when_absent(self) -> None:
        """Missing previous_directive_results key → no feedback section."""
        ctx = _minimal_ctx()
        result = _format_planner_context(ctx)
        assert "上轮指令执行反馈" not in result


# ── QF-2: Chinese language constraint in _SYSTEM_PROMPT ─────────────────────


class TestSystemPromptLanguageConstraint:
    def test_system_prompt_contains_language_rule_in_rules_section(self) -> None:
        """_SYSTEM_PROMPT ## 规则 section must include Chinese language constraint (QF-2)."""
        prompt = AgenticNarrativePlanner._SYSTEM_PROMPT
        assert "语言规则" in prompt, (
            "Expected '语言规则' entry in _SYSTEM_PROMPT ## 规则 section"
        )

    def test_system_prompt_mentions_required_fields(self) -> None:
        """_SYSTEM_PROMPT language rule must mention title, summary, description."""
        prompt = AgenticNarrativePlanner._SYSTEM_PROMPT
        # Check the language rule mentions key fields
        lang_section = prompt[prompt.find("语言规则"):][:200]
        assert "title" in lang_section, f"Expected 'title' in language rule: {lang_section}"
        assert "summary" in lang_section, f"Expected 'summary' in language rule: {lang_section}"
        assert "中文" in lang_section, f"Expected '中文' in language rule: {lang_section}"

    def test_system_prompt_rules_section_has_language_entry(self) -> None:
        """The ## 规则 section specifically has the language rule as item 8."""
        prompt = AgenticNarrativePlanner._SYSTEM_PROMPT
        # Find ## 规则 section
        rules_start = prompt.find("## 规则")
        assert rules_start != -1, "## 规则 section not found"
        # Find end of rules section (next ## heading)
        next_section = prompt.find("##", rules_start + 5)
        rules_text = prompt[rules_start:next_section] if next_section != -1 else prompt[rules_start:]
        assert "语言規則" in rules_text or "语言规则" in rules_text, (
            f"Expected language rule in ## 规则 section:\n{rules_text}"
        )


# ── QF-5: NPC capability boundary prompt ─────────────────────────────────────


class TestCapabilityBoundaryPrompt:
    def test_basic_npc_has_can_do_section(self) -> None:
        """_build_capability_boundary_prompt always includes '你可以' section."""
        from app.game_core.narrative.character_tools import RecallTool, SpeakTool, EmoteTool
        executor = _make_executor_with_tools([SpeakTool, EmoteTool, RecallTool])
        result = _build_capability_boundary_prompt(executor, [])
        assert "你可以" in result, f"Expected '你可以' in prompt:\n{result}"

    def test_basic_npc_has_cannot_do_section(self) -> None:
        """Basic NPC without merchant/receptionist traits has '你不可以' section."""
        from app.game_core.narrative.character_tools import SpeakTool, EmoteTool
        executor = _make_executor_with_tools([SpeakTool, EmoteTool])
        result = _build_capability_boundary_prompt(executor, [])
        assert "你不可以" in result, f"Expected '你不可以' in prompt:\n{result}"

    def test_merchant_npc_can_trade(self) -> None:
        """NPC with 'merchant' tag and OfferTradeTool → '展示商品和交易' in can_do."""
        from app.game_core.narrative.character_tools import (
            SpeakTool, EmoteTool, RecallTool, OfferTradeTool,
        )
        executor = _make_executor_with_tools([SpeakTool, EmoteTool, RecallTool, OfferTradeTool])
        result = _build_capability_boundary_prompt(executor, ["merchant"])
        assert "展示商品和交易" in result, (
            f"Expected merchant trade capability in prompt:\n{result}"
        )

    def test_receptionist_npc_can_offer_quest(self) -> None:
        """NPC with 'receptionist' tag and OfferQuestTool → '发布和介绍任务' in can_do."""
        from app.game_core.narrative.character_tools import (
            SpeakTool, EmoteTool, RecallTool, OfferQuestTool,
        )
        executor = _make_executor_with_tools([SpeakTool, EmoteTool, RecallTool, OfferQuestTool])
        result = _build_capability_boundary_prompt(executor, ["receptionist"])
        assert "发布和介绍任务" in result, (
            f"Expected quest offer capability in prompt:\n{result}"
        )

    def test_capability_prompt_includes_guidance_line(self) -> None:
        """Capability boundary prompt ends with player-guidance instruction."""
        from app.game_core.narrative.character_tools import SpeakTool
        executor = _make_executor_with_tools([SpeakTool])
        result = _build_capability_boundary_prompt(executor, [])
        assert "合适的人" in result, (
            f"Expected guidance about directing player to others:\n{result}"
        )

    def test_capability_prompt_section_header_present(self) -> None:
        """Capability boundary prompt starts with ## 你当前的能力."""
        from app.game_core.narrative.character_tools import SpeakTool
        executor = _make_executor_with_tools([SpeakTool])
        result = _build_capability_boundary_prompt(executor, [])
        assert "你当前的能力" in result, (
            f"Expected section header '你当前的能力' in:\n{result}"
        )

    def test_empty_registry_still_returns_base_capabilities(self) -> None:
        """Even with empty tool registry, base capabilities (说话等) are listed."""
        executor = AgenticExecutor(tool_registry=RoleToolRegistry(), llm=None)  # type: ignore[arg-type]
        result = _build_capability_boundary_prompt(executor, [])
        assert "说话" in result, f"Expected '说话' in base capabilities:\n{result}"
