"""Tests for AgenticNarrativePlanner (O-3).

Verifies:
1. LLM returns valid JSON with directives → plan is returned as-is
2. LLM returns invalid JSON → fallback to deterministic NarrativePlanner
3. LLM returns JSON missing 'directives' key → fallback
4. NarrativePlanner.plan() is now async and behaves identically
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from app.game_core.planning.planner import NarrativePlanner
from app.narrators import AgenticNarrativePlanner


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_llm_response(text: str) -> Any:
    resp = MagicMock()
    resp.text = text
    resp.tool_calls = []
    return resp


def _make_llm(text: str) -> Any:
    llm = MagicMock()
    llm.generate = AsyncMock(return_value=_make_llm_response(text))
    return llm


_MINIMAL_CONTEXT: dict[str, Any] = {
    "narrative_plan": {
        "current_chapter": "ch1",
        "escalation_level": 0,
        "ticks_since_milestone_progress": 0,
        "current_target_milestone": None,
        "pacing_frozen": False,
    },
    "quests": {
        "available_milestones": [],
        "active_milestones": [],
        "dynamic_quests": {},
    },
    "current_tick": 5,
}


# ------------------------------------------------------------------
# Tests: AgenticNarrativePlanner
# ------------------------------------------------------------------


def test_valid_json_with_directives_returned() -> None:
    """LLM returns valid JSON with 'directives' → use LLM plan."""
    plan_json = json.dumps({
        "strategy_notes": "escalate mildly",
        "directives": [{"kind": "escalate", "payload": {"delta": 1}}],
    })
    llm = _make_llm(plan_json)
    planner = AgenticNarrativePlanner(llm=llm)

    result = asyncio.run(planner.plan(_MINIMAL_CONTEXT))

    assert "directives" in result
    assert result["directives"][0]["kind"] == "escalate"
    assert result.get("strategy_notes") == "escalate mildly"


def test_invalid_json_falls_back_to_deterministic() -> None:
    """LLM returns non-JSON text → fallback to NarrativePlanner output."""
    llm = _make_llm("Sorry, I cannot help with that.")
    planner = AgenticNarrativePlanner(llm=llm)

    result = asyncio.run(planner.plan(_MINIMAL_CONTEXT))

    # Deterministic planner always returns dict with directives + metadata
    assert isinstance(result, dict)
    assert "directives" in result
    assert "metadata" in result
    provider = result["metadata"].get("provider", "")
    assert provider == "default_planner"


def test_json_missing_directives_key_falls_back() -> None:
    """LLM returns valid JSON but without 'directives' key → fallback."""
    plan_json = json.dumps({"strategy_notes": "hmm", "actions": []})
    llm = _make_llm(plan_json)
    planner = AgenticNarrativePlanner(llm=llm)

    result = asyncio.run(planner.plan(_MINIMAL_CONTEXT))

    # Must come from fallback (has metadata.provider)
    assert "metadata" in result
    assert result["metadata"].get("provider") == "default_planner"


# ------------------------------------------------------------------
# Tests: NarrativePlanner.plan() async
# ------------------------------------------------------------------


def test_deterministic_planner_is_now_async() -> None:
    """NarrativePlanner.plan() is async and returns the same structure as before."""
    planner = NarrativePlanner()
    context = {
        "quests": {
            "available_milestones": [],
            "active_milestones": [],
            "dynamic_quests": {},
        },
        "narrative_plan": {
            "escalation_level": 0,
            "ticks_since_milestone_progress": 0,
            "pacing_frozen": False,
        },
        "current_tick": 1,
    }

    result = asyncio.run(planner.plan(context))

    assert isinstance(result, dict)
    assert "directives" in result
    assert "metadata" in result
    assert result["metadata"]["provider"] == "default_planner"
