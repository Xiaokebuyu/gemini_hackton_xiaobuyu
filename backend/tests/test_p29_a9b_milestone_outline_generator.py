"""Tests for P29-A9b: MilestoneOutlineGenerator (LLM + deterministic fallback).

Covers:
- _build_fallback_outline  (A9f deterministic path)
- MilestoneOutlineGenerator.generate() with no LLM  → fallback
- MilestoneOutlineGenerator.generate() with LLM returning valid JSON
- MilestoneOutlineGenerator.generate() with LLM returning markdown-wrapped JSON
- MilestoneOutlineGenerator.generate() with LLM returning invalid JSON → fallback
- MilestoneOutlineGenerator.generate() with LLM raising exception → fallback
- MilestoneOutlineGenerator.generate() with LLM returning missing steps → fallback
- _build_user_message content checks
- _parse_response: step normalisation
"""

import asyncio
import json
from typing import Any

from app.narrators import MilestoneOutlineGenerator, _build_fallback_outline
from app.game_core.adapters.llm import LlmResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TEMPLATE_FULL = {
    "key_elements": ["找到农场", "对话农民", "击败哥布林"],
    "narrative_context": "哥布林侵扰了边境农场",
    "success_conditions": ["击败所有哥布林"],
    "involved_npcs": ["farmer", "guild_receptionist"],
    "involved_locations": ["cow_girl_farm", "frontier_town"],
}

_TEMPLATE_MINIMAL = {
    "key_elements": ["单一目标"],
}

_TEMPLATE_EMPTY = {}


def _make_llm_response(text: str) -> LlmResponse:
    return LlmResponse(text=text, finish_reason="stop")


def _valid_llm_json(milestone_id: str = "m_test") -> str:
    outline = {
        "target_milestone_id": milestone_id,
        "chapter_id": "ch1",
        "computed_at_tick": 5,
        "steps": [
            {
                "index": i,
                "description": f"步骤 {i}",
                "type": "investigation",
                "condition": {"type": "flag_set", "flag_name": f"step_{i}_done"},
                "related_npcs": [],
                "related_locations": [],
                "completed": False,
                "quest_id": None,
            }
            for i in range(6)
        ],
    }
    return json.dumps(outline, ensure_ascii=False)


class _StubLlm:
    """Minimal LlmPort stub: returns a fixed response."""

    def __init__(self, text: str = "", raise_exc: bool = False) -> None:
        self._text = text
        self._raise = raise_exc

    async def generate(
        self,
        system_prompt: str,
        history: list[dict[str, Any]],
        tool_declarations: list[dict[str, Any]],
    ) -> LlmResponse:
        if self._raise:
            raise RuntimeError("simulated LLM failure")
        return _make_llm_response(self._text)

    async def generate_stream(self, *args: Any, **kwargs: Any):
        return
        yield  # noqa: unreachable


# ---------------------------------------------------------------------------
# _build_fallback_outline (A9f)
# ---------------------------------------------------------------------------


def test_fallback_outline_uses_key_elements():
    result = _build_fallback_outline(_TEMPLATE_FULL, "m1", "ch1", 10)
    assert result["target_milestone_id"] == "m1"
    assert result["chapter_id"] == "ch1"
    assert result["computed_at_tick"] == 10
    steps = result["steps"]
    assert len(steps) == 3  # 3 key_elements
    assert steps[0]["description"] == "找到农场"
    assert steps[1]["description"] == "对话农民"
    assert steps[2]["description"] == "击败哥布林"


def test_fallback_outline_first_step_gets_npcs_and_locations():
    result = _build_fallback_outline(_TEMPLATE_FULL, "m1", "ch1", 10)
    steps = result["steps"]
    # First step gets up to 2 NPCs and 1 location
    assert "farmer" in steps[0]["related_npcs"]
    assert "cow_girl_farm" in steps[0]["related_locations"]
    # Subsequent steps have empty lists
    assert steps[1]["related_npcs"] == []
    assert steps[1]["related_locations"] == []


