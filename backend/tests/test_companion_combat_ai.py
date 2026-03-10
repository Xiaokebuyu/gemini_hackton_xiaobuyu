"""Tests for app/companion_combat_ai.py — LLM-driven companion combat AI.

All async tests use asyncio.run() wrapper (pytest-asyncio not installed).
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest import mock

from app.companion_combat_ai import (
    _parse_decision_response,
    build_battlefield_summary,
    decide_companion_combat_turn,
)
from app.game_core.adapters.llm import LlmResponse, NullLlmProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_grid(width: int = 8, height: int = 6) -> dict[str, Any]:
    return {
        "width": width,
        "height": height,
        "terrain": ["G" * width for _ in range(height)],
    }


def _make_unit(
    unit_id: str,
    side: str = "ally",
    hp: int = 10,
    max_hp: int = 10,
    ac: int = 12,
    position: list[int] | None = None,
    attacks: list[dict[str, Any]] | None = None,
    speed: int = 3,
) -> dict[str, Any]:
    return {
        "unit_id": unit_id,
        "side": side,
        "name": unit_id,
        "hp": hp,
        "max_hp": max_hp,
        "ac": ac,
        "position": position or [0, 0],
        "alive": True,
        "fled": False,
        "speed": speed,
        "dashed": False,
        "attacks": attacks or [],
    }


# ---------------------------------------------------------------------------
# Mock LLM providers
# ---------------------------------------------------------------------------

class _MockLlmProvider:
    """Returns a fixed text response."""

    def __init__(self, text: str) -> None:
        self._text = text

    async def generate(
        self,
        system_prompt: str,
        history: list[dict[str, Any]],
        tool_declarations: list[dict[str, Any]],
    ) -> LlmResponse:
        return LlmResponse(text=self._text)

    async def generate_stream(self, *args: Any, **kwargs: Any):  # type: ignore[override]
        return
        yield  # noqa: unreachable


class _ThrowingLlmProvider:
    """Always raises an exception on generate()."""

    async def generate(
        self,
        system_prompt: str,
        history: list[dict[str, Any]],
        tool_declarations: list[dict[str, Any]],
    ) -> LlmResponse:
        raise RuntimeError("LLM service unavailable")

    async def generate_stream(self, *args: Any, **kwargs: Any):  # type: ignore[override]
        return
        yield  # noqa: unreachable


# ---------------------------------------------------------------------------
# Test 1: valid LLM response returns a decision dict
# ---------------------------------------------------------------------------

def test_valid_llm_response_returns_decision():
    """LLM returns valid JSON → decide_companion_combat_turn returns dict."""

    async def _run():
        unit = _make_unit(
            "priestess_1",
            side="ally",
            position=[1, 2],
            attacks=[{"name": "Mace", "hit_bonus": 3, "damage_dice": "1d6",
                       "damage_type": "bludgeoning", "range": 1}],
        )
        enemy = _make_unit("goblin_1", side="enemy", position=[5, 1])
        grid_data = _make_grid()
        all_units = [unit, enemy]

        llm_text = '{"move_to": [4, 1], "action": "attack", "target_id": "goblin_1", "attack_index": 0}'
        provider = _MockLlmProvider(llm_text)

        result = await decide_companion_combat_turn(
            unit=unit,
            grid_data=grid_data,
            all_units=all_units,
            character_name="Priestess",
            personality_hint="谨慎的治疗者",
            approval=60,
            llm_provider=provider,
        )

        assert result is not None
        assert result["action"] == "attack"
        assert result["target_id"] == "goblin_1"
        assert result["attack_index"] == 0
        assert result["move_to"] == [4, 1]

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Test 2: invalid JSON from LLM returns None
# ---------------------------------------------------------------------------

def test_invalid_json_returns_none():
    """LLM returns non-JSON text → decide_companion_combat_turn returns None."""

    async def _run():
        unit = _make_unit("warrior_1", side="ally", position=[0, 0])
        enemy = _make_unit("orc_1", side="enemy", position=[3, 0])
        grid_data = _make_grid()
        all_units = [unit, enemy]

        # Invalid JSON — cannot be parsed
        provider = _MockLlmProvider("I will attack the goblin bravely!")

        result = await decide_companion_combat_turn(
            unit=unit,
            grid_data=grid_data,
            all_units=all_units,
            character_name="Warrior",
            personality_hint="勇猛的战士",
            approval=40,
            llm_provider=provider,
        )

        assert result is None

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Test 3: LLM exception returns None (graceful fallback)
# ---------------------------------------------------------------------------

def test_llm_exception_returns_none():
    """LLM raises exception → decide_companion_combat_turn returns None."""

    async def _run():
        unit = _make_unit("rogue_1", side="ally", position=[2, 2])
        enemy = _make_unit("skeleton_1", side="enemy", position=[4, 2])
        grid_data = _make_grid()
        all_units = [unit, enemy]

        provider = _ThrowingLlmProvider()

        result = await decide_companion_combat_turn(
            unit=unit,
            grid_data=grid_data,
            all_units=all_units,
            character_name="Rogue",
            personality_hint="狡猾的盗贼",
            approval=20,
            llm_provider=provider,
        )

        assert result is None

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Test 4: battlefield summary format verification
# ---------------------------------------------------------------------------

def test_battlefield_summary_format():
    """build_battlefield_summary includes grid, all units, and correct marker."""
    grid_data = _make_grid(width=4, height=3)
    ally = _make_unit(
        "healer_1",
        side="ally",
        position=[1, 2],
        hp=6,
        max_hp=8,
        ac=12,
        attacks=[
            {"name": "Holy Light", "hit_bonus": 4, "damage_dice": "1d8",
             "damage_type": "radiant", "range": 3}
        ],
    )
    enemy = _make_unit(
        "goblin_1",
        side="enemy",
        position=[3, 0],
        hp=5,
        max_hp=5,
        ac=10,
    )

    summary = build_battlefield_summary(
        grid_data=grid_data,
        units=[ally, enemy],
        current_unit_id="healer_1",
    )

    # Grid dimensions header
    assert "4x3" in summary

    # Terrain rows present (all G in this test)
    assert "GGGG" in summary

    # Both units listed
    assert "healer_1" in summary
    assert "goblin_1" in summary

    # Current unit marker
    assert "← 你" in summary

    # Side labels
    assert "[ally]" in summary
    assert "[enemy]" in summary

    # Attack info for current unit
    assert "Holy Light" in summary
    assert "1d8" in summary

    # Speed info
    assert "速度" in summary


# ---------------------------------------------------------------------------
# Test 5: _parse_decision_response handles embedded JSON in prose
# ---------------------------------------------------------------------------

def test_parse_decision_response_extracts_embedded_json():
    """_parse_decision_response can extract JSON embedded in surrounding text."""
    text = 'I think the best move is: {"action": "defend", "move_to": null, "target_id": null, "attack_index": 0} That should work!'
    result = _parse_decision_response(text)
    assert result is not None
    assert result["action"] == "defend"


def test_parse_decision_response_returns_none_for_missing_action():
    """_parse_decision_response returns None when action key is absent."""
    text = '{"move_to": [1, 2], "target_id": null}'
    result = _parse_decision_response(text)
    assert result is None


def test_parse_decision_response_returns_none_for_empty_string():
    """_parse_decision_response returns None for empty input."""
    assert _parse_decision_response("") is None
    assert _parse_decision_response("   ") is None
