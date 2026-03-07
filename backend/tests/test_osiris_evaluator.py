"""Tests for AgenticAIOsirisEvaluator."""

from __future__ import annotations

import asyncio
from typing import Any

from app.evaluators import (
    AgenticAIOsirisEvaluator,
    OSIRIS_SYSTEM_PROMPT,
    SUBMIT_CONSEQUENCES_TOOL,
    _parse_llm_response,
)
from app.game_core.adapters.llm import LlmResponse


# ------------------------------------------------------------------
# Recording LLM provider
# ------------------------------------------------------------------


class RecordingLlm:
    """LLM provider that returns pre-configured responses."""

    def __init__(self, response: LlmResponse | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._response = response or LlmResponse()

    async def generate(
        self,
        system_prompt: str,
        history: list[dict[str, Any]],
        tool_declarations: list[dict[str, Any]],
    ) -> LlmResponse:
        self.calls.append({
            "system_prompt": system_prompt,
            "history": history,
            "tool_declarations": tool_declarations,
        })
        return self._response


class ExplodingLlm:
    """LLM that always raises an exception."""

    async def generate(self, *args: Any, **kwargs: Any) -> LlmResponse:
        raise RuntimeError("LLM service unavailable")


# ------------------------------------------------------------------
# _parse_llm_response tests
# ------------------------------------------------------------------


class TestParseLlmResponse:
    def test_tool_call_parsed_correctly(self) -> None:
        response = LlmResponse(
            tool_calls=[{
                "name": "submit_consequences",
                "args": {
                    "reasoning": "Player stole from merchant.",
                    "visible_change": True,
                    "consequences": [
                        {
                            "type": "modify_disposition",
                            "params": {"target": "merchant_guild", "delta": -5},
                            "reason": "Theft detected",
                        },
                        {
                            "type": "set_flag",
                            "params": {"key": "stolen_from_store", "value": True},
                        },
                    ],
                },
            }],
            finish_reason="tool_calls",
            metadata={"provider": "gemini", "profile": "osiris", "thinking_level": "medium"},
        )

        decision = _parse_llm_response(response)

        assert decision.metadata["status"] == "llm"
        assert decision.metadata["source"] == "tool_call"
        assert decision.visible_change is True
        assert decision.metadata["profile"] == "osiris"
        assert decision.reasoning == "Player stole from merchant."
        assert len(decision.consequences) == 2
        assert decision.consequences[0]["type"] == "modify_disposition"
        assert decision.consequences[1]["type"] == "set_flag"

    def test_empty_consequences_from_tool_call(self) -> None:
        response = LlmResponse(
            tool_calls=[{
                "name": "submit_consequences",
                "args": {
                    "reasoning": "Nothing noteworthy happened.",
                    "visible_change": False,
                    "consequences": [],
                },
            }],
            finish_reason="tool_calls",
        )

        decision = _parse_llm_response(response)

        assert decision.metadata["status"] == "llm"
        assert decision.consequences == []
        assert decision.reasoning == "Nothing noteworthy happened."
        assert decision.visible_change is False

    def test_text_json_fallback(self) -> None:
        response = LlmResponse(
            text='{"reasoning": "fallback", "visible_change": true, "consequences": [{"type": "set_flag", "params": {"key": "x", "value": 1}}]}',
        )

        decision = _parse_llm_response(response)

        assert decision.metadata["status"] == "llm"
        assert decision.metadata["source"] == "text_json"
        assert len(decision.consequences) == 1
        assert decision.reasoning == "fallback"
        assert decision.visible_change is True

    def test_unparseable_response(self) -> None:
        response = LlmResponse(text="I cannot help with that.")

        decision = _parse_llm_response(response)

        assert decision.metadata["status"] == "llm_parse_failed"
        assert decision.consequences == []

    def test_empty_response(self) -> None:
        response = LlmResponse()

        decision = _parse_llm_response(response)

        assert decision.metadata["status"] == "llm_parse_failed"


# ------------------------------------------------------------------
# AgenticAIOsirisEvaluator integration tests
# ------------------------------------------------------------------


class TestAgenticAIOsirisEvaluator:
    def test_evaluate_calls_llm_with_correct_prompt(self) -> None:
        llm = RecordingLlm(LlmResponse(
            tool_calls=[{
                "name": "submit_consequences",
                "args": {"reasoning": "ok", "visible_change": False, "consequences": []},
            }],
            finish_reason="tool_calls",
            metadata={"provider": "gemini", "profile": "osiris", "thinking_level": "medium"},
        ))
        evaluator = AgenticAIOsirisEvaluator(llm=llm)

        decision = asyncio.run(evaluator.evaluate(
            summary={"change_count": 1},
            snapshot={"player": {"level": 3}},
            rules_context={"allowed_commands": ["set_flag"]},
        ))

        assert len(llm.calls) == 1
        call = llm.calls[0]
        assert call["system_prompt"] == OSIRIS_SYSTEM_PROMPT
        assert call["tool_declarations"] == [SUBMIT_CONSEQUENCES_TOOL]
        assert len(call["history"]) == 1
        assert call["history"][0]["role"] == "user"
        # User message should contain the three sections
        text = call["history"][0]["parts"][0]["text"]
        assert "time_slot_summary" in text
        assert "world_state_snapshot" in text
        assert "rules_context" in text
        assert decision.consequences == []
        assert decision.metadata["profile"] == "osiris"
        assert decision.metadata["thinking_level"] == "medium"
        assert decision.metadata["latency_ms"] >= 0.0

    def test_evaluate_extracts_consequences(self) -> None:
        llm = RecordingLlm(LlmResponse(
            tool_calls=[{
                "name": "submit_consequences",
                "args": {
                    "reasoning": "Theft detected by companion.",
                    "visible_change": True,
                    "consequences": [
                        {
                            "type": "modify_approval",
                            "params": {"character": "paladin", "delta": -10},
                        },
                    ],
                },
            }],
            finish_reason="tool_calls",
        ))
        evaluator = AgenticAIOsirisEvaluator(llm=llm)

        decision = asyncio.run(evaluator.evaluate(
            summary={}, snapshot={}, rules_context={},
        ))

        assert decision.reasoning == "Theft detected by companion."
        assert len(decision.consequences) == 1
        assert decision.consequences[0]["type"] == "modify_approval"
        assert decision.metadata["status"] == "llm"
        assert decision.visible_change is True

    def test_evaluate_handles_llm_exception(self) -> None:
        evaluator = AgenticAIOsirisEvaluator(llm=ExplodingLlm())

        decision = asyncio.run(evaluator.evaluate(
            summary={}, snapshot={}, rules_context={},
        ))

        assert decision.metadata["status"] == "llm_error"
        assert decision.consequences == []

    def test_evaluate_handles_parse_failure(self) -> None:
        llm = RecordingLlm(LlmResponse(text="random nonsense"))
        evaluator = AgenticAIOsirisEvaluator(llm=llm)

        decision = asyncio.run(evaluator.evaluate(
            summary={}, snapshot={}, rules_context={},
        ))

        assert decision.metadata["status"] == "llm_parse_failed"
        assert decision.consequences == []
