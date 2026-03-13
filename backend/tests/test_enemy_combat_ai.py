"""Tests for app/enemy_combat_ai.py."""

from __future__ import annotations

import asyncio
from typing import Any

from app.enemy_combat_ai import decide_enemy_combat_turn
from app.game_core.adapters.llm import LlmResponse


def _make_grid(width: int = 8, height: int = 6) -> dict[str, Any]:
    return {
        "width": width,
        "height": height,
        "terrain": ["G" * width for _ in range(height)],
    }


def _make_unit(
    unit_id: str,
    *,
    side: str,
    source: str,
    position: list[int],
    attacks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "unit_id": unit_id,
        "monster_id": unit_id if source == "monster" else None,
        "side": side,
        "source": source,
        "name": unit_id,
        "hp": 10,
        "max_hp": 10,
        "ac": 12,
        "position": position,
        "alive": True,
        "fled": False,
        "speed": 6,
        "dashed": False,
        "attacks": attacks or [
            {
                "name": "Claw",
                "hit_bonus": 3,
                "damage_dice": "1d6",
                "damage_type": "slashing",
                "range": 1,
            }
        ],
    }


class _MockLlmProvider:
    def __init__(self, text: str) -> None:
        self._text = text

    async def generate(
        self,
        system_prompt: str,
        history: list[dict[str, Any]],
        tool_declarations: list[dict[str, Any]],
    ) -> LlmResponse:
        del system_prompt, history, tool_declarations
        return LlmResponse(text=self._text)

    async def generate_stream(self, *args: Any, **kwargs: Any):  # type: ignore[override]
        return
        yield  # pragma: no cover


class _ThrowingLlmProvider:
    async def generate(
        self,
        system_prompt: str,
        history: list[dict[str, Any]],
        tool_declarations: list[dict[str, Any]],
    ) -> LlmResponse:
        del system_prompt, history, tool_declarations
        raise RuntimeError("LLM service unavailable")

    async def generate_stream(self, *args: Any, **kwargs: Any):  # type: ignore[override]
        return
        yield  # pragma: no cover


class _CaptureLlmProvider:
    def __init__(self) -> None:
        self.system_prompt = ""

    async def generate(
        self,
        system_prompt: str,
        history: list[dict[str, Any]],
        tool_declarations: list[dict[str, Any]],
    ) -> LlmResponse:
        del history, tool_declarations
        self.system_prompt = system_prompt
        return LlmResponse(
            text='{"move_to": null, "action": "hold", "target_id": null, "attack_index": 0}'
        )

    async def generate_stream(self, *args: Any, **kwargs: Any):  # type: ignore[override]
        return
        yield  # pragma: no cover


def test_valid_llm_response_returns_enemy_decision() -> None:
    async def _run() -> None:
        unit = _make_unit("goblin_rider_1", side="enemy", source="monster", position=[1, 1])
        target = _make_unit("player", side="ally", source="player", position=[4, 1])
        provider = _MockLlmProvider(
            '{"move_to": [3, 1], "action": "attack", "target_id": "player", "attack_index": 0}'
        )

        result = await decide_enemy_combat_turn(
            unit=unit,
            grid_data=_make_grid(),
            all_units=[unit, target],
            monster_name="Goblin Rider",
            decision_tier="boss",
            personality_hint="aggressive",
            tactics_notes="绕后切后排。",
            preferred_terrain=["hill"],
            llm_provider=provider,
        )

        assert result is not None
        assert result["action"] == "attack"
        assert result["target_id"] == "player"
        assert result["attack_index"] == 0
        assert result["move_to"] == [3, 1]

    asyncio.run(_run())


def test_invalid_json_returns_none() -> None:
    async def _run() -> None:
        unit = _make_unit("hobgoblin_1", side="enemy", source="monster", position=[0, 0])
        target = _make_unit("player", side="ally", source="player", position=[1, 0])

        result = await decide_enemy_combat_turn(
            unit=unit,
            grid_data=_make_grid(),
            all_units=[unit, target],
            monster_name="Hobgoblin",
            decision_tier="elite",
            personality_hint="defensive",
            tactics_notes="守住前排。",
            preferred_terrain=["forest"],
            llm_provider=_MockLlmProvider("I will crush the hero."),
        )

        assert result is None

    asyncio.run(_run())


def test_llm_exception_returns_none() -> None:
    async def _run() -> None:
        unit = _make_unit("goblin_shaman_1", side="enemy", source="monster", position=[2, 2])
        target = _make_unit("player", side="ally", source="player", position=[4, 2])

        result = await decide_enemy_combat_turn(
            unit=unit,
            grid_data=_make_grid(),
            all_units=[unit, target],
            monster_name="Goblin Shaman",
            decision_tier="elite",
            personality_hint="aggressive",
            tactics_notes="优先轰炸扎堆目标。",
            preferred_terrain=["ruins"],
            llm_provider=_ThrowingLlmProvider(),
        )

        assert result is None

    asyncio.run(_run())


def test_enemy_prompt_contains_tier_tactics_and_terrain() -> None:
    async def _run() -> None:
        unit = _make_unit("goblin_rider_1", side="enemy", source="monster", position=[1, 1])
        target = _make_unit("player", side="ally", source="player", position=[4, 1])
        provider = _CaptureLlmProvider()

        result = await decide_enemy_combat_turn(
            unit=unit,
            grid_data=_make_grid(),
            all_units=[unit, target],
            monster_name="Goblin Rider Captain",
            decision_tier="boss",
            personality_hint="aggressive",
            tactics_notes="优先绕后切后排治疗者。",
            preferred_terrain=["hill", "road"],
            llm_provider=provider,
        )

        assert result is not None
        assert "你的身份层级：boss" in provider.system_prompt
        assert "战斗性格：aggressive" in provider.system_prompt
        assert "优先绕后切后排治疗者。" in provider.system_prompt
        assert "偏好地形：hill, road" in provider.system_prompt
        assert '"action": "attack"/"defend"/"hold"/"flee"' in provider.system_prompt

    asyncio.run(_run())
