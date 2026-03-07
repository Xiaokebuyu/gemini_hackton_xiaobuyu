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
    assert "You are a narrative planner AI for this RPG." in prompt
    assert "You are NOT the GM" in prompt
    assert "7 Core Principles" in prompt
    assert "L0-L5 ladder" in prompt
    assert "spawn_quest_npc" in prompt
    assert "plant_environmental" in prompt
    assert "fill_area" in prompt
    assert "Output must be strict JSON only" in prompt
    assert "npc_id must come from Area NPCs" in prompt
    assert "board_id must come from Quest boards" in prompt
    assert "Max 3 directives" in prompt