def test_fallback_outline_step_indices_are_sequential():
    result = _build_fallback_outline(_TEMPLATE_FULL, "m1", "ch1", 10)
    indices = [s["index"] for s in result["steps"]]
    assert indices == list(range(len(indices)))


def test_fallback_outline_empty_template_produces_single_step():
    result = _build_fallback_outline(_TEMPLATE_EMPTY, "m_empty", "ch0", 0)
    assert len(result["steps"]) == 1
    assert result["steps"][0]["description"] == "完成里程碑目标"


def test_fallback_outline_caps_at_ten_steps():
    template = {"key_elements": [f"element_{i}" for i in range(20)]}
    result = _build_fallback_outline(template, "m_big", "ch1", 1)
    assert len(result["steps"]) == 10


def test_fallback_outline_string_key_elements():
    template = {"key_elements": "单字符串目标"}
    result = _build_fallback_outline(template, "m_str", "ch1", 1)
    assert len(result["steps"]) == 1
    assert result["steps"][0]["description"] == "单字符串目标"


def test_fallback_outline_steps_have_required_keys():
    result = _build_fallback_outline(_TEMPLATE_FULL, "m1", "ch1", 10)
    required_keys = {
        "index", "description", "type", "condition",
        "related_npcs", "related_locations", "completed", "quest_id",
    }
    for step in result["steps"]:
        assert required_keys.issubset(step.keys()), f"Step missing keys: {step}"


def test_fallback_outline_all_steps_not_completed():
    result = _build_fallback_outline(_TEMPLATE_FULL, "m1", "ch1", 10)
    for step in result["steps"]:
        assert step["completed"] is False
        assert step["quest_id"] is None


# ---------------------------------------------------------------------------
# MilestoneOutlineGenerator — no LLM
# ---------------------------------------------------------------------------


def test_generate_no_llm_returns_fallback():
    gen = MilestoneOutlineGenerator(llm=None)

    async def _run():
        return await gen.generate(
            milestone_template=_TEMPLATE_FULL,
            target_milestone_id="m1",
            chapter_id="ch1",
            current_tick=5,
        )

    result = asyncio.run(_run())
    assert result["target_milestone_id"] == "m1"
    assert len(result["steps"]) == 3  # 3 key_elements in _TEMPLATE_FULL


def test_generate_no_llm_minimal_template():
    gen = MilestoneOutlineGenerator(llm=None)

    async def _run():
        return await gen.generate(
            milestone_template=_TEMPLATE_MINIMAL,
            target_milestone_id="m_min",
            chapter_id="ch2",
            current_tick=0,
        )

    result = asyncio.run(_run())
    assert result["target_milestone_id"] == "m_min"
    assert len(result["steps"]) == 1


# ---------------------------------------------------------------------------
# MilestoneOutlineGenerator — LLM returns valid JSON
# ---------------------------------------------------------------------------


def test_generate_with_valid_llm_response():
    llm = _StubLlm(text=_valid_llm_json("m_llm"))
    gen = MilestoneOutlineGenerator(llm=llm)

    async def _run():
        return await gen.generate(
            milestone_template=_TEMPLATE_FULL,
            target_milestone_id="m_llm",
            chapter_id="ch1",
            current_tick=5,
        )

    result = asyncio.run(_run())
    assert result["target_milestone_id"] == "m_llm"
    assert len(result["steps"]) == 6
    for i, step in enumerate(result["steps"]):
        assert step["index"] == i
        assert step["completed"] is False
        assert step["quest_id"] is None


def test_generate_with_markdown_wrapped_json():
    raw_json = _valid_llm_json("m_md")
    markdown_text = f"```json\n{raw_json}\n```"
    llm = _StubLlm(text=markdown_text)
    gen = MilestoneOutlineGenerator(llm=llm)

    async def _run():
        return await gen.generate(
            milestone_template=_TEMPLATE_FULL,
            target_milestone_id="m_md",
            chapter_id="ch1",
            current_tick=5,
        )

    result = asyncio.run(_run())
    assert result["target_milestone_id"] == "m_md"
    assert len(result["steps"]) == 6


