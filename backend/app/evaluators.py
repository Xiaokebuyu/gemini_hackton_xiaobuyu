"""LLM-driven evaluator implementations — application layer.

These implement the AIOsirisEvaluator Protocol defined in game_core but live
in app/ because they depend on external LLM providers (LlmPort).
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Mapping

from app.game_core.adapters.llm import LlmPort, LlmResponse
from app.game_core.orchestration.hooks.ai_osiris import AIOsirisDecision

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# AI Osiris System Prompt (design spec §四)
# ------------------------------------------------------------------

OSIRIS_SYSTEM_PROMPT = """\
You are AI Osiris, a causal judgement engine for a dark-fantasy CRPG world.

## Your role
Analyse all events that occurred in a single time slot, and reason about \
what cross-system consequences these events would produce in this world.

## Your task
1. Only output **ripple consequences that cross system boundaries** — \
do NOT repeat facts that already happened.
2. Consider: How would NPCs perceive these actions? How would faction \
relations shift? Are there delayed consequences? How do ongoing quests \
get affected?
3. Use entity Tags for reasoning (e.g. stealing a [SACRED] item → \
religious faction consequences).
4. If the time slot produced no meaningful cross-system consequences, \
submit an empty consequences array.
5. **NEVER** output direct modifications to HP, gold, inventory, or \
player location — those belong to the rules engine, not to you.
6. Treat travel / rest / conversation / combat-resolution ticks differently. \
Travel and rest often produce delayed or environmental consequences rather \
than immediate social reactions.
7. Use `schedule_event` when the consequence should happen later, not now.
8. Set `visible_change=true` only when the player could directly perceive \
at least one consequence during or immediately after this time slot.

## Available command types
set_flag, modify_disposition, modify_approval, advance_quest, \
schedule_event, create_rumor, modify_location, add_knowledge, \
modify_completion, adjust_danger

## Constraints
- You may only modify "how the world perceives the player": \
dispositions / flags / events / knowledge.
- You may NOT modify player HP, gold, inventory, or location directly.
- Keep consequences proportional — small actions produce small ripples.

## Tick-specific guidance
- travel ticks: prefer danger, rumors, delayed events, or encounter preparation.
- rest ticks: prefer delayed world changes, companion approval shifts, or new pending events.
- If `tick_kind=rest` and `rest_phase.is_quiet_rest_slot=true`, default to an empty
  consequences array.
- conversation ticks: prefer rumors, disposition changes, approvals, and quest ripples.
- combat_resolution ticks: prefer witness reactions, faction ripples, danger changes, or delayed retaliation.
- If a consequence should not be immediately visible, still emit it as a normal consequence, but keep `visible_change=false` unless the player could perceive it now.
- On quiet long-rest sleep slots, do NOT emit `create_rumor`, `publish_bulletin`,
  unrelated `set_flag`, or social relationship changes without a direct in-world signal.

## Language
Respond in the same language as the input content. If the summary is \
in Chinese, reason and respond in Chinese. If in English, use English.

When the player could directly perceive the consequence this time slot,
set `visible_change=true`.

