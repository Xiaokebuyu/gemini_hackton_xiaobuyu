"""Tests for AgenticNarrativePlanner."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from app.narrators import AgenticNarrativePlanner, _format_planner_context


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
        "story_facts": [{"subject": "guild", "relation": "warns_about", "object": "raiders"}],
    })
    llm = _make_llm(plan_json)
    planner = AgenticNarrativePlanner(llm=llm)

    result = asyncio.run(planner.plan(_MINIMAL_CONTEXT))

    assert "directives" in result
    assert result["directives"][0]["kind"] == "escalate"
    assert result.get("strategy_notes") == "escalate mildly"
    assert result.get("story_facts") == [
        {"subject": "guild", "relation": "warns_about", "object": "raiders"},
    ]


def test_invalid_json_returns_llm_noop() -> None:
    """LLM returns non-JSON text → planner returns noop payload."""
    llm = _make_llm("Sorry, I cannot help with that.")
    planner = AgenticNarrativePlanner(llm=llm)

    result = asyncio.run(planner.plan(_MINIMAL_CONTEXT))

    assert isinstance(result, dict)
    assert "directives" in result
    assert "metadata" in result
    provider = result["metadata"].get("provider", "")
    assert provider == "llm_planner"
    assert result["metadata"].get("reason") == "parse_failed"
    assert result["story_facts"] == []


def test_json_missing_directives_key_returns_llm_noop() -> None:
    """LLM returns valid JSON but without 'directives' key → planner returns noop."""
    plan_json = json.dumps({"strategy_notes": "hmm", "actions": []})
    llm = _make_llm(plan_json)
    planner = AgenticNarrativePlanner(llm=llm)

    result = asyncio.run(planner.plan(_MINIMAL_CONTEXT))

    assert "metadata" in result
    assert result["metadata"].get("provider") == "llm_planner"
    assert result["story_facts"] == []


def test_format_planner_context_includes_party_and_area_context() -> None:
    ctx = {
        "narrative_plan": {
            "current_chapter": "ch1",
            "escalation_level": 2,
            "ticks_since_milestone_progress": 5,
            "pacing_frozen": False,
            "current_target_milestone": "ms_1",
            "behavior_window": [],
        },
        "quests": {
            "dynamic_quests": {"dq_intro": {"status": "active"}},
        },
        "time": {"day": 3, "slot": 2, "period": "afternoon"},
        "current_tick": 50,
        "location": {"area_id": "forest", "location_id": "quest_hub"},
        "changed_slices": ["quests", "player"],
        "change_count": 2,
        "scene": {
            "system_entries_digest": [
                {
                    "command_type": "create_rumor",
                    "reason": "market whispers",
                }
            ],
            "visible_command_types": ["create_rumor"],
        },
        "events": {"pending_events_digest": []},
        "area_npcs": ["guild_girl", "merchant"],
        "area_boards": [
            {"id": "quest_board", "sub_location": "adventurer_guild"},
        ],
        "party": [{"id": "companion_lee"}, {"id": "companion_lin"}],
        "play_style_tags": ["DIALOGUE_HEAVY", "TRADER"],
        "world_context": {
            "area_description": "A dim forest filled with ancient stones.",
            "relevant_factions": [{"id": "f_guild", "name": "Merchant Guild"}],
            "world_rules": [
                {
                    "id": "r1",
                    "title": "Night Curfew",
                    "description": "No shouting during the night shift.",
                }
            ],
        },
    }

    result = _format_planner_context(ctx)

    assert "Party members: companion_lee, companion_lin" in result
    assert "Play style: DIALOGUE_HEAVY, TRADER" in result
    assert "Area NPCs: guild_girl, merchant" in result
    assert "Allowed npc ids: guild_girl, merchant" in result
    assert "Quest boards: quest_board@adventurer_guild" in result
    assert "Allowed board ids: quest_board" in result
    assert "Area description: A dim forest filled with ancient stones." in result
    assert "Factions: Merchant Guild" in result
    assert "World rules:\n  Night Curfew: No shouting during the night shift." in result


def test_system_prompt_contains_full_phase3_requirements() -> None:
    prompt = AgenticNarrativePlanner._SYSTEM_PROMPT
    assert "你是叙事编剧" in prompt
    assert "你不是 GM" in prompt
    assert "story_facts" in prompt
    assert "设计原则" in prompt
    assert "升级阶梯" in prompt
    assert "plant_environmental" in prompt
    assert "fill_area" in prompt
    assert "严格 JSON" in prompt
    assert "npc_id 必须来自" in prompt
    assert "board_id 必须来自" in prompt
    assert "最多 3 条 directives" in prompt