def test_generate_steps_capped_at_ten():
    """LLM returning 15 steps → normalised to 10."""
    steps = [
        {
            "index": i,
            "description": f"step {i}",
            "type": "dialogue",
            "condition": {"type": "flag_set", "flag_name": f"f{i}"},
            "related_npcs": [],
            "related_locations": [],
            "completed": False,
            "quest_id": None,
        }
        for i in range(15)
    ]
    outline = {
        "target_milestone_id": "m_big",
        "chapter_id": "ch1",
        "computed_at_tick": 1,
        "steps": steps,
    }
    llm = _StubLlm(text=json.dumps(outline))
    gen = MilestoneOutlineGenerator(llm=llm)

    async def _run():
        return await gen.generate(
            milestone_template={},
            target_milestone_id="m_big",
            chapter_id="ch1",
            current_tick=1,
        )

    result = asyncio.run(_run())
    assert len(result["steps"]) == 10


# ---------------------------------------------------------------------------
# MilestoneOutlineGenerator — LLM failure paths → fallback
# ---------------------------------------------------------------------------


def test_generate_with_invalid_json_falls_back():
    llm = _StubLlm(text="this is not JSON }{")
    gen = MilestoneOutlineGenerator(llm=llm)

    async def _run():
        return await gen.generate(
            milestone_template=_TEMPLATE_FULL,
            target_milestone_id="m_bad",
            chapter_id="ch1",
            current_tick=3,
        )

    result = asyncio.run(_run())
    # Must fall back to deterministic outline from key_elements
    assert result["target_milestone_id"] == "m_bad"
    assert len(result["steps"]) == 3  # 3 key_elements in _TEMPLATE_FULL


def test_generate_with_llm_exception_falls_back():
    llm = _StubLlm(raise_exc=True)
    gen = MilestoneOutlineGenerator(llm=llm)

    async def _run():
        return await gen.generate(
            milestone_template=_TEMPLATE_FULL,
            target_milestone_id="m_exc",
            chapter_id="ch1",
            current_tick=7,
        )

    result = asyncio.run(_run())
    assert result["target_milestone_id"] == "m_exc"
    assert len(result["steps"]) > 0  # fallback steps


def test_generate_with_empty_response_falls_back():
    llm = _StubLlm(text="")
    gen = MilestoneOutlineGenerator(llm=llm)

    async def _run():
        return await gen.generate(
            milestone_template=_TEMPLATE_MINIMAL,
            target_milestone_id="m_empty_resp",
            chapter_id="ch1",
            current_tick=1,
        )

    result = asyncio.run(_run())
    assert result["target_milestone_id"] == "m_empty_resp"
    assert len(result["steps"]) >= 1


def test_generate_with_missing_steps_falls_back():
    outline_no_steps = {
        "target_milestone_id": "m_nosteps",
        "chapter_id": "ch1",
        "computed_at_tick": 1,
        # "steps" key missing entirely
    }
    llm = _StubLlm(text=json.dumps(outline_no_steps))
    gen = MilestoneOutlineGenerator(llm=llm)

    async def _run():
        return await gen.generate(
            milestone_template=_TEMPLATE_MINIMAL,
            target_milestone_id="m_nosteps",
            chapter_id="ch1",
            current_tick=1,
        )

    result = asyncio.run(_run())
    # Falls back — comes from key_elements
    assert len(result["steps"]) >= 1


def test_generate_with_empty_steps_list_falls_back():
    outline_empty_steps = {
        "target_milestone_id": "m_emptysteps",
        "chapter_id": "ch1",
        "computed_at_tick": 1,
        "steps": [],
    }
    llm = _StubLlm(text=json.dumps(outline_empty_steps))
    gen = MilestoneOutlineGenerator(llm=llm)

    async def _run():
        return await gen.generate(
            milestone_template=_TEMPLATE_FULL,
            target_milestone_id="m_emptysteps",
            chapter_id="ch1",
            current_tick=1,
        )

    result = asyncio.run(_run())
    assert len(result["steps"]) >= 1  # falls back


# ---------------------------------------------------------------------------
# _build_user_message content validation
# ---------------------------------------------------------------------------