Call the `submit_consequences` tool to submit your analysis.\
"""


# ------------------------------------------------------------------
# Tool declaration for structured output
# ------------------------------------------------------------------

SUBMIT_CONSEQUENCES_TOOL: dict[str, Any] = {
    "name": "submit_consequences",
    "description": "Submit the causal analysis of cross-system consequences.",
    "parameters": {
        "type": "object",
        "properties": {
            "reasoning": {
                "type": "string",
                "description": (
                    "Brief explanation of the causal chain — why these "
                    "consequences follow from the events."
                ),
            },
            "visible_change": {
                "type": "boolean",
                "description": (
                    "Whether the player could directly perceive at least one "
                    "consequence this time slot."
                ),
            },
            "consequences": {
                "type": "array",
                "description": (
                    "List of consequence commands. Empty array if nothing "
                    "noteworthy happened."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "description": "Command type from the allowed list.",
                        },
                        "params": {
                            "type": "object",
                            "description": "Command parameters.",
                        },
                        "reason": {
                            "type": "string",
                            "description": "Why this consequence follows.",
                        },
                        "visibility_hint": {
                            "type": "string",
                            "description": "visible or hidden",
                        },
                        "confidence": {
                            "type": "string",
                            "description": "low, medium, or high",
                        },
                    },
                    "required": ["type", "params"],
                },
            },
        },
        "required": ["reasoning", "visible_change", "consequences"],
    },
}


# ------------------------------------------------------------------
# Response parser
# ------------------------------------------------------------------


def _normalize_consequence_payload(raw_value: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_value, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in raw_value:
        if not isinstance(item, Mapping):
            continue
        command_type = item.get("type")
        params = item.get("params")
        if not isinstance(command_type, str) or not command_type.strip():
            continue
        if not isinstance(params, Mapping):
            continue
        payload: dict[str, Any] = {
            "type": command_type.strip(),
            "params": {str(key): value for key, value in params.items()},
        }
        reason = item.get("reason")
        if isinstance(reason, str) and reason.strip():
            payload["reason"] = reason.strip()
        visibility_hint = item.get("visibility_hint")
        if isinstance(visibility_hint, str) and visibility_hint.strip():
            payload["visibility_hint"] = visibility_hint.strip().lower()
        confidence = item.get("confidence")
        if isinstance(confidence, str) and confidence.strip():
            payload["confidence"] = confidence.strip().lower()
        normalized.append(payload)
    return normalized


def _parse_llm_response(response: LlmResponse) -> AIOsirisDecision:
    """Extract AIOsirisDecision from an LLM response.

    Prefers tool_call args; falls back to text JSON parsing.
    """
    base_metadata = dict(response.metadata)
    if response.tool_calls:
        args = response.tool_calls[0].get("args", {})
        if not isinstance(args, dict):
            args = {}
        consequences = _normalize_consequence_payload(args.get("consequences", []))
        metadata = {
            **base_metadata,
            "status": "llm",
            "source": "tool_call",
        }
        return AIOsirisDecision(
            consequences=consequences,
            reasoning=str(args.get("reasoning", "")),
            visible_change=bool(args.get("visible_change", False)),
            metadata=metadata,
        )

    if response.text:
        try:
            data = json.loads(response.text)
            if isinstance(data, dict):
                consequences = _normalize_consequence_payload(data.get("consequences", []))
                metadata = {
                    **base_metadata,
                    "status": "llm",
                    "source": "text_json",
                }
                return AIOsirisDecision(
                    consequences=consequences,
                    reasoning=str(data.get("reasoning", "")),
                    visible_change=bool(data.get("visible_change", False)),
                    metadata=metadata,
                )
        except (json.JSONDecodeError, TypeError):
            pass

    return AIOsirisDecision(
        metadata={
            **base_metadata,
            "status": "llm_parse_failed",
        }
    )


# ------------------------------------------------------------------
# LLM-driven evaluator
# ------------------------------------------------------------------


class GeminiAIOsirisProvider:
    """Single-shot structured provider for AI Osiris."""

    def __init__(self, llm: LlmPort) -> None:
        self._llm = llm

    async def evaluate(
        self,
        summary: dict[str, Any],
        snapshot: dict[str, Any],
        rules_context: dict[str, Any],
    ) -> AIOsirisDecision:
        user_message = json.dumps(
            {
                "time_slot_summary": summary,
                "world_state_snapshot": snapshot,
                "rules_context": rules_context,
            },
            ensure_ascii=False,
            default=str,
        )

        started_at = time.perf_counter()
        try:
            response = await self._llm.generate(
                system_prompt=OSIRIS_SYSTEM_PROMPT,
                history=[{"role": "user", "parts": [{"text": user_message}]}],
                tool_declarations=[SUBMIT_CONSEQUENCES_TOOL],
            )
        except Exception:
            logger.exception("GeminiAIOsirisProvider: LLM call failed")
            return AIOsirisDecision(
                metadata={
                    "status": "llm_error",
                    "provider": "unknown",
                    "latency_ms": (time.perf_counter() - started_at) * 1000.0,
                }
            )

        decision = _parse_llm_response(response)
        metadata = dict(decision.metadata)
        metadata.setdefault("provider", "gemini")
        metadata.setdefault("profile", "osiris")
        metadata["latency_ms"] = (time.perf_counter() - started_at) * 1000.0
        return AIOsirisDecision(
            consequences=list(decision.consequences),
            reasoning=decision.reasoning,
            visible_change=decision.visible_change,
            metadata=metadata,
        )


class AgenticAIOsirisEvaluator(GeminiAIOsirisProvider):
    """Backward-compatible alias for the dedicated Gemini Osiris provider."""
