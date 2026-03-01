"""Tests for SuggestOptionsTool DC support (N-6)."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

from app.game_core.narrative.gm_tools import SuggestOptionsTool
from app.game_core.narrative.tools import AgentContext, ToolResult
from app.game_core.content import WorldInstance
from app.game_core.state import StateContainer
from app.game_core.narrative.models import AgentResult


def _context() -> AgentContext:
    return AgentContext(
        role="gm",
        world=WorldInstance("test_world"),
        state=StateContainer(),
    )


def _tool() -> SuggestOptionsTool:
    return SuggestOptionsTool()


# ------------------------------------------------------------------
# SuggestOptionsTool schema + execute
# ------------------------------------------------------------------


def test_execute_returns_tool_identifier_in_metadata() -> None:
    """execute() metadata must contain 'tool': 'suggest_options' for N-6 extraction."""
    tool = _tool()
    params = {"options": [{"text": "Talk", "action": "talk"}]}
    result = asyncio.run(tool.execute(params, _context()))
    assert result.success is True
    assert result.metadata.get("tool") == "suggest_options"
    assert result.metadata.get("options") == [{"text": "Talk", "action": "talk"}]


def test_validate_option_copies_dc() -> None:
    """_validate_option() should include dc when provided as an int."""
    opt = {"text": "Persuade", "check": {"skill": "persuasion", "dc": 15}}
    result = SuggestOptionsTool._validate_option(opt)
    assert result is not None
    assert result["check"]["skill"] == "persuasion"
    assert result["check"]["dc"] == 15


def test_validate_option_no_dc_is_fine() -> None:
    """_validate_option() without dc should still work (dc is optional)."""
    opt = {"text": "Intimidate", "check": {"skill": "intimidation"}}
    result = SuggestOptionsTool._validate_option(opt)
    assert result is not None
    assert result["check"]["skill"] == "intimidation"
    assert "dc" not in result["check"]


def test_validate_option_dc_non_int_ignored() -> None:
    """_validate_option() should ignore non-int dc values."""
    opt = {"text": "Deceive", "check": {"skill": "deception", "dc": "hard"}}
    result = SuggestOptionsTool._validate_option(opt)
    assert result is not None
    assert "dc" not in result["check"]


# ------------------------------------------------------------------
# npc_interaction Step 5 extraction (unit-level)
# ------------------------------------------------------------------


def _make_gm_result_with_suggest_options(options: list[dict[str, Any]]) -> Any:
    """Build a minimal AgentResult mock containing a suggest_options tool result."""
    tr = MagicMock()
    tr.success = True
    tr.metadata = {"tool": "suggest_options", "options": options}
    result = MagicMock()
    result.tool_results = [tr]
    return result


def _make_gm_result_without_suggest_options() -> Any:
    tr = MagicMock()
    tr.success = True
    tr.metadata = {"tool": "narrate", "text": "The air grows cold."}
    result = MagicMock()
    result.tool_results = [tr]
    return result


def _extract_lm_options(gm_result: Any) -> list[dict[str, Any]] | None:
    """Mirror the extraction logic from npc_interaction.py Step 5."""
    if gm_result is None:
        return None
    for _tr in gm_result.tool_results:
        if (
            _tr.success
            and isinstance(_tr.metadata, dict)
            and _tr.metadata.get("tool") == "suggest_options"
        ):
            _opts = _tr.metadata.get("options")
            if isinstance(_opts, list) and _opts:
                return _opts
    return None


def test_step5_uses_lm_options_when_gm_called_suggest_options() -> None:
    """Step 5 should use LLM options (with dc) when GM called suggest_options."""
    lm_opts = [
        {"text": "Persuade", "check": {"skill": "persuasion", "dc": 12}},
        {"text": "Leave", "action": "farewell"},
    ]
    gm_result = _make_gm_result_with_suggest_options(lm_opts)
    extracted = _extract_lm_options(gm_result)
    assert extracted == lm_opts


def test_step5_falls_back_when_gm_result_is_none() -> None:
    """Step 5 should return None (→ static fallback) when gm_result is None."""
    assert _extract_lm_options(None) is None


def test_step5_falls_back_when_gm_did_not_call_suggest_options() -> None:
    """Step 5 should return None when GM only called narrate/pass_turn."""
    gm_result = _make_gm_result_without_suggest_options()
    assert _extract_lm_options(gm_result) is None
