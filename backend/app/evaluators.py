"""LLM-driven evaluator implementations — application layer.

These implement the AIOsirisEvaluator Protocol defined in game_core but live
in app/ because they depend on external LLM providers (LlmPort).
"""

from __future__ import annotations

import json
import logging
from typing import Any

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

## Available command types
set_flag, modify_disposition, modify_approval, advance_quest, \
schedule_event, create_rumor, modify_location, add_knowledge, \
modify_completion, adjust_danger

## Constraints
- You may only modify "how the world perceives the player": \
dispositions / flags / events / knowledge.
- You may NOT modify player HP, gold, inventory, or location directly.
- Keep consequences proportional — small actions produce small ripples.

## Language
Respond in the same language as the input content. If the summary is \
in Chinese, reason and respond in Chinese. If in English, use English.

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
                    },
                    "required": ["type", "params"],
                },
            },
        },
        "required": ["reasoning", "consequences"],
    },
}


# ------------------------------------------------------------------
# Response parser
# ------------------------------------------------------------------


def _parse_llm_response(response: LlmResponse) -> AIOsirisDecision:
    """Extract AIOsirisDecision from an LLM response.

    Prefers tool_call args; falls back to text JSON parsing.
    """
    if response.tool_calls:
        args = response.tool_calls[0].get("args", {})
        if not isinstance(args, dict):
            args = {}
        raw_consequences = args.get("consequences", [])
        consequences = list(raw_consequences) if isinstance(raw_consequences, list) else []
        return AIOsirisDecision(
            consequences=consequences,
            reasoning=str(args.get("reasoning", "")),
            metadata={"status": "llm", "source": "tool_call"},
        )

    if response.text:
        try:
            data = json.loads(response.text)
            if isinstance(data, dict):
                raw_consequences = data.get("consequences", [])
                consequences = list(raw_consequences) if isinstance(raw_consequences, list) else []
                return AIOsirisDecision(
                    consequences=consequences,
                    reasoning=str(data.get("reasoning", "")),
                    metadata={"status": "llm", "source": "text_json"},
                )
        except (json.JSONDecodeError, TypeError):
            pass

    return AIOsirisDecision(metadata={"status": "llm_parse_failed"})


# ------------------------------------------------------------------
# LLM-driven evaluator
# ------------------------------------------------------------------


class AgenticAIOsirisEvaluator:
    """LLM-driven AI Osiris evaluator implementing the AIOsirisEvaluator Protocol.

    Directly calls LlmPort.generate() with a single tool declaration
    (submit_consequences) to get structured output. No multi-turn agentic
    loop — AI Osiris is a single-shot causal reasoner.
    """

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

        try:
            response = await self._llm.generate(
                system_prompt=OSIRIS_SYSTEM_PROMPT,
                history=[{"role": "user", "parts": [{"text": user_message}]}],
                tool_declarations=[SUBMIT_CONSEQUENCES_TOOL],
            )
        except Exception:
            logger.exception("AgenticAIOsirisEvaluator: LLM call failed")
            return AIOsirisDecision(metadata={"status": "llm_error"})

        return _parse_llm_response(response)