def test_user_message_contains_milestone_id():
    gen = MilestoneOutlineGenerator()
    msg = gen._build_user_message(
        milestone_template=_TEMPLATE_FULL,
        target_milestone_id="m_content_check",
        chapter_id="ch_x",
        current_tick=42,
        game_state_summary={},
        supported_condition_types=["flag_set", "npc_talked"],
    )
    assert "m_content_check" in msg
    assert "ch_x" in msg
    assert "42" in msg


def test_user_message_contains_key_elements():
    gen = MilestoneOutlineGenerator()
    msg = gen._build_user_message(
        milestone_template=_TEMPLATE_FULL,
        target_milestone_id="m1",
        chapter_id="ch1",
        current_tick=0,
        game_state_summary={},
        supported_condition_types=[],
    )
    assert "找到农场" in msg
    assert "对话农民" in msg
    assert "击败哥布林" in msg


def test_user_message_contains_supported_condition_types():
    gen = MilestoneOutlineGenerator()
    msg = gen._build_user_message(
        milestone_template={},
        target_milestone_id="m1",
        chapter_id="ch1",
        current_tick=0,
        game_state_summary={},
        supported_condition_types=["kill_count", "location_entered", "npc_talked"],
    )
    assert "kill_count" in msg
    assert "location_entered" in msg
    assert "npc_talked" in msg


def test_user_message_default_condition_types_when_not_provided():
    gen = MilestoneOutlineGenerator()
    msg = gen._build_user_message(
        milestone_template={},
        target_milestone_id="m1",
        chapter_id="ch1",
        current_tick=0,
        game_state_summary={},
        supported_condition_types=[],  # empty → defaults injected
    )
    # Default fallback list should be injected
    assert "flag_set" in msg


def test_user_message_contains_game_state_summary():
    gen = MilestoneOutlineGenerator()
    msg = gen._build_user_message(
        milestone_template={},
        target_milestone_id="m1",
        chapter_id="ch1",
        current_tick=0,
        game_state_summary={
            "player_area": "frontier_town",
            "active_quests": ["dq_goblin_patrol"],
            "escalation_level": 2,
        },
        supported_condition_types=[],
    )
    assert "frontier_town" in msg
    assert "dq_goblin_patrol" in msg
    assert "2" in msg


# ---------------------------------------------------------------------------
# Integration: outline structure is valid for NarrativePlanSlice
# ---------------------------------------------------------------------------


def test_generated_outline_compatible_with_narrative_plan_slice():
    """Verify the fallback outline can be set on NarrativePlanSlice."""
    from app.game_core.state.slices.narrative_plan import NarrativePlanSlice

    gen = MilestoneOutlineGenerator(llm=None)

    async def _run():
        return await gen.generate(
            milestone_template=_TEMPLATE_FULL,
            target_milestone_id="m_compat",
            chapter_id="ch1",
            current_tick=10,
        )

    outline = asyncio.run(_run())
    slice_ = NarrativePlanSlice()
    slice_.set_milestone_outline(outline)

    assert slice_.milestone_outline["target_milestone_id"] == "m_compat"
    step = slice_.get_current_outline_step()
    assert step is not None
    assert step["index"] == 0
    assert step["completed"] is False


def test_llm_outline_compatible_with_narrative_plan_slice():
    """Verify LLM-parsed outline can be set on NarrativePlanSlice."""
    from app.game_core.state.slices.narrative_plan import NarrativePlanSlice

    llm = _StubLlm(text=_valid_llm_json("m_compat_llm"))
    gen = MilestoneOutlineGenerator(llm=llm)

    async def _run():
        return await gen.generate(
            milestone_template=_TEMPLATE_FULL,
            target_milestone_id="m_compat_llm",
            chapter_id="ch1",
            current_tick=5,
        )

    outline = asyncio.run(_run())
    slice_ = NarrativePlanSlice()
    slice_.set_milestone_outline(outline)

    current_step = slice_.get_current_outline_step()
    assert current_step is not None
    assert current_step["index"] == 0
